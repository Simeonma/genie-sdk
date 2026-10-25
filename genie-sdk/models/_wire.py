"""genie.models._wire — 兼容薄壳：真源已迁 genie_protocol._wire（本仓 protocol/ 子目录，2.0.0a9 起）。

两条 import 路径的分工：``from genie.models import X`` 走包壳（懒加载、
按名字表转发）；``from genie.models._wire import X`` 走本壳（真源模块的
平铺镜像，模块级 import 的调用方用）。星号导入按 ``__all__``（=
genie_protocol.__all__，单一定义面，2026-10-27 G104 清账后不再恒空）。
"""
import genie_protocol._wire as _impl
import genie_protocol

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
__all__ = list(genie_protocol.__all__)
