"""工作区根的解析链（唯一口径）。

优先级：``GENIE_SCRIPTS_DIR``（显式 env，CI/测试隔离）> ``~/.genie/workspace.json``
的 ``root``（``genie workspace PATH`` / Studio 的 Workspace… 按钮写它）> 缺省
``~/data/genie/scripts``。

2026-11-01：``GENIE_SESSION_CWD`` 旧名兼容已删（一个意思一个变量）；owner 层
已删（登录账号不决定文件放哪儿）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    """``~/.genie/workspace.json``（与凭据仓同目录，永不进公开仓）。"""
    return Path.home() / ".genie" / "workspace.json"


def default_root() -> Path:
    """缺省工作区根（调用时解析 home）。"""
    return Path.home() / "data" / "genie" / "scripts"


def env_root() -> Path | None:
    """显式 env 覆盖（最高优先）：``GENIE_SCRIPTS_DIR``。"""
    v = os.environ.get("GENIE_SCRIPTS_DIR")
    return Path(v) if v else None


def _setting_root() -> str:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str((data or {}).get("root") or "").strip() \
        if isinstance(data, dict) else ""


def workspace_root() -> Path:
    """当前工作区根（env > 设置 > 缺省）。"""
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
