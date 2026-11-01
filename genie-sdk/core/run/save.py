"""Simulation 存档族（T4 结构重构，自 core/simulate.py 纯移动拆出）。

方法体 → 模块函数：``self`` → 首参 ``sim``（函数名保持原名）。
``Simulation`` 门面上保留同名方法一行委托（签名/默认值/docstring
与原方法完全一致——公共面文档留在门面侧）。
"""
from __future__ import annotations


def save(sim, path) -> "Path":
    """把当前仿真存成 ``.gensim``（**一个文件 = 一个仿真**）。

    内容：当前配置（``to_config``，未提交也可）+ 本实例 ``run()`` 后
    已自动下载的结果 h5（若有）+ 其元信息（``state.json``）。
    写盘走 ``genie.core.archive.simfile``（格式正典、原子替换）；返回最终路径。

    语义边界：这是**文档产物**，不是第二份可编辑状态——打开/编辑的
    唯一可编辑的永远是实例本身（``genie.open`` 会整份替换 worker 里的模型）。
    """
    from pathlib import Path

    from ..archive import simfile
    result_data = None
    state = None
    doc = getattr(sim, "_result_doc", None)
    if doc:
        p = Path(doc)
        if p.is_file():
            result_data = simfile.result_bytes(p)
            if result_data is not None:
                state = simfile.result_state(
                    result_data, job_id=str(getattr(sim, "_job_id", "") or ""))
    pth = getattr(sim, "_result_local_path", None)
    if pth and result_data is None:
        p = Path(pth)
        if p.is_file():
            result_data = p.read_bytes()
            state = simfile.result_state(
                result_data, job_id=str(getattr(sim, "_job_id", "") or ""))
    return simfile.write(path, sim.to_config(),
                         result_data=result_data, state=state)


def _auto_save_document(sim) -> None:
    """run 成功后把仿真写成 ``.gensim`` 文档（G182 P2：**h5 退役**）。

    落点：``sim._doc_path``（打开过的文档 → 原地更新）→ 否则
    ``<workspace>/runs/<job_id>.gensim``（一次执行一份，历史可追溯）。
    结果字节直接在内存里进容器（零临时文件）。失败仅告警不抛——
    不吞 run 的成功语义，results() 需要时会现场重取。
    """
    try:
        from pathlib import Path

        from .. import paths
        from ..archive import simfile
        raw = sim._client.fetch_result(sim._job_id)
        state = simfile.result_state(raw, job_id=str(sim._job_id or ""))
        doc = Path(sim._doc_path) if sim._doc_path else \
            (paths.runs_dir() / f"{sim._job_id}.gensim")
        p = simfile.write(doc, sim.to_config(),
                          result_data=raw, state=state)
        sim._result_doc = str(p)
        sim._result_local_path = None      # 不再有散落 h5
        print(f"[genie] 结果已保存: {p}（{len(raw)} 字节）", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[genie] 结果保存失败（results() 会现场重取）: {e!r}",
              flush=True)
