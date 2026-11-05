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


def estimate_cells_domain_mesh(domain, mesh) -> int:
    """Total cell count estimation (including the refinement increment of
    refine regions).

    ``domain``/``mesh`` = wire-format models (DomainSpec/MeshSpec,
    attribute access).
    """
    dx = mesh.dx
    dy = mesh.dy or mesh.dx
    dz = mesh.dz or mesh.dx
    nx = _axis_cells(domain.x_range, dx)
    ny = _axis_cells(domain.y_range, dy)
    nz = _axis_cells(domain.z_range, dz)
    base = nx * ny * nz
    if not mesh.refine:
        return base
    vol = ((domain.x_range[1] - domain.x_range[0])
           * (domain.y_range[1] - domain.y_range[0])
           * (domain.z_range[1] - domain.z_range[0]))

    def _clip(rng, dom) -> float:
        lo, hi = max(rng[0], dom[0]), min(rng[1], dom[1])
        return max(0.0, hi - lo)

    extra = 0
    for r in mesh.refine:
        fs = [r.factor] * 3 if isinstance(r.factor, int) else list(r.factor)
        mult = fs[0] * fs[1] * fs[2]
        zv = (_clip(r.x_range, domain.x_range)
              * _clip(r.y_range, domain.y_range)
              * _clip(r.z_range, domain.z_range))
        extra += base * (zv / vol) * (mult - 1)
    return int(base + extra)


def _refined_min_cell(mesh) -> float:
    """min_cell = each axis's base cell size ÷ that axis's cumulative
    product of refine factors — the single formula source for the two
    estimation rulers: n_steps_est and dt_est must share one basis and
    stay self-consistent under refine (dt_est × n_steps_est ≥ total_time).
    Does not chase graded-band inflation: an estimation for a guard
    surface that errs tight rather than loose (see the module header)."""
    fx = fy = fz = 1
    for r in mesh.refine:
        fs = [r.factor] * 3 if isinstance(r.factor, int) else list(r.factor)
        fx, fy, fz = fx * fs[0], fy * fs[1], fz * fs[2]
    return min(mesh.dx / fx, (mesh.dy or mesh.dx) / fy,
               (mesh.dz or mesh.dx) / fz)


def _dt_estimate(domain, mesh) -> float:
    """Time-step upper-bound estimate: dt = cfl/√3 · min_cell (after
    sim/SI unit cancellation, steps = √3·c₀·T/(cfl·min_cell_SI))."""
    return domain.cfl * _refined_min_cell(mesh) / (math.sqrt(3.0) * C0_PHYS)


def estimate_steps_domain_mesh(domain, mesh) -> int:
    """total_time → step-count pre-estimation."""
    return max(1, math.ceil(domain.total_time / _dt_estimate(domain, mesh)))
