"""genie.host.app — HTTP surface (browser + agent) + studio plate + session wiring.

Content routes: three URLs (``/`` / ``/manual/`` / ``/examples/``) +
``/static/*``; session routes (ring projection / exec downstream / cancel /
restart / script read-write — via session; the "restart worker" entry
point = ``POST /restart``); the agent surface = the same set of verbs
mounted under the ``/session/*`` prefix, carrying a local session token
(delivered via the studio plate); ``/gw`` belongs to ``host/gw`` (a
pure proxy). Studio plate ``~/.genie/studio.json``: 0600, only
removes its own pid, no default port — binds ``127.0.0.1:0`` and lets the
system assign; no scanning.

**Form delta generation lives here**: the browser sends only a structured
thin intent (collection / kind / id /
field / value) → ``form_delta`` produces one line of the Q15 API
language (values are
always ``repr`` literals, never concatenated strings; **field types are
asked of protocol — field-type reflection against the source of truth, no
second copied table**: collection→SimConfig field, kind→discriminated
union member, tuple annotation→JSON list collapsed into a tuple) → the
session dispatches ``exec(echo=False)`` (terminal does not echo).
Envelope collections (domain / mesh / sources / monitors) are generated
against the fdtd default solver (the GUI is fdtd-bound v1; structure
collections live on the ``genie`` handle).
Add-structure / remove-structure intents grow with the GUI shell's real
needs (``genie.geometry.add_box('name')`` followed by field-by-field
assignment).
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Literal, Union, get_args, get_origin
from urllib.parse import parse_qs, urlparse

from pydantic.fields import FieldInfo
from genie_protocol import _wire

from . import auth, card, cred, gw
from .script import ScriptState
from genie.net.client import WORKER_TOKEN_HEADER
from genie.session import Session

__all__ = ["App", "form_delta", "form_intent", "form_fields",
           "write_plate", "read_plate", "remove_plate",
           "SESSION_TOKEN_HEADER", "DEFAULT_PLATE"]

#: Local session token header for the agent surface (same-origin browser requests carry none)
SESSION_TOKEN_HEADER = "X-Genie-Session-Token"

DEFAULT_PLATE = os.path.join(os.path.expanduser("~"), ".genie", "studio.json")

_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


# ── studio plate ─────────────────────────────────────────────────────────

def write_plate(path, port: int, token: str) -> None:
    data = {"pid": os.getpid(), "port": port, "token": token}
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(path, 0o600)


def read_plate(path) -> dict | None:
    """Read the studio plate (what agent/cli consumes); missing / bad plate → None."""
    p = str(path)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remove_plate(path) -> None:
    """Removes the plate only if it carries its own pid (others' / bad plates are left untouched)."""
    p = str(path)
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("pid") == os.getpid():
        os.remove(p)


# ── form delta generation ────────────────────────────────────────────────

#: f collection attribute name → SimConfig field name (reflection entry; no copied model field list)
_F_ATTR = {"domain": "domain", "mesh": "mesh", "geom": "geometry",
           "mat": "materials", "src": "source", "mon": "monitor"}

#: Q17 nested house: which wire section each GUI collection reflects
#: against (structure → ModelSpec, fdtd envelope → FdtdConfigSpec).
_SECTION_OF = {"geom": _wire.ModelSpec, "mat": _wire.ModelSpec,
               "domain": _wire.FdtdConfigSpec, "mesh": _wire.FdtdConfigSpec,
               "src": _wire.FdtdConfigSpec, "mon": _wire.FdtdConfigSpec}

#: GUI collection key → the new-syntax line prefix (Q15): structure
#: collections live on the handle, the envelope collections on the fdtd
#: default solver (the GUI is fdtd-bound v1).
_F_PREFIX = {"domain": "genie.fdtd.domain", "mesh": "genie.fdtd.mesh",
             "geom": "genie.geometry", "mat": "genie.materials",
             "src": "genie.fdtd.sources", "mon": "genie.fdtd.monitors"}


def _resolve_spec(coll: str, kind):
    """collection (+kind) → the protocol's Spec class (all via reflection against the source of truth)."""
    ann = _SECTION_OF[coll].model_fields[_F_ATTR[coll]].annotation
    if get_origin(ann) is list:                 # collection field: list[Annotated[...]]
        ann = get_args(ann)[0]
    args = get_args(ann)
    if args and len(args) == 2 and isinstance(args[1], FieldInfo):
        disc = args[1].discriminator            # Annotated[Union, Field(...)]
        for cls in get_args(args[0]):
            fld = cls.model_fields.get(disc)
            if fld is not None and fld.default == kind:
                return cls
        raise ValueError(f"unknown kind {kind!r} for {coll}")
    return ann                                 # singleton: the annotation is the model class


def _coerce(ann, value):
    """JSON value → model-annotation shape (a tuple annotation collapses a list into a tuple; the rest pass through)."""
    if get_origin(ann) is Union:
        for a in get_args(ann):
            if get_origin(a) is tuple:
                return _coerce(a, value)
        return value
    if get_origin(ann) is tuple and isinstance(value, list):
        args = get_args(ann)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(value)
        return tuple(_coerce(a, v) for a, v in zip(args, value))
    return value


def form_delta(intent: dict) -> str:
    """Browser thin intent → a one-line Python delta (the only public surface)."""
    coll = intent.get("collection")
    field = intent.get("field")
    if coll not in _F_ATTR or not isinstance(field, str) \
            or not field.isidentifier():
        raise ValueError(f"bad form intent: {intent!r}")
    spec = _resolve_spec(coll, intent.get("kind"))
    if field not in spec.model_fields:
        raise ValueError(f"unknown field {field!r} on {coll}")
    value = _coerce(spec.model_fields[field].annotation, intent.get("value"))
    if intent.get("id") is None:
        return f"{_F_PREFIX[coll]}.{field} = {value!r}"
    return f"{_F_PREFIX[coll]}[{int(intent['id'])}].{field} = {value!r}"


def _is_union(origin) -> bool:
    return origin is Union or str(origin) == "types.UnionType"


def _unwrap(ann):
    """Optional[X] / X | None → (X, optional=True); Annotated[X, ...] → X."""
    if get_origin(ann) is not None and hasattr(ann, "__metadata__"):
        ann = get_args(ann)[0]
    if _is_union(get_origin(ann)):
        args = [a for a in get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return _unwrap(args[0])[0], True
    return ann, False


def _field_type(ann) -> dict:
    """Annotation → a serializable field descriptor (reflection; no copied field table)."""
    inner = ann
    origin = get_origin(inner)
    if origin is not None and hasattr(inner, "__metadata__"):   # Annotated[...]
        inner = get_args(inner)[0]
        origin = get_origin(inner)
    if _is_union(origin):
        args = [a for a in get_args(inner) if a is not type(None)]
        if len(args) == 1:
            return _field_type(args[0])
        return {"type": "json"}
    if origin is Literal:
        return {"type": "enum", "options": list(get_args(inner))}
    if origin is tuple:
        return {"type": "vec"}
    if origin in (list, dict):
        return {"type": "json"}
    if inner is bool:
        return {"type": "bool"}
    if inner is int:
        return {"type": "int"}
    if inner is float:
        return {"type": "float"}
    if inner is str:
        return {"type": "str"}
    if hasattr(inner, "model_fields"):                       # nested Spec model
        return {"type": "json"}
    return {"type": "json"}


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return None


def form_fields(collection: str, kind=None) -> list[dict]:
    """The inspector's field list for a collection (+kind), reflected from
    the protocol spec (single source of field names/types — Q1.1.4).
    ``required`` marks what wire validation will demand; the live model is
    a draft, so every field stays editable."""
    if collection not in _F_ATTR:
        raise ValueError(f"unknown collection {collection!r}")
    spec = _resolve_spec(collection, kind)
    out = []
    for name, f in spec.model_fields.items():
        if name in ("type", "id"):          # discriminator + system-issued id: not editable (Q1.2)
            continue
        ann, optional = _unwrap(f.annotation)
        desc = {"name": name, "required": f.is_required() and not optional}
        desc.update(_field_type(ann))
        default = f.default
        if default is not None and default.__class__.__name__ != "PydanticUndefined":
            desc["default"] = _jsonable(default)
        out.append(desc)
    return out


#: Which add_* shells the GUI may create per collection. Materials are
#: narrowed to the two online wire shapes: ``from_library`` is a dead node
#: the line format rejects (the library flow goes through genie.mat_from_nk
#: instead); geometry / source / monitor kinds are reflected below.
_MAT_ADD_KINDS = ("const", "lorentz")


def form_intent(intent: dict) -> str:
    """A form intent → one line of Python. Ops: ``param`` (parameters
    table), ``add`` (address-only shell, Q1.4), ``remove`` (by id), and the
    default ``set`` (one field assignment — delegated to form_delta)."""
    op = intent.get("op") or "set"
    if op == "param":
        # 参数表：genie.parameters[<name>] = value / 删除（value=null 或 remove）
        name = intent.get("name")
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"bad parameter name: {name!r}")
        if intent.get("remove") or intent.get("value") is None:
            return f"genie.parameters.pop({name!r}, None)"
        return f"genie.parameters[{name!r}] = {intent.get('value')!r}"
    coll = intent.get("collection")
    if coll not in _F_ATTR:
        raise ValueError(f"bad form intent: {intent!r}")
    if op == "add":
        kind = intent.get("kind")
        if not isinstance(kind, str) or not kind.isidentifier():
            raise ValueError(f"bad add kind: {kind!r}")
        if coll == "mat":
            if kind not in _MAT_ADD_KINDS:
                raise ValueError(f"materials cannot add {kind!r} "
                                 f"(use the library flow)")
        else:
            _resolve_spec(coll, kind)                        # unknown kind → ValueError
        name = intent.get("name")
        if name is None:
            return f"{_F_PREFIX[coll]}.add_{kind}()"
        if not isinstance(name, str) or not name:
            raise ValueError(f"bad add name: {name!r}")
        return f"{_F_PREFIX[coll]}.add_{kind}({name!r})"
    if op == "remove":
        iid = intent.get("id")
        if iid is None:
            raise ValueError("remove needs an item id")
        return f"{_F_PREFIX[coll]}.remove({int(iid)})"
    return form_delta(intent)


# ── App (HTTP surface + session wiring) ────────────────────────────────

def _sweeps(workspace) -> list:
    """Read the sweep accounts off disk: ``parameter_sweep_<name>/index.json``
    (Q10: the index is non-authoritative and rebuildable; the GUI's table
    reads it directly). Read-only; never enumerates beyond that pattern."""
    out = []
    try:
        dirs = sorted(Path(workspace).glob("parameter_sweep_*"))
    except OSError:
        return out
    for d in dirs:
        if not d.is_dir():
            continue
        data = None
        try:
            data = json.loads((d / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        jobs = (data or {}).get("jobs") or []
        ok = sum(1 for j in jobs if j.get("state") == "done")
        bad = sum(1 for j in jobs
                  if j.get("state") not in (None, "done", "running", "queued"))
        out.append({
            "name": (data or {}).get("sweep")
                    or d.name[len("parameter_sweep_"):],
            "dir": d.name,
            "created": (data or {}).get("created"),
            "total": len(jobs),
            "done": ok,
            "failed": bad,
            "jobs": [{"i": j.get("i"), "job_id": j.get("job_id"),
                      "doc": j.get("doc"), "state": j.get("state"),
                      "ok": j.get("ok")} for j in jobs],
        })
    return out

_PLACEHOLDER = {                         # content routes still awaiting their content line
    "/manual/": "Genie Manual",
    "/examples/": "Genie Examples",
}


#: Default cloud gateway: the sole entry point in production; switching
#: gateways goes through env ``GENIE_BACKEND`` (read at the cli layer).
DEFAULT_GATEWAY = "https://genie-optics.com"


class App:
    """All the wiring of one Studio process. Usage::

        app = App(workspace, gateway_url="https://gw.example")  # None = not connected
        app.start()                  # bind 127.0.0.1:0 → session → studio plate
        ...                          # http://127.0.0.1:{app.port}
        app.stop()                   # withdraw own plate + reap worker + stop server

    ``worker_cmd`` is a test seam (fake worker; production has only ``python -m genie.worker``).
    """

    def __init__(self, workspace, *, gateway_url=None, port: int = 0,
                 plate_path=None, cred_path=None, worker_cmd=None):
        self.workspace = str(workspace)
        self.gateway_url = gateway_url
        self.port_hint = port
        self.plate_path = str(plate_path) if plate_path else DEFAULT_PLATE
        self.cred_path = str(cred_path) if cred_path else cred.DEFAULT_PATH
        self.worker_cmd = worker_cmd
        self.token = secrets.token_hex(16)          # local session token (per-start random)
        self.worker_token = secrets.token_hex(16)   # random at spawn
        self.script = ScriptState(self.workspace)
        self.session = None
        self.port = None
        self._srv = None

    # -- lifecycle ----------------------------------------------------

    def start(self) -> None:
        outer = self

        class _Handler(_RouteHandler):
            pass

        _Handler.app = outer
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port_hint),
                                        _Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        # Bind the port before spawning: GENIE_GW_BASE_URL must carry the real port
        self.session = Session(
            self.workspace,
            gw_base_url=f"http://127.0.0.1:{self.port}/gw",
            worker_token=self.worker_token,
            on_cancel=self._cancel_via_gw,
            worker_cmd=self.worker_cmd)
        self.session.start()
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        write_plate(self.plate_path, self.port, self.token)

    def stop(self) -> None:
        remove_plate(self.plate_path)               # only removes its own pid
        if self.session is not None:
            self.session.close()
            self.session = None
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # -- cancel callback (session → /gw, the same entry point) --------------------------

    def _cancel_via_gw(self, job_ids) -> None:
        hdrs = gw.upstream_headers(cred.load_token(path=self.cred_path))
        for jid in job_ids:
            res = gw.handle(
                "POST", f"/api/jobs/{jid}/cancel",
                {WORKER_TOKEN_HEADER: self.worker_token}, None,
                worker_token=self.worker_token, gateway_url=self.gateway_url,
                upstream_headers=hdrs)
            if res.status >= 400:
                raise RuntimeError(f"cancel {jid}: HTTP {res.status}")


class _RouteHandler(BaseHTTPRequestHandler):
    """Thin routing layer: content routes / session routes (browser) + /session (agent) + /gw."""
    app: "App"
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):                      # silent
        pass

    # -- response helpers ----------------------------------------------------

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json")

    def _html(self, status: int, text: str, ctype: str) -> None:
        self._send(status, text.encode("utf-8"), ctype)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # -- dispatch --------------------------------------------------------

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str):
        try:
            self._dispatch(method)
        except BrokenPipeError:
            pass
        except Exception as e:                      # noqa: BLE001 — per-request backstop
            try:
                self._json(500, {"error": str(e)})
            except OSError:
                pass

    def _dispatch(self, method: str):
        app = self.app
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/gw" or path.startswith("/gw/"):
            return self._gw(app, method, path[3:] or "/")
        if path.startswith("/session/"):
            if self.headers.get(SESSION_TOKEN_HEADER) != app.token:
                return self._json(401, {"error": "session token required"})
            return self._verbs(app, method,
                               path[len("/session"):] or "/", parsed)
        if method == "POST":                        # cross-site guard on the browser surface (same-origin sends none)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{app.port}":
                return self._json(403, {"error": "cross-origin"})
        return self._verbs(app, method, path, parsed)

    def _gw(self, app, method: str, path: str):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        res = gw.handle(method, path, self.headers, body,
                        worker_token=app.worker_token,
                        gateway_url=app.gateway_url,
                        upstream_headers=gw.upstream_headers(
                            cred.load_token(path=app.cred_path)))
        self.send_response(res.status)
        self.send_header("Content-Type", res.content_type)
        self.end_headers()
        for chunk in res.chunks:                    # streaming passthrough
            self.wfile.write(chunk)

    def _verbs(self, app, method: str, path: str, parsed):
        q = parse_qs(parsed.query)
        if method == "GET":
            if path == "/events":                   # ring projection (single 300ms tick)
                since = int(q.get("since", ["0"])[0])
                epoch, nxt, evs = app.session.events(since)
                return self._json(200, {"epoch": epoch, "since": nxt,
                                        "events": evs,
                                        "busy": app.session.busy})
            if path == "/auth":                     # login status (whoami-backed, sanitized)
                return self._json(200, auth.status(app))
            if path == "/workspace":                # the process working directory (read-only info)
                return self._json(200, {"root": app.workspace})
            if path == "/sweeps":                   # sweep accounts (parameter_sweep_<name>/index.json)
                return self._json(200, {"sweeps": _sweeps(app.workspace)})
            if path == "/form/fields":              # inspector field list (protocol reflection)
                try:
                    fields = form_fields(q.get("collection", [""])[0],
                                         q.get("kind", [None])[0])
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"fields": fields})
            if path == "/script":                   # editor backfill
                focused = q.get("focus", ["0"])[0] == "1"
                return self._json(200, app.script.read(focused=focused))
            if path == "/":                        # the Studio shell (static/index.html)
                return self._static("index.html")
            if path in _PLACEHOLDER:                # content routes not yet grown
                name = _PLACEHOLDER[path]
                return self._html(200, _page(name), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            return self._json(404, {"error": "no route"})

        body = self._read_json()
        if path == "/auth/login":                  # the GUI login (token captured host-side)
            return self._json(200, auth.login(
                app, str(body.get("username") or ""),
                str(body.get("password") or "")))
        if path == "/auth/logout":
            return self._json(200, auth.logout(app))
        if path == "/form":                         # form intent → Python delta (echo=False)
            try:
                line = form_intent(body)
            except (ValueError, TypeError) as e:
                return self._json(400, {"error": str(e)})
            rid = app.session.submit_exec(line, fresh=False, echo=False)
            return self._json(200,
                              {"id": rid} if rid is not None else {"busy": True})
        if path == "/exec":                         # exec downstream (form/terminal/Run)
            rid = app.session.submit_exec(
                body.get("code", ""), fresh=bool(body.get("fresh")),
                echo=bool(body.get("echo", True)))
            return self._json(200,
                              {"id": rid} if rid is not None else {"busy": True})
        if path == "/cancel":                       # job card stop control / f.cancel, the same entry point
            return self._json(200, {"canceled": card.stop(app.session)})
        if path == "/restart":                      # the "restart worker" entry point
            app.session.restart_worker()
            return self._json(200, {"ok": True})
        if path == "/script":                       # save-on-keystroke / Ctrl+S
            return self._json(200, {"version": app.script.write(
                body.get("text", ""))})
        if path == "/script/target":                # open / save as
            ver = app.script.open_as(body.get("path", ""))
            return self._json(200, {"version": ver,
                                    "path": str(app.script.path)})
        return self._json(404, {"error": "no route"})

    def _static(self, rel: str):
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ("..", "") for p in parts):
            return self._json(404, {"error": "no route"})
        f = _STATIC_ROOT.joinpath(*parts)
        if not f.is_file():
            return self._json(404, {"error": "no route"})
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        data = f.read_bytes()
        if ctype.startswith("text/"):           # bytes go out as-is; only the label gains a charset
            ctype = ctype + "; charset=utf-8"
        self._send(200, data, ctype)


def _page(name: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{name}</title></head><body><h1>{name}</h1>"
            f"<p>The GUI shell arrives with static/.</p></body></html>")
