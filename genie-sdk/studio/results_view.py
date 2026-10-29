"""结果视图纯函数（G13②：结果 = 会话状态，从会话工作区读取）。

**G182 P3 起**：结果的主来源 = **会话当前文档**（`.gensim` 内嵌 h5）；
旧 ``genie_results/*.h5`` 只作只读回退（历史作业 / result/inject 推来的
结果）。本模块把结果读成 GUI 需要的两件东西：结构清单（meta）与降采样
曲线（series）。纯函数、零 FastAPI 依赖、可单测——路由层只做参数校验
与调用。

h5 结构（genie-results-h5/1）：根 attrs（format/dt/executed_steps/
grid_shape）；每组 = 监视器（attrs 记域 f/t/i），数据集 = 场与轴
（freqs/time/x/y/z）。
"""
from __future__ import annotations

import io
import math
import re
from pathlib import Path

import h5py
import numpy as np

from genie.core import simfile
from genie.core.results import AXIS_KEYS, axis_lengths, select, sweep_axes


def _open_h5(source):
    """结果来源 → 打开的 h5py.File（调用方负责 close）。

    ``.gensim`` 文档 → 取内嵌结果字节（`simfile.result_bytes`）；
    其它路径 → 按旧 h5 直接打开。文档里没有结果 → ValueError。
    """
    p = Path(source)
    if p.suffix.lower() == ".gensim":
        data = simfile.result_bytes(p)
        if data is None:
            raise ValueError(f"仿真文档里没有结果：{p.name}")
        return h5py.File(io.BytesIO(data), "r")
    return h5py.File(p, "r")


def _job_id_of(source) -> str:
    """来源的作业 id：文档读 state.json；旧 h5 用文件名。"""
    p = Path(source)
    if p.suffix.lower() == ".gensim":
        return str(simfile.state(p).get("job_id") or "")
    return p.name.removesuffix("_results.h5")


def parse_view(axes: str | None, slices: str | None) -> dict | None:
    """GET 查询串 → select() 的 view 参数（G148 批 2）。

    ``axes="x,y"`` → ["x","y"]；``slices="z:1,time:0"`` →
    {"z":1,"time":0}。``axes`` 空 → None（缺省峰值列折叠视图，
    行为零漂移）；slices 无 axes → ValueError（没有选轴就没有切片面）。
    """
    if not axes:
        if slices:
            raise ValueError("slices 需要 axes（先选轴再定切片）")
        return None
    ax = [a.strip() for a in str(axes).split(",") if a.strip()]
    if not 1 <= len(ax) <= 2:
        raise ValueError("axes 必须是 1 个（1D）或 2 个（2D）轴名")
    sl: dict[str, int] = {}
    if slices:
        for part in str(slices).split(","):
            k, sep, v = part.strip().partition(":")
            if not sep or not k.strip() or not v.strip().lstrip("-").isdigit():
                raise ValueError(
                    f"slices 形如 '轴:下标'，拿到 {part!r}")
            sl[k.strip()] = int(v)
    return {"axes": ax, "slices": sl}


def _pool_stride(n: int, n_target: int) -> int:
    """池化步长（_pool_max 与坐标步进共用的单一真源）。

    R25 确凿 1：修前坐标步进用 ``ceil(len(coord)/z.shape)`` 另算一套，
    轴长非整除时坐标列表长度 ≠ z 行列数（101@30 → 25 列 vs 21 个 x），
    热图错位。池化输出长 = ``floor(n/stride)``，坐标必须走同一 stride。"""
    return 1 if n <= n_target else math.ceil(n / n_target)


def _pool_max(a: np.ndarray, axis: int, n_target: int) -> np.ndarray:
    """max-pool 到 ≤ n_target（保峰值结构——峰值列折叠同哲学）。

    块内用 nanmax（NaN 不劫持峰值，G142 口径；全 NaN 块保 NaN）；
    尾部不足一块的余量丢弃（可视化降采样，非取数语义）。"""
    n = a.shape[axis]
    if n <= n_target:
        return a
    import warnings
    stride = _pool_stride(n, n_target)
    k = (n // stride) * stride
    sl = [slice(None)] * a.ndim
    sl[axis] = slice(0, k)
    trimmed = a[tuple(sl)]
    new_shape = list(trimmed.shape)
    new_shape[axis] = k // stride
    new_shape.insert(axis + 1, stride)
    blocks = trimmed.reshape(new_shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmax(blocks, axis=axis + 1)
    return out


def series(source: Path, mon: str, field: str, maxpoints: int = 1200,
           axes: list | None = None,
           slices: dict | None = None) -> dict:
    """结果视图：1D 降采样曲线（缺省）或选轴 1D/2D（G148 批 2）。

    曲线本体 = core.results 取数尺单一真源——缺省视图 ``curve()``
    （G96/R19，峰值列折叠；不传 axes 行为零漂移），选轴视图
    ``select()``（G148：任意轴 1D / 2D 切面，未选轴缺省峰值切片）。
    2D 降采样 = 每轴独立 max-pool 到 ≤ maxpoints（保峰值）。
    响应 2D 形态：``z`` = 行列表（行 = y 轴，列 = x 轴），外加
    ``y_label/y``；1D 形态与历史一致，另加 ``dim`` 字段。
    ``source`` 可以是 ``.gensim`` 文档（G182 P3 主路径）或旧 h5。"""
    with _open_h5(source) as f:
        if mon not in f or field not in f[mon]:
            raise KeyError(f"监视器/场不存在: {mon}/{field}")
        view = select(f[mon], field, axes=axes, slices=slices,
                      sweep=sweep_axes(f))
    if view["dim"] == 1:
        ys = np.asarray(view["data"])
        xs = np.asarray(view["x"]["values"])
        n = int(min(maxpoints, len(ys))) if maxpoints > 0 else len(ys)
        stride = max(1, math.ceil(len(ys) / max(1, n)))
        return {"ok": True, "mon": mon, "field": field, "dim": 1,
                "x_label": view["x"]["key"],
                "x": [float(v) for v in xs[::stride]],
                "y": [float(v) for v in ys[::stride]],
                "peak": float(np.max(ys)) if len(ys) else 0.0,
                "tag": view.get("tag", "")}   # R25 缺口 9：透传 GUI
    z = np.asarray(view["data"], dtype=float)
    if maxpoints > 0:
        z = _pool_max(z, 0, maxpoints)      # 轴 0 = y
        z = _pool_max(z, 1, maxpoints)      # 轴 1 = x
    yv = np.asarray(view["y"]["values"])
    xv = np.asarray(view["x"]["values"])
    # R25 确凿 1：坐标与 z 同 stride 截到 z 行列数（xv[::sx] 在 n 非整除
    # stride 时比 z 多一个余量点，尾部截齐）。
    sy = _pool_stride(len(yv), maxpoints) if maxpoints > 0 else 1
    sx = _pool_stride(len(xv), maxpoints) if maxpoints > 0 else 1
    return {"ok": True, "mon": mon, "field": field, "dim": 2,
            "x_label": view["x"]["key"], "y_label": view["y"]["key"],
            "x": [float(v) for v in xv[::sx][:z.shape[1]]],
            "y": [float(v) for v in yv[::sy][:z.shape[0]]],
            "z": [[float(v) for v in row] for row in z],
            "peak": float(np.nanmax(z)) if z.size else 0.0,
            "tag": view.get("tag", "")}   # R25 缺口 9：透传 GUI


def results_dir(owner_dir: Path, session_id: str | None = None) -> Path:
    """结果目录：``<owner>/genie_results/``，O2 起再按 session 分一层。

    分层的理由：结果 = 会话状态。两个 session 的结果混在一个目录里，
    按 mtime 回退取"最新"会把别人的文件当成自己的（G58/G66 定案后
    已删 latest_result——结果一律按显式 job_id 取，不猜）。

    例外：``None`` / ``""`` / ``"default"`` 保持**平铺**（O2 之前的布局，
    也是旧客户端的缺省会话）——升级不动存量，旧调用零改动。
    """
    d = owner_dir / "genie_results"
    # 字面 "default" 而非 import DEFAULT_SESSION：本模块被 worker 子进程
    # 使用，不该为常量把 fastapi 拉进执行面。
    if session_id in (None, "", "default"):
        return d
    return d / session_id


def result_path(owner_dir: Path, job_id: str,
                session_id: str | None = None) -> Path:
    """owner 工作区 → 该作业的 h5 路径（job_id 白名单：字母数字-_）。"""
    import re
    if not re.fullmatch(r"[\w\-]{1,128}", job_id or ""):
        raise ValueError("job_id 非法（字母/数字/_/-，≤128）")
    return results_dir(owner_dir, session_id) / f"{job_id}_results.h5"


def locate(owner_dir: Path, job_id: str, session_id: str | None = None,
           *, prefer=()) -> Path | None:
    """``job_id`` → 结果来源（G182 P3 定位器）。

    顺序：**会话当前文档**（``prefer``：打开的文档或 ``<session>.gensim``，
    且 ``state.json.job_id`` 必须相符）→ 旧 h5 只读回退。不扫 ``runs/``
    / ``sweeps/``——那些产物由各自入口显式给文档（SDK 直跑/扫参页）。
    都没有 → None（调用方 404，话说清"可能已被后续运行覆盖"）。
    """
    if not re.fullmatch(r"[\w\-]{1,128}", job_id or ""):
        raise ValueError("job_id 非法（字母/数字/_/-，≤128）")
    for cand in prefer:
        if not cand:
            continue
        p = Path(cand)
        if not p.is_file():
            continue
        try:
            if _job_id_of(p) == job_id:
                return p
        except Exception:   # noqa: BLE001 —— 坏文档不拦回退
            continue
    hp = result_path(owner_dir, job_id, session_id)
    return hp if hp.is_file() else None


def meta(source: Path) -> dict:
    """结构清单：监视器/场/轴 + 轴长 + 根 attrs（GUI 结果面板与 Plot 建卡）。

    ``axes_len``（G148）= core.results.axis_lengths 单一真源：空间轴是
    **唯一值数**（不是坐标云展平长度——曾把 1600 格点云当 z 轴 1600 个
    切片位）；轴能否真当选由 select() 裁决。``source`` 可以是 ``.gensim``
    文档（G182 P3 主路径）或旧 h5。
    """
    with _open_h5(source) as f:
        sw = sweep_axes(f)          # G182 P5b：扫描参数轴（非扫描 → None）
        mons = []
        for name, grp in f.items():
            keys = list(grp.keys())
            mons.append({
                "name": name,
                "domain": str(grp.attrs.get("domain", "")),
                "fields": sorted(k for k in keys if k not in AXIS_KEYS),
                "axes": sorted(k for k in keys if k in AXIS_KEYS),
                "axes_len": axis_lengths(grp, sw)})
        st = {
            "ok": True,
            "job_id": _job_id_of(source),
            "executed_steps": int(f.attrs.get("executed_steps", 0)),
            "dt": float(f.attrs.get("dt", 0)),
            "grid_shape": [int(v) for v in f.attrs.get("grid_shape", [])],
            "sweep": sw,
            "monitors": mons}
    return st


def prune_over_quota(dir_: Path, quota_bytes: int) -> list[str]:
    """结果目录配额：总量超限时按 mtime 最旧先删，直到达标。

    返回被删文件名（空 = 未超限）。删除失败静默跳过（best-effort——
    配额是兜底不是硬保证）。云用户的存储上限由此在服务侧收口
    （2026-10-13 站长裁决：本地 SDK 用户不管，工作区不设自动轮换）。
    """
    if not dir_.is_dir():
        return []
    files = [p for p in dir_.iterdir() if p.is_file()]
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            return []
    if total <= quota_bytes:
        return []
    removed: list[str] = []
    for p in sorted(files, key=lambda x: x.stat().st_mtime):
        if total <= quota_bytes:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            removed.append(p.name)
        except OSError:
            continue
    return removed
