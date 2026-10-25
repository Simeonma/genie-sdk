from __future__ import annotations

"""槽位发射助手 —— 值 → 脚本片段。

哨兵/极化归一、链式排版、表达式 ``*因子`` 声明式发射（R2 声明式形态）。
极化归一与线格式侧的关系：渲染宽容（非法返 None）vs 线格式严格
（抛错）——有意的错误策略分治，同义性由 test_single_source_locks 锁。
"""
import math
import re
import unicodedata
from typing import Any

from ...models.expr import ExprError, _UNITS, eval_expr
from ._numfmt import _js_string, py_num, py_str

_FACTOR_LIT = {
    1e-15: "1e-15", 1e-12: "1e-12", 1e-9: "1e-9", 1e-6: "1e-6",
    1e-3: "1e-3", 1e-2: "1e-2", 1.0: "1",
    1e3: "1e3", 1e6: "1e6", 1e9: "1e9", 1e12: "1e12", 1e15: "1e15",
}
_UNIT_SUFFIX_RE = re.compile(r"\[([^\[\]]+)\]")


def _num(v: Any, env: dict, W: list, path: str) -> str | None:
    """数字槽发射（R2 声明式发射）：number → py_num；表达式串 →
    求值仅做失败检测（成功则原样发射，单位后缀走 _expr_to_py 的 `*因子`
    乘法形态）；求值失败 → "0" + W 记 warning；其余 → None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return py_num(v)
    if isinstance(v, str):
        try:
            eval_expr(v, env or {})
        except ExprError as e:
            W.append((path or "数值槽") + " 表达式求值失败（"
                     + (e.kind or "syntax") + ": " + (e.msg or str(e))
                     + "）——已用 0 占位")
            return "0"
        return _expr_to_py(v)
    return None

def _num_arr(v: Any, n: int, env: dict, W: list, path: str) -> list[str] | None:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    out = []
    for i, x in enumerate(v):
        s = _num(x, env, W, (path + f"[{i}]") if path else "")
        if s is None:
            return None
        out.append(s)
    return out

def _slot_arr(v: Any, n: int, null_lit: str, env: dict, W: list, path: str) -> list[str] | None:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    out = []
    for i, x in enumerate(v):
        if x is None:
            out.append(null_lit)
            continue
        s = _num(x, env, W, (path + f"[{i}]") if path else "")
        if s is None:
            return None
        out.append(s)
    return out

def _num_or_list(v: Any, env: dict, W: list, path: str) -> str | None:
    """标量 → _num；数值列表 → "[a, b, c]"；3×3 嵌套 → "[…],[…],[…]"
    （材料对角/完整张量/逐轴 pole，2026-09）；其余 None。
    列表/嵌套形态逐字节对齐 golden。"""
    if isinstance(v, (list, tuple)):
        parts = []
        for i, x in enumerate(v):
            if isinstance(x, (list, tuple)):
                s = _num_or_list(x, env, W, (path + f"[{i}]") if path else "")
                if s is None:
                    return None
                parts.append(s)
            else:
                s = _num(x, env, W, (path + f"[{i}]") if path else "")
                if s is None:
                    return None
                parts.append(s)
        return "[" + ", ".join(parts) + "]"
    return _num(v, env, W, path)

def _norm_pol(v: Any):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None

    def _isnum(x):
        return (isinstance(x, (int, float)) and not isinstance(x, bool)
                and math.isfinite(x))

    if all(_isnum(x) for x in v):
        return [[x, 0] for x in v]
    if all(isinstance(x, (list, tuple)) and len(x) == 2 and all(_isnum(y) for y in x)
           for x in v):
        return [[x[0], x[1]] for x in v]
    return None

def _eq3(a, b) -> bool:
    return a is not None and len(a) == 3 and a[0] == b[0] and a[1] == b[1] and a[2] == b[2]

def _vertex_list(v) -> list[str] | None:
    """多边形顶点 → 单个列表字面量元素 ["(x, y)", ...]。

    哨兵对两种入态（JSON 的 [null, null] 与 Python 的 (nan, nan)）
    统一渲染为 "(float('nan'), float('nan'))"（哨兵形态锚）。"""
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return None
    out = []
    for p in v:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return None
        if (p[0] is None and p[1] is None) or (
                isinstance(p[0], float) and isinstance(p[1], float)
                and p[0] != p[0] and p[1] != p[1]):  # 哨兵（两形态）
            out.append("(float('nan'), float('nan'))")
            continue
        x, y = py_num(p[0]), py_num(p[1])
        if x is None or y is None:
            return None
        out.append(f"({x}, {y})")
    return out

def _chain(first: str, parts: list[str]) -> list[str]:
    if not parts:
        return [first]
    lines = [first + " \\"]
    for i, p in enumerate(parts):
        lines.append("    " + p + ("" if i == len(parts) - 1 else " \\"))
    return lines

def _pole_ab(p: dict, env: dict, W: list, path: str) -> tuple[str, bool]:
    """rank-1 的 alpha/beta kwargs 段（2026-09 内核 1.4.0）。

    返回 (kwargs 段, ok)。ok=False = alpha/beta 在场但不可净渲染——
    G127（R17）：方向向量槽**不许 0 占位/静默丢失**（畸形 alpha 此前
    被零化成 [0,0]/无声消失，rank-1 各向异性静默变零向量/各向同性），
    调用方须跳过整个 pole 并记 W。beta-without-alpha 同判（与
    builder/协议口径一致）。"""
    has_a = p.get("alpha") is not None
    has_b = p.get("beta") is not None
    if not has_a and not has_b:
        return "", True
    if has_b and not has_a:
        return "", False
    w0 = len(W)
    a = _num_or_list(p["alpha"], env, W,
                     (path + ".alpha") if path else "")
    if a is None or len(W) > w0:      # None=不可渲染；W 增长=有 0 占位
        return "", False
    parts = ["alpha=" + a]
    if has_b:
        w1 = len(W)
        b = _num_or_list(p["beta"], env, W,
                         (path + ".beta") if path else "")
        if b is None or len(W) > w1:
            return "", False
        parts.append("beta=" + b)
    return (", " + ", ".join(parts)), True

def _pole_line(p: dict, side: str, env: dict, W: list, path: str) -> str | None:
    if not isinstance(p, dict):       # G126：poles 条目非 dict 不裸崩
        return None
    # G133 定案①（站长裁决 2026-10-27）：极点强度参数（gamma/omega_p/
    # omega_0/delta_eps）只收标量/表达式串——逐轴列表是内核 1.4.0 已
    # 移除的旧形态（协议拒、builder 拒，渲染器此前照渲，生成运行必炸
    # 的脚本）。三方口径统一：渲染时 W + 跳过该 pole。
    # alpha/beta 是 rank-1 方向向量，3 元列表合法，走 _num_or_list。
    if isinstance(p.get("gamma"), (list, tuple)) or \
            isinstance(p.get("omega_p"), (list, tuple)) or \
            isinstance(p.get("omega_0"), (list, tuple)) or \
            isinstance(p.get("delta_eps"), (list, tuple)):
        W.append((path or "pole") + " 极点参数含列表（逐轴极点是内核"
                 "已移除的旧形态——用标量 + alpha/beta rank-1），已跳过"
                 "该 pole")
        return None
    g = _num(p.get("gamma"), env, W, (path + ".gamma") if path else "")
    if g is None:
        return None
    ab, ok = _pole_ab(p, env, W, path)
    if not ok:
        W.append((path or "pole") + " alpha/beta 非法（方向向量槽不许"
                 " 0 占位/缺失），已跳过该 pole")
        return None
    if p.get("kind") == "drude":
        wp = _num(p.get("omega_p"), env, W, (path + ".omega_p") if path else "")
        if wp is None:
            return None
        return f"add_{side}_drude_pole({wp}, {g}{ab})"
    if p.get("kind") == "lorentz":
        w0 = _num(p.get("omega_0"), env, W, (path + ".omega_0") if path else "")
        if w0 is None:
            return None
        de_val = p.get("delta_eps")
        de_s = _num(de_val, env, W, (path + ".delta_eps") if path else "")
        de = (", " + de_s) if (de_s is not None and de_val != 1) else ""
        return f"add_{side}_lorentz_pole({w0}, {g}{de}{ab})"
    return None

def _expr_to_py(text: str) -> str:
    """表达式串 → 可执行 Python（单位后缀
    `[unit]` → `*factor` 乘法形态）。仅对已通过求值校验的表达式调用，
    故 `[...]` 必为单位后缀。"""
    if not isinstance(text, str):
        return _js_string(text)

    def repl(m: "re.Match[str]") -> str:
        name = m.group(1).strip()
        if len(name) >= 2 and name[0] in "'\"" and name[-1] == name[0]:
            name = name[1:-1]
        norm = unicodedata.normalize("NFKC", name)
        if norm in _UNITS:
            lit = _FACTOR_LIT.get(_UNITS[norm])
            if lit is not None:
                return "*" + lit
        return m.group(0)

    return _UNIT_SUFFIX_RE.sub(repl, text)
