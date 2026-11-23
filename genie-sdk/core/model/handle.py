"""genie.core.model.handle -- Model (the core-side body: shared structure
+ declared solver envelopes) + collection views + short references + the
id allocator (Q15).

Model grows no actions: no run / submit / _wait / _cancel / networking /
persistence at all -- the action surface lives in worker/run (locked by
tests). Short references do not cache item objects -- every attribute
access resolves back to the draft by id, so they naturally become
invalid once the item is removed.

Q15 layering (the API language mirrors the optics repo), Q18 per-solver
envelope shapes:

- **structure** (shared by every solver, one copy per model):
  ``materials`` / ``geometry`` / ``parameters``;
- **solver envelopes** (one per declared solver, each in its own
  vocabulary): fdtd speaks ``domain`` (base grid dx/dy/dz since Q30) /
  ``meshes`` (named refinement boxes) / ``sources`` / ``monitors``
  (Q15+Q30); mode speaks ``domain`` (base grid: spacing-or-coords) /
  ``meshes`` / ``boundary`` + top-level ``wavelength`` / ``n_modes``
  (Q18+Q30 — no fake symmetry: an eigenproblem takes no excitations,
  hence no sources/monitors);
- solvers are declared explicitly (``add_solver``); the default solver
  ``"fdtd"`` auto-vivifies on access (it is the wire default --
  ``solver=None`` -- and the GUI edits it before any script line could
  declare it);
- ``to_config(solver)`` assembles one wire-shaped SimConfig per run:
  shared structure copied in + that solver's envelope + the ``solver``
  field (omitted for the fdtd default -- legacy payloads stay
  byte-identical; protocol b8 frozen).
"""
from __future__ import annotations

import re

from genie_protocol import _wire
from genie_protocol._wire import _SOLVER_NAME

from .draft import DRAFT_OF, to_wire

DomainDraft = DRAFT_OF[_wire.DomainSpec]
MeshDraft = DRAFT_OF[_wire.MeshSpec]
ModeDomainDraft = DRAFT_OF[_wire.ModeDomainSpec]
ModeMeshDraft = DRAFT_OF[_wire.ModeMeshSpec]
ModeConfigDraft = DRAFT_OF[_wire.ModeConfigSpec]

#: the solver whose runs omit the wire ``solver`` field (the wire default;
#: legacy payloads byte-identical) and whose envelope auto-vivifies.
DEFAULT_SOLVER = "fdtd"

_SOLVER_RE = re.compile(_SOLVER_NAME)

_GEO: dict = {
    "box": DRAFT_OF[_wire.BoxSpec],
    "sphere": DRAFT_OF[_wire.SphereSpec],
    "cylinder": DRAFT_OF[_wire.CylinderSpec],
    "ngon_z": DRAFT_OF[_wire.NgonZSpec],
    "polygon_z": DRAFT_OF[_wire.PolygonZSpec],
    "ellipse_z": DRAFT_OF[_wire.EllipseZSpec],
    "ring_z": DRAFT_OF[_wire.RingZSpec],
}
_MAT: dict = {
    "const": DRAFT_OF[_wire.ConstMatSpec],
    "lorentz": DRAFT_OF[_wire.LorentzMatSpec],
    "from_library": DRAFT_OF[_wire.LibraryMatSpec],
}
_ATTR: dict = {
    "matrix_frame": DRAFT_OF[_wire.MatrixFrameSpec],
}
_SRC: dict = {
    "soft_source": DRAFT_OF[_wire.SoftSourceSpec],
    "plane_wave": DRAFT_OF[_wire.PlaneWaveSpec],
    "gaussian_beam": DRAFT_OF[_wire.GaussianBeamSpec],
    "mode": DRAFT_OF[_wire.ModeSourceSpec],
}
_MON: dict = {
    "time": DRAFT_OF[_wire.TimeMonSpec],
    "freq": DRAFT_OF[_wire.FreqMonSpec],
    "index": DRAFT_OF[_wire.IndexMonSpec],
}
_MESH: dict = {
    # Q30: mesh = named refinement-box object (one-kind collection — the
    # draft carries no ``type`` discriminator; add_mesh is the only verb).
    "mesh": DRAFT_OF[_wire.MeshSpec],
}
_MODE_MESH: dict = {
    "mesh": DRAFT_OF[_wire.ModeMeshSpec],
}


class _Allocator:
    """Global id allocator (one shared by all four kinds): monotonically
    increasing, ids are never reused after removal, cannot be reset,
    cannot be set by hand; ``raise_to`` is the only external input
    (opening a document "takes the max"); ``last_id`` is the only
    read-only port (saving writes state.json)."""

    __slots__ = ("_next",)

    def __init__(self) -> None:
        self._next = 1

    def issue(self) -> int:
        v = self._next
        self._next += 1
        return v

    def raise_to(self, highest: int) -> None:
        if highest >= self._next:
            self._next = highest + 1

    @property
    def last_id(self) -> int:
        """The highest issued (or raised-to) id -- the value saved
        as the ``allocator`` key of ``state.json``."""
        return self._next - 1


class _Ref:
    """Short reference: resolves the address once at construction
    (name→id); afterwards every attribute access resolves back to the
    draft by id -- unaffected by renames, raises "not found" after
    removal. Not a variable that must be kept."""

    __slots__ = ("_view", "_id")

    def __init__(self, view: "_CollectionView", iid: int) -> None:
        object.__setattr__(self, "_view", view)
        object.__setattr__(self, "_id", iid)

    def _item(self):
        for it in self._view._items:
            if it.id == self._id:
                return it
        raise LookupError(
            f"{self._view._label}[{self._id}]: item no longer exists "
            f"(removed?)")

    def __getattr__(self, name: str):
        return getattr(self._item(), name)

    def __setattr__(self, name: str, value):
        setattr(self._item(), name, value)

    def __repr__(self):
        return f"<{self._view._label}#{self._id}>"


class _CollectionView:
    """Collection view over the four item kinds: ``add_*`` takes only
    an address, ``remove(name|id)``, ``[name|id]`` dual addressing."""

    def __init__(self, owner: "Model", label: str, items: list,
                 kinds: dict) -> None:
        self._model = owner
        self._label = label
        self._items = items
        self._kinds = kinds

    def _resolve(self, key) -> int:
        if isinstance(key, bool):
            raise TypeError("address by int id or str name, not bool")
        if isinstance(key, int):
            for it in self._items:
                if it.id == key:
                    return key
            raise LookupError(f"{self._label}[{key}]: no item with id {key}")
        if isinstance(key, str):
            for it in self._items:
                if it.name == key:
                    return it.id
            raise LookupError(f"{self._label}[{key!r}]: not found")
        raise TypeError(f"address {key!r}: expected int id or str name")

    def __getitem__(self, key) -> _Ref:
        return _Ref(self, self._resolve(key))

    def remove(self, key) -> None:
        iid = self._resolve(key)
        for i, it in enumerate(self._items):
            if it.id == iid:
                del self._items[i]
                return
        raise LookupError(f"{self._label}: id {iid} vanished mid-remove")

    def _add(self, kind: str, name, node=None) -> str:
        iid = self._model._alloc.issue()
        # Narrow exception: material add accepts a
        # **whole node** -- only the output form of
        # assemble_nk (type check + fields set whole, guarded by the
        # draft's validate_assignment), never per-field kwargs.
        # Shorthand with node as the first argument (add_lorentz(node)):
        # a dict landing in the name slot is swapped into place -- both
        # spellings add_lorentz('n', node) / add_lorentz(node) work.
        if isinstance(name, dict) and node is None:
            name, node = None, name
        if node is not None:
            if not isinstance(node, dict):
                raise TypeError(
                    f"add_{kind}: node must be a dict (a material node "
                    f"from genie.assemble_nk), got "
                    f"{type(node).__name__}")
            if node.get("type") != kind:
                raise ValueError(
                    f"add_{kind} expects a {kind} node, got type "
                    f"{node.get('type')!r}")
            if name is None:
                name = node.get("name")
        if name is None:
            name = f"{kind}_{iid}"
        cls = self._kinds[kind]
        # ``type`` is passed only when the draft carries the field (mesh
        # objects are one-kind collections -- no discriminator field).
        kw = {"name": name, "id": iid}
        if "type" in cls.model_fields:
            kw["type"] = kind
        item = cls(**kw)
        if node is not None:
            for k, v in node.items():
                if k in ("type", "id", "name"):
                    continue
                setattr(item, k, v)     # unknown fields are blocked by the draft (extra=forbid)
        self._items.append(item)
        return name

    def __iter__(self):
        return iter([_Ref(self, it.id) for it in list(self._items)])

    def __len__(self):
        return len(self._items)

    # add_*: commands take only an address (locked by tests)
    def add_box(self, name=None):            return self._add("box", name)
    def add_sphere(self, name=None):         return self._add("sphere", name)
    def add_cylinder(self, name=None):       return self._add("cylinder", name)
    def add_ngon_z(self, name=None):         return self._add("ngon_z", name)
    def add_polygon_z(self, name=None):      return self._add("polygon_z", name)
    def add_ellipse_z(self, name=None):      return self._add("ellipse_z", name)
    def add_ring_z(self, name=None):         return self._add("ring_z", name)

    def add_attribute(self, name=None, type=None):  # noqa: A002
        """Named, reusable object attribute (today: ``matrix_frame``)."""
        kind = type or "matrix_frame"
        if kind not in _ATTR:
            raise ValueError(
                f"attribute type {kind!r} is invalid (expected: "
                f"{', '.join(sorted(_ATTR))})")
        return self._add(kind, name)

    def add_const(self, name=None, node=None):  return self._add("const", name, node)
    def add_lorentz(self, name=None, node=None): return self._add("lorentz", name, node)
    def add_from_library(self, name=None):   return self._add("from_library", name)

    def add_soft_source(self, name=None):    return self._add("soft_source", name)
    def add_plane_wave(self, name=None):     return self._add("plane_wave", name)
    def add_gaussian_beam(self, name=None):  return self._add("gaussian_beam", name)
    def add_mode(self, name=None):           return self._add("mode", name)

    def add_time(self, name=None):           return self._add("time", name)
    def add_freq(self, name=None):           return self._add("freq", name)
    def add_index(self, name=None):          return self._add("index", name)

    def add_mesh(self, name=None):
        """Q30: a named refinement-box object (factor_x/y/z XOR absolute
        dx/dy/dz per axis; the base grid lives on ``domain``)."""
        return self._add("mesh", name)


class Solver:
    """One declared solver's envelope: ``domain`` (singleton, direct
    assignment — carries the base grid ``dx/dy/dz`` since Q30) +
    ``meshes`` / ``sources`` / ``monitors`` (collections; meshes = named
    refinement-box objects). The envelope belongs to the solver, not to
    the model (Q15); structure (materials / geometry / parameters) lives
    on the Model and is shared by every solver."""

    __slots__ = ("name", "kind", "domain", "meshes", "sources", "monitors",
                 "_mesh", "_source", "_monitor")

    def __init__(self, owner: "Model", name: str) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", "fdtd")
        object.__setattr__(self, "domain", DomainDraft())
        self._mesh: list = []
        self._source: list = []
        self._monitor: list = []
        object.__setattr__(self, "meshes",
                           _CollectionView(owner, "meshes", self._mesh,
                                           _MESH))
        object.__setattr__(self, "sources",
                           _CollectionView(owner, "sources",
                                           self._source, _SRC))
        object.__setattr__(self, "monitors",
                           _CollectionView(owner, "monitors",
                                           self._monitor, _MON))

    def __setattr__(self, name, value):
        # name / domain are plain assignment (domain swaps on document
        # open); the collection views must not be overwritten
        if name in ("meshes", "sources", "monitors"):
            raise AttributeError(
                f"{self.name}.{name} is a collection view and "
                f"cannot be overwritten")
        object.__setattr__(self, name, value)

    def __repr__(self):
        return f"<solver {self.name!r}>"


class ModeEnvelope:
    """mode 求解器的包络（Q18）：``domain`` / ``boundary`` 段 + 顶层
    ``wavelength`` / ``n_modes`` + ``meshes`` 集合（Q30 对称迁移：带名
    2D 加密区对象；domain 携带基础网格）——**无 sources / monitors**
    （本征问题不吃激励，不造假对称）。整段是一枚 ModeConfigDraft
    （反射生成、赋值点拦类型/形状错，值只经 ``=``）；提升时的存在性
    守卫（横截面两轴必填、波长必填）在 validate_for_ui，draft 阶段留
    未填态。"""

    __slots__ = ("name", "kind", "_cfg", "meshes")

    #: 包络字段（= ModeConfigSpec 顶层〔mesh 除外——它是集合〕；读写都
    #: 路由进 config draft）。n_ref（Q20）：搜索折射率，缺省 None =
    #: 截面最大折射率（内核缺省）。
    _FIELDS = ("domain", "boundary", "wavelength", "n_modes",
               "n_ref", "rotate_axis", "rotate_angle", "rotate_radius")

    def __init__(self, owner: "Model", name: str) -> None:
        # owner 形参与 Solver 同构（包络不回指模型——结构在 Model 上
        # 共享；owner 只进 meshes 集合视图，换 id 分配器）。
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", "mode")
        cfg = ModeConfigDraft()
        cfg.domain = ModeDomainDraft()      # 急建单件：genie.mode.domain.axis 直达
        cfg.mesh = []                       # Q30：带名加密区对象列表
        cfg.boundary = {}
        object.__setattr__(self, "_cfg", cfg)
        object.__setattr__(self, "meshes",
                           _CollectionView(owner, "meshes", cfg.mesh,
                                           _MODE_MESH))

    def __setattr__(self, name, value):
        if name in ("name", "_cfg", "meshes"):
            object.__setattr__(self, name, value)
        elif name in self._FIELDS:
            setattr(self._cfg, name, value)   # draft 在赋值点拦类型/形状错
        else:
            raise AttributeError(
                f"mode envelope has no attribute {name!r} (envelope "
                f"fields: {', '.join(self._FIELDS)}; meshes: the "
                f"collection)")

    def __getattr__(self, name):
        if name in self._FIELDS:
            return getattr(self._cfg, name)
        raise AttributeError(
            f"mode envelope has no attribute {name!r} (envelope "
            f"fields: {', '.join(self._FIELDS)}; meshes: the "
            f"collection)")

    def __repr__(self):
        return f"<solver {self.name!r} (mode)>"


#: 求解器名 → 包络类（Q18：认识的求解者各拿各的形状；未列名的沿用 fdtd
#: 形通用包络 Solver——开放集合，wire 对未列名求解器的 config 原样透传
#: dict，与 protocol._CONFIG_OF 的「认识谁验谁」同一条线）。
_ENVELOPE_OF = {"mode": ModeEnvelope}


class Model:
    """The core-side body: shared structure + declared solver envelopes
    (semantics live in core, values live in worker).

    - Field values enter only via ``=``; commands take only an address;
    - structure: ``materials`` / ``geometry`` / ``attributes`` collections +
      ``parameters`` key-value pairs, shared by every solver;
    - solvers: ``add_solver(name)`` declares; the fdtd default
      auto-vivifies on access (``envelope``);
    - promotion ``to_config(solver)`` must go through
      ``validate_for_ui`` (the single validation surface);
    - **no run / submit / networking / persistence** (the action surface
      lives in worker/run).
    """

    def __init__(self) -> None:
        self._alloc = _Allocator()
        self.parameters: dict = {}
        self._geometry: list = []
        self._materials: list = []
        self._attributes: list = []
        self.geometry = _CollectionView(self, "geometry", self._geometry, _GEO)
        self.materials = _CollectionView(self, "materials", self._materials, _MAT)
        self.attributes = _CollectionView(self, "attributes",
                                          self._attributes, _ATTR)
        self._solvers: dict[str, Solver] = {}

    # -- solvers ------------------------------------------------------

    def add_solver(self, name, kind=None) -> Solver:
        kind = kind or name
        """Declare a solver (its envelope starts empty). The name follows
        the wire ``_SOLVER_NAME`` pattern (same ruler as the protocol --
        no second regex here). Duplicate declaration is an error (it
        would silently discard the existing envelope)."""
        if not isinstance(name, str) or not _SOLVER_RE.fullmatch(name):
            raise ValueError(
                f"bad solver name {name!r} — expected a lower-case "
                f"identifier like 'fdtd' / 'mode' (same pattern as the "
                f"wire format)")
        if name in self._solvers:
            raise ValueError(
                f"solver {name!r} is already declared — envelopes hold "
                f"solver state, re-declaring would discard it "
                f"(use the existing genie.{name})")
        if kind not in ("fdtd", "mode"):
            raise ValueError(
                f"solver kind must be fdtd|mode, got {kind!r}")
        cls = ModeEnvelope if kind == "mode" else Solver
        s = cls(self, name)
        self._solvers[name] = s
        return s

    def envelope(self, name) -> Solver:
        """The declared solver's envelope. The fdtd **default**
        auto-vivifies (it is the wire ``solver=None`` default; the GUI
        edits it before any script line could declare it); every other
        name must be declared first — a one-line error pointing at
        ``add_solver`` (this also catches typos: ``genie.fdtc`` fails
        here instead of silently building an empty run)."""
        if name == DEFAULT_SOLVER and name not in self._solvers:
            s = _ENVELOPE_OF.get(name, Solver)(self, name)
            self._solvers[name] = s
            return s
        try:
            return self._solvers[name]
        except KeyError:
            if isinstance(name, str) and _SOLVER_RE.fullmatch(name):
                raise LookupError(
                    f"solver {name!r} is not declared — call "
                    f"genie.add_solver({name!r}) first") from None
            raise AttributeError(
                f"model has no attribute {name!r}") from None

    @property
    def solvers(self) -> tuple:
        """The declared solver names (insertion order; read-only)."""
        return tuple(self._solvers)

    def solver_list(self) -> list:
        """The declared solvers as ``[{"name", "kind"}]`` (the GUI tree's
        one fact source — solvers are model declarations, not wire payload,
        so they ride the cfg snapshot and never the submit payload)."""
        return [{"name": n, "kind": getattr(s, "kind", DEFAULT_SOLVER)}
                for n, s in self._solvers.items()]

    # -- promotion ------------------------------------------------------

    def _wire_monitor(self, mon) -> dict:
        """Resolve an SDK ``mode_solver`` reference in-place for the wire.

        ``mon.mode_expansion`` may be a dict ``{"mode_solver": "probe",
        "freq": ...}``; the payload must carry the *profile*, never the
        local name (the remote job has no document). The reference stays on
        the draft; only the serialize is resolved.
        """
        me = getattr(mon, "mode_expansion", None)
        ref = getattr(mon, "_mode_expansion_ref", None)
        if ref is None:
            if not (isinstance(me, dict) and "mode_solver" in me):
                return to_wire(mon)
            ref = me
        ref = dict(ref)
        name = ref.pop("mode_solver")
        env = self.envelope(name)
        if not isinstance(env, ModeEnvelope):
            raise ValueError(
                f"mode_expansion.mode_solver={name!r} is kind "
                f"{getattr(env, 'kind', 'fdtd')!r}, not 'mode'")
        from genie_protocol import ModeExpansionSpec
        profile = {
            "n_modes": int(getattr(env, "n_modes", 1) or 1),
            "n_ref": getattr(env, "n_ref", None),
            "boundary": dict(getattr(env, "boundary", {}) or {}),
            "rotate_axis": getattr(env, "rotate_axis", None),
            "rotate_angle": float(getattr(env, "rotate_angle", 0.0) or 0.0),
            "rotate_radius": float(getattr(env, "rotate_radius", 0.0) or 0.0),
            "freq": ref.pop("freq", "center"),
        }
        if ref:
            raise ValueError(
                f"mode_expansion reference has unknown key(s) "
                f"{sorted(ref)} (only mode_solver / freq)")
        resolved = DRAFT_OF[ModeExpansionSpec].model_validate(profile)
        old = mon.mode_expansion
        mon.mode_expansion = resolved
        try:
            return to_wire(mon)
        finally:
            mon.mode_expansion = old

    def to_config(self, solver: str | None = None) -> dict:
        """Promotion: draft → a dict in the **Q17 nested** SimConfig shape
        (Nones stripped) for one run of ``solver`` (default = the fdtd
        default, whose payload omits the wire ``solver`` field): structure
        under ``model``, that solver's envelope under ``config`` (shape
        follows the solver — fdtd: domain/mesh/source/monitor; mode: Q18
        v2 sections). **No ``run`` section** (Q38): resources are action
        parameters assembled into the payload at submit, never document
        state. The caller must then go through
        ``genie_protocol.resolve.validate_for_ui`` (the single
        validation surface)."""
        name = solver if solver is not None else DEFAULT_SOLVER
        s = self.envelope(name)
        conf = self._envelope_conf(s)
        cfg = {
            "model": self._document_model(),
            "config": conf,
        }
        kind = getattr(s, "kind", DEFAULT_SOLVER)
        if kind != DEFAULT_SOLVER:
            cfg["solver"] = kind
        return cfg

    def to_document(self) -> dict:
        """The **authoring document** (the ``simconfig.json`` member of a
        ``.gensim``): shared structure + **every declared solver envelope,
        by name** — nothing is silently dropped on save. Solvers never
        declared (the fdtd default auto-vivifies only on access) are
        absent. This is SDK-private shape: the wire payload is always
        :meth:`to_config` (one solver), and resources are NOT document
        state (Q38 — run is assembled at submit from action args)."""
        mdl = self._document_model()
        solvers = []
        for name, s in self._solvers.items():
            solvers.append({"name": name,
                            "kind": getattr(s, "kind", DEFAULT_SOLVER),
                            "config": self._envelope_conf(s)})
        return {"model": mdl, "solvers": solvers}

    def _document_model(self) -> dict:
        mdl = {
            "materials": [to_wire(i) for i in self._materials],
            "geometry": [to_wire(i) for i in self._geometry],
            # Empty attributes are OMITTED: the wire key is additive and
            # legacy configs must stay byte-identical (digest unchanged).
            "parameters": dict(self.parameters),
        }
        if self._attributes:
            mdl["attributes"] = [to_wire(i) for i in self._attributes]
        return mdl

    def _envelope_conf(self, s) -> dict:
        """One solver envelope → its ``config`` section (fdtd four-part /
        mode Q18 v2 whole-draft; Nones stripped by ``to_wire``)."""
        if isinstance(s, ModeEnvelope):
            # mode：包络段 = config draft 整体（Q18 v2 分段；Q30 起 mesh
            # = 带名对象列表，draft 里的列表原样承运）；未填字段自然
            # 缺省，缺必填在提升面（validate_for_ui）422。
            return to_wire(s._cfg)
        return {
            "domain": to_wire(s.domain),
            "mesh": [to_wire(i) for i in s._mesh],
            "source": [to_wire(i) for i in s._source],
            "monitor": [self._wire_monitor(i) for i in s._monitor],
        }

    @property
    def last_id(self) -> int:
        """The id allocator's read-only port: when saving, the worker
        writes the ``allocator`` key of ``state.json``. Read-only --
        ``raise_to`` (take the max when opening a document) remains
        the only input port."""
        return self._alloc.last_id

    @classmethod
    def from_config(cls, cfg: dict, *, next_id: int | None = None) -> "Model":
        """Open a document (the **Q38 slot layout**): ``{model, solvers:
        [{name, kind, config}, …]}`` → Model with **every** declared
        envelope restored by name — a save/open round-trip loses nothing
        (the Q15① multi-solver slots, finally). Documents without a
        ``solvers`` section (the 2.0.1a1–2.0.2a1 single-config shape) and
        pre-2.0.1a1 flat documents fail loudly — the Q38 breaking cut, no
        migration shim. A kind without an SDK envelope fails loudly
        (restoring it as fdtd would silently mis-shape it). The id
        allocator takes max(next_id, highest item id) (next_id is the
        value recorded in state.json, preventing reuse after the highest
        id is removed)."""
        if "model" not in cfg or "solvers" not in cfg:
            raise ValueError(
                "not a Q38 slot document (no 'model'/'solvers' section) — "
                "older documents (single-config or flat) are not openable "
                "after the Q38 breaking cut; rebuild the model")
        model = cfg.get("model") or {}
        slots = cfg.get("solvers") or []
        m = cls()
        all_meshes: list = []
        all_src: list = []
        all_mon: list = []
        for slot in slots:
            name = slot.get("name")
            kind = slot.get("kind") or DEFAULT_SOLVER
            conf = slot.get("config") or {}
            if kind != DEFAULT_SOLVER and kind not in _ENVELOPE_OF:
                raise ValueError(
                    f"solver {name!r} (kind {kind!r}) has no SDK envelope "
                    f"(known: {DEFAULT_SOLVER}, "
                    f"{', '.join(sorted(_ENVELOPE_OF))}) — the wire passes "
                    f"it through, but this SDK cannot edit that envelope "
                    f"shape")
            s = m.add_solver(name, kind)
            if kind == DEFAULT_SOLVER:
                s.domain = DomainDraft.model_validate(conf.get("domain") or {})
                for raw in conf.get("mesh") or []:
                    s._mesh.append(MeshDraft.model_validate(raw))
                for raw in conf.get("source") or []:
                    if raw.get("type") not in _SRC:
                        raise ValueError(
                            f"source: unknown item type {raw.get('type')!r}")
                    s._source.append(_SRC[raw["type"]].model_validate(raw))
                for raw in conf.get("monitor") or []:
                    if raw.get("type") not in _MON:
                        raise ValueError(
                            f"monitor: unknown item type "
                            f"{raw.get('type')!r}")
                    s._monitor.append(
                        _MON[raw["type"]].model_validate(raw))
                all_meshes += s._mesh
                all_src += s._source
                all_mon += s._monitor
            else:
                s._cfg = ModeConfigDraft.model_validate(conf)
                mesh_items = s._cfg.mesh or []
                # rebind the collection to the restored list (the view was
                # built against the envelope's original empty list)
                s.meshes._items = mesh_items
                all_meshes += mesh_items
        m.parameters = dict(model.get("parameters") or {})
        for kinds, items, src, key in (
                (_GEO, m._geometry, model, "geometry"),
                (_MAT, m._materials, model, "materials"),
                (_ATTR, m._attributes, model, "attributes")):
            for raw in src.get(key) or []:
                kind = raw.get("type")
                if kind not in kinds:
                    raise ValueError(f"{key}: unknown item type {kind!r}")
                items.append(kinds[kind].model_validate(raw))
        highest = max(
            [next_id or 0]
            + [it.id for it in (m._geometry + m._materials + m._attributes
                                + all_src + all_mon + all_meshes)
               if it.id is not None])
        m._alloc.raise_to(highest)
        return m
