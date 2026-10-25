"""参数扫描句柄（G29b 2026-10-21 重写）——sweep = 普通作业的客户端组合。

站长定案："sweep 本质 = 同一用户提交的多个 job，有空卡并行没卡排队"——
内核不再有 sweep 面（/api/sweeps 退役），本模块承接全部编排：

- **本地展开**：``genie.models.sweep_expand``（与服务端旧实现同源移植，
  轴语义/错误信息零变化——SweepExpandError 在 f.sweep() 调用点即抛，
  对应旧 POST 422 面）；
- **滑动窗口提交**：同时保持 ≤ min(max_parallel, quota.max_concurrent_runs)
  个作业在途，跑完一个补一个（"有空卡并行没卡串行"的客户端化；窗口
  上限经网关 /whoami 查配额——服务端 429/admission 422 双闸仍在，窗口
  只是少撞闸的礼貌）；
- **digest 聚合**：逐点 GET /api/jobs/{id}/digest（小 JSON）→ 本地
  ``build_aggregate``（自 fdtd/serve/aggregate.py 移植，列名/NaN 语义
  零变化：失败/取消点 = NaN 行）；落盘 ``aggregate.npz`` 同款形状，
  下游 plotting 零改动；
- ``on_failure``：``continue``（缺省，失败点 NaN 继续）/ ``abort``
  （首失败即取消在途、余点标 canceled）。

铁律沿用：零计算零物理——SDK 只做线格式搬运与编排，仿真永远是内核的事。
"""
from __future__ import annotations

import json
import secrets
import time as _time
from pathlib import Path
from typing import Any, Callable

from .job_state import TERMINAL_BAD, TERMINAL_OK
from ..models.errors import GenieHTTPError, GenieJobError

from ..models import SweepSpec
from ..models.sweep_expand import validate_sweep_config

# 父终态口径（与旧服务端父状态机同名，下游消费零迁移）
SWEEP_TERMINAL_OK = "done"
SWEEP_TERMINAL_BAD = ("failed", "canceled")

_AXIS_KEYS = ("values", "linspace", "logspace")

# ---- 聚合纯函数（自 fdtd/serve/aggregate.py 移植，语义零变化）----

AGGREGATE_NAME = "aggregate.npz"


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


def _read_param(point: dict, name: str) -> float:
    """点配置里取参数值（点配置已解析——parameters[name] 是具体数字）。"""
    return point.get("parameters", {}).get(name, float("nan"))


def flatten_numeric(obj, prefix: str = "") -> tuple[dict, list]:
    """递归展平：数值标量叶 → {键: float}；非数值叶 → 跳过键清单。

    dict 按键名、list 按下标续接键名（``monitors.0.comps.Ex.abs_max``）；
    bool 是 int 子类但语义非数值——按非数值跳过。NaN/Inf 数值叶原样
    保留（聚合语义里 NaN = 缺失，与缺键同列处理）。
    """
    out: dict[str, float] = {}
    skipped: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            sub, sub_skip = flatten_numeric(v, key)
            out.update(sub)
            skipped.extend(sub_skip)
    elif isinstance(obj, list):
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
    meta = {"ok": True, "file": AGGREGATE_NAME,
            "n_result_columns": len(keys), "axes": axes_map}
    return arrays, meta


def build_sweep_spec(sweep: dict | None = None,
                     parameters: list | None = None, *,
                     gpus_per_sim: int = 1, max_parallel: int = 1,
                     on_failure: str = "continue",
                     mode: str | None = None) -> dict:
    """sweep dict / parameters 便捷形态（二选一）→ SweepSpec JSON。

    parameters = list of axis dict（每项 ``{"name": 参数名, "values": …}``
    / ``{"name": …, "linspace": [a, b, n]}`` / ``logspace`` 同理），与
    手册 08-sweep 页的 SweepAxisSpec（name 轴形态）逐字段同形——**不发明新面**，
    纯拼装。``name`` 必须是基底配置 ``parameters`` 键集内的参数名
    （本地展开期裁决，SweepExpandError）；``{"path": …}`` 旧形态已退役。
    """
    if sweep is not None and parameters is not None:
        raise ValueError("sweep 与 parameters 二选一（同时给了两者）")
    if sweep is not None:
        if not isinstance(sweep, dict):
            raise TypeError(f"sweep 须 dict，得到 {type(sweep).__name__}")
        # G137（R18）：四个 kwargs 此前被 `dict(sweep)` 静默丢弃（只并
        # mode），docstring 却承诺"同 SweepSpec 字段"——全并入（显式
        # kwargs 覆盖 sweep 字典同名键，与 parameters 分支同形）。
        spec = dict(sweep)
        spec["gpus_per_sim"] = gpus_per_sim
        spec["max_parallel"] = max_parallel
        spec["on_failure"] = on_failure
        if mode is not None:
            spec["mode"] = mode
        return spec
    if parameters is None:
        raise ValueError("sweep 与 parameters 必给其一")
    axes = []
    for a in parameters:
        if not isinstance(a, dict) or "name" not in a:
            raise ValueError(
                f"轴须 dict 且带 name（参数名轴形态）：{a!r}")
        axis = {"name": a["name"]}
        given = [k for k in _AXIS_KEYS if k in a]
        if len(given) != 1:
            raise ValueError(
                f"轴 {a['name']!r} 取值三选一（{_AXIS_KEYS}），给了 {given}")
        axis[given[0]] = a[given[0]]
        axes.append(axis)
    spec: dict[str, Any] = {"parameters": axes,
                            "gpus_per_sim": gpus_per_sim,
                            "max_parallel": max_parallel,
                            "on_failure": on_failure}
    if mode is not None:
        spec["mode"] = mode
    return spec


class SweepHandle:
    """客户端 sweep 编排：展开 → 滑动窗口提交 → 轮询 → digest 聚合。

    生命周期（句柄侧状态机，与旧服务端父状态同名）::

        pending → running → done | canceled（abort/取消）/ failed（全灭）
    """

    # 子作业终态词表 = 正典 core.job_state（G91：旧手抄漏 archived，整批
    # 归档时 sweep 终态误报 done）。
    _GOOD = TERMINAL_OK
    _BAD = TERMINAL_BAD
    _INFLIGHT = ("queued", "running")

    def __init__(self, client: Any, config: dict, spec: dict):
        # （K6/R18：原 self._base = config 死字段已删——展开期一次性
        # 消费，句柄全程无人再读。）
        self._client = client
        self._spec = spec
        self.id = "sw-" + secrets.token_urlsafe(6)
        # 本地展开（校验 + 解析在此刻完成——对应旧 POST /api/sweeps 422 面）
        spec_model = SweepSpec.model_validate(spec)
        self.points: list[dict] = validate_sweep_config(spec_model, config)
        self.axis_names: list[str] = [a["name"]
                                      for a in spec.get("parameters") or []]
        self.on_failure = str(spec.get("on_failure") or "continue")
        self._max_parallel = max(1, int(spec.get("max_parallel") or 1))
        self.job_ids: list[str | None] = [None] * len(self.points)
        self.states: list[str] = ["pending"] * len(self.points)
        self.state = "pending"
        self._canceled = False

    # ---- 编排 ----

    def _window(self) -> int:
        """窗口 = min(max_parallel, 配额并发上限)；配额查不到按 2。"""
        cap = 2
        try:
            q = self._client.whoami().get("quota") or {}
            cap = max(1, int(q.get("max_concurrent_runs") or 2))
        except Exception:  # noqa: BLE001 —— 查不到配额按保守窗
            pass
        return max(1, min(self._max_parallel, cap))

    def _inflight(self) -> list[int]:
        return [i for i, s in enumerate(self.states) if s in self._INFLIGHT]

    def rollup(self) -> dict:
        c: dict[str, int] = {}
        for s in self.states:
            key = ("running" if s in self._INFLIGHT
                   else "pending" if s == "pending" else s)
            c[key] = c.get(key, 0) + 1
        return c

    def _refresh(self) -> None:
        for i, s in enumerate(self.states):
            if s in self._INFLIGHT and self.job_ids[i]:
                st = self._client.job_status(self.job_ids[i])
                self.states[i] = st.get("state") or s

    def pump(self) -> None:
        """推进一步：补窗提交 + 刷新在途 + abort/取消裁决 + 终态判定。"""
        if self.state == SWEEP_TERMINAL_OK or self.state in SWEEP_TERMINAL_BAD:
            return
        self.state = "running"
        if self._canceled:
            for i in self._inflight():
                if self.job_ids[i]:
                    try:
                        self._client.cancel(self.job_ids[i])
                    except GenieHTTPError:
                        pass
                self.states[i] = "canceled"
            for i, s in enumerate(self.states):
                if s == "pending":
                    self.states[i] = "canceled"
            self.state = "canceled"
            return
        self._refresh()
        # abort：首失败即取消在途、余点 canceled。G135（R18）：判据从
        # `_BAD[0]`（只认 failed）改为 any-in 词表——killed/archived
        # 同样是坏终态，abort 模式下首个坏终态就该停。
        if self.on_failure == "abort" and self._GOOD not in self.states \
                and any(s in self._BAD for s in self.states):
            self._canceled = True
            self.pump()
            return
        # 补窗（滑动窗口：跑一补一）
        win = self._window()
        while len(self._inflight()) < win:
            try:
                i = self.states.index("pending")
            except ValueError:
                break
            self.job_ids[i] = self._client.submit(self.points[i])
            self.states[i] = "queued"
        # 终态判定
        if not self._inflight() and "pending" not in self.states:
            self.state = ("failed" if all(
                s in self._BAD + ("canceled",) for s in self.states)
                else SWEEP_TERMINAL_OK)

    def wait(self, timeout: float | None = None, poll: float = 2.0,
             progress: bool | Callable[[dict], None] | None = None
             ) -> dict:
        """阻塞编排直至终态（每 poll 秒 pump 一步）。返回终态 status。

        ``progress``：每步回调 ``progress(status)``；True = 打印一行
        rollup 进度。
        """
        if poll < 0:
            raise ValueError("poll 须 ≥ 0")
        on_progress = progress if callable(progress) else (
            (lambda st: print(f"[sweep {self.id}] "
                              + " ".join(f"{k}={v}" for k, v in
                                         sorted(st["rollup"].items()))))
            if progress is True else None)
        t0 = _time.monotonic()
        while True:
            self.pump()
            st = self.status()
            if on_progress is not None:
                on_progress(st)
            if st["state"] == SWEEP_TERMINAL_OK:
                return st
            if st["state"] in SWEEP_TERMINAL_BAD:
                raise GenieJobError(
                    self.id, st["state"],
                    "rollup: " + json.dumps(st["rollup"],
                                            ensure_ascii=False))
            if timeout is not None and _time.monotonic() - t0 > timeout:
                raise GenieHTTPError(
                    f"本地等待超时（>{timeout}s）：sweep {self.id} 当前 "
                    f"{st['state']}——可无参重入 wait() 续编排", 408)
            _time.sleep(poll)

    def status(self) -> dict:
        return {"sweep_id": self.id, "state": self.state,
                "rollup": self.rollup(),
                "points": [{"idx": i, "state": s, "job_id": j}
                           for i, (s, j) in enumerate(zip(self.states,
                                                          self.job_ids))]}

    def cancel(self) -> dict:
        """取消：在途子作业走 cancel，余点标 canceled。"""
        self._canceled = True
        self.pump()
        return {"sweep_id": self.id, "state": self.state}

    # ---- 聚合与落盘 ----

    def digests(self) -> list[dict | None]:
        """逐点 digest（成功点拉取；失败/取消/未跑 = None → NaN 行）。"""
        out: list[dict | None] = []
        for i, s in enumerate(self.states):
            if s == self._GOOD and self.job_ids[i]:
                try:
                    out.append(self._client.digest(self.job_ids[i]))
                except GenieHTTPError:
                    out.append(None)
            else:
                out.append(None)
        return out

    def aggregate(self) -> dict:
        """聚合列 dict（param_* 轴列 + pt_* digest 数值列 + NaN 语义）。"""
        arrays, _meta = build_aggregate(self.points, self.axis_names,
                                        self.digests())
        return arrays

    def download(self, dir: str | Path) -> Path:
        """manifest + aggregate.npz 落地本地目录（形状同旧服务端产物，
        下游零迁移）::

            dir/manifest.json
            dir/aggregate.npz

        逐点完整结果按需取（子作业是普通作业——``genie.connect()`` 后
        attach 即用既有 results 面）。
        """
        import numpy as np
        root = Path(dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps(self.status(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        arrays, _meta = build_aggregate(self.points, self.axis_names,
                                        self.digests())
        np.savez_compressed(root / AGGREGATE_NAME, **arrays)
        return root

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"<SweepHandle {self.id} {self.state}>"
