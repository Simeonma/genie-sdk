"""genie.worker.post — the post-processing machine: the whitelist gate,
the execution core, the submit-tail auto-run, and the implementations of
the four verbs (``f.add/update/run/delete_post_analyze`` are thin
delegates from run.py).

One machine, three consumers (Q10): the submit tail hook (single run),
the sweep driver (per point), and ``sw.run_post_analyze`` (batch re-run
over a sweep). Moved verbatim from run.py — zero behavior change; the
execution core takes an explicit document path (the main document is the
caller's default), which is the seam the sweep driver uses.
"""
from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

from genie import ipc
from genie.core import archive
from genie.core import results as core_results
from .chain import ROW_POST_FAILED

__all__ = ["add_verb", "update_verb", "run_verb", "delete_verb",
           "run_post", "post_one", "post_out", "resolve_result",
           "sync_recipes", "gate_recipe", "check_post_out", "UNSET"]

#: Sentinel for ``f.run_post_analyze`` (distinguishes "no argument" from an
#: explicit ``script=None`` — an unset slot, not a value).
UNSET = object()

#: Recipe contract whitelist: inside recipe code ``f`` may only read the
#: four result verbs; everything else (submit / the four model
#: collections / document commands / post verbs) is refused outright.
_POST_F_OK = ("results", "get_time", "get_freq", "get_index")


def resolve_result(env) -> str:
    """The current results = the document's sole results member (always a
    single copy, no job_id — multiple members = document anomaly with a
    one-line error). A missing results member is stated in one clear line
    (zero-state guard: no talk of state, no terminal-state words)."""
    doc = env.doc_path
    jobs = archive.result_jobs(doc) if doc and os.path.isfile(doc) else []
    if not jobs:
        raise RuntimeError(
            "no results in this .gensim — never succeeded or "
            "invalidated by a newer batch")
    if len(jobs) > 1:
        raise RuntimeError(
            f"multiple results members ({', '.join(jobs)}) in this "
            f".gensim — v2 holds exactly one (the current run)")
    return jobs[0]


def gate_recipe(code: str, where: str) -> None:
    """Whitelist gate: attribute references on ``f`` allow only the
    ``_POST_F_OK`` read verbs; any other attribute / bare ``f`` →
    RuntimeError with a one-line message (update refuses to install it,
    run refuses to execute it)."""
    tree = ast.parse(code)
    ok_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) \
                and isinstance(node.value, ast.Name) and node.value.id == "f":
            if node.attr not in _POST_F_OK:
                raise RuntimeError(
                    f"{where}: f.{node.attr} is not allowed in a "
                    f"post_analyze recipe — recipes may only read results "
                    f"({', '.join('f.' + a for a in _POST_F_OK)}); keep "
                    f"recipe files pure (read results + post-process)")
            ok_ids.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "f" \
                and isinstance(node.ctx, ast.Load) and id(node) not in ok_ids:
            raise RuntimeError(
                f"{where}: bare 'f' is not allowed in a post_analyze "
                f"recipe — recipes may only read results via "
                f"{', '.join('f.' + a for a in _POST_F_OK)}")


def check_post_out(out: dict) -> None:
    """npz value domain: scalar / ndarray / str accepted;
    nested dict / list and the like rejected — one-line error (key name +
    actual type)."""
    for key, val in out.items():
        if isinstance(val, (str, bool, int, float, complex,
                            np.generic, np.ndarray)):
            continue
        raise RuntimeError(
            f"post_out[{key!r}]: npz values must be scalar / ndarray / "
            f"str, got {type(val).__name__}")


def _require_recipe(env, name) -> None:
    if name not in env.post_recipes:
        raise RuntimeError(
            f"no post_analyze named {name!r} — add_post_analyze(name) "
            f"first")


def sync_recipes(env) -> None:
    """Registry → document (immediate atomic write when the document
    exists; no document = deferred to staging / `_save`)."""
    doc = env.doc_path
    if doc and os.path.isfile(doc):
        archive.write_recipes(doc, env.post_recipes)


def add_verb(env, name):
    """**Create an empty recipe**: adds a new **empty** post_analyze entry
    to the document (no code, no call line — a placeholder; code comes in
    via ``update``, the call line is recorded by ``run``). Same name
    already exists → one-line error."""
    if not name:
        raise TypeError("add_post_analyze(name): name is required")
    if name in env.post_recipes:
        raise RuntimeError(
            f"post_analyze {name!r} already exists — "
            f"update_post_analyze to change its code")
    env.post_recipes[name] = {"code": "", "script": ""}
    sync_recipes(env)
    return None


def update_verb(env, name, script_location):
    """**Import code**: writes the **entire content** of the py file at
    ``script_location`` into that recipe's code slot in the document (the
    authoritative copy — later local edits do nothing without update).
    **The whitelist gate runs here** (``f`` may only read the four result
    verbs, everything else refused). Requires the recipe to already exist
    (add first); persists to the document immediately; does not run."""
    _require_recipe(env, name)
    try:
        code = Path(script_location).read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(
            f"cannot read recipe file {str(script_location)!r}: "
            f"{e}") from e
    gate_recipe(code, f"recipe file {str(script_location)!r}")
    env.post_recipes[name]["code"] = code
    sync_recipes(env)
    return None


def run_verb(env, name=None, script=UNSET):
    """**Call and compute**: ``run_post_analyze(name, script)`` — script =
    **a direct call to the function** (e.g. ``calc_q(a=1e-6)``, an
    expression; the return value is the output dict → ``post/<name>.npz``)
    and it is **recorded into the document** (reused by the next
    ``run(name)`` and by the submit-tail auto-run); ``run(name)`` =
    re-run with the stored call line; ``run()`` = run all **complete**
    recipes → ``{name: out}`` (incomplete ones silently skipped —
    placeholder semantics). Environment: ``d`` = the current results (an
    SDK-injected global, usable directly inside the function body) +
    **the document's code is the authoritative copy** (exec overrides
    same-named in-session (live) functions). Exceptions propagate (the
    exec error line)."""
    if name is None:
        if script is not UNSET:
            raise TypeError("run_post_analyze: script requires a name")
        complete = {k: v for k, v in env.post_recipes.items()
                    if v["code"] and v["script"]}
        if not complete:
            raise RuntimeError(
                "no complete post_analyze recipes — add_post_analyze, "
                "update_post_analyze(code file), run_post_analyze(name, "
                "script)")
        jid = resolve_result(env)
        out = {each: post_one(env, env.doc_path, each, entry, jid)
               for each, entry in complete.items()}
        sync_recipes(env)
        return out
    _require_recipe(env, name)
    entry = env.post_recipes[name]
    if script is not UNSET:
        if not isinstance(script, str):
            raise TypeError("run_post_analyze: script must be str")
        gate_recipe(script, f"recipe script {name!r}")
        entry["script"] = script          # record the call line (recorded
        sync_recipes(env)                 # before running — kept even on failure, for the auto-run / re-runs)
    if not entry["code"]:
        raise RuntimeError(
            f"post_analyze {name!r} has no code — update_post_analyze"
            f"(name, script_location) first")
    if not entry["script"]:
        raise RuntimeError(
            f"post_analyze {name!r} has no call script — "
            f"run_post_analyze(name, script) first")
    jid = resolve_result(env)
    out = post_one(env, env.doc_path, name, entry, jid)
    sync_recipes(env)
    return out


def delete_verb(env, name):
    """**Delete one**: removed from the in-memory registry; synced when
    the document exists — the recipes member rewritten +
    ``post/<name>.npz`` removed (otherwise the `_save` carry-over would
    drag the orphan npz along again). Idempotent: unknown names are
    silent."""
    env.post_recipes.pop(name, None)
    doc = env.doc_path
    if doc and os.path.isfile(doc):
        archive.write_recipes(doc, env.post_recipes)
        archive.remove_post(doc, name)
    return None


def post_one(env, doc_path, name, entry, jid):
    """Run one recipe against one document and persist the npz; returns
    the output dict."""
    out, blob = post_out(env, doc_path, name, entry, jid)
    archive.inject_post(doc_path, name, blob)
    return out


def run_post(env, jid, doc_path=None) -> None:
    """Submit-tail auto-run (the single-run hook point): runs each
    **complete** recipe in turn (code + script both present — empty
    placeholders from add are silently skipped); a single failure emits
    one line ``post failed {name}``, the rest still run, and submit still
    returns normally (kernel success is success — the strong form of no
    collective failure); success emits zero lines. ``doc_path=None`` =
    the main document."""
    doc = env.doc_path if doc_path is None else doc_path
    for name, entry in env.post_recipes.items():
        if not (entry["code"] and entry["script"]):
            continue                      # placeholder / incomplete: skip
        try:
            post_one(env, doc, name, entry, jid)
        except Exception as e:            # noqa: BLE001 — one line, no collective failure
            env.emit(ipc.out(ROW_POST_FAILED.format(
                name=name, id=jid,
                detail=f"{type(e).__name__}: {e}")))


def post_out(env, doc_path, name, entry, jid) -> "tuple[dict, bytes]":
    """Recipe execution core: ns = shallow copy of the session namespace +
    ``d`` injected (the document's results as a SimData, a **global
    variable** — usable directly inside the function body) → **exec the
    document's code** (the authoritative copy, overriding same-named
    in-session (live) functions; the whitelist gate at update time already
    guarantees no f actions) → **eval the call line** (a direct call
    expression whose return value is the output; dict + value-domain
    check) → (dict, npz bytes). Exceptions propagate as-is; the ns is
    discarded after use (copied yes, merged back no)."""
    scratch = tempfile.mkdtemp(prefix="genie-post.")
    try:
        path = archive.extract_result(doc_path, scratch, jid)
        data = core_results.open_result(path, job_id=jid,
                                        scratch=scratch)
        try:
            ns = dict(env.current_ns())   # shallow copy: seeded with live session constants
            ns["d"] = data                # d = this document's results (injected as a global)
            exec(compile(entry["code"],
                         f"<genie-post-code:{name}>", "exec"), ns)
            out = eval(compile(entry["script"],
                               f"<genie-post-run:{name}>", "eval"), ns)
            if not isinstance(out, dict):
                raise RuntimeError(
                    f"post_analyze {name!r} call must return a dict, "
                    f"got {type(out).__name__}")
            check_post_out(out)
            buf = io.BytesIO()
            np.savez(buf, **out)
            return out, buf.getvalue()
        finally:
            data.close()                  # scratch dir cleared with it
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
