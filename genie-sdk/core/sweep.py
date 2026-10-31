"""参数扫描编排（G29b 客户端组合；R24c run() 化重写）——
sweep = 编排层：把"跑一个仿真"原语（Simulation.run）用 N 次。

站长定案（R24c，"从最终代码简洁统一出发"）：提交一个作业的代码路径
只有 run() 一条。本模块不再有任何提交/轮询实现，只负责：

- **本地展开**：genie.models.sweep_expand（协议包同一求值核）逐点
  参数代入 + 表达式求值 → N 份纯数值配置（SweepExpandError 在
  f.sweep() 调用点即抛，对应旧 POST 422 面）；
- **窗口执行**：每点 = 一个独立 Simulation 实例走 run() 全原语——
  本地校验、auto-save（逐点 `.gensim` 点文档落扫描文件夹）、visualize/结果面
  随原语免费。max_parallel = 1（缺省）零线程顺序执行；> 1 线程池，
  每线程独占一个实例（run 内部 K4 锁为实例级，无共享状态）；
- **abort（G89 重写）**：首个坏终态停排新点 + 取消在途——不再有
  "首个成功点之后失效"的窗口；缺省 continue = 坏点记 failed 继续排；
- **错误归属（R37）**：点的配置错（GenieConfigError）/作业坏终态
  （GenieJobError）= **点的事**，按 abort/continue 策略处理；其它异常
  （凭据失效、传输断开、网关拒绝）= **环境的事**，第一次出现就停排，
  `wait()` 原样抛出一次——一把坏 key 不再变成 N 个失败点；
- **gpus_per_sim（G90 构造性修复）**：每点 run(gpu=...)——旋钮真实生效；
- **结果 = 立方**（G182 P5b，站长 2026-10-30 裁）：扫描不新增结果面——
  N 份点结果摞成**一份普通结果**（``results()``）：每个字段数组前加
  k 维（k = 扫描轴数，不设上限；轴序 = 轴序声明序），坐标
  （freqs/time/x/y/z）一条轴仍是一个向量，失败点 = NaN 切片。
  取单点就是 ``r.tr.T[i]``（numpy 切片），不另设逐点入口。
  transmission 等换算依赖内核 source 谱归一——物理不过 SDK（铁律）。

铁律沿用：零计算零物理——SDK 只做线格式搬运与编排，仿真永远是内核的事。
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import secrets
import threading
import time as _time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .job_state import TERMINAL_BAD as _JOB_TERMINAL_BAD
from .results import AXIS_KEYS
from ..models.errors import GenieConfigError, GenieHTTPError, GenieJobError

# 父终态口径（与旧服务端父状态机同名，下游消费零迁移）
SWEEP_TERMINAL_OK = "done"
SWEEP_TERMINAL_BAD = ("failed", "canceled")

MANIFEST_NAME = "sweep.json"


def _slug(text: str) -> str:
    """扫描名 → 文件名安全 slug（字母/数字/_/-；空 → "sweep"）。"""
    s = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(text or "").strip()).strip("_-")
    return (s or "sweep")[:48]


def new_sweep_dir(name, axis_names) -> tuple[Path, str]:
    """建一个扫描文件夹：``<workspace>[/owner]/sweeps/<slug>-<时间戳>/``。

    返回 ``(目录, slug)``；同名同秒冲突加 ``-2/-3…``。文件夹在**开始前**
    就建好（``sweep.json`` 先登记全部 pending 点——文档本身点在终态才写，
    不预建，站长 2026-10-30 裁）。
    """
    from . import paths
    base = paths.sweeps_dir()
    slug = _slug(name or (axis_names[0] if len(axis_names or []) == 1
                          else "sweep"))
    stamp = _time.strftime("%Y%m%d-%H%M%S")
    d = base / f"{slug}-{stamp}"
    k = 2
    while d.exists():
        d = base / f"{slug}-{stamp}-{k}"
        k += 1
    d.mkdir(parents=True, exist_ok=True)
    return d, slug

# ── 活跃句柄登记表（R29 C2，站长裁"模块级登记"）──────────────────────
# 会话停止/超时的收尸面用：sweep 点实例**不进会话 sink**（不污染模型
# 合并——kernel_worker 工厂注释明示），收尸侧（_cancel_orphan_jobs）
# 修前扫不到在途点 → 停止后点作业孤儿烧卡（违反 A2「长任务必须能掐」）。
# WeakSet = 句柄被 GC 自动除名，零泄漏。
_ACTIVE_SWEEPS: "weakref.WeakSet[SweepHandle]" = weakref.WeakSet()
_ACTIVE_LOCK = threading.Lock()

# ── 事件钩子（G168/R34，站长裁"汇总为王，点级降噪"）──────────────────
# 会话面注入的**观测面**（与 simulate.hooks 同款注入模式）：
#   每点终态 {"type":"point", sweep_id, idx, state, job_id, total, rollup}
#   扫描终态 {"type":"sweep_done", sweep_id, state, rollup, total}
# None（缺省）= 零开销直通——独立 SDK 使用者无感。回调异常吞掉
# （R29 S3 同款纪律：观测面不得有控制权，不拦编排）。
hooks: dict = {"on_event": None, "visualize_sweep": None}


def _emit_event(ev: dict) -> None:
    cb = hooks.get("on_event")
    if cb is None:
        return
    try:
        cb(ev)
    except Exception:  # noqa: BLE001 —— 观测面异常不拦编排
        pass


def active_handles() -> list["SweepHandle"]:
    """当前进程内所有存活 SweepHandle（收尸/审计面用）。"""
    with _ACTIVE_LOCK:
        return list(_ACTIVE_SWEEPS)


def _read_param(point: dict, name: str) -> float:
    """点配置里取参数值（点配置已解析——parameters[name] 是具体数字）。"""
    return point.get("parameters", {}).get(name, float("nan"))


# ── 立方：扫描 = 结果多 k 维（G182 P5b，站长 2026-10-30 裁）─────────────
# 数据数组形状 = (各扫描轴取值数 …) + 点内形状；坐标（AXIS_KEYS）一条轴
# 仍是一个向量、不复制；失败点 = 该切片 NaN；形状对不上（扫参改了网格）
# 明报不猜。k 不设上限——协议面已经限了 ≤32 轴。


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

    from .results import SimData

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
    滑条把参数轴当普通轴用，画图实现只有一套。无会话 → 明确报错
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


class SweepSet:
    """磁盘上的一个扫描（G182 P5）：读 ``sweep.json`` + 点文档。

    **不打开任何仿真实例**（站长 2026-10-30 裁：读取只要聚合结果）——
    公开入口 ``genie.open_sweep(dir)``。可用于新会话/另一台机器。
    """

    def __init__(self, sweep_dir):
        p = Path(sweep_dir).expanduser()
        if not p.is_dir():
            raise ValueError(f"扫描文件夹不存在：{p}")
        mp = p / MANIFEST_NAME
        if not mp.is_file():
            raise ValueError(
                f"不是扫描文件夹（缺 {MANIFEST_NAME}）：{p}")
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"扫描索引读不了（{mp}）：{e}") from e
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError(f"扫描索引版本不支持：{mp}")
        self.dir = p
        self.manifest = data
        self.name = str(data.get("name") or "")
        self.axes = data.get("axes") or []
        self.state = str(data.get("state") or "")
        self.created = str(data.get("created") or "")
        self.settings = dict(data.get("settings") or {})
        self.points = list(data.get("points") or [])
        self.axis_names = [a.get("name") for a in self.axes
                           if isinstance(a, dict) and a.get("name")]
        if not self.axis_names and self.points:
            self.axis_names = list(self.points[0].get("params") or {})

    # ---- 元数据 ----

    @property
    def id(self) -> str:
        """扫描 id（sweep.json 的 ``sweep_id``）——visualize 的结果键。"""
        return str(self.manifest.get("sweep_id") or "")

    def doc_path(self, i: int) -> Path | None:
        """第 i 点的文档路径（文件夹内相对名 → 绝对；无 → None）。"""
        name = str((self.points[i] or {}).get("doc") or "")
        return (self.dir / name) if name else None

    def rollup(self) -> dict:
        c: dict = {}
        for p in self.points:
            key = str(p.get("state") or "pending")
            c[key] = c.get(key, 0) + 1
        return c

    def status(self) -> dict:
        return {"sweep_id": self.manifest.get("sweep_id", ""),
                "state": self.state, "dir": str(self.dir),
                "rollup": self.rollup(),
                "points": [{"idx": i, "state": p.get("state"),
                            "job_id": p.get("job_id"),
                            "doc": p.get("doc")}
                           for i, p in enumerate(self.points)]}

    # ---- 结果（扫描 = 结果多 k 维；只读文档，零 HTTP）----

    @property
    def params(self) -> dict:
        """{轴名: 逐点取值}——轴序 = ``axis_names``（笛卡尔积序）。"""
        return {a: np.asarray([(p.get("params") or {}).get(a, np.nan)
                               for p in self.points], dtype=float)
                for a in self.axis_names}

    def _point_bytes(self, i: int):
        """第 i 点的结果字节（失败/无文档/坏文档 → None ⇒ NaN 切片）。"""
        from . import simfile
        doc = self.doc_path(i)
        if doc is None or not doc.is_file():
            return None
        try:
            return simfile.result_bytes(doc)
        except Exception:   # noqa: BLE001 —— 坏点文档 → NaN 切片
            return None

    def results(self):
        """扫描结果 → ``SimData``（与单跑同一个类型，数据多 k 维）。

        ``r.tr.T`` 形状 = (n1, …, nk) + 点内形状（轴序 = ``axes``）；
        坐标（``r.tr.freqs``）不复制；取单点就是 ``r.tr.T[i]``。
        """
        return _results(self)

    def _cube_bytes(self) -> bytes:
        return _cube_bytes(self)

    def visualize(self, mon: str, field: str | None = None) -> None:
        """把扫描结果推到 Studio 的 Plot 面板（与 ``f.visualize`` 同款）。

        推的是合并后的结果（数据多 k 维）——面板把参数轴当普通轴。
        无会话 = 明确报错（无本地画图退化轨，G148）。
        """
        return _visualize(self, mon, field)

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"<SweepSet {self.name!r} {self.state} dir={self.dir}>"


class SweepHandle:
    """客户端 sweep 编排：N 个点实例 × run() 原语的窗口执行。

    每点是一个独立 Simulation 实例（``h.sims[i]``）——run() 原语的
    全套语义随点免费：本地校验、auto-save（逐点 `.gensim` 点文档）、
    visualize/结果面。生命周期::

        pending → running → done | canceled（abort/取消）| failed（全灭）
    """

    _GOOD = "succeeded"
    _BAD = _JOB_TERMINAL_BAD          # 单一真源（job_state 正典词表）
    _INFLIGHT = ("queued", "running")
    _TERMINAL = (_GOOD,) + _BAD + ("canceled",)

    def __init__(self, sims, axis_names: list[str], *, client,
                 points: list[dict], gpus_per_sim: int = 1,
                 max_parallel: int = 1, on_failure: str = "continue",
                 dir: str | Path | None = None, name: str = "",
                 axes=None):
        self.sims = list(sims)
        self.points = list(points)      # 逐点已解析配置（聚合的 param 列源）
        self.axis_names = list(axis_names)
        self._client = client
        self.gpus_per_sim = gpus_per_sim
        self.max_parallel = max(1, int(max_parallel))
        self.on_failure = str(on_failure)
        self.id = "sw-" + secrets.token_urlsafe(6)
        self.states: list[str] = ["pending"] * len(self.sims)
        self.job_ids: list[Any | None] = [None] * len(self.sims)
        self.errors: list[BaseException | None] = [None] * len(self.sims)
        self.state = "pending"
        self._aborting = False
        # G182 P4：扫描 = 一个文件夹（sweep.json 索引 + 每点一份文档）。
        # dir 只在经 f.sweep 构造时给；直接构造（阈值测试）不写盘。
        self.dir: Path | None = Path(dir) if dir is not None else None
        self.name = str(name or "")
        self.axes = axes
        self.created = _time.strftime("%Y-%m-%dT%H:%M:%S")
        self.doc_paths: list[Path | None] = [
            (self.dir / f"{self.name}-{i + 1:04d}.gensim")
            if self.dir is not None and self.name else None
            for i in range(len(self.sims))]
        # 环境级错误（凭据/传输/网关拒绝——不是某一个点自己的问题）：
        # 第一次出现就停排、由 wait() 原样抛出一次；不记在任何点的账上。
        self._fatal: BaseException | None = None
        self._lock = threading.Lock()
        self._ex: ThreadPoolExecutor | None = None
        self._futures: list = []
        with _ACTIVE_LOCK:               # R29 C2：收尸登记表
            _ACTIVE_SWEEPS.add(self)
        self._write_manifest()           # 全部点先登记 pending（可见性）

    # ---- 编排 ----

    def rollup(self) -> dict:
        c: dict = {}
        for s in self.states:
            key = ("running" if s in self._INFLIGHT
                   else "pending" if s == "pending" else s)
            c[key] = c.get(key, 0) + 1
        return c

    def status(self) -> dict:
        return {"sweep_id": self.id, "state": self.state,
                "dir": str(self.dir) if self.dir is not None else "",
                "rollup": self.rollup(),
                "points": [{"idx": i, "state": s, "job_id": j}
                           for i, (s, j) in enumerate(zip(self.states,
                                                          self.job_ids))]}

    # ---- 文档化（G182 P4：扫描 = 一个文件夹）------------------------

    def _manifest_dict(self) -> dict:
        pts = []
        for i in range(len(self.sims)):
            params = {}
            for n in self.axis_names:
                try:
                    params[n] = _read_param(self.points[i], n)
                except Exception:   # noqa: BLE001 —— 参数缺省记 None
                    params[n] = None
            err = self.errors[i]
            p = {"idx": i, "params": params,
                 "doc": self.doc_paths[i].name if self.doc_paths[i] else "",
                 "job_id": self.job_ids[i], "state": self.states[i]}
            if err is not None:
                p["error"] = str(err)[:300]
            pts.append(p)
        return {"version": 1, "sweep_id": self.id, "name": self.name,
                "created": self.created, "state": self.state,
                "axes": self.axes,
                "settings": {"gpus_per_sim": self.gpus_per_sim,
                             "max_parallel": self.max_parallel,
                             "on_failure": self.on_failure},
                "points": pts}

    def _write_manifest(self) -> None:
        """``sweep.json`` 原子更新（索引不是真源：文档才是；点状态镜像）。

        pending 时先写一次（全部点可见）；每点终态与扫描终态各更新一次。
        """
        if self.dir is None:
            return
        try:
            with self._lock:
                p = self.dir / MANIFEST_NAME
                tmp = p.with_name(p.name + ".part")
                tmp.write_text(json.dumps(self._manifest_dict(),
                                          ensure_ascii=False, indent=2),
                               encoding="utf-8")
                os.replace(tmp, p)
        except OSError:
            pass

    def _write_point_doc(self, i: int) -> None:
        """点终态 → 它自己的 ``.gensim``（G182 P4）。

        成功点：``Simulation.run`` 已写（``_doc_path`` 在 f.sweep 里钉好）；
        失败/取消点：这里写——**没有结果也要有文档**（state/error 可审计）。
        文档写失败不拦扫描编排（点状态已经定格、事件已经发出）。
        """
        doc = self.doc_paths[i]
        if doc is None:
            return
        from . import simfile
        s = self.sims[i]
        st = self.states[i]
        try:
            if st == self._GOOD:
                if doc.is_file():
                    return
                s.save(doc)          # 兜底：run 内保存失败的极小概率
                return
            if st == "pending":
                return
            cfg = s.to_config() if hasattr(s, "to_config") else self.points[i]
            state: dict = {"job_id": str(self.job_ids[i] or ""), "state": st}
            if self.errors[i] is not None:
                state["error"] = str(self.errors[i])[:2000]
            simfile.write(doc, cfg, state=state)
        except Exception:   # noqa: BLE001 —— 文档写失败不炸扫描
            pass

    def _run_point(self, i: int) -> None:
        with self._lock:
            if self._aborting and self.states[i] == "pending":
                self.states[i] = "canceled"
                return
            if self.states[i] in self._TERMINAL:
                return                     # 重入 wait：已完成点跳过
            self.states[i] = "running"
        s = self.sims[i]
        try:
            s.run(gpu=self.gpus_per_sim)
            with self._lock:
                # R29 S2：cancel()/_finalize 可能在 run 进行中已把本点
                # 定格 canceled（取消请求 best-effort 迟到/失败）——成功
                # 回写不得覆盖取消定格，否则终态归并对偶不一致
                # （handle.state=canceled 而 rollup 全 succeeded）。
                if self.states[i] != "canceled":
                    self.states[i] = self._GOOD
        except GenieJobError as e:
            self.job_ids[i] = s.job_id
            self.errors[i] = e    # R29 P1：坏终态细节也落 errors（消费面）
            with self._lock:
                aborting = self._aborting
            if aborting and e.state in ("killed", "canceled"):
                self.states[i] = "canceled"
            else:
                self.states[i] = e.state or "failed"
        except GenieConfigError as e:
            # 点的配置问题 = 点自己的事（与作业终态失败同档：记 failed 继续）
            self.job_ids[i] = s.job_id or self.job_ids[i]
            self.errors[i] = e
            self.states[i] = "failed"
        except Exception as e:  # noqa: BLE001 —— 环境级错误：停排、原样抛出
            # 凭据失效/传输断开/网关拒绝这类错误，**每个点都会一模一样地
            # 失败**——它是扫描的事，不是点的事。记在扫描级并立刻停排；
            # 不写点状态、不发点事件（这个点根本没跑成）。
            with self._lock:
                if self._fatal is None:
                    self._fatal = e
                self._aborting = True
            return
        else:
            self.job_ids[i] = s.job_id
        finally:
            newly = False
            with self._lock:
                if (self.on_failure == "abort"
                        and self.states[i] in self._BAD
                        and not self._aborting):
                    self._aborting = newly = True
            if newly:
                # R29 S1：取消级联跑两遍（间隔 0.2s）——单遍在兄弟点
                # "states→running 之后、submit 赋 job_id 之前"的微秒窗
                # 读到 None 空转，该点漏取消自然跑完（G89 残余洞）；
                # 双遍把窗口收窄到 0.2s 内仍未提交的极端情形。
                for _pass in range(2):
                    for j, s2 in enumerate(self.sims):
                        if j == i:
                            continue
                        with self._lock:
                            inflight = self.states[j] in self._INFLIGHT
                        # 实时 job_id 在点实例身上（提交即设）——句柄侧
                        # self.job_ids[j] 要等 run() 返回才回填， abort 取消
                        # 恰发生在 run 进行中，读句柄数组会拿到 None 空转。
                        jid2 = getattr(s2, "job_id", None) or self.job_ids[j]
                        if inflight and jid2:
                            try:
                                s2._client.cancel(jid2)
                            except Exception:  # noqa: BLE001 —— 取消尽力而为
                                pass
                    if _pass == 0 and self.max_parallel > 1:
                        # R30 卫生：顺序路径（max_parallel=1）无并发提交
                        # 竞态，第二遍必然空转——只在并行面睡这 0.2s。
                        _time.sleep(0.2)
        # （R30 GAP 清理：原"非作业终态异常不上抛"的守卫段在 P1 把
        #  GenieJobError 也落 errors[] 之后成为死码，删——编排语义
        #  "坏点记 failed/canceled 继续，不炸 sweep"由上方 except 面承袭。）
        # G168/R34：点终态事件（会话面汇总播报的数据源；finally 完成后
        # 本点状态已定格——cancel 覆盖与 abort 级联都在上面发生）。
        # detail = 坏点原因（截断）——点级过程播报降噪后，这是失败原因
        # 唯一的终端出口（诊断性不随降噪丢失）。
        err = self.errors[i]
        _emit_event({"type": "point", "sweep_id": self.id, "idx": i,
                     "state": self.states[i],
                     "job_id": self.job_ids[i],
                     "detail": (str(err)[:200] if err else ""),
                     "total": len(self.sims),
                     "rollup": self.rollup()})
        # G182 P4：点终态 → 点文档 + sweep.json 增量更新
        self._write_point_doc(i)
        self._write_manifest()

    def _window(self) -> int:
        """并发窗 = min(max_parallel, 配额并发上限)——少撞 429 的礼貌。

        配额**查不到按用户声明值**：whoami 异常与在线但 quota 缺键
        （R30 C-B：修前 `or 1` 把缺键静默塌成 1，声明 max_parallel=3
        被无视，与本文档承诺相反）都不收窄。"""
        cap = self.max_parallel
        try:
            q = self._client.whoami().get("quota") or {}
            mcr = q.get("max_concurrent_runs")
            if mcr:
                cap = min(cap, max(1, int(mcr)))
        except Exception:  # noqa: BLE001 —— 查不到配额按用户声明值
            pass
        return max(1, cap)

    def _start_parallel(self) -> None:
        if self._ex is not None:
            return
        n = len(self.sims)
        self._ex = ThreadPoolExecutor(max_workers=min(self._window(), n),
                                      thread_name_prefix=f"sw-{self.id}")
        self._futures = [self._ex.submit(self._run_point, i) for i in range(n)]

    def _finalize(self) -> None:
        # R30 S-A：states 写入进锁——修前无锁写"canceled"恰是 S2 锁内
        # check-then-set 挡不住的写者（点线程锁内读到 running 通过检查
        # → _finalize 无锁写 canceled → 点线程写 _GOOD 覆盖定格）。
        with self._lock:
            for i, s in enumerate(self.states):
                if s == "pending" or s in self._INFLIGHT:
                    self.states[i] = "canceled"
        bad = SWEEP_TERMINAL_BAD + ("killed", "archived")
        if self._fatal is not None:
            # 环境级错误：不是"用户取消"，也不是全部点失败——扫描中止。
            self.state = "failed"
        elif any(s == "canceled" for s in self.states):
            self.state = "canceled"
        elif self.states and all(s in bad for s in self.states):
            self.state = "failed"
        else:
            self.state = SWEEP_TERMINAL_OK
        # G168/R34：扫描终态事件（调用方可能多次 _finalize——cancel 后
        # 重入 wait 再终态；消费侧按 sweep_id 去重）。last_job_id =
        # 最后一个成功点的作业号——会话面在终态补发单条 job-done
        # （结果态切换平价：修前由每点的 job-done 顺带完成，降噪后由
        # 这里一次性接手）。
        last_job = None
        for _i in range(len(self.states) - 1, -1, -1):
            if self.states[_i] == self._GOOD and self.job_ids[_i]:
                last_job = self.job_ids[_i]
                break
        _emit_event({"type": "sweep_done", "sweep_id": self.id,
                     "state": self.state,
                     "rollup": self.rollup(),
                     "last_job_id": last_job,
                     "total": len(self.sims)})
        # G182 P4：扫描终态 → sweep.json 最终态
        self._write_manifest()

    def _terminal(self) -> bool:
        return self.state in (SWEEP_TERMINAL_OK,) + SWEEP_TERMINAL_BAD

    def wait(self, timeout: float | None = None,
             progress: bool | Callable[[dict], None] | None = None) -> dict:
        """阻塞编排直至终态。返回终态 status；坏终态抛 GenieJobError。

        ``timeout``：本地等待墙钟（G172/R35 定案语义，两路径不同——
        **并行路径**每 ~0.5s 检查一次，到点即抛，后台线程继续跑、无参
        重入续等；**顺序路径**只在点间检查——当前点会跑完才抛，单点
        长跑可远超 timeout，408 后无参重入 wait() 从未跑点继续）。
        ``progress``：每步回调 ``progress(status)``；True = 打印 rollup。
        """
        on_progress = progress if callable(progress) else (
            (lambda st: print(f"[sweep {self.id}] "
                              + " ".join(f"{k}={v}" for k, v in
                                         sorted(st["rollup"].items()))))
            if progress is True else None)

        def _safe_progress(st: dict) -> None:
            # R29 S3（站长裁"吞掉+警告继续"）：progress 是观测面，不该有
            # 控制权——修前回调异常直接炸 wait 且顺序路径停排新点、state
            # 卡 running（隐式 abort）。异常打警告进诊断，扫描继续。
            if on_progress is None:
                return
            try:
                on_progress(st)
            except Exception as e:  # noqa: BLE001 —— 观测面异常不拦编排
                print(f"[sweep {self.id}] progress 回调异常（忽略，"
                      f"扫描继续）: {e!r}", flush=True)
        if self.state == "pending":
            self.state = "running"
            if self.max_parallel > 1:
                self._start_parallel()
        t0 = _time.monotonic()

        def _timed_out() -> bool:
            return timeout is not None and _time.monotonic() - t0 > timeout

        if self.max_parallel == 1:
            for i in range(len(self.sims)):
                if self._terminal():
                    break
                self._run_point(i)
                st = self.status()
                _safe_progress(st)
                if self.on_failure == "abort" and self._aborting:
                    break
                if _timed_out():
                    raise GenieHTTPError(
                        f"本地等待超时（>{timeout}s）：sweep {self.id} 当前 "
                        f"{self.state}——顺序执行在点间检查超时（当前点跑完"
                        f"才抛）；无参重入 wait() 从未跑点继续",
                        status=408)
        else:
            while True:
                st = self.status()
                _safe_progress(st)
                if all(f.done() for f in self._futures):
                    break
                if _timed_out():
                    raise GenieHTTPError(
                        f"本地等待超时（>{timeout}s）：sweep {self.id} 当前 "
                        f"{self.state}——后台线程继续，无参重入 wait() 续等",
                        status=408)
                _time.sleep(0.5)
            if self._ex is not None:
                self._ex.shutdown(wait=True)
        self._finalize()
        if self._fatal is not None:
            # 环境级错误原样抛一次（服务端的原话，不翻译）——调用方看得见
            # "是哪把凭据/哪条链断的"，而不是 N 行一模一样的点失败。
            raise self._fatal
        st = self.status()
        if st["state"] in SWEEP_TERMINAL_BAD:
            raise GenieJobError(
                self.id, st["state"],
                "rollup: " + json.dumps(st["rollup"], ensure_ascii=False))
        return st

    def cancel(self) -> dict:
        """取消：在途子作业走 cancel，余点标 canceled。"""
        with self._lock:
            self._aborting = True
        for j, s2 in enumerate(self.sims):
            with self._lock:
                inflight = self.states[j] in self._INFLIGHT
            jid2 = getattr(s2, "job_id", None) or self.job_ids[j]
            if inflight and jid2:
                try:
                    s2._client.cancel(jid2)
                except Exception:  # noqa: BLE001 —— 尽力而为
                    pass
        self._finalize()
        return {"sweep_id": self.id, "state": self.state}

    # ---- 结果（扫描 = 结果多 k 维）----

    @property
    def params(self) -> dict:
        """{轴名: 逐点取值}——轴序 = ``axis_names``（笛卡尔积序）。"""
        return {a: np.asarray([_read_param(p, a) for p in self.points],
                              dtype=float)
                for a in self.axis_names}

    def _point_bytes(self, i: int):
        """第 i 点的结果字节：先读它的文档（G182 P4 产物），
        无文档（未落盘/测试直构）才退回实例自身的结果。"""
        from . import simfile
        doc = self.doc_paths[i] if i < len(self.doc_paths) else None
        if doc is not None and doc.is_file():
            try:
                return simfile.result_bytes(doc)
            except Exception:   # noqa: BLE001 —— 坏文档 → NaN 切片
                return None
        if self.states[i] == self._GOOD:
            try:
                return self.sims[i]._result_bytes()
            except Exception:   # noqa: BLE001 —— 取不到 → NaN 切片
                return None
        return None

    def results(self):
        """扫描结果 → ``SimData``（与单跑同一个类型，数据多 k 维）。

        ``r.tr.T`` 形状 = (n1, …, nk) + 点内形状（轴序 = ``axis_names``）；
        坐标（``r.tr.freqs``）不复制；取单点就是 ``r.tr.T[i]``。
        """
        return _results(self)

    def _cube_bytes(self) -> bytes:
        return _cube_bytes(self)

    def visualize(self, mon: str, field: str | None = None) -> None:
        """把扫描结果推到 Studio 的 Plot 面板（与 ``f.visualize`` 同款）。

        推的是合并后的结果（数据多 k 维）——面板把参数轴当普通轴。
        无会话 = 明确报错（无本地画图退化轨，G148）。
        """
        return _visualize(self, mon, field)

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"<SweepHandle {self.id} {self.state} dir={self.dir}>"
