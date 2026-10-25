"""genie.models._wire — SimConfig 线格式的唯一真源（全部 pydantic 模型）。

> 2026-10 迁移阶段 1a：自内核 ``fdtd/serve/schema.py`` 81-935 行逐字迁入
> （framework.md §三 / migration-plan.md 阶段 1，站长已确认）。内核侧
> 同步变薄壳（重导出 + 资源守卫 + translate），SDK 与内核 import 的
> 是**同一份类对象**——不存在同步问题，锁测试退役。
>
> 拆分说明：原 schema.py 92-115 常量块中，**线格式常量**（FACES /
> DEFAULT_TIMEOUT_S / MAX_SWEEP_* / 字段名集合）随模型住这里；
> **部署策略阈值**（MAX_CELLS / MAX_STEPS / MAX_TIMEOUT_S /
> MAX_MONITORS / MAX_MONITOR_SAMPLES）是服务端资源守卫的配置，
> 留在内核 serve 层，不进 SDK。
>
> 懒加载纪律（2026-10 站长定案：浏览器镜像零新增依赖）：本模块由
> ``genie/models/__init__.py`` 的 ``__getattr__`` 按需加载——
> ``import genie.models.expr`` 不得拉起 pydantic。

线格式 JSON 形状示例（内核 schema.py 原文档头逐字保留）::

    {
      "domain":   {"x_range": [-1e-6, 1e-6], "y_range": [-1e-6, 1e-6],
                    "z_range": [-1e-6, 1e-6], "total_time": 60e-15, "cfl": 0.9,
                    "boundary": {"z_min": {"kind": "pml", "layers": 12},
                                  "z_max": {"kind": "pml", "layers": 12}}},
      "mesh":     {"dx": 20e-9, "dy": 20e-9, "dz": 20e-9},
      "materials":[{"name": "sio2", "type": "const", "eps": 2.10}],
      "geometry": [{"type": "box", "material": "sio2",
                     "x_range": [-0.5e-6, 0.5e-6], "y_range": [-0.5e-6, 0.5e-6],
                     "z_range": [-0.1e-6, 0.1e-6], "priority": 1}],
      "source":   [{"type": "soft_source", "freq": 300e12, "bandwidth": 60e12,
                     "name": "s0", "center": [0, 0, -0.3e-6],
                     "polarization": [1, 0, 0]}],
      "monitor":  [{"type": "time", "name": "pt", "center": [0, 0, 0.2e-6],
                     "size": [0, 0, 0], "field": "Ex"},
                    {"type": "freq", "name": "sp", "center": [0, 0, 0.2e-6],
                     "size": [0, 0, 0], "field": "Ey",
                     "freq": {"center": 300e12, "bw": 120e12, "n": 200}}],
      "run":      {"gpu": 1}
    }

**禁止**任何形式的可执行内容：模型只接受数/串/列表/字典，
无 eval/exec/pickle，polarization 复数用 [re, im] 对表达。数值一律 SI。
"""
from __future__ import annotations

import math
import os
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .expr import valid_param_name


def _walk_finite(v, path: str) -> None:
    """数值 isfinite 全量游走（G118 一双手）：float 逐个查；容器递归；
    None/str/bool/嵌套模型跳过（模型自带本守卫）。"""
    if v is None or isinstance(v, (bool, str)):
        return
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"{path} must be finite (got {v!r})")
    elif isinstance(v, (tuple, list)):
        for x in v:
            _walk_finite(x, path)
    elif isinstance(v, dict):
        for x in v.values():
            _walk_finite(x, path)


# isfinite 全量游走的唯一豁免：vertices 的 (nan, nan) 哨兵对
# （_ser_vertices_json 序列化为 [null,null]，2026-10-15 事故定案）。
_NAN_OK_FIELDS = frozenset({"vertices"})


class _StrictModel(BaseModel):
    """线格式纪律（P1 定案 2026-09）：未知字段一律 422 拒绝。

    pydantic 默认 ``extra="ignore"`` 曾意味着：可选字段拼错（如
    ``prioraty``）被**静默丢弃**，仿真以默认值跑完——静默的错误物理。
    严格化后所有客户端（curl/UI/SDK/agent）统一大声报错；存量
    payload 均为已校验的干净形状，回归全绿（见 test_schema.py）。

    数值字段全量 isfinite（G118，R16 轮）：NaN/Inf 一律拒——``gt/ge``
    约束拦不住 inf（``inf > 0`` 成立），NaN 比较全 False 只被"恰有
    数值约束的字段"顺带拦住。这是 2026-08-31 "eps=inf 穿 >0 静默
    build" 教训的推广（当年只修了 eps 系）。唯一豁免 =
    ``vertices`` 的 (nan, nan) 哨兵对（序列化为 [null, null]）。"""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_nonfinite(self):
        for name in type(self).model_fields:
            if name in _NAN_OK_FIELDS:
                continue
            _walk_finite(getattr(self, name), name)
        return self


# ---------------------------------------------------------------------------
# 线格式常量（自内核 schema.py 92-115 拆出；资源守卫阈值留内核 serve 层）
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


DEFAULT_TIMEOUT_S = 3_600
MAX_SWEEP_POINTS = _env_int("FDTD_MAX_SWEEP_POINTS", 512)  # sweep 展开点数上限
MAX_SWEEP_AXES = 32               # sweep 参数轴数上限

FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
BC_KINDS = ("pec", "pmc", "periodic", "pml", "bloch")
# from_library 材料拟合默认值（G2 归一正典）：线格式默认（LibraryMatSpec）、
# SDK 渲染器的"等于默认则省略"判据、builder.add_from_library 签名、
# material.fit_nk 签名——四处的单一来源，禁再手抄字面量。
LIBRARY_FIT_DEFAULTS = {
    "fit_range": (400e-9, 800e-9),
    "n_drude": 0,
    "n_lorentz": 2,
    "maxfev": 5000,
}
_FIELD_SET = ("Ex", "Ey", "Ez", "Hx", "Hy", "Hz", "all")
_INDEX_FIELD_SET = ("eps", "mu", "both")


# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------

Range2 = tuple[float, float]
Vec3 = tuple[float, float, float]
# 材料 eps 系值形状（2026-09 材料张量提案落地）：标量=iso（三轴同值，
# 语义不变，存量 payload 零扰动）| 3 元=对角各轴独立（Phase2 ③）|
# 3×3=完整张量（行优先嵌套 list[list[float]]，非对角对称=互易介质）。
# 极点参数（PoleSpec）为纯标量：iso（无 α/β）| rank-1（+α/β 方向）——
# 三轴 3元 速记已按站长裁决移除（2026-09 pole-direction 提案，选项 A）。
NumOrTensor = Union[float, tuple[float, float, float], list[list[float]]]


def _check_range(v: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(v[0]), float(v[1])
    if not (hi > lo) or not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(
            f"range [{lo}, {hi}] is invalid (expected: lo < hi and both "
            f"finite)")
    return (lo, hi)


def _check_tensor_sym(v):
    """3×3 张量守卫：形状 3×3、逐元有限、主对角 > 0、对称（相对容差
    1e-6）。镜像内核 _inf_to_tensor 的对称写与 _require_positive_diagonal
    的主对角>0 口径；非对角允许负——各向异性耦合可负是 L1 定案，物理
    稳定性的 schema 层防线只查主对角>0（正定性由引擎侧负责，同内核）。"""
    if len(v) != 3 or any(len(row) != 3 for row in v):
        raise ValueError(
            "tensor must be 3x3 (row-major nested list[list[float]])")
    for row in v:
        for x in row:
            if not math.isfinite(x):
                raise ValueError(f"tensor elements must be finite, got {x!r}")
    for i in range(3):
        if not v[i][i] > 0:
            raise ValueError(
                f"tensor main diagonal must be > 0, got v[{i}][{i}]="
                f"{v[i][i]!r}")
    for i in range(3):
        for j in range(i + 1, 3):
            a, b = v[i][j], v[j][i]
            if abs(a - b) > 1e-6 * max(1.0, abs(a), abs(b)):
                raise ValueError(
                    "tensor must be symmetric (reciprocal medium: "
                    "e[i][j] == e[j][i], relative tolerance 1e-6)")
    return v


def _check_finite_pos(v):
    """eps 系（eps/mu/eps_inf/mu_inf）逐元守卫：标量/3 元对角 = **有限且
    > 0**（镜像本地 _require_positive_diagonal，含 isfinite：eps=inf 曾穿
    ">0" 静默 build 再 NaN 污染全仿真，2026-08-31v 内核实证）；3×3 张量 =
    逐元有限 + 主对角>0 + 对称（_check_tensor_sym，非对角允许负）。两类
    payload 都从 driver 期失败提前到 422，合法有限 payload 零扰动。"""
    if isinstance(v, list):          # 3×3 张量
        return _check_tensor_sym(v)
    for x in (v if isinstance(v, tuple) else (v,)):
        if not (math.isfinite(x) and x > 0):
            raise ValueError(
                f"must be finite and positive (scalar iso or 3-element "
                f"diagonal, per element), got {x!r}")
    return v


def _check_hex_color(v):
    """材料显示色（可选）：``#RRGGBB`` / ``RRGGBB``，归一为带 # 的大写形。

    纯表现层字段（3D 视图按材料上色），求解器不消费；非法值早点报错，
    避免"颜色写错 → 静默回退调色板"的无声偏差（与 _StrictModel 同精神）。"""
    if v is None:
        return v
    s = str(v).strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"color must be '#RRGGBB' or 'RRGGBB', got {v!r}")
    return "#" + s.upper()


def _check_pos(v):
    """omega_0/omega_p > 0（标量；pole 参数已收窄为纯标量）。不查有限：
    本地 pole 路径 VectorPole 构造期已 isfinite 早失败（2026-09-07 批 α）；
    增益=omega_p_sq<0（负振子强度）接受语义（2026-09-07 M2 定案），
    gamma<0 非增益通道、L1 ade_coeffs 硬拒——schema 镜像现值，不发明守卫。"""
    if v is None:
        return v
    if not v > 0:
        raise ValueError(f"must be > 0, got {v!r}")
    return v


def _check_dir(v):
    """alpha/beta 方向向量守卫（镜像内核 `_pole._unit`）：逐元有限、
    norm>0（≈0 → 422「零向量」）、自动归一（|norm−1|>1e-12 时归一；
    归一后内核对单位向量不再告警）。"""
    if v is None:
        return v
    for x in v:
        if not math.isfinite(x):
            raise ValueError(
                f"direction vector elements must be finite, got {x!r}")
    norm = math.hypot(v[0], v[1], v[2])   # G121：hypot 防溢出——手写
    # 平方和在 1e200 量级溢出 inf，归一出 (0,0,0) 静默零向量
    if norm < 1e-15:
        raise ValueError(
            f"direction vector must be non-zero (norm≈0 is a zero vector), "
            f"got {v!r}")
    if abs(norm - 1.0) > 1e-12:
        return (v[0] / norm, v[1] / norm, v[2] / norm)
    return (float(v[0]), float(v[1]), float(v[2]))


class _RangeModel(_StrictModel):
    x_range: Range2
    y_range: Range2
    z_range: Range2

    _v = field_validator("x_range", "y_range", "z_range", mode="after")(_check_range)


def _norm_polarization(v):
    """[1,0,0] 或 [[1,0],[0,0],[0,0]] → 统一存 [[re,im]×3]。"""
    if v is None:
        return [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
    if len(v) != 3:
        raise ValueError("polarization must have exactly 3 components")
    if all(isinstance(x, (int, float)) for x in v):
        return [[float(x), 0.0] for x in v]
    out = []
    for pair in v:
        if len(pair) != 2:
            raise ValueError(
                "polarization complex form must be [re, im] pairs")
        out.append([float(pair[0]), float(pair[1])])
    return out


# ---------------------------------------------------------------------------
# Domain / Mesh
# ---------------------------------------------------------------------------


class BoundarySpec(_StrictModel):
    kind: Literal["pec", "pmc", "periodic", "pml", "bloch"]
    # pml
    layers: int = Field(default=12, ge=2)
    kappa_max: float = Field(default=1.0, ge=1.0)
    order: int = Field(default=3, ge=1)
    # bloch
    k: Optional[Vec3] = None
    from_source: bool = False
    n_background: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "bloch" and self.k is None and not self.from_source:
            raise ValueError(
                "bloch boundary requires k (3-tuple, SI) or "
                "from_source=true")
        return self


class DomainSpec(_RangeModel):
    total_time: float = Field(gt=0)
    cfl: float = Field(default=0.9, gt=0, le=1)
    boundary: dict[str, BoundarySpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_faces(self):
        for face in self.boundary:
            if face not in FACES:
                raise ValueError(
                    f"unknown face {face!r} (expected one of: {FACES})")
        return self


class RefineSpec(_RangeModel):
    factor: Optional[Union[int, list[int]]] = None
    growth_rate: float = Field(default=1.2, ge=1.0)

    @model_validator(mode="after")
    def _check(self):
        if self.factor is None:
            raise ValueError(
                "refine requires factor (an int or [fx, fy, fz], each >= 2)")
        fs = [self.factor] * 3 if isinstance(self.factor, int) else list(self.factor)
        if len(fs) != 3 or any(not isinstance(f, int) or f < 2 for f in fs):
            raise ValueError(
                "factor must be an int >= 2 or a list of 3 ints each >= 2")
        return self


class MeshSpec(_StrictModel):
    dx: float = Field(gt=0)
    dy: Optional[float] = Field(default=None, gt=0)
    dz: Optional[float] = Field(default=None, gt=0)
    refine: list[RefineSpec] = Field(default_factory=list)
    # 细化上限/每轴最少格数（2026-09 Phase2 ①，原 dialect.md:87 挂账）：
    # 守卫镜像本地 Mesh setter（isinstance(int) and >=1），默认同本地
    # 128/10。无量纲，不进资源估算（estimate_cells 只吃 dx/dy/dz/refine）。
    # 注意 max_refinement 查的是实际细化（渐变过渡带可比名义 factor 更细，
    # 见本地 Mesh.refine docstring），守卫在内核 build 期触发。
    max_refinement: int = Field(default=128, ge=1)
    min_cells: int = Field(default=10, ge=1)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


class PoleSpec(_StrictModel):
    """色散极点。两形态（2026-09 pole-direction 提案，站长裁决「三轴
    速记非 pole 形态」只留两档，选项 A 移除 3元 速记）：

    - **iso**：参数全标量、无 alpha/beta → 本地 `_iso_expand` 三轴展开
      （存量语义不变）；
    - **rank-1**：参数全标量 + `alpha`（必）± `beta`（缺省=alpha）→
      单根 VectorPole（lorentz ωp²=Δε·ω₀²；drude ωp²=ω_p²）。

    独立参数的多个 pole 由用户自行叠加。增益=omega_p_sq<0（负振子
    强度/负 Δε，有界稳定，接受语义）；gamma<0（负阻尼）**不是**增益
    通道，L1 ade_coeffs 硬拒——schema 镜像现值不发明守卫
    （2026-09-07 M2 定案，口径同 _check_pos docstring/genie-fdtd docs/GUIDE 陷阱节）。"""

    kind: Literal["lorentz", "drude"]
    omega_0: Optional[float] = None   # rad/s（lorentz；>0）
    omega_p: Optional[float] = None   # rad/s（drude；>0）
    gamma: Optional[float] = None     # rad/s（负阻尼非增益通道——L1 ade_coeffs
                                      #   硬拒, M2 定案；增益走 ωp²<0=负 Δε）
    delta_eps: float = 1.0
    alpha: Optional[Vec3] = None      # rank-1 驱动方向（单位向量，自动归一）
    beta: Optional[Vec3] = None       # rank-1 耦合方向（缺省=alpha）

    _v = field_validator("omega_0", "omega_p", mode="after")(_check_pos)
    _d = field_validator("alpha", "beta", mode="after")(_check_dir)

    @model_validator(mode="after")
    def _check(self):
        if self.gamma is None:
            raise ValueError("pole requires gamma")
        if self.kind == "lorentz" and self.omega_0 is None:
            raise ValueError(
                "lorentz pole requires omega_0 > 0 (use drude for "
                "omega_0=0)")
        if self.kind == "drude" and self.omega_p is None:
            raise ValueError("drude pole requires omega_p > 0")
        # rank-1 守卫：只有 beta 无 alpha → 422（alpha 必填）
        if self.beta is not None and self.alpha is None:
            raise ValueError(
                "rank-1 pole requires alpha (beta without alpha is invalid)")
        return self


class ConstMatSpec(_StrictModel):
    """常数材料。eps/mu：标量（iso，三轴同值——语义不变，存量 payload
    零扰动）| 3 元 ``[xx, yy, zz]``（对角各轴独立）| 3×3 嵌套数组
    （完整张量，非对角对称=互易介质）。守卫镜像本地 _require_positive_
    diagonal：标量/对角逐元有限且 > 0（负 ε/μ 无色散是数值发散源，金属
    走 pole/nk 拟合）；张量逐元有限 + 主对角>0 + 对称（非对角允许负）。

    color：**仅显示用**的可选色值（"#RRGGBB"）。GUI 的 3D 视图按材料上色
    ——同一材料的实体共享一色；缺省由前端按材料名稳定散列取调色板。求解
    器忽略此字段（不进 translate），故它是纯表现层的、可选的、向后兼容的
    扩展。"""

    type: Literal["const"] = "const"
    name: str = Field(min_length=1)
    eps: NumOrTensor = 1.0
    mu: NumOrTensor = 1.0
    color: Optional[str] = None

    _v = field_validator("eps", "mu", mode="after")(_check_finite_pos)
    _vc = field_validator("color", mode="after")(_check_hex_color)


class LorentzMatSpec(_StrictModel):
    """Lorentz/Drude 色散材料。eps_inf/mu_inf 形状语义同 ConstMatSpec
    的 eps/mu（标量 iso | 3 元对角 | 3×3 张量）；poles/mu_poles 逐 pole
    形态见 PoleSpec（iso | rank-1，参数纯标量 ± α/β 方向）。
    color：同 ConstMatSpec.color（仅显示用）。"""

    type: Literal["lorentz"] = "lorentz"
    name: str = Field(min_length=1)
    eps_inf: NumOrTensor = 1.0
    mu_inf: NumOrTensor = 1.0
    poles: list[PoleSpec] = Field(default_factory=list)
    mu_poles: list[PoleSpec] = Field(default_factory=list)
    color: Optional[str] = None

    _v = field_validator("eps_inf", "mu_inf", mode="after")(_check_finite_pos)
    _vc = field_validator("color", mode="after")(_check_hex_color)


class LibraryMatSpec(_StrictModel):
    """裸 from_library 线格式节点 —— **明确拒收**（2026-09 fit 归属迁移
    F2 用户裁决：内核对色散材料只接受 pole 输入，nk 拟合归服务仓）。

    类保留在 union 中（而非删除）是为了让 type="from_library" 的节点
    能被 discriminator 命中并吃到本类的明确指路错误，而不是
    pydantic 的泛化 "no match for discriminator" —— 指路质量是本次
    迁移的用户裁决三定案之一。校验期（model_validate/validate_config
    工具）与作业期（translate）同一双手拒收。
    """

    _REJECT_MSG = (
        "from_library wire-format materials are no longer accepted by the "
        "kernel (2026-09 fit-ownership migration F2): the kernel takes "
        "pole input only for dispersive materials. Use one of: "
        "(1) local fitting via genie.material.fit_nk (the SDK ships the "
        "same n-k library; the former gateway tools face was retired "
        "2026-10-27) and submit the result as a type='lorentz' pole "
        "payload; "
        "(2) the SDK add_from_library sugar (same flow, automated); "
        "(3) submit type='lorentz' pole parameters directly."
    )

    type: Literal["from_library"] = "from_library"
    name: str = Field(min_length=1)                 # 模型注册名
    library: str = Field(min_length=1)              # nk 库文件名，如 "Au"
    fit_range: Range2 = (400e-9, 800e-9)
    n_drude: int = Field(default=0, ge=0)
    n_lorentz: int = Field(default=2, ge=0)
    maxfev: int = Field(default=5000, ge=1)
    quiet: bool = True

    _v = field_validator("fit_range", mode="after")(_check_range)

    @model_validator(mode="after")
    def _reject_from_library(self):
        raise ValueError(LibraryMatSpec._REJECT_MSG)


MaterialSpec = Annotated[
    Union[ConstMatSpec, LorentzMatSpec, LibraryMatSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Geometry（box/sphere/cylinder 为主力；2026-09 方言扩展两批把本地 8 形状
# 全开：+ngon_z/polygon_z（正 n 边形孔阵与任意多边形超表面单元）、
# +ellipse_z/ring_z/polygon_swept（椭圆孔/孔环/斜切扫掠——提案
# kernel_patch_proposals/2026-09-geometry-expansion.md，2026-09 落地；
# 方言与本地 fdtd/api/geometry.py 至此对齐（原 dialect.md 附录 A，已随其退役）
# ---------------------------------------------------------------------------


class _NamedGeo:
    """几何对象的**可选显示名**（2026-10-21 用户定案：树/3D 标签只显示名字）。

    纯表现层字段，求解器不消费（不进 translate）；缺省 None = 由前端回退到
    类型名（"Box" / "N-gon"）。与材料 color 同一性质：可选的、向后兼容的、
    只影响 GUI 读法的扩展，不是物理参数。"""

    name: Optional[str] = None


class BoxSpec(_RangeModel, _NamedGeo):
    type: Literal["box"] = "box"
    material: str = Field(min_length=1)
    priority: int = 0


class SphereSpec(_StrictModel, _NamedGeo):
    type: Literal["sphere"] = "sphere"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    priority: int = 0


class CylinderSpec(_StrictModel, _NamedGeo):
    type: Literal["cylinder"] = "cylinder"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    radius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


class NgonZSpec(_StrictModel, _NamedGeo):
    """正 n 边形（z 轴挤出）。镜像本地 ``add_ngon_z`` 守卫：
    n_sides >= 3（n=2 使 cos(π/2)=0 结构消失）、circumradius/height > 0。"""

    type: Literal["ngon_z"] = "ngon_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    n_sides: int = Field(ge=3)
    circumradius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


def norm_vertices_pairs(vertices):
    """vertices 哨兵归一（polygon_z / polygon_swept 共用）：
    JSON [null, null] 对 → (nan, nan)；坐标须有限。2026-09 几何扩展
    提案落地时自 PolygonZSpec 提出——两形状哨兵语义同一，共享一双手
    禁漂移。子多边形 >=3 顶点等更深校验仍在内核 build 期（同一手）。"""
    out = []
    for x, y in vertices:
        if x is None and y is None:          # JSON 哨兵 → 内核哨兵
            out.append((float("nan"), float("nan")))
        elif x is None or y is None:
            raise ValueError(
                "vertices sentinel must be a complete [null, null] pair "
                "(a single-sided null is invalid)")
        elif not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(
                "vertices coordinates must be finite (use [null, null] "
                "pairs as sentinels)")
        else:
            out.append((float(x), float(y)))
    return out


# 旧私有名兼容（models/__init__ 名字表的历史条目；G2 起 builder 等
# 消费方统一用公共名——polygon 哨兵归一自此单一实现）
_norm_vertices_pairs = norm_vertices_pairs


def _ser_vertices_json(verts):
    """(nan, nan) 内核哨兵 → JSON [null, null]（哨兵 JSON 往返闭合）。
    model_dump(json) 输出 NaN 字面量则 runner 二次 SimConfig 校验必拒
    （2026-10-15 moiré 版图实测）。非哨兵坐标原样。"""
    return [
        [None, None]
        if isinstance(x, float) and math.isnan(x)
        and isinstance(y, float) and math.isnan(y)
        else [x, y]
        for (x, y) in verts
    ]


class PolygonZSpec(_StrictModel, _NamedGeo):
    """任意多边形（z 轴挤出）。``vertices`` 为 (x, y) 列表。

    子多边形分隔哨兵：**JSON 提交用 ``[null, null]`` 对**（JSON 严格
    语法无 NaN 字面量）；validator 归一为 ``(nan, nan)``——与本地 API
    同款语义。Python 侧（SDK/内核）两种写法都接受。schema 层锁形状
    （成对、至少 3 点）；子多边形 >=3 顶点的语义校验在内核 build 期
    （与本地 API 同一手）。"""

    type: Literal["polygon_z"] = "polygon_z"
    material: str = Field(min_length=1)
    vertices: list[tuple[Optional[float], Optional[float]]] = Field(min_length=3)
    z_range: Range2
    priority: int = 0

    @field_serializer("vertices")
    def _ser_vertices_json_(self, verts):
        # (nan, nan) 内核哨兵 → JSON [null, null]（哨兵 JSON 往返闭合，
        # 2026-10-15 moiré 版图实测 NaN 字面量会被 runner 二次校验拒绝）
        return _ser_vertices_json(verts)

    _v = field_validator("z_range", mode="after")(_check_range)

    @model_validator(mode="after")
    def _norm_vertices(self):
        object.__setattr__(
            self, "vertices", norm_vertices_pairs(self.vertices))
        return self


class EllipseZSpec(_StrictModel, _NamedGeo):
    """椭圆（z 轴挤出）。镜像本地 ``add_ellipse_z`` 守卫
    （_validate_structure 的 _pos 三连）：rx/ry/height > 0。"""

    type: Literal["ellipse_z"] = "ellipse_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    rx: float = Field(gt=0)
    ry: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0


class RingZSpec(_StrictModel, _NamedGeo):
    """圆环（z 轴挤出）。inner_radius=0 合法（实心盘）；
    inner < outer（本地 _build_structures 同款语义——反环静默光栅化成
    空掩膜、结构消失零告警，schema 先挡形状错）。"""

    type: Literal["ring_z"] = "ring_z"
    material: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    inner_radius: float = Field(ge=0)
    outer_radius: float = Field(gt=0)
    height: float = Field(gt=0)
    priority: int = 0

    @model_validator(mode="after")
    def _check_radii(self):
        if self.inner_radius >= self.outer_radius:
            raise ValueError(
                "inner_radius must be < outer_radius (0 = solid disk)")
        return self


class PolygonSweptSpec(_StrictModel, _NamedGeo):
    """带斜切（shear）的 z 轴挤出多边形。vertices 哨兵语义同 polygon_z
    （共享 norm_vertices_pairs）。tilt=(dx_dz, dy_dz)：截面随
    (z - z_center) 平移的斜率；每分量 |v| <= 1000（本地
    _TILT_SLOPE_MAX 同款——浮点 tan(90°)=1.6e16 是有限值，有限性防线
    拦不住，结构曾静默消失在域外）。tilt_deg 为本地便捷换算
    （含 89.9° 守卫），不进线格式。"""

    type: Literal["polygon_swept"] = "polygon_swept"
    material: str = Field(min_length=1)
    vertices: list[tuple[Optional[float], Optional[float]]] = Field(min_length=3)
    z_range: Range2
    tilt: Optional[tuple[float, float]] = None        # None → 不斜切
    priority: int = 0

    @field_serializer("vertices")
    def _ser_vertices_json_swept(self, verts):
        return _ser_vertices_json(verts)

    _v = field_validator("z_range", mode="after")(_check_range)

    @field_validator("tilt", mode="after")
    @classmethod
    def _check_tilt(cls, v):
        if v is None:
            return v
        if not (math.isfinite(v[0]) and math.isfinite(v[1])):
            raise ValueError("tilt components must be finite")
        if abs(v[0]) > 1000.0 or abs(v[1]) > 1000.0:
            raise ValueError(
                "tilt slope components must satisfy |v| <= 1000 "
                "(large tilts push the cross-section out of the domain)")
        return (float(v[0]), float(v[1]))

    @model_validator(mode="after")
    def _norm_vertices(self):
        object.__setattr__(
            self, "vertices", norm_vertices_pairs(self.vertices))
        return self


GeometrySpec = Annotated[
    Union[BoxSpec, SphereSpec, CylinderSpec, NgonZSpec, PolygonZSpec,
          EllipseZSpec, RingZSpec, PolygonSweptSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class _TemporalMixin(_StrictModel):
    freq: float = Field(gt=0)
    bandwidth: float = Field(gt=0)
    delay: float = 0.0
    phase: float = 0.0
    name: Optional[str] = None


class SoftSourceSpec(_TemporalMixin):
    type: Literal["soft_source"] = "soft_source"
    center: Vec3 = (0.0, 0.0, 0.0)
    size: Vec3 = (0.0, 0.0, 0.0)          # 各槽 ≤0 = 单格
    # 默认 [[1,0],[0,0],[0,0]]（x̂ 线偏振）；JSON 显式 null 也归一到此
    polarization: Optional[Union[list[float], list[list[float]]]] = [
        [1.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
    amplitude: float = 1.0
    kind: Literal["electric", "magnetic"] = "electric"

    _p = field_validator("polarization", mode="after")(_norm_polarization)


class PlaneWaveSpec(_TemporalMixin):
    type: Literal["plane_wave"] = "plane_wave"
    direction: Literal["+", "-"] = "+"
    amplitude: float = 1.0
    center: Optional[Vec3] = None
    theta: float = Field(default=0.0, ge=0, le=180)   # 度
    phi: float = Field(default=0.0, ge=0, lt=360)     # 度
    psi: float = Field(default=0.0, ge=0, lt=360)     # 度
    # 三槽为全局 (x,y,z) 横向窗；null/≤0 = 非横向窗槽；传播轴 = 恰一非正槽（默认 z）
    size: Optional[tuple[Optional[float], Optional[float], Optional[float]]] = None
    propagation_axis: Optional[Literal["x", "y", "z"]] = None
    from_face: Optional[Literal["x_min", "x_max", "y_min", "y_max",
                                "z_min", "z_max"]] = None


class GaussianBeamSpec(PlaneWaveSpec):
    """高斯束 = 平面波全参数 + 横向高斯包络。radius 为源平面孔径半径（m），
    对应 1/e² 强度束宽 w ≈ 0.47·radius（想要束宽 w 就给 radius ≈ 2.12·w）。"""

    type: Literal["gaussian_beam"] = "gaussian_beam"
    radius: float = Field(gt=0)


SourceSpec = Annotated[
    Union[SoftSourceSpec, PlaneWaveSpec, GaussianBeamSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class DownSampleSpec(_StrictModel):
    time_window: Optional[Range2] = None
    stride: int = Field(default=1, ge=1)

    @field_validator("time_window", mode="after")
    @classmethod
    def _win(cls, v):
        if v is not None:
            _check_range(v)
        return v


class ApodizeSpec(_StrictModel):
    side: Optional[Literal["start", "stop", "both"]] = None
    time_window: Optional[Range2] = None
    width: Optional[float] = Field(default=None, gt=0)

    # G120（R16）：同名字段与 DownSampleSpec.time_window 同一双守卫
    # （此前 apodize 反序窗 [hi,lo] 静默放行、down_sample 同输入被拒）。
    @field_validator("time_window", mode="after")
    @classmethod
    def _win(cls, v):
        if v is not None:
            _check_range(v)
        return v


class _MonitorMixin(_StrictModel):
    name: str = Field(min_length=1)
    center: Vec3 = (0.0, 0.0, 0.0)
    # time/freq 默认 [0,0,0] 点监视器（null 槽 = 全域 span——体积守卫会先拦住滥用）
    size: tuple[Optional[float], Optional[float], Optional[float]] = (0.0, 0.0, 0.0)
    field: str
    down_sample: Optional[DownSampleSpec] = None


class TimeMonSpec(_MonitorMixin):
    type: Literal["time"] = "time"

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _FIELD_SET:
            raise ValueError(f"field {v!r} not in {_FIELD_SET}")
        return v


class FreqMonSpec(_MonitorMixin):
    type: Literal["freq"] = "freq"
    freq: dict[str, float]  # {"center": Hz, "bw": Hz, "n": 点数}
    apodize: Optional[ApodizeSpec] = None

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _FIELD_SET:
            raise ValueError(f"field {v!r} not in {_FIELD_SET}")
        return v

    @field_validator("freq", mode="before")
    @classmethod
    def _fr_shape(cls, v):
        # 常见 type-mismatch 策展（2026-10 站长裁决：错误消息必须可操作）：
        # freq 监视器传列表/位置序列时，pydantic 泛化的
        # "Input should be a valid dictionary" 不指路——这里明说三个命名参数。
        if not isinstance(v, dict):
            raise ValueError(
                "freq: freq monitors require three named params "
                "(center [Hz], bw [Hz], n [points]) as a dict, e.g. "
                '{"center": 300e12, "bw": 120e12, "n": 200}; got '
                f"{type(v).__name__}: {v!r}")
        return v

    @field_validator("freq", mode="after")
    @classmethod
    def _fr(cls, v):
        # G119（R16）：拼错键（n_points 之类）此前被重建 dict 静默丢弃、
        # n 落默认 100——正是 _StrictModel extra=forbid 要消灭的静默面。
        unknown = set(v) - {"center", "bw", "n"}
        if unknown:
            raise ValueError(
                f"freq: unknown key(s) {sorted(unknown)} — freq takes "
                "exactly (center [Hz], bw [Hz], n [points])")
        center = v.get("center")
        bw = v.get("bw", 0.0)
        n = int(v.get("n", 100))
        if center is None or center <= 0:
            raise ValueError("freq.center must be > 0 (Hz)")
        if bw < 0:
            raise ValueError("freq.bw must be >= 0")
        if n < 1:
            raise ValueError("freq.n must be >= 1")
        if center - bw / 2 < 0:
            raise ValueError("freq.center - bw/2 must be >= 0")
        return {"center": float(center), "bw": float(bw), "n": n}


class IndexMonSpec(_MonitorMixin):
    type: Literal["index"] = "index"
    # index 默认全域（null 槽 = 全域 span）
    size: tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)

    @field_validator("field", mode="after")
    @classmethod
    def _f(cls, v):
        if v not in _INDEX_FIELD_SET:
            raise ValueError(f"field {v!r} not in {_INDEX_FIELD_SET}")
        return v


MonitorSpec = Annotated[
    Union[TimeMonSpec, FreqMonSpec, IndexMonSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Run 与总配置
# ---------------------------------------------------------------------------


# run 节（G18 2026-10-16 站长裁决，方案 A 单旋钮）：**唯一时间定义 =
# domain.total_time**（SI 秒，gt=0 必填，见 DomainSpec）——作业推进到
# 网格时间容量为止，步数是 build 后的派生量（summary/results 只读面）。
# run 节本身只剩服务字段：gpu = 卡数（1..4，G16）、timeout_s = 作业
# 超时。旧 run.n_steps（实际推进步数第二旋钮）退役——容量/步数双旋钮
# 语义混乱且 n_steps>容量 必撞 apodization 截断，v2 未发布无兼容包袱。


class RunSpec(_StrictModel):
    gpu: Optional[Annotated[int, Field(ge=1, le=4)]] = None  # 卡数 1..4
    timeout_s: int = Field(default=DEFAULT_TIMEOUT_S, gt=0)


class SimConfig(_StrictModel):
    domain: DomainSpec
    mesh: MeshSpec
    materials: list[MaterialSpec] = Field(default_factory=list)
    geometry: list[GeometrySpec] = Field(default_factory=list)
    source: list[SourceSpec] = Field(min_length=1)
    monitor: list[MonitorSpec] = Field(default_factory=list)
    run: RunSpec
    # 全局参数节（参数系统批 1，2026-10 用户裁决全量开放）：键=标识符
    # （^[A-Za-z_][A-Za-z0-9_]*$，≤64 字符），值=数值或引用其余参数的
    # 表达式字符串（环境=其余参数，顶层拓扑求值）。表达式求值与数值槽
    # 替换不在模型期做——见 resolve_parameters（两段校验的唯一解析点
    # parse_config）；sweep 轴（SweepAxisSpec.name）按名引用这里的键。
    parameters: Optional[dict[str, Union[float, int, str]]] = None

    @field_validator("parameters", mode="after")
    @classmethod
    def _check_param_names(cls, v):
        if v is None:
            return v
        for k in v:
            if not valid_param_name(k):
                raise ValueError(
                    f"parameter name {k!r} is invalid: must be an "
                    f"identifier (starting with a letter or underscore, "
                    f"letters/digits/underscores only, <= 64 chars) — it "
                    f"is referenced by expression slots in numeric fields "
                    f"and by sweep axes (parameters[name])")
        return v

    @model_validator(mode="after")
    def _check(self):
        # 同类 monitor 重名（L4 同款守卫在 build 期，这里提前给友好错误）
        for mtype in ("time", "freq", "index"):
            names = [m.name for m in self.monitor if m.type == mtype]
            if len(names) != len(set(names)):
                dups = sorted({n for n in names if names.count(n) > 1})
                raise ValueError(
                    f"duplicate {mtype} monitor names: {dups} (monitor "
                    f"names must be unique within each type)")
        # geometry 显示名唯一（2026-10-21 用户定案）：名字是树/3D 里的身份，
        # 允许重名就等于允许两行同名对象——找不到、删错一个都是实际代价。
        # 未命名（None）不参与查重：多个匿名对象是合法工作流。
        seen = {}
        for i, g in enumerate(self.geometry):
            n = getattr(g, "name", None)
            if not n:
                continue
            if n in seen:
                raise ValueError(
                    f"duplicate geometry name: {n!r} (objects {seen[n]} "
                    f"and {i}) — names identify objects in the tree/3D "
                    f"view and must be unique")
            seen[n] = i
        # geometry 引用的材料必须存在（build 期同款守卫提前）
        known = {m.name for m in self.materials}
        for g in self.geometry:
            if g.material not in known:
                raise ValueError(
                    f"geometry references undefined material "
                    f"{g.material!r} (expected a name from materials[]: "
                    f"{sorted(known) if known else 'none defined'})")
        # periodic/bloch 必须同轴成对（显式设置冲突时提前报错）
        b = self.domain.boundary
        pairs = (("x_min", "x_max"), ("y_min", "y_max"), ("z_min", "z_max"))
        for f_lo, f_hi in pairs:
            for a, other in ((f_lo, f_hi), (f_hi, f_lo)):
                if a in b and b[a].kind in ("periodic", "bloch"):
                    ok = (other in b and b[other].kind in ("periodic", "bloch"))
                    if not ok:
                        raise ValueError(
                            f"boundary {a}={b[a].kind} must be paired with "
                            f"the opposite face {other} set to periodic/"
                            f"bloch (same-axis pairs only)"
                        )
        return self


# ---------------------------------------------------------------------------
# Sweep（参数扫描线格式 + 展开入口，P2-α 2026-09；调度归 P2-β）
# ---------------------------------------------------------------------------

class SweepAxisSpec(_StrictModel):
    """单参数扫描轴（参数系统批 1 2026-10 破坏性改版：``path`` 寻址
    退役，改为 **参数名寻址**——功能上线一天无存量用户，用户裁决）。

    - ``name``：**参数名**——必须是基底配置 ``parameters`` 键集内的
      名（不在 → 422 列可用名）；展开语义 = 逐点 ``parameters[name]=v``
      后重解析（引用该参数的全部表达式槽逐点联动重求值）；
    - 取值三选一互斥：``values``（显式列表，str 支持枚举型）|
      ``linspace`` = [start, stop, n]（数值，含端点，n>=2）|
      ``logspace`` = [start, stop, n]（10^x，同规则）；
      多给/全缺 → 422 策展错误指路；``linspace/logspace`` 的
      start==stop 拒收（单值轴请直接 values=[x]——退化区间是笔误的
      温床，宁拒不放行）。
    展开与逐点校验入口见 ``fdtd/serve/sweep.py``。
    """

    name: str = Field(min_length=1, max_length=64)
    values: Optional[list[Union[float, int, str, list[float]]]] = None
    linspace: Optional[tuple[float, float, int]] = None
    logspace: Optional[tuple[float, float, int]] = None

    @field_validator("name", mode="after")
    @classmethod
    def _check_name(cls, v):
        if not valid_param_name(v):
            raise ValueError(
                f"axis name {v!r} is invalid: must be a parameter name "
                f"(identifier, <= 64 chars) that exists in the base "
                f"config's parameters keys")
        return v

    @model_validator(mode="after")
    def _check(self):
        given = [x for x in (self.values, self.linspace, self.logspace)
                 if x is not None]
        if len(given) != 1:
            raise ValueError(
                "exactly one of values / linspace / logspace must be "
                f"given (got {len(given)})")
        for name in ("linspace", "logspace"):
            sp = getattr(self, name)
            if sp is None:
                continue
            start, stop, n = sp
            if not (math.isfinite(start) and math.isfinite(stop)):
                raise ValueError(
                    f"{name} start/stop must be finite, got {sp[:2]!r}")
            if n < 2:
                raise ValueError(f"{name}.n must be an integer >= 2, got {n}")
            if start == stop:
                raise ValueError(
                    f"{name} start==stop=={start} is rejected "
                    f"(use values=[x] for a single-value axis)")
        if self.values is not None and len(self.values) == 0:
            raise ValueError(
                "values must be non-empty (a single-element list [x] is "
                "valid)")
        return self

    def value_list(self) -> list:
        """该轴的取值序列（values 原样；linspace/logspace 按公式展开）。"""
        if self.values is not None:
            return list(self.values)
        start, stop, n = self.linspace if self.linspace else self.logspace
        if self.linspace is not None:
            return [start + i * (stop - start) / (n - 1)
                    for i in range(n)]
        return [10 ** (start + i * (stop - start) / (n - 1))
                for i in range(n)]


class SweepSpec(_StrictModel):
    """顶层参数扫描描述（P2-α：纯线格式 + 展开面，不含调度）。

    - ``parameters``：1..32 轴（SweepAxisSpec）；
    - ``mode``：``product``（默认，笛卡尔积）| ``zip``（各轴等长逐点
      配对，长度不等 → 422）；
    - ``gpus_per_sim``：单点仿真占用卡数（1..8；>=2 调度要求 NV
      邻接链——同 run.gpu {"count": k} 语义，归 P2-β 调度门）；
    - ``max_parallel``：并行点数上限（1..16，调度层提示性字段）；
    - ``on_failure``：``continue``（默认）| ``abort``；
    - 展开点数上限 ``FDTD_MAX_SWEEP_POINTS``（默认 512，超限 422
      带点数/上限）。
    """

    parameters: list[SweepAxisSpec] = Field(min_length=1,
                                            max_length=MAX_SWEEP_AXES)
    mode: Literal["product", "zip"] = "product"
    gpus_per_sim: int = Field(default=1, ge=1, le=8)
    max_parallel: int = Field(default=1, ge=1, le=16)
    on_failure: Literal["continue", "abort"] = "continue"

    @model_validator(mode="after")
    def _check(self):
        lens = [len(a.value_list()) for a in self.parameters]
        if self.mode == "zip":
            if len(set(lens)) != 1:
                raise ValueError(
                    f"zip mode requires all axes to have equal lengths, "
                    f"got {lens}")
            n_points = lens[0] if lens else 0
        else:
            n_points = 1
            for L in lens:
                n_points *= L
        if n_points > MAX_SWEEP_POINTS:
            raise ValueError(
                f"expanded point count {n_points} exceeds the limit "
                f"{MAX_SWEEP_POINTS} (axis lengths {lens}; reduce the "
                f"number of points, or ask the admin to raise "
                f"FDTD_MAX_SWEEP_POINTS via the environment variable)")
        return self
