"""genie.core.sweep — SweepSpec 展开器（SDK 本地驱动件，不上线）。

**2026-11-03 自 ``genie_protocol`` 迁入（DECISIONS Q7.1）**：远方（relay 网关 /
BSCC 内核）零消费者，扫参展开是驱动层的活——SweepSpec 根本不上线（客户端展开后
提交的是逐点普通配置）。行为与语义逐字保留，仅模型定义与常量随迁；原线格式位置
（``_wire.py``）同版删除。

把 base SimConfig JSON + SweepSpec 展开为逐点**已解析**配置：

- 纯展开零调度：参数替换、product/zip 组合、逐点深拷贝 + 逐点重解析；
- name 轴语义不变：``name`` 必须在 base ``parameters`` 键集内（不在 →
  SweepExpandError 列可用名）；展开 = 逐点 ``parameters[name]=v`` →
  ``resolve_parameters`` 重解析——引用该参数的全部表达式槽逐点联动；
- 逐点错误带**第几点（1 基）+ 轴名**，提示合法前缀数量；
- 返回可直接 POST /api/jobs 的具体数字配置。

v2 语境（Q2.2 收口 2026-11-04）：驱动经内部原语 ``f._submit`` / ``f._wait`` /
``f._cancel`` 编排（公开面是融合的 ``f.submit``）；本模块是扫参这类驱动的
展开手，不是调度器。
"""
from __future__ import annotations

import copy
import itertools
import math
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from genie_protocol.expr import ExprError, valid_param_name
from genie_protocol.resolve import resolve_parameters_detailed, validate_resolved

__all__ = ["SweepSpec", "SweepAxisSpec", "SweepExpandError",
           "expand_sweep", "validate_sweep_config"]

MAX_SWEEP_POINTS = 512           # sweep 展开点数上限（随 Q7.1 迁入，SDK 本地常量）
MAX_SWEEP_AXES = 32              # sweep 参数轴数上限


class _StrictModel(BaseModel):
    """随迁的线格式纪律：未知字段一律拒绝（原 ``genie_protocol._wire`` 同款）。"""

    model_config = ConfigDict(extra="forbid")


class SweepAxisSpec(_StrictModel):
    """单参数扫描轴（**参数名寻址**，2026-10 破坏性改版语义随迁）。

    - ``name``：**参数名**——必须是基底配置 ``parameters`` 键集内的
      名（不在 → 422 列可用名）；展开语义 = 逐点 ``parameters[name]=v``
      后重解析（引用该参数的全部表达式槽逐点联动重求值）；
    - 取值三选一互斥：``values``（显式列表；str 成员按**表达式串**
      逐点求值——非枚举型，未定义名会在逐点解析报 unknown_name）|
      ``linspace`` = [start, stop, n]（数值，含端点，n>=2）|
      ``logspace`` = [start, stop, n]（10^x，同规则）；
      多给/全缺 → 策展错误指路；``linspace/logspace`` 的
      start==stop 拒收（单值轴请直接 values=[x]——退化区间是笔误的
      温床，宁拒不放行）。
    """

    name: str = Field(min_length=1, max_length=64)
    values: Optional[list[Union[float, int, str, list[float]]]] = None
    linspace: Optional[tuple[float, float, int]] = None
    logspace: Optional[tuple[float, float, int]] = None

    @field_validator("name", mode="after")
    @classmethod
    def _check_name(cls, v):
        if not valid_param_name(v):
            raise ValueError(
                f"axis name {v!r} is invalid: must be a parameter name "
                f"(identifier, <= 64 chars) that exists in the base "
                f"config's parameters keys")
        return v

    @model_validator(mode="after")
    def _check(self):
        given = [x for x in (self.values, self.linspace, self.logspace)
                 if x is not None]
        if len(given) != 1:
            raise ValueError(
                "exactly one of values / linspace / logspace must be "
                f"given (got {len(given)})")
        for name in ("linspace", "logspace"):
            sp = getattr(self, name)
            if sp is None:
                continue
            start, stop, n = sp
            if not (math.isfinite(start) and math.isfinite(stop)):
                raise ValueError(
                    f"{name} start/stop must be finite, got {sp[:2]!r}")
            if n < 2:
                raise ValueError(f"{name}.n must be an integer >= 2, got {n}")
            if start == stop:
                raise ValueError(
                    f"{name} start==stop=={start} is rejected "
                    f"(use values=[x] for a single-value axis)")
        if self.values is not None and len(self.values) == 0:
            raise ValueError(
                "values must be non-empty (a single-element list [x] is "
                "valid)")
        return self

    def value_list(self) -> list:
        """该轴的取值序列（values 原样；linspace/logspace 按公式展开）。

        logspace 展开值查有限（G124⑤：start=400 → 10**400=inf，此前
        不查即穿）。linspace 不查数值合理性——DISCIPLINE §5：输入数值
        的正确性由用户负责（R21 曾加对称检查，站长 2026-10-27 裁回退）。"""
        if self.values is not None:
            return list(self.values)
        start, stop, n = self.linspace if self.linspace else self.logspace
        if self.linspace is not None:
            return [start + i * (stop - start) / (n - 1)
                    for i in range(n)]
        vals = []
        for i in range(n):
            try:
                vals.append(10 ** (start + i * (stop - start) / (n - 1)))
            except OverflowError:   # G124⑤：10**400 直接 OverflowError
                raise ValueError(
                    f"logspace [{start}, {stop}] produces values out of "
                    f"float range (10^x overflow) — narrow the range"
                ) from None
        if not all(math.isfinite(x) for x in vals):
            raise ValueError(
                f"logspace [{start}, {stop}] produces non-finite values "
                f"(10^x overflow) — narrow the range")
        return vals


class SweepSpec(_StrictModel):
    """顶层参数扫描**定义**（纯本地值；P2-α 起源 + R39 C+ 纯化语义随迁）。

    - ``parameters``：1..32 轴（SweepAxisSpec）；
    - ``mode``：``product``（默认，笛卡尔积）| ``zip``（各轴等长逐点
      配对，长度不等 → 错误）；
    - 展开点数上限 512（超限报点数/上限）。

    R39（G169 站长裁 C+）语义随迁：执行旋钮不进规格——SweepSpec 是纯定义。
    """

    parameters: list[SweepAxisSpec] = Field(min_length=1,
                                            max_length=MAX_SWEEP_AXES)
    mode: Literal["product", "zip"] = "product"

    @model_validator(mode="after")
    def _check(self):
        lens = [len(a.value_list()) for a in self.parameters]
        if self.mode == "zip":
            if len(set(lens)) != 1:
                raise ValueError(
                    f"zip mode requires all axes to have equal lengths, "
                    f"got {lens}")
            n_points = lens[0] if lens else 0
        else:
            n_points = 1
            for L in lens:
                n_points *= L
        if n_points > MAX_SWEEP_POINTS:
            raise ValueError(
                f"expanded point count {n_points} exceeds the limit "
                f"{MAX_SWEEP_POINTS} (axis lengths {lens}; reduce the "
                f"number of points)")
        return self


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
        # 2026-10-27 R21 复审 C1 随迁：parameters=5 之类畸形 base 在
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
