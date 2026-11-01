"""本机路径口径（工作区根 / 落点）——**core 层唯一解析点**。

2026-11-01 成包：唯一理由是给这条口径一张 `MODMAP.md`（MODMAP 住在模块包
里）。**公开面与拆包前同名**（`genie.core.paths.*`），调用方零改动。

- 解析链：`workspace_root` / `source` / `env_root` / `default_root` / `settings_path`
- 落点：`runs_dir` / `sweeps_dir`

一个进程一片目录：登录账号不决定文件放哪儿（owner 层已删）。契约见
`MODMAP.md`；别在别处再写第二份"落在哪"。
"""
from __future__ import annotations

from ._layout import runs_dir, sweeps_dir
from ._resolve import (default_root, env_root, settings_path, source,
                       workspace_root)

__all__ = ["settings_path", "default_root", "env_root", "workspace_root",
           "source", "runs_dir", "sweeps_dir"]
