"""参数扫描编排（G29b 客户端组合；R24c run() 化重写）——
sweep = 编排层：把"跑一个仿真"原语（Simulation.run）用 N 次。

站长定案（R24c，"从最终代码简洁统一出发"）：提交一个作业的代码路径
只有 run() 一条。本模块不再有任何提交/轮询实现，只负责：

- **本地展开**：genie.models.sweep_expand（协议包同一求值核）逐点
  参数代入 + 表达式求值 → N 份纯数值配置（SweepExpandError 在
  f.sweep() 调用点即抛，对应旧 POST 422 面）；
- **窗口执行**：每点 = 一个独立 Simulation 实例走 run() 全原语——
  本地校验、auto-save（逐点 h5 落会话结果目录）、visualize/结果面
  随原语免费。max_parallel = 1（缺省）零线程顺序执行；> 1 线程池，
  每线程独占一个实例（run 内部 K4 锁为实例级，无共享状态）；
- **abort（G89 重写）**：首个坏终态停排新点 + 取消在途——不再有
  "首个成功点之后失效"的窗口；缺省 continue = 坏点记 failed 继续排；
- **gpus_per_sim（G90 构造性修复）**：每点 run(gpu=...)——旋钮真实生效；
- **digest 聚合**：逐点 GET /api/jobs/{id}/digest（小 JSON）→ 本地
  build_aggregate（NaN 语义零变化）。transmission 换算依赖内核
  source 谱归一——物理不过 SDK（铁律），digest 保持服务端读取。

铁律沿用：零计算零物理——SDK 只做线格式搬运与编排，仿真永远是内核的事。
"""
from __future__ import annotations

import json
import secrets
import threading
import time as _time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .job_state import TERMINAL_BAD as _JOB_TERMINAL_BAD
from ..models.errors import GenieHTTPError, GenieJobError

# 父终态口径（与旧服务端父状态机同名，下游消费零迁移）
SWEEP_TERMINAL_OK = "done"
SWEEP_TERMINAL_BAD = ("failed", "canceled")

AGGREGATE_NAME = "aggregate.npz"
_AXES_KEYS = ("values", "linspace", "logspace")

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
hooks: dict = {"on_event": None}


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


def unique_column(base: str, taken: set) -> str:
    """列名去重：冲突加 ``_2``/``_3`` …（确定性，先到先得）。"""
    if base not in taken:
        taken.add(base)
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    col = f"{base}_{i}"
    taken.add(col)
    return col


def flatten_numeric(obj, prefix: str = "") -> tuple[dict, list]:
    """递归展平：数值标量叶 → {键: float}；非数值叶 → 跳过键清单。

    dict 按键名、list 按下标续接键名（``monitors.0.comps.Ex.abs_max``）；
    bool 是 int 子类但语义非数值——按非数值跳过键清单。
    NaN/Inf 数值叶原样保留（聚合语义里 NaN = 缺失，与缺键同列处理）。
    """
    out: dict = {}
    skipped: list = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            sub, sub_skip = flatten_numeric(v, key)
            out.update(sub)
            skipped.extend(sub_skip)
    elif isinstance(obj, list):
        # C1（R29）：本分支原写 str(k)——k 只在 dict 分支存在，无 prefix
        # 的顶层 list 直接 NameError；正确形态是下标 i。
        for i, v in enumerate(obj):
            key = f"{prefix}.{i}" if prefix else str(i)
            sub, sub_skip = flatten_numeric(v, key)
            out.update(sub)
            skipped.extend(sub_skip)
    elif isinstance(obj, bool):
        skipped.append(prefix)
    elif isinstance(obj, (int, float)):
        out[prefix] = float(obj)
    else:
        skipped.append(prefix)
    return out, skipped


def _read_param(point: dict, name: str) -> float:
    """点配置里取参数值（点配置已解析——parameters[name] 是具体数字）。"""
    return point.get("parameters", {}).get(name, float("nan"))


def build_aggregate(points: list[dict], axis_names: list[str],
                    digests: list) -> tuple[dict, dict]:
    """纯函数：点配置 × 轴参数名 × 逐点 digest（None = 失败/取消/不可读）
    → npz 列 dict + manifest 摘要 dict。逐列手算可锚（测试主战场）。"""
    arrays: dict = {}
    axes_map: dict[str, str] = {}
    taken: set = set()
    for name in axis_names:
        col = unique_column(f"param_{name}", taken)
        arrays[col] = [_read_param(p, name) for p in points]
        axes_map[col] = name
    # 结果列：全点数值叶并集，缺失点/键 NaN
    flat = [flatten_numeric(d) if d is not None else ({}, [])
            for d in digests]
    keys = sorted({k for f, _ in flat for k in f})
    for k in keys:
        arrays[f"pt_{k}"] = [f.get(k, float("nan")) for f, _ in flat]
    skipped = sorted({s for _, sk in flat for s in sk})
    arrays["_axes"] = json.dumps(
        [{"column": c, "name": p} for c, p in axes_map.items()],
        ensure_ascii=False)
    arrays["_skipped_keys"] = json.dumps(skipped, ensure_ascii=False)
    meta = {"ok": True, "file": "aggregate.npz",
            "n_result_columns": len(keys), "axes": axes_map}
    return arrays, meta


class SweepHandle:
    """客户端 sweep 编排：N 个点实例 × run() 原语的窗口执行。

    每点是一个独立 Simulation 实例（``h.sims[i]``）——run() 原语的
    全套语义随点免费：本地校验、auto-save（逐点 h5 落会话结果目录）、
    visualize/结果面。生命周期::

        pending → running → done | canceled（abort/取消）| failed（全灭）
    """

    _GOOD = "succeeded"
    _BAD = _JOB_TERMINAL_BAD          # 单一真源（job_state 正典词表）
    _INFLIGHT = ("queued", "running")
    _TERMINAL = (_GOOD,) + _BAD + ("canceled",)

    def __init__(self, sims, axis_names: list[str], *, client,
                 points: list[dict], gpus_per_sim: int = 1,
                 max_parallel: int = 1, on_failure: str = "continue"):
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
        self._lock = threading.Lock()
        self._ex: ThreadPoolExecutor | None = None
        self._futures: list = []
        with _ACTIVE_LOCK:               # R29 C2：收尸登记表
            _ACTIVE_SWEEPS.add(self)

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
                "rollup": self.rollup(),
                "points": [{"idx": i, "state": s, "job_id": j}
                           for i, (s, j) in enumerate(zip(self.states,
                                                          self.job_ids))]}

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
        except Exception as e:  # noqa: BLE001 —— 编排面坏点记 failed
            self.job_ids[i] = s.job_id or self.job_ids[i]
            self.errors[i] = e
            self.states[i] = "failed"
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
        if any(s == "canceled" for s in self.states):
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

    def _terminal(self) -> bool:
        return self.state in (SWEEP_TERMINAL_OK,) + SWEEP_TERMINAL_BAD

    def wait(self, timeout: float | None = None, poll: float = 2.0,
             progress: bool | Callable[[dict], None] | None = None) -> dict:
        """阻塞编排直至终态。返回终态 status；坏终态抛 GenieJobError。

        ``max_parallel=1`` 顺序执行（超时抛 408 后无参重入 wait() 从
        未跑点继续）；``>1`` 线程窗（超时后后台线程继续跑，重入续等）。
        ``progress``：每步回调 ``progress(status)``；True = 打印 rollup。
        """
        if poll < 0:
            raise ValueError("poll 须 ≥ 0")
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
                        f"{self.state}——无参重入 wait() 从未跑点继续",
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
                _time.sleep(min(poll, 0.5) if poll else 0.05)
            if self._ex is not None:
                self._ex.shutdown(wait=True)
        self._finalize()
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

    # ---- 聚合与落盘 ----

    def digests(self) -> list[dict | None]:
        """逐点 digest（成功点拉取；失败/取消/未跑 = None → NaN 行）。

        G170 起本地优先：digest 随各点结果 h5 根 attrs 带出，直接读
        auto-save 的本地文件——聚合 0 次 HTTP（G166 的服务端拉取时序
        随之消失）。旧产物缺键/本地不可得回退 GET（同旧行为）。"""
        out: list[dict | None] = []
        for i, s in enumerate(self.sims):
            if self.states[i] == self._GOOD and self.job_ids[i]:
                d = None
                get_local = getattr(s, "_digest_from_local", None)
                if get_local is not None:
                    d = get_local(self.job_ids[i])
                if d is None:
                    try:
                        d = self._client.digest(self.job_ids[i])
                    except GenieHTTPError:
                        d = None
                out.append(d)
            else:
                out.append(None)
        return out

    def aggregate(self) -> dict:
        """聚合列 dict（param_* 轴列 + pt_* digest 数值列 + NaN 语义）。"""
        arrays, _meta = build_aggregate(self.points, self.axis_names,
                                        self.digests())
        return arrays

    def results(self, i: int):
        """第 i 点的 SimData（run() 原语 auto-save 的本地 h5）。"""
        return self.sims[i].results()

    def download(self, dir: str | Path) -> Path:
        """manifest + aggregate.npz 落地本地目录（形状同旧服务端产物，
        下游零迁移）。逐点完整结果已由各点实例 auto-save（run() 原语）。

        R29 S4 死面收口：``_meta``（ok/file/n_result_columns/axes——旧
        服务端 manifest 的聚合摘要字段）修前算出即丢，现并入 manifest
        的 ``aggregate`` 键（与旧产物逐字段的全量兼容核对另挂账）。"""
        import numpy as np
        root = Path(dir)
        root.mkdir(parents=True, exist_ok=True)
        arrays, meta = build_aggregate(self.points, self.axis_names,
                                       self.digests())
        manifest = self.status()
        manifest["aggregate"] = meta
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")
        np.savez_compressed(root / "aggregate.npz", **arrays)
        return root

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"<SweepHandle {self.id} {self.state}>"
