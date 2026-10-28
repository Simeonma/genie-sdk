"""genie.material — client-side Drude-Lorentz n-k → pole config fitting.

**三仓唯一拟合实现**（R38 定案时点）：内核只吃 pole（fdtd serve schema
对 from_library 线格式节点显式拒收并指路本模块），内核侧拟合代码已
随 v100 网关退役整体删除（fdtd HEAD baa59ad 亲验零 curve_fit）——
本模块不再是"内核移植"，是拟合正典；数值回归底座 =
``tests/sdk/test_material_fit.py``（Au golden，自回归性质）。

``genie.material.fit_nk(nk, ...)`` = 纯函数：n-k 数据 → config pole
数据（``materials[].poles``）。与 SDK 其余部分同样依赖轻量：
``import genie`` 只拉 numpy；scipy 的 ``curve_fit`` 在 ``fit_nk``
首次真正拟合时才懒加载（浏览器镜像可以不装 scipy 直到调用拟合）。

返回 dict 的单分量（3-tuple）形态被 Studio 本地端点
``/api/tools/material_fit``（studio/server.py）与 GUI 拟合对话框
（matlib.js）直接消费；多分量输入（dict / 7-tuple）的逐分量量成为
按分量名键控的 dict，``warnings`` 折叠为一张表。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Speed of light in vacuum [m/s]（数值史承自内核 C0_PHYS；内核侧已删）。
_C0_SI = 299792458.0
# Quality-table point cap before sampling（形状史承 tools_runner，已退役）。
QUALITY_TABLE_CAP = 1000
# 对照图稠密模型曲线的最少点数（R38b：fit 曲线不被数据点数绑架）。
_CURVE_MIN_POINTS = 200

from ..models import LIBRARY_FIT_DEFAULTS   # 拟合默认值正典（G2 归一）

__all__ = ["fit_nk", "load_library_nk", "list_library_materials"]

# ── 组合根注入点（G1 分层契约，2026-10-11 定案）──────────────────────
# 2026-10-27 G19b 翻转：材质库 HTTP 回退（remote_nk_fetch hook）随协议层
# 手术退役——n-k 库随包分发，load_library_nk 纯本地（未命中即报错）。
# 本模块已无 hook 面。（历史注：simulate 的 expert_factory hook 已随
# R36 专家面退役。）


# ═══════════════════════════════════════════════════════════════════════════
# Dispersion evaluation (pure numpy)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _DispersionResult:
    """Result of dispersion evaluation from Drude-Lorentz pole tuples.

    omega : angular frequencies [rad/s]; n / k : refractive index real /
    extinction coefficient (Re/Im of sqrt(eps)).  eps 本体不留（R38 存在权
    清理：全仓零消费者）。
    """
    omega: np.ndarray
    n: np.ndarray
    k: np.ndarray


def _evaluate_dispersion(eps_inf, poles, omega):
    """eps(ω) = eps_inf + Σ ωp² / (ω0² − ω² − j·γ·ω)  (pure numpy).

    ``poles`` is a list of (omega_0, gamma, omega_p_sq) SI tuples — the
    unified pole convention.  Returns a ``_DispersionResult``.
    """
    eps = np.full_like(omega, float(eps_inf), dtype=complex)
    w2 = omega * omega
    for w0, gm, omega_p_sq in poles:
        eps += omega_p_sq / (w0**2 - w2 - 1j * gm * omega)
    sq = np.sqrt(eps)
    return _DispersionResult(omega=omega, n=sq.real, k=sq.imag)


# ═══════════════════════════════════════════════════════════════════════════
# Drude-Lorentz fitting (log10-parameter space)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _ComponentFit:
    """Single-component fit result: eps_inf, poles (ω0, γ, ωp²) SI tuples,
    converged."""
    eps_inf: float
    poles: list
    converged: bool


def _params_to_poles(params, n_drude, n_lorentz):
    """Unpack log10-space fit params → (ω0, γ, ωp²) SI pole tuples.

    Sole source of the parameter layout, shared by the residual and the result
    assembly (a second bit-identical copy in the kernel is exactly how the
    historical abs(ωp²) sign bug diverged — keep them in lockstep).
    """
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
        poles.append((w0, g, de * w0 * w0))  # ωp² = Δε·ω0²
    return poles


def _dl_residual(omega, eps_inf, n_drude, n_lorentz, *params):
    """Residual for curve_fit (real+imag stacked) using evaluate_dispersion."""
    r = _evaluate_dispersion(eps_inf, _params_to_poles(params, n_drude, n_lorentz),
                             omega)
    return np.concatenate([r.n, r.k])


def _fit_drude_lorentz(wavelengths, n, k, n_drude=0, n_lorentz=2,
                       maxfev=5000):
    """Fit a single component's n-k data (already windowed) to Drude-Lorentz.

    历史上是内核 ``fit_drude_lorentz`` 的忠实移植（内核侧已删，本实现即
    正典）：log10-space params,
    the frequency search box, the eps_inf high-frequency guess (argsort on the
    shortest wavelengths), the Drude low-frequency guess, the Lorentz peak-scan
    (suppress found peaks), clamping of p0 into bounds, and ``curve_fit`` with
    ``bounds``/``maxfev``.  On failure ``popt`` falls back to ``p0`` and
    ``converged`` is False (never raises for non-convergence).

    scipy is imported lazily here — the first time a fit actually runs.
    A missing scipy raises an ImportError with an explicit pointer
    (fit needs scipy; no silent degradation — F1 2026-09 ruling).

    2026-09 audit 批 α：删除了死的 ``eps_inf_guess=None`` 参数——全仓
    零调用方传它（fit_nk 不透传），留着是幽灵 API（反证：grep
    eps_inf_guess 全仓仅签名与分支两处）。
    """
    try:
        from scipy.optimize import curve_fit  # lazy: import genie stays scipy-free
    except ImportError as exc:
        raise ImportError(
            "fitting requires scipy (curve_fit) — install it with: "
            "pip install scipy"
        ) from exc

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

    # Frequency search box: only the LOWER bound adapts to the data span (so
    # sub-16 THz resonances are fittable); the ceiling stays at the historical
    # constants — a higher damping ceiling let the Au 1-Drude fit collapse into
    # a degenerate γ ≈ 4.6e17 pole that the ADE γ·dt guard rejects at build time.
    w_min = float(np.min(omega))
    w_max = float(np.max(omega))
    freq_lo = min(1e10, w_min * 1e-2)
    freq_hi = 1e17
    gamma_hi = 1e16

    high_idx = len(omega) // 8
    if high_idx <= 0:
        high_idx = 1
    # eps_inf is the HIGH-frequency limit: sample the shortest wavelengths
    # (argsort — input order agnostic). The static value eps(0) >= eps_inf,
    # so a low-frequency guess biases eps_inf high.
    order = np.argsort(wavelengths)
    hi_f_idx = order[:high_idx]
    eps_inf_0 = float(np.mean(n[hi_f_idx]**2 - k[hi_f_idx]**2))
    eps_inf_0 = max(eps_inf_0, 1.0)

    p0 = [eps_inf_0]
    bounds_low = [0.1]
    bounds_high = [100.0]
    # curve_fit/least_squares rejects any initial guess outside its bounds, so
    # extend the eps_inf box to contain the guess (the generic clamp below then
    # covers every parameter uniformly).
    if p0[0] > bounds_high[0]:
        bounds_high[0] = max(bounds_high[0], p0[0] * 1.1)
    elif p0[0] < bounds_low[0]:
        bounds_low[0] = min(bounds_low[0], p0[0] * 0.9)

    if n_drude > 0:
        # omega = 2πc/λ is DESCENDING for ascending wavelengths: the LAST
        # quarter holds the lowest frequencies (Drude tail), not the first.
        # eps_low must be sampled from the same low-frequency point.
        low_idx = int(np.argmax(wavelengths))
        w_low = omega[low_idx]
        eps_low = (n[low_idx] + 1j * k[low_idx]) ** 2
        wp_est = np.sqrt(max(-float(eps_low.real), 1.0)) * w_low
        p0 += [np.log10(wp_est), np.log10(1e14)]
        bounds_low += [np.log10(freq_lo), np.log10(min(1e11, freq_lo))]
        bounds_high += [np.log10(freq_hi), np.log10(gamma_hi)]

    eps_complex = (n + 1j * k) ** 2
    eps_imag = np.abs(eps_complex.imag)   # ufunc → fresh array, safe to mute
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

    # Clamp every initial guess into the search box: curve_fit rejects any p0
    # outside its bounds (eps_inf is a linear parameter; the log10-space pole
    # guesses hit the same trap whenever the data's resonances sit outside the
    # default optical band, e.g. THz data).
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
    return _ComponentFit(eps_inf=eps_inf, poles=ade_poles, converged=fit_converged)


# ═══════════════════════════════════════════════════════════════════════════
# Config-node assembly (pure)
# ═══════════════════════════════════════════════════════════════════════════

def _pole_to_node(w0, gamma, omega_p_sq):
    """(ω0, γ, ωp²) SI pole tuple → config PoleSpec node.

    Lorentz: ωp² = Δε·ω0² → delta_eps = ωp²/ω0²; Drude: ω0 == 0 (the kernel's
    unified convention, ``_params_to_poles`` produces exactly 0.0) → omega_p =
    √ωp².
    """
    if w0 == 0.0:
        return {"kind": "drude", "omega_p": float(omega_p_sq) ** 0.5,
                "gamma": float(gamma)}
    return {"kind": "lorentz", "omega_0": float(w0), "gamma": float(gamma),
            "delta_eps": float(omega_p_sq) / float(w0) ** 2}


def _sanity_check_lorentz_node(node):
    """Light stand-in for the kernel's pydantic ``LorentzMatSpec`` / ``PoleSpec``
    validation (the SDK must never import pydantic or the kernel).

    Re-checks the invariants the config consumer relies on, mirroring the
    kernel's guards: type == 'lorentz', non-empty name, eps_inf finite & > 0
    (``_check_finite_pos``), and per-pole gamma present, kind valid, and
    omega_0 / omega_p > 0 (``_check_pos`` — no finiteness guard on poles, and
    negative gamma = gain is accepted, matching the kernel).

    ``node`` is returned on success; ``ValueError`` is raised on any violation.
    """
    if not isinstance(node, dict):
        raise ValueError("material node must be a dict")
    if node.get("type") != "lorentz":
        raise ValueError(f"material node type must be 'lorentz', got {node.get('type')!r}")
    name = node.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("material node needs a non-empty 'name'")
    eps_inf = node.get("eps_inf")
    if eps_inf is None or not (np.isfinite(eps_inf) and eps_inf > 0):
        raise ValueError(f"eps_inf must be finite and > 0, got {eps_inf!r}")
    poles = node.get("poles")
    if not isinstance(poles, list):
        raise ValueError("material node 'poles' must be a list")
    for i, p in enumerate(poles):
        if not isinstance(p, dict):
            raise ValueError(f"pole {i} must be a dict")
        kind = p.get("kind")
        if kind not in ("lorentz", "drude"):
            raise ValueError(f"pole {i} kind must be 'lorentz' or 'drude', got {kind!r}")
        if p.get("gamma") is None:
            raise ValueError(f"pole {i} needs gamma")
        if kind == "lorentz":
            w0 = p.get("omega_0")
            if w0 is None or not (w0 > 0):
                raise ValueError(f"lorentz pole {i} needs omega_0 > 0")
        else:  # drude
            wp = p.get("omega_p")
            if wp is None or not (wp > 0):
                raise ValueError(f"drude pole {i} needs omega_p > 0")
    return node


def _quality_table(w, n_data, k_data, n_fit, k_fit):
    """Fit-quality summary: RMSE / max-abs-error plus per-wavelength tables.

    形状史承 tools_runner._quality_table（已退役）；逐波长表按 cap 降采样
    arrays above ``QUALITY_TABLE_CAP`` points (RMSE is still computed over the
    full set).
    """
    n = len(w)
    idx = (np.arange(n) if n <= QUALITY_TABLE_CAP
           else np.linspace(0, n - 1, QUALITY_TABLE_CAP).astype(int))
    return {
        "n_points": int(n),
        "subsampled": bool(n > QUALITY_TABLE_CAP),
        "rmse_n": float(np.sqrt(np.mean((n_fit - n_data) ** 2))),
        "rmse_k": float(np.sqrt(np.mean((k_fit - k_data) ** 2))),
        "max_abs_err_n": float(np.max(np.abs(n_fit - n_data))),
        "max_abs_err_k": float(np.max(np.abs(k_fit - k_data))),
        "wavelength_m": [float(v) for v in w[idx]],
        "n_data": [float(v) for v in n_data[idx]],
        "n_fit": [float(v) for v in n_fit[idx]],
        "k_data": [float(v) for v in k_data[idx]],
        "k_fit": [float(v) for v in k_fit[idx]],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════════

def _fit_range_bounds(fit_range):
    """Unpack + validate ``fit_range`` (lo < hi) — a reversed range used to
    surface late as a misleading "Only 0 data points" error, so it fails here
    at the cause with the actual bounds."""
    lo, hi = fit_range
    if not lo < hi:
        raise ValueError(
            f"fit_range must satisfy lo < hi [m]; got ({lo!r}, {hi!r}) — "
            "a reversed range selects zero data points (the fit would "
            "otherwise fail later as 'Only 0 data points', far from the "
            "cause)")
    return lo, hi


def _scalar_result(fit, w, n_data, k_data, default_name):
    """Assemble the single-component result dict（Studio 端点/GUI 契约形态）。

    ``fit`` is a ``_ComponentFit``; ``w``/``n_data``/``k_data`` are the masked
    in-window arrays.  The config ``material_node`` is built only when the fit
    converged AND eps_inf > 0.
    """
    omega = 2.0 * np.pi * _C0_SI / w
    d = _evaluate_dispersion(fit.eps_inf, fit.poles, omega)
    n_fit = np.asarray(d.n)
    k_fit = np.asarray(d.k)

    # 稠密模型曲线（站长走查 R38b）：拟合模型是解析的——eps(ω) 在任意
    # 波长可求值，对照图的 fit 曲线不该被数据点数绑架（Au 窗内仅 12 行
    # 时红线曾是 12 段折线，共振形状看不见）。数据跨度上采样
    # max(200, 数据点数) 个点；rmse/max_abs_err 仍在**数据点**上算
    # （拟合质量在数据处评判，稠密曲线不参与）。
    w_curve = np.linspace(float(w.min()), float(w.max()),
                          max(_CURVE_MIN_POINTS, int(w.size)))
    dc = _evaluate_dispersion(fit.eps_inf, fit.poles,
                              2.0 * np.pi * _C0_SI / w_curve)

    warnings: list[str] = []
    if not fit.converged:
        warnings.append(
            "fit did not converge (curve_fit hit the iteration cap and "
            "fell back to the initial guess) — the poles are a best-effort "
            "attempt, not a trustworthy solution; consider narrowing "
            "fit_range, reducing the pole count, or raising maxfev")
    if not (fit.eps_inf > 0):
        warnings.append(
            f"eps_inf={fit.eps_inf!r} <= 0, cannot build a valid config "
            f"node (lorentz materials require eps_inf > 0)")

    poles = [_pole_to_node(*p) for p in fit.poles]
    node = None
    if fit.converged and fit.eps_inf > 0:
        node = {"type": "lorentz",
                "name": default_name,
                "eps_inf": float(fit.eps_inf),
                "poles": poles}
        try:
            _sanity_check_lorentz_node(node)
        except ValueError as e:
            node = None
            warnings.append(f"fit result failed config-node validation: {e}")

    quality = _quality_table(w, n_data, k_data, n_fit, k_fit)
    quality["curve_wavelength_m"] = [float(v) for v in w_curve]
    quality["n_curve"] = [float(v) for v in np.asarray(dc.n)]
    quality["k_curve"] = [float(v) for v in np.asarray(dc.k)]

    return {
        "converged": bool(fit.converged),
        "eps_inf": float(fit.eps_inf),
        "n_poles": len(poles),
        "poles": poles,                      # SI 角频率 rad/s（delta_eps 无量纲）
        "quality": quality,
        "material_node": node,               # 可直接粘进配置 materials 数组
        "warnings": warnings,
    }


# 各向异性组装（Q5=α 裁决 R38）：7 元组输入 = 晶体/液晶三主轴拟合，
# 出口直接给**可入 cfg 的单节点**（不再留 dict-of-dicts 死角——各向异性
# 正确并法 eps_inf 张量 + rank-1 pole 此前无任何工具做）。内核/线格式
# 全部就绪：LorentzMatSpec.eps_inf 支持 3 元对角；PoleSpec 的 rank-1
# （alpha=beta=轴单位向量 = 只在该轴贡献 Δε，内核 _pole.py
# "direction-selective by construction"）。
_ANISO_AXES = {"xx": (1.0, 0.0, 0.0), "yy": (0.0, 1.0, 0.0),
               "zz": (0.0, 0.0, 1.0)}


def _anisotropic_node(per_component, name):
    """把 xx/yy/zz 三份 iso 拟合组装成一个各向异性 lorentz 节点。

    eps_inf = [exx, eyy, ezz]（3 元对角）；每轴每个极点转 rank-1
    （alpha=轴单位向量，beta 不写——线格式语义缺省 beta=alpha）。
    任一轴 material_node 为 None（未收敛/eps_inf≤0/校验失败）→ 返回
    None，原因在该轴已折叠进 warnings 的条目里。dict 输入（自定义
    分量名）不走这里——保持 dict-of-nodes 形态。
    """
    axes = ("xx", "yy", "zz")
    if any(per_component[c]["material_node"] is None for c in axes):
        return None
    poles = []
    for c in axes:
        for p in per_component[c]["poles"]:
            q = dict(p)
            q["alpha"] = list(_ANISO_AXES[c])
            poles.append(q)
    return {"type": "lorentz", "name": name,
            "eps_inf": [per_component[c]["eps_inf"] for c in axes],
            "poles": poles}


def fit_nk(nk, fit_range=LIBRARY_FIT_DEFAULTS["fit_range"],
           n_drude=LIBRARY_FIT_DEFAULTS["n_drude"],
           n_lorentz=LIBRARY_FIT_DEFAULTS["n_lorentz"],
           maxfev=LIBRARY_FIT_DEFAULTS["maxfev"], name=None):
    """Fit n-k data to a Drude-Lorentz model → poles config.

    Parameters
    ----------
    nk : (wavelengths_m, n, k) | dict of component -> (wl, n, k)
         | 7-tuple (wl, nx, kx, ny, ky, nz, kz)
        n-k data in SI [m, dimensionless, dimensionless].  A 3-tuple is a
        single-component isotropic fit; a 7-tuple is an anisotropic xx/yy/zz
        fit; a dict fits arbitrary named components.
    fit_range : tuple[float, float]
        Wavelength window [m].  Must satisfy lo < hi; each component's data is
        masked to (wl >= lo) & (wl <= hi) before fitting, and fewer than 3
        in-window points raises ValueError.
    n_drude : int
        Number of Drude poles (default 0).
    n_lorentz : int
        Number of Lorentz poles (default 2).
    maxfev : int
        Max function evaluations for scipy ``curve_fit``.
    name : str | None
        Name for the config ``material_node`` (defaults to "fitted" for a
        3-tuple, or the component key for multi-component input).

    Returns
    -------
    dict
        ``{converged, eps_inf, n_poles, poles, quality, material_node,
        warnings}``.  For single-component input these match
        Studio 本地端点 ``/api/tools/material_fit`` 与 GUI 对话框直接消费 —
        ``poles`` is a list of config poles (``{kind, omega_p|omega_0, gamma,
        delta_eps?}``) and ``material_node`` is a config ``lorentz`` node or
        None.  For dict / 7-tuple input the fit detail
        (``converged``/``eps_inf``/``n_poles``/``poles``/``quality``) is a
        dict keyed by component and ``warnings`` is one aggregated list;
        ``material_node`` differs by shape (Q5=α, R38): a **7-tuple**
        (anisotropic xx/yy/zz) yields ONE assembled lorentz node with a
        3-element diagonal ``eps_inf`` and per-axis rank-1 poles
        (``alpha`` = axis unit vector; ``beta`` defaults to ``alpha``
        per wire semantics) — ready for the config's ``materials`` array;
        a **dict** (custom component names, not assumed to be axes) keeps
        ``{component: node}``.

    Raises
    ------
    ValueError
        If ``fit_range`` is reversed (lo >= hi), ``nk`` is not a recognized
        shape, a component is not a (wl, n, k) 3-tuple, the three columns
        have unequal lengths, fewer than 3 points fall inside the fit
        window, or an in-window n/k value is non-finite (G86 failure
        taxonomy: bad input data raises with a data-cleaning prescription;
        only a genuine iteration-cap hit reports the raise-maxfev
        prescription via ``warnings``).
    """
    lo, hi = _fit_range_bounds(fit_range)
    # Normalize every input shape to {component: (wl, n, k)}; the scalar-vs-dict
    # result shape follows the INPUT TYPE (a user dict containing '' must stay
    # dict-shaped, hence the isinstance guard).
    is_scalar = not isinstance(nk, dict) and len(nk) == 3
    is_aniso = not isinstance(nk, dict) and len(nk) == 7   # Q5=α：出口组装
    if isinstance(nk, dict):
        comps = dict(nk)
    elif is_scalar:
        comps = {'': nk}
    elif is_aniso:
        wl_m, nx, kx, ny, ky, nz, kz = nk
        comps = {'xx': (wl_m, nx, kx), 'yy': (wl_m, ny, ky), 'zz': (wl_m, nz, kz)}
    else:
        raise ValueError(
            "nk must be 3-tuple, 7-tuple, or dict of component -> (wl, n, k)")

    fits: dict = {}
    fit_data: dict = {}
    for comp, comp_data in comps.items():
        try:
            wl, n_arr, k_arr = comp_data
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"nk component {comp!r} must be a (wl, n, k) 3-tuple, got "
                f"{type(comp_data).__name__}: {exc}") from exc
        wl = np.asarray(wl, dtype=float)
        n_arr = np.asarray(n_arr, dtype=float)
        k_arr = np.asarray(k_arr, dtype=float)
        if not (wl.size == n_arr.size == k_arr.size):
            raise ValueError(
                f"nk component {comp!r} columns must have equal lengths, "
                f"got wl={wl.size}, n={n_arr.size}, k={k_arr.size} — "
                f"boolean masking of mismatched columns would crash with a "
                f"bare IndexError far from the cause")
        mask = (wl >= lo) & (wl <= hi)
        n_in = int(mask.sum())
        if n_in < 3:
            raise ValueError(
                f"Only {n_in} data points in fit range "
                f"{lo * 1e9:.0f}-{hi * 1e9:.0f} nm for nk component "
                f"{comp!r} — widen fit_range or supply more data inside "
                f"the window")
        # G86（R38）：非有限 n/k 在窗内 = 输入数据问题，必须在拟合前以
        # 数据清洗处方显式报错——curve_fit 对 NaN/inf 的 ValueError 与
        # "撞迭代上限"的 RuntimeError 历史上被压成同一句 maxfev 处方，
        # 三种完全不同的失败拿到同一份无效建议（NaN p0 甚至静默出垃圾）。
        # 只查窗内：窗外脏行不进拟合，拦它是家长式（§5 口径——只拦会崩的）。
        for col_label, col in (("n", n_arr), ("k", k_arr)):
            bad = mask & ~np.isfinite(col)
            if bad.any():
                rows = np.flatnonzero(bad)
                raise ValueError(
                    f"nk component {comp!r} column {col_label!r} has "
                    f"{len(rows)} non-finite value(s) inside fit range "
                    f"{lo * 1e9:.0f}-{hi * 1e9:.0f} nm (first at data row "
                    f"{int(rows[0])} of {wl.size}) — clean the data (drop "
                    f"or fix those rows; JSON null becomes NaN here) or "
                    f"use library data via genie.material.load_library_nk; "
                    f"non-finite values cannot be fitted")
        fits[comp] = _fit_drude_lorentz(
            wl[mask], n_arr[mask], k_arr[mask],
            n_drude=n_drude, n_lorentz=n_lorentz, maxfev=maxfev)
        fit_data[comp] = (wl[mask], n_arr[mask], k_arr[mask])

    if is_scalar:
        w, nd, kd = fit_data['']
        return _scalar_result(fits[''], w, nd, kd, name or "fitted")

    out = {c: _scalar_result(fits[c], *fit_data[c], name or c)
           for c in comps}
    result = {
        "converged": {c: o["converged"] for c, o in out.items()},
        "eps_inf": {c: o["eps_inf"] for c, o in out.items()},
        "n_poles": {c: o["n_poles"] for c, o in out.items()},
        "poles": {c: o["poles"] for c, o in out.items()},
        "quality": {c: o["quality"] for c, o in out.items()},
        "material_node": {c: o["material_node"] for c, o in out.items()},
        "warnings": [warn for o in out.values() for warn in o["warnings"]],
    }
    # Q5=α（R38）：7 元组（xx/yy/zz 三主轴）出口组装为**单个各向异性
    # 节点**（eps_inf 3 元对角 + 每轴极点转 rank-1）——可整份粘进 cfg 的
    # materials 数组；拟合明细（converged/eps_inf/poles/quality）仍按
    # 分量键控。dict 输入保持 dict-of-nodes（自定义分量名不假定是轴）。
    if is_aniso:
        result["material_node"] = _anisotropic_node(out, name or "fitted")
    return result


def load_library_nk(name):
    """取材质库某名的原始 n-k 数据 → (wl_m, n, k)。

    2026-10-27 起**纯本地**（G19b 翻转：材质库回归 SDK，网关 tools 面
    随协议层手术撤除，HTTP 回退与 allow_remote/base_url 等参数一并
    退役）：n-k 库随包分发（``genie/core/nkdata/``，迁移时与内核库逐字节
    一致；内核侧副本已随其拟合面删除），
    本地未命中直接报错并列出查询入口。永不 import fdtd。

    Parameters
    ----------
    name : str
        库名（如 ``'Au'``）。

    Returns
    -------
    (wl_m, n, k) : tuple[np.ndarray, np.ndarray, np.ndarray]
        波长（米）、折射率实部 n、消光系数 k（与 ``fit_nk`` 的 3-tuple
        输入约定一致；波长 µm→m 已换算）。

    Raises
    ------
    ValueError
        库名为空，或本地未命中（可用 genie.material.list_library_materials()
        查全部本地库名）。
    """
    from .nkdata import load_nk_data

    if not isinstance(name, str) or not name:
        raise ValueError(
            "load_library_nk(name) requires a non-empty string library "
            "name")
    data = load_nk_data(name)
    if data is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"genie.material.list_library_materials() lists all local "
            f"library names")
    wl_um, n, k = data
    return (np.asarray(wl_um, dtype=float) * 1e-6,
            np.asarray(n, dtype=float), np.asarray(k, dtype=float))


def list_library_materials():
    """列出本地打包 n-k 库的全部库名（sorted，F1 随迁后离线可用）。"""
    from .nkdata import available_materials

    return available_materials()
