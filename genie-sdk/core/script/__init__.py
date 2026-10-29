"""cfg → genie 脚本渲染器（**唯一渲染器**；输出字节由 golden 冻结）。

产品定位：脚本 SDK 侧的正身——``Simulation.to_script()`` 渲染它，
作业溯源存它，agent 写它。输出字节由**字节锁测试**冻结
（tests/sdk/test_render_golden.py + golden/：同 cfg → 逐字节相同）。

包结构（G2 拆分，2026-10-11；原 1119 行单文件按职责五分）：

- ``_numfmt``  JS 数字/字符串方言（py_num/_exp_form/ECMA toString…）
- ``_emit``    槽位发射助手（_num 族/哨兵/极化归一/链式排版/
               表达式 ``*因子`` 声明式发射）
- ``_sections``八节生成器（domain…results）
- ``_params``  ``# parameters`` 块
- 本 ``__init__``：主入口 ``render`` 与公共符号聚合

数字方言 = ECMA-262 最短往返（v1 前端渲染器的格式，已由 golden 字节冻结，
**不是可调的排版风格**）；单位表来源约束（models.expr 单一正典，禁本地
复制）不变；求值器 = genie.models.expr（迁移阶段 1a）。对未知/非法输入的
处置形态**有意分歧**（渲染侧宽容跳过 + W，线格式侧严格抛错），分歧面由
test_single_source_locks 的形态锚锁定（audit 批 α 定案）。

公共名再导出：``render``（主入口）；FACES/BC_KINDS/
LIBRARY_FIT_DEFAULTS（正典常量透传，消费方免二次深挖）；
``_norm_pol`` 等内部名透传仅供归一锁测试（test_single_source_locks）
锚定——非产品面，勿在新代码 import。
"""
from __future__ import annotations

from ...models import BC_KINDS, FACES, LIBRARY_FIT_DEFAULTS
from ._emit import _norm_pol
from ._numfmt import py_num, py_str
from ._params import _gen_params
from ._sections import (_gen_domain, _gen_geometry, _gen_materials,
                        _gen_mesh, _gen_monitor, _gen_results, _gen_run,
                        _gen_source)

__all__ = ["render"]


def render(cfg: dict) -> str:
    """SimConfig JSON → genie 脚本文本（输出字节被 golden 冻结；字节锁
    测试见 tests/sdk/test_render_golden.py）。R40（2026-10-29）起头行 =
    零参 ``genie.connect()``，``origin`` 参数随之退役（凭据经
    ``genie login`` 注入，脚本自包含不再需要地址占位符）。"""
    W: list[str] = []
    cfg = cfg if isinstance(cfg, dict) else {}

    head = [
        "# ── GENIE Cloud SDK 脚本 · 由 GENIE Studio 表单自动生成 ──",
        "# 本地运行: pip install genie-sdk  # from <YOUR_GENIE_RELEASE_CHANNEL>",
        "import genie",
        "",
        "f = genie.connect()   # 凭据经 genie login 注入本机（一次即可）",
    ]

    params_lines, env = _gen_params(cfg, W)

    domain = _gen_domain(cfg.get("domain") or {}, W, env)
    mesh = _gen_mesh(cfg.get("mesh") or {}, W, env)
    materials = _gen_materials(cfg.get("materials"), W, env)
    geometry = _gen_geometry(cfg.get("geometry"), W, env)
    source = _gen_source(cfg.get("source"), W, env)
    monitor = _gen_monitor(cfg.get("monitor"), W, env)
    run_line = _gen_run(cfg, W, env)
    results = _gen_results(cfg.get("monitor"), run_line is not None)

    body: list[str] = []

    def sec(title: str, lines: list[str]) -> None:
        if not lines:
            return
        if body:
            body.append("")
        body.append("# ── " + title + " ──")
        body.extend(lines)

    sec("Domain 域", domain)
    sec("Mesh 网格", mesh)
    sec("Materials 材料", materials)
    sec("Geometry 结构", geometry)
    sec("Source 源", source)
    sec("Monitor 监视器", monitor)
    sec("Run 运行", [run_line] if run_line else [])
    sec("结果（纯数据 numpy）", results)

    warn_block: list[str] = []
    if W:
        warn_block = ["# ⚠ 配置未完成——以下字段缺失/非法，对应代码行已跳过（表单补齐后自动消失）："]
        warn_block += ["#   · " + w for w in W[:12]]
        if len(W) > 12:
            warn_block.append("#   · …共 " + str(len(W)) + " 条")
        warn_block.append("")

    return "\n".join(head + [""] + params_lines + warn_block + body + [""])