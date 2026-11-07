"""genie.worker.plot — result-view data for the GUI (Q4.5 public read verbs).

Two pure functions over ``core.results`` (SimData):

- ``build_meta(data)``  → the structure list (monitors / fields / axes lengths
  + root attrs), the shape the GUI result panel's builder needs;
- ``build_series(data, mon, field=None, axes=None, slices=None)`` → one
  result-view series: full arrays (station decision 2026-11-06: no
  downsampling), 1D peak-column for time/freq, 2D max-projection grid for
  index monitors (optional slice pin on the remaining axis).

Zero IO, zero physics: every number comes from core/results; this module only
shapes it for display. NaN/inf become ``None`` (strict JSON on the wire).
"""
from __future__ import annotations

import warnings

import numpy as np

__all__ = ["build_meta", "build_series"]

_AXES = ("x", "y", "z")


# ── helpers ─────────────────────────────────────────────────────────────

def _domain_of(data, name):
    """Monitor name → (domain word, Result object); tries t → f → i."""
    for domain, getter in (("t", data.get_time), ("f", data.get_freq),
                           ("i", data.get_index)):
        try:
            return domain, getter(name)
        except Exception:                        # noqa: BLE001 — wrong domain
            continue
    raise RuntimeError(f"monitor {name!r} not found in this result")


def _axis_lengths(res) -> dict:
    out = {}
    for k in _AXES:
        v = getattr(res, k, None)
        if v is not None:
            out[k] = int(np.size(v))
    if getattr(res, "time", None) is not None:
        out["time"] = int(np.size(res.time))
    if getattr(res, "freqs", None) is not None:
        out["freqs"] = int(np.size(res.freqs))
    return out


def _clean(v):
    """numpy / NaN → strict-JSON-safe values (NaN/inf → None)."""
    if isinstance(v, dict):
        return {k: _clean(i) for k, i in v.items()}
    if isinstance(v, np.ndarray):
        return _clean(v.tolist())
    if isinstance(v, list):
        return [_clean(i) for i in v]
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _peak(y) -> float:
    a = np.asarray(y, dtype=float)
    if not a.size:
        return 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmax(np.abs(a))
    return 0.0 if m != m else float(m)


def _fold(arr) -> np.ndarray:
    """Field array → y over its leading (x) axis: magnitude + peak fold.

    A point monitor's field is already 1D (``(n_freqs, 1)`` collapses to
    ``(n_freqs,)``); a line/volume monitor keeps the leading samples and
    folds the remaining (position) axes with nanmax — the peak-column
    semantics the GUI labels as ``|field|``.
    """
    a = np.asarray(arr)
    if np.iscomplexobj(a):
        a = np.abs(a)
    a = np.asarray(a, dtype=float)
    if a.ndim <= 1:
        return a
    other = tuple(i for i in range(a.ndim) if i != 0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmax(a, axis=other)


def _pick_field(res, field):
    fields = sorted(res.fields)
    if not fields:
        raise RuntimeError(f"monitor {res.name!r} has no fields")
    if field:
        if field not in res.fields:
            raise KeyError(f"field {field!r} not in monitor {res.name!r} "
                           f"(fields: {', '.join(fields)})")
        return field
    best, best_peak = fields[0], -1.0
    for f in fields:
        pk = _peak(_fold(res.fields[f]))
        if pk > best_peak:
            best, best_peak = f, pk
    return best


def _slice_cells(vals, coords, slices):
    """Pin the remaining axis (index point cloud) at a slice index."""
    mask = np.ones(len(vals), dtype=bool)
    for k, idx in (slices or {}).items():
        if k not in coords:
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            raise ValueError(f"slice {k!r} must be an integer index") from None
        uniq = np.unique(coords[k])
        if idx < 0 or idx >= len(uniq):
            raise ValueError(f"slice {k}={idx} out of range (0..{len(uniq) - 1})")
        mask &= coords[k] == uniq[idx]
    return vals[mask], {k: v[mask] for k, v in coords.items()}


# ── public builders ─────────────────────────────────────────────────────

def build_meta(data) -> dict:
    """The current result's structure list (monitors, fields, axis lengths
    + executed_steps / dt / grid_shape)."""
    attrs = dict(data.attrs or {})
    monitors = []
    for name in data.monitors:
        try:
            domain, res = _domain_of(data, name)
        except Exception:                        # noqa: BLE001 — skip a bad group
            continue
        monitors.append({
            "name": name,
            "domain": domain,
            "fields": sorted(res.fields),
            "axes": sorted(_axis_lengths(res)),      # v1 shape: names list
            "axes_len": _axis_lengths(res),          # v1 shape: {axis: length}
        })
    try:
        steps = int(attrs.get("executed_steps", 0) or 0)
    except (TypeError, ValueError):
        steps = 0
    try:
        dt = float(attrs.get("dt", 0) or 0)
    except (TypeError, ValueError):
        dt = 0.0
    try:
        shape = attrs.get("grid_shape")
        shape = [] if shape is None else [int(v) for v in shape]
    except (TypeError, ValueError):
        shape = []
    return {
        "ok": True,
        "job_id": getattr(data, "job_id", ""),
        "executed_steps": steps,
        "dt": dt,
        "grid_shape": shape,
        "monitors": monitors,
    }


def build_series(data, mon: str, field: str | None = None,
                 axes=None, slices=None) -> dict:
    """One result-view series: 1D (time / freq / index peak-column) or a
    2D max-projection grid (index monitors, ``axes`` = two of x/y/z)."""
    domain, res = _domain_of(data, mon)
    field = _pick_field(res, field)
    if domain in ("t", "f"):
        x = res.time if domain == "t" else res.freqs
        xs = np.asarray(x, dtype=float)
        y = _fold(res.fields[field])
        n = int(min(len(xs), len(y)))
        return _clean({
            "ok": True, "mon": mon, "field": field, "dim": 1,
            "x_label": "time" if domain == "t" else "freqs",
            "x": xs[:n], "y": y[:n],
            "peak": _peak(y[:n]), "n_total": int(len(y)),
            "tag": "",
        })

    # index monitor: per-cell point cloud + field values
    vals = np.asarray(res.fields[field], dtype=float)
    coords = {k: np.asarray(getattr(res, k))
              for k in _AXES if getattr(res, k, None) is not None}
    if slices:
        vals, coords = _slice_cells(vals, coords, slices)
    ax_list = [a for a in (axes or []) if a in coords]
    if len(ax_list) == 2:
        a, b = ax_list
        xs, ys = np.unique(coords[a]), np.unique(coords[b])
        ix = np.searchsorted(xs, coords[a])
        iy = np.searchsorted(ys, coords[b])
        z = np.full((len(ys), len(xs)), -np.inf)
        np.maximum.at(z, (iy, ix), vals)          # max-projection over the rest
        z = np.where(np.isfinite(z), z, np.nan)
        return _clean({
            "ok": True, "mon": mon, "field": field, "dim": 2,
            "x_label": a, "y_label": b,
            "x": xs, "y": ys, "z": z,
            "peak": _peak(vals), "n_total": int(len(vals)),
            "tag": "max-projection" + (" · slice" if slices else ""),
        })
    if len(ax_list) > 2:
        raise ValueError("axes accepts at most two names (x/y/z)")
    a = ax_list[0] if ax_list else next(iter(coords), None)
    if a is None:
        raise RuntimeError(f"index monitor {mon!r} has no coordinates")
    xs = np.unique(coords[a])
    ix = np.searchsorted(xs, coords[a])
    y = np.full(len(xs), -np.inf)
    np.maximum.at(y, ix, vals)                    # peak-column projection
    y = np.where(np.isfinite(y), y, np.nan)
    return _clean({
        "ok": True, "mon": mon, "field": field, "dim": 1,
        "x_label": a, "x": xs, "y": y,
        "peak": _peak(y), "n_total": int(len(vals)),
        "tag": "max-projection" + (" · slice" if slices else ""),
    })
