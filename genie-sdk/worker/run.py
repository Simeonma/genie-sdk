"""genie.worker.run — the genie verb surface (the manifest file: every
public action in one place; the machinery lives in chain / post / sweep —
the material verbs' thin-delegation pattern, generalized).

- ``genie.<solver>.run(n_gpu=None)`` (the one public run verb, Q15):
  runs the model **through one declared solver** — promote + validate →
  submit to ``/gw`` (batch of 1) → stage the ``.gensim`` → wait → inject
  → the post tail hook → return the job id; failure / cancel raises
  ``GenieJobError``. The fdtd default omits the wire ``solver`` field
  (legacy payloads byte-identical); any other declared name goes into
  the payload verbatim, never written back to the model (same rule as
  `
_gpu``). Composed from chain's primitives — the same machine the
  sweep driver imports (Q10: a sweep is a batched run).
- ``genie.<solver>.validate()`` (design-time preflight): the same check
  ``run`` runs for that solver (promote + ``validate_for_ui``), with no
  submit, no document write and no terminal row; returns the error list
  instead of raising. ``genie.validate()`` = the same surface on the
  fdtd default.
- The read verbs (parsing semantics live in core/results; this
  module only does "pick the member + open the file"): the monitor three
  (get_time/get_freq/get_index) + ``mode_results`` (Q18 拍① — the mode
  eigenmode list, same eager path).
- The four post verbs (add / update / run / delete) — thin delegates to
  ``worker.post``.
- The five material verbs — thin delegates to ``core/material``.
- Document commands ``_save`` / ``_open`` / ``_dirty`` and the restore
  entry points ``load_model`` / ``load_recipes``.
- Internal primitives ``_submit`` / ``_wait`` / ``_cancel`` — thin
  delegates to ``worker.chain`` (silent, zero terminal lines; not on the
  public face).

``genie.lib`` is the package escape hatch (the handle shadows the module
name ``genie`` inside exec namespaces; everything the package exports is
reachable through it). The five terminal rows (constants) live in chain;
they are re-exported here because this file is the wording surface.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile

import numpy as np

import genie as _genie_pkg

from genie import ipc
from genie.core import archive
from genie.core import material as core_material
from genie.core import results as core_results
from genie.core.material import C0_SI as C0
from genie.core.model import Model
from genie.core.model.draft import digest
from genie.net import TERMINAL_OK, GenieJobError
from ..core.param import validate_for_ui

from . import chain, plot, post, sweep
from .api import (Drude as _Drude, Lorentz as _Lorentz,
                  Rational as _Rational, Surface,
                  _ROCollection, _ROItem, _ROMapping)
from .chain import (ROW_CANCELED, ROW_DONE, ROW_FAILED, ROW_POST_FAILED,
                    ROW_SUBMIT_ONE)
from .post import UNSET, resolve_result

__all__ = ["Genie", "bind", "load_model", "load_recipes", "load_sweeps",
           "ROW_SUBMIT_ONE", "ROW_DONE", "ROW_FAILED", "ROW_CANCELED",
           "ROW_POST_FAILED"]


def load_model(doc_path) -> tuple[Model, dict]:
    """``.gensim`` → (Model, state): the id allocator resumes from max —
    the single implementation shared by startup restore and ``genie._open``.
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
    implementation for startup restore and ``genie._open``). Legacy entries
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


class _SolverHandle:
    """One declared solver seen from the script side: attribute reads and
    writes delegate to the solver's envelope (domain / mesh / sources /
    monitors — Q15); the action surface adds ``run`` / ``validate``, and
    the Q25 creation verbs ``add_source`` / ``add_monitor`` /
    ``add_mesh_refine``."""

    __slots__ = ("_model", "_env", "_name", "_surface")

    def __init__(self, model, env, name, surface=None):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_surface", surface)

    def _solver(self):
        return self._model.envelope(self._name)

    def __getattr__(self, name):
        env = self._solver()
        if name in ("sources", "monitors"):
            return _ROCollection(getattr(env, name), f"{self._name}.{name}")
        if name in ("domain", "mesh"):
            return _ROItem(getattr(env, name), f"{self._name}.{name}")
        return getattr(env, name)

    def __setattr__(self, name, value):
        raise AttributeError(
            f"genie.{self._name}.{name}: attributes are read-only — use "
            f"genie.set_named({self._name + '.' + name!r}, ...)")

    def __repr__(self):
        return f"<genie.{self._name}>"

    # -- Q25 creation verbs ------------------------------------------

    def _children(self):
        if self._surface is None:
            raise RuntimeError("solver handle is not bound to a genie surface")
        return self._surface.solver(self._name)

    def add_source(self, name, type):        # noqa: A002
        return self._children().add_source(name, type)

    def add_monitor(self, name, type):       # noqa: A002
        return self._children().add_monitor(name, type)

    def add_mesh_refine(self, name):
        return self._children().add_mesh_refine(name)


class Genie:
    """genie = core ``Model`` + actions (semantics live in core; binding
    and action implementations live in worker). Attribute reads/writes
    delegate to the model (structure: materials / geometry / attributes /
    parameters);
    a declared solver name resolves to its handle (``genie.fdtd`` —
    auto-vivified default — or any name declared via
    ``genie.add_solver``); ``genie.lib`` is the package escape hatch.
    Read / material / post / sweep verbs + the document commands;
    ``run`` lives on the solver handles; **there is no cancel** —
    public cancel is a session-intercepted command, not in worker."""

    __slots__ = ("_model", "_env", "lib", "_surface")

    def __init__(self, model, env):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_env", env)
        # the handle shadows the package name inside exec namespaces;
        # this is the way back to everything the package exports
        object.__setattr__(self, "lib", _genie_pkg)
        object.__setattr__(self, "_surface", Surface(model, env))

    def __getattr__(self, name):
        try:
            attr = getattr(self._model, name)
        except AttributeError:
            attr = None
        if name == "parameters":
            return _ROMapping(self._model.parameters)
        if name in ("geometry", "materials", "attributes"):
            return _ROCollection(attr, name)
        if attr is not None:
            return attr
        # solver access: a well-formed but undeclared name raises with
        # add_solver guidance (as AttributeError, so hasattr() behaves);
        # the fdtd default auto-vivifies inside envelope()
        try:
            self._model.envelope(name)
        except LookupError as e:
            raise AttributeError(str(e)) from None
        return _SolverHandle(self._model, self._env, name, self._surface)

    def __setattr__(self, name, value):
        raise AttributeError(
            f"genie.{name}: attributes are read-only — use "
            f"genie.set_named(\"<family>.{name}\", ...)")

    def __repr__(self):
        return f"<genie model={self._model!r}>"

    # -- design-time preflight (the same surface as run's precheck, no submit) --

    def validate(self):
        """Preflight the **fdtd default configuration** without
        submitting (the common case; per-solver: ``genie.<s>.validate()``).

        Promotes the live model exactly like ``run`` does and runs the
        same validation surface (``protocol.validate_for_ui``):
        parameter key names → expression evaluation (Q13) → structural /
        value guards. Nothing is sent anywhere, nothing is written to the
        document, no terminal row is emitted.

        Returns a list of ``{"loc": ..., "msg": ...}`` — empty means the
        model would pass run's precheck; ``loc`` is the in-config path
        (e.g. ``materials.0.const.eps``), ``""`` means a whole-config
        error. Design-time only: the resource guard is deployment policy
        and is not part of this surface.
        """
        return validate_for_ui(self._model.to_config())

    # -- the read verbs (parsing semantics live in core/results, zero terminal lines)---------

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

    def mode_results(self):
        """Mode solver results → ``ModeSweepResult`` (eager parsing — one
        ``ModeResult`` per wavelength: ``.neff`` / ``.fields{分量}`` /
        ``.coords`` / ``.modes[i]``; a single-λ job is the one-element
        case; zero terminal lines)."""
        return self._read_current(lambda data: data.get_modes())

    def get_expansion(self, mon=None):
        """Kernel-written mode-expansion result for ``mon`` (or the sole
        one when omitted); eager ``ModeExpansionResult``."""
        return self._read_current(lambda data: data.get_expansion(mon))

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

    def _read_current(self, pick):
        """The scratch-dance shared by the eager read verbs (Q18: the
        monitor three + mode_results): extract the current member → open
        behind the format guard → ``pick(data)`` → close + clean. The
        lazy ``results()`` owns its scratch and is deliberately separate."""
        env = self._env
        jid = resolve_result(env)
        scratch = tempfile.mkdtemp(prefix="genie-results.")
        try:
            path = archive.extract_result(env.doc_path, scratch, jid)
            data = core_results.open_result(path, job_id=jid)
            try:
                return pick(data)
            finally:
                data.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

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
        for src in (cfg.get("config") or {}).get("source") or []:
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

    def fit_nk(self, wl, n, k, band=None, max_poles=10,
               imaginary_weight=1.0, make_passive=False, tolerance=0.1):
        """**Single-component** nk → an ``NKFit`` (band · poles · quality ·
        warnings; the opaque pole payload lives in ``model``).

        The band defaults to the **simulation source band** (fit where you
        will simulate, as Lumerical does), falling back to the data range
        when the source band is too sparse. ``max_poles`` is the order-search
        ceiling (default 10); ``imaginary_weight`` weights the imaginary
        residual (default 1.0). Iso still goes through ``genie.assemble_nk``."""
        band, note = self._fit_band(wl, band)
        fit = core_material.fit_nk(wl, n, k, band=band,
                                   max_poles=max_poles,
                                   imaginary_weight=imaginary_weight,
                                   make_passive=make_passive,
                                   tolerance=tolerance)
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

    def _fit_preview(self, name, band=None, max_poles=10, make_passive=False,
                    tolerance=0.1):
        """Library name → fit **preview** (emits ``out(kind="matfit")`` and
        returns the same payload).

        Read-only: it fits and reports the curves, it does **not** touch the
        model (the GUI's material dialog draws them; ``mat_from_nk`` still
        owns "put it in the model"). ``band`` [m] omitted → the fit follows
        the simulation source band, exactly like ``mat_from_nk``.
        """
        wl, n, k = core_material.load_nk(name)
        fit = self.fit_nk(wl, n, k, band=band, max_poles=max_poles,
                          make_passive=make_passive, tolerance=tolerance)
        raw = fit.curve()                       # data points inside the band
        grid = np.linspace(fit.band[0], fit.band[1], 200)
        smooth = fit.curve(wl=grid)             # the fit on a dense grid
        payload = {
            "name": str(name),
            "band": [float(fit.band[0]), float(fit.band[1])],
            "n_poles": int(fit.n_poles),
            "tolerance": float(tolerance),
            "summary": fit.summary(),
            "quality": {k2: (float(v) if isinstance(v, (int, float)) else v)
                        for k2, v in fit.quality.items()},
            "warnings": [str(w) for w in fit.warnings],
            "wl": [float(x) for x in raw["wl"]],
            "n": [float(x) for x in raw["n"]],
            "k": [float(x) for x in raw["k"]],
            "wl_fit": [float(x) for x in smooth["wl"]],
            "n_fit": [float(x) for x in smooth["n_fit"]],
            "k_fit": [float(x) for x in smooth["k_fit"]],
        }
        self._env.emit(ipc.out(f"material fit {name}",
                               kind=ipc.OUT_KIND_MATFIT, data=payload))
        return payload

    def _material_library(self):
        """Local library names for the GUI picker → emits
        ``out(kind="matlib")`` and returns the same list (script / agent).

        Lives on the worker (not a host route): the host is barred from
        importing ``genie.core`` (test_host_purity), and the library is the
        one source of truth for names.
        """
        names = list(core_material.list_library_materials())
        self._env.emit(ipc.out("material library", kind=ipc.OUT_KIND_MATLIB,
                               data={"materials": names}))
        return names

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


    # ══════════════════════════════════════════════════════════════════
    # Q25 unified surface (docs/API-STYLE.md) — thin delegates to
    # worker.api.Surface; the old verb family is retired in the next
    # migration commit.
    # ══════════════════════════════════════════════════════════════════

    def add_parameter(self, name):
        return self._surface.add_parameter(name)

    def add_material(self, name, type, *, library=None):  # noqa: A002
        return self._surface.add_material(name, type, library=library)

    def add_geometry(self, name, type):  # noqa: A002
        return self._surface.add_geometry(name, type)

    def add_attribute(self, name, type="matrix_frame"):  # noqa: A002
        return self._surface.add_attribute(name, type)

    def add_analysis(self, name):
        return self._surface.add_analysis(name)

    def add_sweep(self, name):
        return self._surface.add_sweep(name)

    def add_solver(self, name, type=None):  # noqa: A002
        return self._surface.add_solver(name, type)

    def set_named(self, addr, **fields):
        return self._surface.set_named(addr, **fields)

    def get_named(self, addr, field=None):
        return self._surface.get_named(addr, field)

    def describe(self, addr):
        return self._surface.describe(addr)

    def remove(self, addr):
        return self._surface.remove(addr)

    def fit_material(self, name):
        return self._surface.fit_material(name)

    def run_fdtd(self, *, n_gpu=None):
        return self._surface.run_fdtd(n_gpu=n_gpu)

    def run_mode(self, *, n_gpu=None):
        return self._surface.run_mode(n_gpu=n_gpu)

    def run_sweep(self, name):
        return self._surface.run_sweep(name)

    def run_analysis(self, name=None, call=UNSET):
        return self._surface.run_analysis(name, call)

    def get_result(self, mon, field=None):
        return self._surface.get_result(mon, field)

    def save(self, path=None):
        return self._surface.save(path)

    def open(self, path):
        return self._surface.open(path)

    def validate(self):
        return self._surface.validate()

    # payload constructors (values, not model objects)
    Drude = staticmethod(_Drude)
    Lorentz = staticmethod(_Lorentz)
    Rational = staticmethod(_Rational)


def bind(model, env) -> Genie:
    """The genie composite (exec.py only builds the binding; action
    implementations live in this package)."""
    return Genie(model, env)
