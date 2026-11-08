"""genie.worker.run — the f verb surface (the manifest file: every public
action in one place; the machinery lives in chain / post / sweep — the
material verbs' thin-delegation pattern, generalized).

- ``f.submit(n_gpu=None)`` (the single-run public verb): promote + validate
  → submit to ``/gw`` (batch of 1) → stage the ``.gensim`` → wait → inject
  → the post tail hook → return the job id; failure / cancel raises
  ``GenieJobError``. Composed from chain's primitives — the same machine
  the sweep driver imports (Q10: a sweep is a batched submit).
- ``f.validate()`` (design-time preflight): the same check ``submit`` runs
  (promote + ``validate_for_ui``), with no submit, no document write and no
  terminal row; returns the error list instead of raising.
- The four read verbs (parsing semantics live in core/results; this module
  only does "pick the member + open the file").
- The four post verbs (add / update / run / delete) — thin delegates to
  ``worker.post``.
- The five material verbs — thin delegates to ``core/material``.
- Document commands ``_save`` / ``_open`` / ``_dirty`` and the restore
  entry points ``load_model`` / ``load_recipes``.
- Internal primitives ``_submit`` / ``_wait`` / ``_cancel`` — thin
  delegates to ``worker.chain`` (silent, zero terminal lines; not on the
  public face).

The five terminal rows (constants) live in chain; they are re-exported
here because this file is the wording surface.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile

import numpy as np

from genie import ipc
from genie.core import archive
from genie.core import material as core_material
from genie.core import results as core_results
from genie.core.material import C0_SI as C0
from genie.core.model import Model
from genie.core.model.draft import digest
from genie.net import TERMINAL_OK, GenieJobError
from genie_protocol.resolve import validate_for_ui

from . import chain, plot, post, sweep
from .chain import (ROW_CANCELED, ROW_DONE, ROW_FAILED, ROW_POST_FAILED,
                    ROW_SUBMIT_ONE)
from .post import UNSET, resolve_result

__all__ = ["F", "bind", "load_model", "load_recipes", "load_sweeps",
           "ROW_SUBMIT_ONE", "ROW_DONE", "ROW_FAILED", "ROW_CANCELED",
           "ROW_POST_FAILED"]


def load_model(doc_path) -> tuple[Model, dict]:
    """``.gensim`` → (Model, state): the id allocator resumes from max —
    the single implementation shared by startup restore and ``f._open``.
    Unreadable (missing file / not a .gensim / state missing the four
    fields) → RuntimeError with a one-line message."""
    try:
        cfg = archive.read_config(doc_path)
        state = archive.read_state(doc_path)
    except Exception as e:                      # noqa: BLE001 — one-line error
        raise RuntimeError(f"cannot open {str(doc_path)!r}: {e}") from e
    return Model.from_config(cfg, next_id=state.get("allocator")), state


def load_recipes(doc_path) -> dict:
    """``.gensim`` → the named recipe set ``{name: {code, script}}``; no
    document / member absent / unreadable → ``{}`` (the single
    implementation for startup restore and ``f._open``). Legacy entries
    (plain script strings / older {script, files} dicts) are normalized
    into {code, script} entries (missing code = empty — re-import via
    update)."""
    if doc_path and os.path.isfile(doc_path):
        try:
            raw = archive.read_recipes(doc_path)
        except Exception:                       # noqa: BLE001 — lenient restore
            return {}
        out = {}
        for name, val in raw.items():
            if isinstance(val, str):            # legacy plain-string entry
                val = {"script": val}
            out[name] = {"code": val.get("code", ""),
                         "script": val.get("script", "")}
        return out
    return {}


def load_sweeps(doc_path) -> dict:
    """``.gensim`` → the named sweep definitions ``{name: {parameters,
    mode, n_gpu, on_failure}}``; no document / member absent /
    unreadable → ``{}`` (restored with the document, like the recipe
    set)."""
    if doc_path and os.path.isfile(doc_path):
        try:
            return archive.read_sweeps(doc_path)
        except Exception:                 # noqa: BLE001 — lenient restore
            return {}
    return {}


class F:
    """f = core ``Model`` + actions (semantics live in core; binding and
    action implementations live in worker). All attribute reads/writes
    delegate to the model; submit / validate / _submit / _wait / _cancel /
    _save / _open / _dirty + the four read verbs + five material verbs +
    the four post verbs + the three param_sweep verbs; **there is no
    cancel** — public cancel is a session-intercepted command, not in
    worker."""

    __slots__ = ("_model", "_env")

    def __init__(self, model, env):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_env", env)

    def __getattr__(self, name):
        return getattr(self._model, name)

    def __setattr__(self, name, value):
        setattr(self._model, name, value)

    def __repr__(self):
        return f"<f model={self._model!r}>"

    # -- public verb (the only one; single simulation)-----------

    def submit(self, *, n_gpu=None):
        """Run the current model once and block until it finishes.

        Promotes the live model to a strict config, validates it,
        submits it, waits for the terminal state, then writes the
        result into the document (``results/<job>.h5``) and returns the
        job id (``str``). Submitting invalidates the previous run's
        results immediately. Failure or cancellation raises
        ``GenieJobError`` (wording ``failed`` / ``canceled``).

        ``n_gpu`` mirrors the ``run.n_gpu`` config field; it goes only
        into the submit payload and is never written back to the model.
        Batch runs (parameter sweeps) go through
        ``f.add_param_sweep`` / ``f.run_param_sweep`` — this verb always
        runs exactly one simulation.
        """
        env = self._env
        cfg = self._model.to_config()             # promotion: live model → strict config
        chain._validate([cfg])
        jid = chain.submit(env, self._model, cfg, n_gpu=n_gpu)
        env.emit(ipc.out(ROW_SUBMIT_ONE.format(id=jid)))
        staged = chain.stage(env, self._model, [jid])   # submitting invalidates the old batch
        staged_digest = digest(staged)
        outcome = chain.wait(env, [jid])[0]       # the repo's single wait implementation
        if outcome["state"] in TERMINAL_OK:
            env.emit(ipc.out(ROW_DONE.format(id=jid)))
            archive.inject_result(env.doc_path, jid,
                                  env.client().stream_result(jid),
                                  chain.state_for(staged,
                                                  self._model.last_id, [],
                                                  staged_digest))
            post.run_post(env, jid)               # submit-tail auto-run (only if recipes exist)
            return jid
        if outcome["state"] == "failed":
            env.emit(ipc.out(ROW_FAILED.format(
                id=jid, detail=outcome.get("error") or outcome["state"])))
        else:                                      # killed / canceled
            env.emit(ipc.out(ROW_CANCELED.format(id=jid)))
        raise GenieJobError(jid, outcome["state"], error=outcome.get("error"))

    # -- design-time preflight (the same surface as submit's precheck, no submit) --

    def validate(self):
        """Preflight the **current model** without submitting.

        Promotes the live model exactly like ``submit`` does and runs the
        same validation surface (``protocol.validate_for_ui``): parameter
        key names → expression evaluation (Q13) → structural / value
        guards. Nothing is sent anywhere, nothing is written to the
        document, no terminal row is emitted.

        Returns a list of ``{"loc": ..., "msg": ...}`` — empty means the
        model would pass submit's precheck; ``loc`` is the in-config path
        (e.g. ``materials.0.const.eps``), ``""`` means a whole-config
        error. Design-time only: the resource guard is deployment policy
        and is not part of this surface.
        """
        return validate_for_ui(self._model.to_config())

    # -- the four read verbs (parsing semantics live in core/results, zero terminal lines)---------

    def results(self):
        """The current results → lazy SimData wrapper (``data.<mon>.<field>``
        direct access; no argument — the document always holds exactly one
        member). The read-cache directory belongs to the returned object
        (cleared on close)."""
        env = self._env
        jid = resolve_result(env)
        scratch = tempfile.mkdtemp(prefix="genie-results.")
        path = archive.extract_result(env.doc_path, scratch, jid)
        return core_results.open_result(path, job_id=jid, scratch=scratch)

    def get_time(self, name=None):
        """Time-domain monitors → TimeResult (eager parsing; default = the
        domain's single group taken directly, multiple groups named
        explicitly)."""
        return self._read_monitor("t", name)

    def get_freq(self, name=None):
        """Frequency-domain monitors → FreqResult (eager parsing; default =
        the domain's single group taken directly, multiple groups named
        explicitly)."""
        return self._read_monitor("f", name)

    def get_index(self, name=None):
        """Index monitors → IndexResult (eager parsing; default = the
        domain's single group taken directly, multiple groups named
        explicitly)."""
        return self._read_monitor("i", name)

    # -- the two result-view verbs (Q4.5 public read verbs; emit structured
    # out events for the GUI, zero terminal lines). -------------------------

    def result_meta(self):
        """Current result's structure list → emits ``out(kind="meta")``
        and returns the same dict (script / agent use)."""
        data = self.results()
        try:
            payload = plot.build_meta(data)
            self._env.emit(ipc.out("result meta",
                                   kind=ipc.OUT_KIND_META, data=payload))
        finally:
            data.close()
        return payload

    def visualize(self, mon, field=None, axes=None, slices=None):
        """One result-view series → emits ``out(kind="series")`` and returns
        the same dict. ``axes`` = two of x/y/z for index 2D grids;
        ``slices`` = pin the remaining axis at an index (``{axis: i}``).
        Full arrays — no downsampling (station decision 2026-11-06)."""
        data = self.results()
        try:
            payload = plot.build_series(data, mon, field=field,
                                        axes=axes, slices=slices)
            self._env.emit(ipc.out("result series",
                                   kind=ipc.OUT_KIND_SERIES, data=payload))
        finally:
            data.close()
        return payload

    def _read_monitor(self, domain, name):
        env = self._env
        jid = resolve_result(env)
        scratch = tempfile.mkdtemp(prefix="genie-results.")
        try:
            path = archive.extract_result(env.doc_path, scratch, jid)
            data = core_results.open_result(path, job_id=jid)
            try:
                return data.get_time(name) if domain == "t" else \
                    data.get_freq(name) if domain == "f" else \
                    data.get_index(name)
            finally:
                data.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # -- the four post verbs (implementation lives in worker/post)----------

    def add_post_analyze(self, name):
        """Create a new **empty** recipe (placeholder; same name already
        exists → one-line error)."""
        return post.add_verb(self._env, name)

    def update_post_analyze(self, name, script_location):
        """Import the py file's full text into the recipe's code slot
        (whitelist gate runs here; the document is the authoritative
        copy; does not run)."""
        return post.update_verb(self._env, name, script_location)

    def run_post_analyze(self, name=None, script=UNSET):
        """Call and compute (direct call expression; return dict →
        ``post/<name>.npz``; call line recorded first). ``run()`` = all
        complete recipes."""
        return post.run_verb(self._env, name, script)

    def delete_post_analyze(self, name):
        """Delete one (registry + document synced + orphan npz removed;
        idempotent)."""
        return post.delete_verb(self._env, name)

    # -- the three param_sweep verbs (implementation lives in worker/sweep)------

    def add_param_sweep(self, name, parameters, *, mode="product",
                        n_gpu=None, on_failure="continue"):
        """Define a named parameter sweep: ``parameters = {name:
        sequence}`` (one call carries the whole definition — a definition
        is data, not code; same name overwrites; the name becomes a
        folder name, so letters / digits / '_' / '-' only). Definitions
        are persisted to the document (``sweeps.json`` — the
        authoritative copy)."""
        return sweep.add_verb(self._env, name, parameters, mode=mode,
                              n_gpu=n_gpu, on_failure=on_failure)

    def run_param_sweep(self, name):
        """Run one named sweep and block until the whole batch is
        terminal: expand → one batch submit → stage each point document
        (``parameter_sweep_<name>/p####.gensim`` — re-running a name
        clears and re-stages the folder) → collect each job as it
        finishes (inject result + the same post tail hook + the index
        line). Returns the Sweep handle; ``on_failure="abort"`` raises
        ``GenieJobError`` at the first bad point (still-live jobs
        canceled); zero terminal lines (progress rides the job card)."""
        return sweep.run_verb(self._env, self._model, name)

    def delete_param_sweep(self, name):
        """Delete one definition (document synced; the folder on disk is
        untouched). Idempotent."""
        return sweep.delete_verb(self._env, name)

    # -- material verbs (implementation lives in core/material, zero terminal lines)------------

    def _source_band(self):
        """The configured **source band** as a wavelength window [m], or None.

        Lumerical's rule: the material fit range follows the simulation
        band, not the raw data range. Sources are stored as
        ``{freq, bandwidth}`` in Hz; the window is the union of
        ``freq ± bandwidth/2`` converted to wavelength (min f → max λ).
        """
        try:
            cfg = self._model.to_config()
        except Exception:                                   # noqa: BLE001
            return None
        lo_hz = hi_hz = None
        for src in cfg.get("source") or []:
            if not isinstance(src, dict):
                continue
            try:
                f0 = float(src.get("freq", 0.0))
                bw = float(src.get("bandwidth", 0.0))
            except (TypeError, ValueError):
                continue
            if not (f0 > 0.0):
                continue
            lo, hi = f0 - 0.5 * abs(bw), f0 + 0.5 * abs(bw)
            if not (lo > 0.0):
                lo = 0.5 * f0                    # guard a band crossing DC
            lo_hz = lo if lo_hz is None else min(lo_hz, lo)
            hi_hz = hi if hi_hz is None else max(hi_hz, hi)
        if lo_hz is None:
            return None
        return (C0 / hi_hz, C0 / lo_hz)

    def _fit_band(self, wl, band):
        """Resolve the fit window: **explicit band, verbatim**; otherwise the
        simulation source band; otherwise (no source) the data range.

        An explicit ``band`` is never second-guessed: if it holds too few
        samples the fit raises Lumerical-style ("Only N data points in band
        … widen band"), because the user asked for exactly that window. The
        *default* (source band) is a convenience, so it falls back to the
        data range when it is too sparse — and says so.
        """
        if band is not None:
            return band, None
        want = self._source_band()
        if want is None:
            return None, None
        w = np.asarray(wl, dtype=float)
        inside = int(np.count_nonzero((w >= want[0]) & (w <= want[1])))
        if inside >= 3:
            return want, None
        note = (f"source band {want[0] * 1e9:.0f}-{want[1] * 1e9:.0f} nm "
                f"covers only {inside} data point(s) — fitted over the full "
                f"data range instead; pass band= to force a window")
        return None, note

    def fit_nk(self, wl, n, k, band=None, tolerance=0.1, max_poles=6,
               passive=None):
        """**Single-component** nk → an ``NKFit`` (band · poles · quality ·
        warnings; the opaque pole payload lives in ``model``).

        The band defaults to the **simulation source band** (fit where you
        will simulate, as Lumerical does), falling back to the data range
        when the source band is too sparse. ``tolerance`` is Lumerical's
        weighted-RMS bound on the eps residual (default 0.1); ``max_poles``
        is the coefficient budget (default 6 — both are Lumerical's material
        defaults). ``passive=None`` runs the passivity penalty. Iso still
        goes through ``f.assemble_nk``."""
        band, note = self._fit_band(wl, band)
        fit = core_material.fit_nk(wl, n, k, band=band, tolerance=tolerance,
                                   max_poles=max_poles, passive=passive)
        if note is not None:
            fit = dataclasses.replace(fit, warnings=tuple(fit.warnings)
                                      + (note,))
        return fit

    def assemble_nk(self, fits, name=None):
        """Per-component fit_nk results → a single material node (diagonal
        xx/yy/zz required; the off-diagonal upper triangle mounted under
        one name and mirrored to keep symmetry; rank-1 = the kernel-native
        form)."""
        return core_material.assemble_nk(fits, name=name)

    def load_nk(self, name, component=None):
        """Local n-k library → (wl_m, n, k) (iso taken directly; composite
        materials must name the component explicitly — single returns
        directly, multiple errors out)."""
        return core_material.load_nk(name, component=component)

    def mat_from_nk(self, name, band=None, tolerance=0.1, max_poles=6):
        """Library name → material node in one step (a near-zero-knob
        convenience layer; one line to add into the model:
        ``f.mat.add_lorentz(node)``).

        ``band`` is the fit window [m]: **pass it and it is honoured
        verbatim** (too few samples → a clear "widen band" error). Omit it
        and the fit follows the **simulation source band**; if that band is
        too sparse in the library, it falls back to the full data range and
        says so on the terminal.
        """
        if band is not None:
            return core_material.mat_from_nk(
                name, band=band, tolerance=tolerance, max_poles=max_poles)
        want = self._source_band()
        if want is not None:
            try:
                return core_material.mat_from_nk(
                    name, band=want, tolerance=tolerance, max_poles=max_poles)
            except ValueError as exc:
                if "data points in band" not in str(exc):
                    raise
                core_material._report(
                    f"source band {want[0] * 1e9:.0f}-{want[1] * 1e9:.0f} nm "
                    f"is too sparse in the {name} library — fitted over the "
                    f"full data range instead; pass band= to force a window")
        return core_material.mat_from_nk(name, band=None, tolerance=tolerance,
                                         max_poles=max_poles)

    def list_library_materials(self):
        """Local library material names (sorted)."""
        return core_material.list_library_materials()

    # -- internal primitives (for driver orchestration, silent throughout)-------------------

    def _submit(self, cfg_or_cfgs=None, *, n_gpu=None):
        """Submit and return handles immediately (no staging, no waiting,
        zero terminal text; single = batch of 1, same shape as the public
        surface)."""
        return chain.submit(self._env, self._model, cfg_or_cfgs,
                            n_gpu=n_gpu)

    def _wait(self, handles):
        """Wait for a batch of jobs to reach a terminal state, collecting
        them in completion order; refreshes the progress job card during
        the wait loop (zero terminal text). Reports terminal states
        without judging — failure policy belongs to the driver layer.
        Returns ``[{job_id, state, error?}]`` (completion order)."""
        return chain.wait(self._env, handles)

    def _cancel(self, handles=None) -> None:
        """Cancel in-flight jobs (dialed straight through the gateway;
        zero terminal text). ``handles=None`` = every in-flight job of
        this f (the inflight tracked on the exec surface)."""
        chain.cancel(self._env, handles)

    # -- document commands (_save + startup restore)--

    def _save(self, path=None):
        """Save / save-as (no path = save). digest is updated to the
        save-time fingerprint, inflight cleared, results and post outputs
        carried over (the current batch); the recipe set enters the
        document from current in-memory values. After save-as the live
        document path follows."""
        env = self._env
        target = path or env.doc_path
        cfg = self._model.to_config()
        archive.save(target, simconfig=cfg,
                     state=chain.state_for(cfg, self._model.last_id, []),
                     carry_result_from=env.doc_path,
                     post_recipes=env.post_recipes, sweeps=env.sweeps)
        env.doc_path = target
        return target

    def _open(self, path):
        """Open an existing `.gensim`: swap the model wholesale
        (the id allocator resumes from max) + the live document path
        follows + `f` re-injected immediately + the recipe set swaps with
        the document; results members stay in the document; unsaved
        changes are discarded on the swap. Unopenable → one-line error,
        and neither model nor path moves."""
        env = self._env
        model, _state = load_model(path)    # fails before assignment: nothing moves
        env.model = model
        env.doc_path = str(path)
        env.post_recipes = load_recipes(str(path))
        env.sweeps = load_sweeps(str(path))
        env._reinject_f()                   # later statements in the same exec see the new model at once
        return str(path)

    def _dirty(self) -> bool:
        """Model fingerprint ≠ the current `.gensim`'s state.digest →
        True; no document / unreadable document = True (the mirror side of
        staging counting as one save)."""
        env = self._env
        doc = env.doc_path
        if not doc or not os.path.isfile(doc):
            return True
        try:
            state = archive.read_state(doc)
        except Exception:                   # noqa: BLE001 — no saved state to compare against
            return True
        return digest(self._model.to_config()) != state.get("digest")


def bind(model, env) -> F:
    """The f composite (exec.py only builds the binding; action
    implementations live in this package)."""
    return F(model, env)
