"""Authoring defaults as a pure function (L-62, 2026-11-28).

The document stores only explicit choices.  Defaults are applied when a
solver config becomes a **view** or a **submit payload**:

    to_document  -> raw (explicit choices only)
    to_view      -> effective (this module)
    to_config    -> effective (this module)

No creation seeding, no draft inheritance, no from_config migration, no
GUI seeds.  A default is "the value used when the document does not say";
it is never written back into the document.

Conditional defaults (boundary kind, mode axis) are resolved here as
branches: the effective output contains only the fields that apply to the
chosen kind/axis, so no setter has to clear stale keys.
"""
from __future__ import annotations

import copy
from typing import Any

#: speed of light (m/s) — the default band is anchored at 1.55 µm
C0 = 299792458.0

DEFAULT_RANGE = (-1.0e-6, 1.0e-6)
DEFAULT_SPACING = 5.0e-8
DEFAULT_TOTAL_TIME = 1.0e-12
DEFAULT_CFL = 0.99
DEFAULT_AUTO_SHUTOFF = 1.0e-6
DEFAULT_WAVELENGTH = 1.55e-6
DEFAULT_NMODES = 1

DEFAULT_SOURCE_FREQ = C0 / 1.55e-6          # 1.55 µm
DEFAULT_SOURCE_BW = 20.0e12
DEFAULT_MON_CENTER = 300.0e12
DEFAULT_MON_BW = 120.0e12
DEFAULT_MON_N = 200

FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
MODE_TRANSVERSE = {"x": ("y", "z"), "y": ("x", "z"), "z": ("x", "y")}

BOUNDARY_PML = {"layers": 12, "kappa_max": 1.0, "order": 3}
BOUNDARY_BLOCH = {"k": [0.0, 0.0, 0.0], "from_source": False}

SOFT_POLARIZATION = [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0]]

#: Global source defaults (document-level singleton + the source ids that
#: inherit it). The document stores only explicit fields; the resolver
#: fills the rest and expands ``use_global`` into each source's payload.
GLOBAL_SOURCE_DEFAULTS = {
    "freq": C0 / 1.55e-6,
    "bandwidth": 20.0e12,
    "delay": 0.0,
    "phase": 0.0,
    "amplitude": 1.0,
    "sources": [],
}


def _set(d: dict, key: str, value: Any) -> None:
    """Fill only an absent/None slot; explicit 0/False/"" values win."""
    if d.get(key) is None:
        d[key] = copy.deepcopy(value)


def _boundary_face(raw: Any) -> dict:
    """One boundary face: only the fields that apply to its kind."""
    raw = dict(raw or {})
    kind = raw.get("kind") or "pec"
    out: dict[str, Any] = {"kind": kind}
    if kind == "pml":
        for k, v in BOUNDARY_PML.items():
            out[k] = raw[k] if raw.get(k) is not None else v
        if raw.get("reflection") is not None:
            out["reflection"] = raw["reflection"]
    elif kind == "bloch":
        out["k"] = copy.deepcopy(
            raw["k"] if raw.get("k") is not None else BOUNDARY_BLOCH["k"])
        out["from_source"] = bool(raw.get("from_source", False))
    return out


def _domain(d: dict) -> None:
    for a in "xyz":
        # a spacing table defines the base grid itself; its window must be
        # explicit (filling a default window could not cover the table)
        if not d.get(f"{a}_spacing"):
            _set(d, f"{a}_range", DEFAULT_RANGE)
    _set(d, "total_time", DEFAULT_TOTAL_TIME)
    _set(d, "cfl", DEFAULT_CFL)
    _set(d, "auto_shutoff", DEFAULT_AUTO_SHUTOFF)
    # base grid: only when this axis has no spacing table (Q31 mutual exclusion)
    for a in "xyz":
        if not d.get(f"{a}_spacing"):
            _set(d, f"d{a}", DEFAULT_SPACING)
    raw = d.get("boundary") or {}
    d["boundary"] = {f: _boundary_face(raw.get(f)) for f in FACES}


def _mesh(m: dict, domain: dict) -> None:
    for a in "xyz":
        _set(m, f"{a}_range",
             domain.get(f"{a}_range") or DEFAULT_RANGE)
        _set(m, f"factor_{a}", 1)
    _set(m, "growth_rate", 1.2)


def _source(s: dict, domain: dict | None = None) -> None:
    _set(s, "freq", DEFAULT_SOURCE_FREQ)
    _set(s, "bandwidth", DEFAULT_SOURCE_BW)
    _set(s, "delay", 0.0)
    _set(s, "phase", 0.0)
    t = s.get("type")
    if t == "soft_source":
        _set(s, "amplitude", 1.0)
        _set(s, "center", [0.0, 0.0, 0.0])
        _set(s, "polarization", SOFT_POLARIZATION)
        _set(s, "kind", "electric")
    elif t in ("plane_wave", "gaussian_beam"):
        _set(s, "amplitude", 1.0)
        _set(s, "direction", "+")
        _set(s, "propagation_axis", "z")
        _set(s, "theta", 0.0)
        _set(s, "phi", 0.0)
        _set(s, "psi", 0.0)
        # Domain range 默认（2026-11-28 站长拍）：横截面 = 域范围
        # （mesh 同款先例——窗口跟域走）；注入面放传播轴的域 min 端
        # （"从这头打进去"；center 是整份 Vec3，无逐轴槽）。未写才补；
        # 域该轴无 range（spacing 表轴）则该轴留空 = 全跨度。
        if domain is not None and s.get("center") is None \
                and s.get("size") is None:
            axis = s.get("propagation_axis") or "z"
            center, size = [], []
            for a in ("x", "y", "z"):
                r = domain.get(f"{a}_range")
                if a == axis:
                    center.append(r[0] if r else 0.0)
                    size.append(None)
                else:
                    center.append((r[0] + r[1]) / 2 if r else 0.0)
                    size.append(abs(r[1] - r[0]) if r else None)
            s["center"] = center
            s["size"] = size
    elif t == "mode":
        _set(s, "amplitude", 1.0)
        _set(s, "rotate_angle", 0.0)
        _set(s, "rotate_radius", 0.0)
        sel = s.get("mode_selection")
        if not isinstance(sel, dict):
            s["mode_selection"] = {"n_modes": 1, "index": 0}
        else:
            _set(sel, "n_modes", 1)
            _set(sel, "index", 0)


def _monitor(m: dict) -> None:
    _set(m, "center", [0.0, 0.0, 0.0])
    ds = m.get("down_sample")
    if isinstance(ds, dict):
        _set(ds, "stride", 1)
    t = m.get("type")
    if t == "time":
        _set(m, "size", [0.0, 0.0, 0.0])
        _set(m, "field", "all")
    elif t == "freq":
        _set(m, "size", [0.0, 0.0, 0.0])
        _set(m, "field", "all")
        freq = m.get("freq")
        if not isinstance(freq, dict):
            m["freq"] = {"center": DEFAULT_MON_CENTER,
                         "bw": DEFAULT_MON_BW, "n": DEFAULT_MON_N}
        else:
            _set(freq, "center", DEFAULT_MON_CENTER)
            _set(freq, "bw", DEFAULT_MON_BW)
            _set(freq, "n", DEFAULT_MON_N)
    elif t == "index":
        # size=(None,None,None) is the full-domain default; keep it absent
        _set(m, "field", "both")


def _fdtd(conf: dict) -> None:
    conf["domain"] = conf.get("domain") or {}
    _domain(conf["domain"])
    domain = conf["domain"]
    conf["mesh"] = [dict(m) for m in (conf.get("mesh") or [])]
    for m in conf["mesh"]:
        _mesh(m, domain)
    conf["source"] = [dict(s) for s in (conf.get("source") or [])]
    for s in conf["source"]:
        _source(s, domain)
    conf["monitor"] = [dict(m) for m in (conf.get("monitor") or [])]
    for m in conf["monitor"]:
        _monitor(m)


def _mode(conf: dict) -> None:
    d = conf.get("domain") or {}
    conf["domain"] = d
    axis = d.get("axis") or "z"
    d["axis"] = axis
    _set(d, "position", 0.0)
    t1, t2 = MODE_TRANSVERSE[axis]
    for a in (t1, t2):
        # coords / a spacing table define the window themselves; only fill
        # the uniform default when neither is present
        if not d.get(f"{a}_coords") and not d.get(f"{a}_spacing"):
            _set(d, f"{a}_range", DEFAULT_RANGE)
            _set(d, f"d{a}", DEFAULT_SPACING)
    raw = conf.get("boundary") or {}
    edges = [f"{a}_{side}" for a in (t1, t2) for side in ("min", "max")]
    conf["boundary"] = {e: _boundary_face(raw.get(e)) for e in edges}
    _set(conf, "wavelength", DEFAULT_WAVELENGTH)
    _set(conf, "n_modes", DEFAULT_NMODES)
    # Switching the propagation axis can strand a rotation about the new
    # normal (invalid: an in-plane spin). The resolver drops it; the
    # document keeps the explicit choice untouched.
    if conf.get("rotate_axis") == axis:
        conf["rotate_axis"] = None
        conf["rotate_angle"] = 0.0
        conf["rotate_radius"] = 0.0
    _set(conf, "rotate_angle", 0.0)
    _set(conf, "rotate_radius", 0.0)
    conf["mesh"] = [dict(m) for m in (conf.get("mesh") or [])]
    for m in conf["mesh"]:
        for a in (t1, t2):
            _set(m, f"{a}_range",
                 d.get(f"{a}_range") or DEFAULT_RANGE)
            _set(m, f"factor_{a}", 1)
        _set(m, "growth_rate", 1.2)


def _apply_global(conf: dict, global_source: dict | None,
                  *, strip: bool) -> None:
    """Expand the document-level global source into the referencing sources.

    Membership is by source id (stable across renames); the document keeps
    each source's own fields untouched, so turning ``use_global`` off
    restores the authored values. The payload strips the authoring-only
    keys; the view keeps them.
    """
    if not isinstance(global_source, dict):
        global_source = {}
    ids = set(global_source.get("sources") or [])
    for s in conf.get("source") or []:
        use = s.get("id") in ids
        if use:
            for k in ("freq", "bandwidth", "delay", "phase", "amplitude"):
                s[k] = global_source.get(k, GLOBAL_SOURCE_DEFAULTS[k])
        if strip:
            s.pop("use_global", None)
        else:
            s["use_global"] = use


def effective_config(conf: dict, kind: str,
                     *, global_source: dict | None = None,
                     strip_authoring: bool = False) -> dict:
    """Return a new config with the authoring defaults filled in."""
    out = copy.deepcopy(conf or {})
    if kind == "mode":
        _mode(out)
    else:
        _fdtd(out)
        _apply_global(out, global_source, strip=strip_authoring)
    return out


def effective_model(model: dict, *, with_global_source: bool = False) -> dict:
    """Return a new model section with the effective global-source block."""
    out = copy.deepcopy(model or {})
    if with_global_source:
        gs = out.get("global_source")
        if not isinstance(gs, dict):
            gs = {}
            out["global_source"] = gs
        for k, v in GLOBAL_SOURCE_DEFAULTS.items():
            _set(gs, k, v)
    return out
