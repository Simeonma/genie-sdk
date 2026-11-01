"""会话多实例模型合并 + JSON 安全化（G174 搬家件，2026-10-28 R33）。

来历：这两个纯函数原住 ``core/preview.py``（双消费方 = preview_script 与
``kernel_worker._merged``）。G174（站长三连裁决）把 preview 独立引擎整体
退役后，preview_script 消费面消失——函数与执行引擎无关，是会话"提交/
快照唯一取值口径"的底座，随迁移轮搬到会话层独立小模块（站长裁：不放
kernel_worker 内联——那个文件是被 ``sys.executable <path>`` 直接 spawn
的契约脚本，纯函数单独可 import 便于锁测试）。
"""
from __future__ import annotations

from typing import Any

__all__ = ["merge_configs", "nan_to_none"]


def merge_configs(cfgs: list[dict], ran: list[bool] | None = None) -> dict:
    """多 connect 实例的捕获合并：节 dict 后者覆盖、列表按声明序拼接、
    run 取**最后实际 run 的实例**、parameters 逐名后者覆盖
    （"每个作业用新 connect 实例"的脚本语义）。

    判据注意（阶段 ④）：输入是 model_dump(mode="json") 的 dense dict——
    ``run``/``parameters`` 键恒在场（run 带 gpu/timeout_s 默认、
    parameters 为 None）。**R28 P3（站长裁）**：dense 默认值在从未被碰
    过的实例 dump 里也随身在场，"后者覆盖"分不清显式与默认——修前
    ``a.run(gpu=2)`` 后接一个不 run 的实例，gpu=2 被默认值静默 clobber
    （与旧 docstring"取最后实际 run 的"相反）。``ran[i]`` 标记第 i 个
    实例是否真调过 run（判据 = ``_job_id is not None``），run 只从
    实际 run 的实例取末位；``ran=None`` 视同全部实际 run（单实例
    行为与 to_config() 逐键同形）。domain/mesh/
    列表维持"后者覆盖/按序拼接"（每实例新配置语义，站长同轮裁）。

    唯一消费方 = studio.kernel_worker._merged（会话提交/快照同一口径）。"""
    if ran is not None and len(ran) != len(cfgs):
        raise ValueError(
            f"merge_configs: ran 长度须与 cfgs 一致"
            f"（{len(ran)} != {len(cfgs)}）")
    if ran is None:
        ran = [True] * len(cfgs)
    merged: dict[str, Any] = {
        "domain": {}, "mesh": {}, "materials": [], "geometry": [],
        "source": [], "monitor": [], "run": {},
    }
    last_run: dict | None = None
    for c, r in zip(cfgs, ran):
        for k in ("domain", "mesh"):
            merged[k].update(c.get(k) or {})
        for k in ("materials", "geometry", "source", "monitor"):
            merged[k].extend(c.get(k) or [])
        if r and c.get("run"):
            last_run = c["run"]
        if c.get("parameters"):
            merged.setdefault("parameters", {}).update(c["parameters"])
    if "parameters" not in merged:
        merged["parameters"] = None
    # 无人实际 run → 回退末位实例的 dense 默认（run 键恒在场的 dense
    # 契约不破——golden/预检锁冻着它；单实例与 to_config 逐键同形）。
    merged["run"] = last_run if last_run is not None else (
        cfgs[-1].get("run") if cfgs else {})
    return merged


def nan_to_none(v: Any) -> Any:
    """JSON 安全化：float('nan') → None。

    polygon 哨兵的 Python 侧形态是 (nan, nan)，而**线格式是 [null, null]**
    （JSON 严格语法无 NaN 字面量）。消费方 = kernel_worker 的 cfg 事件
    出口——会话快照直接序列化为 JSON（emit 的 json.dumps），出口处归一
    回线格式正形，浏览器 JSON.parse 不丢整条 cfg 事件（R24 S1：单实例
    也必须过——修前 len==1 直通原文，裸 NaN 会炸严格解析）。"""
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, dict):
        return {k: nan_to_none(x) for k, x in v.items()}
    if isinstance(v, list):
        return [nan_to_none(x) for x in v]
    return v
