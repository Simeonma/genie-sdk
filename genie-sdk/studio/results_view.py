"""结果视图纯函数（G13②：结果 = 会话状态，从会话工作区读取）。

会话用户 `f.run()` 的结果自动落盘在 `工作区/<owner>/genie_results/
<job_id>_results.h5`（simulate._auto_save_result，SDK 用户同一行代码
落自己电脑——部署形态自动正确）。本模块把 h5 读成 GUI 需要的两件
东西：结构清单（meta）与降采样曲线（series）。纯函数、零 FastAPI
依赖、可单测——路由层（session_service / 代理）只做参数校验与调用。

h5 结构（genie-results-h5/1）：根 attrs（format/dt/executed_steps/
grid_shape）；每组 = 监视器（attrs 记域 f/t/i），数据集 = 场与轴
（freqs/time/x/y/z）。
"""
from __future__ import annotations

import math
from pathlib import Path

import h5py
import numpy as np

from genie.core.results import AXIS_KEYS, curve


def results_dir(owner_dir: Path, session_id: str | None = None) -> Path:
    """结果目录：``<owner>/genie_results/``，O2 起再按 session 分一层。

    分层的理由：结果 = 会话状态。两个 session 的结果混在一个目录里，
    ``latest_result``（页面重开的回退数据源）会把别人的最新文件当成自己的。

    例外：``None`` / ``""`` / ``"default"`` 保持**平铺**（O2 之前的布局，
    也是云宿主与旧客户端的缺省会话）——升级不动存量，旧调用零改动。
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


def latest_result(owner_dir: Path, session_id: str | None = None) -> Path | None:
    """owner 工作区最近一次的 h5（页面重开后无事件时的回退数据源）。"""
    d = results_dir(owner_dir, session_id)
    if not d.is_dir():
        return None
    files = [p for p in d.iterdir()
             if p.is_file() and p.name.endswith("_results.h5")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def meta(h5path: Path) -> dict:
    """结构清单：监视器/场/轴 + 根 attrs（GUI 结果面板与 Plot 建卡）。"""
    with h5py.File(h5path, "r") as f:
        mons = []
        for name, grp in f.items():
            keys = list(grp.keys())
            mons.append({
                "name": name,
                "domain": str(grp.attrs.get("domain", "")),
                "fields": sorted(k for k in keys if k not in AXIS_KEYS),
                "axes": sorted(k for k in keys if k in AXIS_KEYS)})
        st = {
            "ok": True,
            "job_id": h5path.name.removesuffix("_results.h5"),
            "executed_steps": int(f.attrs.get("executed_steps", 0)),
            "dt": float(f.attrs.get("dt", 0)),
            "grid_shape": [int(v) for v in f.attrs.get("grid_shape", [])],
            "monitors": mons}
    return st


def series(h5path: Path, mon: str, field: str,
           maxpoints: int = 1200) -> dict:
    """降采样曲线：|复数| 取模；体监视器（任意维）折叠成峰值格点列。
    x 轴 = 第一个出现的轴（freqs/time/x/y/z），缺失时用下标。

    曲线本体 = core.results.curve（G96/R19 取数尺单一来源——此前
    轴优先级/取模/峰值列在本文件与 SimData.plot 各写一份，无相等锁；
    两侧一致性由 test_results_equivalence 锁死）。"""
    with h5py.File(h5path, "r") as f:
        if mon not in f or field not in f[mon]:
            raise KeyError(f"监视器/场不存在: {mon}/{field}")
        axis_key, xs, ys, _tag, _cplx = curve(f[mon], mon, field)
    n = int(min(maxpoints, len(ys))) if maxpoints > 0 else len(ys)
    stride = max(1, math.ceil(len(ys) / max(1, n)))
    xs = (xs if xs is not None else np.arange(len(ys)))[::stride]
    ysel = ys[::stride]
    return {"ok": True, "mon": mon, "field": field,
            "x_label": axis_key or "idx",
            "x": [float(v) for v in xs],
            "y": [float(v) for v in ysel],
            "peak": float(np.max(ys)) if len(ys) else 0.0}


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
