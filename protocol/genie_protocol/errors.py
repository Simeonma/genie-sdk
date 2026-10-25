"""异常类型 — SDK 对外错误契约（住最底层：core 与 net 共用的词汇）。

层级::

    GenieError                  基类（携带 HTTP status / 服务端原始 detail）
    ├─ GenieConfigError         本地配置预检失败（提交边界，genie.models 同双手）
    ├─ GenieHTTPError           请求层失败（4xx/5xx：校验错、key 无效、…）
    └─ GenieJobError            作业终态失败（failed/killed：带日志尾）

分层依据：这些类型被 core（simulate/sweep 抛）与 net（client 抛）同时使用，
按"依赖只能指向更稳定层"的判据放 models（2026-10-23 自 core/errors 迁入；
``genie.GenieError`` 等公开别名经根包重导出，用户面不受影响）。

设计要点：服务端（pydantic ValidationError / 资源守卫 GuardError）的
原始信息**原样保留**在 ``.detail``，消息体只做可读化拼接——用户排查
配置问题时看得到逐字段错误路径，不需要打开浏览器。
"""
from __future__ import annotations

from typing import Any

__all__ = ["GenieError", "GenieConfigError", "GenieHTTPError",
           "GenieJobError"]


class GenieError(Exception):
    """SDK 基础异常。``status`` = HTTP 状态码（非 HTTP 来源为 None），
    ``detail`` = 服务端返回的原始错误体（str / list / dict）。"""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class GenieConfigError(GenieError):
    """提交边界的本地配置预检失败（framework §四，阶段 ④）。

    校验用 ``genie.models``（resolve + validate）——与服务端 parse 同一
    双手，错误路径/文案一致；区别只是提前到本地 fail-fast（preview 半
    成品不受影响——预检只在 ``run()``/``sweep()`` 的提交路径上）。"""


class GenieHTTPError(GenieError):
    """请求失败（4xx/5xx）。常见：401 key 无效、400 守卫拒绝、422 配置形状错。"""


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
