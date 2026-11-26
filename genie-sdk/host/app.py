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

**No form-intent channel** (2026-11-25 站长拍，L-39 裁决)：GUI 表单直接
exec 公开动词（值插值走 static `util.py()` 字面量编码器），host 不生成
第二套 Python 行——写路径只有一条（`/exec`）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from . import auth, card, cred, gw
from .script import ScriptState
from genie.net.client import WORKER_TOKEN_HEADER
from genie.session import Session

__all__ = ["App", "write_plate", "read_plate", "remove_plate",
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

    def _live_instance_port(self):
        """The port of an already-running Studio, or None. **Judged by the
        plate's pid being alive**, not by dialing the port: orphaned
        WebView2/HTTP leftovers kept answering and blocked honest restarts
        (2026-11-22 实测). Single instance is deliberate — two Studios
        would write the same workspace document from two processes."""
        import os
        plate = read_plate(self.plate_path)
        pid = (plate or {}).get("pid")
        if not pid:
            return None
        try:
            os.kill(int(pid), 0)                   # Windows: raises if gone
        except (OSError, ValueError):
            return None
        return plate.get("port")

    def start(self) -> None:
        live = None
        if not __import__("os").environ.get("GENIE_ALLOW_MULTI_STUDIO"):
            live = self._live_instance_port()
        if live is not None:
            raise RuntimeError(
                f"Genie Studio already running at http://127.0.0.1:{live} "
                f"— use that window (close it first)")
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

    def _send(self, status: int, data: bytes, ctype: str,
              no_cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if no_cache:
            self.send_header("Cache-Control", "no-cache")   # 本地 GUI：改静态刷新即生效
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
            if path == "/usage":                    # month ledger + quota (gateway /api/usage)
                return self._json(200, auth.usage(app))
            if path == "/sweeps":                   # sweep accounts (parameter_sweep_<name>/index.json)
                return self._json(200, {"sweeps": _sweeps(app.workspace)})
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
        if path == "/exec":                         # exec downstream (terminal/Run/form)
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
        self._send(200, data, ctype, no_cache=True)


def _page(name: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{name}</title></head><body><h1>{name}</h1>"
            f"<p>The GUI shell arrives with static/.</p></body></html>")
