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
from .run import load_model, load_recipes, load_sweeps

__all__ = ["main", "ENV_GW_BASE_URL", "ENV_WORKER_TOKEN", "ENV_DOC_PATH"]

#: spawn env names (reader side; the session write side has same-named
#: constants; env is not a pipe word and never enters ipc)
ENV_GW_BASE_URL = "GENIE_GW_BASE_URL"
ENV_WORKER_TOKEN = "GENIE_WORKER_TOKEN"
ENV_DOC_PATH = "GENIE_DOC_PATH"


def _restore(doc_path: "str | None") -> Model:
    """Startup restore: if the ``.gensim`` at the spawn env path exists,
    restore the model from it (single implementation = ``run.load_model``,
    the same logic as ``f._open``; the id allocator takes max); otherwise
    a new model. Result members stay in the document (reads go through
    f.get_* / f.results)."""
    if doc_path and os.path.isfile(doc_path):
        return load_model(doc_path)[0]
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
    ctx = ExecContext(
        emit=emit,
        model=_restore(doc_path),
        gw_base_url=env.get(ENV_GW_BASE_URL),
        worker_token=env.get(ENV_WORKER_TOKEN),
        doc_path=doc_path,
    )
    ctx.post_recipes = load_recipes(doc_path)   # recipes are restored with the document
    ctx.sweeps = load_sweeps(doc_path)          # sweep definitions too (the document is the authoritative copy)

    emit(ipc.ready())
    emit(ipc.cfg(ctx.model.to_config()))          # initial emission right after ready

    for line in read_lines:                       # one JSON per line (guaranteed by the session)
        line = line.strip()
        if not line:
            continue
        op = json.loads(line)
        if op.get("op") == ipc.OP_EXEC:
            ctx.exec_op(op)
