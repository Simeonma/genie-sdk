"""genie/studio/rendezvous — 本机 Studio 的"门牌"（O1）。

问题：Studio 的 ``/session`` 面起在 **OS 分配的端口**上（这样不会抢端口、
也不会误连上一个进程留下的旧 worker）。于是别的客户端（``genie console``、
MCP、第二个 Studio）没法靠猜端口找到它。

答案：**一个门牌文件**，由 Studio 写、跨进程客户端读。

    ~/.genie/studio.json  {url, token, pid, started_at}

三条纪律：

1. **只描述，不决策**：读到的地址 + 口令直接拿来拨号；读不到就自己起。
2. **read() 不探活，alive() 探活**：`read()` 只回答"门牌上写了什么"；
   "有没有活着的"由 `alive()` 回答（`read()` + `/health`），一处实现。
   死门牌表现为"连不上"，调用方按"不可达"处理（不猜、不复用）。
3. **单写者**：只有 Studio 写；退出时删（只删自己 pid 的牌）。多开第二个
   Studio 时后写者赢——门牌只是"最近一个可连的"。

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


def write(*, url: str, token: str = "") -> None:
    """写门牌（best-effort：写不进去不影响服务本身）。

    只描述"服务在哪、口令是什么"——模型只有一份，没什么可指的
    （2026-11-01 删掉 session_id / owner 两个字段）。
    """
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        body = {"url": url.rstrip("/"), "token": token,
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


def read() -> dict | None:
    """读门牌；不存在/坏文件 → None（调用方按"没有 Studio"处理）。

    **纯读、不探活**：牌在 ≠ 牌后面那个进程还在。要"有没有活着的"用
    ``alive()``——两个问题两个函数（2026-11-01 收口：原来探活还另有一份
    在 ``genie/__init__._alive_card``，同一个问题两个答案）。
    """
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("url"):
        return None
    return d


def alive(timeout: float = 1.5) -> dict | None:
    """读门牌并探活；没牌/死牌/读不了 → None。**"有没有活着的 Studio"的唯一答案。**

    死牌不删：门牌可能属于另一个进程（它只是暂时没响应/正在重启），
    删了会踩掉别人的；判死只影响这一次调用。
    """
    card = read()
    if not card:
        return None
    try:
        import requests
        if requests.get(card["url"].rstrip("/") + "/health",
                        timeout=timeout).status_code != 200:
            return None
    except Exception:        # noqa: BLE001 —— 死门牌 = 没有 Studio
        return None
    return card


def clear() -> None:
    """删门牌（只在"这份门牌是我写的"时删，避免踩掉别人的）。"""
    cur = read() or {}
    if cur.get("pid") not in (None, os.getpid()):
        return
    try:
        path().unlink()
    except OSError:
        pass
