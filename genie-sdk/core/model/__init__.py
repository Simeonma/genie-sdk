"""genie.core.model — f 的语义（core 侧本体）。地图：``model/MODMAP.md``。"""
from .draft import DRAFT_OF, to_wire
from .handle import Model

__all__ = ["Model", "to_wire", "DRAFT_OF"]
