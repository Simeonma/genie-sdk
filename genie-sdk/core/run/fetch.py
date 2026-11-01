"""Simulation 取数族（T4 结构重构，自 core/simulate.py 纯移动拆出）。

方法体 → 模块函数：``self`` → 首参 ``sim``（函数名保持原名）；方法间
互调改直呼本模块函数（``self._result_bytes()`` → ``_result_bytes(sim)``）。
守卫与错误文案逐字保留（"no job submitted yet" 等是锁死的行为）。
``Simulation`` 门面上保留同名方法一行委托（签名/默认值/docstring 与
原方法完全一致——公共面文档留在门面侧）。
"""
from __future__ import annotations


def _result_bytes(sim) -> bytes:
    """结果字节单一来源（G93/S5，R18 清账）：本地落盘优先——
    auto_save 在 run() 成功时已下载过，同进程 get_*/results() 不再
    二次下载（修前 get_* 每次重取整份 h5）；无本地文件才现场
    fetch（后端已归档时两条路在同一处统一抛 410，不再一生一死）。
    显式取他人/历史作业不走这里（results(job_id=…) 直连）。"""
    from pathlib import Path as _P
    from ..archive import simfile
    doc = getattr(sim, "_result_doc", None)
    if doc and _P(doc).is_file():
        data = simfile.result_bytes(doc)
        if data is not None:
            return data
    local = getattr(sim, "_result_local_path", None)
    if local and _P(local).is_file():
        return _P(local).read_bytes()
    return sim._client.fetch_result(sim._job_id)


def _guard_done_for_fetch(sim) -> None:
    """取数前置守卫（get_* 与 results() 共用，G138/R18）：未成功
    终态 → RuntimeError + 日志尾（不裸打服务端吃 409/410——两条
    取数路径异常面统一）。

    2026-10-31 状态统一：``editing`` 单独答（无结果可读——从未跑过，或
    被 ``reopen()`` 作废），不再复用"作业失败"的措辞误导调用方。
    """
    from ..job_state import StateError
    if sim.state == "done":
        return
    if sim.state == "editing":
        raise StateError("results are not available in state 'editing'")
    tail = ""
    if sim._job_id is not None:
        try:
            tail = (sim._client.log(sim._job_id) or "")[-400:]
        except Exception:  # noqa: BLE001 —— 日志取不到不拦守卫
            tail = ""
    raise RuntimeError(
        f"job did not finish successfully (state={sim.state}); "
        f"no results to fetch. Job {sim._job_id} ended in a "
        f"failure state"
        + (f"; log tail:\n{tail}" if tail else
           " (use client.log(job_id) for the full log)"))


def _result_arrays(sim) -> dict:
    """结果 h5（genie-results-h5/1）→ 平铺 key 字典。

    h5 结构：/<监视器>/<场或轴>，组 attrs["domain"] ∈ {f,t,i}；
    展平回沿用约定 ``<域>@<监视器>.<场>``——下游
    TimeResult/FreqResult.from_npz/_extract 零改动。"""
    _guard_done_for_fetch(sim)
    if sim._arrays is None:
        import h5py
        import io
        import json as _json

        with h5py.File(io.BytesIO(
                _result_bytes(sim)), "r") as h5:
            from ..results import ensure_supported_format
            ensure_supported_format(h5)   # R23 G-a：格式家族明报
            arrays: dict = {}
            for mon_name, grp in h5.items():
                dom = grp.attrs.get("domain", "")
                # G144（R19）：bytes 域标 decode（h5py 变长字符串在
                # 部分写入配置下回读为 bytes——此前拼出 b'f'@pt.Ex
                # 使全部监视器误报 not found；studio 侧早有 str 包裹）。
                if isinstance(dom, bytes):
                    dom = dom.decode("utf-8", "replace")
                for dsname in grp:
                    arrays[f"{dom}@{mon_name}.{dsname}"] = grp[dsname][()]
            # G95（R19）：读根 attrs 的 manifest（driver 对每类监视器
            # 都写 {"name","type","shape",…}）——形态标签的正源，
            # 猜轴元素数对 index（逐格点云恒三轴）必错。
            meta: dict = {}
            try:
                raw_meta = h5.attrs.get("monitors", "")
                if isinstance(raw_meta, bytes):
                    raw_meta = raw_meta.decode("utf-8", "replace")
                parsed = _json.loads(raw_meta) if raw_meta else []
                if isinstance(parsed, list):
                    meta = {m.get("name"): m for m in parsed
                            if isinstance(m, dict)}
            except Exception:  # noqa: BLE001 —— manifest 坏不拦取数
                meta = {}
            sim._monitors_meta = meta
            sim._arrays = arrays
    return sim._arrays


def results(sim, job_id: str | None = None) -> "SimData":
    """拉取作业结果 → SimData 结构体（genie-results-h5/1）。

    ``data.<监视器>.<场>`` 直取（h5py 惰性读）；
    ``data.attrs["executed_steps"]`` 实际执行步数。job_id 缺省 =
    本 Simulation 最近一次提交的作业。（画图走 Studio 的 Plot
    面板——R36 起画图单一实现，SDK 不再内置 matplotlib 面。）"""
    from ..results import SimData, ensure_supported_format
    import h5py
    import io

    jid = job_id or sim._job_id
    if jid is None:
        # R23 C1：先答"还没提交"再谈终态——此前守卫先炸，未提交
        # 实例得到 "Job None ended in a failure state" 的误导诊断
        # （专用错误是死代码）。
        raise RuntimeError(
            "no job submitted yet — results() needs a job_id")
    # R23 S2：本实例作业（缺省或显式传自己的 id）都过终态守卫——
    # G138"两条取数路径异常面统一"对显式形态也成立（修前显式传
    # 自己的 id 吃裸 409）；他人/历史 id 仍直取不守。
    if job_id is None or job_id == sim._job_id:
        _guard_done_for_fetch(sim)
    if job_id is None or job_id == sim._job_id:
        raw = _result_bytes(sim)
    else:
        raw = sim._client.fetch_result(jid)
    from ..results import ensure_supported_format
    h5 = h5py.File(io.BytesIO(raw), "r")
    ensure_supported_format(h5)   # R23 G-a：两条取数路径同一校验
    return SimData(h5, job_id=jid)


def _find_monitor(sim, mtype: str, name: str | None) -> dict:
    for m in sim._cfg.monitor:
        if getattr(m, "type", None) != mtype:
            continue
        if name is None or getattr(m, "name", None) == name:
            return m
    raise KeyError(
        f"{mtype} monitor not found"
        + (f" {name!r}" if name else " (none defined in the config)"))


def _get_result(sim, mtype: str, name: str | None, cls):
    """get_* 公共路径：先核作业终态（未成功直接报，不谈监视器）→
    找监视器配置 → （缓存）构造 Result。"""
    arrays = _result_arrays(sim)
    entry = _find_monitor(sim, mtype, name)
    mname = getattr(entry, "name", None)
    key = (mtype, mname)
    if key not in sim._results:
        # G95：manifest 的 shape 优先（正源）；缺失回退轴元素数猜测
        sim._results[key] = cls.from_npz(
            arrays, mname,
            shape_hint=(sim._monitors_meta.get(mname) or {}).get("shape"))
    return sim._results[key]


def get_time(sim, name: str | None = None):
    from ..results import TimeResult
    return _get_result(sim, "time", name, TimeResult)


def get_freq(sim, name: str | None = None):
    from ..results import FreqResult
    return _get_result(sim, "freq", name, FreqResult)


def get_index(sim, name: str | None = None):
    from ..results import IndexResult
    return _get_result(sim, "index", name, IndexResult)


def summary(sim) -> dict:
    """配置摘要 + （已提交时）服务端作业摘要。打印并返回 dict。"""
    cfg = sim.to_config()
    d = {
        "domain": {k: v for k, v in cfg["domain"].items()},
        "mesh": {k: v for k, v in cfg["mesh"].items()},
        "n_materials": len(cfg["materials"]),
        "n_geometry": len(cfg["geometry"]),
        "n_source": len(cfg["source"]),
        "monitors": [f"{m.get('name')}({m.get('type')})"
                     for m in cfg["monitor"]],
        "run": dict(cfg["run"]),
        "state": sim.state,
        "job_id": sim._job_id,
    }
    if sim._job_id is not None:
        try:
            d["server_summary"] = sim._client.summary(sim._job_id)
        except Exception as e:  # noqa: BLE001 —— 摘要失败不拦主流程
            d["server_summary"] = f"(不可用: {e})"
    print("── GENIE 仿真摘要 " + "─" * 40)
    for k, v in d.items():
        print(f"  {k}: {v}")
    return d


def _digest_from_local(sim, jid) -> dict | None:
    """从本地**结果文档**里读 digest attrs（G170：fdtd 随结果带出，
    0 次 HTTP；G182 P2：改从 ``.gensim`` 内嵌 h5 读）。读不到 → None。"""
    import io

    from ..archive import simfile
    doc = getattr(sim, "_result_doc", None)
    p = doc if doc else getattr(sim, "_result_local_path", None)
    if not p:
        return None
    try:
        import h5py
        from ..results import SimData
        if doc:
            data = simfile.result_bytes(p)
            if data is None:
                return None
            h5 = h5py.File(io.BytesIO(data), "r")
        else:
            h5 = h5py.File(p, "r")
        try:
            return SimData(h5, job_id=jid).digest
        finally:
            h5.close()
    except Exception:  # noqa: BLE001 —— 本地读不到就走 HTTP，不拦
        return None


def digest(sim, job_id: str | None = None) -> dict:
    """结果数值摘要：各监视器峰频/幅值；面监视器含透过率
    min/max/中心频值（``normalized=true`` 才是物理 T；点监视器无
    面积分不含 T——已知方言边界）。数字服务端算好、读取零 GPU，
    不拉 npz（比较/调参轮次读这个，别下结果文件）。

    G170：digest 已随结果 h5 根 attrs 带出——本地 auto-save 在场时
    **本地优先**（0 次 HTTP）；旧产物缺键回退 GET（同旧行为）。
    ``data.digest`` 亦可直接从 results() 的 SimData 读。

    job_id 缺省用本实例已提交的作业。F3 修复段 1.2：默认 Analysis
    模板（app.js ``DEFAULT_ANALYSIS_CODE``）在浏览器 Pyodide 里调
    此法——与 ``summary()`` 同构的服务端读数转发（此前模板调
    ``s.digest`` 必 AttributeError：公共表面缺这一格）。
    """
    jid = job_id or sim._job_id
    if jid is None:
        raise RuntimeError(
            "no job_id to query the digest for (instance not "
            "submitted and no job_id passed) — call run() first, or "
            "pass digest(job_id) explicitly")
    if job_id is None or job_id == sim._job_id:
        d = _digest_from_local(sim, jid)
        if d is not None:
            return d
    return sim._client.digest(jid)
