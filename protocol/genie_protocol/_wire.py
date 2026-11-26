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

Example wire-format JSON shape (2.0.1a1 nested house, Q17)::

    {
      "model": {                       # structure — shared by every solver
        "materials": [{"name": "sio2", "type": "const", "eps": 2.10}],
        "geometry":  [{"type": "box", "material": "sio2", "priority": 1,
                       "x_range": [-0.5e-6, 0.5e-6], "y_range": [-0.5e-6, 0.5e-6],
                       "z_range": [-0.1e-6, 0.1e-6]}],
        "parameters": {"L": 2e-6}},
      "solver": "fdtd",                # solver selection (open set; omit = fdtd)
      "config": {                      # that solver's envelope
        "domain":   {"x_range": [-1e-6, 1e-6], "y_range": [-1e-6, 1e-6],
                      "z_range": [-1e-6, 1e-6], "dx": 20e-9,
                      "total_time": 60e-15, "cfl": 0.9,
                      "boundary": {"z_min": {"kind": "pml", "layers": 12}}},
        "mesh":     [{"name": "core", "x_range": [-0.2e-6, 0.2e-6],
                      "y_range": [-0.2e-6, 0.2e-6], "z_range": [-1e-6, 1e-6],
                      "factor_x": 2, "factor_y": 2}],   # named refine boxes
        "source":   [{"type": "soft_source", "freq": 300e12, "bandwidth": 60e12,
                      "name": "s0", "center": [0, 0, -0.3e-6],
                      "polarization": [1, 0, 0]}],
        "monitor":  [{"type": "freq", "name": "sp", "center": [0, 0, 0.2e-6],
                      "size": [0, 0, 0], "field": "Ey",
                      "freq": {"center": 300e12, "bw": 120e12, "n": 200}}]},
      "run": {"n_gpu": 1}              # orchestration-level (top level)
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

from ._paramname import param_name_error, valid_param_name


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
# Value shapes of the material eps family (protocol 2.0.1a16, P2-5):
# a float base (iso, same value on all three axes) + OPTIONAL per-axis
# diagonal named keys (``eps_xx`` / ``eps_yy`` / ``eps_zz`` — a key that
# is absent/None falls back to the base). The positional 3-element
# diagonal and the 3×3 full tensor are GONE from the wire: a rotated
# diagonal goes through a named ``matrix_frame`` attribute (a15), and an
# off-diagonal tensor is not expressible at all — the illegal shape does
# not exist at the type level, so no shape guard is needed. Pole
# parameters (PoleSpec) are pure scalars: iso (no axis) | axis-mounted
# ('x'|'y'|'z').


def _check_range(v: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(v[0]), float(v[1])
    if not (hi > lo) or not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(
            f"range [{lo}, {hi}] is invalid (expected: lo < hi and both "
            f"finite)")
    return (lo, hi)


def _check_finite_pos(v):
    """Per-value guard for the eps/mu family (scalar base + per-axis
    diagonal named keys): **finite and > 0** (isfinite included: eps=inf
    passes ">0", would silently build, and then NaN-pollute the whole
    simulation).  Invalid values fail with an early 422 here instead of at
    driver time.  (Protocol a16: the positional diagonal and the full
    tensor form are gone from the wire; a rotated diagonal rides a
    matrix_frame attribute, so positivity is checked per scalar value.)"""
    if v is None:
        return v
    if not (math.isfinite(v) and v > 0):
        raise ValueError(
            f"must be finite and positive, got {v!r}")
    return v


def _check_finite_real(v):
    """Per-value guard for ``eps_inf``/``mu_inf`` (base + per-axis named
    keys): finite, **any sign**.

    There is no physical requirement that the high-frequency constant be
    positive — a negative constant is an ordinary rational model and metals
    routinely fit to one.  Only non-finite values are rejected.
    """
    if v is None:
        return v
    if not math.isfinite(v):
        raise ValueError(
            f"must be finite (any sign), got {v!r}")
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
    #: PML design target (power reflection at the outer edge). ``None`` =
    #: solver default (fdtd 1e-3, mode 1e-3 since 2.0.1a13); an explicit
    #: value overrides that face only. Physically this is the ``R(0)`` that
    #: σmax is derived from — smaller = stronger absorption per cell, which
    #: trades against the discrete entrance reflection (measured optimum is
    #: layer-count dependent: ~8e-3 at 16 layers, ~3e-2 at 60).
    reflection: Optional[float] = Field(default=None, gt=0.0, lt=1.0)
    # bloch
    k: Optional[Vec3] = None
    from_source: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "bloch" and self.k is None and not self.from_source:
            raise ValueError(
                "bloch boundary requires k (3-tuple, SI) or "
                "from_source=true")
        return self


# ---------------------------------------------------------------------------
# Domain / Mesh
# ---------------------------------------------------------------------------


class _Identified:
    """v2 item identity: a globally unique number issued by the system —
    immutable, never reused after deletion; the item collections
    (geometry / materials / source / monitor / mesh) share one id
    allocator. **Not consumed by the kernel** (not in translate);
    SDK-only bookkeeping; save / open / submit all carry it."""

    id: Optional[int] = None


class SpacingSegmentSpec(_StrictModel):
    """非均匀基准网格的**一段**（Q31，2.0.1a18）：区间 [lo, hi] 内
    用间距 ``d`` 落格，段边界两侧由 ``growth_rate`` 渐变过渡
    （1.0 = 硬跳；几何与 refine zone 同一渐变语义）。段表 = 段的
    有序列表，**无缝覆盖该轴全域**（守卫住 DomainSpec——洞没有
    语义，auto_grid 的产物天然全轴覆盖）。"""

    lo: float
    hi: float                          # lo < hi 在 _check_spacing 判（域可负坐标）
    d: float = Field(gt=0)
    growth_rate: float = Field(default=1.2, ge=1.0)


def _check_spacing(axis: str, segments, rng) -> None:
    """单轴段表守卫（DomainSpec 用）：无缝覆盖 + 递增 + 段间距。"""
    lo, hi = rng
    for i, s in enumerate(segments):
        if not s.lo < s.hi:
            raise ValueError(
                f"domain.{axis}_spacing[{i}]: lo must be < hi, got "
                f"({s.lo:.6g}, {s.hi:.6g}) — an inverted segment")
        if i and segments[i - 1].hi != s.lo:
            raise ValueError(
                f"domain.{axis}_spacing[{i}]: must tile the axis with no "
                f"gaps/overlaps — previous segment ends at "
                f"{segments[i - 1].hi:.6g}, this one starts at {s.lo:.6g}")
    if segments[0].lo != lo or segments[-1].hi != hi:
        raise ValueError(
            f"domain.{axis}_spacing must cover the whole axis "
            f"[{lo:.6g}, {hi:.6g}] — the table spans "
            f"[{segments[0].lo:.6g}, {segments[-1].hi:.6g}] (holes carry "
            f"no meaning; give a coarse segment for empty regions)")


class DomainSpec(_RangeModel):
    """fdtd 域 = 仿真世界的**全部标定**（Q30，2.0.1a17；Q31 a18）：三轴
    范围 + 时间/CFL/边界 + **基础网格**。基准每轴二选一（Q31）：

    - 均匀：``dx``（``dy``/``dz`` 缺省继承 dx——三轴共享一份写法）；
    - 非均匀段表：``x/y/z_spacing`` = 段列表（区间+间距+growth），
      **无缝覆盖全域**、与 ``dx`` 全局互斥（一旦某轴用段表，三轴各自
      必填——段表不跨轴继承，不留"dx 部分生效"的隐晦）。

    mesh 段只放带名加密区对象（叠加在基准上的手动层）。auto_grid
    （SDK 动词，Q31）的产物 = 三张段表。

    ``auto_shutoff``（P1-22）：场能量比早停阈值，缺省 None = 关
    （0 同义），推荐 1e-6——判据与终报词见 docs/DIGEST.md 铁律 4。"""

    dx: Optional[float] = Field(default=None, gt=0)
    dy: Optional[float] = Field(default=None, gt=0)
    dz: Optional[float] = Field(default=None, gt=0)
    x_spacing: Optional[list[SpacingSegmentSpec]] = None
    y_spacing: Optional[list[SpacingSegmentSpec]] = None
    z_spacing: Optional[list[SpacingSegmentSpec]] = None
    total_time: float = Field(gt=0)
    cfl: float = Field(default=0.9, gt=0, le=1)
    boundary: dict[str, BoundarySpec] = Field(default_factory=dict)
    # P1-22（2.0.1a19，additive）：场能量衰减自动收工阈值——内核在每
    # 个进度节拍对照 digest 的 energy_ratio（全域含 PML 总场能 ÷ 历史
    # 峰，docs/DIGEST.md 铁律 4），降到阈值以下即优雅早停（终报
    # termination="autoshutoff"，steps.done 如实 < steps.total）。
    # **载荷缺省 None = 关**（旧载荷零扰动）；0 = 显式关；(0,1] 生效。
    # 推荐值 1e-6（站长拍 2026-11-20）。
    auto_shutoff: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _check_faces(self):
        for face in self.boundary:
            if face not in FACES:
                raise ValueError(
                    f"unknown face {face!r} (expected one of: {FACES})")
        # ── Q31 基准网格守卫：每轴「均匀 d 或段表」二选一 ──
        # dx 与任何 spacing 同给 = 422（dx 是三轴共享的均匀写法；一旦
        # 某轴用段表，三轴各自必填——段表不跨轴继承，不留"dx 部分生
        # 效"的隐晦）。段表必须无缝覆盖该轴全域。
        spacings = {"x": self.x_spacing, "y": self.y_spacing,
                    "z": self.z_spacing}
        any_spacing = any(v is not None for v in spacings.values())
        if any_spacing:
            if self.dx is not None:
                raise ValueError(
                    "domain: dx and *_spacing are mutually exclusive — dx "
                    "is the shared-uniform spelling; once any axis uses a "
                    "spacing table, every axis needs its own d or spacing")
            for a in ("x", "y", "z"):
                if spacings[a] is None and getattr(self, f"d{a}") is None:
                    raise ValueError(
                        f"domain: axis {a!r} has no base grid — give d{a} "
                        f"or {a}_spacing (spacing tables do not inherit "
                        f"across axes)")
        for a, segs in spacings.items():
            if segs is not None:
                if not segs:
                    raise ValueError(
                        f"domain.{a}_spacing: empty table — give at least "
                        f"one segment (or drop the key and use d{a})")
                _check_spacing(a, segs, getattr(self, f"{a}_range"))
        return self


class _NamedMesh(_Identified):
    """mesh 对象的命名身份（Q30，2.0.1a17）：``name`` = 地址
    （``<solver>.<name>``），集合内唯一（config 层守卫）；``id`` 与
    其他集合共池——SDK 记账专用，内核不消费（not in translate）。"""

    name: str = Field(min_length=1)


class MeshSpec(_RangeModel, _NamedMesh):
    """fdtd mesh = **带名加密区对象**（Q30，2.0.1a17 断代）：一个 box
    区间（三轴 range）+ 该区间自己的间距定义——**每轴二选一**：

    - 倍率 ``factor_x/y/z``：相对 domain 基础间距，>= 1，缺省 1 =
      该轴基础间距；
    - 绝对值 ``dx/dy/dz``：Lumerical override 式覆盖，必须 <= 该轴
      基础间距（mesh 只加密不粗化；FdtdConfigSpec 组合层校验——
      单段物化时没有 domain 判据）。

    同轴两个**有意义**的定义同给（factor≠1 且绝对值非 None）= 两个真相
    （422）；factor=1 是空操作，与绝对覆盖并存 = 一个真相（绝对生效）
    ——判值不判 fields_set，JSON 往返恒等才闭合。**全 factor=1 = 合法
    无操作区**（Q30 改裁 Q22 的「全 1 拒绝」）。全局帽
    ``max_refinement`` / ``min_cells`` 已删除——网格密度是用户自己的
    责任，系统不设上限。渐变过渡带沿用 refine 旧做法（``growth_rate``，
    1.0 = 硬跳）。块结构网格（独立完整网格）不在本设计内。
    """

    factor_x: int = Field(default=1, ge=1)
    factor_y: int = Field(default=1, ge=1)
    factor_z: int = Field(default=1, ge=1)
    dx: Optional[float] = Field(default=None, gt=0)
    dy: Optional[float] = Field(default=None, gt=0)
    dz: Optional[float] = Field(default=None, gt=0)
    growth_rate: float = Field(default=1.2, ge=1.0)

    @model_validator(mode="after")
    def _check(self):
        # 同轴互斥判「值」不判「显式给」：model_dump() 会把默认值全部
        # 物化（factor=1 / dx=None 都进 dump），fields_set 在 JSON 往返
        # 后失真——往返恒等是硬契约。factor=1 是空操作，与绝对覆盖并存
        # = 一个真相（绝对生效）；两个**有意义**的定义同给才 422。
        for a in ("x", "y", "z"):
            if getattr(self, f"factor_{a}") != 1 and getattr(self, f"d{a}") is not None:
                raise ValueError(
                    f"mesh {self.name!r}: axis {a!r} has both factor_{a}="
                    f"{getattr(self, f'factor_{a}')} and d{a} — a factor "
                    f"and an absolute spacing are two truths for one "
                    f"axis, give exactly one")
        return self


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


class PoleSpec(_StrictModel):
    """Dispersion pole = one rational scalar function mounted on ONE axis::

        χ_axis(s) = num(s) / (s² + b1·s + b0)

    Three forms, **one physics** — ``lorentz`` / ``drude`` are special
    cases of ``rational``, and the kernel realises all three through the
    same state space (Q14: no second recurrence):

    - **iso**: all parameters scalar, no ``axis`` → expanded to one pole
      on each of the three axes by local `_iso_expand` (isotropic
      medium; existing semantics unchanged);
    - **axis** (diagonal anisotropy): all parameters scalar +
      ``axis='x'|'y'|'z'`` → one pole mounted on that axis only
      (lorentz ωp²=Δε·ω₀²; drude ωp²=ω_p²). Different pole sets per
      axis = biaxial media;
    - **rational**: `num=(a0,a1,a2)` (coefficients of 1, s, s²) +
      `den=(b0,b1)` (denominator s²+b1·s+b0, i.e. b0=ω₀², b1=γ) — and
      nothing else; `omega_0`/`omega_p`/`gamma`/`delta_eps` belong to the
      shorthand forms and are rejected here rather than silently ignored.

    **Why axes only** (2026-11-11 定案): the pole↔field coupling is a
    real linear map, and the only form verified stable on the staggered
    grid is the axis-mounted one. Off-axis (oblique) couplings pump
    energy through the half-cell Yee misalignment; complex couplings
    (the old β=conj(α) idea) violate χ(−ω)=conj χ(ω) and act as a
    phase-conjugating (parametric) medium that pumps broadband fields.
    Neither is expressible any more. Rotatory / chiral / gyrotropic
    response belongs INSIDE the oscillator (two-component state with an
    odd-in-ω coupling) — future work, not a pole-to-field map.

    `a2` is the **feedthrough** (instantaneous) term: it needs no state,
    and it is applied through the raster-time effective permittivity
    (Q14 ③), *not* folded into the wire `eps_inf`.

    Gains / guards mirror the kernel exactly: gain = negative oscillator
    strength (a0 < 0 / ωp² < 0, bounded); b1 < 0 (= γ < 0) is not a gain
    channel and is hard-rejected by the kernel's ade_coeffs. The schema
    mirrors current values and invents no guard.

    **Time-harmonic convention (P0-2, 2026-11-19, single system-wide
    statement)**: the pole coefficients above define the real rational
    susceptibility χ(s) — convention-free. Every frequency-domain
    evaluation in the system reads it at **s = −jω (e^{−iωt})**, the
    material-library data convention (N = n + ik, k > 0 for lossy):
    the SDK fit/curve side (``genie.core.material.fit``), the kernel
    ``eps_at`` family, the mode solver, and the fdtd DFT monitors alike.
    A passive medium therefore has Im ε(ω) > 0 and a lossy mode Im neff
    > 0 everywhere. (Before 2026-11-19 the mode subsystem + eps_at
    evaluated at s = +jω; the flip conjugated that side — anchors
    re-pinned the same day, cross-repo canonical-pole anchor:
    optics ``internal_test/test_p02_convention.py``.)
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
    axis: Optional[Literal["x", "y", "z"]] = None   # None = iso (all three axes)

    _v = field_validator("omega_0", "omega_p", mode="after")(_check_pos)
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
        return self


class ConstMatSpec(_Identified, _StrictModel):
    """Constant material (protocol a16 shape). The eps/mu family is a
    **scalar base + optional per-axis diagonal named keys**: ``eps`` is
    the iso base (same value on all three axes), and ``eps_xx`` /
    ``eps_yy`` / ``eps_zz`` override their axis (a key that is absent =
    the base). Every combination is a defined state — partial
    specification is the compact uniaxial form — so there is no shape
    guard to write. The positional 3-element diagonal and the 3×3 full
    tensor are **not expressible** on the wire (P2-5: the direct
    off-diagonal tensor entry was the P0-1 instability entry; a rotated
    diagonal goes through a named ``matrix_frame`` attribute instead).
    Values are finite and > 0 per scalar (dispersionless negative ε/μ is
    a source of numerical divergence; metals go through pole/nk fitting).

    color: an optional color value **for display only** ("#RRGGBB").
    The GUI's 3D view colors by material — solids of the same material
    share one color; by default the frontend picks from a palette via a
    stable hash of the material name. The solver ignores this field (not
    in translate), so it is a purely presentational, optional,
    backward-compatible extension. The material name is the **foreign
    key** referenced by geometry.material (renames do not cascade)."""

    type: Literal["const"] = "const"
    name: str = Field(min_length=1)
    eps: float = 1.0
    eps_xx: Optional[float] = None   # axis override; None = eps base
    eps_yy: Optional[float] = None
    eps_zz: Optional[float] = None
    mu: float = 1.0
    mu_xx: Optional[float] = None
    mu_yy: Optional[float] = None
    mu_zz: Optional[float] = None
    color: Optional[str] = None

    _v = field_validator(
        "eps", "eps_xx", "eps_yy", "eps_zz",
        "mu", "mu_xx", "mu_yy", "mu_zz", mode="after")(_check_finite_pos)
    _vc = field_validator("color", mode="after")(_check_hex_color)


class LorentzMatSpec(_Identified, _StrictModel):
    """Lorentz/Drude dispersive material. eps_inf/mu_inf follow the same
    a16 shape semantics as ConstMatSpec's eps/mu (scalar iso base +
    optional per-axis named keys ``eps_inf_xx``/``eps_inf_yy``/
    ``eps_inf_zz``; absent = base) but **any finite sign** is accepted: a
    negative high-frequency constant is an ordinary rational model
    (metals fit to one), so there is no positivity gate here. Per-pole
    forms of poles/mu_poles are described in PoleSpec (iso |
    axis-mounted, purely scalar parameters + optional axis). color: same
    as ConstMatSpec.color (display only)."""

    type: Literal["lorentz"] = "lorentz"
    name: str = Field(min_length=1)
    eps_inf: float = 1.0
    eps_inf_xx: Optional[float] = None   # axis override; None = base
    eps_inf_yy: Optional[float] = None
    eps_inf_zz: Optional[float] = None
    mu_inf: float = 1.0
    mu_inf_xx: Optional[float] = None
    mu_inf_yy: Optional[float] = None
    mu_inf_zz: Optional[float] = None
    poles: list[PoleSpec] = Field(default_factory=list)
    mu_poles: list[PoleSpec] = Field(default_factory=list)
    color: Optional[str] = None

    _v = field_validator(
        "eps_inf", "eps_inf_xx", "eps_inf_yy", "eps_inf_zz",
        "mu_inf", "mu_inf_xx", "mu_inf_yy", "mu_inf_zz",
        mode="after")(_check_finite_real)
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
# Attributes (protocol 2.0.1a15): named, reusable per-object properties.
# 2026-11-18 定案（A）：材料 frame **不是**几何上的内联字段，而是命名的
# ``matrix_frame`` attribute，几何按名引用（``attribute: "rot"``）——和
# material 同一套「命名 + 外键」复用规则。当前只有一种 attribute；联合按
# ``type`` 判别，第二种类型是加法式扩展。
# ---------------------------------------------------------------------------


def _check_matrix_frame(v):
    """``matrix_frame`` shape guard: 3×3 nested list. Finiteness is covered
    by _StrictModel's full isfinite walk; orthogonality is NOT required at
    the wire (the kernel's SVD projection reports the correction) — a
    client may submit a slightly non-unitary measured frame."""
    if v is None:
        return v
    if len(v) != 3 or any(len(row) != 3 for row in v):
        raise ValueError(
            "matrix_frame must be 3x3 (row-major nested "
            "list[list[float]], material frame -> lab)")
    return v


class MatrixFrameSpec(_Identified, _StrictModel):
    """One named material frame: **material frame -> lab**, a real 3×3 U.

    Applied as ``T_lab = U·T_mat·Uᵀ``. The attribute belongs to the
    structure that references it, not to the material: one material may be
    used by framed and unframed objects at the same time, and several
    objects may share one frame. Pole axes always stay in the material
    frame; the kernel projects U to the nearest orthogonal matrix with an
    SVD (``model.materials.PoleFrame``) and reports the correction. With a
    non-identity frame the material constants must be scalar or diagonal in
    the material frame (a full tensor plus a frame would be two rotations;
    the kernel rejects it at build).
    """

    type: Literal["matrix_frame"] = "matrix_frame"
    name: str = Field(min_length=1)
    matrix_frame: list[list[float]]

    _matrix_frame_guard = field_validator(
        "matrix_frame", mode="after")(_check_matrix_frame)


AttributeSpec = Annotated[
    Union[MatrixFrameSpec],
    Field(discriminator="type"),
]


class _NamedGeo(_Identified):
    """The **required name** of geometry objects + the attribute reference.

    The name is a human-readable address (forms locate by id, and the id
    does not drift on rename); the solver does not consume it (not in
    translate). Unique within a collection (SimConfig._check checks
    duplicates).

    ``attribute`` (protocol 2.0.1a15) references a named entry of
    ``ModelSpec.attributes`` (today: a ``matrix_frame``); None = no
    attribute = identity/no frame (legacy payloads validate verbatim).
    This is the **only** way to give an object a material frame: there is
    no inline geometry field, no second path.
    """

    name: str = Field(min_length=1)
    attribute: Optional[str] = None


# ── 几何旋转（任务 4，2026-11-12 站长拍板：欧拉角）──────────────────
# 全部几何原语可选 ``rotation: Vec3``（度，缺省 (0,0,0) = 不转，加法式，
# 旧载荷逐字合法）。约定（**唯一真相点**；model.geometry.contains /
# fdtd._rasterization._shapes 复述 + 对锁锚）：
#   rotation = (α, β, γ)：绕**全局固定轴** x → y → z 依次外旋，
#   R = Rz(γ)·Ry(β)·Rx(α)；隶属判断 = contains(本体形状, Rᵀ·(p − 参考
#   中心))。参考中心：有 ``center`` 字段的形状 = 其 center；box = 三轴
#   区间中点；polygon_z = 顶点包围盒 xy 中心 + z 区间中点。sphere 绕
#   自身中心旋转恒等（填了合法、无效果——文档写明即可）。


class BoxSpec(_RangeModel, _NamedGeo):
    type: Literal["box"] = "box"
    material: str = Field(min_length=1)
    priority: int = 0
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 欧拉角（度），见上方约定


class SphereSpec(_StrictModel, _NamedGeo):
    type: Literal["sphere"] = "sphere"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    priority: int = 0
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 绕自身中心旋转恒等（合法无效果）


class CylinderSpec(_StrictModel, _NamedGeo):
    type: Literal["cylinder"] = "cylinder"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 欧拉角（度），见 BoxSpec 前约定


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
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 欧拉角（度），见 BoxSpec 前约定


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
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 参考中心 = 顶点包围盒 xy 中心
                                           # + z 区间中点（见 BoxSpec 前约定）

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
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 欧拉角（度），见 BoxSpec 前约定


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
    rotation: Vec3 = (0.0, 0.0, 0.0)      # 欧拉角（度），见 BoxSpec 前约定

    @model_validator(mode="after")
    def _check_radii(self):
        if self.inner_radius >= self.outer_radius:
            raise ValueError(
                "inner_radius must be < outer_radius (0 = solid disk)")
        return self



GeometrySpec = Annotated[
    Union[BoxSpec, SphereSpec, CylinderSpec, NgonZSpec, PolygonZSpec,
          EllipseZSpec, RingZSpec],
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
    center: Vec3 = (0.0, 0.0, 0.0)   # 点源：无 size（P1-1，2026-11-26）
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
    # The three slots are the global (x,y,z) transverse window; null =
    # a non-transverse-window slot; propagation axis = the exactly one
    # null slot (default z). Explicit 0/negative = 422 (a zero-width
    # transverse window is not a physical object — omit the slot instead;
    # soft-source/monitor "0 = point" semantics do NOT apply here).
    size: Optional[tuple[Optional[float], Optional[float], Optional[float]]] = None
    propagation_axis: Optional[Literal["x", "y", "z"]] = None
    from_face: Optional[Literal["x_min", "x_max", "y_min", "y_max",
                                "z_min", "z_max"]] = None

    @field_validator("size", mode="after")
    @classmethod
    def _size_positive(cls, v):
        if v is None:
            return v
        bad = [s for s in v if s is not None and not s > 0.0]
        if bad:
            raise ValueError(
                f"plane-wave size slots must be positive lengths or null "
                f"(the propagation slot takes null); got non-positive "
                f"values {bad}")
        return v


class GaussianBeamSpec(PlaneWaveSpec):
    """Gaussian beam = full plane-wave parameters + a transverse Gaussian
    envelope. radius is the source-plane aperture radius (m),
    corresponding to a 1/e² intensity beam width w ≈ 0.47·radius (to get
    beam width w, give radius ≈ 2.12·w)."""

    type: Literal["gaussian_beam"] = "gaussian_beam"
    radius: float = Field(gt=0)


class ModeSelectionSpec(_StrictModel):
    """Which mode a ``mode`` source injects (nested in the source).

    ``n_modes`` : how many modes the solver returns. Asking for more than
    one is the search-stability knob — a (near-)degenerate cross-section
    can hand back an arbitrary superposition, so solve a few and pick.
    ``index`` : which of them is injected; the engine keeps only that one
    and re-indexes it to 0.
    ``neff_target`` : the search centre handed to the solver
    (``ModeSpec.neff_guess``). ``None`` = the cross-section's maximum
    index (kernel default). For a WEAK-CONTRAST guide that default drops
    the whole batch into the window trap modes (measured: a
    ~1.709 − 0.112j leaky mode of the window itself, four copies) — give
    the guide's own estimate (recipe = ``physics_test/
    mode_weak_contrast.py`` header: n_ref ≈ sqrt(n_clad² + 0.72·Δε),
    window ≥ 8·κ⁻¹, n_modes large enough, then pick the branch by index).
    """

    n_modes: int = Field(default=1, ge=1)
    index: int = Field(default=0, ge=0)
    neff_target: Optional[float] = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def _check(self):
        if self.index >= self.n_modes:
            raise ValueError(
                f"mode_selection.index={self.index} is out of range: it "
                f"picks one of the n_modes={self.n_modes} solved modes "
                f"(0 <= index < n_modes)")
        return self


class ModeSourceSpec(_TemporalMixin):
    """Mode source (TFSF): the engine solves the cross-section's
    eigenmode on the source plane and injects that six-component field.

    Time/frequency come from ``_TemporalMixin``; ``freq`` is the carrier
    AND fixes the solve wavelength (lambda = ref/freq — a mode carrying
    another wavelength would inject "another mode" and is rejected
    engine-side).

    ``center`` / ``size`` / ``propagation_axis`` follow PlaneWaveSpec's
    placement vocabulary. A mode is defined on the WHOLE cross-section,
    so ``size`` is the global full-span plane: both transverse slots stay
    null (null = full span) and the propagation-axis slot is non-positive;
    a positive transverse entry is an aperture window that would truncate
    the mode (rejected engine-side).

    ``rotate_axis / rotate_angle / rotate_radius`` = the tilted solve
    plane — the same three sentences as the mode solver's own section
    (``ModeConfigSpec``): the mode is solved on the plane rotated
    ``rotate_angle`` degrees (right-hand rule) about an axis lying
    ``rotate_radius`` metres from the delivery-face centre, then delivered
    back to the source plane (rigid rotation + phase ramp). This is how a
    mode source gets an oblique incidence: unlike a plane wave it carries
    its own propagation direction and polarization, hence no
    theta/phi/psi here. When ``propagation_axis`` is given, the rotation
    axis must not be it (that would be an in-plane spin); when it is null
    the kernel infers the axis from ``size`` and applies the same guard.

    v1 boundaries: injection runs along +propagation_axis (no
    ``direction``); the source plane is placed by ``center`` (no
    ``from_face``). Injection power = amplitude² (the delivered field is
    normalised to unit power on the source plane).
    """

    type: Literal["mode"] = "mode"
    amplitude: float = 1.0
    center: Optional[Vec3] = None
    size: Optional[tuple[Optional[float], Optional[float],
                         Optional[float]]] = None
    propagation_axis: Optional[Literal["x", "y", "z"]] = None
    mode_selection: ModeSelectionSpec = Field(
        default_factory=ModeSelectionSpec)
    # The same three sentences as ModeConfigSpec's two-face placement.
    # Shared vocabulary, shared guards; the one extra case here is that
    # `propagation_axis` is optional — when it is null the engine infers
    # it from `size`, so the "rotation axis is not the normal" half of the
    # guard lives intake-side.
    rotate_axis: Optional[str] = None
    rotate_angle: float = 0.0
    rotate_radius: float = 0.0

    @model_validator(mode="after")
    def _check(self):
        if self.rotate_axis is not None:
            if self.rotate_axis not in ("x", "y", "z"):
                raise ValueError(
                    f"rotate_axis must be one of 'x'/'y'/'z'; got "
                    f"{self.rotate_axis!r}")
            if (self.propagation_axis is not None
                    and self.rotate_axis == self.propagation_axis):
                raise ValueError(
                    f"rotate_axis={self.rotate_axis!r} is the propagation "
                    f"axis (法线) — 绕它转是面内自转：面没换、横轴互搅、"
                    f"网格碎掉；旋转轴必须是两根横向轴之一（给定 "
                    f"propagation_axis={self.propagation_axis!r} 时是另外"
                    f"两根）")
        if self.rotate_angle != 0.0 and self.rotate_axis is None:
            raise ValueError(
                "rotate_axis is required when rotate_angle != 0 — 不静默"
                "猜旋转方向；交货面的两根横向轴选一根")
        if not math.isfinite(self.rotate_angle) \
                or not -90.0 < self.rotate_angle < 90.0:
            raise ValueError(
                f"rotate_angle must be finite and |angle| < 90: at 90° "
                f"the solve plane contains the propagation axis, so a "
                f"mode on it carries no flux; got {self.rotate_angle!r}")
        return self


SourceSpec = Annotated[
    Union[SoftSourceSpec, PlaneWaveSpec, GaussianBeamSpec, ModeSourceSpec],
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


class ModeExpansionSpec(_StrictModel):
    """Mode-expansion profile attached to a freq monitor (a14).

    Only what an expansion basis needs: how many modes, the search index,
    the cross-section boundary, and the two-face rotation. The plane/window/
    grid come from the monitor itself; ``domain``/``mesh``/``wavelength``
    are deliberately absent (those belong to a standalone mode solver).
    """

    n_modes: int = Field(default=1, ge=1)
    n_ref: Optional[float] = Field(default=None, gt=0.0)
    boundary: dict[str, BoundarySpec] = Field(default_factory=dict)
    rotate_axis: Optional[str] = None
    rotate_angle: float = 0.0
    rotate_radius: float = 0.0
    #: 'center' = one solve at the monitor's middle frequency (default);
    #: 'all' = one solve per monitor frequency.
    freq: Literal['center', 'all'] = 'center'

    @model_validator(mode='after')
    def _check(self):
        for face, b in self.boundary.items():
            if face not in FACES:
                raise ValueError(
                    f"mode_expansion.boundary: unknown face {face!r} "
                    f"(expected one of: {FACES})")
            if b.kind == 'bloch' and b.from_source:
                raise ValueError(
                    'mode_expansion.boundary: bloch needs an explicit k '
                    '(3-tuple, SI) — from_source has no source here; the '
                    'expansion profile is self-contained')
        if self.rotate_axis is not None and self.rotate_axis not in (
                'x', 'y', 'z'):
            raise ValueError(
                f"mode_expansion.rotate_axis must be x|y|z or None, got "
                f"{self.rotate_axis!r}")
        if not -90.0 < float(self.rotate_angle) < 90.0:
            raise ValueError(
                'mode_expansion.rotate_angle must be in (-90, 90) degrees; '
                f'got {self.rotate_angle!r}')
        if self.rotate_angle != 0.0 and not self.rotate_axis:
            raise ValueError(
                'mode_expansion.rotate_axis is required when rotate_angle '
                'is non-zero')
        return self


class FreqMonSpec(_MonitorMixin):
    type: Literal["freq"] = "freq"
    freq: dict[str, float]  # {"center": Hz, "bw": Hz, "n": point count}
    apodize: Optional[ApodizeSpec] = None
    # Same as TimeMonSpec: defaults to 'all'.
    field: str = "all"
    #: optional mode-expansion profile (a14); requires field='all'
    mode_expansion: Optional[ModeExpansionSpec] = None

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


    @model_validator(mode='after')
    def _mode_expansion_needs_all_fields(self):
        if self.mode_expansion is not None and self.field != 'all':
            raise ValueError(
                "freq.mode_expansion requires field='all' (the six Yee "
                "components are the projection input); got "
                f"field={self.field!r}")
        return self


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
    #: Q35：build 预演模式——作业在内核 build 后即收（index/格/显存/模解
    #: neff 报告），不跑时间步。fdtd 专属（mode 无"贵的后半段"可省）；
    #: solver=mode 带它 = 422（守卫在 SimConfig）。
    build: bool = False


# ── 求解器选择面 ───────────────────────────────────────────────────
#
# `solver` 是**开放集合**而不是 Literal：求解器是插件，写死枚举等于每加一个
# 算法升一次协议版本。"声明了但本机没装"属**部署事实**，由使用方按能力集拒绝
# （内核 400 + available 清单），不归协议——协议只判形状。
#
# 图案只约束"像不像一个标识"（小写开头 + 小写/数字/下划线，≤32）：长度下限
# 之类是口味不是形状，不拦（DISCIPLINE §4 输入校验口径）。
_SOLVER_NAME = r"^[a-z][a-z0-9_]{0,31}$"


class ModelSpec(_StrictModel):
    """结构段（Q17 嵌套户型）：materials / geometry / attributes /
    parameters——全求解器共享的一份结构（= SDK 的 genie 本体 = 内核
    GenSim 场景）。材料外键、属性外键/重名、重名守卫、参数名图案都随字段
    搬进这里。

    ``attributes``（2.0.1a15）是命名、可复用的对象属性（今天只有
    ``matrix_frame``）；None / 缺省 = 无属性，旧载荷逐字合法（不写空表——
    SDK 提升时同样省略空表，保旧 payload / digest 不变）。"""

    materials: list[MaterialSpec] = Field(default_factory=list)
    geometry: list[GeometrySpec] = Field(default_factory=list)
    attributes: Optional[list[AttributeSpec]] = None
    # Global parameters node: keys = identifiers
    # (^[A-Za-z_][A-Za-z0-9_]*$, <=64 chars), values = numbers or
    # expression strings referencing the other parameters (environment =
    # the other parameters, topologically evaluated at the top level;
    # the resolver lives in the SDK, genie.core.param).
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
        # Duplicate-material-name guard: the material name is the
        # identity referenced by geometry.material — a duplicate means
        # "the later shadows the earlier", and the first material
        # silently fails at solve time.
        mnames = [m.name for m in self.materials]
        if len(mnames) != len(set(mnames)):
            dups = sorted({n for n in mnames if mnames.count(n) > 1})
            raise ValueError(
                f"duplicate material names: {dups} — material names are "
                f"what geometry references; a duplicate silently shadows "
                f"the earlier definition (rename one of them)")
        # Geometry display names unique (unnamed entries are legal).
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
        # Materials referenced by geometry must exist (build-time guard).
        known = {m.name for m in self.materials}
        for g in self.geometry:
            if g.material not in known:
                raise ValueError(
                    f"geometry references undefined material "
                    f"{g.material!r} (expected a name from materials[]: "
                    f"{sorted(known) if known else 'none defined'})")
        # Attributes are named objects referenced by geometry (same pattern
        # as materials): unique names, and every reference must resolve.
        anames = [a.name for a in (self.attributes or [])]
        if len(anames) != len(set(anames)):
            dups = sorted({n for n in anames if anames.count(n) > 1})
            raise ValueError(
                f"duplicate attribute names: {dups} — attribute names are "
                f"what geometry references; a duplicate silently shadows "
                f"the earlier definition (rename one of them)")
        known_attr = set(anames)
        for g in self.geometry:
            ref = getattr(g, "attribute", None)
            if ref is not None and ref not in known_attr:
                raise ValueError(
                    f"geometry references undefined attribute {ref!r} "
                    f"(expected a name from attributes[]: "
                    f"{sorted(known_attr) if known_attr else 'none defined'})")
        return self


class FdtdConfigSpec(_StrictModel):
    """fdtd 包络段：domain / mesh / source / monitor——**这些属于 fdtd
    这个求解器，不属于模型**（Q17：fdtd 只是一种 solver；b8 及以前的
    平铺户型是 fdtd 冒充模型的遗物，2.0.1a1 断代拆除）。2.0.1a17 起
    domain 携带基础网格（dx/dy/dz），``mesh`` = **带名加密区对象列表**
    （Q30；缺省空表 = 无加密区）。"""

    domain: DomainSpec
    mesh: list[MeshSpec] = Field(default_factory=list)
    source: list[SourceSpec] = Field(min_length=1)
    monitor: list[MonitorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        # Mesh names unique across the whole list (names are addresses —
        # fdtd.<name>; a duplicate would give the address two answers).
        mnames = [m.name for m in self.mesh]
        if len(mnames) != len(set(mnames)):
            dups = sorted({n for n in mnames if mnames.count(n) > 1})
            raise ValueError(
                f"duplicate mesh names: {dups} (mesh names must be unique "
                f"within the solver — they are addresses)")
        # Absolute mesh spacing must not exceed the domain base on that
        # axis: meshes refine, they do not coarsen. Q31：基准可能是段表
        # ——绝对 mesh 比它**覆盖到的段的最细间距**（重置不得损失任何
        # 被覆盖段的分辨率）；均匀轴比标量。
        def _axis_base(a):
            segs = getattr(self.domain, f"{a}_spacing")
            if segs is not None:
                return ("table", segs)
            d = getattr(self.domain, f"d{a}") or self.domain.dx
            return ("uniform", d)

        for mi, m in enumerate(self.mesh):
            for a in ("x", "y", "z"):
                d = getattr(m, f"d{a}")
                if d is None:
                    continue
                kind, bv = _axis_base(a)
                if kind == "uniform":
                    if bv is not None and d > bv:
                        raise ValueError(
                            f"mesh {m.name!r}: d{a}={d:.6g} is coarser "
                            f"than the domain base d{a}={bv:.6g} — a mesh "
                            f"refines its box, it cannot coarsen it (use "
                            f"factor or a spacing <= the base)")
                else:
                    rng = getattr(m, f"{a}_range")
                    covered = [s.d for s in bv
                               if s.lo < rng[1] and rng[0] < s.hi]
                    if covered and d > min(covered):
                        raise ValueError(
                            f"mesh {m.name!r}: d{a}={d:.6g} is coarser "
                            f"than the finest covered base segment "
                            f"({min(covered):.6g}) — a mesh refines, it "
                            f"cannot coarsen any segment it covers")
        # Monitor names unique across the whole list (a cross-type
        # duplicate would give the reader two answers).
        names = [m.name for m in self.monitor]
        if len(names) != len(set(names)):
            dups = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(
                f"duplicate monitor names: {dups} (monitor names must be "
                f"globally unique across all monitor types)")
        # periodic/bloch must be paired on the same axis (mixing the two
        # on the same axis is legal — same translation-symmetry family).
        b = self.domain.boundary
        pairs = (("x_min", "x_max"), ("y_min", "y_max"), ("z_min", "z_max"))
        for f_lo, f_hi in pairs:
            for a, other in ((f_lo, f_hi), (f_hi, f_lo)):
                if a in b and b[a].kind in ("periodic", "bloch"):
                    ok = (other in b and b[other].kind in ("periodic",
                                                          "bloch"))
                    if not ok:
                        raise ValueError(
                            f"boundary {a}={b[a].kind} must be paired "
                            f"with the opposite face {other} set to "
                            f"periodic/bloch (same-axis pairs only)")
        return self


#: 传播轴 → (横截面轴1, 横截面轴2)（Q18 拍②：窗口/网格用**真轴名**，
#: h/v 视图相对词退役；轴1 = 字母序在前的横轴——mesh「第二轴缺省 =
#: 第一轴」的锚）。mode 的域是 2D 截面：传播轴没有 range / 网格字段。
_TRANSVERSE = {"x": ("y", "z"), "y": ("x", "z"), "z": ("x", "y")}


def _check_opt_range(v):
    """Optional 版 range 守卫：None 直通（mode 域的 range 按轴可选，
    横截面两轴必填、传播轴禁止——存在性守卫在 ModeDomainSpec._check）。"""
    return None if v is None else _check_range(v)


class ModeDomainSpec(_StrictModel):
    """mode 包络段·域（Q18 拍②/③，2.0.1a3 断代；Q20 v2.1；Q30 2.0.1a17
    基础网格自 ModeMeshSpec 搬入；Q31 a18 段表）：截面放置 + 两根
    **横截面真轴**的窗口 range 元组 + 背景 + **基础网格**（每横轴
    间距 / 坐标向量 / 段表**三选一**）。center/span 拼写退役（与 fdtd
    同一 range 心智）；range 元组槽位吃表达式串（Q13 同一求值面）。
    **Q20：横轴 range 可省**——domain 给了该轴 coords 时窗口由坐标
    向量 min/max 派生（都给 = 两个真相，在 ModeConfigSpec 组合层
    422——单段物化时没有对方判据，存在性守卫跟着事实走）；**段表
    形态下 range 必填**（段表只给间距不给窗口）。**传播轴禁止任何
    网格字段与 range**（2D 截面不沿传播轴网格化，本段._check）。
    auto_grid（Q31）的 mode 对称产物 = 横轴段表。"""

    axis: Literal["x", "y", "z"]              # 传播轴（截面法向）
    position: float                           # 截面沿传播轴的位置 [m]
    x_range: Optional[Range2] = None
    y_range: Optional[Range2] = None
    z_range: Optional[Range2] = None
    # ── 基础网格（Q30 自旧 ModeMeshSpec 搬入；Q31 加段表）──
    dx: Optional[float] = Field(default=None, gt=0.0)
    dy: Optional[float] = Field(default=None, gt=0.0)
    dz: Optional[float] = Field(default=None, gt=0.0)
    #: 采样点坐标向量（Q18 拍② / Q20）：语义 = **格心**（cell centres），
    #: 不是格边——内核由相邻中点派生格边、端部各向外延拓半格
    #: （mode/solver.py `_axis_edges`）；与该轴 d/spacing 三选一。
    x_coords: Optional[list[float]] = None
    y_coords: Optional[list[float]] = None
    z_coords: Optional[list[float]] = None
    #: Q31：非均匀基准段表（每段区间+间距+growth）。横轴与 d/coords
    #: 三选一；无缝覆盖该轴窗口（range 必填——守卫住组合层，这里
    #: 单段物化时窗口可能由 coords 派生缺席）。
    x_spacing: Optional[list[SpacingSegmentSpec]] = None
    y_spacing: Optional[list[SpacingSegmentSpec]] = None
    z_spacing: Optional[list[SpacingSegmentSpec]] = None

    _v = field_validator("x_range", "y_range", "z_range",
                         mode="after")(_check_opt_range)

    def _axis_mesh(self, a: str):
        """单轴网格口径 → ("spacing", v) | ("coords", v) | ("table", v)
        | None。"""
        if getattr(self, f"d{a}") is not None:
            return ("spacing", getattr(self, f"d{a}"))
        if getattr(self, f"{a}_coords") is not None:
            return ("coords", getattr(self, f"{a}_coords"))
        if getattr(self, f"{a}_spacing") is not None:
            return ("table", getattr(self, f"{a}_spacing"))
        return None

    @model_validator(mode="after")
    def _check(self):
        # 传播轴 range / 网格字段禁止：那是 fdtd 的 3D 域概念，mode 是
        # 2D 问题（写了就 422，不给"静默忽略"留门）。横轴 range 的
        # "必填/可省"守卫住 ModeConfigSpec（要看有没有 coords——Q20）。
        t1, t2 = _TRANSVERSE[self.axis]
        for a in ("x", "y", "z"):
            if a == self.axis and (
                    getattr(self, f"{a}_range") is not None
                    or self._axis_mesh(a) is not None):
                raise ValueError(
                    f"domain.{a}_range / d{a} / {a}_coords / {a}_spacing: "
                    f"axis {a!r} is the propagation axis — a mode "
                    f"cross-section has no extent or grid along it (give "
                    f"the two transverse axes {t1}/{t2})")
            # 每轴「间距 / 坐标向量 / 段表」三选一（Q31 从二选一放宽
            # 为三选一——同轴两个非 None = 两个真相 422）+ coords 形状
            # （>=2 点、严格递增）。
            d = getattr(self, f"d{a}")
            c = getattr(self, f"{a}_coords")
            s = getattr(self, f"{a}_spacing")
            if sum(x is not None for x in (d, c, s)) > 1:
                raise ValueError(
                    f"domain: axis {a!r} has more than one of d{a} / "
                    f"{a}_coords / {a}_spacing — spacing, coordinates and "
                    f"a spacing table are two truths for one grid, give "
                    f"exactly one")
            if c is not None:
                if len(c) < 2:
                    raise ValueError(
                        f"domain.{a}_coords needs >= 2 points, got "
                        f"{len(c)}")
                if any(c[i + 1] <= c[i] for i in range(len(c) - 1)):
                    raise ValueError(
                        f"domain.{a}_coords must be strictly increasing")
        return self


class ModeMeshSpec(_StrictModel, _NamedMesh):
    """mode mesh = **带名 2D 加密区对象**（Q30 对称迁移，2.0.1a17 断代）：
    两根横轴的 box 区间 + 每轴间距定义——**每轴二选一**（factor 缺省 1
    / 绝对值 <= 该轴基础间距，组合层校验）。传播轴的任何槽（range /
    factor / d*）在 ModeConfigSpec 组合层 422（单段物化时不知道哪根
    轴是传播轴）。**全 factor=1 = 合法无操作区**（Q30 改裁 Q22）。
    生成器 = fdtd `_grow_refined_grid` 同款算法（mode 侧复述 + 对锁
    测试）。渐变过渡带沿用 refine 旧做法（growth_rate，1.0 = 硬跳）。"""

    x_range: Optional[Range2] = None
    y_range: Optional[Range2] = None
    z_range: Optional[Range2] = None
    factor_x: int = Field(default=1, ge=1)
    factor_y: int = Field(default=1, ge=1)
    factor_z: int = Field(default=1, ge=1)
    dx: Optional[float] = Field(default=None, gt=0.0)
    dy: Optional[float] = Field(default=None, gt=0.0)
    dz: Optional[float] = Field(default=None, gt=0.0)
    growth_rate: float = Field(default=1.2, ge=1.0)

    _v = field_validator("x_range", "y_range", "z_range",
                         mode="after")(_check_opt_range)

    @model_validator(mode="after")
    def _check(self):
        # 同轴互斥判「值」不判「显式给」：model_dump() 会把默认值全部
        # 物化（factor=1 / dx=None 都进 dump），fields_set 在 JSON 往返
        # 后失真——往返恒等是硬契约。factor=1 是空操作，与绝对覆盖并存
        # = 一个真相（绝对生效）；两个**有意义**的定义同给才 422。
        for a in ("x", "y", "z"):
            if getattr(self, f"factor_{a}") != 1 and getattr(self, f"d{a}") is not None:
                raise ValueError(
                    f"mesh {self.name!r}: axis {a!r} has both factor_{a}="
                    f"{getattr(self, f'factor_{a}')} and d{a} — a factor "
                    f"and an absolute spacing are two truths for one "
                    f"axis, give exactly one")
        return self


class ModeConfigSpec(_StrictModel):
    """mode 包络段 v2（Q18，2.0.1a3 断代；Q30 2.0.1a17 mesh 对象化）：
    ``domain / mesh / boundary`` 三段 + 顶层 ``wavelength / n_modes``。
    不造假对称——**无 source / monitor 段**（本征问题不吃激励）；boundary
    复用 fdtd 的 ``BoundarySpec``（per-edge，键 = 横截面四缘真轴名；
    ``n_pml`` 退役 → ``layers``）。2.0.1a1 的平铺 13 字段形
    （center/span_h/dh/…）作废。domain 携带基础网格（间距或 coords），
    ``mesh`` = 带名 2D 加密区对象列表（Q30 对称迁移）。"""

    domain: ModeDomainSpec
    #: 带名加密区对象列表（Q30 对称迁移，2.0.1a17）：缺省空表 = 无
    #: 加密区；传播轴槽/横轴 box 完整性/coords 互斥/绝对<=基础 在
    #: 本段组合层守卫。
    mesh: list[ModeMeshSpec] = Field(default_factory=list)
    boundary: dict[str, BoundarySpec] = Field(default_factory=dict)
    #: 真空波长 [m]——**单字段 union**（站长拍 2026-11-13）：``float`` = 单 λ
    #: （长度 1 的特例），``list[float]`` = 多 λ（serve 升序串行逐 λ 求解）；
    #: 不立 ``wavelengths`` 第二概念。list 非空、全正数、**严格升序**
    #: （重复/乱序 422——升序是解析契约，静默排序会造出两个"不同"作业
    #: 同结果的 digest 脏）；存储保持原样（``float`` 不改写为 ``[float]``，
    #: 存量载荷字节不变）。
    wavelength: Union[float, list[float]]
    n_modes: int = Field(default=1, ge=1)
    #: 搜索折射率（Q20，直通内核 ``ModeSpec.neff_guess``：shift-invert
    #: 中心 + 结果按 |neff−它| 排序）。缺省 None = 截面最大横向折射率
    #: （内核现缺省行为，零变化）。
    n_ref: Optional[float] = Field(default=None, gt=0.0)
    #: 两个面（2026-11-16）：计算面相对交货面（= domain）的摆放——一个
    #: 旋转的三句话。交货面 = domain 本身（轴/位置/窗口）；计算面 = 绕
    #: ``rotate_axis`` 转 ``rotate_angle`` 度（右手定则），旋转轴离交货面
    #: 中心 ``rotate_radius`` 米（沿垂直于轴的横向量、正方向为正，杠杆
    #: 不是范围）。``rotate_axis`` = 两根横向轴之一，**禁 = domain.axis**
    #: （绕法线转是面内自转，无意义）；``rotate_angle != 0`` 时必填（不
    #: 静默猜方向）；``|angle| < 90``（90° 时计算面含传播轴，模无通量）。
    #: ``angle = 0``（缺省）= 无旋转，三者为规范自由度——存量载荷不变。
    rotate_axis: Optional[str] = None
    rotate_angle: float = 0.0
    rotate_radius: float = 0.0

    @field_validator("wavelength", mode="after")
    @classmethod
    def _check_wavelength(cls, v):
        vals = v if isinstance(v, list) else [v]
        if not vals:
            raise ValueError(
                "wavelength: empty list — give at least one wavelength "
                "(or a single float)")
        for w in vals:
            if isinstance(w, bool) or not (
                    isinstance(w, (int, float)) and math.isfinite(w)
                    and w > 0.0):
                raise ValueError(
                    f"wavelength: every wavelength must be a finite "
                    f"positive number [m], got {w!r}")
        if any(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)):
            raise ValueError(
                "wavelength: list must be strictly ascending (duplicates "
                "and unsorted input are rejected — sort before sending)")
        return v

    @model_validator(mode="after")
    def _check(self):
        # 组合层守卫（单段物化没有对方判据的，都住这里）。
        ax = self.domain.axis
        t1, t2 = _TRANSVERSE[ax]
        # Q20：横轴 range × coords——每根横轴恰有一个真相：
        #   coords 在手 → range 可省（窗口由坐标 min/max 派生）；
        #   两者都给 → 422（两个真相不许并存，与间距/coords 二选一
        #   同款铁律）；coords/段表都没有 → range 必填（间距离散它；
        #   Q31 段表只给间距不给窗口，段表轴的 range 同样必填）。
        for a in (t1, t2):
            rng = getattr(self.domain, f"{a}_range")
            coords = getattr(self.domain, f"{a}_coords")
            if rng is not None and coords is not None:
                raise ValueError(
                    f"domain.{a}_range and domain.{a}_coords are two "
                    f"truths for one axis window — give exactly one "
                    f"(coords imply the extent)")
            if rng is None and coords is None:
                raise ValueError(
                    f"domain.{a}_range is required unless domain.{a}_coords "
                    f"is given (axis={ax!r} → transverse axes {t1}/{t2})")
            # 段表轴：无缝覆盖窗口（range 必在上面已判非 None）。
            segs = getattr(self.domain, f"{a}_spacing")
            if segs is not None:
                _check_spacing(a, segs, getattr(self.domain, f"{a}_range"))
        # 轴1（字母序在前的横轴）必须给 spacing/coords/段表之一；轴2
        # 缺省 = 轴1 的**间距**（向量与段表都不可继承——两轴长度各自
        # 独立；段表不跨轴继承与 fdtd 同一条铁律）。
        m1 = self.domain._axis_mesh(t1)
        m2 = self.domain._axis_mesh(t2)
        if m1 is None:
            raise ValueError(
                f"domain: axis {t1!r} needs d{t1} or {t1}_coords or "
                f"{t1}_spacing (nothing given); d{t2} alone cannot seed "
                f"the first axis")
        if m2 is None and m1[0] != "spacing":
            raise ValueError(
                f"domain: axis {t2!r} needs its own d{t2} / {t2}_coords / "
                f"{t2}_spacing (only a spacing can be inherited from "
                f"{t1}, not coordinates or a table)")
        # mesh zone 守卫（Q30：带名 2D 加密区）：名字唯一；传播轴槽禁；
        # 横轴 box 完整（两根横轴 range 都给）；zone 区间 × 该轴 coords
        # = 两个真相；绝对间距 <= 该轴基础间距（mesh 只加密不粗化）。
        znames = [z.name for z in self.mesh]
        if len(znames) != len(set(znames)):
            dups = sorted({n for n in znames if znames.count(n) > 1})
            raise ValueError(
                f"duplicate mesh names: {dups} (mesh names must be unique "
                f"within the solver — they are addresses)")
        # 每横轴的基准（绝对 mesh 的粗化判据，Q31 段表适配）：均匀轴比
        # 标量（缺省继承轴1 间距）；段表轴比**覆盖段的最细间距**——重置
        # 不得损失任何被覆盖段的分辨率；coords 轴无标量基准（zone 区间
        # 与 coords 本就互斥）。
        def _base_of(a):
            am = self.domain._axis_mesh(a)
            if am is None and self.domain._axis_mesh(t1)[0] == "spacing":
                am = ("spacing", self.domain._axis_mesh(t1)[1])
            if am is None:
                return None
            return am if am[0] != "spacing" else ("uniform", am[1])

        for zi, z in enumerate(self.mesh):
            for a in ("x", "y", "z"):
                rng = getattr(z, f"{a}_range")
                d = getattr(z, f"d{a}")
                if a == ax and (rng is not None or d is not None
                                or getattr(z, f"factor_{a}") != 1):
                    raise ValueError(
                        f"mesh[{zi}] ({z.name!r}): axis {a!r} is the "
                        f"propagation axis — a mode cross-section is not "
                        f"meshed along it (transverse axes: {t1}/{t2})")
                if a in (t1, t2):
                    if rng is None:
                        raise ValueError(
                            f"mesh[{zi}] ({z.name!r}): {a}_range is "
                            f"required — a mesh is a box on the two "
                            f"transverse axes (factor=1 marks an axis "
                            f"that is not refined)")
                    if rng is not None and getattr(
                            self.domain, f"{a}_coords") is not None:
                        raise ValueError(
                            f"mesh[{zi}] ({z.name!r}): {a}_range and "
                            f"domain.{a}_coords are two truths for one "
                            f"axis grid — coords carry the whole axis, "
                            f"a mesh cannot apply on top")
                    if d is not None:
                        base = _base_of(a)
                        if base is not None:
                            kind, bv = base
                            if kind == "uniform":
                                if d > bv:
                                    raise ValueError(
                                        f"mesh[{zi}] ({z.name!r}): d{a}="
                                        f"{d:.6g} is coarser than the base "
                                        f"d{a}={bv:.6g} — a mesh refines "
                                        f"its box, it cannot coarsen it")
                            else:      # table：比覆盖段的最细间距
                                covered = [s.d for s in bv
                                           if s.lo < rng[1]
                                           and rng[0] < s.hi]
                                if covered and d > min(covered):
                                    raise ValueError(
                                        f"mesh[{zi}] ({z.name!r}): d{a}="
                                        f"{d:.6g} is coarser than the "
                                        f"finest covered base segment "
                                        f"({min(covered):.6g}) — a mesh "
                                        f"refines, it cannot coarsen any "
                                        f"segment it covers")
        # 两个面：摆放三句话的守卫（口径 = 内核 _grid.solve_grid，响亮不猜）
        import math as _m
        if self.rotate_axis is not None:
            if self.rotate_axis not in ("x", "y", "z"):
                raise ValueError(
                    f"rotate_axis must be one of 'x'/'y'/'z'; got "
                    f"{self.rotate_axis!r}")
            if self.rotate_axis == ax:
                raise ValueError(
                    f"rotate_axis={self.rotate_axis!r} is the propagation "
                    f"axis (法线) — 绕它转是面内自转：面没换、横轴互搅、"
                    f"网格碎掉；旋转轴必须是两根横向轴之一（{t1!r} 或 "
                    f"{t2!r}）")
        if self.rotate_angle != 0.0 and self.rotate_axis is None:
            raise ValueError(
                "rotate_axis is required when rotate_angle != 0 — 不静默"
                "猜旋转方向；交货面的两根横向轴选一根")
        if not _m.isfinite(self.rotate_angle) \
                or not -90.0 < self.rotate_angle < 90.0:
            raise ValueError(
                f"rotate_angle must be finite and |angle| < 90: at 90° "
                f"the solve plane contains the propagation axis, so a "
                f"mode on it carries no flux; got {self.rotate_angle!r}")

        # boundary：键 = 横截面四缘；kind 限 pec/pmc/periodic/pml/bloch
        # （bloch 需显式 k——from_source 是 fdtd 的斜入射概念，mode 无源可读）；
        edges = {f"{a}_{s}" for a in (t1, t2) for s in ("min", "max")}
        for face, b in self.boundary.items():
            if face not in edges:
                raise ValueError(
                    f"boundary face {face!r} is not a cross-section edge "
                    f"(expected one of {sorted(edges)}; propagation-axis "
                    f"faces belong to fdtd, not mode)")
            if b.kind == "bloch" and b.from_source:
                raise ValueError(
                    f"boundary {face}: bloch needs an explicit k (3-tuple, "
                    f"SI) — from_source is an fdtd-side knob; mode has no "
                    f"source to read")
        return self


#: 求解器名 → 包络段形状（协议给**认识的**求解器定形状；开放集合语义
#: 不变——未列名的求解器其 config 只要求是 dict，"装没装 / 形状对不对"
#: 是部署事实，归内核能力表拒绝）。
_CONFIG_OF = {"fdtd": FdtdConfigSpec, "mode": ModeConfigSpec}


class SimConfig(_StrictModel):
    """嵌套户型（Q17，2.0.1a1 断代）：结构一段 + 求解器选择面 + 该
    求解器的包络段 + 顶层 run。b8 及以前的平铺载荷（domain/materials/
    … 挂顶层）一律 422——那是 fdtd 冒充模型的形状。"""

    model: ModelSpec
    # 求解器选择面：缺省 None = fdtd（声明式缺省，不是兼容垫片——
    # 包络段 config 仍必填）。
    solver: Optional[str] = Field(default=None, pattern=_SOLVER_NAME)
    # 包络段：dict 进、按 solver 分派验证后物化为对应 Spec 实例
    # （认识谁验谁；未列名求解者原样透传——形状归部署侧能力表）。
    config: Optional[Union[FdtdConfigSpec, ModeConfigSpec, dict]] = None
    # 编排面（顶层）：n_gpu 是资源旋钮，任何求解器都可能用，
    # 不属于 fdtd（Q17 拍①）。
    run: RunSpec = Field(default_factory=RunSpec)

    @model_validator(mode="after")
    def _check(self):
        # ── 单求解器路：config 必在、按 solver 分派验证并物化 ────
        solver = self.solver or "fdtd"
        if self.config is None:
            raise ValueError(
                f"config is required — the {solver!r} solver's envelope "
                f"(domain/mesh/source/monitor for fdtd) belongs under "
                f"'config', not at the top level (2.0.1a1 nested house)")
        spec = _CONFIG_OF.get(solver)
        if spec is not None and not isinstance(self.config, spec):
            object.__setattr__(self, "config", spec.model_validate(
                self.config if isinstance(self.config, dict)
                else self.config.model_dump()))
        # ── Q35：build 预演是 fdtd 专属（mode 的本征值问题就是全部
        # 工作，无"贵的后半段"可省）——mode 带它 = 422，唯一守卫点。
        if self.run.build and solver == "mode":
            raise ValueError(
                "run.build is an fdtd-only preview mode — mode's eigen "
                "solve IS the whole job; submit it as a normal mode job")
        return self
