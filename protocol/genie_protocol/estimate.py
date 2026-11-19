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


def _base_spacing(domain) -> tuple[float, float, float]:
    """domain 基础间距（dy/dz 缺省 = dx）——两把尺子共用的基准。"""
    return (domain.dx, domain.dy or domain.dx, domain.dz or domain.dx)


def _zone_factor(zone, axis: str, base: float) -> float:
    """mesh 对象每轴有效倍率（Q30）：绝对覆盖 → base/d（浮点倍率，
    与内核 intake 同一换算口径）；否则 factor_x/y/z（int >= 1）。
    绝对 > base 在 wire 层已被 FdtdConfigSpec 422，这里不会见到。"""
    d = getattr(zone, f"d{axis}")
    return base / d if d is not None else float(getattr(zone, f"factor_{axis}"))


def estimate_cells_domain_mesh(domain, meshes) -> int:
    """Total cell count estimation (including the refinement increment of
    the named mesh objects).

    ``domain``/``meshes`` = wire-format models (DomainSpec — carries the
    base spacing dx/dy/dz — and the list of MeshSpec refinement boxes,
    attribute access).
    """
    bx, by, bz = _base_spacing(domain)
    nx = _axis_cells(domain.x_range, bx)
    ny = _axis_cells(domain.y_range, by)
    nz = _axis_cells(domain.z_range, bz)
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
        mult = (_zone_factor(m, "x", bx)
                * _zone_factor(m, "y", by)
                * _zone_factor(m, "z", bz))
        zv = (_clip(m.x_range, domain.x_range)
              * _clip(m.y_range, domain.y_range)
              * _clip(m.z_range, domain.z_range))
        extra += base * (zv / vol) * (mult - 1)
    return int(base + extra)


def _refined_min_cell(domain, meshes) -> float:
    """min_cell = each axis's base cell size ÷ that axis's cumulative
    product of mesh factors — the single formula source for the two
    estimation rulers: n_steps_est and dt_est must share one basis and
    stay self-consistent under refinement (dt_est × n_steps_est ≥
    total_time). Does not chase graded-band inflation: an estimation for
    a guard surface that errs tight rather than loose (see the module
    header)."""
    bx, by, bz = _base_spacing(domain)
    fx = fy = fz = 1.0
    for m in meshes:
        fx *= _zone_factor(m, "x", bx)
        fy *= _zone_factor(m, "y", by)
        fz *= _zone_factor(m, "z", bz)
    return min(bx / fx, by / fy, bz / fz)


def _dt_estimate(domain, meshes) -> float:
    """Time-step upper-bound estimate: dt = cfl/√3 · min_cell (after
    sim/SI unit cancellation, steps = √3·c₀·T/(cfl·min_cell_SI))."""
    return domain.cfl * _refined_min_cell(domain, meshes) / (
        math.sqrt(3.0) * C0_PHYS)


def estimate_steps_domain_mesh(domain, meshes) -> int:
    """total_time → step-count pre-estimation."""
    return max(1, math.ceil(domain.total_time / _dt_estimate(domain, meshes)))
