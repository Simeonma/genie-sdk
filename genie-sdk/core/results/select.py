"""取数算法族（原 core/results.py 上半）——h5 组 → 曲线/选轴视图。

文件头契约与"SDK 零计算、零物理"定案见包 docstring（core/results/__init__）
与 simdata.py；本模块只住取数语义：curve（单曲线峰值折叠尺）、
select / axis_lengths（n-D 选轴视图与轴存在表）、sweep_axes /
ensure_supported_format（扫描轴表与结果格式家族校验）。
"""
from __future__ import annotations

import json

import numpy as np

_AXES = ("x", "y", "z")

# 曲线轴优先级（G96 取数尺的一部分；studio.results_view 共用本常量）
AXIS_KEYS = ("freqs", "time", "x", "y", "z")

# 结果文件格式契约前缀（R23 G-a，站长裁加校验）：内核 driver 写根
# attrs format = "genie-results-h5/<minor>"。SDK 取数入口校验前缀——
# 格式家族升级时明报，不静默按旧布局误读；缺 attrs 同样拒（G94/R19
# 实证：真产物必有根 attrs，全空的是探针残留）。
H5_FORMAT_PREFIX = "genie-results-h5/"


def sweep_axes(h5) -> list[dict] | None:
    """根 attrs ``sweep`` → 参数轴表 ``[{"name","values"}]``（G182 P5b）。

    扫描结果是**一份普通结果**，只是字段数组前面多了 k 维；这 k 维的
    名字/取值随数据走（写文件的 SDK 落 attrs）。非扫描结果 → None。
    """
    raw = h5.attrs.get("sweep")
    if raw is None or raw == "" or raw == b"":
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    ax = d.get("axes") if isinstance(d, dict) else None
    if not isinstance(ax, list) or not ax:
        return None
    return [a for a in ax if isinstance(a, dict) and a.get("name")]


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
    axis_keys = tuple(k for k in AXIS_KEYS if k in group)
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

    唯一外壳 = `studio.results_view.series`（降采样 + JSON）；
    `SimData.plot` 已于 R36 退役，曲线一致性由
    test_results_equivalence 锁。"""
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


def _point_layout(group, field: str, data):
    """单点布局（G148 批 0 钉死）：场数组 → (坐标表, 轴序, 重排后数组)。

    ``data`` = **一个点**的场（≤2 维：谱/时间 × 展平格点）；返回的
    ``order[i]`` 对应重排后数组第 i 维。扫描结果先切出点内片再进来
    （参数轴在文件级，见 `sweep_axes`）。
    """
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
    return coords, order, data


def select(group, field: str, *, axes: list | None = None,
           slices: dict | None = None, sweep: list | None = None):
    """n-D 取数尺（G148 单一真源）：h5 组 + 场 → 选轴视图。

    - ``axes=None``（缺省）：非扫描 = **峰值列折叠**（与 ``curve()``
      同口径，G142 定案，GUI 行为零漂移）；扫描 = 点内轴先摆 x/ y，
      缺的一位由第一个参数轴补上、其余参数轴取首片（就是"二维图 +
      滑条"，多出来的维默认可见）；
    - ``axes=["x"]``：1D——指定轴为横轴；``axes=["y","x"]``：2D——
      第一元素 = 横轴（x）、第二元素 = 纵轴（y），输出 data 形状
      (len(y), len(x))；
    - 可选轴名：``"time"``/``"freqs"``（谱/时间，dim0）+ 反展平成功
      的 ``"x"``/``"y"``/``"z"`` + ``"cell"``（格点下标，无法反展平
      时的空间轴）+ **扫描参数轴**（``sweep``：k 维，见 `sweep_axes`）；
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

    # 扫描结果 = 数据前 k 维是参数轴（k 不设上限）；点内布局照旧，
    # 只是把点内那片先切出来（第 0 号参数组合）再算坐标/轴序。
    lead = [a for a in (sweep or []) if a.get("name")]
    lead_shape = tuple(len(a.get("values") or []) for a in lead)

    # 非扫描缺省视图 = 峰值列折叠（G142，与 curve() 同口径）——先于布局
    # 计算：curve() 对任意 ndim 都折叠（3D 体数据历来走这里，不能因为
    # select 的 ≤2 维布局守卫而变红）。
    if axes is None and not lead:
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

    if lead:
        if (data.ndim < len(lead_shape)
                or data.shape[:len(lead_shape)] != lead_shape):
            raise ValueError(
                f"field {field!r} 形状 {data.shape} 与扫描轴 {lead_shape} "
                f"不符——结果文件的 attrs sweep 与数据自相矛盾")
        point = data[(0,) * len(lead_shape)]
    else:
        point = data
    pcoords, porder, pdata = _point_layout(group, field, point)
    if lead:
        data = data.reshape(lead_shape + pdata.shape)
        coords = {a["name"]: np.asarray(a["values"], dtype=float)
                  for a in lead}
        coords.update(pcoords)
        order = [a["name"] for a in lead] + porder
    else:
        data, coords, order = pdata, pcoords, porder

    # 扫描缺省视图：数据自己的维度先摆（点内轴当 x，二维就再占 y），
    # 缺的一位由第一个参数轴补上，其余参数轴取首片——多出来的维默认
    # 就"看得见"（二维图 + 滑条）。
    if axes is None:
        base = list(porder[:2])
        for a in lead:
            if len(base) >= 2:
                break
            base.append(a["name"])
        cut = {a["name"]: 0 for a in lead if a["name"] not in base}
        return select(group, field, axes=base, slices=cut, sweep=lead)

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


def axis_lengths(group, sweep=None) -> dict:
    """轴存在表 + 长度（G148：meta/面板的单一真源）。

    - ``time``/``freqs`` = 轴长；
    - 空间轴 = **唯一值数**（规则子盒反展平后的每维元数——不是坐标云
      的展平长度！曾把 1600 格点云当 z 轴 1600 个切片位，造出
      "z[0..1599]" 的假轴，站长真机质询抓出）；
    - 云无法反展平（Yee 裁剪/退化）→ 只报 ``cell`` = n_cells；
    - 点监视器（场 1D、无云）→ 只有谱轴；
    - ``sweep``（G182 P5b）：扫描参数轴，k 个就 k 行，排在最前
      （它们是外层维）。
    面板据此渲染轴行/滑条；轴能否真当选仍由 select() 裁决。"""
    out: dict[str, int] = {}
    spec = next((k for k in ("freqs", "time") if k in group), None)
    if spec is not None:
        out[spec] = int(np.asarray(group[spec][()]).size)
    lead = len(sweep or [])          # G182 P5b：前 k 维是参数轴，不算场维
    fields = [k for k in group if k not in _SPATIAL_KEYS and k != spec]
    n_cells = None
    for k in fields:
        shp = np.asarray(group[k][()]).shape[lead:]
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
    if sweep:
        lead = {a["name"]: len(a.get("values") or []) for a in sweep}
        lead.update(out)
        return lead
    return out
