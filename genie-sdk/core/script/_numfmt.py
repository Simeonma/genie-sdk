from __future__ import annotations

"""JS 数字/字符串方言 —— script 渲染的数字格式正身。

语义 = ECMA-262 最短往返（整数值浮点不带 ``.0`` 等 JS 行为）；字节由
golden 锁 test_render_golden.py 冻结——改这里等于改发布出去的脚本字节。
"""
import json
import math
import re
from typing import Any

_EXP_RE = re.compile(r"^(-?)(\d+)(?:\.(\d+))?e([+-])(\d+)$")
_DEC_RE = re.compile(r"^(-?)(\d+)(?:\.(\d+))?$")


def _js_string(v: Any) -> str:
    """JS String(v) 的镜像（本模块用到的值域内）：int→str；整数值浮点
    → 无 .0 的整数串（JS 行为，Python repr 会带 .0）；其余→repr
    （双方言最短往返小数一致）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and math.isfinite(v) and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    if v is None:
        return "null"
    return str(v)

def _exp_form(v: float) -> str:
    """JS (v).toExponential() 的最短往返形态（_exp_form 形态正身）。"""
    s = repr(float(v))
    m = _EXP_RE.match(s)
    if m:
        sign, ip, fp, esign, ep = m.groups()
        frac = (fp or "").rstrip("0")
        mant = ip + (("." + frac) if frac else "")
        exp = int(esign + ep)
    else:
        md = _DEC_RE.match(s)
        if not md:
            return s
        sign, ip, fp = md.groups()
        fp = fp or ""
        digits = ip + fp
        nz = 0
        while nz < len(digits) and digits[nz] == "0":
            nz += 1
        if nz == len(digits):
            return "0"
        sig = digits[nz:]
        if nz < len(ip):
            exp = len(ip) - 1 - nz
        else:
            exp = -(nz - len(ip) + 1)
        mant = sig[0] + (("." + sig[1:].rstrip("0")) if sig[1:].rstrip("0") else "")
        if not sign:
            sign = ""
    if exp == 0:
        return sign + mant
    return sign + mant + "e" + str(exp)

def py_num(v: Any) -> str | None:
    """数值 → 最短可读且 Python 合法的字面量（脚本数字字面量正身）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v):
        return None
    if v == 0:
        return "0"
    a = abs(v)
    plain = _js_string(v)
    sig = len(re.sub(r"^0+", "", plain.replace("-", "", 1).replace(".", "", 1)))
    if "e" not in plain and 1e-4 <= a < 1e7 and sig <= 7:
        return plain
    e = _exp_form(float(v))
    return e if len(e) <= len(plain) else plain

def py_str(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    return "'" + re.sub(r"[\r\n\t]", " ",
                        s.replace("\\", "\\\\").replace("'", "\\'")) + "'"

def _finite_number_to_string(v: float) -> str:
    """ECMA Number::toString 的有限值镜像（最短往返 + 十进制/指数切换）。"""
    neg = v < 0
    x = abs(v)
    if x == 0:
        return "0"
    if x == int(x) and x < 1e21:
        return ("-" if neg else "") + str(int(x))
    s = repr(x)
    if "e" in s or "E" in s:
        mant, exp = re.split("[eE]", s, 1)
        e = int(exp)
    else:
        mant, e = s, 0
    if "." in mant:
        ip, fp = mant.split(".", 1)
        digits = ip + fp
        e -= len(fp)
    else:
        digits = mant
    n_int = int(digits)
    while n_int % 10 == 0 and n_int != 0:
        n_int //= 10
        e += 1
    sd = str(n_int)
    k = len(sd)
    n = e + k
    if k <= n <= 21:
        out = sd + "0" * (n - k)
    elif 0 < n <= 21:
        out = sd[:n] + "." + sd[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + sd
    else:
        mant_part = sd if k == 1 else sd[0] + "." + sd[1:]
        exp_val = n - 1
        out = mant_part + "e" + ("+" if exp_val >= 0 else "-") + str(abs(exp_val))
    return ("-" if neg else "") + out

def _json_num(v: Any) -> str:
    """JS JSON.stringify(number) 镜像（原文注释用；NaN/Inf → "null"）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        fv = float(v)
        if fv != fv or fv in (float("inf"), float("-inf")):
            return "null"
        return _finite_number_to_string(fv)
    return _json_value(v)

def _json_value(v: Any) -> str:
    """JS JSON.stringify 镜像（参数值/参数名的原文注释用）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _json_num(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)

def _clean_num(v: Any) -> Any:
    """JS cleanNum 镜像：15 位有效数字去浮点噪声（50×1e-9 这类尾巴）。"""
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return v
    return float(f"{v:.15g}")
