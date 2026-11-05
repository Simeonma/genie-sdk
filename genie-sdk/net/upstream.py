"""genie.net.upstream — the host→gateway proxy-dialing facade (the only code
that dials the gateway).

Proxy shape: explicit ``gateway_url`` + headers + body; HTTP status
**passes through as-is** (4xx does not raise — status→code normalization is
host /gw's business); responses can stream. Zero credentials: the URL and
headers are passed in explicitly by the caller (host/gw; the cli does not
dial the gateway).
"""
from __future__ import annotations

from ._http import call

__all__ = ["Upstream"]


class Upstream:
    """Proxy-dialing facade. host/gw injects the cloud token header at
    construction, then forwards per request."""

    def __init__(self, gateway_url: str, *, headers: dict | None = None):
        self._base = gateway_url.rstrip("/")
        self._headers = dict(headers or {})

    def request(self, method: str, path: str, *,
                headers: dict | None = None, body: bytes | None = None):
        """One proxy-dialed call → ``Response`` (.status as-is, .read(n)
        streaming)."""
        h = dict(self._headers)
        if headers:
            h.update(headers)
        return call(method, self._base + path, headers=h, body=body)
