"""genie.net._http — one HTTP call + transport-failure normalization (the
only place that touches a socket).

Shared by client and upstream; stdlib ``http.client`` (zero third-party
dependencies). **Not a swappable-transport seam**: no factory, no second
implementation. The transport-level read timeout = a dead-connection probe —
a fixed constant, not a parameter and not a wall clock; unreachable / broken
stream uniformly raises ``GenieHTTPError(status=None,
code="upstream")``, with the message taken from the five-code table.
"""
from __future__ import annotations

import http.client
import ssl
from urllib.parse import urlsplit

from .errors import GW_CODES, GenieHTTPError

__all__ = ["call", "Response"]

#: Dead-connection probe (shared by connect and read; not promoted to a parameter or knob)
_READ_TIMEOUT = 30.0


def _transport_error(op: str) -> GenieHTTPError:
    return GenieHTTPError(f"{op}: {GW_CODES['upstream']}",
                          status=None, code="upstream")


class Response:
    """The response of one call: status / headers / chunked reads / close
    (including the underlying connection)."""

    def __init__(self, conn, resp):
        self._conn = conn
        self._resp = resp
        self.status = resp.status

    def getheader(self, name, default=None):
        return self._resp.getheader(name, default)

    def read(self, n: int = -1) -> bytes:
        try:
            return self._resp.read(n)
        except (OSError, http.client.HTTPException):
            raise _transport_error("read") from None

    def close(self):
        try:
            self._resp.close()
        finally:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def call(method: str, url: str, *, headers: dict | None = None,
         body: bytes | None = None) -> Response:
    """One HTTP call. Success → ``Response``; transport failure → an
    upstream-code exception; **HTTP 4xx/5xx is not raised here** — status
    semantics belong to the two facades (client raises, upstream passes
    through)."""
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(
                host, port, timeout=_READ_TIMEOUT,
                context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port,
                                              timeout=_READ_TIMEOUT)
        try:
            conn.request(method, path, body=body, headers=dict(headers or {}))
            resp = conn.getresponse()
        except BaseException:
            conn.close()
            raise
    except (OSError, http.client.HTTPException):
        raise _transport_error(f"{method} {url}") from None
    return Response(conn, resp)
