"""genie.core.model.draft — draft 镜像（残缺态唯一合法的家）+ 升格转换 + 模型指纹。

契约 → ``../MODMAP.md``：

- 与线格式**同名同型**、字段全部可选、不校验值（锁：
  ``test_draft_mirrors_wire_fields``）；残缺（add 后没填 material）在这里
  合法，值语义只在升格点（``to_wire → validate_for_ui``，Q4.4）。
- 镜像不手写字段清单：全部从 ``genie_protocol._wire`` 反射生成（禁令 2）；
  类型 / 形状错在赋值时拦（``validate_assignment``，禁令 3）。
- ``digest``（⚑P5 拍定 A，2026-11-04）：wire dict → canonical JSON
  （sort_keys + 固定分隔）→ sha256 hex；只哈希模型，不哈希 results /
  state；消费者 = worker/run（落位写、保存写、dirty 判定）。
"""
from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Optional, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model

from genie_protocol import _wire

__all__ = ["DRAFT_OF", "to_wire", "digest"]


class _DraftBase(BaseModel):
    """draft 公共形态：extra=forbid（拼错字段大声报错）+ 赋值时类型检查；
    值约束一律没有（不校验值，残缺态合法）。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _bare(ann: Any) -> Any:
    """剥 Annotated：线格式的约束元数据全部丢弃。"""
    meta = getattr(ann, "__metadata__", None)
    return ann.__origin__ if meta is not None else ann


def _ann_classes(ann: Any) -> set:
    """注解树里引用的全部类（依赖分析用）。"""
    ann = _bare(ann)
    origin = get_origin(ann)
    if origin is Union:
        out: set = set()
        for a in get_args(ann):
            out |= _ann_classes(a)
        return out
    if origin is not None:
        out = set()
        for a in get_args(ann):
            if inspect.isclass(a):
                out.add(a)
            else:
                out |= _ann_classes(a)
        return out
    return {ann} if inspect.isclass(ann) else set()


def _wire_classes() -> list:
    out = []
    for name, obj in vars(_wire).items():
        if (name.startswith("_") or not inspect.isclass(obj)
                or not issubclass(obj, BaseModel)):
            continue
        if obj.__module__ != _wire.__name__:
            continue                      # 再导出的外来类型不镜像
        out.append(obj)
    return out


_WIRE_CLASSES = _wire_classes()
_WIRE_SET = set(_WIRE_CLASSES)


def _deps(cls) -> set:
    out = set()
    for f in cls.model_fields.values():
        out |= {c for c in _ann_classes(f.annotation) if c in _WIRE_SET}
    out.discard(cls)
    return out


def _ordered() -> list:
    """拓扑序（被嵌套者先生成）；线格式是树，无环。"""
    done: list = []
    pending = set(_WIRE_CLASSES)
    while pending:
        ready = sorted((c for c in pending if not _deps(c) & pending),
                       key=lambda c: c.__name__)
        if not ready:
            raise RuntimeError("cycle in _wire model graph")
        done.extend(ready)
        pending.difference_update(ready)
    return done


# wire 类 → draft 类（镜像的唯一登记处；handle 从这里取具名 draft）
DRAFT_OF: dict = {}


def _draftify_inner(ann: Any) -> Any:
    """wire 注解 → draft 注解（不含 Optional 外层）：嵌套模型换 draft、
    约束剥光；Literal / 基本类型保持。"""
    ann = _bare(ann)
    if ann in DRAFT_OF:
        return DRAFT_OF[ann]
    origin = get_origin(ann)
    if origin is Union:
        args = tuple(a for a in get_args(ann) if a is not type(None))
        args = tuple(_draftify_inner(a) for a in args)
        return Union[args] if len(args) > 1 else args[0]
    if origin is list:
        args = get_args(ann)
        return list[_draftify_inner(args[0])] if args else list
    if origin is dict:
        args = get_args(ann)
        return dict[args[0], _draftify_inner(args[1])] if len(args) == 2 else dict
    if origin is tuple:
        args = get_args(ann)
        if args and args[-1] is Ellipsis:
            return tuple[_draftify_inner(args[0]), ...]
        return tuple[tuple(_draftify_inner(a) for a in args)]
    return ann


def _draftify(ann: Any) -> Any:
    """整层 Optional（draft 字段全部可选）。"""
    return Optional[_draftify_inner(ann)]


for _cls in _ordered():
    _name = (_cls.__name__[:-4] if _cls.__name__.endswith("Spec")
             else _cls.__name__) + "Draft"
    DRAFT_OF[_cls] = create_model(
        _name, __base__=_DraftBase,
        **{n: (_draftify(f.annotation), None)
           for n, f in _cls.model_fields.items()})


def to_wire(value: Any) -> Any:
    """升格：draft 值 → 线格式 dict（**剔 None**，递归）；原始 dict 透传
    递归（boundary 逐键赋值塞进的裸 dict 同样被收编）。随后必经
    ``validate_for_ui``（Q4.4：校验面只有一个）。"""
    if isinstance(value, BaseModel):
        out = {}
        for fname in type(value).model_fields:
            v = getattr(value, fname)
            if v is not None:
                out[fname] = to_wire(v)
        return out
    if isinstance(value, dict):
        return {k: to_wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_wire(v) for v in value]
    if isinstance(value, tuple):
        return tuple(to_wire(v) for v in value)
    return value


def digest(wire_dict: dict) -> str:
    """模型指纹（⚑P5 拍定 A）：升格产物 → canonical JSON（``sort_keys`` +
    固定分隔符 ``(",", ":")``）→ sha256 hex。**只哈希模型**（wire dict
    本身——不哈希 results / state，输入即边界）。确定性三案锁：
    ``test_digest_canonical``。"""
    payload = json.dumps(wire_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
