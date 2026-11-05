"""genie.core.material — 材料拟合（fit_nk）+ 通用组装（assemble_nk）
+ 本地 n-k 库加载。

契约 → ``MODMAP.md``（批 9c，2026-11-05 审毕 + 同日**二拍改案**）：

- 公开四口（挂 f 的名字 = Q1.5）：``fit_nk``（**纯标量纯数值**：单
  分量 nk → Drude-Lorentz 拟合数值，六键）/ ``assemble_nk``（**唯一
  节点工厂**：逐分量结果 → 单节点）/ ``load_nk``（库名 → 原始 n-k
  数据）/ ``list_library_materials``（库名清单）。
- **二拍 + 三拍（站长改案）**：fit_nk 一次一个分量——通用性靠**组合**
  （各向同性 / 各向异性 / 全张量 = 逐张量元素分别拟合再 assemble），
  3/7/dict 输入形态退役，返回类型永远稳定一种；材料节点**唯一工厂 =
  assemble_nk**（fit 不产 material_node / 不收 name）；iso 亦统一经
  assemble——三对角同 fit = 三根同参数轴向 rank-1（各向同性显式三向
  表达；内核对 alpha-less iso 同样经 _iso_expand 三向展开，两路物理
  等价，V100 genie-fdtd ``b84485d`` 查证，图 §0）；非对角上三角一名
  **镜像 mount** 保对称。
- material_node 校验 = 真 protocol（``LorentzMatSpec.model_validate``，
  新拍 4）；不带 color（新拍 2）/ id（发号器的事）。
- quality 五标量（新拍 3）；失败分类学 G86 全带（推论 C）：窗内非有限
  = ValueError + 清洗处方，撞迭代上限 = converged=False + 迭代处方。
- 报错文案一律英文（新拍 6 补拍）。
- 数据文件 = ``genie-sdk/resources/materials/*.yml``（37 份，v1 逐字节
  搬迁；refractiveindex.info 格式，CC0）——``__file__`` 相对定位（新拍
  7：包内自带数据，非外部路径，core 三零不破）；解析语义逐条保留
  v1（tabulated DATA 块 + formula 1/4 两路、坏行 warning 跳过不截断、
  非有限行丢弃、≥5 行下限）。
- scipy 懒加载（推论 A）：首次真正拟合才 import；缺了 ImportError
  明报，不静默降级（v1 F1 裁决沿用）。
- v1 只参考不照抄（core 图禁令 1）：砍掉的是 GUI 口粮（逐波长表 /
  稠密曲线 / 显示色表），算法与数值语义逐条保留。
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

#: 光速 [m/s]（数值史承内核 C0_PHYS；内核侧拟合面已随 v2 手术删除，
#: 本模块是拟合正典——core 图 §4）。
_C0_SI = 299792458.0

#: 库数据目录（新拍 7：包内自带数据，``__file__`` 相对——core/material/
#: 往上三级 = 包根 genie-sdk/；非外部路径，三零不破）。
_DATA_DIR = Path(__file__).resolve().parents[2] / "resources" / "materials"


# ═══════════════════════════════════════════════════════════════════════════
# 库加载（load_nk / list_library_materials）
# ═══════════════════════════════════════════════════════════════════════════

def _check_material_name(name) -> None:
    """名字守卫：库名与文件 1:1——路径分隔符 / `.` / `..` / 空 / 非字符串
    在门口拒（v1 语义保留；报错英文）。"""
    if (not isinstance(name, str) or not name
            or "/" in name or "\\" in name or name in (".", "..")):
        raise ValueError(
            f"load_nk(name) requires a bare material name (no path "
            f"separators), got {name!r}")


def list_library_materials() -> list[str]:
    """本地打包 n-k 库全部库名（sorted；离线可用）。"""
    try:
        files = list(_DATA_DIR.glob("*.yml"))
    except OSError:
        return []
    return sorted(f.stem for f in files)


def _scan_entries(raw_text: str, src: str = "") -> list[dict]:
    """RII yml → 数据条目清单（四拍② 多块化：v1 修前**静默丢第二块**）。

    每条 ``{type, direction, wavelength_range, coefficients, data_lines}``；
    ``direction`` 是我们对 RII 页格式的扩展键（o / e / x / y / z），
    无标注 = iso 单分量。坏行（数值形但缺列 / 杂 token）计数 + warning
    跳过、不截断块（v1 语义保留）。
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
                # 裸块式（v1 自产文件 Ag/Si/SiO2：顶层 ``DATA: |``、无
                # ``- type:`` 条目结构）→ 隐式 iso 表格条目。
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
            # 1) 数值行：字符类含 '-'，"-0.5 2 0"（负首列无空格）是数据
            if re.match(r"^[\d.eE+\-]+\s+[\d.eE+\-]+\s+[\d.eE+\-]+", s):
                if cur is not None:
                    cur["data_lines"].append(s)
            # 2) 下一条目（dash 开头）结束本块——先于坏行分支检查
            elif re.match(r"^-\s|^-$", s):
                in_data = False
            # 3) 数值形但畸形（缺列 / 杂 token）：跳过 + warning，不截断
            elif re.match(r"^[\d.eE+\-]", s):
                bad_rows.append((lineno, s))
            # 4) 下一个 YAML 键 = DATA 块结束
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
                    pass            # 坏 range 留 None → formula 走缺省窗
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
    """单数据条目 → (wl_um, n, k)；解析不出 → ValueError 带条目上下文。

    tabulated（``data_lines``）：≥5 有效行下限、非有限行丢弃 +
    warning（``1e999`` 溢出 inf 会毒化拟合）。formula（formula 1
    Sellmeier / formula 4 extended）：缺省窗 0.2–2.0µm、500 采样点
    （v1 语义保留）。
    """
    if entry["saw_data"]:
        if len(entry["data_lines"]) < 5:
            raise ValueError(
                f"materials file exists but could not be parsed: {src} "
                f"(tabulated block, direction {entry['direction']!r}, "
                f"has <5 valid data rows)")
        wl, nv, kv, overflow = [], [], [], []
        for dl in entry["data_lines"]:
            parts = dl.split()               # 行正则保证 ≥3 token
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
        # Sellmeier: n² = A + Σ Bᵢ·λ²/(λ² − Cᵢ²)；系数 "0 B₁ C₁ …" 时 A=1。
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
        # Extended Sellmeier（ZnO 型）：n² = A + B₁·λ^p₁/(λ² − C₁^p₂)
        # + Σ P·λ^p（coeffs[8]==1 标记多项式修正对）。
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
    """库名 → ``{分量: (wl_um, n, k)}``；**缺文件 = None，文件坏 =
    ValueError**（两种失败分开报——新拍 6）。iso 材料 = 单键 ``''``；
    复合材料键 = direction（o/e/x/y/z，四拍②）。"""
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
    """(wl_um, n, k) → (wl_m, n, k)（µm→m 换算在此，单一地址）。"""
    wl_um, n, k = arr
    return (np.asarray(wl_um, dtype=float) * 1e-6,
            np.asarray(n, dtype=float), np.asarray(k, dtype=float))


def load_nk(name, component=None):
    """库名 → ``(wl_m, n, k)`` 原始 n-k 数据（**单直多报**，四拍③）。

    iso 材料（单数据块）直取三元组、``component`` 必须为 None；复合材料
    （多块）**必须点名分量** ``load_nk('ZnO', 'e')``——缺省报错列出
    可选，不猜默认（Q4.5 同款精神）。

    Raises
    ------
    ValueError
        名字不是裸材料名；本地未命中（指路
        :func:`list_library_materials`）；文件在但解析不出（带路径）；
        iso 材料点名分量 / 复合材料未点名或点错名（列出可选）。文案
        一律英文。
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
# Drude-Lorentz 拟合（log10 参数空间；搜索盒 / 初值策略 v1 逐条保留）
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _DispersionResult:
    """eps(ω) 求值结果：ω [rad/s]；n/k = sqrt(eps) 实/虚部。"""
    omega: np.ndarray
    n: np.ndarray
    k: np.ndarray


def _evaluate_dispersion(eps_inf, poles, omega):
    """eps(ω) = eps_inf + Σ ωp²/(ω0² − ω² − j·γ·ω)（纯 numpy）。
    ``poles`` = (ω0, γ, ωp²) SI 三元组清单（统一 pole 约定）。"""
    eps = np.full_like(omega, float(eps_inf), dtype=complex)
    w2 = omega * omega
    for w0, gm, omega_p_sq in poles:
        eps += omega_p_sq / (w0**2 - w2 - 1j * gm * omega)
    sq = np.sqrt(eps)
    return _DispersionResult(omega=omega, n=sq.real, k=sq.imag)


@dataclass
class _ComponentFit:
    """单分量拟合：eps_inf、poles (ω0, γ, ωp²) SI 三元组、收敛旗。"""
    eps_inf: float
    poles: list
    converged: bool


def _params_to_poles(params, n_drude, n_lorentz):
    """log10 空间拟合参数 → (ω0, γ, ωp²) SI 三元组。

    参数布局的唯一来源（残差与结果组装共用）——历史上内核侧第二份
    逐位拷贝的漂移就是 abs(ωp²) 符号 bug 的来源，保持单份。"""
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
    """curve_fit 残差（n/k 实虚堆叠）。"""
    r = _evaluate_dispersion(eps_inf, _params_to_poles(params, n_drude,
                                                       n_lorentz), omega)
    return np.concatenate([r.n, r.k])


def _fit_drude_lorentz(wavelengths, n, k, n_drude=0, n_lorentz=2,
                       maxfev=5000):
    """单分量 n-k 数据（已限窗）→ Drude-Lorentz 拟合。

    策略 v1 逐条保留：log10 空间参数；频率搜索盒只有**下限**随数据
    跨度自适应（亚 16 THz 共振可拟合）、上限钉历史常数（更高的阻尼
    上限会让 Au 1-Drude 拟合塌缩成 ADE γ·dt 守卫拒收的退化极点）；
    eps_inf 高频估计（argsort 最短波长前 1/8）、Drude 低频尾估计
    （ω = 2πc/λ 对升序 λ **递减**——低频在最后一季）、Lorentz 峰扫描
    压制（找到的峰置零找下一个）、p0 全量钳进盒（curve_fit 拒绝界外
    初值）、失败回退 p0 不抛（converged=False）。scipy 懒加载：缺了
    ImportError 明报（推论 A），不静默降级。
    """
    try:
        from scipy.optimize import curve_fit    # lazy：import genie 无 scipy
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
    # eps_inf 是高频极限：取最短波长（argsort，输入序无关）前 1/8 估
    # n²−k²——静态值 eps(0) >= eps_inf，低频估计会把 eps_inf 推高。
    order = np.argsort(wavelengths)
    hi_f_idx = order[:high_idx]
    eps_inf_0 = max(float(np.mean(n[hi_f_idx]**2 - k[hi_f_idx]**2)), 1.0)

    p0 = [eps_inf_0]
    # eps_inf 盒下限 = 1.0（物理硬约束：因果介质 ε(∞) ≥ 1，真空极限
    # 为 1——v1 的 0.1 下限允许非物理分解，是介质材料 eps_inf 撞下界
    # 退化的根因，四拍补记修）。
    bounds_low = [1.0]
    bounds_high = [100.0]
    # curve_fit/least_squares 拒绝任何界外初值：先把 eps_inf 盒扩到含
    # 初值（下方通用钳制再统一兜底）。
    if p0[0] > bounds_high[0]:
        bounds_high[0] = max(bounds_high[0], p0[0] * 1.1)
    elif p0[0] < bounds_low[0]:
        bounds_low[0] = min(bounds_low[0], p0[0] * 0.9)

    if n_drude > 0:
        # 低频尾：升序 λ 下 ω 递减，最低频在 argmax(λ) 不在首季。
        low_idx = int(np.argmax(wavelengths))
        w_low = omega[low_idx]
        eps_low = (n[low_idx] + 1j * k[low_idx]) ** 2
        wp_est = np.sqrt(max(-float(eps_low.real), 1.0)) * w_low
        p0 += [np.log10(wp_est), np.log10(1e14)]
        bounds_low += [np.log10(freq_lo), np.log10(min(1e11, freq_lo))]
        bounds_high += [np.log10(freq_hi), np.log10(gamma_hi)]

    eps_complex = (n + 1j * k) ** 2
    eps_imag = np.abs(eps_complex.imag)          # ufunc → 新数组，可静音
    for _ in range(n_lorentz):
        peak_idx = np.argmax(eps_imag)
        w0_est = omega[peak_idx]
        de_est = max(np.abs(eps_complex.imag[peak_idx]) * 2, 0.1)
        g_est = w0_est * 0.1
        p0 += [np.log10(de_est), np.log10(w0_est), np.log10(g_est)]
        bounds_low += [np.log10(1e-3), np.log10(freq_lo),
                       np.log10(min(1e10, freq_lo))]
        bounds_high += [np.log10(1e4), np.log10(freq_hi), np.log10(gamma_hi)]
        # 压制本峰，下一轮找不同的峰。
        half_width = max(int(len(omega) * 0.05), 3)
        lo = max(0, peak_idx - half_width)
        hi = min(len(eps_imag), peak_idx + half_width + 1)
        eps_imag[lo:hi] = 0.0

    ydata = np.concatenate([n, k])

    # 全量钳制：任何界外 p0 都会被 curve_fit 拒（数据共振落在默认光学
    # 带外时，log10 空间初值同样踩坑，如 THz 数据）。
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
# 结果组装（纯）
# ═══════════════════════════════════════════════════════════════════════════

def _pole_to_node(w0, gamma, omega_p_sq):
    """(ω0, γ, ωp²) SI 三元组 → wire PoleSpec 形 dict。

    Lorentz：ωp² = Δε·ω0² → delta_eps = ωp²/ω0²；Drude：ω0 == 0
    （统一约定，``_params_to_poles`` 产出恰为 0.0）→ omega_p = √ωp²。
    """
    if w0 == 0.0:
        return {"kind": "drude", "omega_p": float(omega_p_sq) ** 0.5,
                "gamma": float(gamma)}
    return {"kind": "lorentz", "omega_0": float(w0), "gamma": float(gamma),
            "delta_eps": float(omega_p_sq) / float(w0) ** 2}


def _fit_range_bounds(fit_range):
    """拆包 + 校验 ``fit_range``（lo < hi）——反着的窗会在晚处报成误导
    性的「Only 0 data points」，在因上拦并给出实际区间。"""
    lo, hi = fit_range
    if not lo < hi:
        raise ValueError(
            f"fit_range must satisfy lo < hi [m]; got ({lo!r}, {hi!r}) — "
            "a reversed range selects zero data points (the fit would "
            "otherwise fail later as 'Only 0 data points', far from the "
            "cause)")
    return lo, hi


def _scalar_result(fit, w, n_data, k_data):
    """单分量结果组装（三拍：**纯数值**——六键，无 material_node /
    name；材料节点唯一工厂 = :func:`assemble_nk`）。quality 五标量在
    **数据点**上算（新拍 3：无表、无曲线）。"""
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
        "poles": poles,                      # SI 角频率 rad/s（Δε 无量纲）
        "quality": quality,
        "warnings": warnings_out,
    }


# 张量组装（二拍：通用组装）。rank-1 语义 = 内核 ADE VectorPole 原生
# 形态（``D += β·P``、``P ∝ α·F``——V100 genie-fdtd ``b84485d`` 查证，
# 图 §0）：对角 mount α=e_i（beta 不写 = 线格式缺省复制 α）；非对角
# **镜像 mount**（α=e_i,β=e_j 与 α=e_j,β=e_i 成对）——单根 α≠β 并矢
# 非对称，成对相加才是对称张量元，互易介质（对称 ε）由构造保证。
_ELEM_AXES = {"xx": (0, 0), "yy": (1, 1), "zz": (2, 2),
              "xy": (0, 1), "xz": (0, 2), "yz": (1, 2)}
_EYE = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_DIAG = ("xx", "yy", "zz")
_OFFDIAG = ("xy", "xz", "yz")


def assemble_nk(fits, name=None):
    """逐分量 ``fit_nk`` 结果 → **单个** lorentz 材料节点（**唯一节点
    工厂**，三拍）。

    Parameters
    ----------
    fits : dict
        键 = 张量元素**上三角六名**（``xx`` / ``yy`` / ``zz`` / ``xy`` /
        ``xz`` / ``yz``），值 = ``fit_nk`` 的返回 dict。三个对角必给
        ——**iso 也走这里**：三对角给同一份 fit = 三根同参数轴向
        rank-1 pole（各向同性的显式三向表达；内核对 alpha-less iso
        同样三向展开，两路物理等价）；非对角给上三角一名即可，组装时
        **镜像 mount**（α/β 成对）保证对称。
    name : str | None
        节点名（缺省 ``"assembled"``）。

    Returns
    -------
    dict
        lorentz 节点（对角-only → ``eps_inf`` 三元对角；含非对角 →
        3×3 全张量）；已过 ``LorentzMatSpec`` 校验。

    Raises
    ------
    ValueError
        ``fits`` 非法（非 dict / 空键 / 坏键名——下三角 yx/zx/zy 不收，
        对称材料镜像自动做 / 缺对角 / 分量不可组装——未收敛或
        eps_inf≤0，点名分量）。文案一律英文。
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
    for c in _DIAG:                      # 对角 mount：α=e_i，beta 缺省=α
        for p in fits[c]["poles"]:
            q = dict(p)
            q["alpha"] = list(_EYE[_ELEM_AXES[c][0]])
            poles.append(q)
    off = [c for c in _OFFDIAG if c in fits]
    for c in off:                        # 非对角：镜像 mount 成对（对称）
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
    _wire.LorentzMatSpec.model_validate(node)   # 显式动词：校验不过直接抛
    return node


# ═══════════════════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════════════════

def fit_nk(wl, n, k, fit_range=(400e-9, 800e-9), n_drude=0, n_lorentz=2,
           maxfev=5000):
    """**单分量** n-k 数据 → Drude-Lorentz pole 拟合（纯标量纯数值，
    二拍 + 三拍）。

    一次拟合一个分量，**只出数值**（六键，无 material_node / name）；
    材料节点唯一工厂 = :func:`assemble_nk`——iso 也走组装（三对角
    同 fit = 显式三向 mount）。

    Parameters
    ----------
    wl, n, k : array_like
        单分量数据，SI [m, 无量纲, 无量纲]。
    fit_range : tuple[float, float]
        拟合窗 [m]（lo < hi）；数据先掩到窗内，窗内 <3 点报错。
    n_drude / n_lorentz : int
        Drude / Lorentz 极点数（缺省 0 + 2）。
    maxfev : int
        scipy ``curve_fit`` 最大函数评估数。

    Returns
    -------
    dict
        ``{converged, eps_inf, n_poles, poles, quality, warnings}``——
        **形状永远这一种**（二拍：无多分量分支；三拍：无节点分支）。
        ``poles`` 为 wire-shaped dict（``assemble_nk`` 直接吃）。

    Raises
    ------
    ValueError
        fit_range 反转；三列不等长；窗内 <3 点；窗内 n/k 非有限
        （G86：脏数据给**清洗处方**，与撞迭代上限的调参处方分开）。
    ImportError
        scipy 未安装（懒加载，首次拟合才需要；明报不静默）。
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
    # G86：窗内非有限 = 输入数据问题，拟合前给**数据清洗处方**报错
    # ——curve_fit 对 NaN/inf 的 ValueError 与「撞迭代上限」的
    # RuntimeError 历史上被压成同一句调参处方，三种完全不同的失败
    # 拿到同一份无效建议（NaN p0 甚至静默出垃圾）。只查窗内：窗外
    # 脏行不进拟合，拦它是家长式。
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
# 便利层（四拍①：一条龙，零旋钮，不碰模型）
# ═══════════════════════════════════════════════════════════════════════════

#: 分量 → 轴 的物理映射（四拍④：**唯一地址**；单轴晶体光轴沿 z 惯例
#: ——o 光垂直光轴（xx=yy）、e 光沿光轴（zz）；双轴将来 x/y/z。换
#: 轴向 = 材料取向题，已挂账 material 图 §7）。
_COMPONENT_AXES = {"o": ("xx", "yy"), "e": ("zz",),
                   "x": ("xx",), "y": ("yy",), "z": ("zz",)}


def _fit_convenience(wl, n, k):
    """便利层窗规则（四拍补记）：先**缺省窗**（400–800nm ∩ 数据域
    ——v1 语义、与 golden 同源，金属 / 常规介质在此窗内信息足够且
    拟合最优）；curve_fit **不收敛**才用**数据全域重试一次**（浅色散
    + 窗内点稀的介质，如 ZnO-e 表格窗内仅 10 点；全域色散信息全、
    必收敛，且窗内精度不低于全域 rmse 上界）。判据 = curve_fit 的
    收敛旗，无魔法阈值。"""
    lo = max(400e-9, float(np.min(wl)))
    hi = min(800e-9, float(np.max(wl)))
    if lo < hi:
        fit = fit_nk(wl, n, k, fit_range=(lo, hi))
        if fit["converged"]:
            return fit
    return fit_nk(wl, n, k,
                  fit_range=(float(np.min(wl)), float(np.max(wl))))


def mat_from_nk(name):
    """库名 → 材料节点**一条龙**（读库 → 按分量逐轴 ``fit_nk`` →
    ``assemble_nk``；四拍①）。

    零旋钮便利层：默认拟合参数（400–800nm / 0+2 极点）不可调、质量
    不可见——要控制走原语层（``load_nk`` / ``fit_nk`` /
    ``assemble_nk``）。**不碰模型**（入模一行 =
    ``f.mat.add_lorentz(node)``，Q1.4 窄例外）；节点名缺省 = 库名。
    iso 材料拟合一次、三轴同装（三拍）；复合材料按 ``_COMPONENT_AXES``
    分轴（o→xx,yy、e→zz），分量不全 → 报错列出库里有什么。

    Raises
    ------
    ValueError
        库名未命中（指路 :func:`list_library_materials`）；分量方向
        不认识；分量不全（缺轴）；任一分量拟合不可组装（未收敛 /
        eps_inf≤0——assemble 点名）。文案一律英文。
    """
    comps = _load_raw(name)
    if comps is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"f.list_library_materials() lists all local library names")
    if set(comps) == {""}:                       # iso：拟合一次，三轴同装
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
