"""genie_protocol — SimConfig 线格式的唯一真源（独立分发包，懒加载壳）。

2026-10-27 拆分自 SDK 仓 ``genie/models/``（逐字节迁移）：pydantic v2、
零内核依赖、零 SDK 依赖——SDK 客户端 / 网关 / 内核三方 import 同一份
模型。SDK 侧 ``genie.models`` 是本包的兼容薄壳。三件套：

- ``_wire.py``   全部 Spec 模型 + SimConfig + 线格式常量（pydantic）
- ``expr.py``    安全线表达式求值器（纯 Python：ast + math + re，零 pydantic）
- ``resolve.py`` 参数解析（dict 伴走 + 表达式槽求值 + 求值结果校验）

懒加载纪律（2026-10 站长定案）——``import genie_protocol.expr`` **不得
拉起 pydantic**：本 ``__init__`` 只做名字表 + ``__getattr__``（PEP 562），
模型本体在属性首次触达时才从 ``_wire`` 加载；星号导入按 ``__all__``
触发完整加载。
"""
from __future__ import annotations

__version__ = "2.0.0a2"   # 与 protocol/pyproject.toml 双处对齐（RELEASE 纪律）

__all__ = [
    # 基础类型别名
    "Range2", "Vec3", "NumOrTensor",
    # Domain / Mesh
    "BoundarySpec", "DomainSpec", "RefineSpec", "MeshSpec",
    # Materials
    "PoleSpec", "ConstMatSpec", "LorentzMatSpec", "LibraryMatSpec",
    "MaterialSpec",
    # Geometry
    "BoxSpec", "SphereSpec", "CylinderSpec", "NgonZSpec", "PolygonZSpec",
    "EllipseZSpec", "RingZSpec", "PolygonSweptSpec", "GeometrySpec",
    # Source
    "SoftSourceSpec", "PlaneWaveSpec", "GaussianBeamSpec", "SourceSpec",
    # Monitor
    "DownSampleSpec", "ApodizeSpec", "TimeMonSpec", "FreqMonSpec",
    "IndexMonSpec", "MonitorSpec",
    # Run 与总配置
    "RunSpec", "SimConfig",
    # Sweep
    "SweepAxisSpec", "SweepSpec",
    # 线格式常量（资源守卫阈值 MAX_CELLS 等是部署策略，留内核 serve 层）
    "FACES", "BC_KINDS", "LIBRARY_FIT_DEFAULTS", "DEFAULT_TIMEOUT_S",
    "MAX_GPU_PER_RUN", "MAX_SWEEP_POINTS", "MAX_SWEEP_AXES",
    "norm_vertices_pairs",
]

# __getattr__ 同样可服务的内部名：内核薄壳显式 import（字段台账测试用
# dir() 枚举 _StrictModel 子类）与跨模块复用需要；不进 __all__。
_INTERNAL = frozenset({
    "_StrictModel", "_RangeModel", "_TemporalMixin", "_MonitorMixin",
    "_env_int", "_check_range", "_norm_polarization", "_norm_vertices_pairs",
})


def __getattr__(name: str):
    if name in __all__ or name in _INTERNAL:
        from . import _wire
        return getattr(_wire, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_INTERNAL))
