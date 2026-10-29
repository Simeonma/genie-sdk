"""genie.core.nkdata — 自有 n-k 材质库加载（F1 随 fit 能力迁入服务仓；
阶段 ④ 自顶层 ``_nkdata.py``+``genie/nkdata/`` 并入本包——加载器与数据同包）。

Port of the kernel's ``fdtd/_material/nkdata/__init__.py`` (REFERENCE
ONLY, never import the kernel): tabulated ``DATA:`` blocks and
formula-based (Sellmeier / extended Sellmeier) refractiveindex.info
yml files, plus the catalogue listing.  The ``.yml`` data files live in
this package (``genie/core/nkdata/``, 37 materials; migrated byte-identical
from the kernel library at migration time — the kernel-side copy has since
been removed along with its fitting face) so the SDK is offline-capable.
There is **no HTTP fallback** (G19b, 2026-10-27): a name absent locally
simply raises.

Semantics preserved from the kernel port (regressions live in
``tests/sdk/test_material_fit.py`` + the tools tests in
``test_studio_server.py``):
- ``load_nk_data(name)`` → ``(wl_um, n, k)`` or ``None`` when no FILE
  exists; ``ValueError`` when the file exists but cannot be parsed
  ("missing" vs "broken" have different remedies and must not share
  one silent None);
- malformed numeric rows inside a DATA block are skipped with a
  warning instead of silently truncating the block; non-finite rows
  (``1e999``) are dropped the same way;
- material names map 1:1 onto files inside ``nkdata/`` — path
  separators and ``.``/``..`` are rejected up front.
"""
from __future__ import annotations

import math
import os
import re
import warnings
from pathlib import Path

import numpy as np

_NKDATA_DIR = Path(__file__).resolve().parent   # 进包后 yml 与本文件同包（阶段 ④ 搬家）

__all__ = ["load_nk_data", "available_materials", "material_color",
           "MATERIAL_COLORS"]

#: 材料库预设显示色（库名 → "#RRGGBB"）。
#: **只是显示**（wire 的 MaterialSpec.color），不影响物理；用户在表单
#: "显示色"里可改、点"自动"回到按名散列。纪律与 SCENE_PALETTE 同一
#: 明度带（中明度、低饱和）：金属给特征色相（金/铜/银），半导体偏石板
#: 蓝，介质偏浅冷灰。缺表项 → 前端按名散列（行为不变）。
MATERIAL_COLORS = {
    # ── 金属 ──
    "Au": "#C9A227",   # 金
    "Ag": "#C0C6CC",   # 银
    "Al": "#B8C0C8",   # 铝
    "Cu": "#B87333",   # 铜
    "Pt": "#B2B8BE", "Pd": "#AEB6BE", "Rh": "#C6CCD2",
    "Ti": "#8E99A6", "Cr": "#9AA4AE", "Mo": "#8A929C", "W": "#808A94",
    "Ta": "#8C96A0", "Ni": "#A9B2BA", "Co": "#8FA0B6", "Fe": "#8B96A1",
    # ── 半导体 ──
    "Si": "#6E7C96", "Ge": "#7A8493", "SiC": "#7E8C84",
    "GaAs": "#7C8FA6", "GaN": "#8FA9C4", "GaP": "#86A88F", "GaSb": "#7E8A9E",
    "InP": "#8AA3B8", "InAs": "#7E93A8", "InSb": "#74839A",
    "CdTe": "#8B8FA8", "AlSb": "#7F8C9E", "ZnO": "#9FB6C8", "ZnSe": "#9AA8BE",
    "Si3N4": "#97A3B0",
    # ── 介质 / 氧化物 ──
    "SiO2": "#A9C7E8", "Al2O3": "#B4C3D2", "HfO2": "#A8B6C4",
    "TiO2": "#B9C0D0", "Nb2O5": "#B0B8CC",
    "MgF2": "#C6D3DD", "CaF2": "#CFDAE2",
}


def material_color(name):
    """库名 → 预设显示色（无此项返回 None；调用方退回按名散列）。"""
    return MATERIAL_COLORS.get(str(name or ""))


def _check_material_name(material_name):
    """Reject path separators: names map 1:1 onto files inside nkdata/."""
    if (not isinstance(material_name, str) or not material_name
            or '/' in material_name or '\\' in material_name
            or material_name in ('.', '..')):
        raise ValueError(
            f"material_name must be a bare file name inside nkdata/, "
            f"got {material_name!r}")


def available_materials():
    """List all materials with raw nkdata (.yml) available locally.

    2026-09 audit 批 α：删除了 ``not f.endswith('.material.yml')``
    排除子句——本仓随迁库（37 个 yml，迁移时与内核库逐字节一致）不
    含任何 ``*.material.yml`` 文件，该子句恒真（反证：``ls
    genie/nkdata/*.material.yml`` 零命中），是死条件。
    """
    try:
        files = os.listdir(_NKDATA_DIR)
    except FileNotFoundError:
        return []
    return sorted(f[:-4] for f in files if f.endswith('.yml'))


def _parse_rii_formula(raw_text):
    """Parse a refractiveindex.info YAML and evaluate formula-based dispersion.
    Returns (wl_um, n, k) arrays or None if not applicable."""
    lines = raw_text.split('\n')

    # Detect formula type
    formula_type = None
    coeffs_str = None
    wl_range = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = re.match(r'-?\s*type:\s*(.+)', s)
        if m:
            formula_type = m.group(1).strip()
        m = re.match(r'-?\s*coefficients:\s*(.+)', s)
        if m:
            coeffs_str = m.group(1).strip()
        m = re.match(r'-?\s*wavelength_range:\s*(.+)', s)
        if m:
            parts = m.group(1).split()
            if len(parts) >= 2:
                try:
                    wl_range = (float(parts[0]), float(parts[1]))
                except ValueError as exc:
                    raise ValueError(
                        f"bad wavelength_range tokens {parts[:2]!r}: {exc}"
                    ) from exc

    if not formula_type or not coeffs_str:
        return None

    try:
        coeffs = [float(v) for v in coeffs_str.split()]
    except ValueError as exc:
        raise ValueError(
            f"bad coefficients string {coeffs_str!r}: {exc}") from exc

    # Default range if not specified
    if wl_range is None:
        wl_range = (0.2, 2.0)

    # Generate evaluation wavelengths (500 points over the range)
    wl_um = np.linspace(wl_range[0], wl_range[1], 500)

    if formula_type.startswith('formula 1'):
        # Sellmeier: n² = A + Σ Bᵢ·λ²/(λ² - Cᵢ²)
        # coefficients are "0 B₁ C₁ ..." with implicit A=1, or "A B₁ C₁ ..."
        if len(coeffs) >= 3:
            A = 1.0 if coeffs[0] == 0 else coeffs[0]
            pairs = coeffs[1:]

            n2 = np.full_like(wl_um, A, dtype=float)
            for i in range(0, len(pairs) - 1, 2):
                B = pairs[i]
                C = pairs[i + 1]
                n2 += B * wl_um**2 / (wl_um**2 - C**2)

            n = np.sqrt(np.maximum(n2, 1.0))
            k = np.zeros_like(n)
            return (wl_um, n, k)

    elif formula_type.startswith('formula 4'):
        # Extended Sellmeier (ZnO style):
        # n² = A + B₁·λ^p₁/(λ² - C₁^p₂) + P·λ^p_p
        # coeffs: [A, B₁, p₁, C₁, p₂, 0, 0, 0, 1, P, p_p]
        if len(coeffs) >= 5:
            A = coeffs[0]
            B1 = coeffs[1]
            p1 = coeffs[2]
            C1 = coeffs[3]
            p2 = coeffs[4]

            n2 = np.full_like(wl_um, A, dtype=float)
            n2 += B1 * wl_um**p1 / (wl_um**2 - C1**p2)

            # Polynomial correction terms (if present)
            if len(coeffs) >= 11 and int(coeffs[8]) == 1:
                # coeffs[8] == 1 is the flag; pairs of (P, exponent) follow
                for j in range(9, len(coeffs) - 1, 2):
                    P = coeffs[j]
                    exp = coeffs[j + 1]
                    n2 += P * wl_um**exp

            n = np.sqrt(np.maximum(n2, 1.0))
            k = np.zeros_like(n)
            return (wl_um, n, k)

    return None


def _parse_rii_tabulated(raw_text, src=''):
    """Parse a refractiveindex.info YAML and extract tabulated nk data.
    Returns (wl_um, n, k) arrays or None.

    ``src``: 源文件路径, 仅用于给 warning 文案提供文件名上下文
    (load_nk_data 传入; 直接调用缺省为空——raise 类错误由调用方
    统一链上文件路径, warning 无异常链可用, 故在源头带上)。

    Line handling inside a ``DATA: |`` block :
    - three-numeric-column rows → data;
    - numeric-looking rows that fail the pattern (missing column, stray
      token) → counted, reported via warnings, and SKIPPED — previously
      they triggered an early ``break`` that silently truncated the block
      (or dropped it below the 5-row minimum, masquerading as "no data");
    - ``#`` comments and blank lines → skipped;
    - YAML list items (``- ...``) and key lines (``foo:``) → block ends.
    """
    raw_lines = raw_text.split('\n')
    data_lines = []
    bad_rows = []
    in_data = False
    for lineno, _line in enumerate(raw_lines, start=1):
        if _line.strip().lower().startswith('data: |'):
            in_data = True
            continue
        if in_data:
            _s = _line.strip()
            if not _s or _s.startswith('#'):
                continue
            # 1) Numeric data row: the char class includes '-', so rows like
            #    "-0.5 2 0" (negative first column, NO space after the
            #    dash) are data — a startswith('-') check truncated the
            #    block at any negative-leading row.
            if re.match(r'^[\d.eE+\-]+\s+[\d.eE+\-]+\s+[\d.eE+\-]+', _s):
                data_lines.append(_s)
            # 2) YAML list item ("- key: ...", dash followed by whitespace)
            #    terminates the block — must be checked BEFORE the
            #    malformed-numeric branch below, whose char class also
            #    matches the leading '-'.
            elif re.match(r'^-\s|^-$', _s):
                break
            # 3) Numeric-looking but malformed (e.g. missing k column):
            #    skip with a warning instead of silently truncating.
            elif re.match(r'^[\d.eE+\-]', _s):
                bad_rows.append((lineno, _s))
            # 4) Next YAML key → end of the DATA block.
            elif re.match(r'^[A-Za-z_][\w\-]*\s*:', _s):
                break
    if bad_rows:
        _of = f" of {src}" if src else ""
        warnings.warn(
            f"nkdata: skipped {len(bad_rows)} malformed data line(s) in a "
            f"DATA block{_of} (first: line {bad_rows[0][0]}: "
            f"{bad_rows[0][1]!r}). "
            "Check the .yml file for missing columns or stray characters.")
    if len(data_lines) < 5:
        return None
    _wl, _nv, _kv = [], [], []
    _overflow = []
    for _dl in data_lines:
        _parts = _dl.split()   # ≥3 tokens guaranteed by the row regex
        try:
            vals = (float(_parts[0]), float(_parts[1]), float(_parts[2]))
        except ValueError:
            # e.g. "1.2.3" passes the char-class regex but not float().
            continue
        if not all(math.isfinite(v) for v in vals):
            # "1e999" parses fine but overflows to inf — let it in and it
            # poisons the fit with inf/nan frequencies.
            _overflow.append(_dl)
            continue
        _wl.append(vals[0]); _nv.append(vals[1]); _kv.append(vals[2])
    if _overflow:
        _of = f" of {src}" if src else ""
        warnings.warn(
            f"nkdata: dropped {len(_overflow)} non-finite data row(s)"
            f"{_of} (first: {_overflow[0]!r}) — values like 1e999 "
            "overflow to inf.")
    if len(_wl) < 5:
        return None
    return (np.array(_wl), np.array(_nv), np.array(_kv))


def load_nk_data(material_name):
    """Load raw (lambda_um, n, k) data from the packaged material library.

    Handles tabulated nk and formula-based nk data.
    Returns (wl_um, n, k) arrays, or None if no data FILE exists for the
    material. If a file exists but neither parser can extract data from it,
    raises ValueError — "missing file" and "broken file" have different
    remedies and must not share one silent None.
    """
    _check_material_name(material_name)
    raw_path = _NKDATA_DIR / (material_name + '.yml')
    if not raw_path.exists():
        return None

    raw_text = raw_path.read_text(encoding='utf-8')

    # Try tabulated nk first (src= 让 warning 带上文件名——raise 类错误
    # 在下方 formula 分支统一链路径, warning 无异常链, 须源头带上下文)
    result = _parse_rii_tabulated(raw_text, src=str(raw_path))
    if result is not None:
        return result

    # Try formula-based parsing; chain parse errors with the file path so
    # the user knows WHICH yml is broken.
    try:
        result = _parse_rii_formula(raw_text)
    except ValueError as exc:
        raise ValueError(
            f"nkdata file exists but could not be parsed: {raw_path} "
            f"— malformed formula section ({exc})") from exc
    if result is not None:
        return result

    raise ValueError(
        f"nkdata file exists but could not be parsed: {raw_path} "
        "(no tabulated DATA block with >=5 valid rows and no known "
        "formula section)")
