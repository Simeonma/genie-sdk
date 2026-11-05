"""genie.worker.main — 进程壳：stdin JSON 行循环 → 分派；ev 发射。

零停止机制（Q2.2-3 改裁）：管子上没有停止消息、进程里没有信号
handler——stop = host 经 ``/gw`` 取消远端作业，等待循环自查见终态自然
返回。``read_lines`` / ``emit`` 可注入——测试缝，仅测试用；生产形态
只有子进程一种（stdin readline / stdout 一行一 JSON，UTF-8）。
"""
from __future__ import annotations

import json
import os
import sys

from genie import ipc
from genie.core.model import Model

from .exec import ExecContext
from .run import load_model, load_recipes

__all__ = ["main", "ENV_GW_BASE_URL", "ENV_WORKER_TOKEN", "ENV_DOC_PATH"]

#: spawn env 名（读侧；session 写侧同名常量，测试锁相等——session 图 §1
#: 对接表；env 不是管子词，不进 ipc）
ENV_GW_BASE_URL = "GENIE_GW_BASE_URL"
ENV_WORKER_TOKEN = "GENIE_WORKER_TOKEN"
ENV_DOC_PATH = "GENIE_DOC_PATH"


def _restore(doc_path: "str | None") -> Model:
    """启动恢复（⚑P6）：spawn env 路径的 ``.gensim`` 有则恢复模型
    （单一实现 = ``run.load_model``，批 9b 薄壳化——与 ``f._open`` 同一
    逻辑；发号器取 max，Q1.2.5），无则新模型。结果成员留在文档里
    （读数走 f.get_* / f.results，批 9a 已落）。"""
    if doc_path and os.path.isfile(doc_path):
        return load_model(doc_path)[0]
    return Model()


def _stdout_emit(msg: dict, _w=None) -> None:
    """ev 发射：一行一 JSON（UTF-8）。``_w`` 绑定进程真 stdout——exec
    劫持 sys.stdout 期间，worker 自己的行照发不误（吞进捕获缓冲 = 灾难）。"""
    w = _w if _w is not None else sys.stdout
    w.write(json.dumps(msg, ensure_ascii=False) + "\n")
    w.flush()


def main(read_lines=None, emit=None, env=None) -> None:
    """行循环：一行一 op（exec 唯一写通道）→ 分派。启动即 ready + cfg
    首发（⚑P2 拍定 A），随后每次 exec 完成必发一版 cfg（exec 面）。"""
    read_lines = read_lines if read_lines is not None else sys.stdin
    real_stdout = sys.stdout                 # 在第一次 exec 劫持之前拿到
    if emit is None:
        def emit(msg, _w=real_stdout):
            _stdout_emit(msg, _w)
    env = os.environ if env is None else env

    doc_path = env.get(ENV_DOC_PATH)
    ctx = ExecContext(
        emit=emit,
        model=_restore(doc_path),
        gw_base_url=env.get(ENV_GW_BASE_URL),
        worker_token=env.get(ENV_WORKER_TOKEN),
        doc_path=doc_path,
    )
    ctx.post_recipes = load_recipes(doc_path)   # Q11 细节 8：配方随档恢复

    emit(ipc.ready())
    emit(ipc.cfg(ctx.model.to_config()))          # ⚑P2：ready 后首发

    for line in read_lines:                       # 一行一 JSON（session 保证）
        line = line.strip()
        if not line:
            continue
        op = json.loads(line)
        if op.get("op") == ipc.OP_EXEC:
            ctx.exec_op(op)
