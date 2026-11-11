# _paramname — 参数名契约（线格式的一部分）
"""什么是一个合法的参数名——SimConfig.parameters 的键规则。

2026-11-09 自 expr.py 拆出（求值器迁 genie-sdk/core/param 轮）：
**名字规则是线格式契约**（_wire.SimConfig._check_param_names 的守卫），
两边必须同意，故留协议；表达式**怎么求值**是 SDK 的编辑器能力，
已迁 genie.core.param.expr。消费方 = _wire + genie.core.param（进口
不另立）。
"""
from __future__ import annotations

import re

__all__ = ["PARAM_NAME_RULE", "param_name_error", "valid_param_name"]

PARAM_NAME_RULE = (
    "must be an identifier (starting with a letter or underscore, "
    "letters/digits/underscores only, <= 64 chars)")

_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def param_name_error(k: str) -> str:
    """The full way-pointing error for an illegal parameter name (the
    single source of **the entire wording**). Consumers =
    resolve.check_raw_param_keys (pre-evaluation check) and
    _wire.SimConfig._check_param_names (a wire-format contract guard — the
    sole interceptor of **unreferenced** bad names on the sweep expansion
    path; do not remove)."""
    return (f"parameter name {k!r} is invalid: {PARAM_NAME_RULE} — "
            f"it is referenced by expression slots in numeric fields "
            f"and by sweep axes (parameters[name])")

def valid_param_name(name: str) -> bool:
    """Parameter-name validity (for schema to reuse the same hands: identifier + ≤64 chars)."""
    return (isinstance(name, str) and len(name) <= 64
            and _PARAM_NAME_RE.fullmatch(name) is not None)
