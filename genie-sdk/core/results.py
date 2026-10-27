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

# 结果文件格式契约前缀（R23 G-a，站长裁加校验）：内核 driver 写根
# attrs format = "genie-results-h5/<minor>"。SDK 取数入口校验前缀——
# 格式家族升级时明报，不静默按旧布局误读；缺 attrs 同样拒（G94/R19
# 实证：真产物必有根 attrs，全空的是探针残留）。
H5_FORMAT_PREFIX = "genie-results-h5/"


def ensure_supported_format(h5) -> None:
    """根 attrs format 前缀校验（Simulate 两条取数路径共用）。"""
    fmt = h5.attrs.get("format", "")
    if isinstance(fmt, bytes):
        fmt = fmt.decode("utf-8", "replace")
    if not fmt.startswith(H5_FORMAT_PREFIX):
        raise RuntimeError(
            f"result file format not recognized: {fmt!r} (expected "
            f"prefix {H5_FORMAT_PREFIX!r}) — the file is either not a "
            f"GENIE result h5 or from an incompatible format family; "
            f"upgrade genie-sdk")


def _reject_axis_dataset(group, field: str) -> None:
    """field 不得是轴数据集（R25 确凿 2）。

    ``x``/``y``/``z``/``time``/``freqs`` 在组内是轴——修前 field="x"
    被当成功读入，坐标云当场、谱轴当 ys，两条长度不等的列表照样打包
    成曲线（静默错值）。统一在这里拒（curve 与 select 共用）。"""
    axis_keys = tuple(k for k in AXIS_KEYS + ("time", "freqs") if k in group)
    if field in axis_keys:
        raise ValueError(
            f"field {field!r} is an axis dataset (axes here: {axis_keys}) "
            f"— expected a field component like 'Ex'; axes are selected "
            f"via axes=, not read as data")


def curve(group, mon: str, field: str):
    """取数尺（G96/R19 唯一一份）：h5 组 → (axis_key, xs, ys, tag, is_cplx)。

    - 轴 = 组内按 AXIS_KEYS 优先级第一个命中；缺失回退行下标；
    - 复数取模（is_cplx 标记给 ylabel 用）；
    - **任意 ndim > 1 统一折叠**成单曲线：reshape(shape[0], -1) 取
      峰值列——G142 定案①（站长裁决 2026-10-27）：**忽略 NaN 选峰**
      （nanargmax——修前 argmax 被 NaN 劫持，数据含 NaN 时"峰值列"
      指向 NaN 列，两侧一致地错）；全 NaN 列时无峰可选，退列 0 并
      标注（比画一条假峰值线诚实）；
    - G143：3D 此前 plot 裸崩（matplotlib 拒 3D y）、series 长度
      错配（x 行数/y 元素数）。

    两个外壳共用本尺：`SimData.plot`（matplotlib）与
    `studio.results_view.series`（降采样 + JSON）——两侧曲线必须
    逐点一致（test_results_equivalence 锁）。"""
    _reject_axis_dataset(group, field)
    arr = group[field][()]
    is_cplx = np.iscomplexobj(arr)
    ys = np.abs(arr) if is_cplx else arr
    axis_key = next((k for k in AXIS_KEYS if k in group), None)
    xs = (group[axis_key][()] if axis_key is not None
          else np.arange(ys.shape[0]))
    tag = ""
    if ys.ndim > 1:
        m = ys.reshape(ys.shape[0], -1)
        if m.shape[1] == 1:
            # 单格点（点监视器 (nf,1)）：直接展开，无"峰值格"可选——
            # R25 可疑 7：修前恒打 " (peak cell #0)" 无意义后缀
            ys = m[:, 0]
            return axis_key, xs, ys, tag, is_cplx
        try:
            cell = int(np.nanargmax(m)) % m.shape[1]
        except ValueError:              # 全 NaN——无峰可选，诚实标注
            cell = 0
            tag = " (all-NaN — no finite peak)"
        else:
            tag = f" (peak cell #{cell})"
        ys = m[:, cell]
    return axis_key, xs, ys, tag, is_cplx


# ── n-D 取数尺（G148 R24c 批 1）───────────────────────────────────────
#
# 维度约定（批 0 钉死，源 = 内核 driver `_save_results_h5` +
# `_monitor/_spatial_alloc.flatten_cells`，fdtd HEAD cb10220）：
#   · 场数组 ≤ 2D：(n_samples|n_freqs, n_cells)——dim0 是谱/时间轴
#     （组内 time/freqs 数据集即坐标）；dim1 是**展平格点**；
#   · 格点 C 序：flat = k·ny·nx + j·nx + i（x 最快，z 最外）；
#     x/y/z 数据集逐格点给坐标（Yee 交错位置）；
#   · 反展平判据：n_cells == nx·ny·nz（坐标云唯一值数）且云可由
#     唯一值按该序外积重建 → 规则子盒，reshape 成 (nz, ny, nx)；
#     不满足（Yee 裁剪/退化钳位/六分量串接）→ 只提供 "cell" 轴
#     （格点下标），不硬造网格。

_SPATIAL_KEYS = ("x", "y", "z")


def _unflatten_axis_map(group, n_cells: int) -> dict:
    """坐标云 → 轴选择表 {轴名: 坐标 1D}（规则子盒时）。

    返回 {} 表示无法反展平（调用方退 "cell" 轴）。严格校验：每条云
    必须精确等于唯一值按 C 序（z 外 → y → x 最快）的外积展开——
    差一个格都不认，防把 Yee 裁剪盒误读成规则网格。"""
    clouds = {}
    for ax in _SPATIAL_KEYS:
        if ax in group:
            c = np.asarray(group[ax][()])
            if c.ndim != 1 or len(c) != n_cells:
                return {}
            clouds[ax] = c
    if not clouds:
        return {}
    uniq = {ax: np.unique(c) for ax, c in clouds.items()}
    n_prod = 1
    for u in uniq.values():
        n_prod *= len(u)
    if n_prod != n_cells:
        return {}
    # C 序外积重建：轴序 z,y,x（x 最快 = meshgrid 最后一维）
    present = [ax for ax in reversed(_SPATIAL_KEYS) if ax in uniq]
    grids = np.meshgrid(*[uniq[ax] for ax in present], indexing="ij")
    for ax, g in zip(present, grids):
        if not np.array_equal(np.asarray(clouds[ax], dtype=np.float64),
                              np.asarray(g.ravel(), dtype=np.float64)):
            return {}
    return uniq


def select(group, field: str, *, axes: list | None = None,
           slices: dict | None = None):
    """n-D 取数尺（G148 单一真源）：h5 组 + 场 → 选轴视图。

    - ``axes=None``（缺省）：**峰值列折叠**——与 ``curve()`` 同口径
      （G142 定案），现 GUI 行为零漂移；
    - ``axes=["x"]``：1D——指定轴为横轴；``axes=["y","x"]``：2D——
      第一元素 = 横轴（x）、第二元素 = 纵轴（y），输出 data 形状
      (len(y), len(x))；
    - 可选轴名：``"time"``/``"freqs"``（谱/时间，dim0）+ 反展平成功
      的 ``"x"``/``"y"``/``"z"`` + ``"cell"``（格点下标，无法反展平
      时的空间轴）；
    - ``slices={"axis": int}``：未选中的轴定切片，**缺省 = 联合峰值
      切片**——全部缺省轴取全局峰格（同一个格）的坐标，与峰值列折叠
      严格同值（G142 同义，非"逐轴各自 argmax"）；全 NaN 退 0；
    - 复数取模（``is_cplx`` 标记）。

    返回 ``{"dim": 1|2, "x": {"key","values"}, "y": {...}|None,
    "data": ndarray, "tag": str, "is_cplx": bool}``。场缺失 →
    KeyError（G146 口径）；轴名/切片形状错 → ValueError 带定位。

    维度约定见本节头注（批 0 钉死：场 ≤2D，dim1 = C 序展平格点
    x 最快，规则子盒才可反展平为 (nz,ny,nx)）。"""
    if field not in group:
        raise KeyError(
            f"field {field!r} not found in monitor group (present: "
            f"{sorted(k for k in group if k not in _SPATIAL_KEYS)}")
    _reject_axis_dataset(group, field)
    arr = np.asarray(group[field][()])
    is_cplx = np.iscomplexobj(arr)
    data = np.abs(arr) if is_cplx else arr

    # 可用轴表 + data 的轴序（order[i] ↔ data 第 i 维）
    spec_key = next((k for k in ("freqs", "time") if k in group), None)
    coords: dict[str, np.ndarray] = {}
    order: list[str] = []
    if data.ndim == 0:
        data = data.reshape(1)
    if data.ndim >= 3:
        raise ValueError(
            f"field {field!r} has {data.ndim} dims — expected ≤2 "
            f"(spectral × flattened cells); the file violates the "
            f"genie-results-h5/1 layout")
    spec_ax = spec_key or "index"
    coords[spec_ax] = (np.asarray(group[spec_key][()])
                       if spec_key else np.arange(data.shape[0]))
    order.append(spec_ax)
    if data.ndim == 2:
        n_cells = data.shape[1]
        if n_cells > 1:
            spatial = _unflatten_axis_map(group, n_cells)
            if spatial:
                for ax in reversed(_SPATIAL_KEYS):     # x 最快 = 最后一维
                    if ax in spatial:
                        coords[ax] = spatial[ax]
                        order.append(ax)
            else:
                coords["cell"] = np.arange(n_cells)
                order.append("cell")
            data = data.reshape([len(coords[a]) for a in order])
        else:
            # 点监视器 (nf,1)：单格点无空间轴（R25 确凿 3——修前
            # 报假轴 cell:1，与"点监视器只有谱轴"的文档违约）
            data = data[:, 0]
    elif (data.ndim == 1 and spec_key is None
          and any(ax in group for ax in _SPATIAL_KEYS)):
        # R25 缺口 8：无谱轴的 1D 场 + 逐格点云（index 监视器的
        # eps/mu）——dim0 就是格点，用空间轴**替换**合成 index 轴
        # （修前三面口径打架：curve 缺省能画 eps vs x 云，select 却
        # 只认 index、axis_lengths 报 {}，选轴视图表达不回缺省视图）。
        # 云不匹配（非逐格点）→ 退 cell 轴。带谱轴的 1D 场不属此形态
        # （真产物无此组合），维持谱轴解读。
        n = data.shape[0]
        coords.pop(spec_ax)
        order.remove(spec_ax)
        spatial = _unflatten_axis_map(group, n)
        if spatial:
            for ax in reversed(_SPATIAL_KEYS):
                if ax in spatial:
                    coords[ax] = spatial[ax]
                    order.append(ax)
            data = data.reshape([len(coords[a]) for a in order])
        else:
            coords["cell"] = np.arange(n)
            order.append("cell")

    # 缺省视图 = 峰值列折叠（G142，与 curve() 同口径；mon 形参仅用于
    # curve 的消息文案，select 面没有监视器名可传，占位空串）
    if axes is None:
        if slices:
            # R25 小案：缺省视图无切片面——GUI parse_view 早有同口径
            # 拦截（"slices 需要 axes"），SDK 直调面修前静默丢弃。
            raise ValueError(
                "slices require axes= — the default (peak-fold) view has "
                "no slice surface; pass e.g. axes=['freqs'] first")
        axis_key, xs, ys, tag, _ = curve(group, "", field)
        return {"dim": 1, "x": {"key": axis_key or "index",
                                "values": xs},
                "y": None, "data": ys, "tag": tag, "is_cplx": is_cplx}

    if not isinstance(axes, list) or not 1 <= len(axes) <= 2:
        raise ValueError(
            f"axes must be a list of 1 (1D) or 2 (2D) axis names, "
            f"got {axes!r}")
    for a in axes:
        if a not in coords:
            raise ValueError(
                f"axis {a!r} not available for this field (available: "
                f"{list(coords)}; spatial axes need a regular monitor "
                f"sub-box — irregular point clouds only offer 'cell')")
    if len(set(axes)) != len(axes):
        raise ValueError(f"axes must be distinct, got {axes!r}")

    slices = dict(slices or {})
    for a in axes:
        # R25 确凿 4：修前已选轴的 slice 键落进"unknown/unselected"
        # 文案——与事实相反（该轴明明是 selected）。先答真话再谈范围。
        if a in slices:
            raise ValueError(
                f"slice for axis {a!r} which is already selected in "
                f"axes={axes} — a selected axis spans the view; slice an "
                f"unselected axis instead")
    pick: list = [None] * len(order)
    for a in axes:
        pick[order.index(a)] = slice(None)
    manual: dict[int, int] = {}
    for i, a in enumerate(order):
        if pick[i] is not None or a not in slices:
            continue
        n = data.shape[i]
        v = slices.pop(a)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < n:
            raise ValueError(
                f"slice for axis {a!r} must be an int in [0, {n}), "
                f"got {v!r}")
        pick[i] = v
        manual[i] = v
    if slices:
        raise ValueError(
            f"slice keys for unknown/unselected axes: {sorted(slices)} "
            f"(data axes here: {order})")
    # 缺省切片 = 联合峰值（G142 同义）：先施手动切片，在余空间里取
    # **全局峰格**的坐标分派给各缺省轴——axes=[谱轴] 的自动切片与
    # 峰值列折叠严格同值（同一格），不是"逐轴各自 argmax"的近似。
    auto_rows = [i for i in range(len(order)) if pick[i] is None]
    if auto_rows:
        sub = data
        for j in sorted(manual, reverse=True):
            sub = np.take(sub, manual[j], axis=j)
        sub_axes = [a for j, a in enumerate(order) if j not in manual]
        try:
            g = int(np.nanargmax(sub))
            c = np.unravel_index(g, sub.shape)
            for jj, a in enumerate(sub_axes):
                if a in axes:
                    continue                    # 选中轴保持 slice(None)
                pick[order.index(a)] = int(c[jj])
        except ValueError:                      # 全 NaN——全部退 0
            for i in auto_rows:
                pick[i] = 0
    view = data[tuple(pick)]
    if len(axes) == 1:
        return {"dim": 1, "x": {"key": axes[0], "values": coords[axes[0]]},
                "y": None, "data": np.asarray(view), "tag": "",
                "is_cplx": is_cplx}
    # 输出统一 (len(y), len(x))：view 轴序 = order 中保留的轴序
    seq = [a for a in order if a in axes]
    out = view if seq == [axes[1], axes[0]] else view.T
    return {"dim": 2, "x": {"key": axes[0], "values": coords[axes[0]]},
            "y": {"key": axes[1], "values": coords[axes[1]]},
            "data": np.asarray(out), "tag": "", "is_cplx": is_cplx}


def axis_lengths(group) -> dict:
    """轴存在表 + 长度（G148：meta/面板的单一真源）。

    - ``time``/``freqs`` = 轴长；
    - 空间轴 = **唯一值数**（规则子盒反展平后的每维元数——不是坐标云
      的展平长度！曾把 1600 格点云当 z 轴 1600 个切片位，造出
      "z[0..1599]" 的假轴，站长真机质询抓出）；
    - 云无法反展平（Yee 裁剪/退化）→ 只报 ``cell`` = n_cells；
    - 点监视器（场 1D、无云）→ 只有谱轴。
    面板据此渲染轴行/滑条；轴能否真当选仍由 select() 裁决。"""
    out: dict[str, int] = {}
    spec = next((k for k in ("freqs", "time") if k in group), None)
    if spec is not None:
        out[spec] = int(np.asarray(group[spec][()]).size)
    fields = [k for k in group if k not in _SPATIAL_KEYS and k != spec]
    n_cells = None
    for k in fields:
        shp = np.asarray(group[k][()]).shape
        if len(shp) >= 2:
            if shp[1] > 1:      # (nf,1) 点监视器不算空间轴（R25 确凿 3）
                n_cells = int(shp[1])
                break
        elif len(shp) == 1 and any(a in group for a in _SPATIAL_KEYS):
            # R25 缺口 8：1D 场 + 逐格点云（index 的 eps/mu）也按
            # 格点数报空间轴——修前报 {}，面板对 index 监视器无轴行。
            n_cells = int(shp[0])
            break
    if n_cells is not None:
        spatial = _unflatten_axis_map(group, n_cells)
        if spatial:
            out.update({a: int(len(v)) for a, v in spatial.items()})
        else:
            out["cell"] = n_cells
    return out


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
