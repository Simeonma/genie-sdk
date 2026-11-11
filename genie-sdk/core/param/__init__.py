# core/param — 参数求值（文档编辑能力，2026-11-09 自 genie_protocol 迁入）
"""求值器（expr）+ 参数解析（resolve）。

**为什么在这里**：参数系统是 SDK 的编辑器能力——文档保留用户写的形态
（表达式 + 参数表），payload 带数字（worker/chain._resolve）。内核不
参与求值（2026-11-09 起内核契约 = 只收数字，genie-optics 87635f9）。
原住 genie_protocol.expr / resolve，随该契约定案迁出。

**例外**：参数名规则（genie_protocol._paramname）留在协议里——"什么是
一个合法参数名"是线格式契约（_wire.SimConfig._check_param_names 要用），
两边必须同意；本包进口它，不另立实现。
"""
_EXPR = frozenset({
    "ExprError", "PARAM_NAME_RULE", "eval_expr", "param_name_error",
    "resolve_parameter_table", "valid_param_name",
})
_RESOLVE = frozenset({
    "check_raw_param_keys", "resolve_parameters",
    "resolve_parameters_detailed", "validate_for_ui", "validate_resolved",
})

__all__ = sorted(_EXPR | _RESOLVE)


def __getattr__(name: str):
    # 懒壳（PEP 562，沿袭 genie_protocol.__init__ 的纪律）：import
    # genie.core.param.expr 不拉 resolve → 不拉 pydantic——求值器保持
    # 纯 stdlib 可独立 import（tests/protocol/test_b1_breaking 锁着）。
    if name in _EXPR:
        from . import expr
        return getattr(expr, name)
    if name in _RESOLVE:
        from . import resolve
        return getattr(resolve, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(__all__)
