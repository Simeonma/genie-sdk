"""genie.host.gw — ``/gw``：唯一网关出口（token 校验 + 白名单 + 五码归一）。

纯代理（host/MODMAP §1 / 禁令 1）：经 ``net/upstream`` 拨网关，不问网关
语义、不缓存、无轮询。三件事（Q2.2-5A）：

1. **worker token 校验**（头名 import ``net/client`` 常量，不手抄；
   缺/错 = 401 auth 包络）；
2. **路径白名单**（批 5 拍定）：作业面五路由（v2 批形提交 / 批量查态 /
   单查态 / 取消 / 结果流）+ 账号面三样（随账号面落地核对）；
   白名单外 404（detail = ``not whitelisted``，与上游 404 可区分）；
3. **上游错误归一**：分类按状态码重导（401/403→auth、400/422→resource、
   429→rate、**5xx 及未列其余→server 兜底**，2026-11-04 收编：归一函数
   对任何状态码必须给桶）；句引 ``net/errors`` 五码表（单源不手抄）；
   detail 原样保留；传输失败（``GenieHTTPError(status=None)`` 是 SDK 侧
   形状）→ HTTP 502 + ``upstream`` 包络。

session 的取消回调也经这里出（``f.cancel`` / kill 杀时 / 卡片停止键，
同一扇门）。``path`` 入参已剥掉 ``/gw`` 前缀（app 路由剥）。
"""
from __future__ import annotations

import json
import re

from genie.net.client import WORKER_TOKEN_HEADER
from genie.net.errors import ENVELOPE_KEYS, GW_CODES, GenieHTTPError
from genie.net.upstream import Upstream

__all__ = ["handle", "upstream_headers", "status_to_code", "GwResponse"]

#: 账号面上行头（云 token 只在 /gw 拼，Q2.1）。头形随 v2 SDK——
#: 契约主人（站长 2026-11-05：service / fdtd 跟随适配）。
_UPSTREAM_AUTH_HEADER = "Authorization"

_ID = r"[^/]+"

#: 白名单：作业面五路由（方法钉死）+ 账号面三样（方法按常例，随账号面
#: 落地核对）
WHITELIST = [
    ("POST", re.compile(r"^/api/jobs$")),                 # v2 批形提交
    ("POST", re.compile(r"^/api/jobs/statuses$")),        # 批量查态（批 3）
    ("GET", re.compile(rf"^/api/jobs/{_ID}$")),           # 单查态
    ("POST", re.compile(rf"^/api/jobs/{_ID}/cancel$")),   # 取消
    ("GET", re.compile(rf"^/api/jobs/{_ID}/result$")),    # 结果流
    ("GET", re.compile(r"^/whoami$")),
    ("POST", re.compile(r"^/api/auth/.+$")),
    ("GET", re.compile(r"^/api/health$")),
]


class GwResponse:
    """一次 /gw 应答：状态 + Content-Type + 字节块迭代（可流式）。"""

    def __init__(self, status, content_type, chunks):
        self.status = status
        self.content_type = content_type
        self.chunks = chunks          # iterable[bytes]


def status_to_code(status: int) -> str:
    """状态码 → 五码桶。对任何状态码必须给桶（2026-11-04 收编）。"""
    if status in (401, 403):
        return "auth"
    if status in (400, 422):
        return "resource"
    if status == 429:
        return "rate"
    return "server"                   # 5xx 及未列其余——兜底桶


def upstream_headers(cloud_token: str | None) -> dict:
    """上行头（云 token 只在 /gw 注入，Q2.1）；无 token 不带。"""
    if not cloud_token:
        return {}
    return {_UPSTREAM_AUTH_HEADER: f"Bearer {cloud_token}"}


def _envelope(status: int, detail) -> GwResponse:
    code = status_to_code(status)
    body = {"code": code, "message": GW_CODES[code], "detail": detail}
    return GwResponse(status, "application/json",
                      [json.dumps(body).encode("utf-8")])


def _upstream_detail(raw: bytes):
    """上游错误体 → detail（原样保留：包络形取 detail，裸体整包进）。"""
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
    """/gw 一次转发。顺序：token → 白名单 → 拨号 → 归一/透传。"""
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
    except GenieHTTPError:            # status=None：SDK 侧传输异常形状
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


assert tuple(ENVELOPE_KEYS) == ("code", "message", "detail")   # 键集对齐
