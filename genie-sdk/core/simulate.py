"""Simulation 主体 — 装载、提交边界与生命周期（framework §四）。

自 _config.py 后半拆出（阶段 ④，纯移动）：``Simulation`` + load_config 的
递归构造（_construct_tree/_construct_value）+ 提交边界一次性预检
（_validate_for_submit，genie.models 同一双手）。builder 六命名空间在
``core/builder.py``。
"""
from __future__ import annotations

import threading
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from .builder import Domain, Geometry, Materials, Mesh, Monitor, Source
from ..models.errors import GenieConfigError, GenieJobError
from ..models import DomainSpec, LorentzMatSpec, MeshSpec, RunSpec, SimConfig
from .sweep import SweepHandle

# ── 组合根注入点（G1 分层契约，2026-10-11 定案）──────────────────────
# core 只依赖 models：上层能力（visualize 注入等）的实现在上层
# （net / integrations），由 ``genie/__init__`` 组合根在 import 时经
# ``_wire`` 注入。缺省 = 离线降级（visualize 报未接线）——只有绕过
# ``import genie`` 直捣 core 子模块的形态才会遇到缺省态，产品面不可达。
# （历史 hooks：expert_factory 随 R36 专家面退役；材质库回退随 G19b。）
# 分层守卫 = tests/sdk/test_layering.py。
hooks: dict = {}


def _wire(**impl) -> None:
    """组合根注册通道（genie/__init__ 专用；幂等覆盖）。"""
    hooks.update(impl)

# ---------------------------------------------------------------------------
# Simulation ≈ 内部 FDTD
# ---------------------------------------------------------------------------

def _construct_tree(cls: type[BaseModel], data: dict, path: str) -> BaseModel:
    """wire dict → 未校验模型树（load_config 专用，framework §四）。

    与裸 ``model_construct(**dict)`` 的差别（实证 E2）：嵌套 dict 会被
    原样塞进字段（后续 builder setattr 即碎）、未知键被**静默丢弃**——
    这里递归构造子模型，且未知键显式拒绝（``_StrictModel`` 精神：拼错
    的键不允许无声消失，那正是 extra=forbid 要防的静默错误物理）。
    判别 union（materials/geometry/source/monitor）按 ``type`` 选成员，
    复用 models.resolve 的判别工具——同一双手，零漂移。

    半成品容忍：节缺失的 dict 合法（构造期语义）——缺的必填字段按
    ``Simulation.__init__`` 同款补**空 construct 工作区**（list 节 → []，
    子模型节 → model_construct()），装载后 builder 继续链式补齐。
    实证修正：``source``/``run`` 这类无默认必填，裸 construct 不回填
    → _rebind/f.run() 即 AttributeError（03_parameters 示例暴露）。
    """
    if not isinstance(data, dict):
        raise GenieConfigError(
            f"load_config: {path} must be a dict ({cls.__name__}), got "
            f"{type(data).__name__}")
    fields = cls.model_fields
    unknown = sorted(k for k in data if k not in fields)
    if unknown:
        raise GenieConfigError(
            f"load_config: {path} ({cls.__name__}) has unknown fields "
            f"{unknown} (expected one of: {sorted(fields)})")
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[k] = _construct_value(fields[k].annotation, v, f"{path}.{k}")
    from pydantic_core import PydanticUndefined
    from ..models.resolve import unwrap_annotation
    for k, f in fields.items():
        has_default = (f.default is not PydanticUndefined
                       or (f.default_factory is not None
                           and f.default_factory is not PydanticUndefined))
        if k in out or has_default:
            continue
        bare, _ = unwrap_annotation(f.annotation)
        if get_origin(bare) is list:          # 必填 list 节 → 空列表工作区
            out[k] = []
        elif isinstance(bare, type) and issubclass(bare, BaseModel):
            out[k] = bare.model_construct()   # 必填子模型 → 空 construct 工作区
    return cls.model_construct(**out)


def _construct_value(ann: Any, v: Any, path: str) -> Any:
    """按注解递归构造：BaseModel 子类/discriminator union/list[模型] 深造，
    其余（标量/parameters/boundary/gpu 等 dict 容器）原样透传——与 builder
    的裸存语义一致（构造期不校验，提交边界统一过模型）。"""
    from ..models.resolve import discriminator_name, select_union_member, unwrap_annotation
    if isinstance(v, list):
        if get_origin(ann) is list:
            (elem_ann,) = get_args(ann)
            return [_construct_value(elem_ann, x, f"{path}[{i}]")
                    for i, x in enumerate(v)]
        return v
    if isinstance(v, dict):
        bare, meta = unwrap_annotation(ann)
        if isinstance(bare, type) and issubclass(bare, BaseModel):
            return _construct_tree(bare, v, path)
        disc = discriminator_name(meta)
        if disc is not None:
            if disc not in v:
                raise GenieConfigError(
                    f"load_config: {path} is missing the discriminator "
                    f"field {disc!r} (see the wire-format docs for "
                    f"valid types)")
            members = [a for a in get_args(bare)
                       if isinstance(a, type) and issubclass(a, BaseModel)]
            member = select_union_member(members, v[disc])
            if member is None:
                raise GenieConfigError(
                    f"load_config: {path}.{disc}={v[disc]!r} is unknown "
                    f"(expected one of: "
                    f"{[m.model_fields[disc].default for m in members]})")
            return _construct_tree(member, v, path)
    return v


def _validate_for_submit(cfg: dict) -> None:
    """提交边界一次性校验（framework §四；validate_assignment 关的另一半）。

    与服务端 parse **同一双手**（``genie.models.resolve``）：表达式串先
    resolve 再校验（float 字段吃不了 str），失败抛 ``GenieConfigError``。
    判定本体 = ``validate_for_ui``——与 /api/validate、宿主 agent 的
    validate_cfg 同一个实现（2026-10-18 接口整理轮收口：此前五份拷贝
    对参数键名检查的处理并不一致）。线格式只有一份模型，不存在字段
    同步问题。上行 payload 不受影响——始终是原文 dump（表达式串原文
    上行，求值只在服务端）。"""
    from ..models.resolve import validate_for_ui

    errors = validate_for_ui(cfg)
    if not errors:
        return
    lines = [(e["loc"] + ": " if e["loc"] else "") + e["msg"]
             for e in errors[:3]]
    more = f" ({len(errors)} issues)" if len(errors) > 3 else ""
    raise GenieConfigError(
        "config precheck failed wire-format validation" + more + ":\n  "
        + "\n  ".join(lines))


def _apply_run_kwargs(sim: "Simulation", gpu: int | None,
                      timeout_s: int | None) -> None:
    """run() kwargs 的共享提交纪律（G102，R24 收敛）：已提交改参拒 +
    R23 S1 整数槽（float/bool/str 全拒）。G174 前是 ``Simulation.run``
    与已退役的 ``_PreviewSim.run`` 同一双手（修前三处纪律各漂移一份）；
    现役唯一消费方 = ``Simulation.run``，真面锁 =
    test_public_surface_contract.py（kwargs 拒）与 test_run_lifecycle.py
    （G136 极性）。"""
    if sim._job_id is not None and any(v is not None for v in (gpu, timeout_s)):
        raise RuntimeError(
            "cannot change gpu/timeout_s after submission (the "
            "config was already sent) — create a new connect() "
            "instance and reconfigure")
    if gpu is not None:
        # R23 S1：对齐 builder 整数槽纪律——int() 静默收编
        # （2.5→2、True→1、"2"→2）会带错误数量语义；这里不是
        # 表达式槽，str 也拒（站长裁"全拒，要求传 int"）。
        if isinstance(gpu, bool) or not isinstance(gpu, int):
            raise TypeError(
                f"gpu: optional cap — pass an int (e.g. gpu=2), "
                f"got {type(gpu).__name__}: {gpu!r}")
        sim._cfg.run.gpu = gpu
    if timeout_s is not None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int):
            raise TypeError(
                f"timeout_s: pass an int (seconds), got "
                f"{type(timeout_s).__name__}: {timeout_s!r}")
        sim._cfg.run.timeout_s = timeout_s


class Simulation:
    """一次云端仿真的全部状态：本地配置 + 作业生命周期 + 结果缓存。

    状态机（同本地包）：``defined → built → running → done``；
    failed/killed 在 run() 里抛 GenieJobError（本地是异常/终态）。
    ``_cfg`` 是 SimConfig 模型实例（framework §四）：构造期 construct
    容忍半成品（必填未设就缺着），完整性在提交边界一次性校验
    （``_validate_for_submit``）。
    """

    def __init__(self, client):
        self._client = client
        self._cfg = SimConfig.model_construct(
            domain=DomainSpec.model_construct(),
            mesh=MeshSpec.model_construct(),
            materials=[], geometry=[], source=[], monitor=[],
            run=RunSpec.model_construct(),
        )
        self._rebind()
        self.state = "defined"
        self._job_id: str | None = None
        self._arrays: dict | None = None   # npz 解包缓存（get_* 惰性共享）
        self._results: dict[tuple, Any] = {}   # (类型, 名) → Result 对象缓存
        self._monitors_meta: dict = {}         # G95：manifest {名: meta}
        self._result_local_path: str | None = None
        # K4（R18，站长裁 A）：提交路径锁——并发 run() 的"双查 _job_id
        # 都是 None → 双提交"窗口是网络 I/O 级（GIL 会释放），后果 =
        # 双计费 + 孤儿作业。锁内完成 参数落 cfg + submit。
        self._run_lock = threading.Lock()

    # ---- 配置 ----

    def to_config(self) -> dict:
        """SimConfig JSON（``model_dump(mode="json")``——线格式 JSON 类型；
        dense：默认值显式在场，GUI 侧新契约。渲染字节由 golden 锁与
        稀疏形态逐字节一致）。"""
        import warnings
        with warnings.catch_warnings():
            # construct 工作区按裸容器存值（list 进 tuple 字段等）——dump
            # 透传时 pydantic 会发 serializer 提示（值与注解不完全一致的
            # 提醒；对未校验工作区是预期态，提交边界才校验）。压制之，
            # 输出形状不受影响。
            warnings.filterwarnings(
                "ignore", message="Pydantic serializer warnings")
            return self._cfg.model_dump(mode="json")

    def load_config(self, cfg: dict) -> "Simulation":
        """把一份 SimConfig JSON 装载进当前实例（示例配置 / 作业回载的
        payload / agent 生成的配置）。深构造整替——之后可继续用链式
        方法改、``to_script()`` 导出、``run()`` 提交。未知字段显式拒
        （防静默丢），值守卫不在本地逐个做——与服务端同把手的完整
        校验在提交边界（``_validate_for_submit``）。

        本方法是「SDK 单轨」的基石：examples 起步、UI 作业回载的等价
        物、agent/MCP 的提交轨道（spec.execute_tool）都经它进入 SDK 层。
        """
        if not isinstance(cfg, dict):
            # K2（R18）：形状错归一为 GenieConfigError——同层
            # _construct_tree 对同类输入抛 GenieConfigError，此前这里是
            # 裸 TypeError（调用方按 Genie 系异常分支生存）。
            raise GenieConfigError(
                f"load_config requires a dict (SimConfig JSON), got "
                f"{type(cfg).__name__}")
        self._cfg = _construct_tree(SimConfig, cfg, "cfg")
        # 重建七个配置对象视图（它们持有 _cfg 子模型的引用）
        self._rebind()
        return self

    def _rebind(self) -> None:
        c = self._cfg
        self.domain = Domain(c.domain)
        self.mesh = Mesh(c.mesh)
        self.mat = Materials(c.materials)
        self.geom = Geometry(c.geometry)
        self.src = Source(c.source)
        self.mon = Monitor(c.monitor)

    def to_script(self) -> str:
        """当前配置 → genie 脚本文本（与 Studio 代码生成器逐字节一致，
        字节锁测试见 tests/test_script.py）。溯源/分享/论文用。
        （R40 起头行 = 零参 ``genie.connect()``，``origin`` 参数退役。）"""
        from .script import render
        return render(self.to_config())

    @property
    def job_id(self) -> str | None:
        return self._job_id

    # ---- 全局参数表（方言「参数与表达式」节）----

    def parameters(self, table: dict) -> "Simulation":
        """设置/合并全局参数表（COMSOL 式 {名: 数值|表达式串}），供
        全配置数值字段的表达式槽与 sweep 轴引用::

            f.parameters({'d': 1e-6})
            f.mesh.dx = 'd / 4'
        合并语义（不整表替换）——多次调用累积。"""
        if not isinstance(table, dict):
            # R23 S3：形状错归一 GenieConfigError（K2/G85 同口径——
            # load_config 对同类输入即 GenieConfigError，此前这里裸
            # TypeError，调用方按 Genie 系异常分支接不住）。
            raise GenieConfigError(
                f"parameters() requires a dict ({{name: value or "
                f"expression string}}), got {type(table).__name__}")
        cur = getattr(self._cfg, "parameters", None) or {}
        cur.update(table)
        self._cfg.parameters = cur
        return self

    # ---- 生命周期 ----

    def _submit(self) -> "Simulation":
        """序列化配置 → POST /api/jobs → 记住 job_id（run 的内部第一步，
        不做产品面——提交只经 run()）。同一实例只提交一次：镜像本地
        「改配置须新建实例」语义。提交前本地预检一次（与服务端同一双
        手的 resolve + validate，fail-fast；payload 始终原文 dump）。"""
        if self._job_id is not None:
            raise RuntimeError(
                "this instance was already submitted (job "
                "{id}) — create a new connect() instance to change the "
                "config".format(id=self._job_id))
        if getattr(self._cfg.domain, "total_time", None) is None:
            # G85（R18 清账）：配置缺失归一 GenieConfigError——其余同类
            # 缺失（validate_for_ui 路径）都是这个类型，此前这里是
            # RuntimeError（同一份半成品配置两种异常型）。
            raise GenieConfigError(
                "domain.total_time is missing (the single time "
                "definition, SI seconds > 0) — set f.domain.total_time "
                "= 6e-14 before submitting")
        cfg = self.to_config()
        _validate_for_submit(cfg)
        self._job_id = self._client.submit(cfg)
        self.state = "built"
        return self

    def run(self, *, gpu: int | None = None,
            timeout_s: int | None = None, poll_interval: float = 2.0,
            progress=None) -> "Simulation":
        """提交（隐含 submit）→ 轮询到终态的同步入口。

        仿真时长 = domain.total_time（G18 单旋钮：唯一时间定义，SI 秒；
        作业推进到网格时间容量为止，步数是 build 后派生量）。其余是
        RunSpec 服务端字段：gpu/timeout_s；``progress(status_dict)``
        每轮询一轮回调一次（打进度条用）。

        gpu = **可选上限**（int ≥1；None = 交由调度器决定）。跨仓契约
        （2026-10-25 定案，与手册 07-run / 网关 _decide_n 同口径）：
        作业给几张卡由网关按等级上限与需求裁定；装不下 → 提交期 422
        （明确报错）；无空闲卡 → 排队等待（不是失败）。设备挑选/NV
        邻接链/切轴全部调度器内部自动。
        """
        # G136 挡板（R18，站长裁 A）：终态实例的重入语义——
        # · done → 直接返回：重查无收益（结果不会变），只会踩归档陷阱
        #   （服务端 janitor 已归档 → state=archived → get_* 的 state
        #   挡板把缓存+本地落盘全拦死，一次误触打死可用实例）；
        # · failed/killed/archived → 明确报错指路：results() 可读本地
        #   落盘，重跑请新开 connect() 实例。
        # 保留的正路：state=running（本地超时后）无参重入 = 纯续查。
        if self.state == "done":
            return self
        if self.state in ("failed", "killed", "archived"):
            raise RuntimeError(
                f"job {self._job_id} already finished with state "
                f"{self.state!r} — results stay available via results()"
                + (" (local file saved)" if self._result_local_path else "")
                + "; to re-run, create a new connect() instance")
        # K4：锁内完成"改参检查 + 参数落 cfg + 提交"（并发 run() 防双提交）
        with self._run_lock:
            _apply_run_kwargs(self, gpu, timeout_s)
            if self._job_id is None:
                self._submit()
        self.state = "running"
        try:
            st = self._client.wait(self._job_id, poll_interval=poll_interval,
                                   progress=progress)
        except GenieJobError as e:
            # 状态机锚（audit 批 α 2026-09）：failed/killed 落进 state，
            # 不冒充 "running"（本地包异常后实例状态可见失败的同款语义）。
            # _result_arrays 的终态挡板继续拒取结果，语义不冲突。
            self.state = e.state            # "failed" / "killed"
            raise
        self.state = "done"
        self._auto_save_result()
        return self

    def cancel(self) -> dict | None:
        """取消已提交的后端作业（无作业则无事可做，返回 None）。"""
        if self._job_id:
            return self._client.cancel(self._job_id)

    def visualize(self, mon: str, field: str | None = None) -> None:
        """把该监视器推到 Studio 的 Plot 交互面板（G148 定案：交互只住
        GUI——SDK 面零 matplotlib）。两条触发腿：**会话内脚本**（Studio
        编辑器/终端——worker 给实例注入会话版 visualize，emit ``plot``
        事件，本方法被实例属性遮蔽）；**外部 SDK 进程**（组合根 hook →
        会话注入端点，G148 批 4b）。未接线（无会话）= 明确报错指路。
        自由画图不是产品面：拿 ``results()`` 的 ndarray 自己写 Python。"""
        hook = hooks.get("visualize")
        if hook is None:
            raise RuntimeError(
                "visualize needs a GUI session — open `genie studio` and "
                "run the script inside it (the session wires visualize), "
                "or connect via a session (bare connect) for the external "
                "push channel; there is no local plotting fallback by "
                "design (G148)")
        return hook(self, mon, field)

    def _auto_save_result(self) -> None:
        """run() 成功后自动下载结果到 SDK 所在电脑（genie-results-h5/1，
        2026-10-09 站长裁决：SDK 用户的仿真结果应当机落盘）。
        落盘：$GENIE_RESULT_DIR 或 ./genie_results/<job_id>_results.h5
        （覆盖式）；失败仅告警不抛——不吞 run 的成功语义，results()
        需要时会现场重取。"""
        try:
            import os
            from pathlib import Path
            raw = self._client.fetch_result(self._job_id)
            d = Path(os.environ.get("GENIE_RESULT_DIR") or "genie_results")
            d.mkdir(parents=True, exist_ok=True)
            pth = d / f"{self._job_id}_results.h5"
            pth.write_bytes(raw)
            os.chmod(pth, 0o600)     # K7：双 import os 已并一
            self._result_local_path = str(pth)
            print(f"[genie] 结果已自动下载: {pth}（{len(raw)} 字节）", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[genie] 结果自动下载失败（results() 会现场重取）: {e!r}",
                  flush=True)

    def sweep(self, parameters=None, *, gpus_per_sim: int = 1,
              max_parallel: int = 1, on_failure: str = "continue",
              mode: str | None = None) -> "SweepHandle":
        """参数扫描（G29b 客户端编排；R24c run() 化重写）→ SweepHandle。

        - ``parameters``：轴列表，每项 ``{"name": 参数名, "values" |
          "linspace" | "logspace": …}``；name 必须在基底配置 parameters
          键集内（本地展开期裁决，SweepExpandError 调用点即抛——G92：
          规格形状/展开/校验错误统一此类型）。
        - 每点 = 一个独立 Simulation 实例走 **run() 全原语**：本地校验、
          auto-save（逐点 h5 落会话结果目录）、visualize/结果面随原语。
        - ``gpus_per_sim``：每点作业的 gpu 上限（run(gpu=…)——旋钮真实
          生效，G90 构造性修复）。
        - ``max_parallel``：并发窗（缺省 1 = 顺序；受配额上限约束）。
        - ``on_failure``：``continue``（缺省，坏点记 failed 继续）/
          ``abort``（首坏点停排新点 + 取消在途——G89 重写语义）。
        聚合/下载/逐点句柄见 SweepHandle。基底配置 = 当前实例收集的配置；
        与 run() 不同，sweep 不占用本实例的 job_id（每点自带 job）。
        """
        points, _axis_names = self._sweep_points(
            parameters, gpus_per_sim=gpus_per_sim,
            max_parallel=max_parallel, on_failure=on_failure, mode=mode)

        hooks_factory = hooks.get("sweep_sim_factory")
        sims = []
        for pt in points:
            s = (hooks_factory() if hooks_factory
                 else Simulation(client=self._client))
            s.load_config(pt)
            sims.append(s)
        return SweepHandle(sims, _axis_names,
                           points=points, client=self._client,
                           gpus_per_sim=gpus_per_sim,
                           max_parallel=max_parallel, on_failure=on_failure)

    def _sweep_points(self, parameters, *, gpus_per_sim: int,
                      max_parallel: int, on_failure: str,
                      mode: str | None) -> tuple[list[dict], list[str]]:
        """sweep 规格校验 + 本地展开（**纯本地零网络**）→ (points, 轴名)。

        R28 S1 提取（站长裁）：轴形态/轴名在基底 parameters 键集/逐点
        展开，这层纯本地校验**在 sweep() 调用点即做**——坏规格当场
        SweepExpandError，不留到提交边界之后（历史：修前已退役的
        preview 面曾把 sweep 整个换成宽容占位，连本地校验也吞掉，
        坏轴名要到会话执行才炸）。"""
        from ..models import SweepSpec, MAX_GPU_PER_RUN
        from ..models.sweep_expand import (SweepExpandError,
                                           validate_sweep_config)
        from pydantic import ValidationError
        if parameters is None:
            raise SweepExpandError(
                "sweep 需要 parameters 轴列表"
                "（每项 {name, values|linspace|logspace}）")
        # R39（G169 C+）：执行旋钮早校验接管——旋钮自 SweepSpec 退役后
        # 这里是它们唯一的校验点。上限引跨仓契约常量 MAX_GPU_PER_RUN
        # （= RunSpec.gpu 的 le=4，单一来源；修前 SweepSpec 抄成 le=8，
        # 5..8 的扫描定义过了本地校验、每个点提交时才死——G169 病根）。
        if not 1 <= int(gpus_per_sim) <= MAX_GPU_PER_RUN:
            raise SweepExpandError(
                f"gpus_per_sim must be 1..{MAX_GPU_PER_RUN} "
                f"(same cap as run(gpu=…)), got {gpus_per_sim!r}")
        if not 1 <= int(max_parallel) <= 16:
            raise SweepExpandError(
                f"max_parallel must be 1..16, got {max_parallel!r}")
        if on_failure not in ("continue", "abort"):
            raise SweepExpandError(
                f"on_failure must be 'continue' or 'abort', "
                f"got {on_failure!r}")
        axes = []
        for a in parameters:
            if not isinstance(a, dict) or "name" not in a:
                raise SweepExpandError(
                    f"轴须 dict 且带 name（参数名轴形态）：{a!r}")
            given = [k for k in ("values", "linspace", "logspace") if k in a]
            if len(given) != 1:
                raise SweepExpandError(
                    f"轴 {a['name']!r} 取值三选一（values/linspace/logspace），"
                    f"给了 {given}")
            axes.append({"name": a["name"], given[0]: a[given[0]]})
        # SweepSpec = 纯定义（parameters/mode）；执行旋钮不进规格（R39）
        spec: dict[str, Any] = {"parameters": axes}
        if mode is not None:
            spec["mode"] = mode
        # G92：规格形状错（pydantic）与展开错（轴名/逐点）统一 SweepExpandError
        try:
            spec_model = SweepSpec.model_validate(spec)
        except ValidationError as e:
            first = str(e.errors()[0].get("msg", e))
            raise SweepExpandError(f"sweep 规格不合法：{first}") from e
        points = validate_sweep_config(spec_model, self.to_config())
        return points, [a["name"] for a in axes]

    # ---- 结果 ----

    def _result_bytes(self) -> bytes:
        """结果字节单一来源（G93/S5，R18 清账）：本地落盘优先——
        auto_save 在 run() 成功时已下载过，同进程 get_*/results() 不再
        二次下载（修前 get_* 每次重取整份 h5）；无本地文件才现场
        fetch（后端已归档时两条路在同一处统一抛 410，不再一生一死）。
        显式取他人/历史作业不走这里（results(job_id=…) 直连）。"""
        from pathlib import Path as _P
        local = getattr(self, "_result_local_path", None)
        if local and _P(local).is_file():
            return _P(local).read_bytes()
        return self._client.fetch_result(self._job_id)

    def _guard_done_for_fetch(self) -> None:
        """取数前置守卫（get_* 与 results() 共用，G138/R18）：未成功
        终态 → RuntimeError + 日志尾（不裸打服务端吃 409/410——两条
        取数路径异常面统一）。"""
        if self.state == "done":
            return
        tail = ""
        if self._job_id is not None:
            try:
                tail = (self._client.log(self._job_id) or "")[-400:]
            except Exception:  # noqa: BLE001 —— 日志取不到不拦守卫
                tail = ""
        raise RuntimeError(
            f"job did not finish successfully (state={self.state}); "
            f"no results to fetch. Job {self._job_id} ended in a "
            f"failure state"
            + (f"; log tail:\n{tail}" if tail else
               " (use client.log(job_id) for the full log)"))

    def _result_arrays(self) -> dict:
        """结果 h5（genie-results-h5/1）→ 平铺 key 字典。

        h5 结构：/<监视器>/<场或轴>，组 attrs["domain"] ∈ {f,t,i}；
        展平回沿用约定 ``<域>@<监视器>.<场>``——下游
        TimeResult/FreqResult.from_npz/_extract 零改动。"""
        self._guard_done_for_fetch()
        if self._arrays is None:
            import h5py
            import io
            import json as _json

            with h5py.File(io.BytesIO(
                    self._result_bytes()), "r") as h5:
                from .results import ensure_supported_format
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
                self._monitors_meta = meta
                self._arrays = arrays
        return self._arrays

    def results(self, job_id: str | None = None) -> "SimData":
        """拉取作业结果 → SimData 结构体（genie-results-h5/1）。

        ``data.<监视器>.<场>`` 直取（h5py 惰性读）；
        ``data.attrs["executed_steps"]`` 实际执行步数。job_id 缺省 =
        本 Simulation 最近一次提交的作业。（画图走 Studio 的 Plot
        面板——R36 起画图单一实现，SDK 不再内置 matplotlib 面。）"""
        from .results import SimData, ensure_supported_format
        import h5py
        import io

        jid = job_id or self._job_id
        if jid is None:
            # R23 C1：先答"还没提交"再谈终态——此前守卫先炸，未提交
            # 实例得到 "Job None ended in a failure state" 的误导诊断
            # （专用错误是死代码）。
            raise RuntimeError(
                "no job submitted yet — results() needs a job_id")
        # R23 S2：本实例作业（缺省或显式传自己的 id）都过终态守卫——
        # G138"两条取数路径异常面统一"对显式形态也成立（修前显式传
        # 自己的 id 吃裸 409）；他人/历史 id 仍直取不守。
        if job_id is None or job_id == self._job_id:
            self._guard_done_for_fetch()
        if job_id is None or job_id == self._job_id:
            raw = self._result_bytes()
        else:
            raw = self._client.fetch_result(jid)
        from .results import ensure_supported_format
        h5 = h5py.File(io.BytesIO(raw), "r")
        ensure_supported_format(h5)   # R23 G-a：两条取数路径同一校验
        return SimData(h5, job_id=jid)

    def _find_monitor(self, mtype: str, name: str | None) -> dict:
        for m in self._cfg.monitor:
            if getattr(m, "type", None) != mtype:
                continue
            if name is None or getattr(m, "name", None) == name:
                return m
        raise KeyError(
            f"{mtype} monitor not found"
            + (f" {name!r}" if name else " (none defined in the config)"))

    def _get_result(self, mtype: str, name: str | None, cls):
        """get_* 公共路径：先核作业终态（未成功直接报，不谈监视器）→
        找监视器配置 → （缓存）构造 Result。"""
        arrays = self._result_arrays()
        entry = self._find_monitor(mtype, name)
        mname = getattr(entry, "name", None)
        key = (mtype, mname)
        if key not in self._results:
            # G95：manifest 的 shape 优先（正源）；缺失回退轴元素数猜测
            self._results[key] = cls.from_npz(
                arrays, mname,
                shape_hint=(self._monitors_meta.get(mname) or {}).get("shape"))
        return self._results[key]

    def get_time(self, name: str | None = None):
        from .results import TimeResult
        return self._get_result("time", name, TimeResult)

    def get_freq(self, name: str | None = None):
        from .results import FreqResult
        return self._get_result("freq", name, FreqResult)

    def get_index(self, name: str | None = None):
        from .results import IndexResult
        return self._get_result("index", name, IndexResult)

    # ---- 概览 ----

    def summary(self) -> dict:
        """配置摘要 + （已提交时）服务端作业摘要。打印并返回 dict。"""
        cfg = self.to_config()
        d = {
            "domain": {k: v for k, v in cfg["domain"].items()},
            "mesh": {k: v for k, v in cfg["mesh"].items()},
            "n_materials": len(cfg["materials"]),
            "n_geometry": len(cfg["geometry"]),
            "n_source": len(cfg["source"]),
            "monitors": [f"{m.get('name')}({m.get('type')})"
                         for m in cfg["monitor"]],
            "run": dict(cfg["run"]),
            "state": self.state,
            "job_id": self._job_id,
        }
        if self._job_id is not None:
            try:
                d["server_summary"] = self._client.summary(self._job_id)
            except Exception as e:  # noqa: BLE001 —— 摘要失败不拦主流程
                d["server_summary"] = f"(不可用: {e})"
        print("── GENIE 仿真摘要 " + "─" * 40)
        for k, v in d.items():
            print(f"  {k}: {v}")
        return d

    def _digest_from_local(self, jid) -> dict | None:
        """本地 auto-save 的 h5 里读 digest attrs（G170：fdtd 随结果带出，
        0 次 HTTP）。文件不在/缺键/坏 JSON → None（调用方回退 GET）。"""
        p = getattr(self, "_result_local_path", None)
        if not p:
            return None
        try:
            import h5py
            from .results import SimData
            with h5py.File(p, "r") as h5:
                return SimData(h5, job_id=jid).digest
        except Exception:  # noqa: BLE001 —— 本地读不到就走 HTTP，不拦
            return None

    def digest(self, job_id: str | None = None) -> dict:
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
        jid = job_id or self._job_id
        if jid is None:
            raise RuntimeError(
                "no job_id to query the digest for (instance not "
                "submitted and no job_id passed) — call run() first, or "
                "pass digest(job_id) explicitly")
        if job_id is None or job_id == self._job_id:
            d = self._digest_from_local(jid)
            if d is not None:
                return d
        return self._client.digest(jid)
