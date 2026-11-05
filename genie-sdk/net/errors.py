"""genie.net.errors — SDK 对外错误契约（GenieError 家族）。

**2026-11-03 自 ``genie_protocol.errors`` 迁入（DECISIONS Q7.1）**：远方（relay
网关 / BSCC 内核）零消费者；三个异常都是「与远端交互失败」的词汇，net 是它们的
家（net 不依赖 core，故不住 core）。行为与层级逐字保留。

层级::

    GenieError                  基类（携带 HTTP status / 服务端原始 detail）
    ├─ GenieConfigError         本地配置预检失败（提交边界）
    ├─ GenieHTTPError           请求层失败（4xx/5xx：校验错、key 无效、…）
    └─ GenieJobError            作业终态失败（failed/killed：带日志尾）

设计要点：服务端（pydantic ValidationError / 资源守卫 GuardError）的
原始信息**原样保留**在 ``.detail``，消息体只做可读化拼接——用户排查
配置问题时看得到逐字段错误路径，不需要打开浏览器。

v2 语境（Q2.2-5A）：host 的 ``/gw`` 把上游错误归一成 code（auth /
upstream / resource / rate / server，英文固定话）；本模块是 SDK 侧的
异常**词汇**，不是第二份措辞面。
"""
from __future__ import annotations

from typing import Any

__all__ = ["GenieError", "GenieConfigError", "GenieHTTPError",
           "GenieJobError", "GW_CODES", "ENVELOPE_KEYS"]

#: /gw 错误包络键（host/gw 产、client 吃；Q2.2-5A）
ENVELOPE_KEYS = ("code", "message", "detail")

#: 五码表：码 → 英文固定话（**唯一一份**；2026-11-04 站长拍定与异常同文件）。
#: host/gw 引用产包络、client 传输失败引表，都不手抄；消费者只认 code、
#: 显示 message，不设第二份映射。
GW_CODES = {
    "auth": "Authentication required",
    "upstream": "Upstream unreachable",
    "resource": "Request rejected",
    "rate": "Rate limited",
    "server": "Server error",
}


class GenieError(Exception):
    """SDK 基础异常。``status`` = HTTP 状态码（非 HTTP 来源为 None），
    ``detail`` = 服务端返回的原始错误体（str / list / dict）。"""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class GenieConfigError(GenieError):
    """提交边界的本地配置预检失败。

    校验走 ``genie_protocol``（resolve + validate_for_ui）——与网关提交闸
    同一双手，错误路径/文案一致；区别只是提前到本地 fail-fast。"""


class GenieHTTPError(GenieError):
    """请求失败（4xx/5xx）或传输失败（``status=None``）。``code`` = /gw
    五码（Q2.2-5A；传输失败固定 ``"upstream"``，句出自 ``GW_CODES``）。"""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None, code: str | None = None):
        super().__init__(message, status=status, detail=detail)
        self.code = code


class GenieJobError(GenieError):
    """作业以 failed/killed 终态结束。``error`` = 服务端错误摘要，
    ``log_tail`` = driver 日志尾（排障第一现场）。"""

    def __init__(self, job_id: str, state: str, error: str | None = None,
                 log_tail: str | None = None):
        msg = f"作业 {job_id} 终态 = {state}"
        if error:
            msg += f": {error}"
        super().__init__(msg, detail=error)
        self.job_id = job_id
        self.state = state
        self.error = error
        self.log_tail = log_tail
