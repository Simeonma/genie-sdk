"""``genie console`` — session 的终端客户端（G12 终局形态，2026-10-13）。

你 shell 里的键盘，与 GUI terminal **平等**（Jupyter console 同款语义）：
同一个 session kernel，浏览器和终端可以同时连——两把键盘排队执行、
共享输出环。会话是专职进程、所有客户端平等（MCP/Jupyter/DAP 考据
定案，决策档案 2026-10-13-session-local.md §6 补记）。

协议复用 genie.studio.session_client（零新协议、零新 exec 实现）：

- 逐条 exec（``mode="run"``，terminal 语义：f 常驻、状态累积）；
- 多行输入：行缓冲 + ``code.compile_command`` 完整性判定（缩进块、
  函数体粘帖即整段提交）；
- Ctrl-C：远端忙 → 发 /stop（SIGINT 掐 worker，A2 语义）；输入中 →
  清当前缓冲；Ctrl-D / exit / quit → 退出（session 不受影响）。

O1 起会话地址与口令由调用方注入（``run_console(client, ...)``），不再从
环境变量猜——``genie console`` 读本机门牌拿到 GUI 正在用的那个会话
（含 session_id），于是"同一个 f"仍然成立。
"""
from __future__ import annotations

import code as _code
import readline  # noqa: F401 — 导入即启用历史/方向键（stdlib 惯例）
import signal
import sys
import time

from .session_client import SessionClient


def _poll_output(cli: SessionClient, cursor: int, *, owner: str,
                 session_id: str) -> tuple[int, bool]:
    """增量打印输出环新行，返回 (新 cursor, busy)。"""
    out = cli.output(owner=owner, session_id=session_id, since=cursor)
    for ln in out.get("lines") or []:
        text = ln.get("text") or ""
        if ln.get("kind") == "error":
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        else:
            sys.stdout.write(text)
        cursor = max(cursor, ln.get("seq", cursor))
    sys.stdout.flush()
    return cursor, bool(out.get("busy"))


def _run_block(cli: SessionClient, block: str, cursor: int, *, owner: str,
               session_id: str) -> int:
    """整段提交到会话并跟随输出到终态（busy False）。"""
    cli.exec(block, owner=owner, session_id=session_id, mode="run",
             label="console")
    stopped = {"v": False}

    def _on_int(signum, frame):
        if not stopped["v"]:
            stopped["v"] = True
            cli.stop(owner=owner, session_id=session_id)
            print("\n^C（已发停止——会话仍在）")

    old = signal.signal(signal.SIGINT, _on_int)
    try:
        while True:
            cursor, busy = _poll_output(cli, cursor, owner=owner,
                                        session_id=session_id)
            if not busy:
                return cursor
            time.sleep(0.15)
    finally:
        signal.signal(signal.SIGINT, old)


def run_console(client: SessionClient | None = None, *,
                session_id: str = "default",
                owner: str | None = None) -> int:
    """终端 REPL 主循环（会话已由调用方确保在跑）。"""
    import os
    from .session_client import resolve_owner
    cli = client or SessionClient()
    owner = owner or resolve_owner()
    cursor = 0
    # 对齐当前环位（不重放历史——那是 GUI 终端窗口的职责）
    try:
        out = cli.output(owner=owner, session_id=session_id, since=0)
        for ln in out.get("lines") or []:
            cursor = max(cursor, ln.get("seq", 0))
    except Exception as e:  # noqa: BLE001 —— 连不上就直说，别进假 REPL
        print(f"✗ 会话不可达：{e}")
        return 1

    print(f"GENIE console → {cli.base_url}   session={session_id}"
          "   （f 常驻；exit/Ctrl-D 退出，Ctrl-C 停止远端执行）")
    buf: list[str] = []
    while True:
        try:
            prompt = ">>> " if not buf else "... "
            line = input(prompt)
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt")
            buf = []
            continue
        buf.append(line)
        block = "\n".join(buf)
        if not block.strip():
            buf = []                      # 空行只作块闭合器，不是命令
            continue
        try:
            # 标准控制台语义：None=未闭合继续读；语法错=完整（提交远端
            # 统一报错）——空串会编译成空模块，上面已先行拦截。
            complete = _code.compile_command(block) is not None
        except (SyntaxError, OverflowError, ValueError):
            complete = True
        if not complete:
            continue                      # 多行块未闭合，继续读
        buf = []
        if block.strip() in ("exit", "quit", "exit()", "quit()"):
            return 0
        try:
            cursor = _run_block(cli, block, cursor, owner=owner,
                                session_id=session_id)
        except Exception as e:      # noqa: BLE001 —— R41/S4：4xx/链路错误
            # 不得冲出 REPL：修前一次 422（如 --session "bad id!"）就让
            # 整个终端带裸栈退出，用户连改的机会都没有。报一句人话，
            # 继续留在循环里——console 的契约是"会话仍在，可继续敲"。
            status = getattr(e, "status_code", None)
            detail = getattr(e, "detail", None) or str(e)
            print(f"✗ {'HTTP ' + str(status) + ' ' if status else ''}{detail}")
