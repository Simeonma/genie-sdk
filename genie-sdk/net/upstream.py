"""genie.net.upstream — host→网关代拨门面（唯一拨网关的代码）。

代理形（net/MODMAP §1）：显式 ``gateway_url`` + 头 + 体；HTTP 状态**原样
透传**（4xx 不抛——归一分类是 host /gw 的事，Q2.2-5A）；响应可流式。
零凭据：URL 与头由调用方（host/gw）显式传入（Q2.1；cli 不拨网关——Q6.9）。
"""
from __future__ import annotations

from ._http import call

__all__ = ["Upstream"]


class Upstream:
    """代拨门面。host/gw 构造时注入云 token 头，之后逐请求转发。"""

    def __init__(self, gateway_url: str, *, headers: dict | None = None):
        self._base = gateway_url.rstrip("/")
        self._headers = dict(headers or {})

    def request(self, method: str, path: str, *,
                headers: dict | None = None, body: bytes | None = None):
        """一次代拨 → ``Response``（.status 原样、.read(n) 流式）。"""
        h = dict(self._headers)
        if headers:
            h.update(headers)
        return call(method, self._base + path, headers=h, body=body)
