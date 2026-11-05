"""genie.host.gw — ``/gw``: the sole gateway egress (token validation +
whitelist + five-code normalization).

Pure proxy: dials the gateway via ``net/upstream``; asks nothing about
gateway semantics; no caching; no polling module. Three things:

1. **worker token validation** (the header name is imported from the
   ``net/client`` constant, not hand-copied; missing/wrong = a 401 auth
   envelope);
2. **path whitelist**: the five job-plane routes (batch-shaped submit /
   batch status query / single-job status query / cancel / result stream) +
   the three account-plane items; outside the whitelist → 404 (detail =
   ``not whitelisted``, distinguishable from an upstream 404);
3. **upstream error normalization**: classification is re-derived from the
   status code (401/403→auth, 400/422→resource, 429→rate, **5xx and all
   unlisted rest→server fallback**: the normalize function must return a
   bucket for any status code); messages come from the ``net/errors``
   five-code table (single source, not hand-copied);
   detail is preserved as-is; transport failure (``GenieHTTPError`` with
   ``status=None`` is the SDK-side shape) → HTTP 502 + ``upstream``
   envelope.

The session's cancel callbacks also go out through here (``f.cancel`` /
cancel-on-kill / the card stop button — the same door). The ``path``
argument arrives with the ``/gw`` prefix already stripped (the app router
strips it).
"""
from __future__ import annotations

import json
import re

from genie.net.client import WORKER_TOKEN_HEADER
from genie.net.errors import ENVELOPE_KEYS, GW_CODES, GenieHTTPError
from genie.net.upstream import Upstream

__all__ = ["handle", "upstream_headers", "status_to_code", "GwResponse"]

#: Account-plane upstream header (the cloud token is assembled only in /gw).
_UPSTREAM_AUTH_HEADER = "Authorization"

_ID = r"[^/]+"

#: Whitelist: the five job-plane routes (methods pinned) + the three
#: account-plane items (methods per common practice)
WHITELIST = [
    ("POST", re.compile(r"^/api/jobs$")),                 # batch-shaped submit
    ("POST", re.compile(r"^/api/jobs/statuses$")),        # batch status query
    ("GET", re.compile(rf"^/api/jobs/{_ID}$")),           # single-job status query
    ("POST", re.compile(rf"^/api/jobs/{_ID}/cancel$")),   # cancel
    ("GET", re.compile(rf"^/api/jobs/{_ID}/result$")),    # result stream
    ("GET", re.compile(r"^/whoami$")),
    ("POST", re.compile(r"^/api/auth/.+$")),
    ("GET", re.compile(r"^/api/health$")),
]


class GwResponse:
    """One /gw response: status + Content-Type + chunked byte iteration
    (streamable)."""

    def __init__(self, status, content_type, chunks):
        self.status = status
        self.content_type = content_type
        self.chunks = chunks          # iterable[bytes]


def status_to_code(status: int) -> str:
    """Status code → five-code bucket. Must return a bucket for any status
    code."""
    if status in (401, 403):
        return "auth"
    if status in (400, 422):
        return "resource"
    if status == 429:
        return "rate"
    return "server"                   # 5xx and all unlisted rest — fallback bucket


def upstream_headers(cloud_token: str | None) -> dict:
    """Upstream headers (the cloud token is injected only in /gw); omitted
    when there is no token."""
    if not cloud_token:
        return {}
    return {_UPSTREAM_AUTH_HEADER: f"Bearer {cloud_token}"}


def _envelope(status: int, detail) -> GwResponse:
    code = status_to_code(status)
    body = {"code": code, "message": GW_CODES[code], "detail": detail}
    return GwResponse(status, "application/json",
                      [json.dumps(body).encode("utf-8")])


def _upstream_detail(raw: bytes):
    """Upstream error body → detail (preserved as-is: envelope-shaped bodies
    yield their detail; bare bodies go in whole)."""
    try:
        env = json.loads(raw.decode("utf-8"))
        if isinstance(env, dict) and "detail" in env:
            return env["detail"]
        return env
    except ValueError:
        return raw.decode("utf-8", "replace")


def _whitelisted(method: str, path: str) -> bool:
    return any(method == m and pattern.match(path) for m, pattern in WHITELIST)


def handle(method, path, headers, body, *, worker_token,
           gateway_url, upstream_headers=None) -> GwResponse:
    """One /gw forwarding pass. Order: token → whitelist → dialing →
    normalization/passthrough."""
    if headers.get(WORKER_TOKEN_HEADER) != worker_token:
        return _envelope(401, "worker token required")
    if not _whitelisted(method, path):
        return _envelope(404, "not whitelisted")
    if not gateway_url:
        return GwResponse(502, "application/json",
                          [_transport_body()])

    fwd = dict(upstream_headers or {})
    ctype = headers.get("Content-Type")
    if body is not None and ctype:
        fwd["Content-Type"] = ctype
    try:
        resp = Upstream(gateway_url, headers=fwd).request(
            method, path, body=body)
    except GenieHTTPError:            # status=None: the SDK-side transport-exception shape
        return GwResponse(502, "application/json", [_transport_body()])
    if resp.status >= 400:
        raw = resp.read()
        resp.close()
        return _envelope(resp.status, _upstream_detail(raw))

    ctype_out = resp.getheader("Content-Type", "application/octet-stream")

    def _chunks(resp=resp):
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()

    return GwResponse(resp.status, ctype_out, _chunks())


def _transport_body() -> bytes:
    body = {"code": "upstream", "message": GW_CODES["upstream"],
            "detail": None}
    return json.dumps(body).encode("utf-8")


assert tuple(ENVELOPE_KEYS) == ("code", "message", "detail")   # key set aligned
