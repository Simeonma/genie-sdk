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

__all__ = ["fit_nk", "assemble_nk", "mat_from_nk", "load_nk",
           "list_library_materials"]

#: Speed of light [m/s] (same value as the kernel's C0_PHYS).
_C0_SI = 299792458.0

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


# ═══════════════════════════════════════════════════════════════════════════
# Drude-Lorentz fitting (log10 parameter space)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _DispersionResult:
    """Result of evaluating eps(ω): ω [rad/s]; n/k = real/imaginary parts of sqrt(eps)."""
    omega: np.ndarray
    n: np.ndarray
    k: np.ndarray


def _evaluate_dispersion(eps_inf, poles, omega):
    """eps(ω) = eps_inf + Σ ωp²/(ω0² − ω² − j·γ·ω) (pure numpy).
    ``poles`` = a list of (ω0, γ, ωp²) SI triplets (unified pole
    convention)."""
    eps = np.full_like(omega, float(eps_inf), dtype=complex)
    w2 = omega * omega
    for w0, gm, omega_p_sq in poles:
        eps += omega_p_sq / (w0**2 - w2 - 1j * gm * omega)
    sq = np.sqrt(eps)
    return _DispersionResult(omega=omega, n=sq.real, k=sq.imag)


@dataclass
class _ComponentFit:
    """Single-component fit: eps_inf, poles as (ω0, γ, ωp²) SI triplets, and a convergence flag."""
    eps_inf: float
    poles: list
    converged: bool


def _params_to_poles(params, n_drude, n_lorentz):
    """log10-space fit parameters → (ω0, γ, ωp²) SI triplets.

    The single source of the parameter layout, shared by the residual
    and the result assembly — keep exactly one copy."""
    poles = []
    idx = 0
    for _ in range(n_drude):
        wp = 10.0 ** float(params[idx]); idx += 1
        gd = 10.0 ** float(params[idx]); idx += 1
        poles.append((0.0, gd, wp * wp))
    for _ in range(n_lorentz):
        de = 10.0 ** float(params[idx]); idx += 1
        w0 = 10.0 ** float(params[idx]); idx += 1
        g = 10.0 ** float(params[idx]); idx += 1
        poles.append((w0, g, de * w0 * w0))     # ωp² = Δε·ω0²
    return poles


def _dl_residual(omega, eps_inf, n_drude, n_lorentz, *params):
    """curve_fit residual (n/k real and imaginary parts stacked)."""
    r = _evaluate_dispersion(eps_inf, _params_to_poles(params, n_drude,
                                                       n_lorentz), omega)
    return np.concatenate([r.n, r.k])


def _fit_drude_lorentz(wavelengths, n, k, n_drude=0, n_lorentz=2,
                       maxfev=5000):
    """Single-component n-k data (already windowed) → a Drude-Lorentz
    fit.

    Strategy: log10-space parameters; of the frequency search box
    only the **lower bound** adapts to the data span (sub-16-THz
    resonances become fittable) while the upper bound stays pinned
    (a higher damping cap would collapse the Au 1-Drude fit into a
    degenerate pole that the ADE γ·dt guard rejects); eps_inf
    high-frequency
    estimate (the first 1/8 at the shortest wavelengths via argsort);
    Drude low-frequency tail estimate (ω = 2πc/λ **decreases** for
    ascending λ — the low frequencies sit in the last quarter);
    Lorentz peak scan with suppression (zero out each found peak,
    then look for the next); p0 fully clamped into the box (curve_fit
    rejects out-of-bounds initial guesses); on failure, fall back to
    p0 without raising (converged=False). scipy lazy import: if
    missing, an ImportError is reported explicitly, no silent
    degradation.
    """
    try:
        from scipy.optimize import curve_fit    # lazy: importing genie needs no scipy
    except ImportError as exc:
        raise ImportError(
            "fitting requires scipy (curve_fit) — install it with: "
            "pip install scipy") from exc

    wavelengths = np.asarray(wavelengths, dtype=float).ravel()
    n = np.asarray(n, dtype=float).ravel()
    k = np.asarray(k, dtype=float).ravel()
    if wavelengths.size == 0:
        raise ValueError("wavelengths must be non-empty")
    if not np.all(np.isfinite(wavelengths)) or np.any(wavelengths <= 0):
        raise ValueError(
            f"wavelengths must be finite and > 0 [m]; got "
            f"min={np.min(wavelengths):.4g}, max={np.max(wavelengths):.4g}")

    omega = 2 * np.pi * _C0_SI / wavelengths

    w_min = float(np.min(omega))
    freq_lo = min(1e10, w_min * 1e-2)
    freq_hi = 1e17
    gamma_hi = 1e16

    high_idx = max(len(omega) // 8, 1)
    # eps_inf is the high-frequency limit: take the first 1/8 at the
    # shortest wavelengths (argsort, independent of input order) and
    # estimate n²−k² — the static value obeys eps(0) >= eps_inf, and a
    # low-frequency estimate would push eps_inf up.
    order = np.argsort(wavelengths)
    hi_f_idx = order[:high_idx]
    eps_inf_0 = max(float(np.mean(n[hi_f_idx]**2 - k[hi_f_idx]**2)), 1.0)

    p0 = [eps_inf_0]
    # eps_inf box lower bound = 1.0 (a hard physical causality
    # constraint: causal media obey ε(∞) ≥ 1, with the vacuum limit
    # being 1 — a lower bound below 1 would admit unphysical
    # decompositions, e.g. dielectric fits degenerating with
    # eps_inf pinned at the bound).
    bounds_low = [1.0]
    bounds_high = [100.0]
    # curve_fit/least_squares rejects any out-of-bounds initial guess:
    # first widen the eps_inf box to include the initial guess (the
    # general clamping below then covers it uniformly).
    if p0[0] > bounds_high[0]:
        bounds_high[0] = max(bounds_high[0], p0[0] * 1.1)
    elif p0[0] < bounds_low[0]:
        bounds_low[0] = min(bounds_low[0], p0[0] * 0.9)

    if n_drude > 0:
        # Low-frequency tail: with ascending λ, ω decreases; the lowest frequency sits at argmax(λ), not in the first quarter.
        low_idx = int(np.argmax(wavelengths))
        w_low = omega[low_idx]
        eps_low = (n[low_idx] + 1j * k[low_idx]) ** 2
        wp_est = np.sqrt(max(-float(eps_low.real), 1.0)) * w_low
        p0 += [np.log10(wp_est), np.log10(1e14)]
        bounds_low += [np.log10(freq_lo), np.log10(min(1e11, freq_lo))]
        bounds_high += [np.log10(freq_hi), np.log10(gamma_hi)]

    eps_complex = (n + 1j * k) ** 2
    eps_imag = np.abs(eps_complex.imag)          # ufunc → a fresh array, safe to mute
    for _ in range(n_lorentz):
        peak_idx = np.argmax(eps_imag)
        w0_est = omega[peak_idx]
        de_est = max(np.abs(eps_complex.imag[peak_idx]) * 2, 0.1)
        g_est = w0_est * 0.1
        p0 += [np.log10(de_est), np.log10(w0_est), np.log10(g_est)]
        bounds_low += [np.log10(1e-3), np.log10(freq_lo),
                       np.log10(min(1e10, freq_lo))]
        bounds_high += [np.log10(1e4), np.log10(freq_hi), np.log10(gamma_hi)]
        # Suppress this peak so the next iteration finds a different one.
        half_width = max(int(len(omega) * 0.05), 3)
        lo = max(0, peak_idx - half_width)
        hi = min(len(eps_imag), peak_idx + half_width + 1)
        eps_imag[lo:hi] = 0.0

    ydata = np.concatenate([n, k])

    # Clamp everything: any out-of-bounds p0 gets rejected by
    # curve_fit (when the data resonance lies outside the default
    # optical band, log10-space initial guesses hit the same trap,
    # e.g. THz data).
    p0 = [min(max(v, lo), hi)
          for v, lo, hi in zip(p0, bounds_low, bounds_high)]

    try:
        popt, _ = curve_fit(
            lambda w, *p: _dl_residual(w, p[0], n_drude, n_lorentz, *p[1:]),
            omega, ydata, p0=p0,
            bounds=(bounds_low, bounds_high), maxfev=maxfev)
        fit_converged = True
    except (RuntimeError, ValueError):
        popt = np.array(p0)
        fit_converged = False

    eps_inf = float(popt[0])
    ade_poles = _params_to_poles(popt[1:], n_drude, n_lorentz)
    return _ComponentFit(eps_inf=eps_inf, poles=ade_poles,
                         converged=fit_converged)


# ═══════════════════════════════════════════════════════════════════════════
# Result assembly (pure)
# ═══════════════════════════════════════════════════════════════════════════

def _pole_to_node(w0, gamma, omega_p_sq):
    """An (ω0, γ, ωp²) SI triplet → a wire-shaped PoleSpec dict.

    Lorentz: ωp² = Δε·ω0² → delta_eps = ωp²/ω0²; Drude: ω0 == 0 (by
    the unified convention, ``_params_to_poles`` produces exactly
    0.0) → omega_p = √ωp².
    """
    if w0 == 0.0:
        return {"kind": "drude", "omega_p": float(omega_p_sq) ** 0.5,
                "gamma": float(gamma)}
    return {"kind": "lorentz", "omega_0": float(w0), "gamma": float(gamma),
            "delta_eps": float(omega_p_sq) / float(w0) ** 2}


def _fit_range_bounds(fit_range):
    """Unpack + validate ``fit_range`` (lo < hi) — a reversed window
    would later surface as the misleading 'Only 0 data points';
    block it at the cause and report the actual range."""
    lo, hi = fit_range
    if not lo < hi:
        raise ValueError(
            f"fit_range must satisfy lo < hi [m]; got ({lo!r}, {hi!r}) — "
            "a reversed range selects zero data points (the fit would "
            "otherwise fail later as 'Only 0 data points', far from the "
            "cause)")
    return lo, hi


def _scalar_result(fit, w, n_data, k_data):
    """Single-component result assembly (**numeric only** — a six-key
    dict, no material_node / name; the only material-node factory is
    :func:`assemble_nk`). The five quality scalars are computed on
    the **data points** (no tables, no curves)."""
    omega = 2.0 * np.pi * _C0_SI / w
    d = _evaluate_dispersion(fit.eps_inf, fit.poles, omega)
    n_fit = np.asarray(d.n)
    k_fit = np.asarray(d.k)

    warnings_out: list[str] = []
    if not fit.converged:
        warnings_out.append(
            "fit did not converge (curve_fit hit the iteration cap and "
            "fell back to the initial guess) — the poles are a best-effort "
            "attempt, not a trustworthy solution; consider narrowing "
            "fit_range, reducing the pole count, or raising maxfev")
    if not (fit.eps_inf > 0):
        warnings_out.append(
            f"eps_inf={fit.eps_inf!r} <= 0 — cannot be assembled into a "
            f"valid lorentz material (assemble_nk rejects this component)")

    poles = [_pole_to_node(*p) for p in fit.poles]
    quality = {
        "n_points": int(w.size),
        "rmse_n": float(np.sqrt(np.mean((n_fit - n_data) ** 2))),
        "rmse_k": float(np.sqrt(np.mean((k_fit - k_data) ** 2))),
        "max_abs_err_n": float(np.max(np.abs(n_fit - n_data))),
        "max_abs_err_k": float(np.max(np.abs(k_fit - k_data))),
    }
    return {
        "converged": bool(fit.converged),
        "eps_inf": float(fit.eps_inf),
        "n_poles": len(poles),
        "poles": poles,                      # SI angular frequencies rad/s (Δε dimensionless)
        "quality": quality,
        "warnings": warnings_out,
    }


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

def fit_nk(wl, n, k, fit_range=(400e-9, 800e-9), n_drude=0, n_lorentz=2,
           maxfev=5000):
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
    n_drude / n_lorentz : int
        Number of Drude / Lorentz poles (defaults 0 + 2).
    maxfev : int
        Maximum function evaluations for scipy ``curve_fit``.

    Returns
    -------
    dict
        ``{converged, eps_inf, n_poles, poles, quality, warnings}`` —
        always exactly this shape. ``poles`` are wire-shaped dicts
        (``assemble_nk`` consumes them directly); ``quality`` holds
        five scalars (``n_points`` / ``rmse_n`` / ``rmse_k`` /
        ``max_abs_err_n`` / ``max_abs_err_k``). A non-converged fit
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
    lo, hi = _fit_range_bounds(fit_range)
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
    fit = _fit_drude_lorentz(wl[mask], n[mask], k[mask],
                             n_drude=n_drude, n_lorentz=n_lorentz,
                             maxfev=maxfev)
    return _scalar_result(fit, wl[mask], n[mask], k[mask])


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
    """Convenience-layer window rule: try the **default window** first
    (400–800nm ∩ data domain — metals / ordinary dielectrics carry
    enough information inside this window and fit best there); only
    when curve_fit **does not converge** is a **single full-range
    retry on
    the whole data domain** used (for weakly dispersive media with
    sparse in-window points, e.g. ZnO-e tabulated data with only 10
    points in the window; the full range carries complete dispersion
    information and always converges, while in-window accuracy stays
    no worse than the full-range rmse bound). The criterion = the
    curve_fit convergence flag; no magic thresholds."""
    lo = max(400e-9, float(np.min(wl)))
    hi = min(800e-9, float(np.max(wl)))
    if lo < hi:
        fit = fit_nk(wl, n, k, fit_range=(lo, hi))
        if fit["converged"]:
            return fit
    return fit_nk(wl, n, k,
                  fit_range=(float(np.min(wl)), float(np.max(wl))))


def mat_from_nk(name):
    """Library name → a material node in **one call** (read library →
    per-component, per-axis ``fit_nk`` → ``assemble_nk``).

    A zero-knob convenience layer: the default fit parameters
    (400–800nm / 0+2 poles) are not adjustable and quality is not
    visible — for control, use the primitive layer (``load_nk`` /
    ``fit_nk`` / ``assemble_nk``). **Does not touch the model** (adding
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
