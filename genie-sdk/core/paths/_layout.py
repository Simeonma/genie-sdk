"""落点：文档 / 扫描。两个函数都只依赖「工作区根」+「一个输出基」。

- ``runs_dir``：一次执行一份文档的目录；
- ``sweeps_dir``：参数扫描的根（每次扫描一个子文件夹）。

显式 ``GENIE_RESULT_DIR`` 是**唯一的输出覆盖**（部署/CI/测试）；
``GENIE_SWEEPS_DIR`` 由会话 spawn 时注入（``<工作区根>/sweeps``），
两者都不是"按人分目录"的残留——owner 层已删。
"""
from __future__ import annotations

import os
from pathlib import Path

from ._resolve import workspace_root


def runs_dir() -> Path:
    """单跑文档目录（G182 P2）：

    - 显式 ``GENIE_RESULT_DIR`` **优先**（部署/CI 的目录覆盖；它同时是
      SDK 直跑 ``f.run()`` 的落点，契约见 ``manual/commands/``）；
    - 否则 ``<workspace>/runs``（一次执行一份文档）。
    """
    env = os.environ.get("GENIE_RESULT_DIR")
    if env:
        return Path(env)
    return workspace_root() / "runs"


def sweeps_dir() -> Path:
    """参数扫描目录（G182 P4）：``<workspace>/sweeps``。

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
        return Path(res) / "sweeps"
    return workspace_root() / "sweeps"
