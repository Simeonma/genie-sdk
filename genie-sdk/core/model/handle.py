"""genie.core.model.handle -- Model (the core-side f body) + collection
views + short references + the id allocator.

Model grows no actions: no submit / _submit / _wait / _cancel /
networking / persistence at all -- the action surface lives in
worker/run (locked by tests). Short references do not cache item
objects -- every attribute access resolves back to the draft by id,
so they naturally become invalid once the item is removed.
"""
from __future__ import annotations

from typing import Any

from genie_protocol import _wire

from .draft import DRAFT_OF, to_wire

DomainDraft = DRAFT_OF[_wire.DomainSpec]
MeshDraft = DRAFT_OF[_wire.MeshSpec]
RunDraft = DRAFT_OF[_wire.RunSpec]

_GEO: dict = {
    "box": DRAFT_OF[_wire.BoxSpec],
    "sphere": DRAFT_OF[_wire.SphereSpec],
    "cylinder": DRAFT_OF[_wire.CylinderSpec],
    "ngon_z": DRAFT_OF[_wire.NgonZSpec],
    "polygon_z": DRAFT_OF[_wire.PolygonZSpec],
    "ellipse_z": DRAFT_OF[_wire.EllipseZSpec],
    "ring_z": DRAFT_OF[_wire.RingZSpec],
    "polygon_swept": DRAFT_OF[_wire.PolygonSweptSpec],
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
    """Short reference: resolves the address once at
    construction (name→id); afterwards every attribute access
    resolves back to the draft by id -- unaffected by renames,
    raises "not found" after removal. Not a variable that must be
    kept."""

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
                    f"from f.mat_from_nk / f.assemble_nk), got "
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
    def add_polygon_swept(self, name=None):  return self._add("polygon_swept", name)

    def add_const(self, name=None, node=None):  return self._add("const", name, node)
    def add_lorentz(self, name=None, node=None): return self._add("lorentz", name, node)
    def add_from_library(self, name=None):   return self._add("from_library", name)

    def add_soft_source(self, name=None):    return self._add("soft_source", name)
    def add_plane_wave(self, name=None):     return self._add("plane_wave", name)
    def add_gaussian_beam(self, name=None):  return self._add("gaussian_beam", name)

    def add_time(self, name=None):           return self._add("time", name)
    def add_freq(self, name=None):           return self._add("freq", name)
    def add_index(self, name=None):          return self._add("index", name)


class Model:
    """The core-side f body (semantics live in core, values live in
    worker).

    - Field values enter only via ``=``; commands take only an address;
    - domain / mesh by direct singleton assignment; geom / mat / src /
      mon as collection views;
    - parameters as key-value pairs; dict fields such as boundary
      accept whole-value or per-key assignment;
    - promotion ``to_config()`` must go through ``validate_for_ui``
      (the single validation surface);
    - **no submit / _submit / _wait / _cancel / networking /
      persistence** (the action surface lives in worker/run).
    """

    def __init__(self) -> None:
        self._alloc = _Allocator()
        self.domain = DomainDraft()
        self.mesh = MeshDraft()
        self.parameters: dict = {}
        self._run = RunDraft()
        self._geometry: list = []
        self._materials: list = []
        self._source: list = []
        self._monitor: list = []
        self.geom = _CollectionView(self, "geom", self._geometry, _GEO)
        self.mat = _CollectionView(self, "mat", self._materials, _MAT)
        self.src = _CollectionView(self, "src", self._source, _SRC)
        self.mon = _CollectionView(self, "mon", self._monitor, _MON)

    def to_config(self) -> dict:
        """Promotion: draft → a dict in the SimConfig shape (Nones
        stripped). The caller must then go through
        ``genie_protocol.resolve.validate_for_ui`` (the single
        validation surface)."""
        return {
            "domain": to_wire(self.domain),
            "mesh": to_wire(self.mesh),
            "materials": [to_wire(i) for i in self._materials],
            "geometry": [to_wire(i) for i in self._geometry],
            "source": [to_wire(i) for i in self._source],
            "monitor": [to_wire(i) for i in self._monitor],
            "run": to_wire(self._run),
            "parameters": dict(self.parameters),
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
        """Open a document: wire-format dict → Model. The id allocator
        takes max(next_id, highest item id) (next_id is the
        value recorded in state.json, preventing reuse after the
        highest id is removed)."""
        m = cls()
        m.domain = DomainDraft.model_validate(cfg.get("domain") or {})
        m.mesh = MeshDraft.model_validate(cfg.get("mesh") or {})
        m._run = RunDraft.model_validate(cfg.get("run") or {})
        m.parameters = dict(cfg.get("parameters") or {})
        for kinds, items, key in (
                (_GEO, m._geometry, "geometry"),
                (_MAT, m._materials, "materials"),
                (_SRC, m._source, "source"),
                (_MON, m._monitor, "monitor")):
            for raw in cfg.get(key) or []:
                kind = raw.get("type")
                if kind not in kinds:
                    raise ValueError(f"{key}: unknown item type {kind!r}")
                items.append(kinds[kind].model_validate(raw))
        highest = max(
            [next_id or 0]
            + [it.id for it in (m._geometry + m._materials + m._source
                                + m._monitor) if it.id is not None])
        m._alloc.raise_to(highest)
        return m
