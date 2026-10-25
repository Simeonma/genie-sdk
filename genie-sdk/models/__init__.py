"""genie.models — 兼容薄壳 → 独立分发包 ``genie-protocol``（import 名 ``genie_protocol``）。

2026-10-27 拆分（挂账⑦手术）：线格式唯一真源 = 本仓 ``protocol/genie_protocol/``
（独立 wheel ``genie-protocol``，公开）；内核与网关直接依赖 ``genie_protocol``，
SDK 侧保留 ``genie.models.*`` 公开面不变。懒加载纪律原样保留——
``import genie.models.expr`` 仍不得拉起 pydantic（本壳自身零重物 import）。
"""
from __future__ import annotations


def _ensure_genie_protocol() -> None:
    """源码树形态下自举 ``genie_protocol``（本仓 ``protocol/`` 子目录）。

    装了 ``genie-protocol``（wheel/依赖声明）的环境 ``import`` 一次即中；
    只有"直接从源码树跑、又没走 ``_tree`` 绑定"的进程（典型 =
    ``kernel_worker`` 子进程，见 kernel_session ``_respawn``）才落到文件位
    自举——按 ``models/`` 的相对位置找 ``protocol/genie_protocol``。
    """
    try:
        import genie_protocol  # noqa: F401
        return
    except ModuleNotFoundError as e:
        # R21 复审 S6：只把"genie_protocol 本身不在"当未装回退源码树。
        # 包体内部的 ImportError（依赖缺失/半升级）必须浮出——静默回退
        # 源码树副本会掩盖环境病；e.name 区分二者（依赖缺失同样是
        # ModuleNotFoundError，如 pydantic 不在）。
        if e.name != "genie_protocol":
            raise
    import importlib.util
    import sys
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[2] / "protocol" / "genie_protocol"
    if not (pkg / "__init__.py").is_file():
        raise ImportError(
            "genie-protocol 未安装，且源码树里找不到 protocol/genie_protocol"
            "（pip install genie-protocol，或从本仓 protocol/ 子目录安装）")
    spec = importlib.util.spec_from_file_location(
        "genie_protocol", pkg / "__init__.py",
        submodule_search_locations=[str(pkg)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["genie_protocol"] = mod
    spec.loader.exec_module(mod)


_ensure_genie_protocol()

# 名字表不手抄（G105 清账 2026-10-27）：公开名 ``__all__`` 与内部名
# ``_INTERNAL`` 都从真源 ``genie_protocol`` import——单一定义面，加模型
# 只改真源一处，本壳自动跟随（有 test_models 表面锁盯着）。
import genie_protocol as _gp

__all__ = list(_gp.__all__)
_INTERNAL = _gp._INTERNAL


def __getattr__(name: str):
    if name in __all__ or name in _INTERNAL:
        import genie_protocol
        return getattr(genie_protocol, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_INTERNAL))
