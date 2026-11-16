"""genie.core.material.fit — the fitting engine (pure numpy).

One scalar n-k component → a **rational pole model**

    eps(s) = eps_inf + Σ_k [ r_k/(s − p_k) + conj(r_k)/(s − conj(p_k)) ]
    s = −jω        (e^{−iωt}; the same convention as ωp²/(ω0² − ω² − jγω))

The fitter is a **Levenberg–Marquardt nonlinear least squares** over the
rational form (Q24: the former vector-fitting path was removed entirely —
its σ pole relocation collapsed on narrow bands).  Parameters are residues plus
exponentiated pole coordinates (stable half plane by construction) and an
``eps_inf = 1 + e^y`` term (ε∞ ≥ 1 hard constraint).  Every pole carries a
complex residue; Lorentz and Drude are then the special case (a single real
positive residue on the axis) and survive only as **wire-format
shorthands** a user may hand-write; the fitter has no Lorentz stage (Q14).

Order search: ``fit_adaptive`` evaluates every order 0..min(max_poles, N−1)
and keeps the global argmin of the weighted RMS of the eps residual
(``tolerance`` is the report threshold, not an early stop).  Order P seeds
from order P−1 plus one zero-residue pole, so the RMS is **monotone
non-increasing** in the order by construction.  Passivity is reported by
default; ``make_passive=True`` enables Lumerical's gain penalty.
"""
from __future__ import annotations

import math

import numpy as np
from dataclasses import dataclass, field

#: Speed of light [m/s] — the one copy (``__init__`` imports it from here).
C0_SI = 299792458.0

#: ε∞ floor (`0x230`, default 1.0) — **hard constraint** (站长拍 2026-11-13):
#: ε∞ ≥ 1, enforced by parameterising ε∞ = 1 + e^y.  A constant below it is a
#: fitting artefact (Ag/Cu were landing at −135 with huge cancelling residues).
_EPSINF_FLOOR = 1.0
#: Levenberg–Marquardt iteration guard (termination only, not a quality knob).
_LM_MAX_ITER = 60
#: Weight on the imaginary equations ("imaginary weight", `PoleFit` field 0x1f8).
#: Lumerical's material default is 1.0 for all shipped materials (`.mdf`, 55/55).
_W_IMAG = 1.0
#: Lumerical material defaults (`.mdf`, 55 entries): tolerance 0.1, pole cap 6; our cap is 10 (the 12-point metals need 9-10 pairs).
#: The search itself starts at **0** (constant model) and only grows when the
#: tolerance is not met, so the cap is a ceiling, not a target.
_TOLERANCE = 0.1
_MAX_COEFFS = 10
#: Passivity penalty (slot[5], gated by ``make_passive``): gain above
#: ``_GAIN_EPS`` multiplies the order's error by ``_PENALTY`` so the order
#: search rejects it (`0x14422ffa8` = 1e-14, `0x14422ffb0` = 1e6).
_GAIN_EPS = 1e-14
_PENALTY = 1e6
#: Lossy-sample threshold for the penalty branch (`FUN_1403aa6a0`: 1e-6 relative).
_LOSSY_EPS = 1e-6


# ═══════════════════════════════════════════════════════════════════════════
# Model evaluation
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DispersionResult:
    """Result of evaluating eps(ω): ω [rad/s]; n/k = real/imaginary parts of sqrt(eps)."""
    omega: np.ndarray
    n: np.ndarray
    k: np.ndarray


def evaluate_dispersion(eps_inf, poles, omega):
    """eps(ω) = eps_inf + Σ (a0 + a1·s + a2·s²)/(s² + b1·s + b0), s = −jω.

    ``poles`` = a list of **(a0, a1, a2, b0, b1)** 5-tuples in SI. A
    second-order section with a general numerator is exactly one conjugate
    pole pair with a general complex residue (plus a constant that ε∞
    absorbs), so this is the general rational model, not a special family.
    """
    eps = np.full_like(omega, float(eps_inf), dtype=complex)
    s = -1j * np.asarray(omega, dtype=float)
    s2 = s * s
    for a0, a1, a2, b0, b1 in poles:
        eps += (a0 + a1 * s + a2 * s2) / (s2 + b1 * s + b0)
    sq = np.sqrt(eps)
    return DispersionResult(omega=omega, n=sq.real, k=sq.imag)


def pole_from_node(node):
    """A wire-shaped PoleSpec dict → the (a0, a1, a2, b0, b1) 5-tuple.

    Inverse of :func:`pole_to_node`; used wherever a *saved* node is
    re-evaluated (``NKFit.curve``, diagnostics) so the wire form stays the
    single on-disk representation.
    """
    kind = node.get("kind")
    if kind == "lorentz":
        b0 = float(node["omega_0"]) ** 2
        return (float(node["delta_eps"]) * b0, 0.0, 0.0, b0,
                float(node["gamma"]))
    if kind == "drude":
        return (float(node["omega_p"]) ** 2, 0.0, 0.0, 0.0,
                float(node["gamma"]))
    if kind == "rational":
        num = list(node["num"])
        den = list(node["den"])
        return (float(num[0]), float(num[1]), float(num[2]),
                float(den[0]), float(den[1]))
    raise ValueError(
        f"unknown pole kind {kind!r} — expected 'lorentz', 'drude' or "
        f"'rational'")


@dataclass
class ComponentFit:
    """One component's fit result (numeric only — no model, no I/O)."""
    eps_inf: float
    poles: list                      # [(a0, a1, a2, b0, b1), ...] SI
    converged: bool = True
    error: float | None = None       # weighted RMS (penalised) — the selection score
    rms: float | None = None         # weighted RMS before the passivity penalty
    poles_tried: int | None = None   # pole count the fit finally used
    target_met: bool = True          # False = best order was over tolerance


# ═══════════════════════════════════════════════════════════════════════════
# Wire form
# ═══════════════════════════════════════════════════════════════════════════

def pole_to_node(a0, a1, a2, b0, b1):
    """An (a0, a1, a2, b0, b1) SI 5-tuple → a wire-shaped PoleSpec dict.

    The **minimal** form is emitted: a1 = a2 = 0 → the historical shorthand
    (b0 = 0 → drude, else lorentz with Δε = a0/b0); otherwise the rational
    form with num/den. Old documents and hand-written poles keep their exact
    shorthand dicts — zeros only appear when a fit really used them.
    """
    if a1 == 0.0 and a2 == 0.0:
        if b0 == 0.0:
            return {"kind": "drude", "omega_p": float(a0) ** 0.5,
                    "gamma": float(b1)}
        return {"kind": "lorentz", "omega_0": float(b0) ** 0.5,
                "gamma": float(b1),
                "delta_eps": float(a0) / float(b0)}
    return {"kind": "rational",
            "num": [float(a0), float(a1), float(a2)],
            "den": [float(b0), float(b1)]}


# ═══════════════════════════════════════════════════════════════════════════
# Model helpers (denominator / residues / metrics)
# ═══════════════════════════════════════════════════════════════════════════

def _pair_den(s, p):
    """Denominator of one conjugate pair: s² − 2Re(p)·s + |p|²."""
    return s * s - 2.0 * p.real * s + abs(p) ** 2


def _residues(s, f, pairs, w_im=_W_IMAG, fixed_const=None):
    """Fixed pairs → (eps_inf, [(a0, a1), ...]) — linear least squares.

    ``fixed_const`` pins the constant at that value instead of fitting it: the
    constant column is dropped and the target becomes ``f − fixed_const``
    (Lumerical's "drop the constant term / ε∞ floor" branch, `0x230/0x238`).
    """
    P = len(pairs)
    dens = [_pair_den(s, p) for p in pairs]
    N = s.size
    ncol = 2 * P + (0 if fixed_const is not None else 1)
    A = np.zeros((2 * N, ncol))
    rhs = np.zeros(2 * N)
    target = f if fixed_const is None else f - fixed_const
    for k in range(P):
        B1 = 1.0 / dens[k]
        Bs = s / dens[k]
        A[:N, 2 * k] = B1.real
        A[:N, 2 * k + 1] = Bs.real
        A[N:, 2 * k] = B1.imag
        A[N:, 2 * k + 1] = Bs.imag
    if fixed_const is None:
        A[:N, 2 * P] = 1.0            # eps_inf is REAL: no imaginary row entry
    rhs[:N] = target.real
    rhs[N:] = target.imag
    if w_im != 1.0:
        A[N:] *= w_im
        rhs[N:] *= w_im
    x, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    coefs = [(float(x[2 * k]), float(x[2 * k + 1])) for k in range(P)]
    if fixed_const is None:
        return float(x[2 * P]), coefs
    return float(fixed_const), coefs


def _weighted_rms(s, f, eps_inf, pairs, coefs, w_im=_W_IMAG):
    """Weighted RMS residual — **Lumerical's error function**, verbatim.

    `PoleFit::error` (`FUN_1403a8910`): residual = model − data, real and
    imaginary parts weighted by (w_re, w_im)/max(w_re, w_im), then
    ``‖·‖₂ / √N`` — an RMS, not a max. Used for model *ranking*; the
    user-facing acceptance test stays the relative n/k error.
    """
    model = np.full(s.size, float(eps_inf), dtype=complex)
    for p, (a0, a1) in zip(pairs, coefs):
        model += (a0 + a1 * s) / _pair_den(s, p)
    r = model - f
    w_re = 1.0
    denom = max(w_re, w_im)
    res = np.concatenate([r.real * (w_re / denom), r.imag * (w_im / denom)])
    return float(np.sqrt(np.mean(res ** 2)))


def _gain_violation(eps_inf, sections, omega, f):
    """Max gain on the lossy samples (the quantity slot[5] penalises).

    Passive means ``imag(eps) >= 0`` in this convention, so a violation is
    ``max(-imag(eps), 0)`` over the samples whose *data* is lossy (threshold
    1e-6 relative, `FUN_1403aa6a0`).  Above 1e-14 the order's error is
    multiplied by 1e6 and the order search rejects it.
    """
    s = -1j * np.asarray(omega, dtype=float)
    eps = np.full(s.shape, float(eps_inf), dtype=complex)
    for a0, a1, a2, b0, b1 in sections:
        eps += (a0 + a1 * s + a2 * s * s) / (s * s + b1 * s + b0)
    f = np.asarray(f, dtype=complex)
    scale = float(np.max(np.abs(f))) or 1.0
    lossy = f.imag > _LOSSY_EPS * scale
    if not lossy.any():
        return 0.0
    return float(np.max(np.maximum(0.0, -eps.imag[lossy])))


def _norm_sections(sections, s0):
    """Physical sections → (poles, residues) in the normalized variable."""
    poles, res = [], []
    for a0, a1, _a2, b0, b1 in sections:
        b0n, b1n = b0 / (s0 * s0), b1 / s0
        half = b1n / 2.0
        disc = max(b0n - half * half, 0.0)
        poles.append(complex(-half, math.sqrt(disc)))
        res.append((a0 / (s0 * s0), a1 / s0))
    return poles, res


def _norm_to_sections(poles, residues, s0):
    out = []
    for p, (a0, a1) in zip(poles, residues):
        pp = p * s0
        out.append((float(a0 * s0 * s0), float(a1 * s0), 0.0,
                    float(abs(pp) ** 2), float(-2.0 * pp.real)))
    return out


def _polish(sn, f, poles, residues, eps_inf, w_im=_W_IMAG, max_iter=None):
    """Levenberg–Marquardt polish of one candidate (**numpy only**).

    Parameters: ``eps_inf = floor + e^y`` — ε∞ ≥ 1 is a hard constraint
    (站长拍 2026-11-13) — per-section residues ``(a0, a1)``, and pole
    coordinates ``p = −e^lr + i·e^lw`` (stable half plane by construction).
    Analytic Jacobian; the result is never worse than the input candidate
    (the order search relies on that monotonicity).  Returns
    ``(eps_inf, poles, residues, rms)``.
    """
    P = len(poles)
    if P == 0:
        return eps_inf, poles, residues, _weighted_rms(sn, f, eps_inf, [], [], w_im)
    it_max = int(max_iter or _LM_MAX_ITER)
    a0 = np.array([r[0] for r in residues], dtype=float)
    a1 = np.array([r[1] for r in residues], dtype=float)
    lr = np.log(np.maximum(-np.array([p.real for p in poles], dtype=float), 1e-12))
    lw = np.log(np.maximum(np.array([p.imag for p in poles], dtype=float), 1e-12))
    ei = max(float(eps_inf), _EPSINF_FLOOR + 1e-9)
    y = math.log(ei - _EPSINF_FLOOR)
    x = np.concatenate([[y], a0, a1, lr, lw])
    denom = max(1.0, w_im)

    def unpack(z):
        yy = z[0]
        aa0 = z[1:1 + P]
        aa1 = z[1 + P:1 + 2 * P]
        r2 = z[1 + 2 * P:1 + 3 * P]
        w2 = z[1 + 3 * P:1 + 4 * P]
        e_y = math.exp(float(np.clip(yy, -60.0, 60.0)))
        ei_z = _EPSINF_FLOOR + e_y
        b1 = 2.0 * np.exp(np.clip(r2, -30.0, 30.0))
        b0 = (np.exp(np.clip(2.0 * r2, -60.0, 60.0))
              + np.exp(np.clip(2.0 * w2, -60.0, 60.0)))
        dens = [sn * sn + b1[k] * sn + b0[k] for k in range(P)]
        model = np.full(sn.shape, ei_z, dtype=complex)
        for k in range(P):
            model += (aa0[k] + aa1[k] * sn) / dens[k]
        return ei_z, e_y, aa0, aa1, b1, b0, dens, model

    def resid(z):
        _ei, _ey, _a0, _a1, _b1, _b0, _dens, model = unpack(z)
        r = model - f
        return np.concatenate([r.real / denom, r.imag * (w_im / denom)])

    def jac(z):
        # column order must match x = [y, a0_0..a0_{P-1}, a1_0.., lr_0.., lw_0..]
        _ei, e_y, aa0, aa1, b1, b0, dens, _model = unpack(z)
        n = sn.size
        d_y = np.concatenate([np.full(n, e_y / denom), np.zeros(n)])
        d_a0, d_a1, d_lr, d_lw = [], [], [], []
        for k in range(P):
            sec = (aa0[k] + aa1[k] * sn) / dens[k]
            d_a0.append(np.concatenate([(1.0 / dens[k]).real / denom,
                                        (1.0 / dens[k]).imag * (w_im / denom)]))
            d_a1.append(np.concatenate([(sn / dens[k]).real / denom,
                                        (sn / dens[k]).imag * (w_im / denom)]))
            e_lr = b1[k] / 2.0
            g_lr = -(sec / dens[k]) * (2.0 * e_lr * sn + 2.0 * e_lr * e_lr)
            g_lw = -(sec / dens[k]) * (2.0 * (b0[k] - e_lr * e_lr))
            d_lr.append(np.concatenate([g_lr.real / denom,
                                        g_lr.imag * (w_im / denom)]))
            d_lw.append(np.concatenate([g_lw.real / denom,
                                        g_lw.imag * (w_im / denom)]))
        return np.vstack([d_y] + d_a0 + d_a1 + d_lr + d_lw)

    p0 = [complex(-math.exp(lr[k]), math.exp(lw[k])) for k in range(P)]
    res0 = list(zip(a0.tolist(), a1.tolist()))
    best_rms = _weighted_rms(sn, f, ei, p0, res0, w_im)
    best_x = x.copy()
    lam = 1e-3
    r = resid(x)
    cost = float(r @ r)
    for _ in range(it_max):
        J = jac(x).T
        JtJ = J.T @ J
        g = J.T @ r
        try:
            delta = np.linalg.solve(JtJ + lam * np.diag(np.diag(JtJ) + 1e-30),
                                    -g)
        except np.linalg.LinAlgError:
            break
        x_new = x + delta
        r_new = resid(x_new)
        cost_new = float(r_new @ r_new)
        if np.isfinite(cost_new) and cost_new < cost:
            x, r, cost = x_new, r_new, cost_new
            lam = max(lam / 3.0, 1e-12)
            if float(delta @ delta) < 1e-24:
                break
        else:
            lam = min(lam * 4.0, 1e12)
            if lam >= 1e12:
                break
    ei_f, _ey, aa0, aa1, _b1, _b0, _dens, _model = unpack(x)
    pp = [complex(-math.exp(np.clip(x[1 + 2 * P + k], -30, 30)),
                  math.exp(np.clip(x[1 + 3 * P + k], -30, 30))) for k in range(P)]
    res = list(zip(aa0.tolist(), aa1.tolist()))
    rms = _weighted_rms(sn, f, ei_f, pp, res, w_im)
    if not np.isfinite(rms) or rms > best_rms:
        return eps_inf, poles, residues, best_rms
    return float(ei_f), pp, res, float(rms)


# ═══════════════════════════════════════════════════════════════════════════
# Adaptive pole count
# ═══════════════════════════════════════════════════════════════════════════

def fit_adaptive(wavelengths, n, k, *, tolerance=_TOLERANCE,
                 max_poles=_MAX_COEFFS, w_im=_W_IMAG,
                 make_passive=False):
    """Lumerical's ``PoleFit::fit_auto`` — same knobs, same yardstick.

    - orders **0, 1, 2, …** up to ``min(max_poles, n_samples−1)`` — order 0
      is the constant model, order 1 is one conjugate pair, and the caller's
      ``max_poles`` is only a ceiling;
    - each order is scored by the **weighted RMS** of the eps residual
      (slot[5]), with the ``w_im`` ("imaginary weight") applied to the
      imaginary residual — and with the passivity penalty on top when
      ``make_passive`` (gain > 1e-14 → error ×1e6);
    - every order is a Levenberg–Marquardt polish start: order 1 from a
      deterministic grid of pole positions, higher orders from the previous
      order's solution plus one new pole with zero residue;
    - **all** orders up to the ceiling are evaluated and the global argmin is
      kept (``tolerance`` is the acceptance/report threshold, not an early
      stop);
    - poles stay in the stable half plane by construction.

    ``eps_inf`` is constrained to **≥ 1** (站长拍 2026-11-13): a constant
    below the floor is a fitting artefact, not a physical dispersion term.
    """
    if not (tolerance > 0):
        raise ValueError(f"tolerance must be > 0, got {tolerance!r}")
    if int(max_poles) < 1:
        raise ValueError(f"max_poles must be >= 1, got {max_poles!r}")
    wl = np.asarray(wavelengths, dtype=float)
    n = np.asarray(n, dtype=float)
    k = np.asarray(k, dtype=float)
    omega = 2.0 * np.pi * C0_SI / wl
    s = -1j * omega
    f = (n + 1j * k) ** 2
    w_lo, w_hi = float(np.min(omega)), float(np.max(omega))
    # 2N complex equations must cover 2P+1 unknowns → P ≤ N−1
    max_order = int(min(int(max_poles), max(0, wl.size - 1)))

    def _search():
        """All orders 0..max_order; keep the global argmin.

        Pure LM pipeline (VF retired 2026-11-13): order 1 is seeded from a
        small deterministic grid of pole positions; every higher order adds
        one pole to the previous order's solution with **zero residue**, so
        its starting RMS equals the previous best and the LM polish never
        increases the cost ⇒ ``best_rms(order) <= best_rms(order-1)`` holds
        by construction.  ε∞ ≥ 1 is enforced inside the polish.
        """
        best = None
        prev_pack = None                  # (eps_inf, sections) of the previous order

        # order 0: a constant model — exact for a non-dispersive medium and
        # the smallest candidate.  The constant may be negative (it is the
        # windowed-fit constant, not a physical ε∞; the pole model still
        # carries ε∞ ≥ 1).
        eps0 = float(np.mean(f.real))
        if np.isfinite(eps0):
            rms0 = _weighted_rms(s, f, eps0, [], [], w_im)
            best = ComponentFit(eps_inf=eps0, poles=[], converged=True,
                                error=rms0, rms=rms0, poles_tried=0,
                                target_met=rms0 < tolerance)
            prev_pack = (eps0, [])

        s0 = float(np.exp(np.mean(np.log(np.abs(s)))))
        sn = s / s0
        rng = np.random.default_rng(20261113)   # fixed seed ⇒ reproducible

        for order in range(1, max_order + 1):
            seeds = []                    # (eps_inf, poles_norm, residues_norm)
            # 1) deterministic-random multi-start: pole positions log-uniform in
            #    [band/3, band*3], off-axis angle 0.05–0.6 (residues from LSQ)
            lo_n, hi_n = w_lo / s0, w_hi / s0
            for _ in range(4):
                ws = np.exp(rng.uniform(np.log(lo_n / 3.0),
                                        np.log(hi_n * 3.0), order))
                pp = [complex(-rng.uniform(0.05, 0.6) * wk, wk) for wk in ws]
                ei0, res0 = _residues(sn, f, pp, w_im)
                seeds.append((max(ei0, _EPSINF_FLOOR + 1e-9), pp, res0))
            # 2) incremental: the previous order's solution + one new pole
            if prev_pack is not None:
                ei_p, secs_p = prev_pack
                poles_n, res_n = _norm_sections(secs_p, s0)
                for alpha in (0.05, 0.3):
                    w_new = math.sqrt(lo_n * hi_n)
                    pp = poles_n + [complex(-alpha * w_new, w_new)]
                    rr = res_n + [(0.0, 0.0)]
                    seeds.append((max(ei_p, _EPSINF_FLOOR + 1e-9), pp, rr))
            if not seeds:
                continue
            polished = []
            for ei0, pp0, rr0 in seeds:
                ei1, pp1, rr1, rms1 = _polish(sn, f, pp0, rr0, ei0, w_im)
                polished.append((ei1, _norm_to_sections(pp1, rr1, s0),
                                 rms1))
            eps_inf, sections, rms = min(polished, key=lambda p: p[2])
            prev_pack = (eps_inf, sections)
            err = rms
            if make_passive and _gain_violation(
                    eps_inf, sections, omega, f) > _GAIN_EPS:
                err *= _PENALTY
            fit = ComponentFit(
                eps_inf=eps_inf, poles=sections, converged=True,
                error=err, rms=rms, poles_tried=order,
                target_met=err < tolerance)
            if best is None or err < best.error:
                best = fit
            if err < tolerance:
                # Lumerical's order loop stops as soon as the tolerance is met
                # (e180:182, err < 0x220 = 0.1) and then keeps the argmin of
                # the orders evaluated so far — growing further only refines a
                # fit that already passed.
                break
        return best

    best = _search()
    if best is None:
        raise ValueError(
            "fit failed for every order — check the band and the data")
    return best


# ═══════════════════════════════════════════════════════════════════════════
# Advisory checks
# ═══════════════════════════════════════════════════════════════════════════

def fit_range_bounds(fit_range):
    """Unpack + validate a fit window (lo < hi) — a reversed window would
    later surface as the misleading 'Only 0 data points'; block it at the
    cause and report the actual range."""
    lo, hi = fit_range
    if not lo < hi:
        raise ValueError(
            f"fit_range must satisfy lo < hi [m]; got ({lo!r}, {hi!r}) — "
            "a reversed range selects zero data points (the fit would "
            "otherwise fail later as 'Only 0 data points', far from the "
            "cause)")
    return lo, hi


def passivity_report(eps_inf, poles, omega):
    """Is the fitted dispersion passive over the sampled band?

    ``imag(eps) >= 0`` everywhere (e^{−iωt} convention): a negative
    imaginary part means gain in a time-domain run. A real fit can still
    fail this, which is why it is checked rather than assumed.
    """
    w = np.asarray(omega, dtype=float)
    eps = np.full_like(w, float(eps_inf), dtype=complex)
    s = -1j * w
    s2 = s * s
    for a0, a1, a2, b0, b1 in poles:
        eps += (a0 + a1 * s + a2 * s2) / (s2 + b1 * s + b0)
    return bool(np.all(eps.imag >= -1e-12))


def coverage_fraction(lo, hi, wl):
    """Fraction of the supplied data rows that the fit window used."""
    wl = np.asarray(wl, dtype=float)
    if wl.size == 0:
        return 0.0
    inside = int(((wl >= lo) & (wl <= hi)).sum())
    return float(inside) / float(wl.size)


# ═══════════════════════════════════════════════════════════════════════════
# Result assembly (pure): numeric → the user dict
# ═══════════════════════════════════════════════════════════════════════════

def scalar_result(fit, w, n_data, k_data, *, passive, coverage):
    """Single-component result assembly (**numeric only** — no node/name).

    ``quality['rms']`` is the **one error concept** (站长拍 2026-11-13):
    the weighted **absolute** ε-RMS of the residual — the quantity the
    optimiser minimises, the quantity ``tolerance`` compares, and the
    quantity every display surface prints.  There are no relative variants
    and no n/k error split.
    """
    omega = 2.0 * np.pi * C0_SI / np.asarray(w, dtype=float)
    _d = evaluate_dispersion(fit.eps_inf, fit.poles, omega)
    n_data = np.asarray(n_data, dtype=float)
    k_data = np.asarray(k_data, dtype=float)

    warnings_out: list[str] = []
    if fit.target_met is False:
        warnings_out.append(
            f"tolerance not met: best weighted ε-RMS = {fit.error:.3g} with "
            f"{len(fit.poles)} pole(s) (of the orders tried) — raise "
            f"max_poles, narrow the band, or accept this fit knowing the "
            f"residual")
    if not passive:
        warnings_out.append(
            "the fitted dispersion is NOT passive in the band "
            "(imag(eps) < 0 somewhere) — a time-domain run would see gain; "
            "re-fit with another pole count or narrow the band")
    if coverage < 1.0:
        warnings_out.append(
            f"the band covers {coverage:.0%} of the supplied data; the fit is "
            f"only meaningful inside it — simulated bands outside are "
            f"unvalidated")

    poles = [pole_to_node(*p) for p in fit.poles]
    # The one error concept (站长拍 2026-11-13): weighted **absolute** ε-RMS.
    # It is what the optimiser minimises, what ``tolerance`` compares, and
    # what every surface displays — no relative variants, no n/k split.
    quality = {
        "n_points": int(np.size(w)),
        "rms": float(fit.rms if fit.rms is not None else fit.error or 0.0),
        "tolerance_met": bool(fit.target_met),
        "passive": bool(passive),
        "coverage": float(coverage),
    }
    return {
        "converged": bool(fit.converged),
        "eps_inf": float(fit.eps_inf),
        "n_poles": len(poles),
        "poles": poles,
        "quality": quality,
        "warnings": warnings_out,
    }
