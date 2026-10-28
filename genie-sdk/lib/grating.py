"""一维光栅——最常用的周期结构（framework §四 lib 起步件）。

参数化构件：改 ``f``、返回 None（``from genie.lib import grating`` 直接
调用；普通 Python，无特殊概念）。节点的生成语义与手写 for 循环逐条
``add_box`` 完全一致——脚本执行（会话/SDK 同一双手）即展开。
"""
from __future__ import annotations

from typing import Any


def grating(f: Any, *, material: str,
            x_range: tuple[float, float],
            y_range: tuple[float, float],
            z_range: tuple[float, float],
            n: int, pitch: float, duty: float = 0.5,
            priority: int = 0) -> None:
    """沿 x 的 n 周期线条光栅。

    Parameters
    ----------
    material : str
        材料名（须已 ``f.mat.add_*`` 注册——引用未定义材料提交时预检即拒）。
    x_range : (x0, x1)
        光栅起点 x0 与名义终点 x1（``pitch * n`` 应 ≈ ``x1 - x0``；
    不强校验——多留/少留半周期的物理意图归用户）。
    y_range / z_range : (lo, hi)
        线条的横截面范围（全线相同）。
    n : int
        周期数（线条数）。
    pitch : float
        周期（m）。
    duty : float
        占空比——线宽 = ``pitch * duty``（0 < duty < 1；默认 0.5）。
    priority : int
        几何优先级（默认 0，全部线条同值）。
    """
    if not 0.0 < duty < 1.0:
        raise ValueError(f"duty 须在 (0, 1) 开区间，得到 {duty}")
    if n < 1:
        raise ValueError(f"n 须 >= 1，得到 {n}")
    x0 = x_range[0]
    w = pitch * duty
    for i in range(n):
        f.geom.add_box().material(material) \
            .x_range(x0 + i * pitch, x0 + i * pitch + w) \
            .y_range(y_range[0], y_range[1]) \
            .z_range(z_range[0], z_range[1]) \
            .priority(priority)
