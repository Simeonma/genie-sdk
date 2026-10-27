# [serve][参数系统批 1 2026-10] 安全线表达式求值器
# [迁移阶段 1a 2026-10] 自内核 fdtd/serve/expr.py 逐字迁入（唯一改动：
# __all__ 补 valid_param_name——SDK _script.py 与内核 schema 共用同一双
# 手，正文逐字不变）。内核 expr.py 变重导出薄壳。
"""COMSOL 式全局参数的安全求值——AST 白名单游走，零 eval/exec。

职责（调度者设计 §2）：

- ``eval_expr(expr, env) -> float``：算术表达式字符串 → float。
  白名单节点 = Constant(数值 int/float) / Name(仅 env 查表) /
  BinOp(+ - * / % **) / UnaryOp(+-) / Call(仅 abs/min/max/sqrt；
  abs/sqrt 单数值参，min/max 一或两参) / 单位后缀 ``primary[unit]``
  （单位语法批 A 2026-10：语法 ``primary '[' IDENT ']'`` 绑定最紧，
  ``2*50[nm]`` == 2*(50[nm])；语义 = 纯数值换算
  ``a[unit] = a × unit_size``，unit_size = 该单位 SI 尺度
  （nm=1e-9、THz=1e12…，注册表 ``_UNITS`` 长度/时间/频率三族 20 单位，
  含 µm/µs Unicode 微米/微秒别名；概念节点 ``_Unit(value, name)`` 供
  服务仓批 2 前端双实现对齐）。**无维度检查**——docstring 声明边界。
  **其余任何节点**（Attribute/Lambda/Compare/BoolOp/IfExp/字符串常量/
  非单位形态的 Subscript/…）→ ``ExprError`` 带节点类型名——属性/下标
  访问是注入的主通道，比较与布尔引入三值逻辑，一律拒绝。
- 数值纪律：除零/模零、sqrt 负数、溢出、结果非有限 → ``ExprError``
  ("math")；空表达式/纯空白 → "syntax"。
- 错误分级（422 策展用）：``ExprError.kind`` ∈
  syntax / unknown_name / unknown_unit / illegal_node / math / nonatomic /
  cycle / guard——前六档由 eval_expr 产生；cycle = 参数节拓扑求值遇环
  （消息带完整链 a→b→a）；guard = 求值结果被 SimConfig 值守卫拦下
  （由 schema.parse_config 产生，异常类型共用，见两段校验）。
- ``resolve_parameter_table(params) -> dict[str, float]``：参数节
  顶层拓扑求值（值=数值或引用其余参数的表达式串，环境=其余参数，
  声明序无关）。golden 语料错误例只覆盖前五档。

双实现互锁：``expr_golden.json`` 是本求值器的正典语料（内核 pytest
全量吃它）；服务仓批 2 的前端表达式预览若另立实现，同一份语料互锁。
"""
from __future__ import annotations

import ast
import math
import re
import unicodedata
from collections.abc import Mapping

__all__ = ["ExprError", "eval_expr", "resolve_parameter_table",
           "valid_param_name", "param_name_error"]

# 参数名规则文案（单一来源——G124⑦：此前 resolve.check_raw_param_keys
# 与 _wire.SimConfig._check_param_names 双手复制同段文案，2026-10-18
# 正因此类漂移收口过 validate_for_ui）。
PARAM_NAME_RULE = (
    "must be an identifier (starting with a letter or underscore, "
    "letters/digits/underscores only, <= 64 chars)")


def param_name_error(k: str) -> str:
    """非法参数名的完整指路报错（G151 收口：**整段文案**的单一来源——
    规则常量 G124⑦ 已单源，指路后半段此前仍在 resolve 与 _wire 手抄
    两份）。消费方 = resolve.check_raw_param_keys（求值前检查）与
    _wire.SimConfig._check_param_names（线格式契约守卫——sweep 展开
    路径对**未引用**坏名的唯一拦截者，不可删）。"""
    return (f"parameter name {k!r} is invalid: {PARAM_NAME_RULE} — "
            f"it is referenced by expression slots in numeric fields "
            f"and by sweep axes (parameters[name])")

# Call 白名单：名 → (最少参数个数, 最多参数个数)
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

_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 单位注册表（单位语法批 A 2026-10）：单一常量，名 → SI 尺度。
# 语义 = 纯数值换算 ``a[unit] = a × _UNITS[unit]``，无维度检查；
# µm/µs 为 Unicode 微米/微秒别名（与 um/us 同值，1e-6）。
_UNITS = {
    # 长度
    "pm": 1e-12,
    "nm": 1e-9,
    "um": 1e-6,
    "µm": 1e-6,
    "mm": 1e-3,
    "cm": 1e-2,
    "m": 1.0,
    # 时间
    "fs": 1e-15,
    "ps": 1e-12,
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    # 频率
    "Hz": 1.0,
    "kHz": 1e3,
    "MHz": 1e6,
    "GHz": 1e9,
    "THz": 1e12,
    "PHz": 1e15,
}
# Python 标识符按 NFKC 归一（µ U+00B5 → μ U+03BC）——键统一到归一形态，
# 使 `1[µm]`（源码里无论键入 MICRO SIGN 还是 GREEK MU 都归一）命中别名。
_UNITS = {unicodedata.normalize("NFKC", k): v for k, v in _UNITS.items()}


class ExprError(ValueError):
    """表达式/参数节错误（HTTP 422 策展）。kind 见模块 docstring。"""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind
        self.msg = msg


def _check_finite(v: float, ctx: str) -> float:
    if not math.isfinite(v):
        raise ExprError("math", f"{ctx} produced a non-finite result ({v!r})")
    return v


def _unit_name(slice_node: ast.AST) -> str | None:
    """单位后缀切片名识别：``primary[IDENT]``（ast.Name，语法正典形态）
    或 ``primary['str']``（字符串常量防御形态）→ 单位名；其余形态
    （数值/切片/表达式）→ None（维持既有 illegal_node 拒绝分支）。

    返回值经 NFKC 归一——与 Python 标识符归一规则一致（µ → μ），
    使字符串常量形态 ``1['µm']`` 也能命中 Unicode 别名键。"""
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
    except TypeError as e:   # G117：如 Mod/Pow 在病态输入下的实现层 TypeError
        raise ExprError("math", f"{ctx} numeric type error: {e}") from None
    if isinstance(v, complex):
        # G117：负底分数幂 Python 返回 complex（(-8)**(1/3)）——本求值器
        # 只产实数，complex 结果即 math 档（此前 math.isfinite(complex)
        # 抛裸 TypeError 逃出 ExprError 契约，422 策展变 500）。
        raise ExprError(
            "math", f"{ctx} produced a complex result ({v!r}) — negative "
            "base with fractional exponent is not supported")
    return _check_finite(v, ctx)


def eval_expr(expr: str, env: dict[str, float] | None = None) -> float:
    """白名单求值：expr → float。env = 可引用的参数名表（缺省空）。"""
    env = env or {}
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("syntax", "expression is empty or whitespace-only")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError) as e:  # ValueError: NUL 字节等
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
            except OverflowError:   # G118：千位级整数字面量超 float 域
                raise ExprError(
                    "math", f"constant {v!r} out of float range") from None
            # G118：1e400 在 AST 里已是 inf 常量——Constant 出口同样
            # 过 _check_finite（此前静默返回 inf，违反本 docstring 的
            # "结果非有限 → ExprError" 自宣契约）。
            return _check_finite(fv, "constant")
        if isinstance(node, ast.Name):
            if node.id in env:
                return float(env[node.id])
            avail = sorted(env)
            raise ExprError(
                "unknown_name",
                f"unknown name {node.id!r}"
                + (f" (available: {avail})" if avail
                   else " (environment is empty, no names available)"))
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

    # G117：深嵌套表达式在 ev 递归里炸 RecursionError——ExprError 是唯一
    # 错误面（调用方按 except ExprError 生存），统一收进 syntax 档。
    try:
        return ev(tree)
    except RecursionError:
        raise ExprError(
            "syntax", "expression too deeply nested") from None


def resolve_parameter_table(params: dict) -> dict[str, float]:
    """参数节顶层拓扑求值：{名: 数值|表达式串} → {名: float}。

    环境 = 其余全部参数（先声明后用不要求——拓扑序自动解析）。
    环 → ExprError("cycle", "参数环: a→b→a")；表达式内未知名 →
    unknown_name 列可用名；数值值原样 float 化。
    键名前置检查（G124⑩：独立分发包的公开面自守卫，此前靠
    SimConfig/check_raw_param_keys 三重把关兜着）。
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
        """env 查表时按需递归求值（拓扑解析的懒惰形态）。

        eval_expr 只做 ``env[name]`` / ``name in env`` / ``sorted(env)``
        三个操作——标准 Mapping 协议三者全部走 __getitem__/__contains__/
        __iter__，必然经过这里的按需解析（子类化 dict 则 C 层槽位会
        绕开 Python 覆写，勿改回 dict 子类）。
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
    except RecursionError:   # G117：千级参数引用链同理收进错误面
        raise ExprError(
            "cycle", "parameter reference chain too deep (possible very "
            "long reference chain)") from None
    return resolved


def valid_param_name(name: str) -> bool:
    """参数名合法性（供 schema 复用同一双手：标识符 + ≤64 字符）。"""
    return (isinstance(name, str) and len(name) <= 64
            and _PARAM_NAME_RE.fullmatch(name) is not None)
