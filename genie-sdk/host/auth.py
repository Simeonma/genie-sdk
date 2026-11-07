"""genie.host.auth — the login service for the browser surface (a frontend
service piece; the only cloud-account face the page talks to).

Q2.1: the GUI's login goes ``/gw/api/auth/login`` → the token is captured
into the credential store **here** (the browser never sees it — the login
response is sanitized to ``{ok, username}``); CLI ``genie login`` writes
the same store through the same write function (``cred.save_token``), so a
CLI login takes effect on the next request without a worker restart.

The browser cannot dial ``/gw`` itself (it holds no worker token — the
worker token is spawn-random and worker/host-only), so this module dials
``/gw`` on the browser's behalf via ``host/gw`` (the sole egress).
``/whoami`` answers the login status; a stored-but-stale token is reported
as ``logged_in: false`` and left in place (clearing happens only through
an explicit logout).
"""
from __future__ import annotations

import json

from genie.net.client import WORKER_TOKEN_HEADER

from . import cred, gw

__all__ = ["status", "login", "logout"]


def _call(app, method: str, path: str, body: dict | None = None):
    """One account-plane call through /gw (the sole egress). Returns
    ``(status, parsed_body_or_None)`` — the gw envelope is already the
    five-code shape."""
    hdrs = {WORKER_TOKEN_HEADER: app.worker_token}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    res = gw.handle(
        method, path, hdrs, payload,
        worker_token=app.worker_token,
        gateway_url=app.gateway_url,
        upstream_headers=gw.upstream_headers(
            cred.load_token(path=app.cred_path)))
    raw = b"".join(res.chunks)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        parsed = None
    return res.status, parsed


def _username(who) -> str | None:
    """whoami body → the display name (``principal`` as a string, or an
    object carrying ``name``)."""
    if not isinstance(who, dict):
        return None
    principal = who.get("principal")
    if isinstance(principal, str):
        return principal
    if isinstance(principal, dict) and isinstance(principal.get("name"), str):
        return principal["name"]
    return None


def status(app) -> dict:
    """Login status for the page: whoami against the gateway when a token
    is stored; a failed whoami never touches the store."""
    has_token = cred.load_token(path=app.cred_path) is not None
    if not app.gateway_url:
        return {"gateway": False, "token": False, "logged_in": False}
    if not has_token:
        return {"gateway": True, "token": False, "logged_in": False}
    code, who = _call(app, "GET", "/whoami")
    if code == 200:
        return {"gateway": True, "token": True, "logged_in": True,
                "username": _username(who)}
    # stale/invalid token (auth) or an unreachable gateway (upstream/…) —
    # report which, keep the store (explicit logout is the only clear).
    reason = (who or {}).get("code") if isinstance(who, dict) else None
    return {"gateway": True, "token": True, "logged_in": False,
            "error": reason}


def login(app, username: str, password: str) -> dict:
    """The GUI login: ``/gw/api/auth/login`` → capture the token into the
    store → sanitized reply (the token never reaches the browser)."""
    if not app.gateway_url:
        return {"ok": False, "code": "upstream",
                "detail": "no gateway configured"}
    code, body = _call(app, "POST", "/api/auth/login",
                       {"username": username, "password": password})
    if code != 200:
        return {"ok": False,
                "code": (body or {}).get("code") if isinstance(body, dict)
                else None,
                "detail": (body or {}).get("detail") if isinstance(body, dict)
                else None}
    token = None
    if isinstance(body, dict):
        token = body.get("token") or body.get("access_token")
    if not token or not isinstance(token, str):
        return {"ok": False, "code": None,
                "detail": "login response carries no token"}
    cred.save_token(token, path=app.cred_path)   # the same write function as the CLI
    return {"ok": True, "username": username or _username(None) or ""}


def logout(app) -> dict:
    """Logout = withdraw the store (no gateway call; the token value never
    lands in a second place)."""
    cred.clear(path=app.cred_path)
    return {"ok": True}
