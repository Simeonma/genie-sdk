"""genie_protocol — the single source of truth for the SimConfig wire
format (an independently distributed package, lazy-import shell).

pydantic v2, zero other runtime dependencies. The trio:

- ``_wire.py``   all Spec models + SimConfig + wire-format constants (pydantic)
- ``_paramname.py`` the parameter-name rule (a wire contract; ``expr``/``resolve``
  moved to genie-sdk ``core/param`` 2026-11-09 — evaluation is a client-side
  editing capability, the kernel takes numbers only)

Lazy-import discipline — this ``__init__`` is only a name table +
``__getattr__`` (PEP 562); the model bodies are loaded from ``_wire`` only
when an attribute is first touched; star imports trigger the full load per
``__all__``.
"""
from __future__ import annotations

__version__ = "2.0.1a7"

__all__ = [
    # basic type aliases
    "Range2", "Vec3", "NumOrTensor",
    # Domain / Mesh
    "BoundarySpec", "DomainSpec", "RefineSpec", "MeshSpec",
    # Materials
    "PoleSpec", "ConstMatSpec", "LorentzMatSpec", "LibraryMatSpec",
    "MaterialSpec",
    # Geometry
    "BoxSpec", "SphereSpec", "CylinderSpec", "NgonZSpec", "PolygonZSpec",
    "EllipseZSpec", "RingZSpec", "GeometrySpec",
    # Source
    "SoftSourceSpec", "PlaneWaveSpec", "GaussianBeamSpec", "SourceSpec",
    # Monitor
    "DownSampleSpec", "ApodizeSpec", "TimeMonSpec", "FreqMonSpec",
    "IndexMonSpec", "MonitorSpec",
    # Run and the top-level config (Q17 nested house)
    "RunSpec", "ModelSpec", "FdtdConfigSpec",
    "ModeDomainSpec", "ModeMeshSpec", "ModeConfigSpec",
    "PlanStageSpec", "SimConfig",
    # wire-format constants (resource-guard thresholds such as MAX_CELLS are deployment policy and live outside this package)
    "FACES", "BC_KINDS", "LIBRARY_FIT_DEFAULTS", "MAX_GPU_PER_RUN",
    "norm_vertices_pairs",
]

# Internal names that __getattr__ also serves: cross-module reuse needs
# them and dir() must enumerate them (locked by tests); they stay out of
# __all__.
_INTERNAL = frozenset({
    "_StrictModel", "_RangeModel", "_TemporalMixin", "_MonitorMixin",
    "_env_int", "_check_range", "_norm_polarization",
})


def __getattr__(name: str):
    if name in __all__ or name in _INTERNAL:
        from . import _wire
        return getattr(_wire, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_INTERNAL))
