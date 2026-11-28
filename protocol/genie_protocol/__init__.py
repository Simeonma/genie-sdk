"""genie_protocol — the single source of truth for the SimConfig wire
format. Two packaging faces of one source (lazy-import shell):

- ``genie-sdk`` wheel carries a copy as top-level ``genie_protocol/``
  (users install one package);
- ``genie-protocol`` is its own wheel for the kernel / gateway
  (genie-fdtd, genie-service), which do not install the SDK.

Both wheels ship byte-identical files. Two owners of ``genie_protocol/`` in
one environment is refused at import (2026-11-11 V100 incident: install order
silently decided which copy loaded).

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

__version__ = "2.0.6a1"


def _forbid_double_owner() -> None:
    """Two distributions both registering ``genie_protocol/`` = loud failure.

    The SDK wheel bundles a copy; the kernel/gateway install the standalone
    ``genie-protocol`` wheel. If both land in one environment, pip's file
    order decides which copy wins and nothing reports it (2026-11-11 V100).
    Judge on RECORD ownership, not on "both distributions are installed":
    editable installs (dev checkouts) register no package files and are fine.
    """
    import importlib.metadata as md
    owners = []
    for name in ("genie-sdk", "genie-protocol"):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        files = {str(f).replace("\\", "/") for f in (dist.files or ())}
        if "genie_protocol/__init__.py" in files:
            owners.append(name)
    if len(owners) == 2:
        raise ImportError(
            "genie-sdk 与 genie-protocol 都装了，且都带 genie_protocol/——"
            "两份副本并存时由装包顺序决定加载哪份，必出错。只留一个："
            "用户机 `pip uninstall genie-protocol`；"
            "内核/网关 `pip uninstall genie-sdk`，再重跑一次安装。"
        )


_forbid_double_owner()

__all__ = [
    # basic type aliases
    "Range2", "Vec3",
    # Domain / Mesh (Q30: base grid on DomainSpec, MeshSpec = named
    # refinement-box objects; RefineSpec retired. Q31: per-axis spacing
    # tables — SpacingSegmentSpec)
    "BoundarySpec", "DomainSpec", "SpacingSegmentSpec", "MeshSpec",
    # Materials
    "PoleSpec", "ConstMatSpec", "LorentzMatSpec", "LibraryMatSpec",
    "MaterialSpec",
    # Attributes (named, reusable per-object properties)
    "MatrixFrameSpec", "AttributeSpec",
    # Geometry
    "BoxSpec", "SphereSpec", "CylinderSpec", "NgonZSpec", "PolygonZSpec",
    "EllipseZSpec", "RingZSpec", "GeometrySpec",
    # Source
    "SoftSourceSpec", "PlaneWaveSpec", "GaussianBeamSpec",
    "ModeSelectionSpec", "ModeSourceSpec", "SourceSpec",
    # Monitor
    "DownSampleSpec", "ApodizeSpec", "TimeMonSpec", "FreqMonSpec", "ModeExpansionSpec",
    "IndexMonSpec", "MonitorSpec",
    # Run and the top-level config (Q17 nested house)
    "RunSpec", "ModelSpec", "FdtdConfigSpec",
    "ModeDomainSpec", "ModeMeshSpec", "ModeConfigSpec",
    "SimConfig",
    # wire-format constants (resource-guard thresholds such as MAX_CELLS are deployment policy and live outside this package)
    "FACES", "BC_KINDS", "LIBRARY_FIT_DEFAULTS", "MAX_GPU_PER_RUN",
    "norm_vertices_pairs",
]

# Internal names that __getattr__ also serves: cross-module reuse needs
# them and dir() must enumerate them (locked by tests); they stay out of
# __all__.
_INTERNAL = frozenset({
    "_StrictModel", "_BoxVocab", "_TemporalMixin", "_MonitorMixin",
    "_env_int", "_check_range", "_norm_polarization",
})


def __getattr__(name: str):
    if name in __all__ or name in _INTERNAL:
        from . import _wire
        return getattr(_wire, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_INTERNAL))
