"""genie.ipc — host↔worker 管子词汇表（零 IO 叶子）。

地图：``ipc/MODMAP.md``（词表、字段集、禁令）；词与构造函数唯一的家
是 ``vocab``——两端（session / worker）从这里取，不得写消息字面量。
"""
from .vocab import *  # noqa: F401,F403 —— 公开面 = vocab 全部名字
from .vocab import __all__ as _vocab_all

__all__ = list(_vocab_all)
