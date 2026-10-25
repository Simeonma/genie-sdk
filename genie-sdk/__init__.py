"""GENIE SDK v2 — 用户在脚本里构建仿真、``f.run()`` 提交云端 GPU。

产品口径（2026-10 terminal 战役后）：
  · SDK 用户：写 genie 脚本，``f`` 是活配置对象，``f.run()`` 提交那一刻
    才序列化 SimConfig JSON（线格式，用完即弃，客户端不存 cfg 状态）；
  · 云 GUI（Studio）背后是常驻会话进程里的同一个 SDK——**会话 f 是
    唯一真源**，Terminal/脚本/表单三条变更通道同汇一处（详见
    genie-service AGENTS.md）；本包不感知 GUI 形态；
  · ``genie.preview_script`` 零网络执行脚本得模型（Studio 预览与
    agent 工具共用；与真提交互不相干）。

快速上手（API 手感与 v1 一致，零学习成本）::

    import genie

    f = genie.connect("https://genie.example.com", api_key="…")

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

Studio 显示（零网络，不提交作业）::

    import genie
    r = genie.preview_script(open("my_sim.py").read())
    r["ok"] and r["cfg"]           # 模型 = 脚本执行捕获的 SimConfig JSON
"""
from __future__ import annotations

import os
import sys as _sys

from .models.errors import (GenieConfigError, GenieError, GenieHTTPError,
                            GenieJobError)
from .core import material
from .core.preview import preview_script
from .core.results import FreqResult, IndexResult, TimeResult
from .core.simulate import Simulation
from .core.sweep import SweepHandle
from .integrations.agent_tools import check_script

# ``import genie.material`` 语句级可用（不只属性寻址）——注册进
# sys.modules 后属性寻址与语句级 import 皆通（material 实体在 core/）。
_sys.modules[__name__ + ".material"] = material

__version__ = "2.0.0a13"


def _swap_connect(fn):
    """临时把 ``genie.connect`` 换成 ``fn``，返回 restore 回调。

    只给 ``core.preview`` 用：预览要在**零网络**下执行用户脚本，而脚本会
    ``import genie`` 调 ``genie.connect``。交换动作属于公开面，所以由这里
    实现、经 hooks 注入——core 不反向 import 本模块（分层契约）。
    """
    global connect
    original = connect
    connect = fn

    def _restore() -> None:
        global connect
        connect = original

    return _restore


# ── 组合根（G1 分层契约，2026-10-11 定案）───────────────────────────
# core 只依赖 models；expert 的实现在上层（integrations），在此一次性
# 注入 core 的 hooks——``import genie`` 即接线，core 永不反向 import 上层。
# （2026-10-27 G19b 翻转：材质库 HTTP 回退 hook 已退役，材质库纯本地。）
# 分层守卫 = tests/sdk/test_layering.py。
def _wire_core_hooks() -> None:
    from .core import preview as _preview, simulate as _simulate
    from .net.expert import ExpertSession

    _simulate._wire(
        expert_factory=lambda client: ExpertSession(client),
    )
    # 2026-10-27 G19b 翻转：材质库 HTTP 回退（remote_nk_fetch）退役——
    # 材质库纯本地（genie/nkdata 随包分发），材质面不再接线。
    _preview._wire(_swap_connect)


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

# 公共表面：connect → Simulation（构建+提交）、preview_script（脚本→模型，
# Studio/工具消费）+ 结果对象 + 异常。Domain/Mesh/… 经 f.domain 等属性到达；
# Client 是内部传输层。``material`` 是 client-side 纯函数子模块。
__all__ = [
    "connect", "Simulation", "preview_script",
    "check_script", "SweepHandle",
    "TimeResult", "FreqResult", "IndexResult",
    "GenieError", "GenieHTTPError", "GenieJobError", "GenieConfigError",
    "material",
    "__version__",
]


def connect(base_url: str | None = None, api_key: str | None = None,
            username: str | None = None, password: str | None = None, *,
            timeout: float = 30.0) -> Simulation:
    """连接 GENIE 服务 → Simulation（≈ 本地包的 ``FDTD()``）。

    两条轨（G72 终稿）：

    - **裸 ``connect()``** → 会话代持轨：经门牌找到本机会话（没有就在
      进程内起一个无头会话），凭据由会话持有——**脚本零凭据**，token
      不进脚本的世界。登录由人在 Studio 登录闸或 ``genie login`` 完成。
    - **显式参数 / env（``GENIE_URL`` 等）** → 直连轨（脚本自包含、测试、
      机器预置 ``GENIE_USERNAME/PASSWORD``）：``api_key`` 或
      ``username``+``password``（POST /api/auth/login 换 token）二选一。

    返回对象的 ``domain/mesh/mat/geom/src/mon`` 六个子对象与本地包同名
    同手感。
    """
    from .net import client as _client_mod
    from .net.config import resolve_credential, resolve_gateway

    if base_url is None and api_key is None and password is None \
            and not os.environ.get("GENIE_URL"):
        via = _connect_via_session(_client_mod, timeout=timeout)
        if via is not None:
            return via
    url = resolve_gateway(base_url)
    key = resolve_credential(api_key, "GENIE_API_KEY", "api_key")
    user = resolve_credential(username, "GENIE_USERNAME", "username")
    pwd = password or os.environ.get("GENIE_PASSWORD")
    client = _client_mod.Client(url, key, username=user,
                                timeout=timeout)  # 模块属性寻址——测试可注入假传输
    if pwd and not key:
        if not user:
            raise ValueError("password 登录需要同时给 username")
        client.login(user, pwd)
    return Simulation(client)


def _connect_via_session(client_mod, *, timeout: float):
    """会话代持轨（G72）：返回 Simulation 或 None（None = 落回直连轨）。

    顺序：① 门牌上有活会话 → 经它的 /gw；② 没有且允许自起 → 进程内起
    无头会话（凭据仓载入；无凭据 → 指路错误，不起空会话）；③ studio
    未装（无 server extra）或 GENIE_SESSION_AUTOSTART=0 → None 落直连。
    """
    from .models.errors import GenieConfigError
    from .net.config import load_config

    try:
        from .studio import rendezvous as rv
    except ImportError:      # 无 server extra：会话形态不存在
        return None

    def _alive(url: str) -> bool:
        # 门牌纪律"不做存活探测"防的是**复用旧会话的模型态**；这里只用
        # 它的 /gw 代理（无状态），探测死门牌是为了不把用户引向死路。
        try:
            import requests as _rq
            return _rq.get(url.rstrip("/") + "/health",
                           timeout=1.5).status_code == 200
        except Exception:    # noqa: BLE001 —— 死门牌 = 没有会话
            return False

    card = rv.read()
    if card and card.get("url") and _alive(card["url"]):
        return Simulation(client_mod.Client(
            card["url"].rstrip("/") + "/gw", None, timeout=timeout,
            extra_headers={"X-Session-Token": card.get("token", "")}
            if card.get("token") else None))

    if os.environ.get("GENIE_SESSION_AUTOSTART", "1") == "0":
        return None
    if not (load_config().get("api_key")
            or os.environ.get("GENIE_API_KEY")):
        raise GenieConfigError(
            "尚未登录——会话没有可代持的凭据。两个入口任选其一：\n"
            "  ① genie login            （终端，输一次账号密码）\n"
            "  ② genie studio           （GUI 登录闸，输一次账号密码）")
    import secrets as _secrets

    from .studio import session_service as ss
    token = _secrets.token_urlsafe(24)
    ss.set_token(token)
    _server, port = ss.serve(0, token=token)
    base = f"http://127.0.0.1:{port}"
    rv.write(url=base, token=token)
    try:
        return Simulation(client_mod.Client(
            base + "/gw", None, timeout=timeout,
            extra_headers={"X-Session-Token": token}))
    except Exception:        # noqa: BLE001 —— 起失败不拦脚本，落直连
        return None
