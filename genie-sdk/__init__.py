"""GENIE SDK v2 — 用户在脚本里构建仿真、``f.run()`` 提交云端 GPU。

产品口径（2026-10 terminal 战役后；G174 起单执行引擎）：
  · SDK 用户：写 genie 脚本，``f`` 是活配置对象，``f.run()`` 提交那一刻
    才序列化 SimConfig JSON（线格式，用完即弃，客户端不存 cfg 状态）；
  · 云 GUI（Studio）背后是常驻 worker 里的同一个 SDK——**worker 里的 f
    是唯一可编辑的东西**，Terminal/脚本/表单三条变更通道同汇一处（术语见
    genie-sdk/studio/session/MODMAP.md §0）；本包不感知 GUI 形态；
  · **一个执行引擎（worker）、一种语义（真执行——错了报错带
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
from .core.run import Simulation
from .core.sweep import SweepHandle

# ``import genie.material`` 语句级可用（不只属性寻址）——注册进
# sys.modules 后属性寻址与语句级 import 皆通（material 实体在 core/）。
_sys.modules[__name__ + ".material"] = material

__version__ = "2.1.1"


# ── 组合根（G1 分层契约，2026-10-11 定案）───────────────────────────
# core 只依赖 models；上层能力在此一次性注入 core 的 hooks。
# 2026-11-02：最后一个 hook（外部 visualize 推送）随"一个执行面"删除，
# 组合根现在没有需要接线的东西——留着这行注释，等真有上层能力再加。
# 分层守卫 = tests/sdk/test_layering.py。



def _alive_card() -> dict | None:
    """门牌探活：**唯一实现**在 ``rendezvous.alive()``；这里只兜"studio 缺件"。"""
    try:
        from .studio.session import rendezvous as rv
    except ImportError:      # 异形安装（studio 缺件）：执行面不存在
        return None
    return rv.alive()


def _session_client():
    """MCP/工具面用的"端口把手"：接上**活着的** Studio（没有 → None）。

    用 ``alive()`` 而不是 ``discover()``：工具面拿到一个已经死掉的 client
    只会把"没有 Studio"伪装成"传输错误"。

    懒 import：``import genie`` 不该把 fastapi 拉进来（studio 是可选 extra）。
    注入方向也是组合根的老规矩——``integrations`` 不得依赖 ``studio``。
    """
    card = _alive_card()
    if card is None:
        return None
    from .studio.session.session_client import SessionClient
    return SessionClient(card["url"], token=card.get("token", ""))


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


def connect() -> Simulation:
    """**只能在 Studio 里用**：worker 会接管这个符号，建一个新实例。

    在 Studio 的脚本编辑器 / 终端里，``genie.connect()`` 由 worker 接管
    （零参——参数一律被忽略，凭据由 worker 持有）。在外面它直接拒绝：
    唯一执行面是 worker。外面只想**建模**（不跑）用
    ``genie.Simulation()`` / ``genie.open(路径)``；要**跑**就在 Studio 里写配方。
    """
    from .models.errors import GenieConfigError
    raise GenieConfigError(
        "connect() 只能在 Studio 里用（脚本编辑器 / 终端会自动接管）。\n"
        "  只想建模（不跑）：genie.Simulation() 或 genie.open(路径)\n"
        "  要跑仿真：在 Studio 里写配方（唯一执行面是 worker）")

def _connect_direct(base_url: str, api_key: str | None = None, *,
                    timeout: float = 30.0) -> Simulation:
    """内部直连（worker / 测试用）：不经门牌、不走公开参数面。"""
    from .net import client as _client_mod
    return Simulation(_client_mod.Client(base_url, api_key, timeout=timeout))


def open(path) -> Simulation:
    """打开一个 ``.gensim`` 仿真文件 → 载入其配置的 Simulation。

    **worker 内**（Studio 终端 / agent）：整份替换当前模型并返回活实例
    ——与图形界面「打开仿真」同一语义。worker 外：新建一个**没有 client**
    的实例载入配置（只能建模 / 另存，不能跑）。两条轨都会把
    ``_doc_path`` 钉到这个文件：之后 ``run()`` 成功**原地更新它**
    （G182：文档是结果产物）。

    容器格式由 ``genie.core.archive.simfile`` 维护（与 ``f.save`` / Studio
    的存储/打开同一份实现）。
    """
    from .core.archive import simfile
    from .core import run as _simulate

    cfg = simfile.read_cfg(path)
    hook = _simulate.hooks.get("open_sim")
    if hook is not None:
        return hook(cfg, str(path))
    f = Simulation(None)
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

