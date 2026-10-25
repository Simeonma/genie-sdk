"""genie.models.resolve — 参数解析（COMSOL 式参数替换 + 求值结果校验）。

> 2026-10 迁移阶段 1a：自内核 ``fdtd/serve/schema.py`` 1063-1285 行逐字
> 迁入，**跳过 parse_config 编排**——它的尾巴上调内核资源守卫
> ``validate_resources``（部署策略），编排壳留在内核薄壳里。消费方：
> 内核 ``fdtd/serve/schema.parse_config`` 与 sweep 逐点校验；SDK 侧
> 阶段 2 起 builder→SimConfig 校验复用同一双手。

两段校验语义（内核原文）：求值必须在前（表达式字符串过不了数值字段
的类型层），值守卫在求值结果上跑即达成同一语义；幂等锚——无数值槽
字符串时输出与输入逐值相等（老配置零扰动）。
"""
from __future__ import annotations

import copy
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .expr import (PARAM_NAME_RULE, ExprError, eval_expr,
                   resolve_parameter_table, valid_param_name)
from ._wire import SimConfig

__all__ = ["unwrap_annotation", "discriminator_name", "select_union_member",
           "resolve_parameters", "resolve_parameters_detailed",
           "check_raw_param_keys", "validate_resolved", "validate_for_ui"]


def unwrap_annotation(ann) -> tuple[object, tuple | None]:
    """剥 Annotated：返回 (裸注解, metadata 元组)。"""
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        ann = ann.__origin__
    return ann, meta


def discriminator_name(meta) -> str | None:
    """Annotated[Union[...], Field(discriminator=...)] 的判别名。"""
    for m in meta or ():
        d = getattr(m, "discriminator", None)
        if isinstance(d, str):
            return d
        d = getattr(d, "discriminator", None)   # Discriminator 对象形态
        if isinstance(d, str):
            return d
    return None


def select_union_member(members, disc_value):
    """按 discriminator 字面值选 Union 成员（各成员 type=Literal[...]）。"""
    for m in members:
        if m in (float, int, str, bool, type(None)):
            continue
        lit = m.model_fields.get("type")
        if lit is None:
            continue
        for a in get_args(unwrap_annotation(lit.annotation)[0]) or ():
            if a == disc_value:
                return m
    return None


def _path_str(path) -> str:
    """路径段 → 配置内路径串：("source",0,"freq") → "source[0].freq"。"""
    out = ""
    for seg in path:
        out += f"[{seg}]" if isinstance(seg, int) else (("." + seg) if out else seg)
    return out


def _resolve_value(ann, value, path, env, expr_at):
    """按 SimConfig 注解伴走原始 dict 树，数值槽字符串 → 表达式求值。

    返回（可能是新的）标量值；容器就地改（调用方对 dict/list 元素回写）。
    非数值槽（str 字段、bool、Literal 等）不触碰——幂等锚：无数值槽
    字符串时输出与输入逐值相等（老配置零扰动）。
    """
    ann, meta = unwrap_annotation(ann)
    members = (get_args(ann)
               if get_origin(ann) is Union else None)
    if members is not None:
        disc = discriminator_name(meta)
        if disc is not None and isinstance(value, dict):
            sub = select_union_member(members, value.get(disc))
            if sub is not None:
                return _resolve_value(sub, value, path, env, expr_at)
            return value          # 未知判别值 → 留给 pydantic 422
        nonnone = [m for m in members if m is not type(None)]
        if len(nonnone) == 1:     # Optional[X] 退包
            return _resolve_value(nonnone[0], value, path, env, expr_at)
        # NumOrTensor 类（数值 ∪ tuple/list）：str → 求值；序列 → 逐元
        if isinstance(value, str) and any(m in (float, int) for m in nonnone):
            return _eval_slot(value, path, env, expr_at)
        if isinstance(value, (list, tuple)):
            return [_resolve_seq_elem(v, path + [i], env, expr_at)
                    for i, v in enumerate(value)]
        return value
    if ann in (float, int):
        if isinstance(value, str):
            return _eval_slot(value, path, env, expr_at)
        return value
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        if isinstance(value, dict):
            for fname, finfo in ann.model_fields.items():
                if fname == "parameters":    # 参数节由拓扑求值整段处理
                    continue
                if fname in value:
                    value[fname] = _resolve_value(
                        finfo.annotation, value[fname], path + [fname],
                        env, expr_at)
        return value
    origin = get_origin(ann)
    if origin is tuple:
        args = get_args(ann)
        if isinstance(value, (list, tuple)):
            # G122（R16）：不就地改写——Python 调用方传 tuple 时
            # `value[i]=` 炸裸 TypeError；建新容器（list 进 list 出、
            # tuple 进 tuple 出）。
            out = list(value)
            for i, v in enumerate(out):
                elem = (args[i] if i < len(args)
                        else (float if args and args[-1] is Ellipsis else float))
                out[i] = _resolve_value(elem, v, path + [i], env, expr_at)
            return tuple(out) if isinstance(value, tuple) else out
        return value
    if origin is list:
        args = get_args(ann)
        elem = args[0] if args else float
        if isinstance(value, list):
            return [_resolve_value(elem, v, path + [i], env, expr_at)
                    for i, v in enumerate(value)]
        return value
    if origin is dict:
        args = get_args(ann)
        val_ann = args[1] if len(args) == 2 else float
        if isinstance(value, dict):
            return {k: _resolve_value(val_ann, v, path + [k], env, expr_at)
                    for k, v in value.items()}
        return value
    return value                      # str/bool/Literal/未知形态：不触碰


def _resolve_seq_elem(v, path, env, expr_at):
    """NumOrTensor 序列元素：str → 求值；嵌套 list（3×3 张量行）逐元。"""
    if isinstance(v, str):
        return _eval_slot(v, path, env, expr_at)
    if isinstance(v, list):
        return [_resolve_seq_elem(x, path + [i], env, expr_at)
                for i, x in enumerate(v)]
    return v


def _eval_slot(expr: str, path, env, expr_at) -> float:
    """单个数值槽表达式求值；错误带配置内路径（如 source[0].freq）。"""
    try:
        v = eval_expr(expr, env)
    except ExprError as e:
        raise ExprError(
            e.kind,
            f"{_path_str(path)}: failed to evaluate expression {expr!r}: "
            f"{e.msg}") from e
    expr_at[tuple(path)] = expr
    return v


def resolve_parameters_detailed(raw: dict) -> tuple[dict, dict]:
    """resolve_parameters 的内部形态：额外返回 {路径段元组: 表达式原文}
    （两段校验第二段守卫错误指路"表达式 t 的求值结果"用）。"""
    out = copy.deepcopy(raw)
    expr_at: dict = {}
    raw_params = out.get("parameters")
    if raw_params:
        try:
            table = resolve_parameter_table(raw_params)
        except ExprError as e:
            raise ExprError(
                e.kind, f"parameters table evaluation failed: {e.msg}") from e
        out["parameters"] = dict(table)
    else:
        table = {}
    _resolve_value(SimConfig, out, [], table, expr_at)
    return out, expr_at


def resolve_parameters(raw: dict) -> dict:
    """COMSOL 式参数替换：参数节拓扑求值 → 全数值字段的表达式槽求值。

    - 参数节（``parameters``）：值=数值或表达式串（环境=其余参数，
      声明序无关；环 → ExprError("cycle") 带环链；未知名 → 列可用名）；
    - 数值字段（含 Optional/带约束 Annotated/tuple 内元素/dict 值/
      NumOrTensor 张量元）：原值为 str → 按参数环境求值，错误带配置内
      路径（如 ``source[0].freq``）；
    - **幂等**：无数值槽字符串时输出与输入逐值相等（老配置零扰动）。
    纯函数：不改动入参（deepcopy 后伴走）。
    """
    return resolve_parameters_detailed(raw)[0]


def check_raw_param_keys(data: dict) -> None:
    """参数键名前置检查（求值前拦——名字错的表达式不值得求值）。"""
    params = data.get("parameters")
    if not params:
        return
    for k in params:
        if not valid_param_name(k):
            raise ExprError(
                "syntax",
                f"parameter name {k!r} is invalid: {PARAM_NAME_RULE} — "
                f"it is referenced by expression slots in numeric fields "
                f"and by sweep axes (parameters[name])")

def validate_resolved(resolved: dict, expr_at: dict) -> SimConfig:
    """求值结果的模型校验（parse_config 与 sweep 逐点校验共用）。

    失败位置若是表达式槽 → ExprError(kind="guard") 指路
    "表达式 t 的求值结果"（被既有值守卫拦下）；否则原样 ValidationError
    （真结构/字面量错）。"""
    try:
        return SimConfig.model_validate(resolved)
    except ValidationError as e:
        first = e.errors()[0]
        loc = tuple(first["loc"])
        if loc in expr_at:
            raise ExprError(
                "guard",
                f"{_path_str(loc)}: {first['msg']} "
                f"(result of evaluating expression {expr_at[loc]!r})") from e
        raise


def validate_for_ui(cfg: dict) -> list[dict]:
    """设计态预检的**唯一实现**：返回错误清单（空 = 通过）。

    消费方（此前各抄一份八行，2026-10-18 接口整理轮收口）：
    ``/api/validate``（本地 Studio、云宿主、公共服务网关）、``.gensim``
    导入前的配置校验、宿主 agent 的 ``validate_cfg`` 工具。

    与内核 ``parse_config`` 的关系 = 同一双手、少一步：这里跑
    「参数键名 → 拓扑求值 → 结构/值守卫」，**不跑资源守卫**（那属内核
    部署策略，阈值不进 SDK）。返回形状 ``[{loc, msg}]``——``loc`` 是
    配置内路径（如 ``source[0].freq``），空串表示全局错误。

    收口前 agent 路径漏了 ``check_raw_param_keys``：同一份非法参数名的
    配置，UI 红框、agent 工具却说 OK——这类"宣言与实现不同步"正是本
    函数存在的理由（DISCIPLINE §5：改判定只改这里）。
    """
    if not isinstance(cfg, dict):
        return [{"loc": "", "msg": "cfg must be an object (SimConfig JSON)"}]
    try:
        check_raw_param_keys(cfg)                     # 名字错在求值前拦
        resolved, expr_at = resolve_parameters_detailed(cfg)
        validate_resolved(resolved, expr_at)
    except ValidationError as e:
        return [{"loc": ".".join(str(x) for x in er["loc"]), "msg": er["msg"]}
                for er in e.errors()]
    except ExprError as e:
        return [{"loc": "", "msg": f"{e.kind}: {e.msg}"}]
    except Exception as e:  # noqa: BLE001 —— 其余解析失败同样按红框语义回
        return [{"loc": "", "msg": f"{getattr(e, 'kind', 'error')}: {e}"}]
    return []


# ── 兼容别名（2026-10-18 接口整理轮已删除）────────────────────────────
# 曾在此保留 `_check_raw_param_keys` / `_resolve_parameters_impl` /
# `_validate_resolved` 等 `_` 前缀别名，供内核薄壳过渡期 import。内核已
# 改用公共名（genie-fdtd:fdtd/serve/schema.py、sweep.py），双名字表随之
# 删除——新增消费方一律用公共名，见 SERVICE.md 历史 D9。
