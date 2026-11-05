"""genie.core.orchestration.sweep — the Sweep account: the pure read face
over one ``parameter_sweep_<name>/`` directory.

The data is the truth and lives on disk: per-job point documents
``p####.gensim`` (plain ``.gensim`` files — openable with ``f._open``)
plus ``index.json`` (rebuildable, non-authoritative; the GUI reads it
directly). This handle is only the skin: **zero caching** — every call
re-reads the point documents, scratch is cleaned afterwards (the same
discipline as the single-run read verbs).

Cube semantics (v1 G182, unchanged): the read verbs return the **same
containers** as the single-run face (TimeResult / FreqResult /
IndexResult) — the fields arrays just carry the sweep axes as their
**leading** dims. Product = k leading dims (axis order = definition
insertion order; the first axis is the slowest / outermost loop, matching
``itertools.product``); zip = a single point-axis (k=1). Failed points
are NaN slices; the coordinates (time / freqs / …) are taken from the
first point and must agree across points (mismatch → one-line error —
refuse rather than guess).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

from .. import archive
from .. import results as core_results
from ..results import FreqResult, IndexResult, TimeResult

__all__ = ["Sweep", "read_index", "write_index", "doc_name"]

INDEX_NAME = "index.json"
#: index schema version (additive evolution only)
INDEX_VERSION = 1


def doc_name(i: int) -> str:
    """Point document name (1-based, four digits): ``p0001.gensim``."""
    return f"p{i:04d}.gensim"


def read_index(dir) -> dict:
    """``index.json`` → dict; a missing index → one-line error (the
    directory is not a sweep account)."""
    p = Path(dir) / INDEX_NAME
    if not p.is_file():
        raise RuntimeError(
            f"no {INDEX_NAME} in {str(dir)!r} — not a sweep directory")
    return json.loads(p.read_text(encoding="utf-8"))


def write_index(dir, index: dict) -> None:
    """Atomic index rewrite (temp + rename) — one write per job; the
    driver's only index action."""
    dir = Path(dir)
    fd, tmp = tempfile.mkstemp(dir=str(dir), prefix=".index.",
                               suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, dir / INDEX_NAME)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Sweep:
    """Read face over one sweep directory. Built by the worker driver
    (``f.run_param_sweep`` returns a subclass that also carries
    ``run_post_analyze``); this pure base exists so the cube semantics
    are testable with fabricated point documents — no subprocess needed.
    ``jobs`` = ``{i: row}``; a row = an index line (``i / job_id / doc /
    state / ok / error? / cfg``)."""

    def __init__(self, dir):
        self.dir = Path(dir)
        idx = read_index(self.dir)
        self.index = idx                  # raw index (mutating a row's dict
        #  mutates the account; rewrite via write_index)
        self.sweep = idx.get("sweep")
        self.mode = idx.get("mode", "product")
        names = [a[0] for a in (idx.get("axes") or [])]
        lens = [int(a[1]) for a in (idx.get("axes") or [])]
        if self.mode == "zip":
            # one point-axis: the zipped tuple of all axes (k = 1)
            self.axis_names = [",".join(names) or "zip"]
            self.grid = (lens[0],) if lens else ()
        else:
            self.axis_names = names
            self.grid = tuple(lens)
        self.n_points = int(np.prod(self.grid)) if self.grid else 0
        self.jobs = {int(r["i"]): r for r in idx.get("jobs", [])}

    def __repr__(self):
        return (f"<Sweep {self.sweep!r} {self.dir.name}/ "
                f"{self.n_points} points, axes {self.axis_names}>")

    # -- where point i sits on the grid ---------------------------------

    def _pos(self, i: int) -> tuple:
        """Grid position of point i (1-based; C order — last axis varies
        fastest, matching itertools.product with the first axis
        outermost)."""
        if not (1 <= i <= self.n_points):
            raise RuntimeError(
                f"index row i={i} is outside the sweep grid "
                f"({self.n_points} points)")
        return tuple(int(x) for x in np.unravel_index(i - 1, self.grid))

    # -- the three read verbs (same verbs / containers as single-run)----

    def get_time(self, name=None):
        """Time-domain monitors → TimeResult; fields carry the sweep axes
        as leading dims."""
        return self._read("t", name)

    def get_freq(self, name=None):
        """Frequency-domain monitors → FreqResult (same shape rule)."""
        return self._read("f", name)

    def get_index(self, name=None):
        """Index monitors → IndexResult (same shape rule)."""
        return self._read("i", name)

    # -- internals ------------------------------------------------------

    def _read(self, domain, name):
        ok: dict = {}                          # i -> container
        first = None
        scratch = tempfile.mkdtemp(prefix="genie-sweep.")
        try:
            for i in sorted(self.jobs):
                row = self.jobs[i]
                if not row.get("ok"):
                    continue                   # failed point → NaN slice
                path = archive.extract_result(self.dir / doc_name(i),
                                              scratch, row["job_id"])
                data = core_results.open_result(path,
                                                job_id=row["job_id"])
                try:
                    c = (data.get_time(name) if domain == "t" else
                         data.get_freq(name) if domain == "f" else
                         data.get_index(name))
                finally:
                    data.close()               # scratch dir cleared with it
                if first is None:
                    first = c
                else:
                    self._check_agree(first, c, i)
                ok[i] = c
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if first is None:
            raise RuntimeError(
                f"all {self.n_points} points of this sweep failed — "
                f"no results to read (see index.json)")
        fields = {key: self._stack(first, ok, key) for key in first.fields}
        return self._remake(first, fields)

    def _check_agree(self, first, c, i: int) -> None:
        """Coordinates / field sets / shapes must agree across points."""
        for attr in ("time", "freqs", "x", "y", "z"):
            a, b = getattr(first, attr, None), getattr(c, attr, None)
            if a is None and b is None:
                continue
            if a is None or b is None or not np.array_equal(a, b):
                raise RuntimeError(
                    f"point {i} disagrees with the first point on "
                    f"{attr} — coordinates must agree across sweep "
                    f"points (refuse rather than guess)")
        if set(first.fields) != set(c.fields):
            raise RuntimeError(
                f"point {i} field set differs from the first point "
                f"({sorted(c.fields)} vs {sorted(first.fields)})")
        for key, arr in c.fields.items():
            if arr.shape != first.fields[key].shape:
                raise RuntimeError(
                    f"point {i} field {key!r} shape {arr.shape} differs "
                    f"from the first point "
                    f"({first.fields[key].shape})")

    def _stack(self, first, ok, key):
        """Pure stacking: grid + per-point shape; failed points NaN."""
        base = first.fields[key]
        dtype = np.result_type(base,
                               *(c.fields[key] for c in ok.values()))
        if self.n_points > len(ok) and not (
                np.issubdtype(dtype, np.floating)
                or np.issubdtype(dtype, np.complexfloating)):
            dtype = np.float64          # NaN needs a float-able slot
        out = np.full(self.grid + np.shape(base), np.nan, dtype=dtype)
        for i, c in ok.items():
            out[self._pos(i)] = c.fields[key]
        return out

    def _remake(self, first, fields):
        """Same container class, first point's coordinates / axes."""
        if isinstance(first, TimeResult):
            return TimeResult(first.name, first.time, fields, None,
                              x=first.x, y=first.y, z=first.z)
        if isinstance(first, FreqResult):
            return FreqResult(first.name, first.freqs, fields, None,
                              x=first.x, y=first.y, z=first.z)
        return IndexResult(first.name, fields, None,
                           x=first.x, y=first.y, z=first.z)
