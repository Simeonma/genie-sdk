"""genie.session — host 侧拉起并持有 worker：spawn / pipe / ops / ring。

契约（职责 / 禁令 / 接缝）→ ``MODMAP.md``；裁决 → ``docs/DECISIONS.md``
（Q2.1 / Q2.2-3 终局 / Q3.3 / Q3.4 / Q5.3 / Q6.2 / Q6.3）。首版单文件，
内部按四节分；真长开了再拆（core/archive 同款注记）。

- **spawn**：``python -m genie.worker``（cwd = 工作区），等 ``ready``；
  spawn env 三名（对接表 = 本图 §1；worker/main 读侧同名常量锁相等）。
- **pipe**：一行一 JSON（UTF-8）双向；读线程收 ev 行入环，坏行记
  ``out(kind=error)`` 一行不断管、继续 readline。
- **ops**：exec 下行唯一通道 + 忙即拒（唯一放行例外 = ``f.cancel``
  拦截，忙闲皆灵、不进 exec）；id 由本包发号、worker 原样带回。
- **ring**：ev 环 + ``since`` 游标 + epoch——host 进程内存、不落盘；
  有上限，超了丢旧行（Q6.3 代价）。

拨网关一律回调 host（``on_cancel``；本包不碰网）；``net`` 仅 import
``net/states`` 词表（在飞清单终态判定，2026-11-04 拍的口子）。
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from genie import ipc
from genie.net.states import TERMINAL_BAD, TERMINAL_OK

__all__ = ["Session",
           "ENV_GW_BASE_URL", "ENV_WORKER_TOKEN", "ENV_DOC_PATH",
           "CANCEL_COMMAND", "ROW_BUSY",
           "ROW_CANCELED_ONE", "ROW_CANCELED_MANY",
           "ROW_RESTART_ONE", "ROW_RESTART_MANY"]

#: spawn env 名（写侧；worker/main 读侧同名常量，测试锁相等——图 §1
#: 对接表；env 不是管子词，不进 ipc）
ENV_GW_BASE_URL = "GENIE_GW_BASE_URL"      # 值含 /gw 前缀（Client 拼接契约）
ENV_WORKER_TOKEN = "GENIE_WORKER_TOKEN"
ENV_DOC_PATH = "GENIE_DOC_PATH"            # 文档路径初值（host 定；Q4.3）

#: ``f.cancel`` 拦截命令（Q2.2-3：无参 = 本文档在飞整批；不进 exec、
#: 不加管子词）
CANCEL_COMMAND = "f.cancel"

# 回话字面（英文，与 worker 终端行同语言；测试 golden 锁）
ROW_BUSY = "busy: run in progress"                 # 忙即拒回话（Q3.4）
ROW_CANCELED_ONE = "canceled job {id}"             # f.cancel 回话（2026-11-04 拍）
ROW_CANCELED_MANY = "canceled {n} jobs"
ROW_RESTART_ONE = "worker restarted; canceled job {id}"    # kill/EOF 回话
ROW_RESTART_MANY = "worker restarted; canceled {n} jobs"

_RING_MAX = 1000            # 环上限，超了丢旧行（Q6.3 代价，不落盘）
_READY_DEADLINE_S = 10.0    # spawn 等 ready 的死线（spawn 判据，非作业超时）


class Session:
    """host 进程内的 worker 会话。用法::

        s = Session(workspace, gw_base_url="http://127.0.0.1:PORT/gw",
                    worker_token=..., doc_path=..., on_cancel=host_dial)
        s.start()                      # spawn + 等 ready
        s.submit_exec("f.mesh.dx = 20e-9")
        epoch, since, evs = s.events()  # 300ms 单拍的消费口（Q3.3）
        s.close()

    ``on_cancel(job_ids)`` = host 拨 ``/gw`` 的回调（f.cancel 与 kill/EOF
    兜底都走它；本包零网）。``worker_cmd`` 是测试缝（假 worker；生产形态
    只有 ``python -m genie.worker`` 一种）。
    """

    def __init__(self, workspace, *, gw_base_url, worker_token,
                 doc_path=None, on_cancel=None, worker_cmd=None):
        self.workspace = str(workspace)
        self.gw_base_url = gw_base_url
        self.worker_token = worker_token
        # 文档路径初值由 host 定（Q4.3：有路径写原路径）；缺省 = 工作区内
        # genie.gensim。持有与写盘在 worker（改动通道 = f._save 跟随）。
        self.doc_path = (str(doc_path) if doc_path is not None
                         else os.path.join(self.workspace, "genie.gensim"))
        self.on_cancel = on_cancel
        self.worker_cmd = (list(worker_cmd) if worker_cmd is not None
                           else [sys.executable, "-m", "genie.worker"])
        self.epoch = uuid.uuid4().hex       # host 每次启动换（Q6.3；worker 重生不动它）

        self._ring = collections.deque(maxlen=_RING_MAX)
        self._seq = 0                       # 全局序号（since 游标）
        self._inflight = {}                 # job_id -> 最新 state（非终态即在飞）
        self._busy = False                  # exec 发出到 done 之间（Q3.4）
        self._next_exec_id = 1              # exec id 发号（ipc §4）
        self._proc = None
        self._threads = []
        self._generation = 0                # 每次 spawn 成功 +1（重启等它）
        self._closed = False
        self._lock = threading.Lock()

    # ── ring（ev 环 + since 游标 + epoch；host 进程内存、不落盘）────

    def _ring_append(self, ev: dict) -> None:
        with self._lock:
            self._ring.append((self._seq, ev))
            self._seq += 1

    def events(self, since: int = 0):
        """环投影（Q3.3：300ms 单拍、断线从游标续）。``since`` = 已消费
        条数（游标），返回 ``(epoch, next_since, 未消费的 ev 清单)``；
        ``since`` 早于现存最旧条目（旧行已丢）时给整窗，消费方按需整份
        重取；epoch 换代由消费方比对。"""
        with self._lock:
            items = [(s, e) for s, e in self._ring if s >= since]
            nxt = items[-1][0] + 1 if items else since
            return self.epoch, nxt, [e for _, e in items]

    # ── ops（exec 下行 + 忙即拒 + f.cancel 拦截）────────────────────

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def inflight(self) -> tuple:
        """在飞清单（rollup `data.jobs` 累积；非终态 id 即在飞）。"""
        with self._lock:
            return tuple(sorted(self._inflight))

    def submit_exec(self, code, *, fresh=False, echo=True):
        """exec 下行唯一通道（REPL 行 / fresh 整份；表单 delta =
        ``echo=False``）。返回 exec id；忙时新写立即回话一行
        （``ROW_BUSY``）、不排队，返回 None。唯一放行例外 = ``f.cancel``
        拦截（忙闲皆灵、不进 exec）。读类不经此（自然不拒）。"""
        if str(code).strip() == CANCEL_COMMAND:
            self.cancel_inflight()
            return None
        with self._lock:
            if self._busy:
                reject = True
            else:
                reject = False
                self._busy = True
                exec_id = self._next_exec_id
                self._next_exec_id += 1
        if reject:
            self._ring_append(ipc.out(ROW_BUSY))    # 回话即终答（Q3.4）
            return None
        self._write(ipc.exec(exec_id, code, fresh=fresh, echo=echo))
        return exec_id

    def cancel_inflight(self):
        """``f.cancel`` 拦截入口（卡片停止键 = 同一扇门）：取消本文档
        在飞整批 → 回话一行。经 ``on_cancel`` 回调 host 直拨 ``/gw``；
        刚取消的 id 立即出清单（防重复拨号 / 计数虚高）。返回取消的
        job id 清单；回调失败记 error 行、清单不动。"""
        with self._lock:
            ids = sorted(self._inflight)
        if not self._dial(ids):
            return []
        with self._lock:
            for jid in ids:
                self._inflight.pop(jid, None)
        self._ring_append(ipc.out(self._cancel_row(ids)))
        return ids

    def _write(self, op: dict) -> None:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise RuntimeError("worker not started")
            self._proc.stdin.write(json.dumps(op, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()             # 不并发写：同锁串行

    # ── pipe（读线程：收 ev 入环 / 坏行不断管 / EOF 兜底）──────────

    def _reader_loop(self, proc, ready: threading.Event) -> None:
        established = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if not isinstance(ev, dict):
                        raise ValueError("not an object")
                except ValueError:
                    # 坏行不断管：记 error 行，继续 readline（图 §1 pipe）
                    self._ring_append(ipc.out(f"bad pipe line: {line!r}",
                                              kind=ipc.OUT_KIND_ERROR))
                    continue
                self._ring_append(ev)            # 收行入环，不解释内容（禁令 5）
                if ev.get("ev") == ipc.EV_READY:
                    ready.set()
                    established = True
                elif ev.get("ev") == ipc.EV_DONE:
                    with self._lock:
                        self._busy = False        # busy 解除（Q3.4）
                elif (ev.get("ev") == ipc.EV_OUT
                        and ev.get("kind") == ipc.OUT_KIND_PROGRESS):
                    self._update_inflight(ev.get("data") or {})
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if established and not self._closed:
            self._worker_died()                  # EOF 统一兜底（kill / 自死同款）

    def _update_inflight(self, data: dict) -> None:
        """在飞清单口粮 = rollup ``data.jobs``：逐 id 记最新 state；
        终态（net/states 词表）即出清单。"""
        jobs = data.get("jobs") or {}
        with self._lock:
            for jid, info in jobs.items():
                state = (info or {}).get("state")
                if state in TERMINAL_OK or state in TERMINAL_BAD:
                    self._inflight.pop(jid, None)
                else:
                    self._inflight[jid] = state

    def _dial(self, ids) -> bool:
        """取消回调（host 拨 /gw）；失败记 error 行、返回 False。"""
        if not ids:
            return True
        if self.on_cancel is None:
            return True
        try:
            self.on_cancel(list(ids))
            return True
        except Exception as e:                   # noqa: BLE001 —— 回话进环，不拦兜底
            self._ring_append(ipc.out(f"cancel failed: {e}",
                                      kind=ipc.OUT_KIND_ERROR))
            return False

    @staticmethod
    def _cancel_row(ids, *, restart=False) -> str:
        one, many = ((ROW_RESTART_ONE, ROW_RESTART_MANY) if restart
                     else (ROW_CANCELED_ONE, ROW_CANCELED_MANY))
        return one.format(id=ids[0]) if len(ids) == 1 else many.format(n=len(ids))

    def _worker_died(self) -> None:
        """EOF 统一兜底五连（2026-11-04 扫描后拍）：取消在飞（同一回调）
        → 清空清单（重生 worker 不认识旧条目）→ busy 复位 → 回话一行
        → 重生（启动恢复 ⚑P6 在 worker）。"""
        with self._lock:
            ids = sorted(self._inflight)
            self._inflight.clear()
            self._busy = False
        self._dial(ids)                          # 兜底照拨；失败仅记 error 行（清空无条件）
        self._ring_append(ipc.out(self._cancel_row(ids, restart=True)))
        try:
            self._spawn_worker()
        except Exception as e:                   # noqa: BLE001 —— 重生失败报一行，不循环重生
            self._ring_append(ipc.out(f"worker restart failed: {e}",
                                      kind=ipc.OUT_KIND_ERROR))

    # ── spawn（起 worker + 等 ready；重生同路）──────────────────────

    def start(self) -> None:
        """spawn worker 并等 ``ready``（spawn 完成判据）。"""
        with self._lock:
            if self._proc is not None:
                raise RuntimeError("session already started")
        self._spawn_worker()

    def restart_worker(self) -> None:
        """host 会话面「重启 worker」入口（GUI 按钮）：kill → EOF 统一
        兜底（取消在飞 + 清空 + busy 复位 + 回话 + 重生），等重生完成。"""
        with self._lock:
            gen = self._generation
            proc = self._proc
        if proc is None:
            raise RuntimeError("worker not started")
        proc.terminate()
        deadline = time.monotonic() + _READY_DEADLINE_S
        while time.monotonic() < deadline:
            with self._lock:
                if self._closed:
                    raise RuntimeError("session closed")
                if self._generation > gen:
                    return
            time.sleep(0.01)
        raise RuntimeError("worker restart timed out")

    def _spawn_worker(self) -> None:
        env = dict(os.environ)
        env.update({
            ENV_GW_BASE_URL: self.gw_base_url,
            ENV_WORKER_TOKEN: self.worker_token,
            ENV_DOC_PATH: self.doc_path,
            "PYTHONUTF8": "1",                   # 管子契约 = UTF-8（图 §1 pipe）
        })
        proc = subprocess.Popen(
            self.worker_cmd, cwd=self.workspace,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=env)
        ready = threading.Event()
        t = threading.Thread(target=self._reader_loop, args=(proc, ready),
                             daemon=True)
        t.start()
        deadline = time.monotonic() + _READY_DEADLINE_S
        while not ready.is_set():
            if proc.poll() is not None:          # ready 前死：读线程不兜底（未立）
                t.join(timeout=2)
                raise RuntimeError(
                    f"worker died before ready (exit {proc.returncode})")
            if time.monotonic() > deadline:      # 死线到：杀沉默进程不留孤儿
                proc.terminate()
                t.join(timeout=2)
                raise RuntimeError("worker did not signal ready")
            time.sleep(0.01)
        with self._lock:
            self._proc = proc
            self._threads.append(t)
            self._generation += 1

    def close(self) -> None:
        """会话关闭（host 退出）：杀进程、收线程；不触发兜底（孤儿作业
        不追杀、不提醒——2026-11-04 拍）。"""
        self._closed = True
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=3)
