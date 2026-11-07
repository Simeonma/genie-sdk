"""genie.agent — the MCP adapter over Studio's local API.

Studio is the one execution surface (Q3.1): the agent edits **the same
script** and drives **the same terminal** the browser shows. This package is
only Studio's client — three thin verbs (``edit_script`` / ``run_script`` /
``run_code``), each one hop over the ``/session/*`` endpoints the browser
uses, authorised by the local session token.

Connection (Q3.2): read the studio plate (``~/.genie/studio.json``), no
default port, no port scanning, no plate → "start ``genie studio``". An
explicit ``GENIE_STUDIO_URL`` / ``GENIE_STUDIO_TOKEN`` overrides the plate.

Import discipline (Q6.6): nothing from the rest of the SDK — no
``genie.host`` (the adapter is the host's client, not part of it), no
``genie.worker`` / ``genie.core`` / ``genie.session`` / ``genie.net`` /
``genie.ipc``. Locked by ``tests/agent/test_agent.py`` (AST).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

__all__ = ["connect", "edit_script", "run_script", "run_code",
           "StudioError", "DEFAULT_PLATE", "SESSION_TOKEN_HEADER",
           "ENV_URL", "ENV_TOKEN"]

#: Studio's plate and local session-token header. Mirrored from ``genie.host.app``
#: on purpose (the adapter must not import the host); the mirror is enforced by
#: a test in ``tests/agent/test_agent.py``, not by memory.
DEFAULT_PLATE = os.path.join(os.path.expanduser("~"), ".genie", "studio.json")
SESSION_TOKEN_HEADER = "X-Genie-Session-Token"

#: Explicit override (Q3.2: base_url may override the plate).
ENV_URL = "GENIE_STUDIO_URL"
ENV_TOKEN = "GENIE_STUDIO_TOKEN"

#: How often the exec wait re-reads the ring (the browser's tick is 300ms).
_POLL_S = 0.1

#: Default wait for one run to reach its terminal state (seconds).
_TIMEOUT_S = 600.0

#: Ring ``out`` kinds that are GUI projections, not terminal text.
_STRUCTURED = frozenset({"progress", "meta", "series", "plot"})

#: Tail cap: an agent needs the end of the output, not the whole scrollback.
_TAIL_LINES = 400


class StudioError(RuntimeError):
    """Studio unreachable / the call could not be made (verbs turn this into ``ok: false``)."""


# ── connection (plate or explicit override) ───────────────────────────────

def _read_plate(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _endpoint(plate_path: str | None = None):
    """``(base_url, token)``: explicit env override first, else the plate."""
    url = os.environ.get(ENV_URL) or ""
    if url:
        return url.rstrip("/"), os.environ.get(ENV_TOKEN, "")
    data = _read_plate(plate_path or DEFAULT_PLATE)
    if not data:
        raise StudioError(
            f"no Studio session: {plate_path or DEFAULT_PLATE} is missing "
            "(start `genie studio` first, or set GENIE_STUDIO_URL)")
    port = data.get("port")
    if not isinstance(port, int):
        raise StudioError(f"bad studio plate: {plate_path or DEFAULT_PLATE} carries no port")
    return f"http://127.0.0.1:{port}", str(data.get("token") or "")


def _call(method: str, path: str, body=None, *, timeout: float = 30.0,
          plate_path: str | None = None):
    base, token = _endpoint(plate_path)
    url = base + path
    data = None
    headers = {SESSION_TOKEN_HEADER: token}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except (ValueError, OSError):
            pass
        raise StudioError(f"{path} -> HTTP {e.code}" + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, OSError) as e:
        raise StudioError(f"Studio not reachable at {base} ({e}); start `genie studio`")
    return json.loads(raw.decode("utf-8")) if raw else {}


def connect(plate_path: str | None = None) -> dict:
    """Is a Studio session there? ``{"session": true, "base_url": ...}`` / ``false`` + reason."""
    try:
        base, _token = _endpoint(plate_path)
    except StudioError as e:
        return {"session": False, "error": str(e)}
    try:
        ws = _call("GET", "/workspace", plate_path=plate_path)
    except StudioError as e:
        return {"session": False, "base_url": base, "error": str(e)}
    return {"session": True, "base_url": base, "workspace": ws.get("root")}


# ── the three verbs (Q3.1) ────────────────────────────────────────────────

def edit_script(text: str, *, plate_path: str | None = None) -> dict:
    """Write the workspace script (the same file the editor shows; last writer wins, Q3.5)."""
    try:
        res = _call("POST", "/session/script", {"text": str(text)},
                    plate_path=plate_path)
    except StudioError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "version": res.get("version")}


def run_script(*, plate_path: str | None = None,
               timeout: float | None = None) -> dict:
    """Run the current workspace script (Run semantics: a fresh namespace, the model is rebuilt)."""
    try:
        cur = _call("GET", "/session/script", plate_path=plate_path)
    except StudioError as e:
        return {"ok": False, "error": str(e)}
    text = cur.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": "the workspace script is empty"}
    return _run(text, fresh=True, plate_path=plate_path, timeout=timeout)


def run_code(code: str, *, plate_path: str | None = None,
             timeout: float | None = None) -> dict:
    """Run one snippet in the terminal's namespace (REPL line semantics, the model survives)."""
    if not str(code).strip():
        return {"ok": False, "error": "empty code"}
    return _run(str(code), fresh=False, plate_path=plate_path, timeout=timeout)


def _run(code: str, *, fresh: bool, plate_path: str | None = None,
         timeout: float | None = None) -> dict:
    """Submit one exec and block to its terminal state, returning the tail output (Q3.4)."""
    deadline_s = _TIMEOUT_S if timeout is None else float(timeout)
    try:
        head = _call("GET", "/session/events?since=0", plate_path=plate_path)
        since, epoch = int(head.get("since", 0)), head.get("epoch")
        res = _call("POST", "/session/exec", {"code": code, "fresh": fresh},
                    plate_path=plate_path)
    except StudioError as e:
        return {"ok": False, "error": str(e)}
    if res.get("busy"):
        # The ring already carries the busy line (session appends it before replying);
        # relay that text instead of inventing a second wording for the same fact.
        try:
            page = _call("GET", f"/session/events?since={since}", plate_path=plate_path)
        except StudioError as e:
            return {"ok": False, "busy": True, "error": str(e)}
        line = _last_out(page.get("events") or [])
        out = {"ok": False, "busy": True}
        if line:
            out["message"] = line
        return out
    rid = res.get("id")
    if rid is None:
        return {"ok": False, "error": "Studio accepted no exec id"}

    lines: list[str] = []
    deadline = time.monotonic() + deadline_s
    while True:
        try:
            page = _call("GET", f"/session/events?since={since}", plate_path=plate_path)
        except StudioError as e:
            return {"ok": False, "error": str(e), "output": _tail(lines)}
        if page.get("epoch") != epoch:
            return {"ok": False, "output": _tail(lines),
                    "error": "Studio restarted while the run was in progress"}
        for ev in page.get("events") or []:
            kind = ev.get("ev")
            if kind == "done" and ev.get("id") == rid:
                return {"ok": bool(ev.get("ok")), "output": _tail(lines),
                        "error": ev.get("err")}
            if kind == "out" and ev.get("kind") not in _STRUCTURED:
                lines.append(str(ev.get("text", "")))
        since = int(page.get("since", since))
        if time.monotonic() >= deadline:
            return {"ok": False, "output": _tail(lines),
                    "error": f"timed out after {deadline_s:g}s waiting for the run to finish"}
        time.sleep(_POLL_S)


def _last_out(events) -> str:
    for ev in reversed(list(events)):
        if ev.get("ev") == "out" and ev.get("kind") not in _STRUCTURED:
            return str(ev.get("text", ""))
    return ""


def _tail(lines: list[str]) -> str:
    tail = lines[-_TAIL_LINES:]
    return "\n".join(tail)
