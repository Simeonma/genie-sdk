"""agent 侧原语：脚本体检与样板（零网络那半）。

工具面按"本体是会话里那份模型"重排后（见 ``integrations/mcp.py``），
agent 侧的纯本地能力只剩两个：

    check_script(src) → 语法/语义体检（本地执行，不推给 GUI，供自纠）
    starter_script()  → 一份已验证可跑的样板（照抄改参数）

v2 的 ``current_script`` / ``publish_script`` 已退役：脚本不是本体，"读脚本
文本"改成读模型（``get_model``），"推脚本文本"改成把脚本跑进会话
（``run_script``）。它们走的草稿通道连凭据都没带——上线后一直是 401 静默死。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.preview import preview_script

__all__ = ["check_script", "starter_script"]

_MAX_SCRIPT = 256 * 1024
# R31 站长裁：check_script 执行的是 LLM 生成的**不可信代码**——死循环
# 冻结 agent 会话、脚本污染宿主（sys.path/env/猴子补丁）都不可接受；
# 子进程隔离让超时可杀（Windows 无 SIGALRM 也可靠——杀进程树）、
# 宿主零污染。preview_script 公开面保持进程内（终端用户自己的进程
# 自己负责——G161 责任边界 2026-10-28 站长定）。
_SUBPROC_TIMEOUT_S = 30.0


def _preview_isolated(source: str, label: str,
                      timeout_s: float) -> dict:
    """子进程跑 preview_script（stdin=JSON / stdout=JSON）。

    超时 → 信封报 ``kind="timeout"``（agent 可读、可自纠）；子进程起
    不来/输出坏 → 降级进程内（保可用性——降级形态文档注明）。"""
    runner = (
        "import sys, json\n"
        "import genie\n"
        "req = json.loads(sys.stdin.read())\n"
        "r = genie.preview_script(req['script'], label=req['label'])\n"
        "sys.stdout.write(json.dumps(r, ensure_ascii=False))\n"
    )
    sdk_root = str(Path(__file__).resolve().parents[2])
    env = {**os.environ,
           "PYTHONPATH": sdk_root + os.pathsep + os.environ.get("PYTHONPATH", ""),
           # 子进程 stdio 强制 UTF-8（Windows 缺省 GBK 会让含中文的
           # 信封在管道两侧编解码漂移）+ 摘 BOM（utf-8-sig 容错）。
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(
            [sys.executable, "-c", runner],
            input=json.dumps({"script": source, "label": label}),
            capture_output=True, text=True, timeout=timeout_s,
            encoding="utf-8", env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "cfg": {}, "stdout": "", "stderr": "",
                "error": {"kind": "timeout",
                          "message": f"脚本执行超 {timeout_s:g}s 未结束——"
                                     f"疑似死循环（执行子进程已终止）",
                          "line": 0, "traceback": ""}}
    except OSError:
        return preview_script(source, label=label)   # 起不来：降级
    if p.returncode == 0 and p.stdout:
        try:
            out = json.loads(p.stdout.lstrip("﻿"))
            if isinstance(out, dict):
                return out
        except ValueError:
            pass
    return preview_script(source, label=label)       # 输出坏：降级


def check_script(source: str, *, label: str = "<agent脚本>",
                 timeout_s: float = _SUBPROC_TIMEOUT_S) -> dict:
    """脚本体检：语法 + 执行两关，返回 agent 可读的诊断。

    与 ``preview_script`` 的差别：本函数面向 **LLM 自纠回路**——把
    "能不能跑""跑出来是什么"两件事一次说清，错误带行号与原因，
    成功带模型摘要（节点数/源/监视器/run 步数）。执行在**子进程**
    里跑（R31：死循环超时可杀、宿主零污染；``timeout_s`` 覆盖）。

    返回 ``{"ok", "errors":[{kind,message,line}], "summary":{...},
    "warnings":[...]}``。ok=True 表示可以推到 GUI 了。
    """
    out: dict[str, Any] = {"ok": False, "errors": [], "summary": None,
                           "warnings": []}
    if not isinstance(source, str) or not source.strip():
        out["errors"].append({"kind": "empty", "message": "脚本为空", "line": 0})
        return out
    if len(source.encode("utf-8")) > _MAX_SCRIPT:
        out["errors"].append({"kind": "too_large",
                              "message": "脚本超 256KiB 上限", "line": 0})
        return out

    cap = _preview_isolated(source, label, timeout_s)
    if not cap.get("ok"):
        e = cap.get("error") or {}
        out["errors"].append({"kind": e.get("kind") or "error",
                              "message": e.get("message") or "执行失败",
                              "line": e.get("line") or 0})
        return out

    cfg = cap.get("cfg") or {}
    n = lambda k: len(cfg.get(k) or [])          # noqa: E731
    run = cfg.get("run") or {}
    out["summary"] = {
        "materials": n("materials"), "geometry": n("geometry"),
        "source": n("source"), "monitor": n("monitor"),
        "has_domain": bool(cfg.get("domain")),
        "has_mesh": bool(cfg.get("mesh")),
        "total_time": (cfg.get("domain") or {}).get("total_time"),
        "stdout": (cap.get("stdout") or "")[:2000],
        "stderr": (cap.get("stderr") or "")[:2000],   # G164：告警面回流 agent
    }

    # 常见遗漏告警（不阻断——agent 可自行判断是否补）
    if not out["summary"]["source"]:
        out["warnings"].append("没有源（source）——仿真不会激励，通常需要至少一个")
    if not (cfg.get("domain") or {}).get("total_time"):
        out["warnings"].append(
            "未设置 domain.total_time——唯一时间定义（SI 秒 >0），提交必填")
    if not out["summary"]["monitor"]:
        out["warnings"].append("没有监视器（monitor）——跑完拿不到结果")

    out["ok"] = True
    return out


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

    用法：拿到后按用户需求改数值/增删对象，再 check_script 自纠。
    想复用构件就把它包成 `def asm_<名>(f, 参数=默认值):` + `asm_<名>(f)`。
    """
    return {"ok": True, "script": _STARTER,
            "note": "这是可跑样板；改数值/增删对象即可，勿改 API 写法"}
