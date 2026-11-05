"""genie.host.cred — 云凭据仓（``~/.genie/credentials.json``，0600，Q2.1）。

云 token 只住 host：进本仓、出 ``/gw`` 上行头，**不进 worker、不进
spawn env**。写文件的函数只有一个（``_write_store``——两入口
login/logout 都经它；AST 锁，tests/host/test_gw_cred.py）；上行头不在这
拼（头名只在 ``host/gw`` 出现，tests/host 扫锁）。
"""
from __future__ import annotations

import json
import os

__all__ = ["save_token", "load_token", "clear", "DEFAULT_PATH"]

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".genie",
                            "credentials.json")


def _write_store(path: str, data: dict) -> None:
    """唯一的写函数：0600 落盘（目录缺则建，权限尽力——POSIX 全效）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def save_token(token: str, *, path: str | None = None) -> None:
    """login 入口：存云 token。"""
    _write_store(path or DEFAULT_PATH, {"token": token})


def load_token(*, path: str | None = None) -> str | None:
    p = path or DEFAULT_PATH
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh).get("token")
    except (OSError, ValueError):
        return None


def clear(*, path: str | None = None) -> None:
    """logout 入口：撤仓（删文件；token 值不落第二处）。"""
    p = path or DEFAULT_PATH
    if os.path.exists(p):
        os.remove(p)
