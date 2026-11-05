"""genie.net._http — 一次 HTTP 调用 + 传输失败归一（唯一碰 socket 的地方）。

client 与 upstream 共用（net/MODMAP §1）；标准库 ``http.client``（零三方，
站长拍定 2026-11-04）。**不是可换传输缝**：无工厂、无第二实现（Q3.1
浏览器不是执行面）。
传输级 read timeout = 死连接探测器——固定常量，不是参数、不是墙钟
（Q2.2-4 / Q7.2）；连不上 / 断流一律 ``GenieHTTPError(status=None,
code="upstream")``，句出自五码表。
"""
from __future__ import annotations

import http.client
import ssl
from urllib.parse import urlsplit

from .errors import GW_CODES, GenieHTTPError

__all__ = ["call", "Response"]

#: 死连接探测器（连接与读共用；Q2.2-4：不上升为参数或旋钮）
_READ_TIMEOUT = 30.0


def _transport_error(op: str) -> GenieHTTPError:
    return GenieHTTPError(f"{op}: {GW_CODES['upstream']}",
                          status=None, code="upstream")


class Response:
    """一次调用的响应：状态 / 头 / 逐块读 / 关闭（含底层连接）。"""

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
    """一次 HTTP 调用。成功 → ``Response``；传输失败 → upstream 码异常；
    **HTTP 4xx/5xx 不在这里抛**——状态语义归两个门面（client 抛、
    upstream 透传）。"""
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
