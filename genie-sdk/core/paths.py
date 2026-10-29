"""本机路径口径（工作区根 / 结果目录）——**core 层唯一解析点**。

分层约束：core 只依赖 models + stdlib，所以这里不 import studio；Studio 的
``workspace_setting`` 反向委派到本模块（单一来源，G4a 2026-10-30）。

解析优先级（唯一口径）：

1. ``GENIE_SESSION_CWD`` / ``GENIE_SCRIPTS_DIR``（显式 env，CI/特殊场景）；
2. ``~/.genie/workspace.json`` 的 ``root``（``genie workspace PATH`` /
   Studio 的 Workspace… 按钮写它）；
3. 缺省 ``~/data/genie/scripts``。

**SDK 直跑也认这个根**：``f.run()`` 的结果落在
``<root>/genie_results/``（给了 owner 则是 ``<root>/<owner>/genie_results/``，
Studio 会话用后者）；显式 ``GENIE_RESULT_DIR`` 仍然最高优先。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["settings_path", "default_root", "env_root", "workspace_root",
           "source", "result_dir", "runs_dir"]


def settings_path() -> Path:
    """``~/.genie/workspace.json``（与凭据仓同目录，永不进公开仓）。"""
    return Path.home() / ".genie" / "workspace.json"


def default_root() -> Path:
    """缺省工作区根（调用时解析 home）。"""
    return Path.home() / "data" / "genie" / "scripts"


def env_root() -> Path | None:
    for key in ("GENIE_SESSION_CWD", "GENIE_SCRIPTS_DIR"):
        v = os.environ.get(key)
        if v:
            return Path(v)
    return None


def _setting_root() -> str:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str((data or {}).get("root") or "").strip() \
        if isinstance(data, dict) else ""


def workspace_root() -> Path:
    """当前用户工作区根（env > 设置 > 缺省）。"""
    root = env_root()
    if root is not None:
        return root
    v = _setting_root()
    return Path(v) if v else default_root()


def source() -> str:
    """``env`` / ``setting`` / ``default``——界面与 CLI 如实显示来源。"""
    if env_root() is not None:
        return "env"
    return "setting" if _setting_root() else "default"


def result_dir(owner: str | None = None) -> Path:
    """结果目录：``<root>/[<owner>/]genie_results``。

    **G182 起仅作历史 h5 的只读位置**（新产物是文档）；保留此函数供
    旧路径回退与测试。
    """
    root = workspace_root()
    return (root / owner / "genie_results") if owner else (root / "genie_results")


def runs_dir(owner: str | None = None) -> Path:
    """单跑文档目录（G182 P2）：

    - 显式 ``GENIE_RESULT_DIR`` **优先**（部署/CI 的目录覆盖；修前它决定
      h5 落点，现在决定文档落点，语义延续）；
    - 否则 ``<workspace>/[<owner>/]runs``（一次执行一份文档）。
    """
    env = os.environ.get("GENIE_RESULT_DIR")
    if env:
        d = Path(env)
        return (d / owner / "runs") if owner else d
    root = workspace_root()
    return (root / owner / "runs") if owner else (root / "runs")


def sweeps_dir(owner: str | None = None) -> Path:
    """参数扫描目录（G182 P4）：``<workspace>/[<owner>/]sweeps``。

    每个扫描一个子文件夹（``<name>-<时间戳>/``），里面 ``sweep.json``
    索引 + 每点一份 ``<name>-0001.gensim``。解析：
    ``GENIE_SWEEPS_DIR``（会话显式注入）→ ``GENIE_RESULT_DIR``/sweeps
    （部署/CI 基）→ 工作区根。
    """
    env = os.environ.get("GENIE_SWEEPS_DIR")
    if env:
        return Path(env)
    res = os.environ.get("GENIE_RESULT_DIR")
    if res:
        return Path(res) / ("sweeps" if not owner else f"{owner}/sweeps")
    root = workspace_root()
    return (root / owner / "sweeps") if owner else (root / "sweeps")
