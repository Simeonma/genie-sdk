"""genie.worker.main — process shell: stdin JSON line loop → dispatch; ev emission.

No stop machinery: there is no stop message on
the pipe and no signal handler in the process — stop = the host cancels
the remote job via ``/gw``, and the waiting loop returns naturally once
its own check sees the terminal state. ``read_lines`` / ``emit`` are
injectable — a test seam, for tests only; production has just the single
subprocess form (stdin readline / stdout one JSON per line, UTF-8).
"""
from __future__ import annotations

import json
import os
import sys

from genie import ipc
from genie.core.model import Model

from .exec import ExecContext

__all__ = ["main", "ENV_GW_BASE_URL", "ENV_WORKER_TOKEN", "ENV_DOC_PATH"]

#: spawn env names (reader side; the session write side has same-named
#: constants; env is not a pipe word and never enters ipc)
ENV_GW_BASE_URL = "GENIE_GW_BASE_URL"
ENV_WORKER_TOKEN = "GENIE_WORKER_TOKEN"
ENV_DOC_PATH = "GENIE_DOC_PATH"


def _restore(doc_path: "str | None") -> Model:
    """Startup = **empty model** (2026-11-25 站长拍)：默认打开不载入任何设定。

    早先这里会自动恢复 spawn env 指向的 ``.gensim``；文档格式断代后，一个旧档
    就能让整个进程起不来（窗口打不开）。现在载入只有一条路：``genie.open(path)``。
    ``doc_path`` 仍保留为默认保存目标（GUI 的 Save 走它）。"""
    return Model()


def _stdout_emit(msg: dict, _w=None) -> None:
    """ev emission: one JSON per line (UTF-8). ``_w`` is bound to the
    process's real stdout — while exec hijacks sys.stdout, the worker's
    own lines still go out (swallowing them into the capture buffer =
    disaster)."""
    w = _w if _w is not None else sys.stdout
    w.write(json.dumps(msg, ensure_ascii=False) + "\n")
    w.flush()


def main(read_lines=None, emit=None, env=None) -> None:
    """Line loop: one op per line (exec is the only write channel) →
    dispatch. On startup, ready + the cfg initial emission; afterwards,
    one cfg version is always emitted after each exec completes
    (exec plane)."""
    read_lines = read_lines if read_lines is not None else sys.stdin
    real_stdout = sys.stdout                 # grab it before the first exec hijack
    if emit is None:
        def emit(msg, _w=real_stdout):
            _stdout_emit(msg, _w)
    env = os.environ if env is None else env

    doc_path = env.get(ENV_DOC_PATH)
    if doc_path and os.path.isfile(doc_path):
        # 不载入它，就也不把它当默认保存目标——绝不覆盖一份没打开过的旧档。
        # 用户显式 open 之后 doc_path 才会重新指过去。
        doc_path = None
    ctx = ExecContext(
        emit=emit,
        model=_restore(doc_path),
        gw_base_url=env.get(ENV_GW_BASE_URL),
        worker_token=env.get(ENV_WORKER_TOKEN),
        doc_path=doc_path,
    )
    # 配方 / 扫参只在 open 时随档恢复；启动不读文件（同一理由：默认不载入）
    ctx.post_recipes = {}
    ctx.sweeps = {}

    emit(ipc.ready())
    emit(ipc.cfg(ctx.gui_snapshot()))          # initial emission right after ready

    for line in read_lines:                       # one JSON per line (guaranteed by the session)
        line = line.strip()
        if not line:
            continue
        op = json.loads(line)
        if op.get("op") == ipc.OP_EXEC:
            ctx.exec_op(op)
