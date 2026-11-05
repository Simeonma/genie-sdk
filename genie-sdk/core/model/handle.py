"""genie.core.model.handle — Model（core 侧 f 本体）+ 集合视图 + 短引用 + 发号器。

契约 → ``../MODMAP.md``。禁令 1：Model 不长动作（submit / _submit /
_wait / _cancel / 联网 / 落盘一概没有——动作面住 worker/run，Q2.2 终版；
``test_model_has_no_run`` 锁，随 worker 批改名 test_model_has_no_actions）；
禁令 4：短引用不缓存
item 对象，每次属性访问按 id 解析回 draft——删除后自然失效。
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
    """全局发号器（四类共用一套）：单调递增、删除不复用、不可重置、
    不可手工设值；``raise_to`` 是唯一外部输入口（打开文档「取最大」，
    禁令 5）；``last_id`` 是唯一只读口（保存写 state.json，批 4）。"""

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
        """最高已发（或已抬到）的 id——保存值（``state.json`` 的
        ``allocator`` 键，Q1.2.5 / archive 图四栏）。"""
        return self._next - 1


class _Ref:
    """短引用（Q1.4）：构造时解析一次地址（name→id），之后每次属性访问
    按 id 回 draft 解析——改名不失效、删除后报「找不到」。不是必须保存
    的变量。"""

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
    """四类 item 集合视图（Q1.4）：``add_*`` 只带地址、``remove(name|id)``、
    ``[name|id]`` 双寻址（Q1.2.4）。"""

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
        # Q1.4 窄例外（2026-11-05）：材料 add 可收**整节点**——只收
        # mat_from_nk / assemble_nk 的产出物形态（type 校验 + 字段整装，
        # draft 的 validate_assignment 把关），永不逐字段 kwargs。
        # node 作第一参（add_lorentz(node)）的简写：dict 落在 name 位
        # 就换位——两种写法 add_lorentz(node) / add_lorentz('n', node)
        # 都通。
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
                setattr(item, k, v)     # 未知字段被 draft（extra=forbid）拦
        self._items.append(item)
        return name

    def __iter__(self):
        return iter([_Ref(self, it.id) for it in list(self._items)])

    def __len__(self):
        return len(self._items)

    # add_*：命令只带地址（Q1.4 规则 2；test_add_takes_address_only 锁）
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
    """core 侧的 f 本体（Q1.4 统一 f；Q6.10 语义住 core、值住 worker）。

    - 字段值只从 ``=`` 进；命令只带地址；
    - domain / mesh 单例直改；geom / mat / src / mon 集合视图；
    - parameters 键值；boundary 等字典字段整份或逐键赋值皆可；
    - 升格 ``to_config()`` 必经 ``validate_for_ui``（Q4.4）；
    - **无 submit / _submit / _wait / _cancel / 联网 / 落盘**（禁令 1；
      动作面住 worker/run）。
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
        """升格：draft → SimConfig 形状 dict（剔 None）。调用方必经
        ``genie_protocol.resolve.validate_for_ui``（Q4.4：校验面只有一个）。"""
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
        """发号器只读口（批 4，Q1.2.5）：worker 保存写 ``state.json``
        的 ``allocator`` 键（archive 图四栏）。只读——``raise_to``（打开
        文档取最大）仍是唯一输入口（禁令 5）。"""
        return self._alloc.last_id

    @classmethod
    def from_config(cls, cfg: dict, *, next_id: int | None = None) -> "Model":
        """打开文档：线格式 dict → Model。发号器取 max(next_id, 最大 item id)
        （Q1.2.5；next_id = state.json 记录值，防删除最大 id 后复用）。"""
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
