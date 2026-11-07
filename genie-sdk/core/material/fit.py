"""genie.core.material.fit — the fitting engine (pure numpy, lazy scipy).

One scalar n-k component → a Drude-Lorentz pole set: ``eps(ω) = eps_inf +
Σ ωp²/(ω0² − ω² − jγω)`` (``ω0 = 0`` = Drude). Everything here is numeric:
no model, no I/O, no material node (nodes are built by ``assemble_nk``).

Two modes:

- **fixed count** (``fit_drude_lorentz``): the historical path — the caller
  names the pole counts (default 0 + 2);
- **adaptive** (``fit_adaptive``): a target ``tolerance`` drives the pole
  count up to ``max_poles`` (Q13 follow-up: the fitter was the missing
  product layer — a band edge needs more poles than a metal, and the user
  should not have to guess how many).

``passivity_report`` / ``coverage_fraction`` are the two checks Lumerical
exposes as "make fit passive" and the fit-range warning; both are advisory
and land in ``quality`` / ``warnings`` only in adaptive mode.
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass

#: Speed of light [m/s] — the one copy (``__init__`` imports it from here).
C0_SI = 299792458.0


# ═══════════════════════════════════════════════════════════════════════════
# Drude-Lorentz fitting (log10 parameter space)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DispersionResult:
    """Result of evaluating eps(ω): ω [rad/s]; n/k = real/imaginary parts of sqrt(eps)."""
    omega: np.ndarray
    n: np.ndarray
    k: np.ndarray


def evaluate_dispersion(eps_inf, poles, omega):
    """eps(ω) = eps_inf + Σ ωp²/(ω0² − ω² − j·γ·ω) (pure numpy).
    ``poles`` = a list of (ω0, γ, ωp²) SI triplets (unified pole
    convention)."""
    eps = np.full_like(omega, float(eps_inf), dtype=complex)
    w2 = omega * omega
    for w0, gm, omega_p_sq in poles:
        eps += omega_p_sq / (w0**2 - w2 - 1j * gm * omega)
    sq = np.sqrt(eps)
    return DispersionResult(omega=omega, n=sq.real, k=sq.imag)


@dataclass
class ComponentFit:
    """Single-component fit: eps_inf, poles as (ω0, γ, ωp²) SI triplets, and a
    convergence flag. The three trailing fields exist only for the
    tolerance-driven path (``fit_adaptive``) and default to the fixed-count
    behaviour."""
    eps_inf: float
    poles: list
    converged: bool
    error: float | None = None          # max(rmse_n, rmse_k) of this fit
    poles_tried: int | None = None      # Lorentz poles actually used
    target_met: bool = True             # False = max_poles reached short of tolerance


def params_to_poles(params, n_drude, n_lorentz):
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


def dl_residual(omega, eps_inf, n_drude, n_lorentz, *params):
    """curve_fit residual (n/k real and imaginary parts stacked)."""
    r = evaluate_dispersion(eps_inf, params_to_poles(params, n_drude,
                                                       n_lorentz), omega)
    return np.concatenate([r.n, r.k])


def fit_drude_lorentz(wavelengths, n, k, n_drude=0, n_lorentz=2,
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

    omega = 2 * np.pi * C0_SI / wavelengths

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
            lambda w, *p: dl_residual(w, p[0], n_drude, n_lorentz, *p[1:]),
            omega, ydata, p0=p0,
            bounds=(bounds_low, bounds_high), maxfev=maxfev)
        fit_converged = True
    except (RuntimeError, ValueError):
        popt = np.array(p0)
        fit_converged = False

    eps_inf = float(popt[0])
    ade_poles = params_to_poles(popt[1:], n_drude, n_lorentz)
    return ComponentFit(eps_inf=eps_inf, poles=ade_poles,
                         converged=fit_converged)


# ═══════════════════════════════════════════════════════════════════════════
# Result assembly (pure)
# ═══════════════════════════════════════════════════════════════════════════

def pole_to_node(w0, gamma, omega_p_sq):
    """An (ω0, γ, ωp²) SI triplet → a wire-shaped PoleSpec dict.

    Lorentz: ωp² = Δε·ω0² → delta_eps = ωp²/ω0²; Drude: ω0 == 0 (by
    the unified convention, ``params_to_poles`` produces exactly
    0.0) → omega_p = √ωp².
    """
    if w0 == 0.0:
        return {"kind": "drude", "omega_p": float(omega_p_sq) ** 0.5,
                "gamma": float(gamma)}
    return {"kind": "lorentz", "omega_0": float(w0), "gamma": float(gamma),
            "delta_eps": float(omega_p_sq) / float(w0) ** 2}


def fit_range_bounds(fit_range):
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


def scalar_result(fit, w, n_data, k_data, extra=None):
    """Single-component result assembly (**numeric only** — a six-key
    dict, no material_node / name; the only material-node factory is
    :func:`assemble_nk`). The five quality scalars are computed on
    the **data points** (no tables, no curves)."""
    omega = 2.0 * np.pi * C0_SI / w
    d = evaluate_dispersion(fit.eps_inf, fit.poles, omega)
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

    poles = [pole_to_node(*p) for p in fit.poles]
    quality = {
        "n_points": int(w.size),
        "rmse_n": float(np.sqrt(np.mean((n_fit - n_data) ** 2))),
        "rmse_k": float(np.sqrt(np.mean((k_fit - k_data) ** 2))),
        "max_abs_err_n": float(np.max(np.abs(n_fit - n_data))),
        "max_abs_err_k": float(np.max(np.abs(k_fit - k_data))),
    }
    if extra:                                   # adaptive mode only (shape locked otherwise)
        quality.update(extra)
        if fit.target_met is False:
            warnings_out.append(
                f"tolerance not met: best max(rmse_n, rmse_k) = "
                f"{fit.error:.4g} with {fit.poles_tried} Lorentz pole(s); "
                f"raise max_poles, widen fit_range, or accept this fit "
                f"knowing the residual")
        if extra.get("passive") is False:
            warnings_out.append(
                "the fitted dispersion is NOT passive in the window "
                "(imag(eps) < 0 somewhere) — a time-domain run would see "
                "gain; re-fit with a different pole count or window")
        cov = extra.get("coverage")
        if isinstance(cov, float) and cov < 1.0:
            warnings_out.append(
                f"fit_range used {cov:.0%} of the supplied data; the fit is "
                f"only meaningful inside that window — simulated bands "
                f"outside it are unvalidated")
    return {
        "converged": bool(fit.converged),
        "eps_inf": float(fit.eps_inf),
        "n_poles": len(poles),
        "poles": poles,                      # SI angular frequencies rad/s (Δε dimensionless)
        "quality": quality,
        "warnings": warnings_out,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Tolerance-driven fitting + the two advisory checks
# ═══════════════════════════════════════════════════════════════════════════

def fit_error(fit, wavelengths, n, k):
    """max(rmse_n, rmse_k) of a fit on the given (already windowed) data."""
    omega = 2.0 * np.pi * C0_SI / np.asarray(wavelengths, dtype=float)
    d = evaluate_dispersion(fit.eps_inf, fit.poles, omega)
    rn = float(np.sqrt(np.mean((np.asarray(d.n) - np.asarray(n)) ** 2)))
    rk = float(np.sqrt(np.mean((np.asarray(d.k) - np.asarray(k)) ** 2)))
    return max(rn, rk)


def fit_adaptive(wavelengths, n, k, *, tolerance, max_poles=8, n_drude=0,
                 start_poles=2, maxfev=5000):
    """Fit with the **pole count driven by a residual target**.

    Starts at ``start_poles`` Lorentz poles and adds one at a time until
    ``max(rmse_n, rmse_k) <= tolerance`` or ``max_poles`` is reached (the
    best of the tried counts is returned then, with ``target_met=False``).
    Rationale (measured on a Tauc-Lorentz spectrum): a metal fits with 2
    poles, a band edge needs 6–8 — the user should not have to guess.

    The pole *locations* still come from ``fit_drude_lorentz`` (peak-scan
    start + log10-space least squares): this is the adaptive-count upgrade,
    not a different pole basis, so the kernel keeps seeing plain
    Lorentz/Drude poles (no protocol / ADE change).
    """
    if not (tolerance > 0):
        raise ValueError(
            f"tolerance must be > 0, got {tolerance!r} — it is an absolute "
            f"bound on max(rmse_n, rmse_k)")
    if int(max_poles) < 1:
        raise ValueError(f"max_poles must be >= 1, got {max_poles!r}")
    start = max(1, int(start_poles))
    best = None
    for n_pol in range(start, int(max_poles) + 1):
        fit = fit_drude_lorentz(wavelengths, n, k, n_drude=n_drude,
                                n_lorentz=n_pol, maxfev=maxfev)
        err = fit_error(fit, wavelengths, n, k)
        if best is None or err < best.error:
            fit.error, fit.poles_tried = err, n_pol
            fit.target_met = err <= tolerance
            best = fit
        if err <= tolerance:
            return best
    return best


def passivity_report(eps_inf, poles, omega):
    """Is the fitted dispersion passive over the sampled band?

    ``imag(eps) >= 0`` everywhere under this module's convention
    (``1/(ω0² − ω² − jγω)``): a negative imaginary part would mean gain in
    a time-domain run. A real fit can still fail this (``eps_inf <= 0``, or
    a pole pair with a negative strength), which is why it is checked
    rather than assumed.
    """
    eps = np.full_like(np.asarray(omega, dtype=float), float(eps_inf),
                       dtype=complex)
    for w0, gm, omega_p_sq in poles:
        eps += omega_p_sq / (w0 ** 2 - np.asarray(omega, dtype=float) ** 2
                             - 1j * gm * np.asarray(omega, dtype=float))
    return bool(np.all(eps.imag >= -1e-12))


def coverage_fraction(lo, hi, wl):
    """Fraction of the supplied data rows that the fit window used."""
    wl = np.asarray(wl, dtype=float)
    if wl.size == 0:
        return 0.0
    inside = int(((wl >= lo) & (wl <= hi)).sum())
    return float(inside) / float(wl.size)
