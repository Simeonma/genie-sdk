"""结果对象 — 纯数据容器（平铺键 dict → 带 numpy 属性的对象）。

定案（用户裁决 2026-09）：**SDK 零计算、零物理**。本模块不做任何
派生量（transmission/flux/spectrum/FFT 一概不在此层）——技术全部
留在服务端。想在本地做分析的用户直接拿裸 numpy 数组自己写；要看
图的走 UI 的作业预览（PNG 由服务端 driver 渲染，预览域不进 SDK）。

输入约定（**内存中介形态**，非盘上格式——盘上是分组 h5，见
`simulate._result_arrays` 的展平与 HTTP_API.md"结果文件结构"；真源
= 内核 driver 的 `_save_results_h5`）::

    t@<名>.time            时域轴 (n_samples,)
    t@<名>.<comp>          场分量，如 t@pt.Ex —— (n_samples,) 或 (n_samples, n_pos)
    t@<名>.x|y|z           位置轴（点监视器无）
    f@<名>.freqs           频轴 (n_freqs,)
    f@<名>.<comp>          复数 DFT 振幅 —— (n_freqs, 1) 或 (n_freqs, n_pos)
    i@<名>.eps|mu          介电/磁导率点云
    i@<名>.x|y|z           每格坐标
"""
from __future__ import annotations

from typing import Any

import numpy as np

_AXES = ("x", "y", "z")

# 曲线轴优先级（G96 取数尺的一部分；studio.results_view 共用本常量）
AXIS_KEYS = ("freqs", "time", "x", "y", "z")


def curve(group, mon: str, field: str):
    """取数尺（G96/R19 唯一一份）：h5 组 → (axis_key, xs, ys, tag, is_cplx)。

    - 轴 = 组内按 AXIS_KEYS 优先级第一个命中；缺失回退行下标；
    - 复数取模（is_cplx 标记给 ylabel 用）；
    - **任意 ndim > 1 统一折叠**成单曲线：reshape(shape[0], -1) 取
      峰值列（全局最大元所在列）——G143：3D 此前 plot 裸崩
      （matplotlib 拒 3D y）、series 长度错配（x 行数/y 元素数）；
    - NaN 劫持 argmax 的口径维持现状（G142 待站长裁决，尺内一处改）。

    两个外壳共用本尺：`SimData.plot`（matplotlib）与
    `studio.results_view.series`（降采样 + JSON）——两侧曲线必须
    逐点一致（test_results_equivalence 锁）。"""
    arr = group[field][()]
    is_cplx = np.iscomplexobj(arr)
    ys = np.abs(arr) if is_cplx else arr
    axis_key = next((k for k in AXIS_KEYS if k in group), None)
    xs = (group[axis_key][()] if axis_key is not None
          else np.arange(ys.shape[0]))
    tag = ""
    if ys.ndim > 1:
        m = ys.reshape(ys.shape[0], -1)
        cell = int(np.argmax(m)) % m.shape[1]
        ys = m[:, cell]
        tag = f" (peak cell #{cell})"
    return axis_key, xs, ys, tag, is_cplx


def _extract(arrays: dict, prefix: str, name: str,
             axis_names: tuple[str, ...]) -> tuple[dict, dict]:
    """挑出 ``<prefix>@<name>.<field>`` 键 → (场分量 dict, 轴 dict)。"""
    pre = f"{prefix}@{name}."
    fields: dict[str, np.ndarray] = {}
    axes: dict[str, np.ndarray] = {}
    for k, v in arrays.items():
        if not k.startswith(pre):
            continue
        tail = k[len(pre):]
        if tail in axis_names:
            axes[tail] = np.asarray(v)
        else:
            fields[tail] = np.asarray(v)
    return fields, axes


def _shape_label(axes: dict, shape_hint: str | None = None) -> str:
    """形态标签（描述性元数据）。

    G95（R19）：``shape_hint`` = 根 attrs manifest 的 shape（正源，
    driver 对每类监视器都写）优先；缺失才回退猜测——猜测按位置轴
    元素数计数（n 只统计 x/y/z，``n < 4`` 恒真），对 index 类
    （x/y/z 是逐格点云，全长 n_cells）必报 volume，对真 surface
    单格轴会低报——所以 manifest 在场时不猜。"""
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


class _DataBase:
    """公共只读外壳：属性名与内部包的结果对象对齐（.fields/.x/.y/.z），
    另给 keys()/dict()/[] 便于交互式查看。"""

    fields: dict[str, np.ndarray]
    name: str
    shape: str

    def comps(self) -> list[str]:
        """记录的分量名列表。"""
        return list(self.fields)

    def keys(self) -> list[str]:
        return self.comps()

    def dict(self) -> dict[str, np.ndarray]:
        return dict(self.fields)

    def __contains__(self, key: str) -> bool:
        return key in self.fields

    def __getitem__(self, key: str) -> np.ndarray:
        """``r['Ey']`` ≡ ``r.fields['Ey']``。"""
        return self.fields[key]

    def __repr__(self) -> str:
        inner = ", ".join(
            f"{k}: {v.shape}" + (" complex" if np.iscomplexobj(v) else "")
            for k, v in self.fields.items())
        return (f"<{type(self).__name__} {self.name!r} "
                f"{self.shape}: {inner or '(空)'}>")


class TimeResult(_DataBase):
    """时域监视器结果（SI）。``.time`` 秒；``.fields`` {分量: 数组}。"""

    def __init__(self, name: str, time: np.ndarray, fields: dict,
                 shape_hint: str | None = None, **ax: Any):
        self.name = name
        self.time = time
        self.fields = fields
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_samples = int(np.size(time))
        self.n_positions = _n_positions(fields)

    @classmethod
    def from_npz(cls, arrays: dict, name: str,
                 shape_hint: str | None = None) -> "TimeResult":
        fields, axes = _extract(arrays, "t", name, ("time",) + _AXES)
        time = axes.pop("time", None)
        if time is None:
            raise KeyError(
                f"npz 中未找到时域监视器 {name!r}（缺 t@{name}.time 键）")
        return cls(name, time, fields, shape_hint=shape_hint, **axes)


class FreqResult(_DataBase):
    """频域监视器结果（SI）。``.freqs`` 赫兹；``.fields`` {分量: 复数数组}。

    点监视器的场保持 (n_freqs, 1) 尾轴（与内部包约定一致）；
    多位置为 (n_freqs, n_pos)。派生分析（透过率/通量）不在 SDK 层——
    用裸分量自算；要看图走 UI/前端的作业预览面板（preview 是 UI 域
    方法，SDK 无此方法——见手册 07-run/09-results 页）。
    """

    def __init__(self, name: str, freqs: np.ndarray, fields: dict,
                 shape_hint: str | None = None, **ax: Any):
        self.name = name
        self.freqs = freqs
        self.fields = fields
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_freqs = int(np.size(freqs))
        self.n_positions = _n_positions(fields)

    @classmethod
    def from_npz(cls, arrays: dict, name: str,
                 shape_hint: str | None = None) -> "FreqResult":
        fields, axes = _extract(arrays, "f", name, ("freqs",) + _AXES)
        freqs = axes.pop("freqs", None)
        if freqs is None:
            raise KeyError(
                f"npz 中未找到频域监视器 {name!r}（缺 f@{name}.freqs 键）")
        return cls(name, freqs, fields, shape_hint=shape_hint, **axes)


class IndexResult(_DataBase):
    """index 监视器结果：``.eps`` / ``.mu``（每格点云）+ 每格 x/y/z 坐标。"""

    def __init__(self, name: str, fields: dict,
                 shape_hint: str | None = None, **ax: Any):
        self.name = name
        self.fields = fields
        self.eps = fields.get("eps")
        self.mu = fields.get("mu")
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)

    def comps(self) -> list[str]:
        return [k for k in ("eps", "mu") if self.fields.get(k) is not None]

    @classmethod
    def from_npz(cls, arrays: dict, name: str,
                 shape_hint: str | None = None) -> "IndexResult":
        fields, axes = _extract(arrays, "i", name, _AXES)
        if not fields:
            raise KeyError(
                f"npz 中未找到 index 监视器 {name!r}（缺 i@{name}.eps|mu 键）")
        return cls(name, fields, shape_hint=shape_hint, **axes)


class _MonitorView:
    """单监视器视图：属性/下标直达数据集（data.tr.Ex → 复数 ndarray）。"""

    def __init__(self, group) -> None:
        self._g = group

    def __getattr__(self, name: str):
        # G145（R19）：下划线守卫——_f/_g 赋值前的属性探测（pickle/
        # 复制协议查 __deepcopy__ 等）不得递归进本方法爆 RecursionError
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._g[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str):
        return self._g[name][()]          # 直接给 ndarray（不是 h5py 数据集）

    def keys(self) -> list[str]:
        return list(self._g.keys())

    def __dir__(self):
        return list(self._g.keys())


class SimData:
    """作业结果结构体（genie-results-h5/1 的 SDK 形态）——result.h5 的包装。

    组 = 监视器，数据集 = 场/轴；属性直达: ``data.tr.Ex`` → 惰性
    h5py Dataset（切片或 ``[()]`` 才是 ndarray；C2/R19 措辞修正——
    此前误写"complex ndarray"）。元信息: ``data.attrs``（根属性
    executed_steps/dt/grid_shape/format）。画图: ``data.plot("tr","Ex")``
    （matplotlib 为可选依赖，import 才触发）。
    """

    def __init__(self, h5file, job_id: str = "") -> None:
        self._f = h5file
        self.job_id = job_id

    # K5（R18）：h5 句柄显式关闭面——BytesIO 背书不占 OS 句柄（低危），
    # 但反复 results() 会积累打开的 h5py.File；上下文管理器 + close +
    # 兜底 __del__ 三件套，习惯 with 的用户零成本。
    def close(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001 —— 已关/不可关都不炸
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        self.close()

    @property
    def attrs(self) -> dict:
        return dict(self._f.attrs)

    @property
    def monitors(self) -> list[str]:
        return list(self._f.keys())

    def __getattr__(self, name: str):
        # 仅在常规属性缺失时触发 → 监视器名直达（data.tr → _MonitorView）
        # G145：下划线守卫（同 _MonitorView，防 _f 前属性探测递归）
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return _MonitorView(self._f[name])
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str) -> _MonitorView:
        return _MonitorView(self._f[name])

    def __dir__(self):
        return sorted(set(list(super().__dir__()) + self.monitors))

    def __repr__(self) -> str:
        return (f"SimData(job_id={self.job_id!r}, monitors={self.monitors}, "
                f"attrs={self.attrs})")

    def plot(self, mon: str, field: str, ax=None):
        """画监视器某场。体监视器（任意维）缺省画**峰值格点**的曲线
        （峰值格点号入图例，ASCII 标签避免字体缺字形）；matplotlib 为可选依赖
        （pip install genie-sdk[plot]）。返回 Axes。"""
        try:
            import matplotlib.pyplot as plt  # 可选依赖，import 才触发
        except ImportError as e:
            # G146（R19）：报错带安装指路（docstring 承诺的 extras 名）。
            raise ImportError(
                "plot() needs matplotlib (optional dependency) — "
                "pip install 'genie-sdk[plot]'") from e
        # G146：缺监视器/缺场给可定位的 KeyError（对照 series 侧）
        if mon not in self._f:
            raise KeyError(f"monitor {mon!r} not found in result "
                           f"(monitors: {self.monitors})")
        if field not in self._f[mon]:
            raise KeyError(f"field {field!r} not found in monitor {mon!r} "
                           f"(datasets: {list(self._f[mon].keys())})")
        # G96：曲线本体走取数尺（任意维折叠 + 峰值列，G143 根治 3D 崩）
        axis_key, xs, ys, tag, is_cplx = curve(self._f[mon], mon, field)
        created = ax is None
        ax = ax or plt.gca()
        ax.plot(xs, ys, label=f"{mon}.{field}{tag}")
        ax.set_xlabel(axis_key or "idx")
        ax.set_ylabel(("|" + field + "|") if is_cplx else field)
        if created:
            ax.legend()
        return ax
