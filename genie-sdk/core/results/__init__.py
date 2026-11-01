"""结果对象与取数尺 —— 结果旅程的 SDK 落点。

作业跑完后，result.h5（内核 driver 写的分组 h5）经 SDK 变成可交互的
数据：SimData 包装 h5 组；curve/select/axis_lengths 是取数尺单一真源
（Studio 结果面板共用）；TimeResult/FreqResult/IndexResult 是 npz 拆包
的纯数据容器。SDK 零计算、零物理——派生量全在服务端。

实现分住两个模块（行为与原单文件 core/results.py 完全一致，仅搬移）：
select.py（取数算法族）与 simdata.py（数据类族）；本 __init__ 聚合
再导出，`from genie.core.results import X` 的既有路径不变。
"""
from .select import (AXIS_KEYS, H5_FORMAT_PREFIX, axis_lengths, curve,
                     ensure_supported_format, select, sweep_axes)
from .simdata import (FreqResult, IndexResult, SimData, TimeResult,
                      _MonitorView, _n_positions, _shape_label)

__all__ = ["AXIS_KEYS", "H5_FORMAT_PREFIX", "SimData", "TimeResult",
           "FreqResult", "IndexResult", "axis_lengths", "select",
           "sweep_axes", "curve", "ensure_supported_format"]
