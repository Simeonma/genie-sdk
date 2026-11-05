"""genie.worker.exec — 执行面：命名空间 / f 组合体 / 输出捕获。

- REPL：exec 就地跑在当前命名空间（半途失败**保留前面**，Q1.1.1）；
- fresh（Run）：在**老命名空间浅拷贝**里、对**新空模型**整份重建；成功才
  整份替换命名空间 + 原子换模型；失败全不动，但 fresh 内已发生的提交与
  落位**不回滚**（外部副作用不随命名空间回滚，Q6.1 补）；
- ``f`` 每次 exec 重注入（bind 住 run 的动作面——本模块只造绑定，
  动作实现住 run.py，worker 图 §2）；
- stdout 是 IPC 通道：exec 期间劫持（捕获进 out）；``echo=False`` 只关
  用户 stdout（Q3.6），cfg 照发、stderr 照进环（红行）；
- cfg 发射（⚑P2 拍定 A）：每次 exec 完成后必发一版（不论成败、无条件
  快照——幂等，300ms 拍自然合并）。
"""
from __future__ import annotations

import io
import sys

from genie import ipc
from genie.core.model import Model

from .run import bind


class ExecContext:
    """worker 的执行面状态：活模型、命名空间、动作环境（emit / 文档
    路径 / 网关入参）。run 的动作经 ``client()`` 惰性建 net Client。"""

    def __init__(self, emit, model=None, gw_base_url=None,
                 worker_token=None, doc_path=None):
        self.emit = emit
        self.model = model if model is not None else Model()
        self.gw_base_url = gw_base_url
        self.worker_token = worker_token
        self.doc_path = doc_path
        self.inflight: set = set()          # 本 f 在飞 job（_cancel(None) 用）
        self.post_recipes: dict = {}        # Q11 具名配方 {name: {code, script}}
        self.ns: dict = {}
        self._active_ns: "dict | None" = None   # 正在 exec 的 ns（fresh = 局部拷贝）
        self._reinject_f()

    # -- f / net ------------------------------------------------------

    def _reinject_f(self) -> None:
        self.ns["f"] = bind(self.model, self)

    def current_ns(self) -> dict:
        """当下正在 exec 的命名空间（fresh = 局部拷贝；平时 self.ns）。
        Q11 post 执行面从这里取浅拷贝——配方要解析得到本段 exec 里
        刚定义的函数。"""
        return self._active_ns if self._active_ns is not None else self.ns

    def client(self):
        """net Client（零凭据纪律：base_url / token 显式传入，Q2.1）。"""
        from genie.net import Client
        return Client(self.gw_base_url, worker_token=self.worker_token)

    # -- exec（唯一写通道的执行面）------------------------------------

    def exec_op(self, op: dict) -> None:
        """一条 exec op：跑 → out 行 → cfg 快照 → done。"""
        ok, err = self._run(op["code"], op.get("fresh", False),
                            op.get("echo", True))
        # cfg 无条件快照（⚑P2）：失败也发（当前活模型）
        self.emit(ipc.cfg(self.model.to_config()))
        self.emit(ipc.done(op["id"], ok, err))

    def _run(self, code: str, fresh: bool, echo: bool):
        """一段代码：REPL 就地 / fresh 浅拷贝重建；stdout 劫持；结果
        (ok, err)。"""
        ns = dict(self.ns) if fresh else self.ns
        model = Model() if fresh else self.model
        ns["f"] = bind(model, self)          # f 每次 exec 重注入（Q6.1）

        saved_out, saved_err = sys.stdout, sys.stderr
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        self._active_ns = ns                 # Q11：post 面取正在 exec 的 ns
        try:
            exec(compile(code, "<genie>", "exec"), ns)
        except Exception as e:               # noqa: BLE001 —— 用户代码失败回 done
            # 失败：命名空间与模型都不动（fresh 的拷贝直接丢弃）
            return False, f"{type(e).__name__}: {e}"
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
            self._active_ns = None

        if fresh:                            # 成功才整份替换 + 原子换模型
            self.ns = ns
            self.model = model
        self._reinject_f()

        text = buf_out.getvalue()
        if echo and text:                    # echo=False：stdout 捕获后丢弃
            self.emit(ipc.out(text))
        errtext = buf_err.getvalue()
        if errtext:                          # stderr → 红行（Q3.6 只关 stdout）
            self.emit(ipc.out(errtext, kind=ipc.OUT_KIND_ERROR))
        return True, None
