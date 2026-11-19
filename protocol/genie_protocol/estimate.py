"""genie_protocol.estimate — the single source of truth for resource
estimation: total cell count / dt / step count.

All pure math (no simulation-engine dependencies). Estimation is a guard
surface, not a prophecy: graded transition bands can be finer than the
nominal factor; the authoritative step count is the simulation's own step
count after build.
"""
from __future__ import annotations

import math

__all__ = ["C0_PHYS", "estimate_cells_domain_mesh",
           "estimate_steps_domain_mesh"]

C0_PHYS = 299792458.0          # vacuum speed of light, SI (m/s)


def _axis_cells(rng, h: float) -> int:
    return max(1, math.ceil((rng[1] - rng[0]) / h))


def _axis_table(rng, d, segs) -> int:
    """Q31：段表轴的格数（逐段 ceil 求和；均匀轴回落 _axis_cells）。"""
    if segs is None:
        return _axis_cells(rng, d)
    return sum(max(1, math.ceil((s.hi - s.lo) / s.d)) for s in segs)


def _axis_base_d(d, segs) -> float:
    """Q31：绝对 mesh → 倍率换算的基准（段表轴取最细段——估算面保守）。"""
    if segs is not None:
        return min(s.d for s in segs)
    return d


def _base_spacing(domain) -> tuple:
    """domain 基础间距（dy/dz 缺省 = dx；段表轴返回 None——格数走
    _axis_table，换算基准走 _axis_base_d）。"""
    out = []
    for a in ("x", "y", "z"):
        if getattr(domain, f"{a}_spacing") is not None:
            out.append(None)
        else:
            out.append(getattr(domain, f"d{a}") or domain.dx)
    return tuple(out)


def _zone_factor(zone, axis: str, rng, d, segs) -> float:
    """mesh 对象每轴有效倍率（Q30/Q31）：绝对覆盖 → 基准/abs（浮点
    倍率，段表轴以最细段为基准——估算口径）；否则 factor_x/y/z。"""
    dv = getattr(zone, f"d{axis}")
    if dv is not None:
        return _axis_base_d(d, segs) / dv
    return float(getattr(zone, f"factor_{axis}"))


def estimate_cells_domain_mesh(domain, meshes) -> int:
    """Total cell count estimation (including the refinement increment of
    the named mesh objects).

    ``domain``/``meshes`` = wire-format models (DomainSpec — carries the
    base grid: per-axis uniform d or a Q31 spacing table — and the list
    of MeshSpec refinement boxes, attribute access).
    """
    segs = tuple(getattr(domain, f"{a}_spacing")
                 for a in ("x", "y", "z"))
    bx, by, bz = _base_spacing(domain)
    nx = _axis_table(domain.x_range, bx, segs[0])
    ny = _axis_table(domain.y_range, by, segs[1])
    nz = _axis_table(domain.z_range, bz, segs[2])
    base = nx * ny * nz
    if not meshes:
        return base
    vol = ((domain.x_range[1] - domain.x_range[0])
           * (domain.y_range[1] - domain.y_range[0])
           * (domain.z_range[1] - domain.z_range[0]))

    def _clip(rng, dom) -> float:
        lo, hi = max(rng[0], dom[0]), min(rng[1], dom[1])
        return max(0.0, hi - lo)

    extra = 0
    for m in meshes:
        mult = (_zone_factor(m, "x", domain.x_range, bx, segs[0])
                * _zone_factor(m, "y", domain.y_range, by, segs[1])
                * _zone_factor(m, "z", domain.z_range, bz, segs[2]))
        zv = (_clip(m.x_range, domain.x_range)
              * _clip(m.y_range, domain.y_range)
              * _clip(m.z_range, domain.z_range))
        extra += base * (zv / vol) * (mult - 1)
    return int(base + extra)


def _refined_min_cell(domain, meshes) -> float:
    """min_cell = each axis's base cell size ÷ that axis's cumulative
    product of mesh factors — the single formula source for the two
    estimation rulers (n_steps_est and dt_est must share one basis and
    stay self-consistent under refinement, dt_est × n_steps_est ≥
    total_time). Q31：段表轴的基准 = 最细段（估算口径，保守侧）；
    mesh 绝对覆盖在此换算成等效倍率。Does not chase graded-band
    inflation: an estimation for a guard surface that errs tight rather
    than loose (see the module header)."""
    segs = tuple(getattr(domain, f"{a}_spacing")
                 for a in ("x", "y", "z"))
    bx, by, bz = _base_spacing(domain)
    fx = fy = fz = 1.0
    for m in meshes:
        fx *= _zone_factor(m, "x", domain.x_range, bx, segs[0])
        fy *= _zone_factor(m, "y", domain.y_range, by, segs[1])
        fz *= _zone_factor(m, "z", domain.z_range, bz, segs[2])
    return min(_axis_base_d(bx, segs[0]) / fx,
               _axis_base_d(by, segs[1]) / fy,
               _axis_base_d(bz, segs[2]) / fz)


def _dt_estimate(domain, meshes) -> float:
    """Time-step upper-bound estimate: dt = cfl/√3 · min_cell (after
    sim/SI unit cancellation, steps = √3·c₀·T/(cfl·min_cell_SI))."""
    return domain.cfl * _refined_min_cell(domain, meshes) / (
        math.sqrt(3.0) * C0_PHYS)


def estimate_steps_domain_mesh(domain, meshes) -> int:
    """total_time → step-count pre-estimation."""
    return max(1, math.ceil(domain.total_time / _dt_estimate(domain, meshes)))
