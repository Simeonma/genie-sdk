"""genie.ipc.vocab — the words and constructors of the host↔worker pipe (their one and only home).

This module is implementation only:

- Zero IO: imports nothing, only builds dicts (directly ``json.dumps``-able);
- No schema / versioning / handshake (both ends are the same package at the same commit);
- Structured kinds are frozen in ``OUT_KINDS`` — adding a word means editing this module.

The sending side (session) builds only ops; the receiving side (worker)
builds only evs; message literals must not appear in code on either end
(any found are folded in here).
"""
from __future__ import annotations

__all__ = [
    # words
    "OP_EXEC", "EV_READY", "EV_OUT", "EV_CFG", "EV_DONE",
    "OUT_KIND_PLOT", "OUT_KIND_ERROR", "OUT_KIND_PROGRESS",
    "OUT_KIND_META", "OUT_KIND_SERIES", "OUT_KIND_MATFIT", "OUT_KIND_MATLIB",
    "OUT_KINDS",
    # constructors (one per message)
    "exec", "ready", "out", "cfg", "done",
]

# ── words (the one and only home)────────────────────────────────────────────────

OP_EXEC = "exec"

EV_READY = "ready"
EV_OUT = "out"
EV_CFG = "cfg"
EV_DONE = "done"

OUT_KIND_PLOT = "plot"       # data = {mon, field, job_id} (a reference, not the bytes)
OUT_KIND_ERROR = "error"     # red error line; the text is the payload
# Rollup of the current batch (emitter = the worker/run wait loop,
# consumer = cards):
# data = {total, done, failed, jobs: {job_id: {state, progress?}}} —
# progress is optional (display only, never decides terminal state);
# a single run is a batch of length 1
OUT_KIND_PROGRESS = "progress"
# Result-view structure list (emitter = f.result_meta; consumer = the GUI's
# result panel / plot builder). Q4.5 public read verbs:
# data = {ok, job_id, executed_steps, dt, grid_shape,
#         monitors: [{name, domain, fields, axes: {axis: length}}]}
OUT_KIND_META = "meta"
# One result-view series (emitter = f.visualize; consumer = the GUI plot
# canvas). Full arrays (no downsampling — station decision 2026-11-06):
# data = {ok, mon, field, dim, x, y[, z], x_label[, y_label], peak, tag}
OUT_KIND_SERIES = "series"
# Material fit preview (emitter = f.fit_preview; consumer = the GUI's
# material-fit dialog). Read-only: it fits a library name and reports the
# curves, it does not touch the model:
# data = {name, band:[lo,hi], n_poles, summary, quality, warnings,
#         wl:[], n:[], k:[],               ← raw data inside the band
#         wl_fit:[], n_fit:[], k_fit:[]}   ← the fit on a dense grid
OUT_KIND_MATFIT = "matfit"
# Material-library name list (emitter = f.material_library; consumer = the
# GUI's material dialog picker). Data = {materials: [name, ...]}.
OUT_KIND_MATLIB = "matlib"
OUT_KINDS = frozenset({OUT_KIND_PLOT, OUT_KIND_ERROR, OUT_KIND_PROGRESS,
                       OUT_KIND_META, OUT_KIND_SERIES, OUT_KIND_MATFIT,
                       OUT_KIND_MATLIB})


# ── op (host → worker; built by session)──────────────────────────────

def exec(id, code, *, fresh=False, echo=True):
    """The one and only write channel. ``fresh=True`` = execute in a shallow
    copy of the old namespace and, only on success, swap the copy in
    wholesale as the current namespace and atomically replace the model (Run
    semantics); ``echo=False`` = silent execution (form delta: stdout does
    not enter the terminal ring). ``id`` is issued by the session and echoed
    back unchanged in ``done``.
    """
    return {"op": OP_EXEC, "id": int(id), "code": str(code),
            "fresh": bool(fresh), "echo": bool(echo)}


# ── ev (worker → host; built by worker)────────────────────────────────

def ready():
    """Worker-ready signal (consumed by the session's spawn wait)."""
    return {"ev": EV_READY}


def out(text, kind=None, data=None):
    """Terminal-ring entry. ``kind`` allows only the frozen ``OUT_KINDS`` set;
    keys that are ``None`` never appear; ``data`` must accompany a structured
    ``kind`` (plot's reference field set).
    """
    if kind is None:
        if data is not None:
            raise ValueError("out: data requires a structured kind")
    elif kind not in OUT_KINDS:
        raise ValueError(f"out: unknown kind {kind!r} "
                         f"(frozen set: {sorted(OUT_KINDS)})")
    m = {"ev": EV_OUT, "text": str(text)}
    if kind is not None:
        m["kind"] = kind
        if data is not None:
            m["data"] = data
    return m


def cfg(snapshot):
    """Model projection (a SimConfig-shaped dict, sent up the pipe unchanged; snapshot fields only, no meta fields)."""
    return {"ev": EV_CFG, "cfg": snapshot}


def done(id, ok, err=None):
    """Exec completion signal (busy released); ``err`` is a short human-readable phrase."""
    m = {"ev": EV_DONE, "id": int(id), "ok": bool(ok)}
    if err is not None:
        m["err"] = str(err)
    return m
