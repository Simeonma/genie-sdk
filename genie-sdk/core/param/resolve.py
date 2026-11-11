"""genie_protocol.resolve — parameter resolution (COMSOL-style parameter
substitution + evaluation-result validation).

Two-stage validation semantics: evaluation must come first (an expression
string cannot pass the type layer of a numeric field), and running the
value guard on the evaluation result achieves the same semantics;
idempotency anchor — with no numeric-slot strings, the output is
value-by-value equal to the input (zero disturbance to legacy configs).
"""
from __future__ import annotations

import copy
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .expr import (PARAM_NAME_RULE, ExprError, eval_expr,
                   param_name_error, resolve_parameter_table,
                   valid_param_name)
from genie_protocol import SimConfig  # 公开名（__all__ 在册）

__all__ = ["unwrap_annotation", "discriminator_name", "select_union_member",
           "resolve_parameters", "resolve_parameters_detailed",
           "check_raw_param_keys", "validate_resolved", "validate_for_ui"]


def unwrap_annotation(ann) -> tuple[object, tuple | None]:
    """Strip Annotated: return (bare annotation, metadata tuple)."""
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        ann = ann.__origin__
    return ann, meta


def discriminator_name(meta) -> str | None:
    """The discriminator name of Annotated[Union[...], Field(discriminator=...)]."""
    for m in meta or ():
        d = getattr(m, "discriminator", None)
        if isinstance(d, str):
            return d
        d = getattr(d, "discriminator", None)   # Discriminator object form
        if isinstance(d, str):
            return d
    return None


def select_union_member(members, disc_value):
    """Select the Union member by discriminator literal value (each member has type=Literal[...])."""
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
    """Path segments → in-config path string: ("source",0,"freq") → "source[0].freq"."""
    out = ""
    for seg in path:
        out += f"[{seg}]" if isinstance(seg, int) else (("." + seg) if out else seg)
    return out


def _resolve_value(ann, value, path, env, expr_at):
    """Walk the raw dict tree alongside the SimConfig annotations;
    numeric-slot strings → expression evaluation.

    Returns the (possibly new) scalar value; containers are modified in
    place (the caller writes dict/list elements back). Non-numeric slots
    (str fields, bool, Literal, etc.) are left untouched — idempotency
    anchor: with no numeric-slot strings, the output is value-by-value
    equal to the input (zero disturbance to legacy configs).
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
            return value          # unknown discriminator value → left to pydantic 422
        nonnone = [m for m in members if m is not type(None)]
        if len(nonnone) == 1:     # unwrap Optional[X]
            return _resolve_value(nonnone[0], value, path, env, expr_at)
        # NumOrTensor-like (numeric ∪ tuple/list): str → evaluate; sequences → element-wise
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
                if fname == "parameters":    # the parameters section is handled wholesale by topological evaluation
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
            # Do not rewrite in place — when a Python caller passes a
            # tuple, `value[i]=` blows up with a bare TypeError; build a
            # new container (list in, list out; tuple in, tuple out).
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
    return value                      # str/bool/Literal/unknown shapes: untouched


def _resolve_seq_elem(v, path, env, expr_at):
    """NumOrTensor sequence element: str → evaluate; nested lists (3×3 tensor rows) element-wise."""
    if isinstance(v, str):
        return _eval_slot(v, path, env, expr_at)
    if isinstance(v, list):
        return [_resolve_seq_elem(x, path + [i], env, expr_at)
                for i, x in enumerate(v)]
    return v


def _eval_slot(expr: str, path, env, expr_at) -> float:
    """Evaluate a single numeric-slot expression; errors carry the in-config path (e.g. source[0].freq)."""
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
    """The internal form of resolve_parameters: additionally returns
    {path-segment tuple: original expression text} (used by the second
    stage of the two-stage validation to point guard errors at "the
    evaluation result of expression t").

    Q17 nested house: the parameters table lives at
    ``model.parameters``; the solver ``config`` (and every plan stage's
    ``config``) is a union without a discriminator, so the generic
    annotation walk cannot pick a member — the wire's own dispatch rule
    (``_CONFIG_OF[solver]``, omit = fdtd) is applied here first, then
    the generic walk handles the rest (already-resolved dicts pass
    through untouched — idempotency anchor holds).
    """
    from genie_protocol._wire import _CONFIG_OF

    out = copy.deepcopy(raw)
    expr_at: dict = {}
    model = out.get("model")
    model = model if isinstance(model, dict) else {}
    raw_params = model.get("parameters")
    if raw_params:
        try:
            table = resolve_parameter_table(raw_params)
        except ExprError as e:
            raise ExprError(
                e.kind, f"parameters table evaluation failed: {e.msg}") from e
        model["parameters"] = dict(table)
        out["model"] = model
    else:
        table = {}
    # config / plan-stage configs: explicit dispatch (no discriminator
    # on the wire union), expressions resolved in place.
    if out.get("plan") is None:
        solver = out.get("solver") or "fdtd"
        spec = _CONFIG_OF.get(solver)
        if spec is not None and isinstance(out.get("config"), dict):
            out["config"] = _resolve_value(spec, out["config"],
                                           ["config"], table, expr_at)
    else:
        for i, st in enumerate(out["plan"]):
            if not isinstance(st, dict):
                continue
            spec = _CONFIG_OF.get(st.get("solver"))
            if spec is not None and isinstance(st.get("config"), dict):
                st["config"] = _resolve_value(
                    spec, st["config"], ["plan", i, "config"],
                    table, expr_at)
    _resolve_value(SimConfig, out, [], table, expr_at)
    return out, expr_at


def resolve_parameters(raw: dict) -> dict:
    """COMSOL-style parameter substitution: topological evaluation of the
    parameters section → expression-slot evaluation over all numeric fields.

    - Parameters section (``parameters``): values are numbers or expression
      strings (environment = the other parameters, declaration order
      irrelevant; cycles → ExprError("cycle") with the cycle chain; unknown
      names → lists the available names);
    - Numeric fields (including Optional / constrained Annotated / tuple
      elements / dict values / NumOrTensor tensor elements): when the
      original value is a str → evaluate against the parameter environment,
      errors carrying the in-config path (e.g. ``source[0].freq``);
    - **Idempotent**: with no numeric-slot strings, the output is
      value-by-value equal to the input (zero disturbance to legacy
      configs).
    Pure function: does not modify its input (deepcopy first, then walk
    along).
    """
    return resolve_parameters_detailed(raw)[0]


def check_raw_param_keys(data: dict) -> None:
    """Upfront check of parameter key names (intercept before evaluation — an expression with a bad name is not worth evaluating)."""
    model = data.get("model")
    params = model.get("parameters") if isinstance(model, dict) else None
    if not params:
        return
    for k in params:
        if not valid_param_name(k):
            # reuse the full wording from expr.param_name_error (single source)
            raise ExprError("syntax", param_name_error(k))

def validate_resolved(resolved: dict, expr_at: dict) -> SimConfig:
    """Model validation of the evaluation result.

    If the failing location is an expression slot → ExprError(kind="guard")
    pointing at "the evaluation result of expression t" (intercepted by the
    existing value guard); otherwise the ValidationError propagates as-is
    (a real structural / literal error)."""
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
    """Design-time preflight: returns the error list (empty = passed).

    This runs "parameter key names → topological evaluation → structural /
    value guards" and **does not run the resource guard** (resource
    thresholds are deployment policy and do not enter the SDK). Return
    shape ``[{loc, msg}]`` — ``loc`` is the in-config path (e.g.
    ``source[0].freq``); the empty string means a global error.
    """
    if not isinstance(cfg, dict):
        return [{"loc": "", "msg": "cfg must be an object (SimConfig JSON)"}]
    try:
        check_raw_param_keys(cfg)                     # intercept bad names before evaluation
        resolved, expr_at = resolve_parameters_detailed(cfg)
        validate_resolved(resolved, expr_at)
    except ValidationError as e:
        return [{"loc": ".".join(str(x) for x in er["loc"]), "msg": er["msg"]}
                for er in e.errors()]
    except ExprError as e:
        return [{"loc": "", "msg": f"{e.kind}: {e.msg}"}]
    except Exception as e:  # noqa: BLE001 — other resolution failures also come back as red-box semantics
        return [{"loc": "", "msg": f"{getattr(e, 'kind', 'error')}: {e}"}]
    return []
