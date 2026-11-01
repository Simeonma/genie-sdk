"""genie.core.material — 材料旅程：fit_nk 拟合 + 本地 n-k 库。

公开面（fit_nk / load_library_nk / list_library_materials）经本 __init__
聚合再导出——`from genie.core.material import X` 的既有路径不变；
实现分住 fit.py（Drude-Lorentz 拟合）与 library.py（yml 库加载）。
QUALITY_TABLE_CAP 一并再导出（tests/sdk/test_material_fit.py 直取）。"""
from .fit import QUALITY_TABLE_CAP, fit_nk
from .library import list_library_materials, load_library_nk

__all__ = ["fit_nk", "load_library_nk", "list_library_materials"]
