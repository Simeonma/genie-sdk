"""genie.host.card — 作业卡片（皮在浏览器；数据走环、停止键同门）。

Q5.3 补晚终版 / Q2.2-3：卡片数据 = 环里 worker 等待循环刷的
``out(kind=progress)`` rollup（host 只搬运、不问网关——本包零拨号）；
**停止键 = 整批一颗**，与 ``f.cancel`` 同一扇门（session 拦截入口 →
经 host/gw 直拨网关）。一批一卡（单跑 = 批长 1 亦一张）；「提交后出现、
全批完成且用户关闭后消失」与批内明细展开的皮细节随 GUI 壳落码。

词面注记：``"out"`` / ``"progress"`` 是环载荷的既定词（ipc 图 §4——
前端（含本包与 GUI JS）不得另立字段名；host 不 import ipc，字面与
GUI JS 同源对齐，ipc 图 §7 的跨语言对账挂账在册）。
"""
from __future__ import annotations

__all__ = ["stop", "snapshot"]


def stop(session) -> list:
    """整批一颗停止键：与 ``f.cancel`` 同门（session 拦截入口——
    ``cancel_inflight`` → host 回调经 ``/gw`` 直拨；忙闲皆灵）。"""
    return session.cancel_inflight()


def snapshot(events) -> dict | None:
    """环投影 → 最新一条 progress rollup（卡片数据；无则 None）。"""
    data = None
    for e in events:
        if e.get("ev") == "out" and e.get("kind") == "progress":
            data = e.get("data")
    return data
