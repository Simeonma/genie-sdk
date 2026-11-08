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
                   coverage_fraction, evaluate_dispersion, fit_adaptive,
                   fit_error, fit_range_bounds, passivity_report,
                   pole_from_node, pole_to_node, scalar_result)

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
        non-assemblable component (not converged, named explicitly in
        the message).  ``eps_inf`` may be any real value (negative
        constants are valid rational models).
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
        if not isinstance(r, NKFit):
            raise ValueError(
                f"assemble_nk: component {key!r} must be an NKFit from "
                f"f.fit_nk(...), got {type(r).__name__}")

    eps_inf = [fits[c].model["eps_inf"] for c in _DIAG]
    poles = []
    for c in _DIAG:                      # diagonal mount: α=e_i, beta defaults to α
        for p in fits[c].model["poles"]:
            q = dict(p)
            q["alpha"] = list(_EYE[_ELEM_AXES[c][0]])
            poles.append(q)
    off = [c for c in _OFFDIAG if c in fits]
    for c in off:                        # off-diagonal: mirrored mount in pairs (symmetric)
        i, j = _ELEM_AXES[c]
        for p in fits[c].model["poles"]:
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
            m[i][j] = m[j][i] = fits[c].model["eps_inf"]
        eps_inf = m
    node = {"type": "lorentz", "name": name or "assembled",
            "eps_inf": eps_inf, "poles": poles}
    _wire.LorentzMatSpec.model_validate(node)   # explicit verb: failed validation raises immediately
    return node


# ═══════════════════════════════════════════════════════════════════════════
# Public entry points
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class NKFit:
    """One component's fit result — **what the user gets**.

    Read ``quality`` (relative error, passivity, coverage), ``summary()``
    and ``curve()``. ``model`` is the opaque pole payload that
    ``assemble_nk`` and the document consume; its coefficients are the wire
    form, deliberately not a human-facing result (Q14: 用户不读系数).
    """

    band: tuple
    n_poles: int
    quality: dict
    warnings: tuple
    model: dict

    def summary(self) -> str:
        """One line: band · poles · error · passive · coverage."""
        q = self.quality
        state = "passive" if q.get("passive") else "NOT passive"
        return (f"{self.band[0] * 1e9:.0f}-{self.band[1] * 1e9:.0f}nm | "
                f"{self.n_poles} pole(s) | "
                f"err {100.0 * q['rel_err_n']:.2f}% (n) / "
                f"{100.0 * q['rel_err_k']:.2f}% (k) | {state} | "
                f"coverage {q['coverage']:.0%}")

    def curve(self, wl=None):
        """Sampled curves for a plot: ``{wl, n, k, n_fit, k_fit}``.

        Default sample = the data inside the band (what the fit saw).
        """
        wl_in, n_in, k_in = self.model["data"]
        if wl is None:
            wl = wl_in
        omega = 2.0 * np.pi * C0_SI / np.asarray(wl, dtype=float)
        d = evaluate_dispersion(
            self.model["eps_inf"],
            [pole_from_node(p) for p in self.model["poles"]], omega)
        return {"wl": wl, "n": n_in, "k": k_in,
                "n_fit": d.n, "k_fit": d.k}


def fit_nk(wl, n, k, band=None, tolerance=0.1, max_poles=10, imaginary_weight=1.0, passive=None):
    """Fit **one scalar n-k component** to a rational dispersion model.

    The yardstick is **Lumerical's**: ``tolerance`` is the ``PoleFit``
    tolerance on the **weighted RMS of the eps residual** ("Tolerance for RMS
    error in data fit").  The pole budget starts at **1** (the simplest
    model) and is raised by the caller when the residual is too large — the
    order search grows up to ``max_poles`` and stops as soon as the tolerance
    is met.  The model family is not a user choice: the fitter always fits
    the general rational form; Lorentz/Drude survive only as wire-format
    shorthands.

    Parameters
    ----------
    wl, n, k : array_like
        Single-component data, SI [m, dimensionless, dimensionless].
    band : tuple[float, float] or None
        Fit window [m] (lo < hi). **Default = the whole data range** — the
        fit never silently truncates. Fewer than 3 points inside raises.
    tolerance : float
        Weighted-RMS tolerance for the order search (Lumerical default 0.1).
        The order grows until it is met or ``max_poles`` is reached; an
        unmet tolerance lands in ``warnings`` (never silent).
    max_poles : int
        **Ceiling** for the order search (one pole = one second-order section
        = one conjugate pair). The search starts at order 0 (a constant
        model) and grows only while the tolerance is unmet, so this is an
        upper bound, not a target; default 10 (the 12-point metals need 9-10
        pairs, and the sample count bounds it anyway).
    imaginary_weight : float
        Weight on the **imaginary** residual (Lumerical's "imaginary weight",
        default 1.0). When k is tiny the imaginary part of eps is tiny too;
        raising this makes its fit accuracy count proportionally more.
    passive : bool or None
        ``None`` (default) = **report only**: passivity lands in
        ``quality['passive']`` and a warning fires when it is False.
        ``True`` = enforce Lumerical's "make fit passive" penalty (a model
        with gain > 1e-14 gets its error multiplied by 1e6 so the order
        search rejects it).  Off by default because a narrow band often has a
        slightly non-passive best fit and the penalty would force a much
        worse one (TiO2 over 120nm-125um: 0.3% free vs 62% penalised).

    Returns
    -------
    NKFit
        ``band`` / ``n_poles`` / ``quality`` / ``warnings`` / ``model``,
        plus ``summary()`` and ``curve()``. ``quality['rms']`` is the
        acceptance metric; ``rel_err_n`` / ``rel_err_k`` are diagnostics
        only (at k≈0 their denominator makes them meaningless).

    Raises
    ------
    ValueError
        band reversed; unequal column lengths; fewer than 3 samples inside;
        non-finite n/k inside the band (with a data-cleaning prescription).
    """
    make_passive = False if passive is None else bool(passive)
    wl = np.asarray(wl, dtype=float)
    n = np.asarray(n, dtype=float)
    k = np.asarray(k, dtype=float)
    if not (wl.size == n.size == k.size):
        raise ValueError(
            f"columns must have equal lengths, got wl={wl.size}, "
            f"n={n.size}, k={k.size} — boolean masking of mismatched "
            f"columns would crash with a bare IndexError far from the "
            f"cause")
    if band is None:
        lo = float(np.min(wl))
        hi = float(np.max(wl))
    else:
        lo, hi = fit_range_bounds(band)
    if not lo < hi:
        raise ValueError(
            f"band must satisfy lo < hi [m]; got ({lo!r}, {hi!r})")
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
    if n_in < 3:                                  # one pole pair needs (n-1)//2 >= 1
        raise ValueError(
            f"Only {n_in} data points in band "
            f"{lo * 1e9:.0f}-{hi * 1e9:.0f} nm — the one-pole minimum needs "
            f"3; widen band or supply more data inside it")
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
                       max_poles=max_poles, w_im=imaginary_weight,
                       make_passive=make_passive)
    omega_win = 2.0 * np.pi * C0_SI / wl[mask]
    res = scalar_result(fit, wl[mask], n[mask], k[mask],
                        passive=passivity_report(fit.eps_inf, fit.poles,
                                                 omega_win),
                        coverage=coverage_fraction(lo, hi, wl))
    return NKFit(band=(lo, hi), n_poles=res["n_poles"],
                 quality=res["quality"], warnings=tuple(res["warnings"]),
                 model={"eps_inf": res["eps_inf"], "poles": res["poles"],
                        "data": (wl[mask], n[mask], k[mask])})


# ═══════════════════════════════════════════════════════════════════════════
# Convenience layer (one call, zero knobs, does not touch the model)
# ═══════════════════════════════════════════════════════════════════════════

#: Physical component → axis mapping. Uniaxial-crystal convention:
#: the optic axis lies along z — the o wave is perpendicular to the
#: optic axis (xx=yy), the e wave along it (zz); biaxial crystals
#: use x/y/z.
_COMPONENT_AXES = {"o": ("xx", "yy"), "e": ("zz",),
                   "x": ("xx",), "y": ("yy",), "z": ("zz",)}


def _fit_convenience(wl, n, k, band=None, tolerance=0.1, max_poles=10, imaginary_weight=1.0):
    """Convenience-layer rule: fit the window the **caller** names at the
    default tolerance and keep the result even if the tolerance is missed —
    the warnings say so, and ``mat_from_nk`` surfaces them.

    ``band=None`` means "the whole data range" *at this layer*; the worker
    verb passes the **simulation source band** (the Lumerical rule: fit
    where you will simulate), falling back to the data range when the
    source band holds too few samples to constrain a fit.
    """
    return fit_nk(wl, n, k, band=band, tolerance=tolerance,
                  max_poles=max_poles, imaginary_weight=imaginary_weight)


def _report(line: str) -> None:
    """One-line human report — **only on an interactive terminal**.

    ``mat_from_nk`` is also a worker verb, and the worker's stdout *is* the
    IPC channel: an unconditional print there corrupts the stream (the
    journey test caught exactly that). A terminal user still gets the line;
    a script / worker / pipe stays silent and reads ``NKFit.summary()``
    itself.
    """
    import sys
    if sys.stdout.isatty():
        print(line)


def mat_from_nk(name, band=None, tolerance=0.1, max_poles=10, imaginary_weight=1.0):
    """Library name → a material node in **one call** (read library →
    per-component, per-axis ``fit_nk`` → ``assemble_nk``).

    A near-zero-knob convenience layer: ``band`` (fit window [m]),
    ``tolerance`` (weighted-RMS, Lumerical default 0.1) and ``max_poles``
    are forwarded, nothing else is. The
    per-component quality is not surfaced — for control use the primitive
    layer (``load_nk`` / ``fit_nk`` / ``assemble_nk``). **Does not touch the model** (adding
    to a model is one line, ``f.mat.add_lorentz(node)``); the node
    name defaults to the library name. An iso material is fitted
    once and mounted on all three axes; compound materials are
    split per axis via
    ``_COMPONENT_AXES`` (o→xx,yy, e→zz), and incomplete components →
    an error listing what the library contains.

    ``band=None`` = the whole data range. The **worker verb** passes the
    simulation source band here (fit where you will simulate), so the
    one-liner follows the source by default while this core function stays
    independent of the model.

    Raises
    ------
    ValueError
        Library name not found (pointing to
        :func:`list_library_materials`); an unknown component
        direction; incomplete components (missing axes); any
        component's fit not assemblable (not converged —
        assemble names it explicitly). Messages are always English.
    """
    comps = _load_raw(name)
    if comps is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"f.list_library_materials() lists all local library names")
    if set(comps) == {""}:                       # iso: fit once, mount on all three axes
        wl, n, k = _to_m(comps[""])
        fit = _fit_convenience(wl, n, k, band=band, tolerance=tolerance,
                               max_poles=max_poles,
                               imaginary_weight=imaginary_weight)
        _report(f"{name} (iso)  {fit.summary()}")
        return assemble_nk({"xx": fit, "yy": fit, "zz": fit}, name=name)
    by_axis: dict = {}
    for comp in sorted(comps):
        if comp not in _COMPONENT_AXES:
            raise ValueError(
                f"library {name!r}: unknown component direction {comp!r} "
                f"(known: o/e/x/y/z)")
        wl, n, k = _to_m(comps[comp])
        fit = _fit_convenience(wl, n, k, band=band, tolerance=tolerance,
                               max_poles=max_poles,
                               imaginary_weight=imaginary_weight)
        _report(f"{name} ({comp})  {fit.summary()}")
        for ax in _COMPONENT_AXES[comp]:
            by_axis[ax] = fit
    missing = sorted({"xx", "yy", "zz"} - set(by_axis))
    if missing:
        raise ValueError(
            f"library {name!r}: components {sorted(comps)} do not cover "
            f"axes {missing} — the library entry is incomplete")
    return assemble_nk(by_axis, name=name)
