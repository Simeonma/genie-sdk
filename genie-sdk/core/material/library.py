"""genie.core.material.library — 本地 n-k 材质库加载族。

n-k 库随包分发（``genie/core/nkdata/``），``load_library_nk`` 纯本地
（2026-10-27 G19b 翻转：HTTP 回退已退役，未命中即报错）。永不 import
fdtd。（2026-10-31 结构重构批 T2：自 core/material.py 单文件拆入，
函数体逐字保留——仅函数内 lazy 的 ``.nkdata`` 相对导入随目录加深补为
``..nkdata``。）
"""
from __future__ import annotations

import numpy as np


def load_library_nk(name):
    """取材质库某名的原始 n-k 数据 → (wl_m, n, k)。

    2026-10-27 起**纯本地**（G19b 翻转：材质库回归 SDK，网关 tools 面
    随协议层手术撤除，HTTP 回退与 allow_remote/base_url 等参数一并
    退役）：n-k 库随包分发（``genie/core/nkdata/``，迁移时与内核库逐字节
    一致；内核侧副本已随其拟合面删除），
    本地未命中直接报错并列出查询入口。永不 import fdtd。

    Parameters
    ----------
    name : str
        库名（如 ``'Au'``）。

    Returns
    -------
    (wl_m, n, k) : tuple[np.ndarray, np.ndarray, np.ndarray]
        波长（米）、折射率实部 n、消光系数 k（与 ``fit_nk`` 的 3-tuple
        输入约定一致；波长 µm→m 已换算）。

    Raises
    ------
    ValueError
        库名为空，或本地未命中（可用 genie.material.list_library_materials()
        查全部本地库名）。
    """
    from ..nkdata import load_nk_data

    if not isinstance(name, str) or not name:
        raise ValueError(
            "load_library_nk(name) requires a non-empty string library "
            "name")
    data = load_nk_data(name)
    if data is None:
        raise ValueError(
            f"library {name!r} not found in the local n-k library — "
            f"genie.material.list_library_materials() lists all local "
            f"library names")
    wl_um, n, k = data
    return (np.asarray(wl_um, dtype=float) * 1e-6,
            np.asarray(n, dtype=float), np.asarray(k, dtype=float))


def list_library_materials():
    """列出本地打包 n-k 库的全部库名（sorted，F1 随迁后离线可用）。"""
    from ..nkdata import available_materials

    return available_materials()
