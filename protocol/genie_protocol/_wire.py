"""genie_protocol._wire — the single source of truth for the SimConfig
wire format (all pydantic models).

> The kernel side is a thin shell (re-exports + resource guards +
> translate); the SDK and the kernel import the **same class objects** —
> no synchronization problem exists.
>
> **Wire-format constants** (FACES / MAX_GPU_PER_RUN / field-name sets)
> live here with the models; **deployment policy thresholds** (MAX_CELLS
> / MAX_STEPS / MAX_MONITORS / MAX_MONITOR_SAMPLES) are configuration of
> the server-side resource guards and stay in the kernel serve layer, out
> of the SDK.
>
> Lazy-loading discipline: this module is loaded on demand by this
> package's ``__init__.py`` ``__getattr__`` — ``import
> genie_protocol.expr`` must not pull in pydantic.

Example wire-format JSON shape::

    {
      "domain":   {"x_range": [-1e-6, 1e-6], "y_range": [-1e-6, 1e-6],
                    "z_range": [-1e-6, 1e-6], "total_time": 60e-15, "cfl": 0.9,
                    "boundary": {"z_min": {"kind": "pml", "layers": 12},
                                  "z_max": {"kind": "pml", "layers": 12}}},
      "mesh":     {"dx": 20e-9, "dy": 20e-9, "dz": 20e-9},
      "materials":[{"name": "sio2", "type": "const", "eps": 2.10}],
      "geometry": [{"type": "box", "material": "sio2",
                     "x_range": [-0.5e-6, 0.5e-6], "y_range": [-0.5e-6, 0.5e-6],
                     "z_range": [-0.1e-6, 0.1e-6], "priority": 1}],
      "source":   [{"type": "soft_source", "freq": 300e12, "bandwidth": 60e12,
                     "name": "s0", "center": [0, 0, -0.3e-6],
                     "polarization": [1, 0, 0]}],
      "monitor":  [{"type": "time", "name": "pt", "center": [0, 0, 0.2e-6],
                     "size": [0, 0, 0], "field": "Ex"},
                    {"type": "freq", "name": "sp", "center": [0, 0, 0.2e-6],
                     "size": [0, 0, 0], "field": "Ey",
                     "freq": {"center": 300e12, "bw": 120e12, "n": 200}}],
      "run":      {"n_gpu": 1}
    }

**Executable content of any kind is forbidden**: models accept only
numbers/strings/lists/dicts, there is no eval/exec/pickle, and complex
polarization values are expressed as [re, im] pairs. All values are SI.
"""
from __future__ import annotations

import math
import os
import re

# Monitor name whitelist: ASCII [\w\-]{1,64} — the name is an h5
# group name and the address segment of the result key t@<name>.<field>;
# dots/spaces would create parsing ambiguity.
_MONITOR_NAME_RE = re.compile(r"[\w\-]{1,64}", re.ASCII)
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .expr import param_name_error, valid_param_name


def _walk_finite(v, path: str) -> None:
    """Full isfinite walk over numeric values: floats are checked one
    by one; containers recurse;
    None/str/bool/nested models are skipped (models carry this guard
    themselves)."""
    if v is None or isinstance(v, (bool, str)):
        return
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"{path} must be finite (got {v!r})")
    elif isinstance(v, (tuple, list)):
        for x in v:
            _walk_finite(x, path)
    elif isinstance(v, dict):
        for x in v.values():
            _walk_finite(x, path)


# The only exemption to the full isfinite walk: the (nan, nan) sentinel
# pair of vertices (_ser_vertices_json serializes it as [null,null]).
_NAN_OK_FIELDS = frozenset({"vertices"})


class _StrictModel(BaseModel):
    """Wire-format discipline: unknown fields are always rejected with
    422.

    The pydantic default ``extra="ignore"`` would let a misspelled
    optional field (e.g. ``prioraty``) be **silently dropped** and the
    simulation run to the end on defaults — silently wrong physics.
    Strict mode makes all clients (curl/UI/SDK/agent) fail loudly and
    uniformly.

    Full isfinite over numeric fields: NaN/Inf are always rejected —
    ``gt/ge`` constraints cannot stop inf (``inf > 0`` holds), and NaN
    comparisons are all False, caught only incidentally by fields that
    "happen to have a numeric constraint". The only exemption = the
    (nan, nan) sentinel pair of ``vertices`` (serialized as
    [null, null])."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_nonfinite(self):
        for name in type(self).model_fields:
            if name in _NAN_OK_FIELDS:
                continue
            _walk_finite(getattr(self, name), name)
        return self


# ---------------------------------------------------------------------------
# Wire-format constants (resource guard thresholds stay in the kernel
# serve layer)
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from an environment variable (the
    canonical source of kernel serve's MAX_CELLS / MAX_STEPS).

    Lower-bound foolproofing: a misconfiguration such as
    FDTD_MAX_SWEEP_POINTS=-1 would pass through and 422 every request —
    non-positive values fall back to the default. (The kernel still
    consumes this helper.)"""
    try:
        v = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return v if v >= 1 else default


# Cap on GPU cards per job. fdtd serve demand.MAX_RANKS imports the same
# value — the cap has a single source, and one change takes effect on
# both sides. The default compute is a single node with
# 8×V100-SXM2-32GB and a job may be scheduled a whole node; cross-node
# is beyond this constant's scope.
MAX_GPU_PER_RUN = 8

FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
BC_KINDS = ("pec", "pmc", "periodic", "pml", "bloch")
# from_library material fitting defaults: the single source for the
# wire-format defaults (LibraryMatSpec), the SDK renderer's "omit when
# equal to default" criterion, the builder.add_from_library signature,
# and the material.fit_nk signature — hand-copying the literals is
# forbidden.
LIBRARY_FIT_DEFAULTS = {
    "fit_range": (400e-9, 800e-9),
    "n_drude": 0,
    "n_lorentz": 2,
    "maxfev": 5000,
}
_FIELD_SET = ("Ex", "Ey", "Ez", "Hx", "Hy", "Hz", "all")
_INDEX_FIELD_SET = ("eps", "mu", "both")


# ---------------------------------------------------------------------------
# Basic types
# ---------------------------------------------------------------------------

Range2 = tuple[float, float]
Vec3 = tuple[float, float, float]
# Value shapes of the material eps family: scalar = iso (same value on
# all three axes) | 3-element = diagonal with independent axes | 3×3 =
# full tensor (row-major nested list[list[float]]; symmetric
# off-diagonal = reciprocal medium). Pole parameters (PoleSpec) are pure
# scalars: iso (no α/β) | rank-1 (+α/β direction).
NumOrTensor = Union[float, tuple[float, float, float], list[list[float]]]


def _check_range(v: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(v[0]), float(v[1])
    if not (hi > lo) or not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(
            f"range [{lo}, {hi}] is invalid (expected: lo < hi and both "
            f"finite)")
    return (lo, hi)


def _check_tensor_sym(v):
    """3×3 tensor guard: shape 3×3, every element finite, main diagonal
    > 0, symmetric (relative tolerance 1e-6). Mirrors the kernel's
    symmetric write in _inf_to_tensor and the main-diagonal>0 criterion
    of _require_positive_diagonal; off-diagonal entries may be negative —
    negative anisotropic coupling is legal, and the
    schema-layer defense for physical stability only checks main
    diagonal > 0 (positive-definiteness is the engine's responsibility,
    same as the kernel)."""
    if len(v) != 3 or any(len(row) != 3 for row in v):
        raise ValueError(
            "tensor must be 3x3 (row-major nested list[list[float]])")
    for row in v:
        for x in row:
            if not math.isfinite(x):
                raise ValueError(f"tensor elements must be finite, got {x!r}")
    for i in range(3):
        if not v[i][i] > 0:
            raise ValueError(
                f"tensor main diagonal must be > 0, got v[{i}][{i}]="
                f"{v[i][i]!r}")
    for i in range(3):
        for j in range(i + 1, 3):
            a, b = v[i][j], v[j][i]
            if abs(a - b) > 1e-6 * max(1.0, abs(a), abs(b)):
                raise ValueError(
                    "tensor must be symmetric (reciprocal medium: "
                    "e[i][j] == e[j][i], relative tolerance 1e-6)")
    return v


def _check_finite_pos(v):
    """Per-element guard for the eps family (eps/mu/eps_inf/mu_inf):
    scalar / 3-element diagonal = **finite and > 0** (mirrors the local
    _require_positive_diagonal, isfinite included: eps=inf passes
    ">0", would silently build, and then NaN-pollute the whole
    simulation); 3×3 tensor = every element finite + main diagonal > 0
    + symmetric (_check_tensor_sym; off-diagonal may be negative).
    Invalid values fail with an early 422 here instead of at driver
    time."""
    if isinstance(v, list):          # 3×3 tensor
        return _check_tensor_sym(v)
    for x in (v if isinstance(v, tuple) else (v,)):
        if not (math.isfinite(x) and x > 0):
            raise ValueError(
                f"must be finite and positive (scalar iso or 3-element "
                f"diagonal, per element), got {x!r}")
    return v


def _check_hex_color(v):
    """Material display color (optional): ``#RRGGBB`` / ``RRGGBB``,
    normalized to the uppercase form prefixed with #.

    A purely presentational field (the 3D view colors by material), not
    consumed by the solver; invalid values fail early, avoiding the
    silent drift of "color typo → silent fallback to palette" (same
    spirit as _StrictModel)."""
    if v is None:
        return v
    s = str(v).strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"color must be '#RRGGBB' or 'RRGGBB', got {v!r}")
    return "#" + s.upper()


def _check_pos(v):
    """omega_0/omega_p > 0 (pole parameters are pure scalars).
    Finiteness is not checked here: the local pole path already fails
    early on isfinite at VectorPole construction. gain = omega_p_sq < 0
    (negative oscillator strength) is accepted semantics, while
    gamma < 0 (negative damping) is not a gain channel and is
    hard-rejected by the kernel's ade_coeffs — the schema mirrors
    current values and invents no guard."""
    if v is None:
        return v
    if not v > 0:
        raise ValueError(f"must be > 0, got {v!r}")
    return v


def _check_dir(v):
    """alpha/beta direction vector guard (mirrors kernel `_pole._unit`):
    every element finite, norm > 0 (≈0 → 422 "zero vector"), automatic
    normalization (normalized when |norm−1| > 1e-12; once normalized the
    kernel no longer warns about the unit vector)."""
    if v is None:
        return v
    for x in v:
        if not math.isfinite(x):
            raise ValueError(
                f"direction vector elements must be finite, got {x!r}")
    norm = math.hypot(v[0], v[1], v[2])   # hypot prevents overflow —
    # a hand-written sum of squares overflows to inf at 1e200 scale, and
    # normalization then yields a silent (0,0,0) zero vector
    if norm < 1e-15:
        raise ValueError(
            f"direction vector must be non-zero (norm≈0 is a zero vector), "
            f"got {v!r}")
    if abs(norm - 1.0) > 1e-12:
        return (v[0] / norm, v[1] / norm, v[2] / norm)
    return (float(v[0]), float(v[1]), float(v[2]))


def _check_num(v):
    """Rational-pole numerator (a0, a1, a2) — coefficients of 1, s, s².

    Finiteness is covered by _StrictModel's isfinite walk. An identically
    zero numerator is a pole with no dispersion at all (it would ship a
    silent no-op), so it fails early and points at the forms: a0 alone =
    the lorentz strength Δε·ω₀², a0+a1s = a pole with one zero."""
    if v is None:
        return v
    if len(v) != 3:
        raise ValueError(
            f"num must be (a0, a1, a2) — the coefficients of 1, s, s²; "
            f"got {v!r}")
    if v[0] == 0.0 and v[1] == 0.0 and v[2] == 0.0:
        raise ValueError(
            "num is identically zero — a pole with no numerator has no "
            "dispersion at all (a0 alone is the lorentz strength, a1 adds "
            "one zero, a2 is the instantaneous term)")
    return (float(v[0]), float(v[1]), float(v[2]))


def _check_den(v):
    """Rational-pole denominator (b0, b1) of s² + b1·s + b0 — b0 = ω₀²,
    b1 = γ. Mirrors the kernel's hard rejects exactly: γ < 0 is not a
    gain channel (ade_coeffs raises), and b0 = 0 is the legal Drude
    limit. (0, 0) is a bare double integrator — no restoring force, no
    damping, infinite DC gain."""
    if v is None:
        return v
    if len(v) != 2:
        raise ValueError(
            f"den must be (b0, b1) — the denominator s² + b1·s + b0; "
            f"got {v!r}")
    b0, b1 = float(v[0]), float(v[1])
    if b0 < 0.0:
        raise ValueError(
            f"den b0 = ω₀² must be >= 0 (b0 = 0 is the Drude limit), "
            f"got {b0!r}")
    if b1 < 0.0:
        raise ValueError(
            f"den b1 = γ must be >= 0, got {b1!r} — negative damping is "
            f"not a gain channel and is hard-rejected by the kernel's "
            f"ade_coeffs; gain is a negative numerator strength (a0 < 0)")
    if b0 == 0.0 and b1 == 0.0:
        raise ValueError(
            "den (0, 0) is a bare s² — no restoring force and no damping "
            "(infinite DC gain, unstable)")
    return (b0, b1)


class _RangeModel(_StrictModel):
    x_range: Range2
    y_range: Range2
    z_range: Range2

    _v = field_validator("x_range", "y_range", "z_range", mode="after")(_check_range)


def _norm_polarization(v):
    """[1,0,0] or [[1,0],[0,0],[0,0]] → stored uniformly as [[re,im]×3].

    Zero vectors are rejected (an all-zero polarization carries no
    field and is a breeding ground for typos); non-finite components
    are caught by _StrictModel's full isfinite walk."""
    if v is None:
        v = [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
    elif len(v) != 3:
        raise ValueError("polarization must have exactly 3 components")
    elif all(isinstance(x, (int, float)) for x in v):
        v = [[float(x), 0.0] for x in v]
    else:
        out = []
        for pair in v:
            if len(pair) != 2:
                raise ValueError(
                    "polarization complex form must be [re, im] pairs")
            out.append([float(pair[0]), float(pair[1])])
        v = out
    if all(re == 0.0 and im == 0.0 for re, im in v):
        raise ValueError(
            "polarization must be non-zero (an all-zero vector carries "
            "no field)")
    return v


# ---------------------------------------------------------------------------
# Domain / Mesh
# ---------------------------------------------------------------------------


class BoundarySpec(_StrictModel):
    kind: Literal["pec", "pmc", "periodic", "pml", "bloch"]
    # pml
    layers: int = Field(default=12, ge=2)
    kappa_max: float = Field(default=1.0, ge=1.0)
    order: int = Field(default=3, ge=1)
    # bloch
    k: Optional[Vec3] = None
    from_source: bool = False
    n_background: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "bloch" and self.k is None and not self.from_source:
            raise ValueError(
                "bloch boundary requires k (3-tuple, SI) or "
                "from_source=true")
        return self


class DomainSpec(_RangeModel):
    total_time: float = Field(gt=0)
    cfl: float = Field(default=0.9, gt=0, le=1)
    boundary: dict[str, BoundarySpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_faces(self):
        for face in self.boundary:
            if face not in FACES:
                raise ValueError(
                    f"unknown face {face!r} (expected one of: {FACES})")
        return self


class RefineSpec(_RangeModel):
    factor: Optional[Union[int, list[int]]] = None
    growth_rate: float = Field(default=1.2, ge=1.0)

    @model_validator(mode="after")
    def _check(self):
        if self.factor is None:
            raise ValueError(
                "refine requires factor (an int or [fx, fy, fz], each >= 2)")
        fs = [self.factor] * 3 if isinstance(self.factor, int) else list(self.factor)
        if len(fs) != 3 or any(not isinstance(f, int) or f < 2 for f in fs):
            raise ValueError(
                "factor must be an int >= 2 or a list of 3 ints each >= 2")
        return self


class MeshSpec(_StrictModel):
    dx: float = Field(gt=0)
    dy: Optional[float] = Field(default=None, gt=0)
    dz: Optional[float] = Field(default=None, gt=0)
    refine: list[RefineSpec] = Field(default_factory=list)
    # Refinement cap / minimum cells per axis: the guard mirrors
    # the local Mesh setter (isinstance(int) and >=1), with the same
    # defaults as local, 128/10. Dimensionless, not part of resource
    # estimation (estimate_cells consumes only dx/dy/dz/refine). Note
    # that max_refinement checks actual refinement (the graded transition
    # band can be finer than the nominal factor, see the local
    # Mesh.refine docstring); the guard fires at kernel build time.
    max_refinement: int = Field(default=128, ge=1)
    min_cells: int = Field(default=10, ge=1)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


class _Identified:
    """v2 item identity: a globally unique number issued by the system —
    immutable, never reused after deletion; the four item collections
    (geometry / materials / source / monitor) share one id allocator.
    **Not consumed by the kernel** (not in translate); SDK-only
    bookkeeping; save / open / submit all carry it."""

    id: Optional[int] = None


class PoleSpec(_StrictModel):
    """Dispersion pole = one rational scalar function times a rank-1
    tensor weight::

        χ(s) = num(s) / (s² + b1·s + b0)     D += χ(s)·β⊗α·F

    Three forms, **one physics** — ``lorentz`` / ``drude`` are special
    cases of ``rational``, and the kernel realises all three through the
    same state space (Q14: no second recurrence):

    - **iso**: all parameters scalar, no alpha/beta → expanded to all
      three axes by local `_iso_expand` (existing semantics unchanged);
    - **rank-1**: all parameters scalar + `alpha` (required) ± `beta`
      (default = alpha) → one pole mounted on α⊗β (lorentz
      ωp²=Δε·ω₀²; drude ωp²=ω_p²);
    - **rational**: `num=(a0,a1,a2)` (coefficients of 1, s, s²) +
      `den=(b0,b1)` (denominator s²+b1·s+b0, i.e. b0=ω₀², b1=γ) — and
      nothing else; `omega_0`/`omega_p`/`gamma`/`delta_eps` belong to the
      shorthand forms and are rejected here rather than silently ignored.

    **Completeness** (why this is the general form and not a feature
    list): any tensor-valued rational dispersion is a sum of rank-1
    weights (SVD of each residue matrix), and any higher-order
    denominator factorises into real second-order factors — so
    "scalar rational × rank-1 weight, second-order denominator" is
    closed. `a2` is the **feedthrough** (instantaneous) term: it needs no
    state, and it is applied through the raster-time effective
    permittivity (Q14 ③), *not* folded into the wire `eps_inf` — so it
    neither requires α=β nor breaks reciprocity rules of `eps_inf`, and
    instantaneous non-reciprocal media become expressible.

    Gains / guards mirror the kernel exactly: gain = negative oscillator
    strength (a0 < 0 / ωp² < 0, bounded); b1 < 0 (= γ < 0) is not a gain
    channel and is hard-rejected by the kernel's ade_coeffs. The schema
    mirrors current values and invents no guard.
    """

    kind: Literal["lorentz", "drude", "rational"]
    omega_0: Optional[float] = None   # rad/s (lorentz; >0)
    omega_p: Optional[float] = None   # rad/s (drude; >0)
    gamma: Optional[float] = None     # rad/s (lorentz/drude; negative damping is
                                      #   not a gain channel — hard-rejected by
                                      #   ade_coeffs; gain goes via a0/ωp² < 0 =
                                      #   negative strength)
    delta_eps: float = 1.0
    num: Optional[Vec3] = None        # rational only: (a0, a1, a2) — 1, s, s²
    den: Optional[Range2] = None      # rational only: (b0, b1) — s² + b1·s + b0
    alpha: Optional[Vec3] = None      # rank-1 drive direction (unit vector, auto-normalized)
    beta: Optional[Vec3] = None       # rank-1 coupling direction (default = alpha)

    _v = field_validator("omega_0", "omega_p", mode="after")(_check_pos)
    _d = field_validator("alpha", "beta", mode="after")(_check_dir)
    _n = field_validator("num", mode="after")(_check_num)
    _q = field_validator("den", mode="after")(_check_den)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "rational":
            if self.num is None or self.den is None:
                raise ValueError(
                    "rational pole requires num=(a0, a1, a2) and "
                    "den=(b0, b1)")
            for name in ("omega_0", "omega_p", "gamma"):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"rational pole takes num/den only — {name} belongs "
                        f"to the lorentz/drude forms (b0=ω₀², b1=γ are the "
                        f"denominator coefficients)")
            if self.delta_eps != 1.0:
                raise ValueError(
                    "rational pole takes num/den only — delta_eps belongs "
                    "to the lorentz form (write the numerator num instead)")
        else:
            if self.num is not None or self.den is not None:
                raise ValueError(
                    f"{self.kind} pole is the rational special case — use "
                    f"kind='rational' with num/den instead of mixing the "
                    f"two forms")
            if self.gamma is None:
                raise ValueError("pole requires gamma")
            if self.kind == "lorentz":
                if self.omega_0 is None:
                    raise ValueError(
                        "lorentz pole requires omega_0 > 0 (use drude for "
                        "omega_0=0)")
                if self.omega_p is not None:
                    raise ValueError(
                        "lorentz pole takes omega_0 (+delta_eps), not "
                        "omega_p — the strength is ωp²=Δε·ω₀²")
            else:  # drude
                if self.omega_p is None:
                    raise ValueError("drude pole requires omega_p > 0")
                if self.omega_0 is not None:
                    raise ValueError(
                        "drude pole takes omega_p, not omega_0 (kind="
                        "'lorentz' is the ω₀>0 form)")
        # rank-1 guard: beta without alpha → 422 (alpha is required)
        if self.beta is not None and self.alpha is None:
            raise ValueError(
                "rank-1 pole requires alpha (beta without alpha is invalid)")
        return self


class ConstMatSpec(_Identified, _StrictModel):
    """Constant material. eps/mu: scalar (iso, same value on all three
    axes) |
    3-element ``[xx, yy, zz]`` (diagonal, independent per axis) | 3×3
    nested array (full tensor; symmetric off-diagonal = reciprocal
    medium). The guard mirrors the local _require_positive_diagonal:
    scalar/diagonal entries finite and > 0 element-wise (dispersionless
    negative ε/μ is a source of numerical divergence; metals go through
    pole/nk fitting); tensor entries finite + main diagonal > 0 +
    symmetric (off-diagonal may be negative).

    color: an optional color value **for display only** ("#RRGGBB").
    The GUI's 3D view colors by material — solids of the same material
    share one color; by default the frontend picks from a palette via a
    stable hash of the material name. The solver ignores this field (not
    in translate), so it is a purely presentational, optional,
    backward-compatible extension. The material name is the **foreign
    key** referenced by geometry.material (renames do not cascade)."""

    type: Literal["const"] = "const"
    name: str = Field(min_length=1)
    eps: NumOrTensor = 1.0
    mu: NumOrTensor = 1.0
    color: Optional[str] = None

    _v = field_validator("eps", "mu", mode="after")(_check_finite_pos)
    _vc = field_validator("color", mode="after")(_check_hex_color)


class LorentzMatSpec(_Identified, _StrictModel):
    """Lorentz/Drude dispersive material. eps_inf/mu_inf follow the same
    shape semantics as ConstMatSpec's eps/mu (scalar iso | 3-element
    diagonal | 3×3 tensor); per-pole forms of poles/mu_poles are
    described in PoleSpec (iso | rank-1, purely scalar parameters ± α/β
    direction). color: same as ConstMatSpec.color (display only)."""

    type: Literal["lorentz"] = "lorentz"
    name: str = Field(min_length=1)
    eps_inf: NumOrTensor = 1.0
    mu_inf: NumOrTensor = 1.0
    poles: list[PoleSpec] = Field(default_factory=list)
    mu_poles: list[PoleSpec] = Field(default_factory=list)
    color: Optional[str] = None

    _v = field_validator("eps_inf", "mu_inf", mode="after")(_check_finite_pos)
    _vc = field_validator("color", mode="after")(_check_hex_color)


class LibraryMatSpec(_StrictModel):
    """Bare from_library wire-format node — **explicitly rejected**
    (the kernel accepts pole input only for dispersive materials; nk
    fitting belongs to the service repo).

    The class stays in the union (rather than being deleted) so that a
    type="from_library" node is still matched by the discriminator and
    gets this class's explicit, pointing-the-way error instead of
    pydantic's generic "no match for discriminator". Validation time
    (model_validate / the validate_config tool) and job time
    (translate) reject via the same single implementation.
    """

    _REJECT_MSG = (
        "from_library wire-format materials are not accepted by the "
        "kernel: it takes pole input only for dispersive materials. "
        "Use one of: "
        "(1) local fitting via genie.material.fit_nk (the SDK ships the "
        "same n-k library) and submit the result as a type='lorentz' "
        "pole payload; "
        "(2) the SDK add_from_library sugar (same flow, automated); "
        "(3) submit type='lorentz' pole parameters directly."
    )

    type: Literal["from_library"] = "from_library"
    name: str = Field(min_length=1)                 # model registry name
    library: str = Field(min_length=1)              # nk library file name, e.g. "Au"
    # This class rejects everything and consumes no fields, but its
    # defaults still reference the canon (the same LIBRARY_FIT_DEFAULTS
    # as the renderer's omit criterion).
    fit_range: Range2 = LIBRARY_FIT_DEFAULTS["fit_range"]
    n_drude: int = Field(default=LIBRARY_FIT_DEFAULTS["n_drude"], ge=0)
    n_lorentz: int = Field(default=LIBRARY_FIT_DEFAULTS["n_lorentz"], ge=0)
    maxfev: int = Field(default=LIBRARY_FIT_DEFAULTS["maxfev"], ge=1)
    quiet: bool = True

    _v = field_validator("fit_range", mode="after")(_check_range)

    @model_validator(mode="after")
    def _reject_from_library(self):
        raise ValueError(LibraryMatSpec._REJECT_MSG)


MaterialSpec = Annotated[
    Union[ConstMatSpec, LorentzMatSpec, LibraryMatSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Geometry (box/sphere/cylinder as the workhorses; +ngon_z/polygon_z
# (regular n-gon hole arrays and arbitrary-polygon metasurface cells),
# +ellipse_z/ring_z/polygon_swept (elliptical holes / hole rings /
# sheared sweeps); the dialect aligns with local fdtd/api/geometry.py)
# ---------------------------------------------------------------------------


class _NamedGeo(_Identified):
    """The **required name** of geometry objects.

    The name is a human-readable address (forms locate by id, and the id
    does not drift on rename); the solver does not consume it (not in
    translate). Unique within a collection (SimConfig._check checks
    duplicates)."""

    name: str = Field(min_length=1)


class BoxSpec(_RangeModel, _NamedGeo):
    type: Literal["box"] = "box"
    material: str = Field(min_length=1)
    priority: int = 0


class SphereSpec(_StrictModel, _NamedGeo):
    type: Literal["sphere"] = "sphere"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    priority: int = 0


class CylinderSpec(_StrictModel, _NamedGeo):
    type: Literal["cylinder"] = "cylinder"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


class NgonZSpec(_StrictModel, _NamedGeo):
    """Regular n-gon (extruded along z). Mirrors the local ``add_ngon_z``
    guard: n_sides >= 3 (with n=2, cos(π/2)=0 makes the structure
    vanish), circumradius/height > 0."""

    type: Literal["ngon_z"] = "ngon_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    n_sides: int = Field(ge=3)
    circumradius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


def norm_vertices_pairs(vertices):
    """vertices sentinel normalization (shared by polygon_z /
    polygon_swept): a JSON [null, null] pair → (nan, nan); coordinates
    must be finite. The two shapes share one sentinel semantics and one
    implementation, forbidding drift. Deeper checks
    such as sub-polygon >= 3 vertices stay at kernel build time (the
    same single implementation)."""
    out = []
    for x, y in vertices:
        if x is None and y is None:          # JSON sentinel → kernel sentinel
            out.append((float("nan"), float("nan")))
        elif x is None or y is None:
            raise ValueError(
                "vertices sentinel must be a complete [null, null] pair "
                "(a single-sided null is invalid)")
        elif not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(
                "vertices coordinates must be finite (use [null, null] "
                "pairs as sentinels)")
        else:
            out.append((float(x), float(y)))
    return out


def _ser_vertices_json(verts):
    """(nan, nan) kernel sentinel → JSON [null, null] (closing the
    sentinel's JSON round trip). If model_dump(json) emitted NaN
    literals, the runner's second SimConfig validation would reject
    them. Non-sentinel coordinates pass through unchanged."""
    return [
        [None, None]
        if isinstance(x, float) and math.isnan(x)
        and isinstance(y, float) and math.isnan(y)
        else [x, y]
        for (x, y) in verts
    ]


class PolygonZSpec(_StrictModel, _NamedGeo):
    """Arbitrary polygon (extruded along z). ``vertices`` is a list of
    (x, y).

    Sub-polygon separator sentinel: **JSON submissions use ``[null,
    null]`` pairs** (strict JSON has no NaN literal); the validator
    normalizes to ``(nan, nan)`` — the same semantics as the local API.
    The Python side (SDK/kernel) accepts both spellings. The schema
    layer locks the shape (paired, at least 3 points); semantic
    validation of sub-polygon >= 3 vertices happens at kernel build time
    (the same single implementation as the local API)."""

    type: Literal["polygon_z"] = "polygon_z"
    material: str = Field(min_length=1)
    vertices: list[tuple[Optional[float], Optional[float]]] = Field(min_length=3)
    z_range: Range2
    priority: int = 0

    @field_serializer("vertices")
    def _ser_vertices_json_(self, verts):
        # (nan, nan) kernel sentinel → JSON [null, null] (closing the
        # sentinel's JSON round trip; NaN literals would be rejected by
        # the runner's second validation)
        return _ser_vertices_json(verts)

    _v = field_validator("z_range", mode="after")(_check_range)

    @model_validator(mode="after")
    def _norm_vertices(self):
        object.__setattr__(
            self, "vertices", norm_vertices_pairs(self.vertices))
        return self


class EllipseZSpec(_StrictModel, _NamedGeo):
    """Ellipse (extruded along z). Mirrors the local ``add_ellipse_z``
    guard (the _pos triple in _validate_structure): rx/ry/height > 0."""

    type: Literal["ellipse_z"] = "ellipse_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    rx: float = Field(gt=0)
    ry: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


class RingZSpec(_StrictModel, _NamedGeo):
    """Ring (extruded along z). inner_radius=0 is legal (a solid disk);
    inner < outer (same semantics as local _build_structures — an
    inverted ring silently rasterizes into an empty mask and the
    structure vanishes without a warning; the schema blocks the shape
    error first)."""

    type: Literal["ring_z"] = "ring_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    inner_radius: float = Field(ge=0)
    outer_radius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0

    @model_validator(mode="after")
    def _check_radii(self):
        if self.inner_radius >= self.outer_radius:
            raise ValueError(
                "inner_radius must be < outer_radius (0 = solid disk)")
        return self


class PolygonSweptSpec(_StrictModel, _NamedGeo):
    """Sheared z-extruded polygon. vertices sentinel semantics are the
    same as polygon_z (shared norm_vertices_pairs). tilt=(dx_dz, dy_dz):
    the slope at which the cross-section translates with (z - z_center);
    each component |v| <= 1000 (same as the local _TILT_SLOPE_MAX — the
    floating-point tan(90°)=1.6e16 is finite, so the finiteness defense
    cannot stop it, and a huge tilt makes the structure silently vanish
    outside the domain). tilt_deg is a local convenience conversion
    (with the 89.9° guard) and is not part of the wire format."""

    type: Literal["polygon_swept"] = "polygon_swept"
    material: str = Field(min_length=1)
    vertices: list[tuple[Optional[float], Optional[float]]] = Field(min_length=3)
    z_range: Range2
    tilt: Optional[tuple[float, float]] = None        # None → no shear
    priority: int = 0

    @field_serializer("vertices")
    def _ser_vertices_json_swept(self, verts):
        return _ser_vertices_json(verts)

    _v = field_validator("z_range", mode="after")(_check_range)

    @field_validator("tilt", mode="after")
    @classmethod
    def _check_tilt(cls, v):
        if v is None:
            return v
        if not (math.isfinite(v[0]) and math.isfinite(v[1])):
            raise ValueError("tilt components must be finite")
        if abs(v[0]) > 1000.0 or abs(v[1]) > 1000.0:
            raise ValueError(
                "tilt slope components must satisfy |v| <= 1000 "
                "(large tilts push the cross-section out of the domain)")
        return (float(v[0]), float(v[1]))

    @model_validator(mode="after")
    def _norm_vertices(self):
        object.__setattr__(
            self, "vertices", norm_vertices_pairs(self.vertices))
        return self


GeometrySpec = Annotated[
    Union[BoxSpec, SphereSpec, CylinderSpec, NgonZSpec, PolygonZSpec,
          EllipseZSpec, RingZSpec, PolygonSweptSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class _TemporalMixin(_Identified, _StrictModel):
    freq: float = Field(gt=0)
    bandwidth: float = Field(gt=0)
    delay: float = 0.0
    phase: float = 0.0
    # The four item classes require names, unique within their class;
    # this is a generational break: configs without a name are rejected
    # across the board.
    name: str = Field(min_length=1)


class SoftSourceSpec(_TemporalMixin):
    type: Literal["soft_source"] = "soft_source"
    center: Vec3 = (0.0, 0.0, 0.0)
    size: Vec3 = (0.0, 0.0, 0.0)          # per-slot <= 0 = single cell
    # Default [[1,0],[0,0],[0,0]] (x̂ linear polarization); an explicit
    # JSON null is also normalized to this
    polarization: Optional[Union[list[float], list[list[float]]]] = [
        [1.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
    amplitude: float = 1.0
    kind: Literal["electric", "magnetic"] = "electric"

    _p = field_validator("polarization", mode="after")(_norm_polarization)


class PlaneWaveSpec(_TemporalMixin):
    type: Literal["plane_wave"] = "plane_wave"
    direction: Literal["+", "-"] = "+"
    amplitude: float = 1.0
    center: Optional[Vec3] = None
    theta: float = Field(default=0.0, ge=0, le=180)   # degrees
    phi: float = Field(default=0.0, ge=0, lt=360)     # degrees
    psi: float = Field(default=0.0, ge=0, lt=360)     # degrees
    # The three slots are the global (x,y,z) transverse window; null/<=0
    # = a non-transverse-window slot; propagation axis = the exactly one
    # non-positive slot (default z)
    size: Optional[tuple[Optional[float], Optional[float], Optional[float]]] = None
    propagation_axis: Optional[Literal["x", "y", "z"]] = None
    from_face: Optional[Literal["x_min", "x_max", "y_min", "y_max",
                                "z_min", "z_max"]] = None


class GaussianBeamSpec(PlaneWaveSpec):
    """Gaussian beam = full plane-wave parameters + a transverse Gaussian
    envelope. radius is the source-plane aperture radius (m),
    corresponding to a 1/e² intensity beam width w ≈ 0.47·radius (to get
    beam width w, give radius ≈ 2.12·w)."""

    type: Literal["gaussian_beam"] = "gaussian_beam"
    radius: float = Field(gt=0)


SourceSpec = Annotated[
    Union[SoftSourceSpec, PlaneWaveSpec, GaussianBeamSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class DownSampleSpec(_StrictModel):
    time_window: Optional[Range2] = None
    stride: int = Field(default=1, ge=1)

    @field_validator("time_window", mode="after")
    @classmethod
    def _win(cls, v):
        if v is not None:
            _check_range(v)
        return v


class ApodizeSpec(_StrictModel):
    side: Optional[Literal["start", "stop", "both"]] = None
    time_window: Optional[Range2] = None
    width: Optional[float] = Field(default=None, gt=0)

    # The same-named field shares one guard with
    # DownSampleSpec.time_window (an inverted apodize window [hi,lo]
    # must be rejected just like the same down_sample input).
    @field_validator("time_window", mode="after")
    @classmethod
    def _win(cls, v):
        if v is not None:
            _check_range(v)
        return v


class _MonitorMixin(_Identified, _StrictModel):
    # Monitor name whitelist [\w\-]{1,64} (re.ASCII — same as the
    # filename whitelist). The name becomes an h5 group name and the
    # address segment of t@<name>.<field> result keys — dots or spaces
    # would make key parsing ambiguous (the field of "a.b" would be
    # swallowed by monitor "a"'s prefix).
    name: str = Field(min_length=1)

    @field_validator("name", mode="after")
    @classmethod
    def _name_ok(cls, v):
        if not _MONITOR_NAME_RE.fullmatch(v):
            raise ValueError(
                f"monitor name {v!r} is invalid: use letters/digits/"
                "underscore/hyphen only, 1-64 chars (ASCII) — the name "
                "becomes an h5 group and a t@<name>.<field> result key; "
                "dots or spaces would make that address ambiguous")
        return v

    center: Vec3 = (0.0, 0.0, 0.0)
    # time/freq default to the [0,0,0] point monitor (null slot =
    # full-domain span — the volume guard stops abuse first)
    size: tuple[Optional[float], Optional[float], Optional[float]] = (0.0, 0.0, 0.0)
    field: str


class TimeMonSpec(_MonitorMixin):
    type: Literal["time"] = "time"
    # field defaults to 'all' (aligned with the fdtd local API default)
    # — all six components are recorded, so Poynting/flux/transmission
    # can be derived; an explicit single component (saving 6x
    # data/VRAM) remains a legal path.
    field: str = "all"
    # down_sample belongs to time only: time-domain gating for freq is
    # apodize's job, and index has no time axis. A freq monitor
    # carrying down_sample would pass validation then hit
    # AttributeError at kernel build time (translate calling a method
    # the builder does not have), and an index monitor carrying it
    # would be silently dropped by translate; both are validation-time
    # 422 here.
    down_sample: Optional[DownSampleSpec] = None

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _FIELD_SET:
            raise ValueError(f"field {v!r} not in {_FIELD_SET}")
        return v


class FreqMonSpec(_MonitorMixin):
    type: Literal["freq"] = "freq"
    freq: dict[str, float]  # {"center": Hz, "bw": Hz, "n": point count}
    apodize: Optional[ApodizeSpec] = None
    # Same as TimeMonSpec: defaults to 'all'.
    field: str = "all"

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _FIELD_SET:
            raise ValueError(f"field {v!r} not in {_FIELD_SET}")
        return v

    @field_validator("freq", mode="before")
    @classmethod
    def _fr_shape(cls, v):
        # Curation of a common type-mismatch: when a list / positional
        # sequence is passed to a freq monitor, pydantic's generic
        # "Input should be a valid dictionary" does not point the way —
        # spell out the three named parameters here.
        if not isinstance(v, dict):
            raise ValueError(
                "freq: freq monitors require three named params "
                "(center [Hz], bw [Hz], n [points]) as a dict, e.g. "
                '{"center": 300e12, "bw": 120e12, "n": 200}; got '
                f"{type(v).__name__}: {v!r}")
        return v

    @field_validator("freq", mode="after")
    @classmethod
    def _fr(cls, v):
        # A misspelled key (like n_points) would be silently dropped on
        # dict rebuild and n would fall back to the default 100 —
        # exactly the silent surface _StrictModel extra=forbid exists
        # to eliminate.
        unknown = set(v) - {"center", "bw", "n"}
        if unknown:
            raise ValueError(
                f"freq: unknown key(s) {sorted(unknown)} — freq takes "
                "exactly (center [Hz], bw [Hz], n [points])")
        center = v.get("center")
        bw = v.get("bw", 0.0)
        n_raw = v.get("n", 100)
        n = int(n_raw)
        if n != n_raw:   # silent truncation of 200.7 to 200 is a
                         # breeding ground for typos — reject
            raise ValueError(
                f"freq.n must be an integer, got {n_raw!r}")
        if center is None or center <= 0:
            raise ValueError("freq.center must be > 0 (Hz)")
        if bw < 0:
            raise ValueError("freq.bw must be >= 0")
        if n < 1:
            raise ValueError("freq.n must be >= 1")
        if center - bw / 2 < 0:
            raise ValueError("freq.center - bw/2 must be >= 0")
        return {"center": float(center), "bw": float(bw), "n": n}


class IndexMonSpec(_MonitorMixin):
    type: Literal["index"] = "index"
    # index defaults to the full domain (null slot = full-domain span)
    size: tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
    # field defaults to 'both': the index vocabulary is exactly
    # eps/mu/both, with no 'all' alias — time/freq's 'all' means all
    # six components, while index's "store everything" has always been
    # called 'both'; forcing unification would add one more spelling.
    field: str = "both"

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _INDEX_FIELD_SET:
            raise ValueError(f"field {v!r} not in {_INDEX_FIELD_SET}")
        return v


MonitorSpec = Annotated[
    Union[TimeMonSpec, FreqMonSpec, IndexMonSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Run and the top-level config
# ---------------------------------------------------------------------------


# run node: **the only time definition = domain.total_time** (SI
# seconds, gt=0 required, see DomainSpec) — the job advances until the
# mesh's time capacity; the step count is a post-build derived quantity
# (a read-only surface in summary/results). The run node itself keeps
# only service fields: n_gpu = optional cap (ge=1, le=MAX_GPU_PER_RUN
# (currently 8); cross-repo contract: how many cards to grant is
# decided by the gateway; does not fit → 422, no free cards → queue).


class RunSpec(_StrictModel):
    n_gpu: Optional[Annotated[int, Field(ge=1, le=MAX_GPU_PER_RUN)]] = None  # optional cap


class SimConfig(_StrictModel):
    domain: DomainSpec
    mesh: MeshSpec
    materials: list[MaterialSpec] = Field(default_factory=list)
    geometry: list[GeometrySpec] = Field(default_factory=list)
    source: list[SourceSpec] = Field(min_length=1)
    monitor: list[MonitorSpec] = Field(default_factory=list)
    run: RunSpec
    # Global parameters node: keys = identifiers
    # (^[A-Za-z_][A-Za-z0-9_]*$, <=64 chars), values = numbers or
    # expression strings referencing the other parameters (environment =
    # the other parameters, topologically evaluated at the top level).
    # Expression evaluation and numeric-slot substitution do not happen
    # at model time — see resolve_parameters (the single parse point of
    # the two-stage validation, parse_config); parameter-sweep axes
    # reference these keys by name (the sweep expander lives in
    # genie.core.sweep).
    parameters: Optional[dict[str, Union[float, int, str]]] = None

    @field_validator("parameters", mode="after")
    @classmethod
    def _check_param_names(cls, v):
        if v is None:
            return v
        for k in v:
            if not valid_param_name(k):
                # The error wording reuses the same implementation as
                # expr.param_name_error (constructive single source, not
                # coincidental hand-copying). The sweep expander's
                # per-point validation still reaches this guard via
                # validate_resolved; do not delete.
                raise ValueError(param_name_error(k))
        return v

    @model_validator(mode="after")
    def _check(self):
        # Monitor names unique across the whole list (a cross-type
        # duplicate would give f.mon['tr'] two answers). The character
        # whitelist for names lives in _MonitorMixin.
        names = [m.name for m in self.monitor]
        if len(names) != len(set(names)):
            dups = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(
                f"duplicate monitor names: {dups} (monitor names must be "
                f"globally unique across all monitor types)")
        # Duplicate-material-name guard: the material name is the
        # identity referenced by geometry.material — a duplicate means
        # "the later shadows the earlier", and the first material
        # silently fails at solve time; this guard stops duplicates at
        # the validation boundary. Material names are required and
        # non-empty (min_length=1); all of them participate in the
        # duplicate check.
        mnames = [m.name for m in self.materials]
        if len(mnames) != len(set(mnames)):
            dups = sorted({n for n in mnames if mnames.count(n) > 1})
            raise ValueError(
                f"duplicate material names: {dups} — material names are "
                f"what geometry references; a duplicate silently shadows "
                f"the earlier definition (rename one of them)")
        # Geometry display names unique: the name is the identity in
        # the tree/3D view; allowing duplicates
        # equals allowing two rows of same-named objects — not finding
        # them or deleting the wrong one is a real cost. Unnamed (None)
        # entries do not participate in the duplicate check: multiple
        # anonymous objects are a legal workflow.
        seen = {}
        for i, g in enumerate(self.geometry):
            n = getattr(g, "name", None)
            if not n:
                continue
            if n in seen:
                raise ValueError(
                    f"duplicate geometry name: {n!r} (objects {seen[n]} "
                    f"and {i}) — names identify objects in the tree/3D "
                    f"view and must be unique")
            seen[n] = i
        # Materials referenced by geometry must exist (the build-time
        # guard, applied earlier)
        known = {m.name for m in self.materials}
        for g in self.geometry:
            if g.material not in known:
                raise ValueError(
                    f"geometry references undefined material "
                    f"{g.material!r} (expected a name from materials[]: "
                    f"{sorted(known) if known else 'none defined'})")
        # periodic/bloch must be paired on the same axis (report early on
        # explicitly set conflicts).
        # [Mixing periodic+bloch on the same axis is legal — both kinds
        # belong to the "translation-symmetry" family.]
        b = self.domain.boundary
        pairs = (("x_min", "x_max"), ("y_min", "y_max"), ("z_min", "z_max"))
        for f_lo, f_hi in pairs:
            for a, other in ((f_lo, f_hi), (f_hi, f_lo)):
                if a in b and b[a].kind in ("periodic", "bloch"):
                    ok = (other in b and b[other].kind in ("periodic", "bloch"))
                    if not ok:
                        raise ValueError(
                            f"boundary {a}={b[a].kind} must be paired with "
                            f"the opposite face {other} set to periodic/"
                            f"bloch (same-axis pairs only)"
                        )
        return self
