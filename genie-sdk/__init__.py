"""GENIE SDK v2 — 用户在脚本里构建仿真、``f.run()`` 提交云端 GPU。

产品口径（2026-10 terminal 战役后；G174 起单执行引擎）：
  · SDK 用户：写 genie 脚本，``f`` 是活配置对象，``f.run()`` 提交那一刻
    才序列化 SimConfig JSON（线格式，用完即弃，客户端不存 cfg 状态）；
  · 云 GUI（Studio）背后是常驻会话进程里的同一个 SDK——**会话 f 是
    唯一真源**，Terminal/脚本/表单三条变更通道同汇一处（详见
    genie-service AGENTS.md）；本包不感知 GUI 形态；
  · **一个执行引擎（会话 worker）、一种语义（真执行——错了报错带
    行号自纠）**：G174（2026-10-28 站长三连裁决）退役了 preview 零网络
    预览引擎与 check_script 体检面，"验证"不是用户任务的步骤。

快速上手（API 手感与 v1 一致，零学习成本）::

    import genie

    f = genie.connect()           # 凭据经 genie login 注入本机（一次即可）

    f.domain.x_range = (-1e-6, 1e-6)
    f.domain.z_range = (-2e-6, 2e-6)
    f.domain.total_time = 60e-15
    f.domain.boundary_condition('z_min', 'pml', layers=12)

    f.mesh.dx = 25e-9
    f.mat.add_const().name('sio2').eps(2.10)
    f.geom.add_box().material('sio2') \\
        .x_range(-0.5e-6, 0.5e-6).y_range(-0.5e-6, 0.5e-6) \\
        .z_range(-0.1e-6, 0.1e-6).priority(1)
    f.src.add_plane_wave().freq(300e12).bandwidth(60e12).from_face('z_min')
    f.mon.add_freq().name('tr').center(0, 0, 500e-9) \\
        .freq(300e12, 120e12, 200)

    f.run()                        # 提交 + 轮询到终态（隐含 submit，同步语义；时长 = domain.total_time）
    fr = f.get_freq('tr')          # 纯数据：.freqs / .fields（隐含 download）
"""
from __future__ import annotations

import os
import sys as _sys

from .models.errors import (GenieConfigError, GenieError, GenieHTTPError,
                            GenieJobError)
from .core import material
from .core.results import FreqResult, IndexResult, TimeResult
from .core.simulate import Simulation
from .core.sweep import SweepHandle

# ``import genie.material`` 语句级可用（不只属性寻址）——注册进
# sys.modules 后属性寻址与语句级 import 皆通（material 实体在 core/）。
_sys.modules[__name__ + ".material"] = material

__version__ = "2.0.0"


# ── 组合根（G1 分层契约，2026-10-11 定案）───────────────────────────
# core 只依赖 models；上层能力（画图注入等）在此一次性
# 注入 core 的 hooks——``import genie`` 即接线，core 永不反向 import 上层。
# （2026-10-27 G19b 翻转：材质库 HTTP 回退 hook 已退役，材质库纯本地。）
# 分层守卫 = tests/sdk/test_layering.py。
def _push_visualize(sim, mon: str, field: str | None = None) -> None:
    """G148 批 4b 腿 B：外部 SDK 的 ``f.visualize`` → 会话注入端点。

    结果 h5 字节随行上传——外部作业的落盘在**用户机器**
    （``_auto_save_result``），会话工作区读不到，推图即推数。
    无门牌（无会话）= 明确报错指路；不做本地画图退化轨（站长定案）。"""
    if getattr(sim, "_job_id", None) is None:
        raise RuntimeError(
            "visualize: 本实例还没有作业——先 f.run()（推的是该作业的"
            "结果数据）")
    from .studio.session_client import SessionClient
    cli = SessionClient.discover()
    if cli is None:
        raise RuntimeError(
            "visualize: 找不到本机会话门牌——先 `genie studio` 开会话"
            "（在 Studio 里跑脚本则天然可用）；按定案无本地画图退化轨")
    cli.result_inject(job_id=sim._job_id, mon=mon, field=field or "",
                      data=sim._result_bytes())


def _push_sweep_visualize(sw, mon: str, field: str | None = None) -> None:
    """G182 P5c：扫描的 ``visualize``——同一个动词、同一条推送路径。

    推的是**合并结果**（数据多 k 维，坐标不复制）；会话面板已有的
    轴选择器/滑条把参数轴当普通轴用——画图实现只有一套（G148 定案：
    SDK 面零 matplotlib，交互只住 GUI）。"""
    sid = str(getattr(sw, "id", "") or "")
    if not sid:
        raise RuntimeError("visualize: 这个扫描还没有 id（sweep.json 缺 "
                           "sweep_id）——重跑一次 f.sweep() 即可")
    from .studio.session_client import SessionClient
    cli = SessionClient.discover()
    if cli is None:
        raise RuntimeError(
            "visualize: 找不到本机会话门牌——先 `genie studio` 开会话"
            "（在 Studio 里跑脚本则天然可用）；按定案无本地画图退化轨")
    cli.result_inject(job_id=sid, mon=mon, field=field or "",
                      data=sw._cube_bytes())


def _wire_core_hooks() -> None:
    from .core import simulate as _simulate
    from .core import sweep as _sweep

    _simulate._wire(
        visualize=_push_visualize,
    )
    _sweep.hooks.update(visualize_sweep=_push_sweep_visualize)
    # 2026-10-27 G19b 翻转：材质库 HTTP 回退（remote_nk_fetch）退役——
    # 材质库纯本地（genie/nkdata 随包分发），材质面不再接线。
    # 2026-10-28 G174：preview 的 connect 交换钩子（_swap_connect）随
    # 零网络预览引擎一并退役。
    # 2026-10-28 R36：expert_factory 钩子随专家会话面（net/expert.py、
    # f.expert、expert_post）整体退役——问/托不是产品主链的一环
    # （存在权清理，见 WAR_LOG R36；网关 /expert/* 端点的归宿归服务仓）。


_wire_core_hooks()


def _session_client():
    """MCP/工具面用的"会话把手"：读门牌接上正在跑的 Studio（没有 → None）。

    懒 import：``import genie`` 不该把 fastapi 拉进来（studio 是可选 extra）。
    注入方向也是组合根的老规矩——``integrations`` 不得依赖 ``studio``。
    """
    from .studio.session_client import SessionClient
    return SessionClient.discover()


def _wire_agent_surface() -> None:
    from .integrations import mcp as _mcp
    _mcp.set_session_factory(_session_client)


_wire_agent_surface()


# 公共表面：connect → Simulation（构建+提交）+ 结果对象 + 异常。
# Domain/Mesh/… 经 f.domain 等属性到达；Client 是内部传输层。
# ``material`` 是 client-side 纯函数子模块。
# （G174/R33：preview_script/check_script 公开名已随硬摘轮删除——
#  锁 = test_public_surface_contract「名字不在公开面」。）
__all__ = [
    "connect", "open", "open_sweep", "Simulation", "SweepHandle",
    "TimeResult", "FreqResult", "IndexResult",
    "GenieError", "GenieHTTPError", "GenieJobError", "GenieConfigError",
    "material",
    "__version__",
]


def connect(base_url: str | None = None, api_key: str | None = None,
            username: str | None = None, password: str | None = None, *,
            timeout: float = 30.0) -> Simulation:
    """连接 GENIE 服务 → Simulation（≈ 本地包的 ``FDTD()``）。

    正道 = **零参**（R40 定案 2026-10-29）：凭据经 ``genie login``（终端）
    或 Studio 登录闸注入本机凭据仓，一次即可；脚本永远 ``genie.connect()``。
    显式参数已退役——传任何连接参数都会被拒并指路；机器预置走 env
    （``GENIE_URL`` / ``GENIE_API_KEY`` / ``GENIE_USERNAME`` / ``GENIE_PASSWORD``）。

    两条轨：

    - **本机有活 Studio**（门牌探活通过）→ 搭车轨：经会话 ``/gw`` 代理，
      凭据由会话代持（token 不进脚本世界——这条纪律的真值场在 GUI）。
    - **其余一切情况** → 直拨轨：网关与 token 从 env/凭据仓解析
      （``resolve_gateway`` / ``resolve_credential``）；解析不出网关 →
      报错并给"下一步动作"。

    （R40/G109 定案：原"无门牌时进程内自起无头会话"引擎整体退役——
    三仓 grep 零消费者实证，同族 G176/G177 随之销账；存在权审查：
    自起服务的两条卖点——"token 不进脚本"在同机同用户下是安全戏、
    "console 可接上"从未生效——均答不出服务的真实用户任务。）
    """
    if base_url or api_key or username or password:
        from .models.errors import GenieConfigError
        raise GenieConfigError(
            "connect() 不接受连接参数（2026-10-29 起零参唯一形态）。\n"
            "  凭据注入：genie login（终端，一次即可）或 genie studio 登录闸\n"
            "  机器预置：set GENIE_URL / GENIE_API_KEY / GENIE_USERNAME"
            " / GENIE_PASSWORD")
    from .net import client as _client_mod
    from .net.config import resolve_credential, resolve_gateway

    if not os.environ.get("GENIE_URL"):
        # 搭车轨：机器没显式指网关（env）且本机有活 Studio 门牌
        card = _alive_card()
        if card is not None:
            return Simulation(_client_mod.Client(
                card["url"].rstrip("/") + "/gw", None, timeout=timeout,
                extra_headers={"X-Session-Token": card.get("token", "")}
                if card.get("token") else None))
    # 直拨轨（默认；env GENIE_URL 预置时直接走这）
    url = resolve_gateway(None)
    key = resolve_credential(None, "GENIE_API_KEY", "api_key")
    user = resolve_credential(None, "GENIE_USERNAME", "username")
    pwd = os.environ.get("GENIE_PASSWORD")
    client = _client_mod.Client(url, key, username=user,
                                timeout=timeout)  # 模块属性寻址——测试可注入假传输
    if pwd and not key:
        if not user:
            raise ValueError("password 登录需要同时给 username")
        client.login(user, pwd)
    return Simulation(client)


def _connect_direct(base_url: str, api_key: str | None = None, *,
                    timeout: float = 30.0) -> Simulation:
    """内部直连（会话 worker / 测试用）：不经门牌、不走公开参数面。"""
    from .net import client as _client_mod
    return Simulation(_client_mod.Client(base_url, api_key, timeout=timeout))


def open(path) -> Simulation:
    """打开一个 ``.gensim`` 仿真文件 → 载入其配置的 Simulation。

    **会话内**（Studio 终端 / agent）：整份替换当前会话模型并返回活实例
    ——与图形界面「打开仿真」同一语义（2026-10-30 站长定案）。会话外：
    新建实例载入配置（配好凭据即可直接 ``f.run()``）。两条轨都会把
    ``_doc_path`` 钉到这个文件：之后 ``run()`` 成功**原地更新它**
    （G182：文档是结果产物）。

    容器格式由 ``genie.core.simfile`` 维护（与 ``f.save`` / Studio
    的存储/打开同一份实现）。
    """
    from .core import simfile
    from .core import simulate as _simulate

    cfg = simfile.read_cfg(path)
    hook = _simulate.hooks.get("open_sim")
    if hook is not None:
        return hook(cfg, str(path))
    f = connect()
    f.load_config(cfg)
    f._doc_path = str(path)
    return f


def open_sweep(sweep_dir) -> "SweepSet":
    """打开一个**磁盘上的扫描文件夹**（G182 P5）→ ``SweepSet``。

    只读索引 + 点文档里的结果：``axes`` / ``points``（元数据）/
    ``rollup()`` / ``results()``；**不打开任何逐点仿真实例**（站长
    2026-10-30 裁：读取只要结果）。典型用法：

    ``sw = genie.open_sweep("D:/proj/sweeps/pitch-20261030-0217")``
    ``r = sw.results()``    # 与单跑同一个对象。r.tr.T 多 k 个前导维

    与 ``f.sweep()`` 的活句柄互补：一个跑，一个读；同一个文件夹。
    """
    from .core.sweep import SweepSet
    return SweepSet(sweep_dir)


def _alive_card() -> dict | None:
    """读门牌并探活；无牌/死牌/读不了 → None（调用方落直拨轨）。

    门牌纪律"不做存活探测"防的是**复用旧会话的模型态**；这里只用
    它的 /gw 代理（无状态），探测死门牌是为了不把用户引向死路。
    """
    try:
        from .studio import rendezvous as rv
    except ImportError:      # 异形安装（studio 缺件）：会话形态不存在
        return None
    card = rv.read()
    if not (card and card.get("url")):
        return None
    try:
        import requests as _rq
        if _rq.get(card["url"].rstrip("/") + "/health",
                   timeout=1.5).status_code != 200:
            return None
    except Exception:        # noqa: BLE001 —— 死门牌 = 没有会话
        return None
    return card
