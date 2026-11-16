"""genie.core.results — the single canonical path for reading results (h5 → objects).

Behavior contract:

- **SDK does zero computation, zero physics**: it returns only raw fields / axes / shape;
  derived quantities (transmission / peak frequency) are the user's own numpy work, and
  productized derived quantities go the "kernel computes and writes into the h5" route.
- **Zero HTTP, zero state machine**: results are always local ``.gensim`` members; the
  only "guard" = whether the member exists — the worker side states it in one plain sentence,
  and this package does not discuss state.
- **Zero caching**: a read parses on the spot and the caller owns what is returned.
- **digest reads attrs only**: missing key = None, pending the kernel write side; no HTTP
  fallback (the five net methods expose no such hook).
- Parsing guards against known h5 pitfalls: variable-length strings can read back as bytes
  → decode before comparing; the manifest's shape is the source of truth — never guess when
  it is present; a field key is never read as an axis dataset; ``__getattr__`` needs an
  underscore guard to prevent recursion.
"""
from __future__ import annotations

import json
import shutil
from typing import Any

import h5py
import numpy as np

__all__ = ["H5_FORMAT_PREFIX", "SimData", "TimeResult", "FreqResult",
           "IndexResult", "ModeResult", "ensure_supported_format",
           "read_monitor", "read_modes", "modes_manifest",
           "open_result"]

#: Result-file format contract prefix (the kernel driver writes root attrs ``format`` =
#: ``"genie-results-h5/<minor>"``). SDK read-verb entry points validate the prefix — a
#: format-family upgrade is reported loudly instead of silently misreading an old layout;
#: missing attrs are rejected the same way (real artifacts always carry them).
H5_FORMAT_PREFIX = "genie-results-h5/"

#: Positional axis dataset names (these keys are axes; field keys are never read as axes).
_AXES = ("x", "y", "z")

#: Domain letter → word (for error wording; parsing keys keep the kernel's f/t/i).
_DOMAIN_WORDS = {"t": "time", "f": "freq", "i": "index"}


def _decode_str(v) -> str:
    """h5py variable-length strings read back as bytes under some write configurations — the domain label must be decoded."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def ensure_supported_format(h5) -> None:
    """Root attrs ``format`` prefix guard: a bad prefix / missing attrs is reported loudly, never guessed."""
    fmt = _decode_str(h5.attrs.get("format", ""))
    if not fmt.startswith(H5_FORMAT_PREFIX):
        raise RuntimeError(
            f"result file format not recognized: {fmt!r} (expected "
            f"prefix {H5_FORMAT_PREFIX!r}) — the file is either not a "
            f"GENIE result h5 or from an incompatible format family")


def monitors_manifest(h5) -> dict:
    """Root attrs ``monitors`` → ``{name: meta}`` (source of truth for shape; a bad
    manifest does not block data retrieval — shape falls back to guessing from axis element counts)."""
    raw = h5.attrs.get("monitors", "")
    raw = _decode_str(raw) if isinstance(raw, (bytes, np.bytes_)) else raw
    try:
        parsed = json.loads(raw) if raw else []
        if isinstance(parsed, list):
            return {m.get("name"): m for m in parsed if isinstance(m, dict)}
    except ValueError:
        pass
    return {}


def modes_manifest(h5) -> dict:
    """Root attrs ``modes`` → dict（Q18 拍①：模式列表的正源——
    ``n / wavelength / sorted_by / 两轴名``；排序/模式识别口径归内核 M1，
    锁在 manifest，不进 wire）。坏 JSON / 缺 attrs → {}（数据面照读，
    同 monitors manifest 的宽口径）。"""
    raw = h5.attrs.get("modes", "")
    raw = _decode_str(raw) if isinstance(raw, (bytes, np.bytes_)) else raw
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _domain_of(group) -> str:
    return _decode_str(group.attrs.get("domain", ""))


def _shape_label(axes: dict, shape_hint) -> str:
    """Shape label: the manifest's shape wins (source of truth — guessing from axis element
    counts would always report volume for index's per-cell point cloud and under-report a true
    surface with single-cell axes, so no guessing when it is present); only when it is missing
    do we count positional axis element counts (``n < 4`` always holds)."""
    if shape_hint in ("point", "line", "surface", "volume"):
        return shape_hint
    n = sum(1 for ax in _AXES if ax in axes and np.size(axes[ax]) > 1)
    return ("point", "line", "surface", "volume")[n]


def _n_positions(fields: dict) -> int:
    pos = 1
    for v in fields.values():
        if getattr(v, "ndim", 0) > 1:
            pos = max(pos, v.shape[-1])
    return pos


# ── the three Result classes (return shapes of f.get_*; pure data containers, zero computation)────────────────

class _DataBase:
    """Common read-only shell: ``.fields`` + convenience surface (keys/dict/[])."""

    fields: dict[str, np.ndarray]
    name: str
    shape: str

    def comps(self) -> list[str]:
        return list(self.fields)

    def keys(self) -> list[str]:
        return self.comps()

    def dict(self) -> dict[str, np.ndarray]:
        return dict(self.fields)

    def __contains__(self, key: str) -> bool:
        return key in self.fields

    def __getitem__(self, key: str) -> np.ndarray:
        """``r['Ey']`` ≡ ``r.fields['Ey']``."""
        return self.fields[key]

    def __repr__(self) -> str:
        inner = ", ".join(
            f"{k}: {v.shape}" + (" complex" if np.iscomplexobj(v) else "")
            for k, v in self.fields.items())
        return (f"<{type(self).__name__} {self.name!r} "
                f"{self.shape}: {inner or '(empty)'}>")


class TimeResult(_DataBase):
    """Time-domain monitor result (SI): ``.time`` in seconds; ``.fields`` {component: array}."""

    def __init__(self, name: str, time: np.ndarray, fields: dict,
                 shape_hint=None, **ax: Any):
        self.name = name
        self.time = time
        self.fields = fields
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_samples = int(np.size(time))
        self.n_positions = _n_positions(fields)


class FreqResult(_DataBase):
    """Frequency-domain monitor result (SI): ``.freqs`` in hertz; ``.fields`` {component: complex array}.

    Point-monitor fields keep the (n_freqs, 1) trailing axis; derived analysis (transmission /
    flux) is not in the SDK layer — compute it yourself from the raw components (zero
    computation, zero physics).
    """

    def __init__(self, name: str, freqs: np.ndarray, fields: dict,
                 shape_hint=None, **ax: Any):
        self.name = name
        self.freqs = freqs
        self.fields = fields
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_freqs = int(np.size(freqs))
        self.n_positions = _n_positions(fields)


class IndexResult(_DataBase):
    """Index monitor result: ``.eps`` / ``.mu`` (per-cell point cloud) + per-cell x/y/z coordinates."""

    def __init__(self, name: str, fields: dict, shape_hint=None,
                 **ax: Any):
        self.name = name
        self.fields = fields
        self.eps = fields.get("eps")
        self.mu = fields.get("mu")
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)

    def comps(self) -> list[str]:
        return [k for k in ("eps", "mu") if self.fields.get(k) is not None]


class _ModeView:
    """单模视图（Q18 拍①）：``.neff``（标量）+ ``.fields`` {分量: (N1, N2)}
    ——整模列表切掉第 0 维；零计算零物理，纯切片。"""

    def __init__(self, neff, fields: dict) -> None:
        self.neff = neff
        self.fields = fields

    def keys(self) -> list[str]:
        return list(self.fields)

    def __contains__(self, key: str) -> bool:
        return key in self.fields

    def __getitem__(self, key: str) -> np.ndarray:
        return self.fields[key]

    def __repr__(self):
        inner = ", ".join(
            f"{k}: {v.shape}" + (" complex" if np.iscomplexobj(v) else "")
            for k, v in self.fields.items())
        return f"<ModeView neff={self.neff}: {inner or '(empty)'}>"


#: 模分类阈值（只标注、不过滤）：|Im(neff)| < 它 = 实模（guided）。
#: 虚部是物理结果（材料有耗 / 辐射泄漏），分类只帮助判读，不删任何模。
GUIDED_IM_TOL = 1e-6


class ModePlane:
    """mode 结果的一张面（h5 /2：``cross-section`` / ``delivery`` 同构载荷）：
    ``.fields`` {分量: (n, Nv+1, Nh+1)}、``.edges`` {"h"/"v": (N+1,)}（**完整
    边**，非格心）、``.frame_origin (3,)`` / ``.frame_R (3,3)``（面→全局映射；
    delivery 恒等，cross-section 倾斜时含刚转）。``.centers`` = 格心（/1
    旧件 ``coords`` 的同值口径）。零计算零物理——只承运数组。"""

    def __init__(self, fields: dict, edges: dict,
                 frame_origin, frame_R) -> None:
        self.fields = fields
        self.edges = edges
        self.frame_origin = frame_origin
        self.frame_R = frame_R

    @property
    def centers(self) -> dict:
        return {k: 0.5 * (v[:-1] + v[1:]) for k, v in self.edges.items()}

    def __repr__(self):
        inner = ", ".join(f"{k}: {v.shape}" for k, v in self.fields.items())
        return f"<ModePlane {inner or '(empty)'}>"


class ModeResult:
    """一个波长的模列表（ModeSweepResult 的元素）：``.neff (n,)``、
    ``.fields`` {分量: (n, N1, N2)}（**交付面单位功率**归一，绝对尺度归
    注入侧幅度）、``.coords`` {轴: (N,)}``、
    ``.wavelength``（组属性）、``.sorted_by``（manifest 口径，缺 = None
    不猜）、``.modes[i]`` 单模视图。与三个监视器 Result 同族：零计算零
    物理——只承运原始数组。

    h5 /2 起 ``.fields``/``.coords`` = **交货面**（消费方语义；coords
    由 edges 派生格心），另露对称两面：``.delivery`` / ``.cross_section``
    = :class:`ModePlane`（完整边 + frame + 场）。"""

    def __init__(self, neff: np.ndarray, fields: dict, coords: dict,
                 wavelength=None, sorted_by=None, delivery=None,
                 cross_section=None) -> None:
        self.neff = neff
        self.fields = fields
        self.coords = coords
        self.wavelength = wavelength
        self.sorted_by = sorted_by
        self.n_modes = int(np.size(neff))
        self.delivery = delivery
        self.cross_section = cross_section

    @property
    def modes(self) -> list[_ModeView]:
        return [_ModeView(self.neff[i],
                          {k: v[i] for k, v in self.fields.items()})
                for i in range(self.n_modes)]

    @property
    def kinds(self) -> list[str]:
        """逐模分类标签（只标注，不删模）：``"guided"`` = |Im(neff)| <
        ``GUIDED_IM_TOL``（实模）；``"lossy_leaky"`` = 其余（材料有耗或
        辐射泄漏——虚部是物理结果，不是噪声）。"""
        return ["guided" if abs(complex(x).imag) < GUIDED_IM_TOL
                else "lossy_leaky" for x in self.neff]

    def comps(self) -> list[str]:
        return list(self.fields)

    def keys(self) -> list[str]:
        return self.comps()

    def __getitem__(self, key: str) -> np.ndarray:
        """``r['Ey']`` ≡ ``r.fields['Ey']``（分量寻址；单模用 ``.modes[i]``）。"""
        return self.fields[key]

    def __contains__(self, key: str) -> bool:
        return key in self.fields

    def __repr__(self):
        inner = ", ".join(
            f"{k}: {v.shape}" + (" complex" if np.iscomplexobj(v) else "")
            for k, v in self.fields.items())
        return (f"<ModeResult n={self.n_modes} "
                f"wavelength={self.wavelength!r}: {inner or '(empty)'}>")


# ── h5 → object parsing (single canonical path)──────────────────────────────────

def read_monitor(h5, domain: str, name: str | None = None):
    """Pick the monitor group by domain → Result object (``domain ∈ {"t","f","i"}``).

    Default semantics (direct pick when single, error when multiple): when the domain
    has exactly one group it is picked directly; with multiple groups an error asks for ``name``.
    Axis datasets (x/y/z/time/freqs) are read as axes, everything else as fields.
    """
    word = _DOMAIN_WORDS.get(domain, domain)
    ensure_supported_format(h5)
    cands = [k for k in h5.keys() if _domain_of(h5[k]) == domain]
    if name is None:
        if not cands:
            raise RuntimeError(
                f"no {word} monitor in this result h5 "
                f"(monitors: {', '.join(h5.keys()) or 'none'})")
        if len(cands) > 1:
            raise RuntimeError(
                f"multiple {word} monitors present "
                f"({', '.join(cands)}) — pass name explicitly")
        name = cands[0]
    elif name not in cands:
        raise RuntimeError(
            f"{word} monitor {name!r} not found; available {word} "
            f"monitors: {', '.join(cands) or 'none'}")

    grp = h5[name]
    axes: dict = {}
    fields: dict = {}
    for key in grp.keys():                       # axis-name keys are axes, field keys are fields
        arr = np.asarray(grp[key][()])
        if key in _AXES or key in ("time", "freqs"):
            axes[key] = arr
        else:
            fields[key] = arr
    hint = monitors_manifest(h5).get(name, {}).get("shape")

    if domain == "t":
        if "time" not in axes:
            raise RuntimeError(
                f"time monitor {name!r} has no 'time' axis dataset")
        return TimeResult(name, axes.pop("time"), fields,
                          shape_hint=hint, **axes)
    if domain == "f":
        if "freqs" not in axes:
            raise RuntimeError(
                f"freq monitor {name!r} has no 'freqs' axis dataset")
        return FreqResult(name, axes.pop("freqs"), fields,
                          shape_hint=hint, **axes)
    return IndexResult(name, fields, shape_hint=hint, **axes)


def _read_plane_group(g) -> "ModePlane":
    """h5 /2 面组（edges + frame + 场分量）→ ModePlane（两面同构）。"""
    fields: dict = {}
    edges: dict = {}
    frame_origin = frame_R = None
    for key in g.keys():
        arr = np.asarray(g[key][()])
        if key == "edges_h":
            edges["h"] = arr
        elif key == "edges_v":
            edges["v"] = arr
        elif key == "frame_origin":
            frame_origin = arr
        elif key == "frame_R":
            frame_R = arr
        else:
            fields[key] = arr
    if not edges:
        raise RuntimeError(
            f"mode plane group {g.name!r} has no edges datasets — a /2 "
            f"plane carries edges_h/edges_v + frame + field components")
    return ModePlane(fields, edges, frame_origin, frame_R)


def read_modes(h5) -> ModeSweepResult:
    """mode 结果 → ``ModeSweepResult``（唯一读法：**λ 是组，不是形态**）。

    组 ``modes/lam_###``：``neff (n,)`` 必在 + **两面组**（``delivery`` /
    ``cross-section`` → :class:`ModePlane`；``coords`` 由 delivery 的 edges
    派生格心）。**没有 /1 兼容垫片**：a10 前的 mode 结果档（顶层键布局）
    不回读——重新跑作业。**λ 住组属性
    ``wavelength``**（根 manifest 不再抄一份 λ 列表）；组序按 λ 升序，不靠
    组名排序。排序口径来自根 attrs ``modes`` manifest 的 ``sorted_by``
    （缺 = None，不猜）。

    单 λ 结果 = 只含一个组的同一读法——没有第二套形状、第二套守卫。
    """
    ensure_supported_format(h5)
    if "modes" not in h5.keys():
        raise RuntimeError(
            f"no 'modes' group in this result h5 (groups: "
            f"{', '.join(h5.keys()) or 'none'}) — mode results carry an "
            f"eigenmode list, not monitors")
    man = modes_manifest(h5)
    root = h5["modes"]
    groups = [root[k] for k in root.keys()]
    if not groups:
        raise RuntimeError(
            "modes group is empty — mode results carry one 'lam_###' "
            "subgroup per wavelength (a single-λ result is the same shape "
            "with one subgroup)")
    missing = [g.name for g in groups if "wavelength" not in g.attrs]
    if missing:
        raise RuntimeError(
            f"modes subgroup(s) {missing} carry no 'wavelength' attr — the "
            f"wavelength lives on the group (one subgroup per wavelength)")
    groups.sort(key=lambda g: float(g.attrs["wavelength"]))
    axes = man.get("axes") or ["h", "v"]
    results = []
    for g in groups:
        if "delivery" not in g.keys():
            raise RuntimeError(
                f"modes subgroup {g.name!r} has no 'delivery' plane — mode "
                f"results are the two-plane layout (genie-results-h5/2, "
                f"2.0.1a10+); pre-a10 archives are not back-read by "
                f"design (no compatibility shim) — rerun the job")
        if "cross-section" not in g.keys():
            raise RuntimeError(
                f"modes subgroup {g.name!r} has 'delivery' but no "
                f"'cross-section' — a /2 group carries both planes")
        if "neff" not in g.keys():
            raise RuntimeError("modes group has no 'neff' dataset")
        delivery = _read_plane_group(g["delivery"])
        cross = _read_plane_group(g["cross-section"])
        centers = delivery.centers
        coords = {axes[0]: centers["h"], axes[1]: centers["v"]}
        results.append(ModeResult(
            np.asarray(g["neff"][()]), delivery.fields, coords,
            wavelength=float(g.attrs["wavelength"]),
            sorted_by=man.get("sorted_by"),
            delivery=delivery, cross_section=cross))
    return ModeSweepResult([r.wavelength for r in results], results,
                           man.get("sorted_by"))


class ModeSweepResult:
    """mode 结果容器：``wavelengths``（升序）+ 每 λ 一枚 ModeResult
    （**ragged 如实**——死支消失、新支排组末尾，各组 n 可不同）。单 λ
    结果 = 只含一个元素的同一个容器——没有"单 λ 结果类"。

    ``sorted_by = "branch_cont"``（n>1）：输出序 = 重叠配对的分支连续序
    ——λ 间不保证按 neff 对齐，跨支画色散曲线前先认分支（同支在各 λ 组内
    序号相同）；``"neff_re_desc"``（n=1）：按 Re(neff) 降序。"""

    def __init__(self, wavelengths, results, sorted_by=None) -> None:
        self.wavelengths = [float(w) for w in wavelengths]
        self.results = list(results)
        self.sorted_by = sorted_by

    @property
    def n_lambdas(self) -> int:
        return len(self.wavelengths)

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, i: int) -> ModeResult:
        return self.results[i]

    def __repr__(self) -> str:
        ns = [r.n_modes for r in self.results]
        return (f"<ModeSweepResult n_lambdas={self.n_lambdas} "
                f"modes_per_lambda={ns} sorted_by={self.sorted_by!r}>")


# ── SimData (lazy wrapper over h5)───────────────────────────────────────────

class _MonitorView:
    """Single-monitor view: attribute/subscript access reaches the dataset directly (``data.tr.Ex`` → lazy Dataset).

    ``owner`` = strong reference to the owning ``SimData``: a temporary SimData created inside
    a chained access is kept alive by this reference, so ``__del__`` will not close its h5
    file and leave the view holding a dead group.
    """

    def __init__(self, group, owner=None) -> None:
        self._g = group
        self._owner = owner

    def __getattr__(self, name: str):           # underscore guard prevents recursion
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._g[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str):
        return self._g[name][()]                # subscript returns an ndarray directly

    def keys(self) -> list[str]:
        return list(self._g.keys())

    def __dir__(self):
        return list(self._g.keys())


class SimData:
    """Lazy wrapper over a result h5 (groups = monitors, datasets = fields/axes).

    ``data.<mon>.<field>`` attribute access is direct (a lazy h5py Dataset; only slicing or
    ``[()]`` yields an ndarray); ``data.attrs`` exposes root attrs read-only; ``data.digest``
    reads root attrs only (missing key / bad JSON = None, pending the kernel write side —
    no HTTP fallback). close is a trio (with / close / ``__del__`` as the backstop); the
    ``scratch`` directory (the worker's read cache) belongs to this object — close removes
    the directory as well.
    """

    def __init__(self, h5file, job_id: str = "", scratch=None) -> None:
        self._f = h5file
        self.job_id = job_id
        self._scratch = scratch

    def close(self) -> None:
        scratch = getattr(self, "_scratch", None)
        self._scratch = None
        try:
            self._f.close()
        except Exception:                       # noqa: BLE001 — already closed, must not raise
            pass
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    def __enter__(self) -> "SimData":
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:                       # noqa: BLE001 — backstop, must not raise
            pass

    @property
    def attrs(self) -> dict:
        return dict(self._f.attrs)

    @property
    def digest(self):
        """Root attrs ``digest`` (JSON) → dict; missing key / bad JSON → None."""
        raw = self._f.attrs.get("digest")
        if raw is None or raw == "" or raw == b"":
            return None
        raw = _decode_str(raw) if isinstance(raw, (bytes, np.bytes_)) else raw
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @property
    def monitors(self) -> list[str]:
        return list(self._f.keys())

    # -- parsing entry points (same canonical path as f.get_*)----------

    def get_time(self, name: str | None = None) -> TimeResult:
        return read_monitor(self._f, "t", name)

    def get_freq(self, name: str | None = None) -> FreqResult:
        return read_monitor(self._f, "f", name)

    def get_index(self, name: str | None = None) -> IndexResult:
        return read_monitor(self._f, "i", name)

    def get_modes(self) -> ModeSweepResult:
        """mode 结果 → ModeSweepResult（与 get_time/get_freq/get_index 同一条
        eager 解析路径）。单 λ 结果 = 只含一个元素；没有第二个人口、也没有
        "单 λ 结果类型"。"""
        return read_modes(self._f)

    def __getattr__(self, name: str):           # monitor-name direct access; underscore guard
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return _MonitorView(self._f[name], self)
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str) -> _MonitorView:
        return _MonitorView(self._f[name], self)

    def __dir__(self):
        return sorted(set(list(super().__dir__()) + self.monitors))

    def __repr__(self) -> str:
        return (f"SimData(job_id={self.job_id!r}, monitors={self.monitors}, "
                f"attrs={self.attrs})")


def open_result(path, *, job_id: str = "", scratch=None) -> SimData:
    """h5 file path → SimData behind a format guard (lazy). Bad prefix: close the handle, then raise."""
    h5 = h5py.File(str(path), "r")
    try:
        ensure_supported_format(h5)
    except BaseException:
        h5.close()
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        raise
    return SimData(h5, job_id=job_id, scratch=scratch)
