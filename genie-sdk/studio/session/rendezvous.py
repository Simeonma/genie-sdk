"""genie/studio/rendezvous — 本机会话的"门牌"（O1）。

问题：会话由 ``genie studio`` 自己起在 **OS 分配的端口**上（这样不会抢端口、
也不会误连上一个进程留下的旧会话）。于是别的客户端（``genie console``、
第二个 Studio）没法靠猜端口找到它。

答案：**一个门牌文件**，由会话的拥有者写、别人读。

    ~/.genie/studio.json  {url, token, session_id, owner, pid, started_at}

三条纪律：

1. **只描述，不决策**：读到的地址 + 口令直接拿来拨号；读不到就自己起。
2. **不做存活探测**：门牌里的 pid 只作排障信息；死掉的门牌表现为"连不上"，
   调用方按"服务不可达"处理（不猜、不复用）。
3. **单写者**：只有会话拥有者写；退出时删。多开第二个 Studio 时后写者赢
   ——但每个 Studio 都有自己的会话，门牌只是"最近一个可连的"。

env ``GENIE_RENDEZVOUS`` 可整体改路径（测试隔离用）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("GENIE_RENDEZVOUS")
                or (Path.home() / ".genie" / "studio.json"))


def write(*, url: str, token: str = "", session_id: str = "",
          owner: str = "") -> None:
    """写门牌（best-effort：写不进去不影响服务本身）。"""
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        body = {"url": url.rstrip("/"), "token": token,
                "session_id": session_id, "owner": owner,
                "pid": os.getpid(), "started_at": time.time()}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)       # 口令在文件里 → 只给本人读
        except OSError:
            pass
        tmp.replace(p)
    except OSError:
        pass


def update(**fields) -> None:
    """就地更新若干字段（会话中途换 session_id 时用）。"""
    cur = read() or {}
    if not cur:
        return
    cur.update({k: v for k, v in fields.items() if v is not None})
    write(url=cur.get("url", ""), token=cur.get("token", ""),
          session_id=cur.get("session_id", ""), owner=cur.get("owner", ""))


def read() -> dict | None:
    """读门牌；不存在/坏文件 → None（调用方按"没有会话"处理）。"""
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("url"):
        return None
    return d


def clear() -> None:
    """删门牌（只在"这份门牌是我写的"时删，避免踩掉别人的）。"""
    cur = read() or {}
    if cur.get("pid") not in (None, os.getpid()):
        return
    try:
        path().unlink()
    except OSError:
        pass
