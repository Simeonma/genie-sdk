"""genie.core.grid — Q31 auto-grid: geometry + materials + λ → per-axis
spacing tables (pure computation, host-only).

The language contract (DECISIONS Q31): the non-uniform BASE grid lives on
domain as per-axis spacing tables; ``auto_grid`` (the worker verb) is a
one-shot generator in the fit_material family — its output is plain data
(the three tables on the solver's domain), visible, editable, deletable.

Physics ruler: per merged projection segment, d = λ₀_min / (N · n_local),
n_local = max Re(n(ω)) over the working band (evaluated from the FITTED
poles via core.material.fit.evaluate_dispersion — the surface the P0-2
cross-repo anchor locks bitwise against the kernel eps_at). Empty regions
take the background index (vacuum 1.0; background materials are blocks).
"""
from __future__ import annotations

import math

import numpy as np

from .material.fit import evaluate_dispersion, pole_from_node

__all__ = ["n_of_material", "project_axis", "suggest_table",
           "auto_grid_tables", "DEFAULT_PPW"]

#: 每波长格数缺省（Q31 开题：工程惯例 10–20，取保守中值）
DEFAULT_PPW = 14
#: 背景恒为真空（n_bg 概念已从 wire 删除，2026-11-26）——自动网格的
#: 空区折射率就是 1.0；要背景就画 block，它会作为几何进入折影。
_VACUUM_N = 1.0

_C0 = 299792458.0


def _band_freqs(f_lo: float, f_hi: float, n: int = 9) -> np.ndarray:
    """工作频段采样（log 均匀 9 点——金属段谱变化快，两端必含）。"""
    f_lo, f_hi = max(f_lo, 1.0), max(f_hi, f_lo * 1.0000001 + 1.0)
    return np.geomspace(f_lo, f_hi, n) * (2.0 * math.pi)   # rad/s


def n_of_material(mat, f_lo: float, f_hi: float) -> float:
    """wire 材料（draft/spec/dict 均可，属性访问）→ 频段内 max Re(n)。

    const → √eps（对角命名键取最大分量）；lorentz → 拟合极点求值
    （evaluate_dispersion，P0-2 锚面）。库材必须已拟合（poles 在手）——
    调用方（auto_grid 动词）先过 _fit_pending。"""
    omega = _band_freqs(f_lo, f_hi)
    kind = getattr(mat, "type", None)
    if kind == "const":
        base = getattr(mat, "eps", None)
        if base is None:
            raise ValueError(
                f"material {getattr(mat, 'name', '?')!r}: const without eps")
        comps = [base] + [getattr(mat, k) for k in ("eps_xx", "eps_yy",
                                                    "eps_zz")]
        return math.sqrt(max(float(c) for c in comps if c is not None))
    if kind == "lorentz":
        eps_inf = getattr(mat, "eps_inf", None)
        if eps_inf is None:
            raise ValueError(
                f"material {getattr(mat, 'name', '?')!r}: lorentz without "
                f"eps_inf")
        poles = [pole_from_node(p if isinstance(p, dict)
                                else {"kind": p.kind, **{
                                    k: getattr(p, k) for k in (
                                        "omega_0", "gamma", "delta_eps",
                                        "omega_p", "num", "den")}})
                 for p in (getattr(mat, "poles", None) or [])]
        res = evaluate_dispersion(eps_inf, poles, omega)
        return float(np.max(res.n))
    raise ValueError(
        f"material {getattr(mat, 'name', '?')!r} of type {kind!r} has no "
        f"n(ω) — const/lorentz only (library materials must be fitted "
        f"first: call genie.fit_material or run auto_grid after validate)")


#: 各形状的**未旋转**三轴范围（旋转 ≠ 0 时退化为包围球——保守方向，
#: 只会多加密不会漏）。形状语义 = wire Spec 字段（真源）；height 是
#: 全高（z 挤出族：cylinder/ngon_z/ellipse_z/ring_z）。
def _unrotated_extents(g):
    t = getattr(g, "type", None)
    c = list(getattr(g, "center", (0.0, 0.0, 0.0)))
    if t == "box":
        return tuple(tuple(getattr(g, f"{a}_range")) for a in "xyz")
    if t == "sphere":
        r = float(g.radius)
        return tuple((c[i] - r, c[i] + r) for i in range(3))
    if t == "cylinder":
        r, h = float(g.radius), float(g.height)
        return ((c[0] - r, c[0] + r), (c[1] - r, c[1] + r),
                (c[2] - h / 2, c[2] + h / 2))
    if t == "ngon_z":
        r, h = float(g.circumradius), float(g.height)
        return ((c[0] - r, c[0] + r), (c[1] - r, c[1] + r),
                (c[2] - h / 2, c[2] + h / 2))
    if t == "polygon_z":
        vs = [(x, y) for x, y in g.vertices
              if x is not None and y is not None
              and not (isinstance(x, float) and math.isnan(x))]
        xs = [x for x, _ in vs] or [c[0]]
        ys = [y for _, y in vs] or [c[1]]
        return ((min(xs), max(xs)), (min(ys), max(ys)),
                tuple(g.z_range))
    if t == "ellipse_z":
        rx, ry, h = float(g.rx), float(g.ry), float(g.height)
        return ((c[0] - rx, c[0] + rx), (c[1] - ry, c[1] + ry),
                (c[2] - h / 2, c[2] + h / 2))
    if t == "ring_z":
        r, h = float(g.outer_radius), float(g.height)
        return ((c[0] - r, c[0] + r), (c[1] - r, c[1] + r),
                (c[2] - h / 2, c[2] + h / 2))
    raise ValueError(
        f"geometry {getattr(g, 'name', '?')!r} of type {t!r}: no axis "
        f"extent rule — auto-grid cannot project it")


def _axis_extent(g, axis):
    """一个几何体在一根轴上的（保守）范围：未旋转 = 原生范围；带旋转
    = 3D 包围球投影（半轴向量取范数——任意旋转下的上界）。"""
    ext = _unrotated_extents(g)
    rot = getattr(g, "rotation", None) or (0.0, 0.0, 0.0)
    if any(abs(a) > 0 for a in rot):
        centers = [0.5 * (lo + hi) for lo, hi in ext]
        halves = [0.5 * (hi - lo) for lo, hi in ext]
        r = math.sqrt(sum(h * h for h in halves))
        i = "xyz".index(axis)
        return (centers[i] - r, centers[i] + r)
    return ext["xyz".index(axis)]


def project_axis(axis, geometries, materials, f_lo, f_hi):
    """一根轴的 1D 折影：几何区间 → [(lo, hi, n_local)]，重叠取 max n
    （保守不错值），空区/背景由 suggest_table 按真空补。断点扫描：k 个
    几何 O(k²)，k 是物体数（个位到几十），不值得上堆。"""
    intervals = []
    for g in geometries:
        mat_name = getattr(g, "material", None)
        if mat_name is None:
            continue
        mat = materials.get(mat_name)
        if mat is None:
            raise ValueError(
                f"geometry {getattr(g, 'name', '?')!r} references unknown "
                f"material {mat_name!r}")
        lo, hi = _axis_extent(g, axis)
        if lo < hi:
            intervals.append((float(lo), float(hi),
                              n_of_material(mat, f_lo, f_hi)))
    if not intervals:
        return []
    pts = sorted({p for lo, hi, _ in intervals for p in (lo, hi)})
    out = []
    for lo, hi in zip(pts, pts[1:]):
        mid = 0.5 * (lo + hi)
        covering = [n for l, h, n in intervals if l <= mid <= h]
        n = max(covering)
        if n > _VACUUM_N:
            if out and out[-1][2] == n and out[-1][1] == lo:
                out[-1] = (out[-1][0], hi, n)      # 相邻同 n 段合并
            else:
                out.append((lo, hi, n))
    return out


def suggest_table(axis_range, segments, lam_min, ppw=DEFAULT_PPW,
                  growth=1.2):
    """投影段表 → 无缝覆盖全域的 spacing 段表（wire 形 dict 列表）。

    d = λ₀_min / (ppw · n)；空区/背景区 n = 1.0（真空）。相邻同 d 段合并。"""
    lo0, hi0 = float(axis_range[0]), float(axis_range[1])
    spans = [(lo, hi, n) for lo, hi, n in segments if hi > lo0 and lo < hi0]
    spans.sort()
    covered = []
    cur = lo0
    for lo, hi, n in spans:
        lo = max(lo, lo0)
        hi = min(hi, hi0)
        if lo > cur:
            covered.append((cur, lo, _VACUUM_N))
            cur = lo
        covered.append((lo, hi, n))
        cur = max(cur, hi)
    if cur < hi0:
        covered.append((cur, hi0, _VACUUM_N))
    # 相邻同间距合并 → wire 段表
    table = []
    for lo, hi, n in covered:
        d = lam_min / (ppw * n)
        if table and abs(table[-1]["d"] - d) <= 1e-15 * d:
            table[-1]["hi"] = hi
        else:
            table.append({"lo": lo, "hi": hi, "d": d,
                          "growth_rate": growth})
    return table


def auto_grid_tables(ranges, geometries, materials, band,
                     ppw=DEFAULT_PPW, growth=1.2):
    """三轴一次算齐（fdtd 用法）。

    ranges    = {'x': (lo,hi), 'y': ..., 'z': ...}
    band      = (f_lo, f_hi) 工作频段 [Hz]（源频谱并集）
    返回 {'x': [seg-dict...], ...}（None 轴 = 几何无投影 → None，调用方
    决定：fdtd 全轴必填表或 d）。"""
    f_lo, f_hi = band
    lam_min = _C0 / max(f_lo, f_hi)
    out = {}
    for axis, rng in ranges.items():
        segs = project_axis(axis, geometries, materials, f_lo, f_hi)
        out[axis] = suggest_table(rng, segs, lam_min, ppw, growth)
    return out
