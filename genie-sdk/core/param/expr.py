"""Safe evaluation of COMSOL-style global parameters — AST whitelist walk,
zero eval/exec.

Responsibilities:

- ``eval_expr(expr, env) -> float``: arithmetic expression string → float.
  Whitelist nodes = Constant (numeric int/float) / Name (env lookup, then the
  built-in constants ``pi`` / ``c0`` / ``eps0`` / ``mu0`` — a parameter of the
  same name wins) /
  BinOp(+ - * / % **) / UnaryOp(+-) / Call (abs/min/max/sqrt only; abs/sqrt
  take a single numeric argument, min/max one or two) / unit suffix
  ``primary[unit]`` (the grammar ``primary '[' IDENT ']'`` binds tightest,
  ``2*50[nm]`` == 2*(50[nm]); semantics = pure numeric conversion
  ``a[unit] = a × unit_size``, where unit_size is the SI scale of that
  unit (nm=1e-9, THz=1e12, ...; the registry ``_UNITS`` holds 20 units
  across the length/time/frequency families, including the µm/µs Unicode
  micrometer/microsecond aliases). **No dimension checking**. **Any other
  node** (Attribute/Lambda/Compare/BoolOp/IfExp/string constants/
  non-unit-shaped Subscript/...) → ``ExprError`` carrying the node type
  name — attribute and subscript access are the main injection channels,
  and comparisons and booleans introduce three-valued logic; all are
  rejected.
- Numeric discipline: division/modulo by zero, sqrt of a negative,
  overflow, non-finite results → ``ExprError`` ("math"); empty or
  whitespace-only expressions → "syntax".
- Error tiers (for HTTP 422 curation): ``ExprError.kind`` ∈
  syntax / unknown_name / unknown_unit / illegal_node / math / nonatomic /
  cycle / guard — the first six kinds are produced by eval_expr; cycle = a
  cycle hit while topologically evaluating the parameters section (the
  message carries the full chain a→b→a); guard = an evaluation result
  intercepted by the SimConfig value guard (produced by
  resolve.validate_resolved; the exception type is shared — see the
  two-stage validation in resolve).
- ``resolve_parameter_table(params) -> dict[str, float]``: top-level
  topological evaluation of the parameters section (values are numbers or
  expression strings referencing the other parameters; environment = the
  other parameters, declaration order irrelevant).
"""
from __future__ import annotations

from genie_protocol._paramname import (
    PARAM_NAME_RULE, _PARAM_NAME_RE, param_name_error,
    valid_param_name,
)

import ast
import math
import re
import unicodedata
from collections.abc import Mapping

__all__ = ["ExprError", "eval_expr", "resolve_parameter_table",
           "valid_param_name", "param_name_error"]

# 参数名契约（什么是一个合法参数名 = 线格式契约，两边必须同意）住在
# genie_protocol._paramname——这里只进口，不另立实现。


# Call whitelist: name → (min argument count, max argument count)
_FUNCTIONS = {
    "abs": (1, 1),
    "min": (1, 2),
    "max": (1, 2),
    "sqrt": (1, 1),
}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_UNARY_OPS = {
    ast.UAdd: lambda a: a,
    ast.USub: lambda a: -a,
}


# Unit registry: a single constant, name → SI scale.
# Semantics = pure numeric conversion ``a[unit] = a × _UNITS[unit]``, no
# dimension checking; µm/µs are Unicode micrometer/microsecond aliases
# (same values as um/us, 1e-6).
_UNITS = {
    # length
    "pm": 1e-12,
    "nm": 1e-9,
    "um": 1e-6,
    "µm": 1e-6,
    "mm": 1e-3,
    "cm": 1e-2,
    "m": 1.0,
    # time
    "fs": 1e-15,
    "ps": 1e-12,
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    # frequency
    "Hz": 1.0,
    "kHz": 1e3,
    "MHz": 1e6,
    "GHz": 1e9,
    "THz": 1e12,
    "PHz": 1e15,
}
# Python identifiers are NFKC-normalized (µ U+00B5 → μ U+03BC) — keys are
# unified into the normalized form, so `1[µm]` hits the alias whether the
# source typed MICRO SIGN or GREEK MU.
_UNITS = {unicodedata.normalize("NFKC", k): v for k, v in _UNITS.items()}

# Built-in constants (SI) — usable in every expression slot without being
# declared in the parameters section: `2*pi/a`, `c0/f`. Two rules:
#   - **a parameter of the same name wins** (a document that already defines
#     `pi` in its table keeps its own value; nothing migrates);
#   - they are values, not units: no dimension checking, so `a/c0` is seconds
#     only when `a` is metres.
# eta0 = sqrt(mu0/eps0) stays derivable — never a second copy here.
_CONSTANTS = {
    "pi": math.pi,                     # ratio
    "c0": 299792458.0,                 # speed of light in vacuum, m/s
    "eps0": 8.8541878128e-12,          # vacuum permittivity, F/m
    "mu0": 1.25663706212e-6,           # vacuum permeability, H/m
}


class ExprError(ValueError):
    """Expression / parameters-section error (curated for HTTP 422). See the module docstring for kind."""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind
        self.msg = msg


def _check_finite(v: float, ctx: str) -> float:
    if not math.isfinite(v):
        raise ExprError("math", f"{ctx} produced a non-finite result ({v!r})")
    return v


def _unit_name(slice_node: ast.AST) -> str | None:
    """Recognize the unit-suffix slice name: ``primary[IDENT]`` (ast.Name,
    the canonical grammar form) or ``primary['str']`` (string-constant
    defensive form) → the unit name; any other shape (numeric / slice /
    expression) → None (keeps the existing illegal_node rejection branch).

    The return value is NFKC-normalized — consistent with Python's
    identifier normalization rule (µ → μ), so the string-constant form
    ``1['µm']`` also hits the Unicode alias key."""
    if isinstance(slice_node, ast.Name):
        return unicodedata.normalize("NFKC", slice_node.id)
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return unicodedata.normalize("NFKC", slice_node.value)
    return None


def _apply_bin(op, a: float, b: float, ctx: str) -> float:
    try:
        v = op(a, b)
    except ZeroDivisionError:
        raise ExprError(
            "math", f"{ctx} division/modulo by zero ({a!r} and {b!r})") from None
    except OverflowError:
        raise ExprError(
            "math", f"{ctx} overflow ({a!r} and {b!r})") from None
    except TypeError as e:   # e.g. implementation-level TypeError from Mod/Pow on pathological inputs
        raise ExprError("math", f"{ctx} numeric type error: {e}") from None
    if isinstance(v, complex):
        # Python returns complex for a negative base with a fractional
        # exponent ((-8)**(1/3)) — this evaluator produces real numbers
        # only, so a complex result is the math kind.
        raise ExprError(
            "math", f"{ctx} produced a complex result ({v!r}) — negative "
            "base with fractional exponent is not supported")
    return _check_finite(v, ctx)


def eval_expr(expr: str, env: dict[str, float] | None = None) -> float:
    """Whitelist evaluation: expr → float. env = the table of referencable parameter names (empty by default)."""
    env = env or {}
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("syntax", "expression is empty or whitespace-only")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError) as e:  # ValueError: NUL bytes and the like
        raise ExprError(
            "syntax", f"expression syntax error: {expr!r} ({e})") from e

    def fn_of(node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExprError(
                "illegal_node",
                f"unsupported function form {type(node.func).__name__} "
                f"(only direct calls to abs/min/max/sqrt are allowed)")
        name = node.func.id
        if name not in _FUNCTIONS:
            raise ExprError(
                "illegal_node",
                f"unsupported function {name!r} "
                f"(whitelist: {sorted(_FUNCTIONS)})")
        if node.keywords:
            raise ExprError(
                "nonatomic", "function calls do not accept keyword arguments")
        lo, hi = _FUNCTIONS[name]
        n = len(node.args)
        if not (lo <= n <= hi):
            raise ExprError(
                "nonatomic",
                f"{name} got {n} arguments (expected {lo}"
                + (f"~{hi}" if hi > lo else "") + " numeric arguments)")
        return name

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ExprError(
                    "nonatomic",
                    f"constant must be numeric, got {v!r} "
                    f"({type(v).__name__})")
            try:
                fv = float(v)
            except OverflowError:   # thousand-digit integer literals beyond the float domain
                raise ExprError(
                    "math", f"constant {v!r} out of float range") from None
            # 1e400 is already an inf constant in the AST — the Constant
            # exit likewise goes through _check_finite.
            return _check_finite(fv, "constant")
        if isinstance(node, ast.Name):
            if node.id in env:                      # a parameter wins over a built-in
                return float(env[node.id])
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            avail = sorted(set(env) | set(_CONSTANTS))
            raise ExprError(
                "unknown_name",
                f"unknown name {node.id!r} (available: {avail})")
        if isinstance(node, ast.BinOp):
            op_t = type(node.op)
            if op_t not in _BIN_OPS:
                raise ExprError(
                    "illegal_node", f"unsupported operator {op_t.__name__}")
            a = ev(node.left)
            b = ev(node.right)
            return _apply_bin(_BIN_OPS[op_t], a, b, f"{op_t.__name__} operation")
        if isinstance(node, ast.UnaryOp):
            op_t = type(node.op)
            if op_t not in _UNARY_OPS:
                raise ExprError(
                    "illegal_node",
                    f"unsupported unary operator {op_t.__name__}")
            v = ev(node.operand)
            return _check_finite(_UNARY_OPS[op_t](v), "unary operation")
        if isinstance(node, ast.Call):
            name = fn_of(node)
            args = [ev(a) for a in node.args]
            try:
                if name == "abs":
                    return _check_finite(abs(args[0]), "abs")
                if name == "sqrt":
                    if args[0] < 0:
                        raise ExprError(
                            "math", f"sqrt of a negative number ({args[0]!r})")
                    return _check_finite(math.sqrt(args[0]), "sqrt")
                return _check_finite(
                    min(args) if name == "min" else max(args), name)
            except (OverflowError, ValueError) as e:
                raise ExprError("math", f"{name} numeric error: {e}") from e
        if isinstance(node, ast.Subscript):
            name = _unit_name(node.slice)
            if name is None:
                raise ExprError(
                    "illegal_node",
                    "unsupported node Subscript (only primary[unit] unit "
                    "suffixes with a registered unit identifier are "
                    "allowed)")
            if name not in _UNITS:
                raise ExprError(
                    "unknown_unit",
                    f"unknown unit {name!r} (registered: {sorted(_UNITS)})")
            v = ev(node.value)
            return _check_finite(v * _UNITS[name], f"unit {name!r}")
        raise ExprError(
            "illegal_node",
            f"unsupported node {type(node).__name__} (whitelist: "
            "Constant/Name/BinOp(+-*/%**)/UnaryOp(+-)/"
            "Call(abs,min,max,sqrt)/primary[unit] unit suffix)")

    # Deeply nested expressions blow up with RecursionError inside the ev
    # recursion — ExprError is the only error surface (callers survive via
    # `except ExprError`), so it is uniformly folded into the syntax kind.
    try:
        return ev(tree)
    except RecursionError:
        raise ExprError(
            "syntax", "expression too deeply nested") from None


def resolve_parameter_table(params: dict) -> dict[str, float]:
    """Top-level topological evaluation of the parameters section:
    {name: number | expression string} → {name: float}.

    Environment = all the other parameters (declare-before-use is not
    required — topological order resolves it automatically). Cycles →
    ExprError("cycle", "parameter cycle: a→b→a"); unknown names inside an
    expression → unknown_name listing the available names; numeric values
    are floated as-is. Upfront key-name check — a self-guard on the public
    surface of an independently distributed package.
    """
    for k in params:
        if not valid_param_name(k):
            raise ExprError(
                "syntax",
                f"parameter name {k!r} is invalid: {PARAM_NAME_RULE}")
    resolved: dict[str, float] = {}
    visiting: list[str] = []

    def get(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            chain = visiting[visiting.index(name):] + [name]
            raise ExprError(
                "cycle", "parameter cycle: " + " -> ".join(chain))
        raw = params[name]
        if isinstance(raw, str):
            visiting.append(name)
            try:
                v = eval_expr(raw, _lazy_env)
            finally:
                visiting.pop()
        elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ExprError(
                "nonatomic",
                f"parameter {name!r} value must be a number or an "
                f"expression string, got {raw!r}")
        else:
            v = float(raw)
        if not math.isfinite(v):
            raise ExprError(
                "math",
                f"parameter {name!r} resolved to a non-finite value "
                f"({v!r})")
        resolved[name] = v
        return v

    class _LazyEnv(Mapping):
        """Recursive on-demand evaluation when env is looked up (the lazy
        form of topological resolution).

        eval_expr performs only three operations — ``env[name]`` /
        ``name in env`` / ``sorted(env)`` — and the standard Mapping
        protocol routes all three through
        __getitem__/__contains__/__iter__, so they must pass through the
        on-demand resolution here (subclassing dict instead would let
        C-level slots bypass the Python overrides; do not change it back
        to a dict subclass).
        """

        def __getitem__(self, key):
            if key in params:
                return get(key)
            raise KeyError(key)

        def __contains__(self, key):
            return key in params

        def __iter__(self):
            return iter(params)

        def __len__(self):
            return len(params)

    _lazy_env = _LazyEnv()
    try:
        for name in params:
            get(name)
    except RecursionError:   # thousand-deep parameter reference chains likewise fold into the error surface
        raise ExprError(
            "cycle", "parameter reference chain too deep (possible very "
            "long reference chain)") from None
    return resolved
