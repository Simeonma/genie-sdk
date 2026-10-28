"""agent 侧原语：样板（G174 后的纯本地剩余面）。

工具面按"本体是会话里那份模型"重排后（见 ``integrations/mcp.py``），
agent 侧的纯本地能力只剩一个：

    starter_script()  → 一份已验证可跑的样板（照抄改参数）

check_script（本地体检）与它背后的 preview 零网络执行引擎已于
2026-10-28 随 G174 整体退役：自纠回路 = 会话执行原生能力
（``run_script(mode="build")`` 错误带行号；"看模型不跑" = build +
``get_model``）——agent 手里总有会话，无需第二个执行引擎。
v2 的 ``current_script`` / ``publish_script`` 更早退役（脚本不是本体）。
"""
from __future__ import annotations

__all__ = ["starter_script"]


# 可直接交付的起步脚本（agent 的"照抄样板"）。经实测：把骨架放在提示词里
# 模型仍会自行发明 API；给一个**可调用的样板工具**才真正管用——它把
# "该长什么样"从说明变成事实。
_STARTER = '''import genie

f = genie.connect("http://127.0.0.1:8777",
                  api_key="YOUR_API_KEY", username="YOUR_USERNAME")

# ── Domain 域 ──
f.domain.x_range = (-1e-6, 1e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 6e-14
f.domain.boundary_condition('z_min', 'pml')
f.domain.boundary_condition('z_max', 'pml')

# ── Mesh 网格 ──
f.mesh.dx = 2e-8
f.mesh.dy = 2e-8
f.mesh.dz = 2e-8

# ── Materials 材料 ──
f.mat.add_const().name('sio2').eps(2.1)

# ── Geometry 结构 ──
f.geom.add_box().material('sio2') \
    .x_range(-5e-7, 5e-7) \
    .y_range(-5e-7, 5e-7) \
    .z_range(-1e-7, 1e-7) \
    .priority(1)

# ── Source 源 ──
f.src.add_soft_source() \
    .freq(3e14) \
    .bandwidth(6e13) \
    .name('s0') \
    .center(0, 0, -3e-7)

# ── Monitor 监视器 ──
f.mon.add_time() \
    .name('pt') \
    .center(0, 0, 2e-7) \
    .field('Ex')

# ── Run 运行 ──
f.run(timeout_s=600)
'''


def starter_script() -> dict:
    """返回一份**已验证可跑**的最小脚本（照抄改参数即可）。

    用法：拿到后按用户需求改数值/增删对象，再送会话执行
    （``run_script(mode="build")``，报错带行号自纠）。想复用构件就把它
    包成 `def asm_<名>(f, 参数=默认值):` + `asm_<名>(f)`。
    """
    return {"ok": True, "script": _STARTER,
            "note": "这是可跑样板；改数值/增删对象即可，勿改 API 写法"}
