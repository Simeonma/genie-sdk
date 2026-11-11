"""genie.core.model.handle -- Model (the core-side body: shared structure
+ declared solver envelopes) + collection views + short references + the
id allocator (Q15).

Model grows no actions: no run / submit / _wait / _cancel / networking /
persistence at all -- the action surface lives in worker/run (locked by
tests). Short references do not cache item objects -- every attribute
access resolves back to the draft by id, so they naturally become
invalid once the item is removed.

Q15 layering (the API language mirrors the optics repo):

- **structure** (shared by every solver, one copy per model):
  ``materials`` / ``geometry`` / ``parameters``;
- **solver envelopes** (one per declared solver): ``domain`` / ``mesh`` /
  ``sources`` / ``monitors`` -- these belong to the *solver*, not to the
  model;
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
RunDraft = DRAFT_OF[_wire.RunSpec]

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
_SRC: dict = {
    "soft_source": DRAFT_OF[_wire.SoftSourceSpec],
    "plane_wave": DRAFT_OF[_wire.PlaneWaveSpec],
    "gaussian_beam": DRAFT_OF[_wire.GaussianBeamSpec],
}
_MON: dict = {
    "time": DRAFT_OF[_wire.TimeMonSpec],
    "freq": DRAFT_OF[_wire.FreqMonSpec],
    "index": DRAFT_OF[_wire.IndexMonSpec],
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
        # **whole node** -- only the output form of mat_from_nk /
        # assemble_nk (type check + fields set whole, guarded by the
        # draft's validate_assignment), never per-field kwargs.
        # Shorthand with node as the first argument (add_lorentz(node)):
        # a dict landing in the name slot is swapped into place -- both
        # spellings add_lorentz(node) / add_lorentz('n', node) work.
        if isinstance(name, dict) and node is None:
            name, node = None, name
        if node is not None:
            if not isinstance(node, dict):
                raise TypeError(
                    f"add_{kind}: node must be a dict (a material node "
                    f"from genie.mat_from_nk / genie.assemble_nk), got "
                    f"{type(node).__name__}")
            if node.get("type") != kind:
                raise ValueError(
                    f"add_{kind} expects a {kind} node, got type "
                    f"{node.get('type')!r}")
            if name is None:
                name = node.get("name")
        if name is None:
            name = f"{kind}_{iid}"
        item = self._kinds[kind](type=kind, name=name, id=iid)
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

    def add_const(self, name=None, node=None):  return self._add("const", name, node)
    def add_lorentz(self, name=None, node=None): return self._add("lorentz", name, node)
    def add_from_library(self, name=None):   return self._add("from_library", name)

    def add_soft_source(self, name=None):    return self._add("soft_source", name)
    def add_plane_wave(self, name=None):     return self._add("plane_wave", name)
    def add_gaussian_beam(self, name=None):  return self._add("gaussian_beam", name)

    def add_time(self, name=None):           return self._add("time", name)
    def add_freq(self, name=None):           return self._add("freq", name)
    def add_index(self, name=None):          return self._add("index", name)


class Solver:
    """One declared solver's envelope: ``domain`` / ``mesh`` (singletons,
    direct assignment) + ``sources`` / ``monitors`` (collections). The
    envelope belongs to the solver, not to the model (Q15); structure
    (materials / geometry / parameters) lives on the Model and is shared
    by every solver."""

    __slots__ = ("name", "domain", "mesh", "sources", "monitors",
                 "_source", "_monitor")

    def __init__(self, owner: "Model", name: str) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "domain", DomainDraft())
        object.__setattr__(self, "mesh", MeshDraft())
        self._source: list = []
        self._monitor: list = []
        object.__setattr__(self, "sources",
                           _CollectionView(owner, "sources",
                                           self._source, _SRC))
        object.__setattr__(self, "monitors",
                           _CollectionView(owner, "monitors",
                                           self._monitor, _MON))

    def __setattr__(self, name, value):
        # name / domain / mesh are plain assignment (domain / mesh swap on
        # document open); the collection views must not be overwritten
        if name in ("sources", "monitors"):
            raise AttributeError(
                f"{self.name}.sources/monitors are collection views and "
                f"cannot be overwritten")
        object.__setattr__(self, name, value)

    def __repr__(self):
        return f"<solver {self.name!r}>"


class Model:
    """The core-side body: shared structure + declared solver envelopes
    (semantics live in core, values live in worker).

    - Field values enter only via ``=``; commands take only an address;
    - structure: ``materials`` / ``geometry`` collections +
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
        self._run = RunDraft()
        self._geometry: list = []
        self._materials: list = []
        self.geometry = _CollectionView(self, "geometry", self._geometry, _GEO)
        self.materials = _CollectionView(self, "materials", self._materials, _MAT)
        self._solvers: dict[str, Solver] = {}

    # -- solvers ------------------------------------------------------

    def add_solver(self, name) -> Solver:
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
        s = Solver(self, name)
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
            s = Solver(self, name)
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

    # -- promotion ------------------------------------------------------

    def to_config(self, solver: str | None = None) -> dict:
        """Promotion: draft → a dict in the **Q17 nested** SimConfig shape
        (Nones stripped) for one run of ``solver`` (default = the fdtd
        default, whose payload omits the wire ``solver`` field): structure
        under ``model``, that solver's envelope under ``config``, ``run``
        at the top level. The caller must then go through
        ``genie_protocol.resolve.validate_for_ui`` (the single
        validation surface)."""
        name = solver if solver is not None else DEFAULT_SOLVER
        s = self.envelope(name)
        cfg = {
            "model": {
                "materials": [to_wire(i) for i in self._materials],
                "geometry": [to_wire(i) for i in self._geometry],
                "parameters": dict(self.parameters),
            },
            "config": {
                "domain": to_wire(s.domain),
                "mesh": to_wire(s.mesh),
                "source": [to_wire(i) for i in s._source],
                "monitor": [to_wire(i) for i in s._monitor],
            },
            "run": to_wire(self._run),
        }
        if solver is not None and solver != DEFAULT_SOLVER:
            cfg["solver"] = solver
        return cfg

    @property
    def last_id(self) -> int:
        """The id allocator's read-only port: when saving, the worker
        writes the ``allocator`` key of ``state.json``. Read-only --
        ``raise_to`` (take the max when opening a document) remains
        the only input port."""
        return self._alloc.last_id

    @classmethod
    def from_config(cls, cfg: dict, *, next_id: int | None = None) -> "Model":
        """Open a document: **Q17 nested** wire-format dict → Model. The
        single config is restored as the fdtd default solver's envelope
        (v1 documents hold one config; the multi-solver slot layout is
        deferred, Q15 挂账①). Flat legacy documents (b8 and earlier,
        domain/materials/… at the top level) fail loudly — the 2.0.1a1
        breaking cut, no migration shim. The id allocator takes
        max(next_id, highest item id) (next_id is the value recorded in
        state.json, preventing reuse after the highest id is removed)."""
        if "model" not in cfg or "config" not in cfg:
            raise ValueError(
                "not a 2.0.1a1 nested document (no 'model'/'config' "
                "section) — pre-2.0.1a1 flat documents are not openable "
                "after the Q17 breaking cut; rebuild the model or use an "
                "older SDK to convert")
        model = cfg.get("model") or {}
        conf = cfg.get("config") or {}
        m = cls()
        s = m.add_solver(DEFAULT_SOLVER)
        s.domain = DomainDraft.model_validate(conf.get("domain") or {})
        s.mesh = MeshDraft.model_validate(conf.get("mesh") or {})
        m._run = RunDraft.model_validate(cfg.get("run") or {})
        m.parameters = dict(model.get("parameters") or {})
        for kinds, items, src, key in (
                (_GEO, m._geometry, model, "geometry"),
                (_MAT, m._materials, model, "materials"),
                (_SRC, s._source, conf, "source"),
                (_MON, s._monitor, conf, "monitor")):
            for raw in src.get(key) or []:
                kind = raw.get("type")
                if kind not in kinds:
                    raise ValueError(f"{key}: unknown item type {kind!r}")
                items.append(kinds[kind].model_validate(raw))
        highest = max(
            [next_id or 0]
            + [it.id for it in (m._geometry + m._materials + s._source
                                + s._monitor) if it.id is not None])
        m._alloc.raise_to(highest)
        return m
