"""Builder 层 — 内部 fdtd 包 API 的云端镜像（配置收集，零计算）。

framework §四 终态结构：本模块 = ``core/builder.py``（阶段 ④ 自 _config.py
前半拆出，纯移动）。Simulation 六命名空间与链式工厂在此；Simulation 主体
（装载/提交/生命周期）在 ``core/simulate.py``。

线格式纪律（framework §三/§四，2026-10 心脏置换定案）：
- 构造期赋值不逐次校验（validate_assignment 关）——链式写 construct 出的
  子 Spec，setattr 无类型灌注，值原样落字段；完整性在提交边界一次性校验
  （simulate._validate_for_submit）；
- 未知链式方法即刻 ValueError（extra=forbid），不再静默等服务端 422；
- 表达式串一律原样存储（求值只在服务端；本地预检在旁路 resolve 副本上做）。
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from ..models import (
    BoxSpec,
    ConstMatSpec,
    CylinderSpec,
    DomainSpec,
    EllipseZSpec,
    GaussianBeamSpec,
    IndexMonSpec,
    LorentzMatSpec,
    MeshSpec,
    NgonZSpec,
    PlaneWaveSpec,
    PolygonSweptSpec,
    PolygonZSpec,
    RingZSpec,
    SoftSourceSpec,
    SourceSpec,
    SphereSpec,
    TimeMonSpec,
    FreqMonSpec,
)

# 线格式常量正典 = models._wire（G2 归一：此前本地手抄两份已漂移面）
from ..models import (BC_KINDS as BOUNDARY_KINDS, FACES,
                      LIBRARY_FIT_DEFAULTS, RefineSpec)


# ---------------------------------------------------------------------------
# 标量规范化（Python 值 → JSON 可序列化值）
# ---------------------------------------------------------------------------

def _num(v: Any) -> Any:
    """数值槽：int/float 直转；bool 排除（isinstance(bool, int) 陷阱）；
    **str 原样返回**——表达式串/单位串（``"20[nm]"``）/hex 都是服务端
    求值域的原文，SDK 零计算不解析（模块头注「表达式槽」）。

    返回类型不恒 float：str 槽位原样，故调用侧不得对结果做算术或大小
    比较（值守卫留给服务端求值后重做）。
    """
    if isinstance(v, str):
        return v
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(
            f"expected a number or an expression string, got "
            f"{type(v).__name__}: {v!r}")
    return float(v)


def _int_slot(v: Any, slot: str) -> Any:
    """整数槽：int 原样；**str 原样上行**（表达式串——求值后整数性由
    服务端裁）；float/bool/其余拒绝。

    与 _int_prop 同一口径，供 refine factor / down_sample stride /
    monitor freq.n 等非 property 的整数槽复用。
    """
    if isinstance(v, str):
        return v
    if isinstance(v, bool) or not isinstance(v, int):
        # 静默 int(3.7)→3 截断会带错误数量语义入配置（min_cells/
        # max_refinement 是整数槽）——在写入口拒，指路传 int。
        raise TypeError(
            f"{slot}: integer slot (expected: int, got: "
            f"{type(v).__name__}: {v!r}) — pass an int")
    return v


def _scalar(v: Any) -> Any:
    """通用槽：bool → bool，数值 → 数值，str → str，其余报错。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v) if isinstance(v, float) else int(v)
    if isinstance(v, str):
        return v
    raise TypeError(
        f"unsupported config value type {type(v).__name__}: {v!r}")


def _num_or_tensor(v: Any):
    """标量 | 3 元对角 | 3×3 张量（eps 系用；值即形态，2026-09 全开）。"""
    if isinstance(v, (list, tuple)):
        if not v:
            raise ValueError(
                "diagonal input must have 3 elements, got 0 (an empty "
                "list is not a valid form)")
        vals = [_num(x) for x in v] if not (
            isinstance(v[0], (list, tuple))) else None
        if vals is not None:
            if len(vals) != 3:
                raise ValueError(
                    f"diagonal input must have 3 elements, got {len(vals)}")
            return vals
        if len(v) != 3:
            raise ValueError(f"tensor must be 3x3, got {len(v)} rows")
        out = []
        for row in v:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise ValueError(
                    f"tensor rows must have 3 elements, got row {row!r}")
            out.append([_num(x) for x in row])
        return out
    return _num(v)


def _value(v: Any):
    """通用槽 + 张量：标量/str 走 _scalar，数值列表走对角/3×3 张量。"""
    try:
        return _scalar(v)
    except TypeError:
        return _num_or_tensor(v)


def _cplx(v: Any) -> list[float]:
    """复数分量 → [re, im]（SimConfig 的 polarization 线格式）。"""
    if isinstance(v, complex):
        return [v.real, v.imag]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(
            f"polarization component must be numeric or complex, got {v!r}")
    return [float(v), 0.0]


# ---------------------------------------------------------------------------
# Domain / Mesh
# ---------------------------------------------------------------------------

def _range_prop(key: str):
    return property(
        lambda self: getattr(self._d, key, None),
        lambda self, v: setattr(self._d, key, [_num(v[0]), _num(v[1])]),
    )


def _float_prop(key: str):
    return property(
        lambda self: getattr(self._d, key, None),
        lambda self, v: setattr(self._d, key, _num(v)),
    )


def _int_prop(key: str):
    def setter(self, v):
        setattr(self._d, key, _int_slot(v, key))
    return property(
        lambda self: getattr(self._d, key, None),
        setter,
    )


class _SealedProps:
    """属性命名空间守卫（G83 清账 2026-10-27）。

    Domain/Mesh 的合法槽全部是类级 property（data descriptor）；拼错的
    名字（clf / min_cell / dxx）在类上不存在，赋值会落进实例 __dict__
    变成无人读取的影子属性——配置静默回落默认/回退值，恰好是改网格/
    稳定性时最常手打错的那组槽。链式方法早有严格守卫（`_Chain` 未知
    方法即刻 ValueError，pydantic extra=forbid），这里给属性命名空间
    同款纪律：非下划线、非已声明槽的赋值**当场 AttributeError**（带
    最近名提示 + 可设槽位清单），提交前校验看不出来成为历史。
    """

    def __setattr__(self, name, value):
        cls = type(self)
        if name.startswith("_") or isinstance(vars(cls).get(name), property):
            object.__setattr__(self, name, value)
            return
        import difflib
        props = sorted(n for n, v in vars(cls).items()
                       if isinstance(v, property))
        hint = difflib.get_close_matches(name, props, n=1)
        raise AttributeError(
            f"{cls.__name__} has no attribute {name!r}"
            + (f" (did you mean {hint[0]!r}?)" if hint else "")
            + f"；可设槽位：{', '.join(props)}")


class Domain(_SealedProps):
    """物理域 + 边界 + 总时长（SI）。setter 与 boundary_condition 同本地包。"""

    x_range = _range_prop("x_range")
    y_range = _range_prop("y_range")
    z_range = _range_prop("z_range")
    total_time = _float_prop("total_time")
    cfl = _float_prop("cfl")

    def __init__(self, store: DomainSpec):
        self._d = store

    def boundary_condition(self, face: str, kind: str, **kw) -> "Domain":
        """face∈FACES；kind∈{pec,pmc,periodic,pml,bloch}；
        pml: layers/kappa_max/order；bloch: k=(kx,ky,kz)/from_source/n_background。"""
        if face not in FACES:
            raise ValueError(
                f"unknown face {face!r} (expected one of: {FACES})")
        if kind not in BOUNDARY_KINDS:
            raise ValueError(
                f"unknown boundary kind {kind!r} "
                f"(expected one of: {BOUNDARY_KINDS})")
        # boundary_condition 的 kwargs 未知即拒（G129/R17：此前拼错名
        # 如 layer=8 被静默丢弃——链式方法有 extra=forbid 守卫，kwargs
        # 这条道没有；与模块头"未知即 ValueError"口径对齐）。
        d: dict[str, Any] = {"kind": kind}
        if kind == "pml":
            for k in ("layers", "kappa_max", "order"):
                if kw.get(k) is not None:
                    d[k] = _scalar(kw.pop(k))
        elif kind == "bloch":
            if kw.get("k") is not None:
                d["k"] = [_num(x) for x in kw.pop("k")]
            if kw.pop("from_source", False):
                d["from_source"] = True
            if kw.get("n_background") is not None:
                d["n_background"] = _num(kw.pop("n_background"))
        if kw:
            raise TypeError(
                f"boundary_condition: unknown kwargs {sorted(kw)} for "
                f"kind={kind!r} (accepted: layers/kappa_max/order for pml; "
                "k/from_source/n_background for bloch; none for "
                "pec/pmc/periodic)")
        # boundary 字段类型 dict[str, BoundarySpec]——条目存裸 dict（构造期
        # 不校验），提交边界统一过模型
        b = getattr(self._d, "boundary", None)
        if not isinstance(b, dict):
            b = {}
        b[face] = d
        setattr(self._d, "boundary", b)
        return self


class Mesh(_SealedProps):
    """名义格距 + refine 加密区。dy/dz 未设时服务端回退 dx（同本地包）。"""

    dx = _float_prop("dx")
    dy = _float_prop("dy")
    dz = _float_prop("dz")
    max_refinement = _int_prop("max_refinement")   # 2026-09 方言开放
    min_cells = _int_prop("min_cells")

    def __init__(self, store: MeshSpec):
        self._d = store

    def refine(self, x_range, y_range, z_range, factor=None,
               growth_rate: float = RefineSpec.model_fields["growth_rate"]
               .default) -> "Mesh":
        # growth_rate 省略判据 = 协议 Field 默认（G99：此前字面量 1.2
        # 双写于渲染器与这里，协议一改即静默漂移）。
        if factor is None:
            raise ValueError(
                "refine requires factor (an int >= 2, [fx, fy, fz] with "
                "each >= 2, or an expression string)")
        # G129（R17）：factor 是整数槽——走 _int_slot（str 表达式原样
        # 上行、float/bool 拒收），不再裸 int() 静默截断（2.9→2、
        # True→1 与 _int_slot 自宣纪律矛盾）。
        if isinstance(factor, str):
            fac: Any = factor          # 表达式串原样上行（服务端求值）
        elif isinstance(factor, (list, tuple)):
            fac = [f if isinstance(f, str) else _int_slot(f, "refine.factor")
                   for f in factor]
        else:
            fac = _int_slot(factor, "refine.factor")
        item: dict[str, Any] = {
            "x_range": [_num(x_range[0]), _num(x_range[1])],
            "y_range": [_num(y_range[0]), _num(y_range[1])],
            "z_range": [_num(z_range[0]), _num(z_range[1])],
            "factor": fac,
        }
        if growth_rate != RefineSpec.model_fields["growth_rate"].default:
            item["growth_rate"] = _num(growth_rate)
        lst = getattr(self._d, "refine", None)
        if not isinstance(lst, list):
            lst = []
            setattr(self._d, "refine", lst)
        lst.append(item)
        return self

    def clear_refinements(self) -> "Mesh":
        setattr(self._d, "refine", [])
        return self


# ---------------------------------------------------------------------------
# 通用链式 builder（fdtd 手感的核心：.name('x').eps(2) … 返回自身）
# ---------------------------------------------------------------------------

class _Chain:
    """一次 add_* 调用对应的配置节点（construct 出的子 Spec，未校验）。

    未识别的方法名一律视为「设一个字段、返回自身」——与 fdtd builder 的
    鸭子类型行为一致；**字段必须存在于线格式模型**（extra=forbid：拼错
    名字即刻 ValueError，不再静默记录等服务端 422——framework §三 强类型
    纪律，脚本执行面带行号直接暴露）。构造期不校验（validate_assignment
    关），完整性在提交边界一次性过 ``_validate_for_submit``。
    """

    _TUPLE2 = ("x_range", "y_range", "z_range", "fit_range", "tilt")
    _TUPLE3 = ("center", "size", "k")
    _POLE_EPS = ("add_eps_lorentz_pole", "add_eps_drude_pole")
    _POLE_MU = ("add_mu_lorentz_pole", "add_mu_drude_pole")

    def __init__(self, item: BaseModel, freq_style: str | None = None):
        self.__dict__["_item"] = item
        self.__dict__["_freq_style"] = freq_style

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        item = self.__dict__["_item"]

        def call(*args, **kw):
            if name in self._TUPLE2:
                if len(args) != 2:
                    raise TypeError(f"{name}(a, b) requires 2 arguments")
                setattr(item, name, [_num(args[0]), _num(args[1])])
            elif name in self._TUPLE3:
                if len(args) != 3:
                    raise TypeError(
                        f"{name}(a, b, c) requires 3 arguments "
                        f"(None slot = full span)")
                setattr(item, name,
                        [None if a is None else _num(a) for a in args])
            elif name == "polarization":
                if len(args) != 3:
                    raise TypeError(
                        "polarization(px, py, pz) requires 3 arguments")
                setattr(item, name, [_cplx(a) for a in args])
            elif name == "vertices":
                # 多边形顶点 [(x, y), ...]——单参数列表原样入 cfg；
                # 哨兵归一/有限性校验 = models.norm_vertices_pairs（G2
                # 起单一实现；JSON 的 [null, null] 与 Python 的
                # (None, None)/(nan, nan) 对在此统一）
                if len(args) != 1 or not isinstance(args[0], (list, tuple)):
                    raise TypeError(
                        "vertices([(x, y), ...]) requires a single list "
                        "of vertices")
                from ..models import norm_vertices_pairs
                setattr(item, name,
                        [list(p) for p in norm_vertices_pairs(args[0])])
            elif name in self._POLE_EPS or name in self._POLE_MU:
                pole = self._make_pole(
                    name, args,
                    alpha=kw.pop("alpha", None), beta=kw.pop("beta", None))
                if kw:
                    raise TypeError(
                        f"{name}: unsupported kwargs {sorted(kw)} "
                        "(3-component poles were removed — use iso "
                        "scalars or rank-1 alpha/beta)")
                lst_key = "poles" if name in self._POLE_EPS else "mu_poles"
                lst = getattr(item, lst_key, None)
                if not isinstance(lst, list):
                    lst = []
                    setattr(item, lst_key, lst)
                lst.append(pole)
            elif name == "down_sample":
                if len(args) > 2:      # G129：超参不静默丢
                    raise TypeError(
                        "down_sample(time_window, stride) takes at most 2 "
                        f"arguments, got {len(args)}")
                win, stride = (args + (None, 1))[:2] if len(args) < 2 else args[:2]
                d: dict[str, Any] = {}
                if win is not None:
                    d["time_window"] = [_num(win[0]), _num(win[1])]
                d["stride"] = _int_slot(stride, "down_sample.stride")
                setattr(item, "down_sample", d)
            elif name == "apodize":
                if len(args) > 3:      # G129：超参不静默丢
                    raise TypeError(
                        "apodize(side, time_window, width) takes at most 3 "
                        f"arguments, got {len(args)}")
                side = args[0] if args else None
                win = args[1] if len(args) > 1 else None
                width = args[2] if len(args) > 2 else None
                d = {}
                if side is not None:
                    d["side"] = side
                if win is not None:
                    d["time_window"] = [_num(win[0]), _num(win[1])]
                if width is not None:
                    d["width"] = _num(width)
                setattr(item, "apodize", d)
            elif name == "freq" and self.__dict__["_freq_style"] == "monitor":
                if len(args) != 3:
                    raise TypeError(
                        "freq(center, bw, n) requires 3 arguments "
                        "(Hz, Hz, point count)")
                setattr(item, "freq", {"center": _num(args[0]),
                                       "bw": _num(args[1]),
                                       "n": _int_slot(args[2], "freq.n")})
            else:
                if kw:
                    cur = getattr(item, name, None)
                    if not isinstance(cur, dict):
                        if cur is not None:
                            raise TypeError(
                                f"{name} already has a non-dict value "
                                f"{cur!r}; cannot merge kwargs")
                        cur = {}
                    cur.update({k: _scalar(v) for k, v in kw.items()})
                    setattr(item, name, cur)
                elif not args:
                    setattr(item, name, True)      # .quiet() 式无参调用
                elif len(args) == 1:
                    setattr(item, name, _value(args[0]))
                else:
                    setattr(item, name, [_scalar(a) for a in args])
            return self

        return call

    @staticmethod
    def _dir3(v, tag):
        """rank-1 方向向量：3 元（有限数值 **或表达式串**，服务端自动归一）。

        表达式串原样上行——零向量/有限性守卫对串不做本地判定，服务端
        求值后重做同一守卫（实证：解析后为零向量的 alpha 被 422 拒）。
        """
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise TypeError(f"{tag} must be [x, y, z] (3 elements)")
        out = []
        for x in v:
            if isinstance(x, str):
                out.append(x)
                continue
            if isinstance(x, bool) or not isinstance(x, (int, float)) \
                    or not math.isfinite(x):
                raise TypeError(
                    f"{tag} components must be finite numbers or "
                    f"expression strings, got {x!r}")
            out.append(float(x))
        if all(not isinstance(x, str) for x in out) \
                and math.hypot(*out) < 1e-15:
            # R22 G158：与线格式 _check_dir 同一口径（hypot 范数 < 1e-15，
            # _wire.py:253）。此前逐分量 1e-12——[1e-13]×3 builder 拒、
            # 协议收，同一合法输入两入口判定相反。
            raise ValueError(
                f"{tag} cannot be a zero vector (rank-1 needs a direction)")
        return out

    @staticmethod
    def _make_pole(name: str, args, alpha=None, beta=None) -> dict:
        """标量 iso 极点；alpha/beta 给定时 = rank-1（单 VectorPole）。

        3元 pole 参数已随内核 1.4.0 移除——需要逐轴独立参数请叠多个
        iso/rank-1 pole（UI 旧速记有「拆成三个」迁移）。
        """
        def sc(v, tag):
            if isinstance(v, (list, tuple)):
                raise TypeError(
                    f"{tag} received a 3-element list — 3-component "
                    f"poles were removed (kernel 1.4.0); use iso "
                    f"scalars or rank-1 (alpha/beta)")
            return _num(v)

        drude = "drude" in name
        if drude:
            if len(args) < 2:
                raise TypeError(f"{name}(omega_p, gamma) requires 2 arguments")
            omega_p = sc(args[0], "omega_p")
            gamma = sc(args[1], "gamma")
            # 表达式串跳过本地值守卫（比不了大小）——服务端求值后 422
            if not isinstance(omega_p, str) and omega_p <= 0:
                raise ValueError("drude pole omega_p must be > 0")
            pole = {"kind": "drude", "omega_p": omega_p, "gamma": gamma}
        else:
            if len(args) < 2:
                raise TypeError(
                    f"{name}(omega_0, gamma, delta_eps=1) requires at "
                    f"least 2 arguments")
            omega_0 = sc(args[0], "omega_0")
            gamma = sc(args[1], "gamma")
            delta = sc(args[2], "delta_eps") if len(args) > 2 else 1.0
            if not isinstance(omega_0, str) and omega_0 <= 0:
                raise ValueError(
                    "lorentz pole omega_0 must be > 0 (use drude for "
                    "omega_0=0)")
            pole = {"kind": "lorentz", "omega_0": omega_0, "gamma": gamma,
                    "delta_eps": delta}
        if alpha is not None:
            pole["alpha"] = __class__._dir3(alpha, "alpha")
            if beta is not None:
                pole["beta"] = __class__._dir3(beta, "beta")
            # beta 缺省 = alpha：留给服务端（线格式语义），SDK 不补写
        elif beta is not None:
            raise TypeError(
                "beta given without alpha — rank-1 requires alpha "
                "(beta defaults to alpha)")
        return pole


# ---------------------------------------------------------------------------
# Materials / Geometry / Source / Monitor（列表工厂）
# ---------------------------------------------------------------------------

class Materials:
    def __init__(self, store: list):
        self._list = store

    def add_const(self) -> _Chain:
        item = ConstMatSpec.model_construct()   # construct 填 type 默认（E1）
        self._list.append(item)
        return _Chain(item)

    def add_lorentz(self) -> _Chain:
        item = LorentzMatSpec.model_construct()
        self._list.append(item)
        return _Chain(item)

    def add_from_library(self, library: str, *, name: str | None = None,
                         fit_range: tuple = LIBRARY_FIT_DEFAULTS["fit_range"],
                         n_drude: int = LIBRARY_FIT_DEFAULTS["n_drude"],
                         n_lorentz: int = LIBRARY_FIT_DEFAULTS["n_lorentz"],
                         maxfev: int = LIBRARY_FIT_DEFAULTS["maxfev"]) -> _Chain:
        """从 n-k 材质库生成色散材料（**客户端展开糖**，F1 2026-09）。

        内部立即 load_library_nk（优先 SDK 自带本地库 ``genie/nkdata/``，
        离线可用）→ ``genie.material.fit_nk`` → 注册成 **lorentz pole
        材料节点**（eps_inf + poles）——配置里携带的就是 pole，
        本地/云端同构；数值与旧内核服务端拟合一致（golden 锁
        ``test_fit_golden.py``，rel=1e-9）。

        Parameters
        ----------
        library : str
            库名（如 ``'Au'``；``genie.material.list_library_materials()``
            可列全部 37 个）。
        name : str | None
            材料名（默认 = 库名），geometry 以此引用。
        fit_range / n_drude / n_lorentz / maxfev
            拟合参数，语义同 ``genie.material.fit_nk``。

        Notes
        -----
        拟合需要 scipy——缺失时抛 ``ImportError`` 指路
        ``pip install scipy``，不静默降级。本方法即时计算（无延迟
        链式 setter，fit 参数走关键字实参）；返回的 _Chain 指向生成
        的 lorentz 节点。
        """
        if not library or not isinstance(library, str):
            raise ValueError(
                "add_from_library(library) requires a non-empty string")
        from . import material as gm  # lazy：import genie 不背 scipy 面

        wl, n_arr, k_arr = gm.load_library_nk(library)
        res = gm.fit_nk((wl, n_arr, k_arr), fit_range=fit_range,
                        n_drude=n_drude, n_lorentz=n_lorentz,
                        maxfev=maxfev, name=name or library)
        node = res["material_node"]
        if node is None:
            raise ValueError(
                f"library material {library!r} fit produced no usable "
                f"lorentz node: " + "; ".join(res["warnings"]))
        item = LorentzMatSpec.model_construct(**node)
        self._list.append(item)
        return _Chain(item)

    # ---- 查询/管理（同本地包）----
    def list_models(self) -> list:
        return [getattr(m, "name", None) for m in self._list]

    def has_model(self, name: str) -> bool:
        return name in self.list_models()

    @property
    def count_models(self) -> int:
        return len(self._list)

    def remove_model(self, name: str) -> bool:
        """移除**所有**同名条目（F1 修复段 1.4，2026-09-11）。

        dialect:100/:671 定案「材料重名后写覆盖先写」——服务端 view 下
        同名只剩最后一个生效。原实现删首个匹配即 return True：[x, x]
        场景删掉被覆盖的前一条、生效的后一条原封不动，API 报成功而
        材料实际未删。语义修正为「移除名为 name 的材料」：返回 True ⇔
        has_model(name) 变为 False。单条/未命中场景行为不变。"""
        before = len(self._list)
        self._list[:] = [m for m in self._list
                         if getattr(m, "name", None) != name]
        return len(self._list) != before

    def clear_models(self) -> None:
        self._list.clear()


class Geometry:
    def __init__(self, store: list):
        self._list = store

    def add_box(self) -> _Chain:
        item = BoxSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_sphere(self) -> _Chain:
        item = SphereSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_cylinder(self) -> _Chain:
        item = CylinderSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_ngon_z(self) -> _Chain:
        item = NgonZSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_polygon_z(self) -> _Chain:
        item = PolygonZSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_ellipse_z(self) -> _Chain:
        """椭圆（z 轴挤出）：.rx().ry().height()——2026-09 内核 1.1.0 开放。"""
        item = EllipseZSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_ring_z(self) -> _Chain:
        """圆环（z 轴挤出）：.inner_radius(0=实心盘).outer_radius().height()。"""
        item = RingZSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_polygon_swept(self) -> _Chain:
        """斜切多边形：vertices/z_range 同 polygon_z，另 .tilt(dx_dz, dy_dz)。"""
        item = PolygonSweptSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def clear(self) -> None:
        self._list.clear()


class Source:
    def __init__(self, store: list):
        self._list = store

    def add_soft_source(self) -> _Chain:
        item = SoftSourceSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_plane_wave(self) -> _Chain:
        item = PlaneWaveSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_gaussian_beam(self) -> _Chain:
        item = GaussianBeamSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)


class Monitor:
    def __init__(self, store: list):
        self._list = store

    def add_time(self) -> _Chain:
        item = TimeMonSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def add_freq(self) -> _Chain:
        item = FreqMonSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item, freq_style="monitor")

    def add_index(self) -> _Chain:
        item = IndexMonSpec.model_construct()   # construct 填 type 默认
        self._list.append(item)
        return _Chain(item)

    def clear(self) -> None:
        self._list.clear()
