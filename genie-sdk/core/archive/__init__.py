"""genie.core.archive — `.gensim` document container primitives.

Contract:

- Three-member layout ``simconfig.json + results/ + state.json``;
- Per-member compression policy: result h5 always STORED, small members
  DEFLATED;
- Atomic persistence = write the complete document to temp, then rename;
  on mid-way failure the original file stays intact and temp is cleaned up;
- Result members are written as streams, no whole-payload buffering;
- **Only the current batch is kept**: one ``results/<job>.h5`` member per
  job in the batch; staging (no results) invalidates the old batch;
  ``inject_result`` = "inject one member + update state" as a single
  atomic step;
- **Opaque to member content** (no interpretation, no semantic validation;
  h5 belongs to results, model restoration belongs to worker); **zero
  genie dependencies** (standard library only); **carries no business
  state machine, reads no environment** (paths are passed in explicitly).
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

__all__ = ["STATE_KEYS", "Writer", "save", "inject_result", "read_config",
           "read_state", "result_jobs", "extract_result", "read_recipes",
           "write_recipes", "read_sweeps", "write_sweeps", "inject_post",
           "remove_post", "post_names"]

_CONFIG_MEMBER = "simconfig.json"
_STATE_MEMBER = "state.json"
_RESULT_PREFIX = "results/"
_RESULT_SUFFIX = ".h5"
#: Post members (the document is the authoritative copy): the recipe
#: member at the document root ``post_analyze.json`` (named entries
#: ``{name: {code, script}}``) plus ``post/<name>.npz`` output members
#: (the name is the key — one output member per name).
_RECIPE_MEMBER = "post_analyze.json"
#: Named sweep definitions (Q10): ``sweeps.json`` at the document root —
#: ``{name: {parameters, mode, n_gpu, on_failure}}``; the authoritative
#: copy lives in the main document (never sent over the wire).
_SWEEPS_MEMBER = "sweeps.json"
_POST_PREFIX = "post/"
_POST_SUFFIX = ".npz"
_COPY_CHUNK = 1 << 20                      # streaming copy chunk size (used when carrying over result members)

#: state.json key set: format = format version (key absent = v1 legacy
#: document), allocator = id allocator value, digest = model fingerprint
#: at save time (written at staging = staging counts as one save),
#: inflight = in-flight batch trace (this batch's job id list; cleared on
#: a successful terminal state, kept on failure / cancel). The job binding
#: does not go in here — the results/ member's entry name is the binding.
STATE_KEYS = ("format", "allocator", "digest", "inflight")


def _check_state(state: dict) -> dict:
    if set(state) != set(STATE_KEYS):
        raise ValueError(
            f"state.json keys must be exactly {sorted(STATE_KEYS)}, "
            f"got {sorted(state)}")
    if not isinstance(state["inflight"], list):
        raise ValueError("state.json 'inflight' must be a list of job ids")
    return state


class Writer:
    """Atomically write a whole `.gensim`: persist to temp → rename only
    on success.

    Usage (both worker/run regular saves and "auto-save on run success"
    go through here)::

        with Writer(path) as w:
            w.write_config(cfg)
            w.write_state({"format": 2, "allocator": 25, "digest": "…"})
            w.carry_result(old_path)             # save after model changes: results carried over
            w.stream_result(job_id, chunk_iter)  # new result: streamed write (either one of the two)

    Abnormal exit = the original `.gensim` on disk is byte-for-byte intact;
    temp leftovers are cleaned up. At most one result member per document
    (a shape guard, not business timing/sequencing).
    """

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._tmp: Path | None = None
        self._zf: zipfile.ZipFile | None = None
        self._result_names: set = set()

    # -- lifecycle ------------------------------------------------------

    def __enter__(self) -> "Writer":
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                    prefix=self._path.name + ".",
                                    suffix=".tmp")
        os.close(fd)
        self._tmp = Path(tmp)
        self._zf = zipfile.ZipFile(self._tmp, "w", allowZip64=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        tmp, zf = self._tmp, self._zf
        self._tmp = self._zf = None
        if exc_type is not None:      # failure: clean up temp, leave the original file untouched
            try:
                if zf is not None:
                    zf.close()
            finally:
                tmp.unlink(missing_ok=True)
            return False
        try:
            zf.close()
            os.replace(tmp, self._path)   # atomic replace
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return False

    # -- member writes --------------------------------------------------

    def write_config(self, cfg: dict) -> None:
        """simconfig.json (small member, DEFLATED). Content is opaque: the
        dict is serialized as-is."""
        self._write_small(_CONFIG_MEMBER, cfg)

    def write_state(self, state: dict) -> None:
        """state.json (small member, DEFLATED). Only the four-key shape is
        accepted (STATE_KEYS)."""
        self._write_small(_STATE_MEMBER, _check_state(state))

    def _write_small(self, member: str, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self._zf.writestr(member, data,
                          compress_type=zipfile.ZIP_DEFLATED)

    def write_recipes(self, recipes: dict) -> None:
        """``post_analyze.json`` (small member at the document root,
        DEFLATED) — the named recipe set ``{name: {code, script}}``;
        content is opaque; written only when there is >= 1 entry (empty =
        the member is not written = clear semantics)."""
        self._write_small(_RECIPE_MEMBER, recipes)

    def write_sweeps(self, sweeps: dict) -> None:
        """``sweeps.json`` (small member at the document root, DEFLATED) —
        the named sweep definitions ``{name: {parameters, mode, n_gpu,
        on_failure}}``; content is opaque; written only when there is
        >= 1 entry (empty = the member is not written)."""
        self._write_small(_SWEEPS_MEMBER, sweeps)

    def _claim_result(self, name: str) -> None:
        if name in self._result_names:
            raise ValueError(
                f"results member {name!r} already written "
                f"(one member per job, no re-inject in one write)")

    def stream_result(self, job_id: str, chunks: Iterable[bytes]) -> None:
        """`results/<job>.h5`: STORED, streamed (written chunk by chunk,
        no whole-payload buffering). One member per job in the batch;
        re-injecting the same name is rejected."""
        name = _RESULT_PREFIX + job_id + _RESULT_SUFFIX
        self._claim_result(name)
        # Member compression for ZipFile.open(w) = the constructor's ZIP_STORED (the default)
        with self._zf.open(name, "w") as fh:
            for chunk in chunks:
                fh.write(chunk)
        self._result_names.add(name)

    def carry_result(self, source) -> None:
        """Carry over **all** result members + `post/*.npz` output members
        from an existing `.gensim` (regular save: the model changed, the
        results did not — the current batch is carried over; post outputs
        are carried over the same way as results). No matching members in
        the source = a silent no-op. Copied as a stream, no whole-payload
        buffering."""
        src = Path(source)
        if not src.exists():
            return
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                is_result = (name.startswith(_RESULT_PREFIX)
                             and name.endswith(_RESULT_SUFFIX))
                is_post = (name.startswith(_POST_PREFIX)
                           and name.endswith(_POST_SUFFIX))
                if not (is_result or is_post):
                    continue
                self._claim_result(name)
                with z.open(name) as r, self._zf.open(name, "w") as w:
                    while True:
                        buf = r.read(_COPY_CHUNK)
                        if not buf:
                            break
                        w.write(buf)
                self._result_names.add(name)


def save(path, *, simconfig: dict, state: dict,
         carry_result_from=None, result=None, post_recipes=None,
         sweeps=None) -> None:
    """One-step save (a composition shell over Writer; staging = this
    function called without result arguments). ``result =
    (job_id, chunks)`` writes a new result (streamed);
    ``carry_result_from = path`` carries over old results (post outputs
    included); ``post_recipes = {name: {code, script}}`` writes the recipe
    member (None / empty = not written = clear semantics; the current
    in-memory value is passed in by the caller); ``sweeps = {name: def}``
    writes the sweep-definitions member the same way; result and carry are
    mutually exclusive (a usage-shape rule, not a member cap — multiple
    members are achieved through repeated ``stream_result`` /
    ``inject_result`` calls on Writer)."""
    if carry_result_from is not None and result is not None:
        raise ValueError("save: result and carry_result_from are mutually "
                         "exclusive (a usage-shape rule; batch N uses "
                         "repeated stream/inject)")
    with Writer(path) as w:
        w.write_config(simconfig)
        w.write_state(state)
        if post_recipes:
            w.write_recipes(post_recipes)
        if sweeps:
            w.write_sweeps(sweeps)
        if carry_result_from is not None:
            w.carry_result(carry_result_from)
        if result is not None:
            job_id, chunks = result
            w.stream_result(job_id, chunks)


# ── reading ───────────────────────────────────────────────────────────


def read_config(path) -> dict:
    """simconfig.json → dict (content is opaque: validation and
    restoration belong to the caller)."""
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(_CONFIG_MEMBER).decode("utf-8"))


def read_state(path) -> dict:
    """state.json → dict; a key set other than the four keys (e.g. a v1
    legacy document) → ValueError."""
    with zipfile.ZipFile(path) as z:
        state = json.loads(z.read(_STATE_MEMBER).decode("utf-8"))
    return _check_state(state)


def result_jobs(path) -> "list[str]":
    """Job ids of all results/ members (returned sorted) — **the entry
    name is the job binding**."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith(_RESULT_PREFIX) and n.endswith(_RESULT_SUFFIX)]
    return sorted(n[len(_RESULT_PREFIX):-len(_RESULT_SUFFIX)] for n in names)


def read_recipes(path) -> dict:
    """``post_analyze.json`` → ``{name: {code, script}}``; member absent
    → ``{}`` (content is opaque, restoration belongs to worker)."""
    with zipfile.ZipFile(path) as z:
        if _RECIPE_MEMBER not in z.namelist():
            return {}
        return json.loads(z.read(_RECIPE_MEMBER).decode("utf-8"))


def read_sweeps(path) -> dict:
    """``sweeps.json`` → ``{name: {parameters, mode, n_gpu,
    on_failure}}``; member absent → ``{}`` (content is opaque,
    restoration belongs to worker)."""
    with zipfile.ZipFile(path) as z:
        if _SWEEPS_MEMBER not in z.namelist():
            return {}
        return json.loads(z.read(_SWEEPS_MEMBER).decode("utf-8"))


def post_names(path) -> "list[str]":
    """Recipe names of all post/ output members (sorted) — the name is the
    key (the opening for the GUI to read npz directly; no read verb in
    v1)."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith(_POST_PREFIX) and n.endswith(_POST_SUFFIX)]
    return sorted(n[len(_POST_PREFIX):-len(_POST_SUFFIX)] for n in names)


def extract_result(path, dest_dir, job_id=None) -> Path:
    """Extract the result member to ``dest_dir/<job>.h5`` and return the
    path ("read cache, not external storage": the lifecycle belongs to
    the caller — delete after reading). Streamed, no whole-payload
    buffering. ``job_id=None`` = with exactly one member, extract that
    one; zero or more than one → ValueError."""
    jobs = result_jobs(path)
    if job_id is None:
        if len(jobs) != 1:
            raise ValueError(
                f"expected exactly one results member, got {jobs}; "
                f"pass job_id explicitly")
        job_id = jobs[0]
    dest = Path(dest_dir) / (job_id + _RESULT_SUFFIX)
    with zipfile.ZipFile(path) as z:
        with z.open(_RESULT_PREFIX + job_id + _RESULT_SUFFIX) as r, \
                open(dest, "wb") as w:
            while True:
                buf = r.read(_COPY_CHUNK)
                if not buf:
                    break
                w.write(buf)
    return dest


# ── terminal-state injection ──────────────────────────────────────────


def inject_result(path, job_id: str, chunks: Iterable[bytes],
                  state: dict) -> None:
    """Inject **one** `results/<job>.h5` member into an existing `.gensim`
    + update state — a single atomic step (temp + rename; semantically the
    model is not rewritten: simconfig and existing members are carried
    over unchanged). Mid-way failure = the original document is
    byte-for-byte intact, temp cleaned up."""
    path = Path(path)
    _check_state(state)
    member = _RESULT_PREFIX + job_id + _RESULT_SUFFIX
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == _STATE_MEMBER \
                        or info.filename == member:
                    continue                  # state rewritten; member not re-injected
                if info.filename.startswith(_RESULT_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:          # result member: streamed carry-over (STORED)
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                        # small member: carried over with its original compression
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            data = json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8")
            dst.writestr(_STATE_MEMBER, data,
                         compress_type=zipfile.ZIP_DEFLATED)
            with dst.open(member, "w") as fh:   # new result: streamed (STORED)
                for chunk in chunks:
                    fh.write(chunk)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def inject_post(path, name: str, data: bytes) -> None:
    """Inject / replace one ``post/<name>.npz`` member in an existing
    `.gensim` — a single atomic step (temp + rename; state untouched,
    simconfig and existing members carried over unchanged;
    **same-name replacement** = re-run semantics). Mid-way failure = the
    original document is byte-for-byte intact, temp cleaned up. npz is
    already compressed content → STORED."""
    path = Path(path)
    member = _POST_PREFIX + name + _POST_SUFFIX
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == member:
                    continue                 # same-name replacement (re-run)
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:          # large member: streamed carry-over (STORED)
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                        # small member: carried over with its original compression
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            dst.writestr(member, data,
                         compress_type=zipfile.ZIP_STORED)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _rewrite_small(path, member: str, obj) -> None:
    """Atomically replace one small JSON member in an existing `.gensim`
    (**replaces only** that member; an empty / falsy ``obj`` removes it).
    All other members (model / state / results / post outputs) are carried
    over unchanged. Shared by the recipe set and the sweep definitions."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == member:
                    continue                 # target member replaced
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:          # large member: streamed carry-over (STORED)
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                        # small member: carried over with its original compression
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            if obj:
                data = json.dumps(obj, ensure_ascii=False,
                                  indent=1).encode("utf-8")
                dst.writestr(member, data,
                             compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_recipes(path, recipes: dict) -> None:
    """Atomically write the recipe set into an existing `.gensim`
    (**replaces only** the ``post_analyze.json`` member;
    ``f.update_post_analyze`` writes into the document immediately).
    An empty dict removes the member."""
    _rewrite_small(path, _RECIPE_MEMBER, recipes)


def write_sweeps(path, sweeps: dict) -> None:
    """Atomically write the sweep definitions into an existing `.gensim`
    (**replaces only** the ``sweeps.json`` member; the param_sweep verbs
    write into the document immediately). An empty dict removes the
    member."""
    _rewrite_small(path, _SWEEPS_MEMBER, sweeps)


def remove_post(path, name: str) -> None:
    """Remove a ``post/<name>.npz`` member from an existing `.gensim` — a
    single atomic step (member absent = no-op). All other members are
    carried over unchanged."""
    path = Path(path)
    member = _POST_PREFIX + name + _POST_SUFFIX
    with zipfile.ZipFile(path) as z:
        if member not in z.namelist():
            return
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == member:
                    continue                 # removal target
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:          # large member: streamed carry-over (STORED)
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                        # small member: carried over with its original compression
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
