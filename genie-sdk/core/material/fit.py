"""genie.core.material.fit — the fitting engine (pure numpy).

One scalar n-k component → a **rational pole model**

    eps(s) = eps_inf + Σ_k [ r_k/(s − p_k) + conj(r_k)/(s − conj(p_k)) ]
    s = −jω        (e^{−iωt}; the same convention as ωp²/(ω0² − ω² − jγω))

The fitter is **vector fitting with pole relocation** (Gustavsen–Semlyen;
no SK reweighting — that broke convergence here). Every pole carries a
complex residue, so "zeros" are not a separate stage — they *are* the
residue phase. Lorentz and Drude are then the special case (a single real
positive residue on the axis) and survive only as **wire-format
shorthands** a user may hand-write; the fitter has no Lorentz stage (Q14).

Why this algorithm: with the poles fixed the model is **linear** in the
residues; the pole *positions* are recovered by solving another linear
problem for the zeros of an auxiliary function σ(s) and iterating. No
nonlinear optimiser, no scipy. Relocation is non-convex, so each order is
restarted from randomized starting points and the best RMS wins.

``vector_fit`` returns the pole set; ``fit_adaptive`` evaluates every order
0..min(max_poles, N−1) and keeps the global argmin of the weighted RMS of
the eps residual (``tolerance`` is the report threshold, not an early
stop). Passivity is reported by default; ``make_passive=True`` enables
Lumerical's gain penalty.
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field

#: Speed of light [m/s] — the one copy (``__init__`` imports it from here).
C0_SI = 299792458.0

#: Pole relocation is non-convex, so every order is restarted from a set of
#: random starting points (log-uniform off-axis angle, log/lin spacing) and the
#: best RMS is kept.  Fixed seed ⇒ reproducible on the same data.
_VF_STARTS = 12
_VF_SEED = 20261108
#: Relocation runs to convergence (Δerr < 1e-12); 200 is only a termination
#: guard for pathological cases, not a quality knob.
_VF_MAX_ITER = 20
_VF_EPS = 1e-6
#: Lumerical's ε∞ floor (`0x230`, default 1.0): n-k library data always
#: satisfies ε∞ ≥ 1, so a constant fitted below it is a fitting artefact
#: (Ag/Cu were landing at −135 with huge cancelling residues).  Their branch
#: drops the constant column, pins it here, and refits once (`0x238 = 0`).
_EPSINF_FLOOR = 1.0
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
# Vector fitting (pole relocation)
# ═══════════════════════════════════════════════════════════════════════════

def _starting_poles(omega_lo, omega_hi, n_pairs, alpha, grid="log"):
    """One starting-pole set: **one pole per pair** (the caller's pole count
    *is* the pair count, since a wire pole is a second-order section), placed
    on ``grid`` (``"log"`` or ``"lin"``) with the real part ``-alpha·ω``.

    There is no Lorentz pre-fit: the starting point is a property of the
    band.  ``_vf_starts`` samples this family because relocation is non-convex.
    """
    if n_pairs <= 0:
        return []
    lo = max(float(omega_lo), 1.0)
    hi = max(float(omega_hi), lo * 1.000001)
    if grid == "log":
        w = np.exp(np.linspace(np.log(lo), np.log(hi), n_pairs))
    else:
        w = np.linspace(lo, hi, n_pairs)
    return [complex(-alpha * float(wk), float(wk)) for wk in w]


def _vf_starts(seed=_VF_SEED, n=_VF_STARTS):
    """Randomized (but seeded ⇒ reproducible) starting points.

    The old fixed six-set family was itself a restriction; relocation is
    non-convex, so the off-axis angle is sampled log-uniformly in
    ``[1e-3, 1]`` and the spacing is log or lin with equal probability.
    """
    rng = np.random.default_rng(seed)
    return [(10.0 ** rng.uniform(-3.0, 0.0),
             "log" if rng.random() < 0.5 else "lin")
            for _ in range(int(n))]


def _pair_den(s, p):
    """Denominator of one conjugate pair: s² − 2Re(p)·s + |p|²."""
    return s * s - 2.0 * p.real * s + abs(p) ** 2


def _sigma_zeros(pairs, u, v):
    """Zeros of ``σ(s) = 1 + Σ (u_k s + v_k)/den_k`` via state-space.

    Each section is realised as ``A_k = [[0,1],[-b0,-b1]]``, ``B_k = [0,1]ᵀ``,
    ``C_k = [v_k, u_k]``; then σ = 1 + C(sI−A)⁻¹B = det(sI−A+BC)/det(sI−A),
    so its zeros are the eigenvalues of ``A − B·C`` (2P×2P).  This replaces the
    monomial-polynomial expansion + ``np.roots``, which is ill-conditioned at
    higher orders and was losing the good pole set (Au order 5/6 regressed).
    """
    P = len(pairs)
    if P == 0:
        return np.array([], dtype=complex)
    A = np.zeros((2 * P, 2 * P))
    B = np.zeros((2 * P, 1))
    C = np.zeros((1, 2 * P))
    for k, p in enumerate(pairs):
        b0 = float(abs(p) ** 2)
        b1 = float(-2.0 * p.real)
        i = 2 * k
        A[i, i + 1] = 1.0
        A[i + 1, i] = -b0
        A[i + 1, i + 1] = -b1
        B[i + 1, 0] = 1.0
        C[0, i] = float(v[k])
        C[0, i + 1] = float(u[k])
    return np.linalg.eigvals(A - B @ C)


def _vf_step(s, f, pairs, w_im=_W_IMAG, with_const=True):
    """One pole-relocation step in the **conjugate-pair basis**.

    Working with pairs (rather than raw poles) makes the whole thing
    real-coefficient by construction: the σ function is a real-rational
    function, so its zeros come in conjugate pairs and every section is
    realizable as s²+b1·s+b0. Model:

        Σ_k (a1_k·s + a0_k)/den_k + d  ≈  f · [ 1 + Σ_k (u_k·s + v_k)/den_k ]

    Zeros of the bracket are the improved poles (polynomial roots).
    ``with_const=False`` drops the ``d`` column — used by the constrained
    pass where the constant is pinned (``f`` is then already ``eps - d``).

    ``w_im`` weights every **imaginary** equation row (Lumerical's
    "imaginary weight" knob, default 1.0, optionally 2.0 — reverse-engineered
    from `PoleFit`, field 0x1f8). No SK reweighting by the previous σ: that
    was tried and *broke* convergence; Lumerical also uses the plain
    linearised (Levy) system.
    """
    P = len(pairs)
    dens = [_pair_den(s, p) for p in pairs]
    Bs = [s / d for d in dens]
    B1 = [1.0 / d for d in dens]
    N = s.size
    base = 2 * P + (1 if with_const else 0)
    ncol = base + 2 * P
    A = np.zeros((2 * N, ncol))
    rhs = np.zeros(2 * N)
    # model residues (a0 real column, a1 real column)
    for k in range(P):
        A[:N, 2 * k] = B1[k].real
        A[:N, 2 * k + 1] = Bs[k].real
        A[N:, 2 * k] = B1[k].imag
        A[N:, 2 * k + 1] = Bs[k].imag
    if with_const:
        A[:N, 2 * P] = 1.0
    # − f · σ  (σ coefficients u, v)
    for k in range(P):
        cu = -f * Bs[k]
        cv = -f * B1[k]
        A[:N, base + 2 * k] = cu.real
        A[N:, base + 2 * k] = cu.imag
        A[:N, base + 2 * k + 1] = cv.real
        A[N:, base + 2 * k + 1] = cv.imag
    rhs[:N] = f.real
    rhs[N:] = f.imag
    if w_im != 1.0:
        A[N:] *= w_im
        rhs[N:] *= w_im
    x, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    u = x[base::2][:P]
    v = x[base + 1::2][:P]
    # zeros of σ(s) = 1 + Σ (u_k s + v_k)/den_k via a state-space linearisation
    # (eig(A − B·C), 2P×2P) — numerically far better than expanding the common
    # denominator into a monomial polynomial and calling np.roots, which loses
    # the good poles at higher orders
    roots = _sigma_zeros(pairs, u, v)
    keep = [r for r in roots if r.imag > 0.0]
    if len(keep) < P:                      # pad with the best available roots
        rest = sorted((r for r in roots if r.imag <= 0.0),
                      key=lambda r: -abs(r.imag))
        keep = keep + rest[:P - len(keep)]
    keep = keep[:P]
    out = []
    for r in keep:
        re = -abs(r.real) if r.real > 0 else r.real
        im = abs(r.imag) if r.imag != 0 else 1e-6
        out.append(complex(re, im))
    return out


def _den_poly(p):
    """(s² − 2Re(p)s + |p|²) as a polynomial, real coefficients."""
    return np.poly1d([1.0, -2.0 * float(p.real), float(abs(p) ** 2)])


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


def vector_fit(s, f, pairs, w_im=_W_IMAG, const_floor=None):
    """Pole relocation + residues in **normalized frequency**.

    1/(s−p) with s, p ~ 1e15 makes the LSQ matrix condition ~1e30 (SVD stops
    converging), so everything is solved with s' = s/s0 and converted back
    (poles ×s0). Returns (eps_inf, section list, weighted RMS error).

    The relocation runs to ``|Δerr| < _VF_EPS``; ``_VF_MAX_ITER`` is only a
    termination guard, and the best iterate is kept.

    ``const_floor`` implements Lumerical's ε∞ floor (`0x230/0x238`): if the
    fitted constant lands **below** the floor, the constant column is dropped,
    it is pinned at the floor, and the relocation is redone — the returned
    ``eps_inf`` is then exactly the floor. n-k library data always satisfies
    ε∞ ≥ 1, so a fit that dives below it is a fitting artefact (Ag/Cu were
    landing at −135), not physics.
    """
    s = np.asarray(s, dtype=complex)
    f = np.asarray(f, dtype=complex)
    s0 = float(np.exp(np.mean(np.log(np.abs(s)))))
    sn = s / s0
    pairs = [complex(p) / s0 for p in pairs]
    best = None
    prev = None
    for _ in range(max(1, int(_VF_MAX_ITER))):
        pairs = _vf_step(sn, f, pairs, w_im, with_const=True)
        eps_inf, coefs = _residues(sn, f, pairs, w_im)
        err = _weighted_rms(sn, f, eps_inf, pairs, coefs, w_im)
        if best is None or err < best[0]:
            best = (err, eps_inf, list(pairs), coefs)
        if prev is not None and abs(err - prev) < _VF_EPS:
            break                       # converged (|Δerr| < _VF_EPS)
        prev = err
    err, eps_inf, pairs, coefs = best

    if const_floor is not None and eps_inf < const_floor:
        # Lumerical: drop the constant term, pin it at the floor, redo.
        floor = float(const_floor)
        best = None
        prev = None
        for _ in range(max(1, int(_VF_MAX_ITER))):
            pairs = _vf_step(sn, f - floor, pairs, w_im, with_const=False)
            _, coefs = _residues(sn, f, pairs, w_im, fixed_const=floor)
            err = _weighted_rms(sn, f, floor, pairs, coefs, w_im)
            if best is None or err < best[0]:
                best = (err, list(pairs), coefs)
            if prev is not None and abs(err - prev) < _VF_EPS:
                break
            prev = err
        if best is None:
            raise ValueError("eps_inf floor refit produced no iterate")
        err, pairs, coefs = best
        eps_inf = floor

    sections = []
    for p, (a0, a1) in zip(pairs, coefs):
        p = p * s0
        sections.append((float(a0 * s0 * s0), float(a1 * s0), 0.0,
                         float(abs(p) ** 2), float(-2.0 * p.real)))
    return eps_inf, sections, err


# ═══════════════════════════════════════════════════════════════════════════
# Metrics + adaptive pole count
# ═══════════════════════════════════════════════════════════════════════════

def fit_error(eps_inf, poles, wavelengths, n, k):
    """max(rel_err_n, rel_err_k) over the supplied samples — the user's metric."""
    omega = 2.0 * np.pi * C0_SI / np.asarray(wavelengths, dtype=float)
    d = evaluate_dispersion(eps_inf, poles, omega)
    scale_n = np.maximum(np.abs(np.asarray(n, dtype=float)), 1e-12)
    scale_k = np.maximum(np.abs(np.asarray(k, dtype=float)), 1e-3)
    rn = float(np.max(np.abs(d.n - n) / scale_n))
    rk = float(np.max(np.abs(d.k - k) / scale_k))
    return max(rn, rk)


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
    - every order is restarted from ``_VF_STARTS`` randomized starting points
      and the best RMS is kept — pole relocation is non-convex, so a single
      starting point can land in a local minimum (Ag 16% vs 0.8%);
    - **all** orders up to the ceiling are evaluated and the global argmin is
      kept (``tolerance`` is the acceptance/report threshold, not an early
      stop);
    - relocation runs to convergence; there is no pole clamp.

    ``eps_inf`` is a free real constant: there is **no positivity constraint**
    anywhere in the pipeline (a negative constant is a perfectly ordinary
    rational model, and metals routinely need one).
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
    starts = _vf_starts()

    def _search():
        """All orders 0..max_order; keep the global argmin."""
        best = None

        # order 0: a constant model — exact for a non-dispersive medium and
        # the smallest candidate.  The constant may be negative.
        eps0 = float(np.mean(f.real))
        if np.isfinite(eps0):
            rms0 = _weighted_rms(s, f, eps0, [], [], w_im)
            best = ComponentFit(eps_inf=eps0, poles=[], converged=True,
                                error=rms0, rms=rms0, poles_tried=0,
                                target_met=rms0 < tolerance)

        for order in range(1, max_order + 1):
            # relocation is non-convex — randomized (seeded) restarts per order
            best_order = None
            for alpha, grid in starts:
                poles0 = _starting_poles(w_lo, w_hi, order, alpha, grid)
                try:
                    eps_inf, sections, rms = vector_fit(s, f, poles0, w_im,
                                                        const_floor=_EPSINF_FLOOR)
                except (ValueError, np.linalg.LinAlgError):
                    continue
                if not np.isfinite(eps_inf) or not np.all(np.isfinite(sections)):
                    continue
                if best_order is None or rms < best_order[2]:
                    best_order = (eps_inf, sections, rms)
            if best_order is None:
                continue
            eps_inf, sections, rms = best_order
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
            "vector fitting failed for every order — check the band and the data")
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
# Result assembly (pure): numeric → the six-key user dict
# ═══════════════════════════════════════════════════════════════════════════

def scalar_result(fit, w, n_data, k_data, *, passive, coverage):
    """Single-component result assembly (**numeric only** — no node/name).

    ``quality['rms']`` is the **acceptance metric** — Lumerical's weighted RMS
    of the eps residual (absolute) — and ``quality['tolerance_met']`` says
    whether it landed under the requested tolerance.  ``rms_rel`` is the same
    residual divided by the data norm (the number a human should read:
    0.03 = 3%); the relative n/k errors are **diagnostics** only (at k≈0 they
    are dominated by the ratio, not by the model).
    """
    omega = 2.0 * np.pi * C0_SI / np.asarray(w, dtype=float)
    d = evaluate_dispersion(fit.eps_inf, fit.poles, omega)
    n_fit = np.asarray(d.n)
    k_fit = np.asarray(d.k)
    n_data = np.asarray(n_data, dtype=float)
    k_data = np.asarray(k_data, dtype=float)

    warnings_out: list[str] = []
    eps_norm = (float(np.sqrt(np.mean(np.abs((n_data + 1j * k_data) ** 2) ** 2)))
                or 1.0)
    if fit.target_met is False:
        warnings_out.append(
            f"tolerance not met: best weighted RMS = {fit.error:.3g} "\
            f"({fit.error / eps_norm:.1%} of data) with "
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
    scale_n = np.maximum(np.abs(n_data), 1e-12)
    scale_k = np.maximum(np.abs(k_data), 1e-3)
    eps_norm = float(np.sqrt(np.mean(np.abs((n_data + 1j * k_data) ** 2) ** 2))) or 1.0
    quality = {
        "n_points": int(np.size(w)),
        "rms": float(fit.rms if fit.rms is not None else fit.error or 0.0),
        "rms_rel": float((fit.rms if fit.rms is not None else fit.error or 0.0) / eps_norm),
        "tolerance_met": bool(fit.target_met),
        "rel_err_n": float(np.max(np.abs(n_fit - n_data) / scale_n)),
        "rel_err_k": float(np.max(np.abs(k_fit - k_data) / scale_k)),
        "rmse_n": float(np.sqrt(np.mean((n_fit - n_data) ** 2))),
        "rmse_k": float(np.sqrt(np.mean((k_fit - k_data) ** 2))),
        "max_abs_err_n": float(np.max(np.abs(n_fit - n_data))),
        "max_abs_err_k": float(np.max(np.abs(k_fit - k_data))),
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
