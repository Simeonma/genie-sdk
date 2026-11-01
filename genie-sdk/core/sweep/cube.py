"""扫描立方：两类（活句柄 / 只读集合）共享的模块级结果实现。

从 core/sweep.py 剪切（T5 结构重构，零行为变化）：数据数组形状 =
(各扫描轴取值数 …) + 点内形状；坐标（AXIS_KEYS）一条轴仍是一个向量、
不复制；失败点 = 该切片 NaN；形状对不上（扫参改了网格）明报不猜。
k 不设上限——协议面已经限了 ≤32 轴。
"""
from __future__ import annotations

import io
import json
import math

import numpy as np

from ..results import AXIS_KEYS

MANIFEST_NAME = "sweep.json"

# ── 事件钩子（G168/R34，站长裁"汇总为王，点级降噪"）──────────────────
# worker 面注入的**观测面**（与 simulate.hooks 同款注入模式）：
#   每点终态 {"type":"point", sweep_id, idx, state, job_id, total, rollup}
#   扫描终态 {"type":"sweep_done", sweep_id, state, rollup, total}
# None（缺省）= 零开销直通——独立 SDK 使用者无感。回调异常吞掉
# （R29 S3 同款纪律：观测面不得有控制权，不拦编排）。
hooks: dict = {"on_event": None, "visualize_sweep": None}


def _grid(obj) -> tuple[int, ...]:
    """扫描轴取值数（轴序 = ``axis_names``）；非笛卡尔（zip）→ (点数,)。"""
    vals = obj.params
    sizes = [len(dict.fromkeys(np.asarray(vals[a]).tolist()))
             for a in obj.axis_names]
    n = len(obj.points)
    if not sizes or math.prod(sizes) != n:
        return (n,)                    # zip：点序就是唯一的那一维
    return tuple(sizes)


def _sweep_id(obj) -> str:
    return str(getattr(obj, "id", None)
               or (getattr(obj, "manifest", {}) or {}).get("sweep_id") or "")


def _point_state(obj, i: int) -> tuple[str, str]:
    """第 i 点的 (state, job_id)——活句柄读内存数组，只读集合读索引。"""
    states = getattr(obj, "states", None)
    if states is not None:
        jids = getattr(obj, "job_ids", None) or [None] * len(states)
        return str(states[i]), str(jids[i] or "")
    p = obj.points[i] or {}
    return str(p.get("state") or ""), str(p.get("job_id") or "")


def _cube_meta(obj) -> dict:
    """合并结果的根 attrs ``sweep``：轴名/取值（与 `_grid` 同序）+ 点表。

    数据自带坐标——谁拿到这份结果都知道前 k 维是什么，不用另传参数表。
    """
    vals = obj.params
    axes = [{"name": a,
             "values": list(dict.fromkeys(np.asarray(vals[a]).tolist()))}
            for a in obj.axis_names]
    pts = []
    for i in range(len(obj.points)):
        st, jid = _point_state(obj, i)
        pts.append({"idx": i, "state": st, "job_id": jid})
    return {"sweep_id": _sweep_id(obj),
            "name": str(getattr(obj, "name", "") or ""),
            "dir": str(getattr(obj, "dir", "") or ""),
            "state": str(getattr(obj, "state", "") or ""),
            "axes": axes, "grid": list(_grid(obj)), "points": pts}


def _open_runs(blobs: list) -> list:
    """逐点结果字节 → 打开的 h5py.File 列表（None = 失败/无结果）。"""
    import h5py
    return [h5py.File(io.BytesIO(b), "r") if b else None for b in blobs]


def _merge_cube(runs: list, grid: tuple, meta: dict) -> bytes:
    """逐点结果 h5（None = 失败）→ 合并结果 h5 的字节（数据多 k 维）。"""
    import h5py

    base = next((r for r in runs if r is not None), None)
    if base is None:
        raise RuntimeError("扫描还没有任何成功点——没有结果可读")
    buf = io.BytesIO()
    with h5py.File(buf, "w") as out:
        for k, v in base.attrs.items():
            out.attrs[k] = v
        out.attrs["sweep"] = json.dumps(meta, ensure_ascii=False)
        for mon in base:
            src = base[mon]
            dst = out.create_group(mon)
            for k, v in src.attrs.items():
                dst.attrs[k] = v
            for key in src:
                if key in AXIS_KEYS:          # 坐标：一条轴一个向量
                    dst.create_dataset(key, data=src[key][()])
                    continue
                a0 = src[key][()]
                rows = []
                for i, r in enumerate(runs):
                    if r is None:             # 失败点：NaN 切片，形状不塌
                        rows.append(np.full(a0.shape, np.nan))
                        continue
                    if mon not in r or key not in r[mon]:
                        raise ValueError(
                            f"点 {i + 1} 缺 {mon}/{key}——首点有、它没有："
                            f"扫描改了监视器配置，不能摞成一个结果")
                    a = r[mon][key][()]
                    if a.shape != a0.shape:
                        raise ValueError(
                            f"点 {i + 1} 的 {mon}/{key} 形状 {a.shape} ≠ "
                            f"首点 {a0.shape}——扫参改了网格，不能摞成一个结果")
                    rows.append(a)
                dst.create_dataset(
                    key, data=np.stack(rows).reshape(grid + a0.shape))
    return buf.getvalue()


def _results(obj):
    """扫描 → 合并结果对象（与单跑同一个 ``SimData``）。"""
    import h5py

    from ..results import SimData

    return SimData(h5py.File(io.BytesIO(_cube_bytes(obj)), "r"),
                   job_id=_sweep_id(obj))


def _cube_bytes(obj) -> bytes:
    """逐点结果 → 合并结果 h5 字节（数据多 k 维；坐标不复制）。"""
    blobs = [obj._point_bytes(i) for i in range(len(obj.points))]
    runs = _open_runs(blobs)
    try:
        return _merge_cube(runs, _grid(obj), _cube_meta(obj))
    finally:
        for h in runs:
            if h is not None:
                h.close()


def _visualize(obj, mon: str, field: str | None = None):
    """扫描看图：与 ``f.visualize`` 同一个动词、同一个 Plot 面板。

    G182 P5c：推的是合并结果（数据多 k 维）——面板已有的轴选择器/
    滑条把参数轴当普通轴用，画图实现只有一套。没有 Studio → 明确报错
    （SDK 面零 matplotlib，G148 定案）。
    """
    hook = hooks.get("visualize_sweep")
    if hook is None:
        raise RuntimeError(
            "visualize needs a GUI session — open `genie studio` and run "
            "the script inside it (the session wires visualize), or "
            "connect via a session (bare connect) for the external push "
            "channel; there is no local plotting fallback by design "
            "(G148)")
    return hook(obj, mon, field)
