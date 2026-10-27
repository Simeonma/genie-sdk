"""脚本 → 模型：执行捕获引擎（genie-service Studio 的心脏）。

定位
----
v2 定案「脚本是本体」：GUI 不再有 cfg 中心，显示所需的模型 = **在本地
把脚本执行一遍、捕获其构建结果**。本模块就是这扇门::

    import genie
    r = genie.preview_script(source)
    # {"ok": bool, "cfg": SimConfig dict, "stdout": str, "stderr": str,
    #  "error": None | {"kind", "message", "line", "traceback"}}

机制（trace，不做静态翻译）
--------------------------
临时把 ``genie.connect`` 换成零网络工厂（``_PreviewSim``），``exec``
脚本文本；builder 链式调用天然逐条落进 ``_cfg``（``_Chain`` 本就是
记录器）。由此：

  · ``for`` 循环 = 执行 N 次 = cfg 里 N 个节点（天然"编译展开"）；
  · numpy/任意 Python 表达式 = 编译期求值成字面量；
  · 脚本里的普通 ``def``（含 ``asm_<名>`` 命名的组装体函数——就是普通
    Python 函数）照常定义与调用，产出的节点平铺进各列表（2026-10
    阶段 2：asm 归属归因/纯化/写回已按 framework §十 作废删除，
    不再有块级视图与只读保护）；
  · ``f.run()`` 只录制 ``run`` 节（gpu/timeout_s），**不提交**；
  · ``f.get_*()/f.sweep()/summary/digest`` 返回宽容占位（``_Inert``）
    ——后处理脚本不崩，但拿不到真数据（预览不做作业）；
  · ``print`` 输出被捕获随结果返回（GUI 诊断窗）。

失败语义 = 带行号的诊断（不静默）：语法错/运行时异常 → ``ok=False`` +
错误定位（脚本帧行号）+ traceback 尾部；已捕获的部分 cfg 照样返回
（改到哪行一目了然）。``genie.connect`` 无论成败都在 ``finally`` 里
恢复原函数。

纪律：零网络（connect 被换掉后无任何 requests 路径可达）、零 fdtd
import（同 ``_script.py``，无内核机可用）。
"""
from __future__ import annotations

import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from .simulate import Simulation, _apply_run_kwargs

__all__ = ["preview_script"]


# ── 组合根钩子（G1 分层契约）──────────────────────────────────────────────
# 本模块要执行**用户脚本**，而用户脚本会 ``import genie`` 调 ``genie.connect``
# ——那是公开面（上层）。core 不能反向 import 它（`test_layering.py` 的
# 分层契约），所以照 simulate/material 的老规矩：**组合根注入能力**。
# 公开面的 ``genie/__init__.py::_swap_connect`` 在 import 时注册进来，
# 负责临时把 ``genie.connect`` 换成零网络工厂并返回 restore 回调。
_swap_connect = None


def _wire(connect_swap) -> None:
    """组合根接线：注入"临时替换 connect"的能力（只由 ``genie/__init__`` 调）。"""
    global _swap_connect
    _swap_connect = connect_swap


class _Inert:
    """宽容占位对象：预览模式下 get_*/sweep 句柄的返回值。

    只求"脚本不因取结果而崩"（显示用），不求语义——属性/下标/调用/
    迭代全接管，str/repr 明示占位身份。"""

    __slots__ = ()

    def __getattr__(self, name: str) -> "_Inert":
        return _Inert()

    def __getitem__(self, key: Any) -> "_Inert":
        return _Inert()

    def __call__(self, *a: Any, **k: Any) -> "_Inert":
        return _Inert()

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<preview 占位：预览模式不执行作业>"

    def __str__(self) -> str:
        return "<preview 占位>"

    def __format__(self, spec: str) -> str:
        # R28 P4：数值类 spec（".3f"/"d"）对 str 非法——修前
        # f"{t:.3f}" 直接 ValueError，宽容占位对最常见的 f-string
        # 打印形态反而不宽容。非法 spec 退占位字符串。
        try:
            return format(str(self), spec)
        except ValueError:
            return str(self)


class _PreviewSim(Simulation):
    """零网络 Simulation：run/sweep/get_* 录制不执行。

    继承全部 builder 面（domain/mesh/mat/geom/src/mon 与 to_config/
    load_config/to_script 原样可用）；只覆写会碰网络的生命周期方法。
    """

    def __init__(self) -> None:
        super().__init__(client=None)

    # ---- 生命周期（录制，不提交）----

    def _submit(self) -> "_PreviewSim":
        # G98（R24 站长裁"删硬门，兑现宽容"）：preview 对半成品宽容——
        # 缺必填是常态，total_time 门只在真提交边界（Simulation._submit）
        # 有意义；预览只录占位 id。异常型/文案手抄（G85 修复前的中文旧版）
        # 随本门一并消解。
        self._job_id = "preview"          # 占位 id：防同实例二次提交语义漂移
        self.state = "built"
        return self

    def run(self, *, gpu: int | None = None,
            timeout_s: int | None = None, poll_interval: float = 2.0,
            progress=None) -> "_PreviewSim":
        """录制 run 节后即视为完成（不提交、不轮询）。kwargs 纪律与真
        run 共享同一双手（``simulate._apply_run_kwargs``，G102 R24 收敛：
        改参拒/R23 S1 整数槽一处实现）；done 态重入 = 幂等返回（与真面
        G136 挡板同极性，R24 站长裁 F4）；与真 run 不同处：不过提交边界
        预检——preview 对半成品宽容（缺必填是常态，GUI 只显示已建造
        部分）。"""
        if self.state == "done":
            return self
        _apply_run_kwargs(self, gpu, timeout_s)
        if self._job_id is None:
            self._submit()
        self.state = "done"
        return self

    def sweep(self, parameters=None, *, gpus_per_sim: int = 1,
              max_parallel: int = 1, on_failure: str = "continue",
              mode: str | None = None, **_ignored: Any) -> _Inert:
        """R28 S1（站长裁）：**纯本地校验照做**——规格/轴名/展开错当场
        SweepExpandError（信封带行号，check_script 体检承诺兑现；修前
        preview 把 sweep 整个吞成 _Inert，坏轴名要到会话执行才炸）。
        校验过后宽容占位（预览不跑作业）。与真面共享 ``_sweep_points``
        同一双手，无第二份校验实现。"""
        self._sweep_points(parameters, gpus_per_sim=gpus_per_sim,
                           max_parallel=max_parallel,
                           on_failure=on_failure, mode=mode)
        return _Inert()

    def visualize(self, *a: Any, **k: Any) -> _Inert:
        """R28 P2（G160/G162 同族第三瓣）：预览不推图——真面 visualize
        走会话 hook/结果数据（`_client=None` 必崩且该行后构建全丢，
        手册 09 教的 `f.run(); f.visualize('pt')` 写法在预览整份进脚本
        即炸），与 results/get_*/expert 同口径宽容占位。"""
        return _Inert()

    def results(self, job_id: str | None = None) -> _Inert:
        """G160（R24 站长裁"补覆写+锁"）：预览不执行作业——results 与
        get_* 同为宽容占位（修前漏覆写，`_client=None` 直接 AttributeError
        崩预览，手册 09 的后处理写法在 live 通道整份进 preview 必炸）。"""
        return _Inert()

    def cancel(self) -> None:
        """G160（R24）：预览无作业可取消——与真面"无作业返回 None"
        同语义（修前漏覆写走 `_client.cancel` 崩）。"""
        return None

    @property
    def expert(self) -> _Inert:
        """G162（R24b）：预览不问专家——与 get_*/results 同为宽容占位
        （修前继承真面 expert 属性，`_client=None` 走 expert_post 直接
        AttributeError 崩预览，该行之后的构建全部丢失）。"""
        return _Inert()

    def get_time(self, name: str | None = None) -> _Inert:
        return _Inert()

    def get_freq(self, name: str | None = None) -> _Inert:
        return _Inert()

    def get_index(self, name: str | None = None) -> _Inert:
        return _Inert()

    def summary(self) -> dict:
        return {"preview": True}

    def digest(self, job_id: str | None = None) -> dict:
        return {"preview": True}


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
    行为与 to_config() 逐键同形——smoke 往返锚不变）。domain/mesh/
    列表维持"后者覆盖/按序拼接"（每实例新配置语义，站长同轮裁）。

    双消费方 = preview_script 与 studio.kernel_worker._merged（会话
    提交/快照同一口径），改这里两侧同愈。"""
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
    （JSON 严格语法无 NaN 字面量）。preview 的 cfg 会直接序列化为 JSON
    （GUI/端点），出口处归一回线格式正形——顺带消灭 pyworker 时代
    json.dumps 产出裸 NaN 非法 JSON 的潜在坑（2026-10 阶段 ②）。"""
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, dict):
        return {k: nan_to_none(x) for k, x in v.items()}
    if isinstance(v, list):
        return [nan_to_none(x) for x in v]
    return v


def preview_script(source: str, *, label: str = "<genie脚本>") -> dict:
    """执行 genie 脚本（零网络捕获）→ 显示模型。

    返回 dict：``ok``（执行到底与否）、``cfg``（SimConfig JSON，异常时
    为已捕获部分）、``stdout``（print 输出）、``stderr``（warnings 与
    stderr 输出，G164 起单独回流——此前漏到宿主进程，诊断面盲区）、
    ``error``（None 或 ``{kind, message, line, traceback}``，
    line = 脚本帧行号）。
    """
    if _swap_connect is None:
        raise RuntimeError(
            "preview 未接线：需要组合根先 import genie（见 core/preview._wire）")

    out: dict[str, Any] = {"ok": False, "cfg": {}, "stdout": "",
                           "stderr": "", "error": None}

    # ① 语法先行（compile 给出带行号的 SyntaxError）
    try:
        code = compile(source, label, "exec")
    except SyntaxError as e:
        out["error"] = {"kind": "syntax", "message": str(e.msg or e),
                        "line": e.lineno or 0, "traceback": ""}
        return out

    # ② exec：connect 换零网络工厂 + stdout 捕获；finally 恢复
    sims: list[_PreviewSim] = []

    def _fake_connect(*args: Any, **kwargs: Any) -> "_PreviewSim":
        # G97（R24 站长裁"全吞"）：预览不消费任何凭据/地址参数，签名与
        # 公开面 connect 全吞对齐（同 kernel_worker 的 session_connect）
        # ——公开面今后加参数，预览面结构上不可能再 TypeError（修前
        # 带 password 的文档化直连轨脚本预览直接崩）。
        sim = _PreviewSim()
        sims.append(sim)
        return sim

    buf = io.StringIO()
    err_buf = io.StringIO()      # G164（R24b）：warnings/stderr 单独捕获
    ns: dict[str, Any] = {"__name__": "__main__", "__file__": label}
    restore = _swap_connect(_fake_connect)      # 组合根负责换/还原公开面
    try:
        with redirect_stdout(buf), redirect_stderr(err_buf):
            # __file__ 给标签名：脚本引导块（sys.path/Path(__file__) 类）
            # 在预览态不 NameError，路径退化为无害垃圾（2026-10 live 通道实测）
            exec(code, ns)
        out["ok"] = True
    except BaseException as e:  # noqa: BLE001 —— 诊断对象，不吞信息
        tb_text = traceback.format_exc(limit=8)
        line = 0
        tb_e = e.__traceback__
        while tb_e is not None:            # 由内向外找脚本帧行号
            if tb_e.tb_frame.f_code.co_filename == label:
                line = tb_e.tb_lineno
            tb_e = tb_e.tb_next
        out["error"] = {"kind": type(e).__name__, "message": str(e),
                        "line": line, "traceback": tb_text}
    finally:
        restore()

    out["stdout"] = buf.getvalue()
    out["stderr"] = err_buf.getvalue()
    # G163（R24b 站长裁 A，与会话面 _insts_of 同口径）：扫命名空间收
    # 直建实例——脚本可以 `f = genie.Simulation(client=None)` 绕开
    # connect，"预览显示脚本建造的模型"对任何写法成立（崩溃路径同样
    # 收：已捕获部分照常返回）。按身份去重，connect 出来的已在 sims。
    # R28 P1：捕获/合并段同样在诊断信封之内——修前 parameters 透传槽
    # 塞不可序列化值（object()/ndarray）会让 to_config 在信封**之外**
    # 裸抛 PydanticSerializationError，check_script（LLM 自纠）/MCP/
    # GUI 预览整条崩，{ok,cfg,error} 契约对该阶段不成立。
    try:
        for v in ns.values():
            if isinstance(v, Simulation) and not any(v is s for s in sims):
                sims.append(v)
        if sims:
            # R28 P3：ran 标记实际 run 过的实例（判据 = _job_id 非空）
            out["cfg"] = nan_to_none(merge_configs(
                [s.to_config() for s in sims],
                ran=[getattr(s, "_job_id", None) is not None
                     for s in sims]))
    except Exception as e:  # noqa: BLE001 —— 信封承诺：不向调用方裸抛
        out["ok"] = False
        out["error"] = {
            "kind": type(e).__name__,
            "message": f"捕获失败：{e}（parameters 等透传槽的值需可 "
                       f"JSON 序列化）",
            "line": 0, "traceback": traceback.format_exc(limit=4)}
    if sims and out["cfg"]:
        # G19a（G3 方向 2）：设计态估算随行——格数/dt 估/步数估，与内核
        # 守卫同一把尺子（genie.models.estimate 单一真源）。preview 对
        # 半成品宽容：domain/mesh 缺字段就静默省略此节。
        try:
            from ..models._wire import DomainSpec, MeshSpec
            from ..models.estimate import mesh_preview_stats
            cfg = out["cfg"]
            if cfg.get("domain") and cfg.get("mesh"):
                out["estimate"] = mesh_preview_stats(
                    DomainSpec.model_validate(cfg["domain"]),
                    MeshSpec.model_validate(cfg["mesh"]))
        except Exception:              # noqa: BLE001 —— 估算失败不伤 preview
            pass
    return out
