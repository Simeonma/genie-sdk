"""结果对象 — 纯数据容器（npz 线格式 → 带 numpy 属性的对象）。

定案（用户裁决 2026-09）：**SDK 零计算、零物理**。本模块不做任何
派生量（transmission/flux/spectrum/FFT 一概不在此层）——技术全部
留在服务端。想在本地做分析的用户直接拿裸 numpy 数组自己写；要看
图的走 UI 的作业预览（PNG 由服务端 driver 渲染，预览域不进 SDK）。

npz 键约定（v100/backend/driver.py 固定的线格式）::

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


def _shape_label(axes: dict) -> str:
    """形态标签（描述性元数据）：位置轴数 0/1/2/3 → point/line/surface/volume。

    n 只统计 x/y/z 三个轴（axis_names 上限 3），``n < 4`` 恒真——
    第四项 volume 由 n==3 直接触达，无需兜底分支。"""
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

    def __init__(self, name: str, time: np.ndarray, fields: dict, **ax: Any):
        self.name = name
        self.time = time
        self.fields = fields
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label({k: v for k, v in ax.items() if v is not None})
        self.n_samples = int(np.size(time))
        self.n_positions = _n_positions(fields)

    @classmethod
    def from_npz(cls, arrays: dict, name: str) -> "TimeResult":
        fields, axes = _extract(arrays, "t", name, ("time",) + _AXES)
        time = axes.pop("time", None)
        if time is None:
            raise KeyError(
                f"npz 中未找到时域监视器 {name!r}（缺 t@{name}.time 键）")
        return cls(name, time, fields, **axes)


class FreqResult(_DataBase):
    """频域监视器结果（SI）。``.freqs`` 赫兹；``.fields`` {分量: 复数数组}。

    点监视器的场保持 (n_freqs, 1) 尾轴（与内部包约定一致）；
    多位置为 (n_freqs, n_pos)。派生分析（透过率/通量）不在 SDK 层——
    用裸分量自算；要看图走 UI/前端的作业预览面板（preview 是 UI 域
    方法，SDK 无此方法——见手册 07-run/09-results 页）。
    """

    def __init__(self, name: str, freqs: np.ndarray, fields: dict, **ax: Any):
        self.name = name
        self.freqs = freqs
        self.fields = fields
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label({k: v for k, v in ax.items() if v is not None})
        self.n_freqs = int(np.size(freqs))
        self.n_positions = _n_positions(fields)

    @classmethod
    def from_npz(cls, arrays: dict, name: str) -> "FreqResult":
        fields, axes = _extract(arrays, "f", name, ("freqs",) + _AXES)
        freqs = axes.pop("freqs", None)
        if freqs is None:
            raise KeyError(
                f"npz 中未找到频域监视器 {name!r}（缺 f@{name}.freqs 键）")
        return cls(name, freqs, fields, **axes)


class IndexResult(_DataBase):
    """index 监视器结果：``.eps`` / ``.mu``（每格点云）+ 每格 x/y/z 坐标。"""

    def __init__(self, name: str, fields: dict, **ax: Any):
        self.name = name
        self.fields = fields
        self.eps = fields.get("eps")
        self.mu = fields.get("mu")
        self.x = ax.get("x")
        self.y = ax.get("y")
        self.z = ax.get("z")
        self.shape = _shape_label({k: v for k, v in ax.items() if v is not None})

    def comps(self) -> list[str]:
        return [k for k in ("eps", "mu") if self.fields.get(k) is not None]

    @classmethod
    def from_npz(cls, arrays: dict, name: str) -> "IndexResult":
        fields, axes = _extract(arrays, "i", name, _AXES)
        if not fields:
            raise KeyError(
                f"npz 中未找到 index 监视器 {name!r}（缺 i@{name}.eps|mu 键）")
        return cls(name, fields, **axes)


class _MonitorView:
    """单监视器视图：属性/下标直达数据集（data.tr.Ex → 复数 ndarray）。"""

    def __init__(self, group) -> None:
        self._g = group

    def __getattr__(self, name: str):
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

    组 = 监视器，数据集 = 场/轴；属性直达: ``data.tr.Ex`` → 复数 ndarray
    （h5py 惰性读，切片才真正搬数据）。元信息: ``data.attrs``（根属性
    executed_steps/dt/grid_shape/format）。画图: ``data.plot("tr", "Ex")``
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
        """画监视器某场。体监视器（axis × cells 二维）缺省画**峰值格点**
        的曲线（峰值格点号入图例，ASCII 标签避免字体缺字形）；matplotlib 为可选依赖
        （pip install genie-sdk[plot]）。返回 Axes。"""
        import matplotlib.pyplot as plt  # 可选依赖，import 才触发

        ds = self._f[mon][field]
        arr = ds[()]
        ys = np.abs(arr) if np.iscomplexobj(arr) else arr
        axis_key = next((k for k in ("freqs", "time", "x", "y", "z")
                         if k in self._f[mon]), None)
        xs = (self._f[mon][axis_key][()] if axis_key is not None
              else list(range(len(ys))))
        tag = ""
        if ys.ndim == 2:                        # 体监视器：取峰值格点
            idx = int(np.argmax(ys))
            cell = idx % ys.shape[1]
            ys = ys[:, cell]
            tag = f" (peak cell #{cell})"
        created = ax is None
        ax = ax or plt.gca()
        ax.plot(xs, ys, label=f"{mon}.{field}{tag}")
        ax.set_xlabel(axis_key or "idx")
        ax.set_ylabel(("|" + field + "|") if np.iscomplexobj(arr) else field)
        if created:
            ax.legend()
        return ax
