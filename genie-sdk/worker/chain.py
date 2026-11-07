"""genie.worker.chain — the submission chain: the one machine both
``f.submit`` and the sweep driver share (Q10: "a sweep is a batched
submit" is expressed by this shared module — the import graph says it).

Contents (moved verbatim from run.py, zero behavior change):

- batch submission (``submit`` — wire format is always batch-shaped;
  single = batch of 1), validation (the sole validation surface), the
  payload rule (action parameters never written back to the model);
- staging (``stage`` — "submitting invalidates the prior run": whole
  document, no results, inflight records this batch; staging counts as
  one save) and the state builder;
- the wait loop: ``as_completed`` (incremental delivery — poll /gw once
  per tick, deliver each job **the moment it reaches a terminal state**;
  the progress job card is refreshed along the way) and ``wait`` (the
  drain-all shell, completion order, reports without judging — failure
  policy belongs to the driver);
- ``cancel`` (cut in-flight jobs).

Silent throughout: zero terminal lines (the five golden rows live here as
constants but are emitted by run's public verb / post's tail hook).
"""
from __future__ import annotations

import time

from genie import ipc
from genie.core import archive
from genie.core.model.draft import digest
from genie.net import (TERMINAL_BAD, TERMINAL_OK, GenieConfigError)
from genie_protocol.resolve import resolve_parameters, validate_for_ui

__all__ = ["submit", "wait", "as_completed", "cancel", "stage",
           "state_for"]

#: Wait loop tick (seconds) — poll /gw once per second. A fixed constant,
#: not a knob (stays out of the signature); tests patch the module
#: attribute.
_POLL_S = 1.0

#: Document format version (the state.json format field)
_FORMAT = 2

# The five terminal lines (the single copy; the fifth is emitted only when
# post fails, zero lines on success)
ROW_SUBMIT_ONE = "submitted job {id}"
ROW_DONE = "done job {id}: result saved"
ROW_FAILED = "failed job {id}: {detail}"
ROW_CANCELED = "canceled job {id}"
ROW_POST_FAILED = "post failed {name} job {id}: {detail}"


def _normalize(model, cfg_or_cfgs):
    """Argument normalization: None = promote the live model; dict = single;
    list = batch. → (single, [cfg…])."""
    if cfg_or_cfgs is None:
        return True, [model.to_config()]
    if isinstance(cfg_or_cfgs, dict):
        return True, [cfg_or_cfgs]
    return False, list(cfg_or_cfgs)


def _validate(cfgs) -> None:
    """Pre-submit validation (the sole validation surface); failure →
    GenieConfigError (error paths carry per-field details, so users can
    debug without opening a browser)."""
    errs = []
    for cfg in cfgs:
        errs.extend(validate_for_ui(cfg))
    if errs:
        raise GenieConfigError(
            "run.precheck: " + "; ".join(
                f"{e.get('loc') or 'cfg'}: {e.get('msg')}" for e in errs),
            detail=errs)


def _resolve(cfgs):
    """Promotion to what the engine gets: numeric slots holding an
    expression string (``"L*2"``, Q13) are evaluated against the
    parameters table here — one call to ``protocol.resolve``, the only
    implementation. The document keeps the authored form (expressions +
    table); the payload carries the numbers."""
    return [resolve_parameters(cfg) for cfg in cfgs]


def _payload(cfgs, n_gpu):
    """Action parameters go only into the submit payload, never written back
    to the model (the parameter set maps one-to-one onto RunSpec fields,
    currently just n_gpu)."""
    if n_gpu is None:
        return cfgs
    return [{**c, "run": {**(c.get("run") or {}), "n_gpu": n_gpu}}
            for c in cfgs]


def state_for(cfg: dict, allocator, inflight, staged_digest=None) -> dict:
    """The four-column state document (format / allocator / digest /
    inflight)."""
    return {"format": _FORMAT,
            "allocator": allocator,
            "digest": staged_digest or digest(cfg),
            "inflight": list(inflight)}


def stage(env, model, ids, *, doc=None, cfg=None) -> str:
    """Staging: writes the whole document, no results (submitting
    invalidates — old post outputs vanish with it), inflight records this
    batch; digest = the staging fingerprint (staging counts as one save — a
    failed job leaves the model not dirty); the recipe set enters the
    document from current in-memory values.

    ``doc=None`` = the main document (cfg from the live model, allocator =
    the model's allocator). ``doc`` given = a **point document** (cfg
    required, allocator=0 — ``Model.from_config`` resumes from max when
    opened; no recipe member: the authoritative copy stays in the main
    document). Returns the staged cfg."""
    if cfg is None:
        cfg = model.to_config()
    allocator = model.last_id if doc is None else 0
    recipes = env.post_recipes if doc is None else None
    sweeps = env.sweeps if doc is None else None
    archive.save(doc if doc is not None else env.doc_path,
                 simconfig=cfg,
                 state=state_for(cfg, allocator, ids),
                 post_recipes=recipes, sweeps=sweeps)
    return cfg


def submit(env, model, cfg_or_cfgs=None, *, n_gpu=None):
    """Submit and return handles immediately (no staging, no waiting, zero
    terminal text; single = batch of 1, same shape as the public surface).
    Staging is main-document semantics; point documents are the driver's
    own business."""
    single, cfgs = _normalize(model, cfg_or_cfgs)
    _validate(cfgs)
    cfgs = _resolve(cfgs)                                # expression slots → numbers (Q13)
    ids = env.client().submit(_payload(cfgs, n_gpu))  # always batch form
    if not isinstance(ids, list):
        ids = [ids]
    env.inflight.update(ids)
    return ids[0] if single else ids


def _progress_row(by_id: dict) -> dict:
    """Per-tick rollup for the wait loop (ipc kind=progress; display only,
    no terminal-state judgment)."""
    done = sum(1 for s in by_id.values() if s["state"] in TERMINAL_OK)
    failed = sum(1 for s in by_id.values() if s["state"] in TERMINAL_BAD)
    jobs = {}
    for jid, s in by_id.items():
        item = {"state": s["state"]}
        if "progress" in s:
            item["progress"] = s["progress"]
        jobs[jid] = item
    return ipc.out(f"{done + failed}/{len(by_id)}",
                   kind=ipc.OUT_KIND_PROGRESS,
                   data={"total": len(by_id), "done": done,
                         "failed": failed, "jobs": jobs})


def as_completed(env, ids):
    """Incremental wait: poll the batch once per tick, deliver each job
    **the moment it reaches a terminal state** (completion order — this is
    what makes an abort-mode driver able to cancel jobs that are still
    alive). Refreshes the progress job card along the way; zero terminal
    text; reports terminal states without judging. Yields
    ``{job_id, state, error?}``; the batch's in-flight ids are cleared
    once the loop drains."""
    ids = [ids] if isinstance(ids, str) else list(ids)
    seen: dict = {}
    while len(seen) < len(ids):
        by_id = {s["job_id"]: s for s in env.client().job_statuses(ids)}
        env.emit(_progress_row(by_id))
        for jid in ids:
            if jid in seen:
                continue
            st = by_id[jid]["state"]
            if st in TERMINAL_OK or st in TERMINAL_BAD:
                seen[jid] = by_id[jid]
                yield {"job_id": jid, "state": st,
                       **({"error": by_id[jid]["error"]}
                          if by_id[jid].get("error") else {})}
        if len(seen) < len(ids):
            time.sleep(_POLL_S)
    env.inflight.difference_update(ids)


def wait(env, handles) -> "list[dict]":
    """Wait for a batch of jobs to reach a terminal state, collecting them
    in completion order (= draining ``as_completed``); zero terminal text.
    Reports terminal states without judging — failure policy belongs to the
    driver layer. Returns ``[{job_id, state, error?}]`` (completion
    order)."""
    return list(as_completed(env, handles))


def cancel(env, handles=None) -> None:
    """Cancel in-flight jobs (dialed straight through the gateway; zero
    terminal text). ``handles=None`` = every in-flight job of this f (the
    inflight tracked on the exec surface)."""
    ids = ([handles] if isinstance(handles, str) else list(handles)) \
        if handles is not None else list(env.inflight)
    client = env.client()
    for jid in ids:
        client.cancel(jid)
    env.inflight.difference_update(ids)
