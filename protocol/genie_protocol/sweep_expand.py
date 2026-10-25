"""SweepSpec 展开器（G29b 2026-10-21 自 fdtd/serve/sweep.py 移植——
sweep 降维为普通作业后，展开是客户端的活）。

把 base SimConfig JSON + SweepSpec 展开为逐点**已解析**配置：

- 纯展开零调度：参数替换、product/zip 组合、逐点深拷贝 + 逐点重解析；
- name 轴语义不变：``name`` 必须在 base ``parameters`` 键集内（不在 →
  SweepExpandError 列可用名）；展开 = 逐点 ``parameters[name]=v`` →
  ``resolve_parameters`` 重解析——引用该参数的全部表达式槽逐点联动；
- 逐点错误带**第几点（1 基）+ 轴名**，提示合法前缀数量；
- 返回可直接 POST /api/jobs 的具体数字配置（与旧服务端 422 面同源，
  只是裁决点从服务端搬到客户端——错误信息零变化）。
"""
from __future__ import annotations

import copy
import itertools

from pydantic import ValidationError

from .expr import ExprError
from .resolve import resolve_parameters_detailed, validate_resolved
from ._wire import SweepSpec

__all__ = ["SweepExpandError", "expand_sweep", "validate_sweep_config"]


class SweepExpandError(ValueError):
    """展开期错误（轴名不在参数表 / 逐点解析校验失败）。

    与旧服务端 422 策展错误同档：payload 形状/取值错，非资源守卫。"""


def expand_sweep(base: dict, sweep: SweepSpec) -> list[dict]:
    """展开：每点 = deepcopy(base) + 各轴值写进 parameters[name]。

    product（默认）= 笛卡尔积，axis 顺序为最外层循环；zip = 各轴
    等长逐点配对。纯函数：不改 base、**不解析表达式**（解析与逐点
    校验归 validate_sweep_config）。
    """
    axes = [(a.name, a.value_list()) for a in sweep.parameters]
    if sweep.mode == "zip":
        combos = zip(*(vals for _, vals in axes))
    else:
        combos = itertools.product(*(vals for _, vals in axes))
    out = []
    for combo in combos:
        point = copy.deepcopy(base)
        params = point.setdefault("parameters", {})
        for (name, _), value in zip(axes, combo):
            params[name] = copy.deepcopy(value)
        out.append(point)
    return out


def _check_axis_names(sweep: SweepSpec, base: dict) -> None:
    known = base.get("parameters") or {}
    if not isinstance(known, dict):
        # 2026-10-27 R21 复审 C1：parameters=5 之类畸形 base 此前在
        # sorted(known) 抛裸 TypeError，逃出 SweepExpandError 错误契约
        # （expand_sweep 的 setdefault 同样会崩——守卫收在最前一道）。
        raise SweepExpandError(
            f"base config 'parameters' must be a mapping of parameter "
            f"names, got {type(known).__name__}")
    available = sorted(known)
    for a in sweep.parameters:
        if a.name not in known:
            raise SweepExpandError(
                f"axis name {a.name!r} not in base config parameters"
                + (f" (available: {available})" if available
                   else " (base config has no parameters section)"))


def _first_validation_msg(e: ValidationError) -> str:
    first = e.errors()[0]
    loc = ".".join(str(x) for x in first["loc"])
    return f"{loc}: {first['msg']}"


def validate_sweep_config(sweep: SweepSpec, base: dict) -> list[dict]:
    """提交端校验：轴名检查 + 展开 + 逐点求值解析 + 两段校验。

    任一点非法 → SweepExpandError 带**第几个点（1 基）+ 轴名 + 原因**
    （错误信息含"此前 N-1 点均合法"提示）。全部合法 → 返回**已解析**
    的逐点配置列表（可直接 POST /api/jobs）。
    """
    _check_axis_names(sweep, base)
    axis_names = [a.name for a in sweep.parameters]
    points = expand_sweep(base, sweep)
    resolved_points: list[dict] = []
    for i, point in enumerate(points, start=1):
        try:
            resolved, expr_at = resolve_parameters_detailed(point)
            validate_resolved(resolved, expr_at)
        except (ExprError, ValidationError) as e:
            msg = (f"{getattr(e, 'msg', '')}" if isinstance(e, ExprError)
                   else _first_validation_msg(e))
            raise SweepExpandError(
                f"point {i} of {len(points)} (axes {axis_names}) is "
                f"invalid: {msg}; the first {i - 1} points were valid"
            ) from e
        resolved_points.append(resolved)
    return resolved_points
