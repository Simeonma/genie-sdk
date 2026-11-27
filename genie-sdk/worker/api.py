"""genie.worker.api — the unified user surface (Q25 / docs/API-STYLE.md).

One address grammar, one write verb, one read verb:

    genie.add_parameter / add_material / add_geometry / add_attribute /
    add_analysis / add_sweep / add_solver / <solver>.add_source /
    add_monitor / add_mesh
    genie.set_named(addr, **fields)      # the ONLY write channel
    genie.get_named(addr[, field])       # model fields
    genie.describe(addr)                 # field table
    genie.remove(addr)
    genie.fit_material(name)             # explicit library fit
    genie.run_fdtd() / run_mode(name) / build_fdtd() / run_sweep(name) / run_analysis(name)
    genie.get_result(mon[, field])       # result data
    genie.save(path=None) / open(path)

Addresses are ``<family>.<name>`` for top-level objects, ``<solver>`` for a
solver itself, ``<solver>.<name>`` for its children (sources, monitors,
mesh objects; one shared namespace per solver), and ``global_source`` for
the document-level global source singleton (Q50). Q30: the base grid
dx/dy/dz lives on the solver's domain; ``add_mesh`` creates named
refinement boxes.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

import numpy as np

from genie import ipc
from genie.core import material as core_material
from genie.core.model import Model
from genie.core.model.draft import digest
from genie.net import GenieJobError

from genie.core import archive

from . import chain, post, sweep as sweep_mod
from .chain import (ROW_CANCELED, ROW_DONE, ROW_FAILED,
                    ROW_SUBMIT_ONE)
from genie.net import TERMINAL_OK

#: one address segment: letters/digits/underscore/hyphen, must not contain '.'
_SEG = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

#: top-level families of the user surface
_FAMILIES = ("parameter", "material", "geometry", "attribute", "analysis",
             "sweep")

#: material types accepted by ``add_material`` (wire type in parentheses)
_MAT_TYPES = {"const": "const", "lorentz": "lorentz", "library": "lorentz"}

#: attribute types accepted by ``add_attribute`` (one path: named frame)
_ATTR_TYPES = {"matrix_frame"}

#: geometry types → wire kinds (same name on both sides)
_GEO_TYPES = {"box", "sphere", "cylinder", "ngon_z", "polygon_z",
              "ellipse_z", "ring_z"}

#: source / monitor types → wire kinds
_SRC_TYPES = {"plane_wave", "soft_source", "gaussian_beam", "mode"}
_MON_TYPES = {"time", "freq", "index"}

#: solver-flattened fields: user name → (section, wire field). Q30: the
#: base grid dx/dy/dz lives on domain; the caps (max_refinement /
#: min_cells) are deleted from the language. Q31: per-axis spacing
#: tables (mutually exclusive with dx — auto_grid writes them).
_SOLVER_FIELDS = {
    "x_range": ("domain", "x_range"), "y_range": ("domain", "y_range"),
    "z_range": ("domain", "z_range"), "total_time": ("domain", "total_time"),
    "cfl": ("domain", "cfl"), "boundary": ("domain", "boundary"),
    "dx": ("domain", "dx"), "dy": ("domain", "dy"), "dz": ("domain", "dz"),
    "x_spacing": ("domain", "x_spacing"),
    "y_spacing": ("domain", "y_spacing"),
    "z_spacing": ("domain", "z_spacing"),
}

#: temporal source/monitor aliases (user name → wire field)
_TEMPORAL = {"freq_center": "freq", "freq_bw": "bandwidth"}
#: monitor band trio (merged into the wire ``freq`` dict)
_MON_BAND = {"freq_center": "center", "freq_bw": "bw", "freq_n": "n"}

_SEVERITY = "error"


class _Target:
    """One resolved address: kind + the thing + how to read/write its fields."""

    __slots__ = ("kind", "family", "name", "obj", "solver")

    def __init__(self, kind, family, name, obj=None, solver=None):
        self.kind = kind          # parameter|material|geometry|analysis|sweep|
        #                           solver|source|monitor|mesh
        self.family = family      # address prefix as typed (may be None)
        self.name = name
        self.obj = obj
        self.solver = solver

    def addr(self):
        if self.kind == "solver":
            return self.name
        if self.family:
            return f"{self.family}.{self.name}"
        return self.name


class Surface:
    """The user-facing surface bound to one model + session env."""

    __slots__ = ("_model", "_env", "_state")

    def __init__(self, model, env):
        state = getattr(env, "api_state", None)
        if state is None:
            state = {"mat_meta": {}}
            env.api_state = state
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_state", state)

    # state kept on the exec env (a fresh Genie is bound on every exec)
    @property
    def _mat_meta(self):
        return self._state["mat_meta"]

    # ══════════════════════════════════════════════════════════════
    # address parsing / resolution
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _check_name(name, what="object"):
        if not isinstance(name, str) or not _SEG.fullmatch(name):
            raise ValueError(
                f"{what} name {name!r} is invalid: letters / digits / '_' / '-' "
                f"only, must start with a letter, no '.' (the address "
                f"separator)")
        if name == "global_source":
            raise ValueError(
                "'global_source' is reserved for the document-level global "
                "source singleton")

    def _family_members(self, family):
        """family → {name: obj/tuple} (live view)."""
        m = self._model
        if family == "parameter":
            return {k: ("parameter", k) for k in m.parameters}
        if family == "material":
            return {it.name: it for it in m._materials}
        if family == "attribute":
            return {it.name: it for it in m._attributes}
        if family == "geometry":
            return {it.name: it for it in m._geometry}
        if family == "analysis":
            return {k: {"script": (v or {}).get("script")}
                    for k, v in self._env.post_recipes.items()}
        if family == "sweep":
            return {k: v for k, v in self._env.sweeps.items()}
        return {}

    def _solver_children(self, solver):
        """solver → {name: _Target} for sources / monitors / mesh objects."""
        env = self._model.envelope(solver)
        out = {}
        src = getattr(env, "_source", None)
        mon = getattr(env, "_monitor", None)
        if src is not None:
            for it in src:
                out[it.name] = _Target("source", solver, it.name, it, solver)
        if mon is not None:
            for it in mon:
                out[it.name] = _Target("monitor", solver, it.name, it, solver)
        meshes = getattr(env, "meshes", None)     # fdtd + mode (Q30)
        if meshes is not None:
            for it in meshes._items:
                out[it.name] = _Target("mesh", solver, it.name, it, solver)
        return out

    def _all_candidates(self):
        """{short name: [_Target, ...]} across every family + solver child."""
        cands: dict = {}

        def push(t):
            cands.setdefault(t.name, []).append(t)

        for fam in _FAMILIES:
            for nm, obj in self._family_members(fam).items():
                push(_Target(fam, fam, nm, obj))
        for sname in self._model._solvers:
            push(_Target("solver", None, sname, self._model.envelope(sname)))
            for nm, t in self._solver_children(sname).items():
                push(t)
        return cands

    def _resolve(self, addr):
        if not isinstance(addr, str) or not addr.strip():
            raise ValueError(f"address must be a non-empty string, got {addr!r}")
        addr = addr.strip()
        if "." in addr:
            fam, _, name = addr.partition(".")
            if fam in self._model._solvers:                 # solver child
                child = self._solver_children(fam).get(name)
                if child is None:
                    raise ValueError(
                        f"address {addr!r}: {fam} has no object named {name!r} "
                        f"(existing: {self._names_hint(self._solver_children(fam))})")
                return child
            if fam not in _FAMILIES:
                raise ValueError(
                    f"address {addr!r}: unknown family {fam!r} "
                    f"(expected one of: {', '.join(_FAMILIES)}; or a solver name)")
            members = self._family_members(fam)
            if name not in members:
                raise ValueError(
                    f"address {addr!r}: {fam} has no object named {name!r} "
                    f"(existing: {self._names_hint(members)})")
            return _Target(fam, fam, name, members[name])
        if addr == "global_source":                         # 全局 source 设置（文档级）
            return _Target("global_source", None, addr, self._model)
        if addr in self._model._solvers:                    # the solver itself
            return _Target("solver", None, addr, self._model.envelope(addr))
        cands = self._all_candidates().get(addr, [])
        if not cands:
            raise ValueError(f"address {addr!r}: no object with that name")
        if len(cands) > 1:
            alts = ", ".join(t.addr() for t in cands)
            raise ValueError(
                f"address {addr!r} is ambiguous ({len(cands)} objects); "
                f"use the full form: {alts}")
        return cands[0]

    @staticmethod
    def _names_hint(members):
        names = sorted(members)[:12]
        more = "" if len(members) <= 12 else ", …"
        return ", ".join(names) + more if names else "none"

    # ══════════════════════════════════════════════════════════════
    # creation verbs (identity + type only)
    # ══════════════════════════════════════════════════════════════

    def _ensure_free(self, name, family):
        self._check_name(name)
        if name in self._family_members(family):
            raise ValueError(f"{family}.{name} already exists")

    def add_parameter(self, name):
        self._ensure_free(name, "parameter")
        self._model.parameters[name] = None
        return f"parameter.{name}"

    def add_material(self, name, type, *, library=None):  # noqa: A002
        self._ensure_free(name, "material")
        if type not in _MAT_TYPES:
            raise ValueError(
                f"material type {type!r} is invalid (expected: const | lorentz "
                f"| library)")
        if type == "library":
            if not isinstance(library, str) or not library:
                raise ValueError(
                    f"add_material(type='library') needs library=<name>, e.g. "
                    f"library='Si'")
            if library not in core_material.list_library_materials():
                raise ValueError(
                    f"library {library!r} is not in the local n-k library "
                    f"(use genie.list_library_materials())")
        self._model.materials._add(_MAT_TYPES[type], name)
        if type == "library":
            self._mat_meta[name] = {"library": library, "band": None,
                                    "tolerance": 0.1, "max_poles": 10,
                                    "imaginary_weight": 1.0,
                                    "make_passive": False, "quality": None}
        return f"material.{name}"

    def add_geometry(self, name, type):  # noqa: A002
        self._ensure_free(name, "geometry")
        if type not in _GEO_TYPES:
            raise ValueError(
                f"geometry type {type!r} is invalid (expected: "
                f"{', '.join(sorted(_GEO_TYPES))})")
        self._model.geometry._add(type, name)
        return f"geometry.{name}"

    def add_attribute(self, name, type="matrix_frame"):  # noqa: A002
        """Named, reusable object attribute (today: ``matrix_frame``)."""
        self._ensure_free(name, "attribute")
        if type not in _ATTR_TYPES:
            raise ValueError(
                f"attribute type {type!r} is invalid (expected: "
                f"{', '.join(sorted(_ATTR_TYPES))})")
        self._model.attributes._add(type, name)
        return f"attribute.{name}"

    def add_analysis(self, name=None):
        if name is None:                       # GUI 便利：分析/分析_2 …
            taken = self._env.post_recipes
            name = "analysis"
            i = 1
            while name in taken:
                i += 1
                name = f"analysis_{i}"
        self._ensure_free(name, "analysis")
        post.add_verb(self._env, name)          # creates the empty recipe
        return f"analysis.{name}"

    def add_sweep(self, name):
        self._ensure_free(name, "sweep")
        self._env.sweeps[name] = {"parameters": {}, "mode": "product",
                                  "n_gpu": None, "on_failure": "continue"}
        sweep_mod.sync_sweeps(self._env)
        return f"sweep.{name}"

    def add_solver(self, name=None, type=None):  # noqa: A002
        type = type or name
        if type not in ("fdtd", "mode"):
            raise ValueError(f"solver type {type!r} is invalid (expected: "
                             f"fdtd | mode)")
        if name is None:
            # GUI buttons / 终端便利：省 name = 自动唯一实例名
            # (mode, mode_2, mode_3, ...) — the naming rule lives here,
            # not in the caller.
            taken = self._model._solvers
            name = type
            i = 1
            while name in taken:
                i += 1
                name = f"{type}_{i}"
        self._check_name(name)
        self._model.add_solver(name, type)
        return name

    def solver(self, name):
        """The solver-child creation handle (``genie.fdtd.add_source(...)``)."""
        return _SolverChildren(self, name)

    # ══════════════════════════════════════════════════════════════
    # the single write channel
    # ══════════════════════════════════════════════════════════════

    def set_named(self, addr, node=None, **fields):
        """The one write channel, two granularities: keyword fields, or —
        **material addresses only** — one whole node dict (the output of
        ``assemble_nk`` / a fit pipeline; 整装入住, Q1.4's exception
        reborn in the Q25 API). The node's ``type`` must match the
        declared type; ``name`` is ignored (the address decides)."""
        t = self._resolve(addr)
        if node is not None:
            if fields:
                raise ValueError(
                    f"set_named({addr!r}): pass either a whole node or "
                    f"keyword fields, not both")
            if t.kind != "material":
                raise ValueError(
                    f"set_named({addr!r}, node): whole-node writes are a "
                    f"material exception — use keyword fields here")
            if not isinstance(node, dict):
                raise ValueError(
                    f"set_named({addr!r}, node): expected a node dict, "
                    f"got {type(node).__name__}")
            n = {k: v for k, v in node.items() if k not in ("name",)}
            ntype = n.pop("type", None)
            declared = getattr(t.obj, "type", None)
            if ntype is not None and declared is not None \
                    and ntype != declared:
                raise ValueError(
                    f"set_named({addr!r}, node): node type {ntype!r} does "
                    f"not match the declared type {declared!r}")
            fields = n
        if not fields:
            raise ValueError(f"set_named({addr!r}): no fields given")
        writer = getattr(self, f"_set_{t.kind}")
        writer(t, fields)
        return t.addr()

    # -- per-kind writers ------------------------------------------------

    def _set_parameter(self, t, fields):
        self._only(t, fields, {"value"})
        val = fields["value"]
        if not isinstance(val, (int, float, str)):
            raise ValueError(
                f"{t.addr()}.value: expected a number or an expression "
                f"string, got {type(val).__name__}")
        self._model.parameters[t.name] = val

    def _set_global_source(self, t, fields):
        """The document-level global source singleton (values + member ids)."""
        self._only(t, fields, {"freq", "bandwidth", "delay", "phase",
                               "amplitude", "sources"})
        gs = self._model._global_source
        for k, v in fields.items():
            if k == "sources":
                if not isinstance(v, (list, tuple)):
                    raise ValueError(
                        f"{t.addr()}.sources: expected a list of source ids")
                known = {it.id for s in self._model._solvers.values()
                         for it in getattr(s, "_source", [])}
                ids = [int(x) for x in v]
                bad = [x for x in ids if x not in known]
                if bad:
                    raise ValueError(
                        f"{t.addr()}.sources: unknown source id(s) {bad}")
                gs["sources"] = ids
                continue
            if not isinstance(v, (int, float, str)):
                raise ValueError(
                    f"{t.addr()}.{k}: expected a number or an expression "
                    f"string, got {type(v).__name__}")
            gs[k] = v

    def _set_material(self, t, fields):
        draft = t.obj
        meta = self._mat_meta.get(t.name)
        allowed = set(getattr(type(draft), "model_fields", {}))
        if meta is not None:
            allowed |= {"library", "band", "tolerance", "max_poles",
                        "imaginary_weight", "make_passive"}
        self._only(t, fields, allowed)
        for k, v in fields.items():
            if meta is not None and k in ("library", "band", "tolerance",
                                          "max_poles", "imaginary_weight",
                                          "make_passive"):
                if k == "library":
                    if v not in core_material.list_library_materials():
                        raise ValueError(
                            f"{t.addr()}.library: {v!r} is not in the local "
                            f"n-k library")
                meta[k] = v
                meta["quality"] = None          # invalidate the fit
                continue
            setattr(draft, k, v)

    def _set_geometry(self, t, fields):
        draft = t.obj
        self._only(t, fields, set(type(draft).model_fields))
        for k, v in fields.items():
            if k == "material" and v is not None:
                if v not in self._family_members("material"):
                    raise ValueError(
                        f"{t.addr()}.material: material {v!r} not found "
                        f"(existing: {self._names_hint(self._family_members('material'))})")
            if k == "attribute" and v is not None:
                if v not in self._family_members("attribute"):
                    raise ValueError(
                        f"{t.addr()}.attribute: attribute {v!r} not found "
                        f"(existing: {self._names_hint(self._family_members('attribute'))})")
            setattr(draft, k, v)

    def _set_attribute(self, t, fields):
        draft = t.obj
        self._only(t, fields, set(type(draft).model_fields))
        for k, v in fields.items():
            setattr(draft, k, v)

    def _set_solver(self, t, fields):
        env = t.obj
        for k, v in fields.items():
            if k == "name":
                # 名字是字段（身份是 id，2026-11-25）：改名走同一条写通道
                self._model.rename_solver(t.name, v)
                continue
            if "." in k:                  # nested path (GUI boundary edits)
                node, attr = env, None
                parts = k.split(".")
                for part in parts[:-1]:
                    if isinstance(node, dict):
                        node = node.setdefault(part, {})
                    else:
                        node = getattr(node, part)
                        if node is None:
                            raise ValueError(f"{t.addr()}.{k}: no parent "
                                             f"object {part!r}")
                attr = parts[-1]
                if isinstance(node, dict):
                    node[attr] = v
                else:
                    setattr(node, attr, v)
                continue
            if hasattr(env, "_FIELDS") and k in env._FIELDS:
                # 包络自有字段优先（mode：boundary 在 ModeConfigSpec 顶层，
                # 不在 domain——_SOLVER_FIELDS 的 domain.boundary 是 fdtd
                # 形状，先查包络才不会把 mode 的边界写进 domain）
                setattr(env, k, v)
                continue
            if k in _SOLVER_FIELDS:
                section, wire = _SOLVER_FIELDS[k]
                setattr(getattr(env, section), wire, v)
                continue
            if k in getattr(type(env.domain), "model_fields", {}):
                setattr(env.domain, k, v)
                continue
            raise ValueError(
                f"unknown field {k!r} on solver {t.addr()!r} — fields: "
                f"{', '.join(sorted(_SOLVER_FIELDS))} (or a dotted path)")

    def _set_source(self, t, fields):
        draft = t.obj
        mapping = dict(_TEMPORAL)
        allowed = set(type(draft).model_fields) | set(_TEMPORAL)
        self._only(t, fields, allowed)
        for k, v in fields.items():
            if k in mapping:
                setattr(draft, mapping[k], v)
            else:
                setattr(draft, k, v)

    def _set_monitor(self, t, fields):
        draft = t.obj
        allowed = set(type(draft).model_fields) | set(_MON_BAND)
        self._only(t, fields, allowed)
        band = dict(getattr(draft, "freq", None) or {})
        rest = {}
        for k, v in fields.items():
            if k in _MON_BAND:
                band[_MON_BAND[k]] = v
            else:
                rest[k] = v
        from ..core.model.handle import ModeEnvelope
        for k, v in rest.items():
            if k == "mode_expansion" and isinstance(v, dict) \
                    and "mode_solver" in v:
                ref = dict(v)
                name = ref["mode_solver"]
                env = self._model.envelope(name)
                if not isinstance(env, ModeEnvelope):
                    raise ValueError(
                        f"mode_expansion.mode_solver={name!r} is kind "
                        f"{getattr(env, 'kind', 'fdtd')!r}, not 'mode'")
                extra = set(ref) - {"mode_solver", "freq"}
                if extra:
                    raise ValueError(
                        f"mode_expansion reference has unknown key(s) "
                        f"{sorted(extra)} (only mode_solver / freq)")
                object.__setattr__(draft, "_mode_expansion_ref",
                                   {**ref, "mode_solver_id": env.id})
                continue
            if k == "mode_expansion":
                object.__setattr__(draft, "_mode_expansion_ref", None)
            setattr(draft, k, v)
        if band:
            draft.freq = band

    def _set_mesh(self, t, fields):
        draft = t.obj
        self._only(t, fields, set(type(draft).model_fields))
        for k, v in fields.items():
            setattr(draft, k, v)

    def _set_analysis(self, t, fields):
        self._only(t, fields, {"script", "call"})
        val = fields.get("script")
        if val is not None:
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{t.addr()}.script: expected source text "
                                 f"or a .py file path")
            code, src = val, None
            if "\n" not in val and val.endswith(".py"):
                if not Path(val).is_file():
                    raise ValueError(
                        f"{t.addr()}.script: cannot read analysis script "
                        f"file {val!r}")
                code = Path(val).read_text(encoding="utf-8")
                src = val
            post.set_code(self._env, t.name, code, src)
            self._state.setdefault("sources", {})[t.name] = src
        if "call" in fields:
            post.set_call(self._env, t.name, fields["call"])

    def _set_sweep(self, t, fields):
        self._only(t, fields, {"parameters", "mode", "n_gpu", "on_failure"})
        rec = dict(self._env.sweeps[t.name])
        for k, v in fields.items():
            if k == "parameters":
                if not isinstance(v, dict) or not v:
                    raise ValueError(
                        f"{t.addr()}.parameters: expected a non-empty "
                        f"{{parameter: [values, ...]}} dict")
                missing = [p for p in v if p not in self._model.parameters]
                if missing:
                    raise ValueError(
                        f"{t.addr()}.parameters: unknown parameter(s) "
                        f"{missing} (existing: {self._names_hint(self._family_members('parameter'))})")
            rec[k] = v
        # same rules as the definition path (shape / mode / 512-point cap)
        if rec.get("parameters"):
            rec["parameters"] = sweep_mod.validate_def(
                rec["parameters"], mode=rec.get("mode", "product"),
                on_failure=rec.get("on_failure", "continue"))
        self._env.sweeps[t.name] = rec
        sweep_mod.sync_sweeps(self._env)

    @staticmethod
    def _only(t, fields, allowed):
        bad = [k for k in fields if k not in allowed]
        if bad:
            raise ValueError(
                f"unknown field {bad[0]!r} on {t.kind} {t.addr()!r} — "
                f"fields: {', '.join(sorted(allowed))}")

    # ══════════════════════════════════════════════════════════════
    # read channel
    # ══════════════════════════════════════════════════════════════

    def get_named(self, addr, field=None):
        t = self._resolve(addr)
        snap = getattr(self, f"_get_{t.kind}")(t)
        if field is None:
            return snap
        if field not in snap:
            raise ValueError(
                f"unknown field {field!r} on {t.kind} {t.addr()!r} — "
                f"fields: {', '.join(sorted(snap))}")
        return snap[field]

    def describe(self, addr):
        t = self._resolve(addr)
        snap = self.get_named(addr)
        out = []
        for k in sorted(snap):
            out.append({"field": k, "type": type(snap[k]).__name__,
                        "value": snap[k]})
        return out

    def _get_parameter(self, t):
        return {"value": self._model.parameters[t.name]}

    def _get_global_source(self, t):
        return copy.deepcopy(self._model._global_source)

    def _get_material(self, t):
        snap = {k: getattr(t.obj, k) for k in type(t.obj).model_fields
                if getattr(t.obj, k, None) is not None}
        meta = self._mat_meta.get(t.name)
        if meta is not None:
            snap.update({k: meta[k] for k in
                         ("library", "band", "tolerance", "quality")})
        return snap

    def _get_geometry(self, t):
        return {k: getattr(t.obj, k) for k in type(t.obj).model_fields
                if getattr(t.obj, k, None) is not None}

    def _get_attribute(self, t):
        return {k: getattr(t.obj, k) for k in type(t.obj).model_fields
                if getattr(t.obj, k, None) is not None}

    def _get_solver(self, t):
        env = t.obj
        out = {}
        for user, (section, wire) in _SOLVER_FIELDS.items():
            if hasattr(env, "_FIELDS") and user in env._FIELDS:
                out[user] = getattr(env, user)   # mode：boundary 在包络顶层
                continue
            holder = getattr(env, section)
            # 只读该求解器形状里真有的字段（mode 的 domain 没有
            # total_time/cfl——fdtd 形状的扁平表不能盲读全量）
            if user in getattr(type(holder), "model_fields", {}):
                out[user] = getattr(holder, wire)
        return out

    def _get_source(self, t):
        snap = {k: getattr(t.obj, k) for k in type(t.obj).model_fields
                if getattr(t.obj, k, None) is not None}
        for user, wire in _TEMPORAL.items():
            snap[user] = getattr(t.obj, wire)
            snap.pop(wire, None)
        return snap

    def _get_monitor(self, t):
        draft = t.obj
        snap = {k: getattr(draft, k) for k in type(draft).model_fields
                if getattr(draft, k, None) is not None}
        band = snap.pop("freq", None) or {}
        for user, wire in _MON_BAND.items():
            if wire in band:
                snap[user] = band[wire]
        return snap

    def _get_mesh(self, t):
        return {k: getattr(t.obj, k) for k in type(t.obj).model_fields
                if getattr(t.obj, k, None) is not None}

    def _get_analysis(self, t):
        rec = self._env.post_recipes.get(t.name) or {}
        src = self._state.get("sources", {}).get(t.name)
        return {"script": rec.get("code") or "",
                "call": rec.get("script") or None,
                "source": src}

    def _get_sweep(self, t):
        return dict(self._env.sweeps[t.name])

    def remove(self, addr):
        try:
            t = self._resolve(addr)
        except ValueError:
            # 幂等：已不存在的限定地址 / 求解器名 = 无操作（裸名只有
            # 求解器一种，词形判定与声明规则同源）。
            from ..core.model.handle import _SOLVER_RE
            if isinstance(addr, str) and ("." in addr
                                          or _SOLVER_RE.fullmatch(addr)):
                return addr
            raise
        if t.kind == "parameter":
            self._model.parameters.pop(t.name, None)
        elif t.kind == "material":
            self._model.materials.remove(t.name)
            self._mat_meta.pop(t.name, None)
        elif t.kind == "geometry":
            self._model.geometry.remove(t.name)
        elif t.kind == "attribute":
            self._model.attributes.remove(t.name)
        elif t.kind == "analysis":
            post.delete_verb(self._env, t.name)
        elif t.kind == "sweep":
            self._env.sweeps.pop(t.name, None)
            sweep_mod.sync_sweeps(self._env)
        elif t.kind == "solver":
            # 声明制对称（2026-11-24 站长拍）：删求解器 = 其包络整体
            # 消失（domain/source/monitor/mesh 级联——它们住在包络里）；
            # 被 mode_expansion 引用的拒删（报错列引用者）。
            self._model.remove_solver(t.name)
        else:                                   # solver child
            env = self._model.envelope(t.solver)
            if t.kind == "source":
                env.sources.remove(t.name)
            elif t.kind == "monitor":
                env.monitors.remove(t.name)
            else:                               # mesh object (Q30)
                env.meshes.remove(t.name)
        return t.addr()

    # ══════════════════════════════════════════════════════════════
    # material fitting
    # ══════════════════════════════════════════════════════════════

    def _source_band(self):
        cfg = self._model.to_config()
        lo = hi = None
        for src in (cfg.get("config") or {}).get("source") or []:
            try:
                f0 = float(src.get("freq", 0.0))
                bw = float(src.get("bandwidth", 0.0))
            except (TypeError, ValueError):
                continue
            if f0 <= 0:
                continue
            if bw > 0:
                lo = f0 - bw / 2.0 if lo is None else min(lo, f0 - bw / 2.0)
                hi = f0 + bw / 2.0 if hi is None else max(hi, f0 + bw / 2.0)
        if lo is None or lo <= 0:
            return None
        c0 = core_material.C0_SI
        return (c0 / hi, c0 / lo)

    def fit_material(self, name):
        meta = self._mat_meta.get(name)
        if meta is None:
            raise ValueError(
                f"fit_material({name!r}): not a library material (add it with "
                f"add_material(type='library', library=...))")
        band = meta.get("band") or self._source_band()
        if band is None:
            raise ValueError(
                f"material.{name}: no fit window — set band=(lo, hi) in "
                f"metres, or add a simulation source first")
        lib = meta["library"]
        tables = core_material._load_axes(lib)
        # Q39: three axes, one fit each — the same table object fits once
        # (iso); the report is the constant three-key dict {"x","y","z"}.
        fitted: dict = {}
        report: dict = {}
        for ax in ("x", "y", "z"):
            t = tables[ax]
            if id(t) not in fitted:
                wl, n, k = t
                fitted[id(t)] = core_material.fit_nk(
                    wl, n, k, band=band,
                    tolerance=meta.get("tolerance", 0.1),
                    max_poles=int(meta.get("max_poles", 10)),
                    imaginary_weight=float(meta.get("imaginary_weight", 1.0)),
                    make_passive=bool(meta.get("make_passive", False)))
            fit = fitted[id(t)]
            report[ax] = {
                **{k2: (float(v) if isinstance(v, (int, float)) else v)
                   for k2, v in fit.quality.items()},
                "n_poles": int(fit.n_poles),
                "warnings": list(fit.warnings)}
        node = core_material.assemble_nk(
            {"xx": fitted[id(tables["x"])],
             "yy": fitted[id(tables["y"])],
             "zz": fitted[id(tables["z"])]}, name=name)
        draft = self._family_members("material")[name]
        draft.eps_inf = node.get("eps_inf", 1.0)
        for f in ("eps_inf_xx", "eps_inf_yy", "eps_inf_zz"):
            setattr(draft, f, node.get(f))
        draft.poles = node["poles"]
        meta["band"] = band
        meta["quality"] = report
        return report

    def _fit_pending(self):
        for name, meta in list(self._mat_meta.items()):
            if meta.get("quality") is None:
                self.fit_material(name)

    # ══════════════════════════════════════════════════════════════
    # auto grid (Q31)
    # ══════════════════════════════════════════════════════════════

    def auto_grid(self, n=None, growth=1.2, solver=None):
        """Q31：逐轴 1D 折影（几何 + 材料 n(ω) + 工作频段）→ domain
        段表。fit_material 同族：一次性生成动作，产物是普通数据（三
        张/两张段表写在该求解器的 domain 上），之后可改可删——不搞
        提交期隐藏重算。fdtd 频段 = 源频谱并集；mode = wavelength
        （取最短 λ）；背景恒为真空。``n`` = 每波长格数（缺省
        core.grid.DEFAULT_PPW=14）。库材先懒拟合（与 validate/run 同
        一入口）。返回 {axis: 段数} 便于终端回显。"""
        from ..core import grid as core_grid
        from ..core.model.handle import ModeEnvelope
        self._fit_pending()
        name = solver or self._default_solver_name()
        env = self._model.envelope(name)
        materials = {m.name: m for m in self._model._materials}
        geometries = list(self._model._geometry)
        if isinstance(env, ModeEnvelope):
            from genie_protocol._wire import _TRANSVERSE
            t1, t2 = _TRANSVERSE[env.domain.axis]
            lam = env.wavelength
            lam_min = min(lam) if isinstance(lam, list) else float(lam)
            f_hi = core_grid._C0 / lam_min
            f_lo = f_hi / 2.0          # 单 λ：以 λ 为中心的一个倍频程
            ranges = {}
            for a in (t1, t2):
                rng = getattr(env.domain, f"{a}_range")
                if rng is None:
                    raise ValueError(
                        f"auto_grid: mode domain.{a}_range is required "
                        f"(spacing tables carry no window)")
                ranges[a] = rng
            out = {}
            for a, rng in ranges.items():
                segs = core_grid.project_axis(a, geometries, materials,
                                              f_lo, f_hi)
                table = core_grid.suggest_table(rng, segs, lam_min,
                                                core_grid.DEFAULT_PPW if n is None else n,
                                                growth)
                setattr(env.domain, f"{a}_spacing", table)
                setattr(env.domain, f"d{a}", None)   # 同轴互斥（wire 422）
                out[a] = len(table)
            return out
        # fdtd：三轴，频段 = 源频谱并集（材料拟合同款取法）
        band = self._source_band()
        if band is None:
            raise ValueError(
                "auto_grid: no source band — add a simulation source "
                "first (the grid criterion needs the working wavelength)")
        wl_lo, wl_hi = band
        f_lo, f_hi = core_grid._C0 / wl_hi, core_grid._C0 / wl_lo
        lam_min = wl_lo
        d = env.domain
        ranges = {"x": d.x_range, "y": d.y_range, "z": d.z_range}
        tables = core_grid.auto_grid_tables(
            ranges, geometries, materials, (f_lo, f_hi),
            ppw=core_grid.DEFAULT_PPW if n is None else n, growth=growth)
        for a, table in tables.items():
            setattr(d, f"{a}_spacing", table)
        # 段表与 dx 全局互斥（wire 422）：auto 产物三轴全表 → 清均匀键
        d.dx = None
        d.dy = None
        d.dz = None
        return {a: len(t) for a, t in tables.items()}

    def _default_solver_name(self):
        from ..core.model.handle import DEFAULT_SOLVER
        return DEFAULT_SOLVER

    # ══════════════════════════════════════════════════════════════
    # actions
    # ══════════════════════════════════════════════════════════════

    def run_fdtd(self, *, n_gpu=None):
        self._fit_pending()
        return self._run_solver("fdtd", n_gpu=n_gpu, expect="fdtd")

    def build_fdtd(self, *, n_gpu=None):
        """Q35 build 预演：run.build=true 提交，内核 build 后即收——
        格/dt/步数、实测显存、index 监视器、mode 源 neff 一份报告，
        不跑时间步（计费 run_s=0）。fdtd 专属（mode 无贵的后半段）。"""
        self._fit_pending()
        return self._run_solver("fdtd", n_gpu=n_gpu, build=True,
                                expect="fdtd")

    def _run_solver(self, solver, *, n_gpu=None, build=False,
                    expect="fdtd"):
        # verb↔kind guard (2026-11-24): solver names are document labels;
        # the wire carries the kind — a name declared under another kind
        # must fail loudly here, not silently submit the other solver.
        got = getattr(self._model.envelope(solver), "kind", None)
        if got != expect:
            raise ValueError(
                f"solver {solver!r} is declared as kind {got!r} — this "
                f"verb runs {expect!r} envelopes (the wire payload "
                f"carries the kind, so this would silently submit a "
                f"{got!r} job)")
        env = self._env
        cfg = self._model.to_config(solver)
        chain._validate([cfg])
        jid = chain.submit(env, self._model, cfg, n_gpu=n_gpu, build=build)
        env.emit(ipc.out(ROW_SUBMIT_ONE.format(id=jid)))
        staged = chain.stage(env, self._model, [jid])
        staged_digest = digest(staged)
        outcome = chain.wait(env, [jid])[0]
        if outcome["state"] in TERMINAL_OK:
            env.emit(ipc.out(ROW_DONE.format(id=jid)))
            archive.inject_result(env.doc_path, jid,
                                  env.client().stream_result(jid),
                                  chain.state_for(staged, self._model.last_id,
                                                  [], staged_digest))
            if not build:                       # 预演无物理结果，post 链不跑
                post.run_post(env, jid)
            return jid
        if outcome["state"] == "failed":
            env.emit(ipc.out(ROW_FAILED.format(
                id=jid, detail=outcome.get("error") or outcome["state"])))
        else:                                      # killed / canceled
            env.emit(ipc.out(ROW_CANCELED.format(id=jid)))
        raise GenieJobError(jid, outcome["state"],
                            error=outcome.get("error"))

    def run_mode(self, name="mode", *, n_gpu=None):
        self._fit_pending()
        return self._run_solver(name, n_gpu=n_gpu, expect="mode")

    def run_sweep(self, name):
        self._fit_pending()
        if name not in self._env.sweeps:
            raise ValueError(f"run_sweep({name!r}): no such sweep "
                             f"(existing: {self._names_hint(self._env.sweeps)})")
        return sweep_mod.run_verb(self._env, self._model, name)

    def run_analysis(self, name=None, call=post.UNSET):
        return post.run_verb(self._env, name, call)

    # ══════════════════════════════════════════════════════════════
    # results
    # ══════════════════════════════════════════════════════════════

    def get_result(self, mon, field=None):
        """Read the current result by monitor name (``solver.monitor`` is
        accepted too). The monitor's domain is discovered from the result
        file itself (freq / time / index), so a name the document does not
        know about still reads correctly."""
        want = mon.split(".", 1)[1] if "." in mon else mon
        jid = post.resolve_result(self._env)
        scratch = self._tmpdir()
        try:
            path = archive.extract_result(self._env.doc_path, scratch, jid)
            data = core_results_open(path, jid, scratch)
            try:
                res = None
                for getter in (data.get_freq, data.get_time, data.get_index):
                    try:
                        res = getter(want)
                        break
                    except (KeyError, ValueError, RuntimeError):
                        continue
                if res is None:
                    raise ValueError(
                        f"get_result({mon!r}): no monitor named {want!r} in "
                        f"the current result (existing: "
                        f"{', '.join(sorted(data.monitors)) or 'none'})")
                if field is None:
                    return res
                return res.fields[field]
            finally:
                data.close()
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)

    def _tmpdir(self):
        import tempfile
        return tempfile.mkdtemp(prefix="genie-results.")

    # ══════════════════════════════════════════════════════════════
    # document commands
    # ══════════════════════════════════════════════════════════════

    def _new(self):
        """Blank document (File → New): empty model, no doc path, recipes and
        sweep accounts cleared. Internal bridge (no user-facing verb change);
        the GUI refreshes from the cfg snapshot exec always emits."""
        env = self._env
        env.model = Model()
        env.doc_path = None
        env.post_recipes = {}
        env.sweeps = {}
        return True

    def save(self, path=None):
        env = self._env
        target = path or env.doc_path
        cfg = self._model.to_document()
        archive.save(target, simconfig=cfg,
                     state=chain.state_for(cfg, self._model.last_id, []),
                     carry_result_from=env.doc_path,
                     post_recipes=env.post_recipes, sweeps=env.sweeps)
        env.doc_path = target
        return target

    def open(self, path):
        try:
            cfg = archive.read_config(path)
            state = archive.read_state(path)
        except Exception as e:                  # noqa: BLE001 — one-line error
            raise RuntimeError(f"cannot open {str(path)!r}: {e}") from e
        model = Model.from_config(cfg, next_id=state.get("allocator"))
        env = self._env
        env.model = model
        env.doc_path = str(path)
        try:
            env.post_recipes = archive.read_recipes(path)
        except Exception:                       # noqa: BLE001 — lenient restore
            env.post_recipes = {}
        try:
            env.sweeps = archive.read_sweeps(path)
        except Exception:                       # noqa: BLE001 — lenient restore
            env.sweeps = {}
        object.__setattr__(self, "_model", model)
        env.api_state = {"mat_meta": {}}
        object.__setattr__(self, "_state", env.api_state)
        env._reinject_genie()
        return str(path)

    def __repr__(self):
        return "<genie surface>"


class _ROItem:
    """Read-only view of one model item (attributes are reads only — Q25)."""

    __slots__ = ("_obj", "_label")

    def __init__(self, obj, label):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_label", label)

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __setattr__(self, name, value):
        raise AttributeError(
            f"{self._label}.{name}: attributes are read-only — use "
            f"genie.set_named(<address>, {name}=...)")

    def __repr__(self):
        return f"<{self._label} (read-only)>"


class _ROCollection:
    """Read-only collection view: index/iterate only, no add_*/remove/attr writes."""

    __slots__ = ("_view", "_family")

    def __init__(self, view, family):
        object.__setattr__(self, "_view", view)
        object.__setattr__(self, "_family", family)

    def __getitem__(self, key):
        if isinstance(key, int):
            for it in self._view._items:
                if it.id == key:
                    return _ROItem(it, f"{self._family}[{key}]")
            raise LookupError(f"{self._family}[{key}]: no item with id {key}")
        for it in self._view._items:
            if it.name == key:
                return _ROItem(it, f"{self._family}[{key!r}]")
        raise LookupError(f"{self._family}[{key!r}]: not found")

    def __iter__(self):
        return iter([_ROItem(it, f"{self._family}[{it.name!r}]")
                     for it in self._view._items])

    def __len__(self):
        return len(self._view._items)

    def __setattr__(self, name, value):
        raise AttributeError(
            f"{self._family}: read-only — use genie.add_* / genie.set_named")

    def __getattr__(self, name):
        raise AttributeError(
            f"{self._family}.{name}: not available on the read-only view — "
            f"use genie.add_* / genie.set_named / genie.remove")


class _ROMapping:
    """Read-only view over the parameters table (writes go through set_named)."""

    __slots__ = ("_dict",)

    def __init__(self, d):
        object.__setattr__(self, "_dict", d)

    def __getitem__(self, k):
        return self._dict[k]

    def get(self, k, default=None):
        return self._dict.get(k, default)

    def keys(self):
        return self._dict.keys()

    def items(self):
        return self._dict.items()

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __setitem__(self, k, v):
        raise TypeError("genie.parameters is read-only — use "
                        "genie.set_named('parameter.<name>', value=...)")

    def __setattr__(self, name, value):
        raise AttributeError("genie.parameters is read-only")


class _SolverChildren:
    """``genie.fdtd.add_source / add_monitor / add_mesh`` (mode solvers
    expose ``add_mesh`` too — Q30 symmetric migration)."""

    __slots__ = ("_s", "_name")

    def __init__(self, surface, name):
        self._s = surface
        self._name = name

    def _env(self):
        return self._s._model.envelope(self._name)

    def _free(self, name):
        Surface._check_name(name)
        if name in self._s._solver_children(self._name):
            raise ValueError(f"{self._name}.{name} already exists")

    def add_source(self, name, type):  # noqa: A002
        self._free(name)
        if type not in _SRC_TYPES:
            raise ValueError(f"source type {type!r} is invalid (expected: "
                             f"{', '.join(sorted(_SRC_TYPES))})")
        self._env().sources._add(type, name)
        return f"{self._name}.{name}"

    def add_monitor(self, name, type):  # noqa: A002
        self._free(name)
        if type not in _MON_TYPES:
            raise ValueError(f"monitor type {type!r} is invalid (expected: "
                             f"{', '.join(sorted(_MON_TYPES))})")
        self._env().monitors._add(type, name)
        return f"{self._name}.{name}"

    def add_mesh(self, name):
        """Q30: a named refinement box (factor_x/y/z XOR absolute dx/dy/dz
        per axis; the base grid lives on the solver's domain)."""
        self._free(name)
        self._env().meshes.add_mesh(name)
        return f"{self._name}.{name}"

    def __repr__(self):
        return f"<solver {self._name} children>"


def core_results_open(path, jid, scratch):
    from genie.core import results
    return results.open_result(path, job_id=jid, scratch=scratch)


def _payload(kind, **fields):
    """Wire-shaped pole payload (a value, not a model object)."""
    node = {"kind": kind}
    node.update(fields)
    return node


def Drude(*, omega_p, gamma):
    return _payload("drude", omega_p=omega_p, gamma=gamma)


def Lorentz(*, omega_0, gamma, delta_eps, axis=None):
    node = _payload("lorentz", omega_0=omega_0, gamma=gamma,
                    delta_eps=delta_eps)
    if axis is not None:
        if axis not in ("x", "y", "z"):
            raise ValueError(f"Lorentz axis {axis!r}: expected x | y | z")
        node["axis"] = axis
    return node


def Rational(*, num, den, axis=None):
    if len(num) != 3 or len(den) != 2:
        raise ValueError("Rational(num=..., den=...): num is 3 coefficients "
                         "(a0, a1, a2), den is 2 (b0, b1)")
    node = _payload("rational", num=list(num), den=list(den))
    if axis is not None:
        node["axis"] = axis
    return node
