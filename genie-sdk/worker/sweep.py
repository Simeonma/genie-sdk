"""genie.worker.sweep — the parameter-sweep driver: the three verbs
(``f.add_param_sweep`` / ``f.run_param_sweep`` / ``f.delete_param_sweep``)
plus the run loop. A sweep point is **a job** (the same word, the same
machine as ``f.submit``): the per-point record ``{parameters, cfg, doc,
job_id}`` is an index line's in-memory mirror — no brand name, no second
execution semantics.

The loop is the submit chain, batched (Q10):

1. expand (core/orchestration.expand — per-point errors carry the point
   number) → resolved per-point configs;
2. ``parameter_sweep_<name>/`` — one folder per name (coexisting names);
   re-running a name clears and re-stages it (one current batch per name,
   Q4.2-style);
3. one batch ``chain.submit`` (single POST, the wire format is already
   batch-shaped);
4. stage each point (the same staging machine, doc = the point document:
   allocator=0, inflight=[that job], no recipe member — the authoritative
   recipe copy stays in the main document);
5. collect **as each job finishes** (``chain.as_completed`` — completion
   order): inject the result into the point document → run the same post
   tail hook against it (silent failures, recorded as ``post_errors`` in
   the index line) → rewrite the index atomically (one write per job);
6. return the ``WorkerSweep`` handle.

One exec, blocks until the whole batch is terminal (busy-rejects
everything else; ``f.cancel`` stays the only out-of-band door — canceled
jobs ride the bad-point path). Zero terminal lines: progress rides the
wait loop's rollup (one card for the whole batch); ``on_failure``
``abort`` cancels the still-live jobs at the first bad point and raises
``GenieJobError`` (the same exception / wording as ``f.submit``); the
account stays on disk.

Definitions are data, not code — ``add`` takes the whole definition in
one call (no add/update split: that split exists for post because code
arrives by file import); same name overwrites; the document is the
authoritative copy (``sweeps.json`` member).
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import numpy as np

from genie.core import archive
from genie.net import TERMINAL_OK, GenieJobError
from genie.core.orchestration import expand
from genie.core.orchestration.sweep import (INDEX_VERSION, Sweep,
                                            doc_name, write_index)

from . import chain, post

__all__ = ["add_verb", "run_verb", "delete_verb", "WorkerSweep"]

#: A definition name becomes a path component — filesystem-safe
#: characters only (letters / digits / underscore / hyphen).
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_ON_FAILURE = ("continue", "abort")


def sweep_dir(env, name) -> Path:
    """``<main doc dir>/parameter_sweep_<name>/``."""
    return Path(env.doc_path).parent / f"parameter_sweep_{name}"


def sync_sweeps(env) -> None:
    """Registry → document (immediate atomic write when the document
    exists; no document = deferred to staging / `_save`)."""
    doc = env.doc_path
    if doc and Path(doc).is_file():
        archive.write_sweeps(doc, env.sweeps)


def _axis_values(seq) -> list:
    """One axis's sequence → plain list (list / tuple / ndarray pass;
    ndarray flattened by ``tolist`` — vector parameter values stay
    nested lists)."""
    if isinstance(seq, np.ndarray):
        seq = seq.tolist()
    if not isinstance(seq, (list, tuple)):
        raise TypeError(
            "param_sweep axis values must be a sequence (list / tuple / "
            f"ndarray), got {type(seq).__name__}")
    if not seq:
        raise ValueError(
            "param_sweep axis values must be non-empty (a single-element "
            "[x] is valid)")
    return list(seq)


def add_verb(env, name, parameters, *, mode="product", n_gpu=None,
             on_failure="continue"):
    """Define a named sweep: ``{name: {parameters, mode, n_gpu,
    on_failure}}``; same name overwrites (a definition is one line of
    data — redefining is editing). The name must be filesystem-safe (it
    becomes a folder name)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise RuntimeError(
            f"param_sweep name {name!r} is invalid — letters / digits / "
            f"'_' / '-' only (it becomes a folder name)")
    if not isinstance(parameters, dict) or not parameters:
        raise TypeError(
            "add_param_sweep(name, parameters): parameters must be a "
            "non-empty {name: sequence} dict")
    if mode not in ("product", "zip"):
        raise ValueError(f"mode must be 'product' or 'zip', got {mode!r}")
    if on_failure not in _ON_FAILURE:
        raise ValueError(
            f"on_failure must be one of {_ON_FAILURE}, got "
            f"{on_failure!r}")
    defs = {k: _axis_values(v) for k, v in parameters.items()}
    expand.SweepSpec(                      # early axis / zip / 512 rules
        parameters=[expand.SweepAxisSpec(name=k, values=v)
                    for k, v in defs.items()], mode=mode)
    env.sweeps[name] = {"parameters": defs,
                        "mode": mode, "n_gpu": n_gpu,
                        "on_failure": on_failure}
    sync_sweeps(env)
    return None


def delete_verb(env, name):
    """Delete a definition (document synced; the folder on disk is
    untouched — the data is yours, delete it yourself). Idempotent:
    unknown names are silent."""
    env.sweeps.pop(name, None)
    sync_sweeps(env)
    return None


def _run_point_post(env, doc, jid) -> dict:
    """Per-point post tail: the same machinery, silent failures (zero
    terminal lines — the index line records them under ``post_errors``);
    no collective failure."""
    errors = {}
    for name, entry in env.post_recipes.items():
        if not (entry["code"] and entry["script"]):
            continue                      # placeholder / incomplete: skip
        try:
            post.post_one(env, str(doc), name, entry, jid)
        except Exception as e:            # noqa: BLE001 — recorded, no collective failure
            errors[name] = f"{type(e).__name__}: {e}"
    return errors


def run_verb(env, model, name) -> "WorkerSweep":
    """Run one named sweep; blocks until the whole batch is terminal.
    Returns the ``WorkerSweep`` handle (``abort`` raises
    ``GenieJobError``; the account stays on disk)."""
    d = env.sweeps.get(name)
    if d is None:
        known = f" (known: {sorted(env.sweeps)})" if env.sweeps else ""
        raise RuntimeError(
            f"no param_sweep named {name!r} — add_param_sweep first"
            f"{known}")
    base = model.to_config()
    spec = expand.SweepSpec(              # axis/zip/512 rules raise here
        parameters=[expand.SweepAxisSpec(name=k, values=v)
                    for k, v in d["parameters"].items()],
        mode=d["mode"])
    cfgs = expand.validate_sweep_config(spec, base)
    n = len(cfgs)

    folder = sweep_dir(env, name)
    if folder.exists():
        shutil.rmtree(folder)             # re-run = clear and re-stage
    folder.mkdir(parents=True)

    jids = chain.submit(env, model, cfgs, n_gpu=d["n_gpu"])
    jid_to_i = {jid: i + 1 for i, jid in enumerate(jids)}
    rows: list = [None] * n
    index = {"version": INDEX_VERSION,
             "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "sweep": name, "mode": d["mode"],
             "axes": [[k, len(v)] for k, v in d["parameters"].items()],
             "jobs": []}
    for i, (cfg, jid) in enumerate(zip(cfgs, jids), start=1):
        chain.stage(env, model, [jid], doc=str(folder / doc_name(i)),
                    cfg=cfg)
    write_index(folder, index)

    abort_error = None
    done: set = set()
    for out in chain.as_completed(env, jids):
        jid = out["job_id"]
        i = jid_to_i[jid]
        done.add(jid)
        row = {"i": i, "job_id": jid, "doc": doc_name(i),
               "state": out["state"], "ok": out["state"] in TERMINAL_OK,
               "cfg": cfgs[i - 1]}
        if out.get("error"):
            row["error"] = out["error"]
        if row["ok"]:
            doc = folder / doc_name(i)
            archive.inject_result(
                doc, jid, env.client().stream_result(jid),
                chain.state_for(cfgs[i - 1], 0, []))
            errors = _run_point_post(env, doc, jid)
            if errors:
                row["post_errors"] = errors
        elif d["on_failure"] == "abort" and abort_error is None:
            # first bad point: cut the jobs that are still alive (this is
            # what as_completed makes possible), then keep collecting
            abort_error = GenieJobError(jid, out["state"],
                                        error=out.get("error"))
            chain.cancel(env, [j for j in jids if j not in done])
        rows[i - 1] = row
        index["jobs"] = [r for r in rows if r is not None]
        write_index(folder, index)        # one atomic write per job
    if abort_error is not None:
        raise abort_error
    return WorkerSweep(env, folder)


class WorkerSweep(Sweep):
    """The handle ``f.run_param_sweep`` returns: the pure read face plus
    ``run_post_analyze(name=None)`` — re-runs the main document's
    **current** complete recipes over every successful point (npz written
    back into the point documents, same-name replacement = re-run) and
    returns the outputs axis-lifted: scalars → axis-shaped arrays,
    ndarrays → axes + original shape, str → object array (missing / failed
    points NaN / None). Recipes are changed on ``f``, never here."""

    def __init__(self, env, dir):
        super().__init__(dir)
        self._env = env

    def run_post_analyze(self, name=None):
        env = self._env
        if name is None:
            complete = {k: v for k, v in env.post_recipes.items()
                        if v["code"] and v["script"]}
            if not complete:
                raise RuntimeError(
                    "no complete post_analyze recipes — add_post_analyze, "
                    "update_post_analyze(code file), run_post_analyze(name, "
                    "script)")
            return {each: self._run_one(each, env.post_recipes[each])
                    for each in complete}
        if name not in env.post_recipes:
            raise RuntimeError(
                f"no post_analyze named {name!r} — add_post_analyze(name) "
                f"first")
        entry = env.post_recipes[name]
        if not (entry["code"] and entry["script"]):
            raise RuntimeError(
                f"post_analyze {name!r} is incomplete — "
                f"update_post_analyze(name, code file) + "
                f"run_post_analyze(name, script) first")
        return self._run_one(name, entry)

    def _run_one(self, name, entry) -> dict:
        outs = {}                         # i -> output dict (successful points)
        failed = False
        for i in sorted(self.jobs):
            row = self.jobs[i]
            if not row.get("ok"):
                continue                  # failed point → NaN slot
            doc = str(self.dir / doc_name(i))
            try:
                out, blob = post.post_out(self._env, doc, name, entry,
                                          row["job_id"])
                archive.inject_post(doc, name, blob)
                outs[i] = out
            except Exception as e:        # noqa: BLE001 — this point fails, others run on
                row.setdefault("post_errors", {})[name] = \
                    f"{type(e).__name__}: {e}"
                failed = True
        if not outs:
            raise RuntimeError(
                f"post_analyze {name!r} produced no output — no "
                f"successful point to run against")
        if failed:                        # re-run failures update the account
            write_index(self.dir, self.index)
        keys = sorted({k for out in outs.values() for k in out})
        return {key: self._lift(key, outs) for key in keys}

    def _lift(self, key, outs) -> np.ndarray:
        """Axis-lift one output key across the successful points."""
        vals = {i: out[key] for i, out in outs.items() if key in out}
        sample = next(iter(vals.values()))
        if isinstance(sample, str):
            arr = np.empty(self.grid, dtype=object)
            arr[:] = None
            for i, v in vals.items():
                arr[self._pos(i)] = v
            return arr
        if isinstance(sample, np.ndarray):
            for i, v in vals.items():     # shapes must agree — refuse to guess
                if np.shape(v) != np.shape(sample):
                    raise RuntimeError(
                        f"post output {key!r} shape {np.shape(v)} at point "
                        f"{i} differs from {np.shape(sample)} — outputs "
                        f"must agree across points")
            dtype = np.result_type(*(v.dtype for v in vals.values()))
            if len(vals) < self.n_points and not (
                    np.issubdtype(dtype, np.floating)
                    or np.issubdtype(dtype, np.complexfloating)):
                dtype = np.float64
            arr = np.full(self.grid + np.shape(sample), np.nan, dtype=dtype)
            for i, v in vals.items():
                arr[self._pos(i)] = v
            return arr
        dtype = np.result_type(*(type(v) for v in vals.values()))
        if len(vals) < self.n_points and not (
                np.issubdtype(dtype, np.floating)
                or np.issubdtype(dtype, np.complexfloating)):
            dtype = np.float64
        arr = np.full(self.grid, np.nan, dtype=dtype)
        for i, v in vals.items():
            arr[self._pos(i)] = v
        return arr
