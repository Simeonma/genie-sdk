"""genie.core.material — material fitting (fit_nk) + general assembly
(assemble_nk) + local n-k library loading.

Public surface:

- Public entry points: ``fit_nk`` (**scalar single-component,
  numeric-only**: single-component nk → Drude-Lorentz fit numbers,
  six-key dict) / ``assemble_nk`` (**the only node factory**:
  per-component results → a single node) / ``load_nk`` (library
  name → raw n-k data) / ``list_library_materials`` (list of
  library names) / ``mat_from_nk`` (one-call convenience: library
  name → a material node).
- fit_nk fits one component at a time — generality comes from
  **composition** (isotropic / anisotropic / full tensor = fit each
  tensor element separately, then assemble); the return type is
  always one stable shape; the **only material-node factory =
  assemble_nk** (fit produces no material_node / takes no name); iso
  likewise always goes through assemble — three diagonals sharing
  one fit = three same-parameter axial rank-1 poles (an explicit
  three-way expression of isotropy; the kernel likewise expands
  alpha-less iso three ways via _iso_expand — the two paths are
  physically equivalent); each off-diagonal takes one upper-triangle
  name and a **mirrored mount** to preserve symmetry.
- material_node validation = a real protocol
  (``LorentzMatSpec.model_validate``); carries no color / no id
  (that is the id issuer's business).
- quality holds five scalars; failure taxonomy: non-finite inside
  the fit window = ValueError + a data-cleaning prescription;
  hitting the iteration cap = converged=False + an iteration
  prescription.
- Error messages are always English.
- Data files = ``genie-sdk/resources/materials/*.yml`` (37 files;
  refractiveindex.info format, CC0) — located relative to
  ``__file__`` (the data ships inside the package, not at an
  external path); parsing semantics: tabulated DATA blocks + both
  formula 1/4 paths, bad rows skipped with a warning without
  truncating, non-finite rows dropped, a lower bound of ≥5 rows.
- scipy lazy import: imported only at the first real fit; if
  missing, an ImportError is reported explicitly — no silent
  degradation.
"""
from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from genie_protocol import _wire
from .fit import (C0_SI, ComponentFit, DispersionResult,
                   coverage_fraction, dl_residual,
                   evaluate_dispersion, fit_adaptive,
                   fit_drude_lorentz, fit_error, fit_range_bounds,
                   params_to_poles, passivity_report, pole_to_node,
                   scalar_result)

__all__ = ["fit_nk", "assemble_nk", "mat_from_nk", "load_nk",
           "list_library_materials"]

#: Speed of light [m/s] (same value as the kernel's C0_PHYS).

#: Library data directory: ships inside the package, resolved
#: relative to ``__file__`` (core/material/ up three levels = the
#: package root genie-sdk/).
_DATA_DIR = Path(__file__).resolve().parents[2] / "resources" / "materials"


# ═══════════════════════════════════════════════════════════════════════════
# Library loading (load_nk / list_library_materials)
# ═══════════════════════════════════════════════════════════════════════════

def _check_material_name(name) -> None:
    """Name guard: library names map 1:1 to files — path separators /
    `.` / `..` / empty / non-strings are rejected at the gate
    (errors in English)."""
    if (not isinstance(name, str) or not name
            or "/" in name or "\\" in name or name in (".", "..")):
        raise ValueError(
            f"load_nk(name) requires a bare material name (no path "
            f"separators), got {name!r}")


def list_library_materials() -> list[str]:
    """All library names in the locally bundled n-k library (sorted; works offline)."""
    try:
        files = list(_DATA_DIR.glob("*.yml"))
    except OSError:
        return []
    return sorted(f.stem for f in files)


def _scan_entries(raw_text: str, src: str = "") -> list[dict]:
    """RII yml → a list of data entries (multi-block support: every
    ``- type:`` block is kept, not only the first).

    Each entry is
    ``{type, direction, wavelength_range, coefficients, data_lines}``;
    ``direction`` is our extension key on the RII page format
    (o / e / x / y / z); an unlabeled block = a single iso component.
    Bad rows (numeric-shaped but missing columns / stray tokens) are
    counted and skipped with a warning, without truncating the block.
    """
    entries: list[dict] = []
    cur: dict | None = None
    in_data = False
    bad_rows: list[tuple[int, str]] = []
    for lineno, line in enumerate(raw_text.split("\n"), start=1):
        s = line.strip()
        m = re.match(r"^-\s*type:\s*(.+)$", s)
        if m:
            cur = {"type": m.group(1).strip(), "direction": None,
                   "wavelength_range": None, "coefficients": None,
                   "data_lines": [], "saw_data": False}
            entries.append(cur)
            in_data = False
            continue
        if s.lower().startswith("data: |"):
            in_data = True
            if cur is None:
                # Bare-block form (a top-level ``DATA: |`` with no
                # ``- type:`` entry structure) → an implicit iso
                # tabulated-data entry.
                cur = {"type": "tabulated nk", "direction": None,
                       "wavelength_range": None, "coefficients": None,
                       "data_lines": [], "saw_data": True}
                entries.append(cur)
            else:
                cur["saw_data"] = True
            continue
        if in_data:
            if not s or s.startswith("#"):
                continue
            # 1) Numeric row: the character class contains '-', so "-0.5 2 0" (negative first column, no space) is data
            if re.match(r"^[\d.eE+\-]+\s+[\d.eE+\-]+\s+[\d.eE+\-]+", s):
                if cur is not None:
                    cur["data_lines"].append(s)
            # 2) The next entry (starts with a dash) ends this block — checked before the bad-row branch
            elif re.match(r"^-\s|^-$", s):
                in_data = False
            # 3) Numeric-shaped but malformed (missing columns / stray tokens): skip + warning, without truncating
            elif re.match(r"^[\d.eE+\-]", s):
                bad_rows.append((lineno, s))
            # 4) The next YAML key = end of the DATA block
            elif re.match(r"^[A-Za-z_][\w\-]*\s*:", s):
                in_data = False
            continue
        if cur is None:
            continue
        m = re.match(r"^direction:\s*(\S+)$", s)
        if m:
            cur["direction"] = m.group(1)
            continue
        m = re.match(r"^wavelength_range:\s*(.+)$", s)
        if m:
            parts = m.group(1).split()
            if len(parts) >= 2:
                try:
                    cur["wavelength_range"] = (float(parts[0]),
                                               float(parts[1]))
                except ValueError:
                    pass            # a bad range stays None → formula uses the default window
            continue
        m = re.match(r"^coefficients:\s*(.+)$", s)
        if m:
            cur["coefficients"] = m.group(1).strip()
            continue
    if bad_rows:
        of = f" of {src}" if src else ""
        warnings.warn(
            f"materials: skipped {len(bad_rows)} malformed data line(s) in "
            f"a DATA block{of} (first: line {bad_rows[0][0]}: "
            f"{bad_rows[0][1]!r}) — check the .yml for missing columns or "
            f"stray characters")
    return entries


def _parse_entry(entry: dict, src: str):
    """One data entry → (wl_um, n, k); unparseable → ValueError with
    entry context.

    tabulated (``data_lines``): a lower bound of ≥5 valid rows;
    non-finite rows dropped + warning (``1e999`` overflowing to inf
    would poison the fit). formula (formula 1 Sellmeier / formula 4
    extended): default window 0.2–2.0µm, 500 sample points.
    """
    if entry["saw_data"]:
        if len(entry["data_lines"]) < 5:
            raise ValueError(
                f"materials file exists but could not be parsed: {src} "
                f"(tabulated block, direction {entry['direction']!r}, "
                f"has <5 valid data rows)")
        wl, nv, kv, overflow = [], [], [], []
        for dl in entry["data_lines"]:
            parts = dl.split()               # the line regex guarantees ≥3 tokens
            try:
                vals = (float(parts[0]), float(parts[1]), float(parts[2]))
            except ValueError:
                continue
            if not all(math.isfinite(v) for v in vals):
                overflow.append(dl)
                continue
            wl.append(vals[0])
            nv.append(vals[1])
            kv.append(vals[2])
        if overflow:
            of = f" of {src}" if src else ""
            warnings.warn(
                f"materials: dropped {len(overflow)} non-finite data "
                f"row(s){of} (first: {overflow[0]!r}) — values like 1e999 "
                f"overflow to inf")
        if len(wl) < 5:
            raise ValueError(
                f"materials file {src}: tabulated block (direction "
                f"{entry['direction']!r}) has <5 valid data rows")
        return (np.array(wl), np.array(nv), np.array(kv))

    coeffs_str = entry["coefficients"]
    if not coeffs_str:
        raise ValueError(
            f"materials file {src}: entry (direction "
            f"{entry['direction']!r}) has neither tabulated data nor "
            f"coefficients")
    try:
        coeffs = [float(v) for v in coeffs_str.split()]
    except ValueError as exc:
        raise ValueError(
            f"materials file {src}: bad coefficients string "
            f"{coeffs_str!r} ({exc})") from exc
    wl_range = entry["wavelength_range"] or (0.2, 2.0)
    wl_um = np.linspace(wl_range[0], wl_range[1], 500)
    ftype = entry["type"] or ""
    if ftype.startswith("formula 1"):
        # Sellmeier: n² = A + Σ Bᵢ·λ²/(λ² − Cᵢ²); with coefficients "0 B₁ C₁ …", A=1.
        if len(coeffs) >= 3:
            a = 1.0 if coeffs[0] == 0 else coeffs[0]
            pairs = coeffs[1:]
            n2 = np.full_like(wl_um, a, dtype=float)
            for i in range(0, len(pairs) - 1, 2):
                b, c = pairs[i], pairs[i + 1]
                n2 += b * wl_um**2 / (wl_um**2 - c**2)
            n = np.sqrt(np.maximum(n2, 1.0))
            return (wl_um, n, np.zeros_like(n))
    elif ftype.startswith("formula 4"):
        # Extended Sellmeier (ZnO type): n² = A + B₁·λ^p₁/(λ² − C₁^p₂)
        # + Σ P·λ^p (coeffs[8]==1 marks the polynomial correction pairs).
        if len(coeffs) >= 5:
            a, b1, p1, c1, p2 = coeffs[:5]
            n2 = np.full_like(wl_um, a, dtype=float)
            n2 += b1 * wl_um**p1 / (wl_um**2 - c1**p2)
            if len(coeffs) >= 11 and int(coeffs[8]) == 1:
                for j in range(9, len(coeffs) - 1, 2):
                    n2 += coeffs[j] * wl_um**coeffs[j + 1]
            n = np.sqrt(np.maximum(n2, 1.0))
            return (wl_um, n, np.zeros_like(n))
    raise ValueError(
        f"materials file {src}: entry (direction {entry['direction']!r}) "
        f"has unknown or inapplicable type {ftype!r}")


def _load_raw(name: str):
    """Library name → ``{component: (wl_um, n, k)}``; **missing file =
    None, broken file = ValueError** (the two failures are reported
    separately). An iso material = a single key ``''``; compound
    material keys = direction (o/e/x/y/z)."""
    _check_material_name(name)
    path = _DATA_DIR / (name + ".yml")
    if not path.exists():
        return None
    raw_text = path.read_text(encoding="utf-8")
    entries = _scan_entries(raw_text, src=str(path))
    comps: dict = {}
    for e in entries:
        arr = _parse_entry(e, str(path))
        key = e["direction"] or ""
        if key in comps:
            raise ValueError(
                f"materials file {path}: duplicate component "
                f"{key or '(iso)'}")
        comps[key] = arr
    if not comps:
        raise ValueError(
            f"materials file exists but could not be parsed: {path} "
            f"(no data entries with a known formula or tabulated block)")
    return comps


def _to_m(arr):
    """(wl_um, n, k) → (wl_m, n, k) (the µm→m conversion lives here, at a single address)."""
    wl_um, n, k = arr
    return (np.asarray(wl_um, dtype=float) * 1e-6,
            np.asarray(n, dtype=float), np.asarray(k, dtype=float))


def load_nk(name, component=None):
    """Library name → raw n-k data ``(wl_m, n, k)`` (**direct pick when
    unambiguous, otherwise error listing options**).

    An iso material (single data block) returns the triplet directly
    and ``component`` must be None; a compound material (multi-block)
    **requires naming the component explicitly**, e.g.
    ``load_nk('ZnO', 'e')`` — when omitted, the error lists the
    options instead of guessing a default.

    Raises
    ------
    ValueError
        The name is not a bare material name; no local hit (pointing
        to :func:`list_library_materials`); the file exists but does
        not parse (with the path); naming a component on an iso
        material / not naming one on a compound material, or naming
        a wrong one (options listed). Messages are always English.
    """
    comps = _load_raw(name)
    if comps is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"f.list_library_materials() lists all local library names")
    if set(comps) == {""}:
        if component is not None:
            raise ValueError(
                f"library {name!r} is isotropic (single data set) — no "
                f"component to select")
        return _to_m(comps[""])
    if component is None:
        raise ValueError(
            f"library {name!r} has multiple components — pass one of: "
            + ", ".join(repr(c) for c in sorted(comps)))
    if component not in comps:
        raise ValueError(
            f"library {name!r} has no component {component!r} — "
            f"available: " + ", ".join(repr(c) for c in sorted(comps)))
    return _to_m(comps[component])


# Tensor assembly (general assembly). rank-1 semantics = the native
# form of the kernel ADE VectorPole (``D += β·P``, ``P ∝ α·F``): a
# diagonal mount is α=e_i (beta omitted = the line-format default
# duplicates α); an off-diagonal is a **mirrored mount** (α=e_i,β=e_j
# paired with α=e_j,β=e_i) — a single α≠β dyad is asymmetric, and
# only the pairwise sum is a symmetric tensor element, so reciprocal
# media (symmetric ε) are guaranteed by construction.
_ELEM_AXES = {"xx": (0, 0), "yy": (1, 1), "zz": (2, 2),
              "xy": (0, 1), "xz": (0, 2), "yz": (1, 2)}
_EYE = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_DIAG = ("xx", "yy", "zz")
_OFFDIAG = ("xy", "xz", "yz")


def assemble_nk(fits, name=None):
    """Per-component ``fit_nk`` results → **a single** lorentz material
    node (the only node factory).

    Parameters
    ----------
    fits : dict
        Keys = the **six upper-triangle tensor-element names**
        (``xx`` / ``yy`` / ``zz`` / ``xy`` / ``xz`` / ``yz``); values =
        ``fit_nk`` result dicts. All three diagonals are required —
        isotropic media go through here too (pass the same fit on the
        three diagonals). Each off-diagonal needs only one
        upper-triangle name; the mirrored mount (α/β in symmetric
        pairs) keeps the tensor symmetric by construction.
    name : str | None
        Node name (defaults to ``"assembled"``).

    Returns
    -------
    dict
        A lorentz node (diagonal-only → ``eps_inf`` as a diagonal
        triplet; with off-diagonals → a full 3×3 tensor); already
        validated by ``LorentzMatSpec``.

    Raises
    ------
    ValueError
        ``fits`` is not a non-empty dict; unknown key names (the
        lower triangle yx/zx/zy is not accepted — mirroring for
        symmetric materials is automatic); a missing diagonal; a
        non-assemblable component (not converged or eps_inf ≤ 0,
        named explicitly in the message).
    """
    if not isinstance(fits, dict) or not fits:
        raise ValueError(
            "assemble_nk(fits) requires a non-empty dict of fit_nk "
            "results keyed by tensor element (xx/yy/zz/xy/xz/yz)")
    for key in fits:
        if key not in _ELEM_AXES:
            raise ValueError(
                f"assemble_nk: unknown element key {key!r} — use "
                f"upper-triangle names xx/yy/zz/xy/xz/yz (off-diagonals "
                f"are mirrored automatically for symmetric materials)")
    for need in _DIAG:
        if need not in fits:
            raise ValueError(
                f"assemble_nk: diagonal element {need!r} is required — "
                f"got {sorted(fits)}")
    for key, r in fits.items():
        if not isinstance(r, dict):
            raise ValueError(
                f"assemble_nk: component {key!r} must be a fit_nk "
                f"result dict, got {type(r).__name__}")
        if not r.get("converged") or not (r.get("eps_inf", 0.0) > 0):
            raise ValueError(
                f"assemble_nk: component {key!r} is not assemblable "
                f"(converged={r.get('converged')}, "
                f"eps_inf={r.get('eps_inf')!r}) — refit that component "
                f"before assembling")

    eps_inf = [fits[c]["eps_inf"] for c in _DIAG]
    poles = []
    for c in _DIAG:                      # diagonal mount: α=e_i, beta defaults to α
        for p in fits[c]["poles"]:
            q = dict(p)
            q["alpha"] = list(_EYE[_ELEM_AXES[c][0]])
            poles.append(q)
    off = [c for c in _OFFDIAG if c in fits]
    for c in off:                        # off-diagonal: mirrored mount in pairs (symmetric)
        i, j = _ELEM_AXES[c]
        for p in fits[c]["poles"]:
            fwd = dict(p)
            fwd["alpha"] = list(_EYE[i])
            fwd["beta"] = list(_EYE[j])
            bwd = dict(p)
            bwd["alpha"] = list(_EYE[j])
            bwd["beta"] = list(_EYE[i])
            poles.append(fwd)
            poles.append(bwd)
    if off:
        m = [[eps_inf[0], 0.0, 0.0], [0.0, eps_inf[1], 0.0],
             [0.0, 0.0, eps_inf[2]]]
        for c in off:
            i, j = _ELEM_AXES[c]
            m[i][j] = m[j][i] = fits[c]["eps_inf"]
        eps_inf = m
    node = {"type": "lorentz", "name": name or "assembled",
            "eps_inf": eps_inf, "poles": poles}
    _wire.LorentzMatSpec.model_validate(node)   # explicit verb: failed validation raises immediately
    return node


# ═══════════════════════════════════════════════════════════════════════════
# Public entry points
# ═══════════════════════════════════════════════════════════════════════════

def fit_nk(wl, n, k, fit_range=(400e-9, 800e-9), tolerance=0.05,
           max_poles=8, n_drude=0):
    """Fit **one scalar n-k component** to a Drude-Lorentz pole model.

    One component is fitted per call and **only numbers come out** —
    a six-key dict, never a material node. Build nodes with
    :func:`assemble_nk` (for an isotropic medium, pass the same fit
    on the three diagonals).

    Parameters
    ----------
    wl, n, k : array_like
        Single-component data, SI [m, dimensionless, dimensionless].
    fit_range : tuple[float, float]
        Fit window [m] (lo < hi); the data is masked to the window
        first, and fewer than 3 points inside raises.
    tolerance : float
        Absolute bound on ``max(rmse_n, rmse_k)``. The fitter starts with
        **no Lorentz pole** (a dispersionless medium needs none) and adds
        one at a time until the bound is met or ``max_poles`` is reached.
        Default 0.05 — tight enough that a metal in its window passes.
    max_poles : int
        Lorentz pole budget (default 8). A metal needs 2, a band edge
        needs 6–8; hitting the budget short of ``tolerance`` lands in
        ``warnings``, never silent.
    n_drude : int
        Fixed Drude count (default 0) — a Drude term is a **model choice**
        (free carriers), not something a tolerance can invent.

    Returns
    -------
    dict
        ``{converged, eps_inf, n_poles, poles, quality, warnings}`` —
        always exactly this shape. ``poles`` are wire-shaped dicts
        (``assemble_nk`` consumes them directly); ``quality`` holds
        five scalars (``n_points`` / ``rmse_n`` / ``rmse_k`` /
        ``max_abs_err_n`` / ``max_abs_err_k`` / ``passive`` / ``coverage``)
        — one shape, always. A non-converged fit
        still returns — ``converged=False`` plus tuning advice in
        ``warnings``.

    Raises
    ------
    ValueError
        fit_range reversed; unequal column lengths; fewer than 3
        points inside the window; non-finite n/k inside the window
        (reported with a data-cleaning prescription, distinct from
        the tuning advice for hitting the iteration cap).
    ImportError
        scipy not installed (imported lazily at the first fit;
        reported explicitly, not swallowed).
    """
    lo, hi = fit_range_bounds(fit_range)
    wl = np.asarray(wl, dtype=float)
    n = np.asarray(n, dtype=float)
    k = np.asarray(k, dtype=float)
    if not (wl.size == n.size == k.size):
        raise ValueError(
            f"columns must have equal lengths, got wl={wl.size}, "
            f"n={n.size}, k={k.size} — boolean masking of mismatched "
            f"columns would crash with a bare IndexError far from the "
            f"cause")
    mask = (wl >= lo) & (wl <= hi)
    n_in = int(mask.sum())
    if n_in < 3:
        raise ValueError(
            f"Only {n_in} data points in fit range "
            f"{lo * 1e9:.0f}-{hi * 1e9:.0f} nm — widen fit_range or "
            f"supply more data inside the window")
    # Non-finite inside the window = an input-data problem: raise with
    # a **data-cleaning prescription** before fitting, distinct from
    # the tuning advice given when the iteration cap is hit (the two
    # failure classes get different prescriptions). Only the window
    # is checked: dirty rows outside the window never enter the fit —
    # rejecting them would be paternalistic.
    for col_label, col in (("n", n), ("k", k)):
        bad = mask & ~np.isfinite(col)
        if bad.any():
            rows = np.flatnonzero(bad)
            raise ValueError(
                f"column {col_label!r} has {len(rows)} non-finite "
                f"value(s) inside fit range {lo * 1e9:.0f}-"
                f"{hi * 1e9:.0f} nm (first at data row {int(rows[0])} "
                f"of {wl.size}) — clean the data (drop or fix those "
                f"rows; JSON null becomes NaN here) or use library "
                f"data via f.load_nk; non-finite values cannot be "
                f"fitted")
    fit = fit_adaptive(wl[mask], n[mask], k[mask], tolerance=tolerance,
                       max_poles=max_poles, n_drude=n_drude)
    omega_win = 2.0 * np.pi * C0_SI / wl[mask]
    return scalar_result(fit, wl[mask], n[mask], k[mask],
                         passive=passivity_report(fit.eps_inf, fit.poles,
                                                  omega_win),
                         coverage=coverage_fraction(lo, hi, wl))


# ═══════════════════════════════════════════════════════════════════════════
# Convenience layer (one call, zero knobs, does not touch the model)
# ═══════════════════════════════════════════════════════════════════════════

#: Physical component → axis mapping. Uniaxial-crystal convention:
#: the optic axis lies along z — the o wave is perpendicular to the
#: optic axis (xx=yy), the e wave along it (zz); biaxial crystals
#: use x/y/z.
_COMPONENT_AXES = {"o": ("xx", "yy"), "e": ("zz",),
                   "x": ("xx",), "y": ("yy",), "z": ("zz",)}


def _fit_convenience(wl, n, k):
    """Convenience-layer window rule: fit the **default window** first
    (400–800nm ∩ data domain — metals / ordinary dielectrics carry enough
    information there and fit best); if the tolerance is **not met**,
    retry once over the **whole data domain** and keep the better of the
    two (for weakly dispersive media with sparse in-window points, e.g.
    ZnO-e with only 10 points inside the window: the full range carries
    the complete dispersion and meets the tolerance, while in-window
    accuracy is still bounded by the same target). Criterion = the
    tolerance, not a convergence flag — one rule, same as the primitive
    layer."""
    lo = max(400e-9, float(np.min(wl)))
    hi = min(800e-9, float(np.max(wl)))
    if lo < hi:
        fit = fit_nk(wl, n, k, fit_range=(lo, hi))
        if "tolerance not met" not in " ".join(fit["warnings"]):
            return fit
    return fit_nk(wl, n, k,
                  fit_range=(float(np.min(wl)), float(np.max(wl))))


def mat_from_nk(name):
    """Library name → a material node in **one call** (read library →
    per-component, per-axis ``fit_nk`` → ``assemble_nk``).

    A zero-knob convenience layer: the default fit parameters
    (400–800nm / tolerance 0.05 / ≤8 poles) are not adjustable and the
    per-component quality is not surfaced — for control use the primitive
    layer (``load_nk`` / ``fit_nk`` / ``assemble_nk``). **Does not touch the model** (adding
    to a model is one line, ``f.mat.add_lorentz(node)``); the node
    name defaults to the library name. An iso material is fitted
    once and mounted on all three axes; compound materials are
    split per axis via
    ``_COMPONENT_AXES`` (o→xx,yy, e→zz), and incomplete components →
    an error listing what the library contains.

    Raises
    ------
    ValueError
        Library name not found (pointing to
        :func:`list_library_materials`); an unknown component
        direction; incomplete components (missing axes); any
        component's fit not assemblable (not converged /
        eps_inf≤0 — assemble names it explicitly). Messages are
        always English.
    """
    comps = _load_raw(name)
    if comps is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"f.list_library_materials() lists all local library names")
    if set(comps) == {""}:                       # iso: fit once, mount on all three axes
        wl, n, k = _to_m(comps[""])
        fit = _fit_convenience(wl, n, k)
        return assemble_nk({"xx": fit, "yy": fit, "zz": fit}, name=name)
    by_axis: dict = {}
    for comp in sorted(comps):
        if comp not in _COMPONENT_AXES:
            raise ValueError(
                f"library {name!r}: unknown component direction {comp!r} "
                f"(known: o/e/x/y/z)")
        wl, n, k = _to_m(comps[comp])
        fit = _fit_convenience(wl, n, k)
        for ax in _COMPONENT_AXES[comp]:
            by_axis[ax] = fit
    missing = sorted({"xx", "yy", "zz"} - set(by_axis))
    if missing:
        raise ValueError(
            f"library {name!r}: components {sorted(comps)} do not cover "
            f"axes {missing} — the library entry is incomplete")
    return assemble_nk(by_axis, name=name)
