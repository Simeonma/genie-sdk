"""genie-mcp v4 — 三个动词（2026-11-02 产品形态：agent 与人共用同一套入口）。

本体是 **worker 里那份脚本与模型**。agent 不拥有模型——它只是另外一双写同一
个编辑器、同一个终端的手，所以工具只有三个动作：

  · ``edit_script(text)``  改脚本（只改配方，不跑）
  · ``run_script()``       跑那份脚本（整份重建，原子；失败模型不动）
  · ``run_code(code)``     在当前模型上就地执行一段（增量）

**知识不在工具里**：``f`` 的 API、单位、物理语义、建模套路 → ``docs/agent.md``
（skill 文本）。工具表不随物理能力增长——这是 2026-11-02 定形的判据。

设计纪律
--------
· **模型从哪来**：组合根注入的工厂（``genie/__init__._session_client``）用
  ``rendezvous.alive()`` 读门牌并探活，接上正在跑的 Studio——本模块不 import
  studio（分层契约：integrations 不得依赖 studio）。
· **工具描述是 prompt**：第一句写"什么时候该用我"，报错要能自我纠正。
· **写是同步的**：两个 run 都等到落地再返回，尾部输出一并带回（agent 靠它
  判断成了没有，不需要第四个工具）。

用法::

    pip install "genie-sdk[agent]"          # mcp>=2.0
    genie-mcp                            # stdio 服务器（MCP 客户端拉起）
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

__all__ = ["TOOLS", "TOOL_NAMES", "execute_tool", "build_server", "main",
           "set_session_factory", "SessionUnavailable"]

# 组合根注入：返回一个"接上正在跑的 Studio"的端口客户端，没有活着的 Studio 时返回 None。
# （为什么不在这里 import studio：integrations 层不得依赖组合层。）
_session_factory: Callable[[], Any] | None = None


def set_session_factory(fn: Callable[[], Any] | None) -> None:
    """由 ``genie/__init__`` 注入端口发现函数（懒 import，避免拉进 fastapi）。"""
    global _session_factory
    _session_factory = fn


class SessionUnavailable(RuntimeError):
    """没有活着的 Studio（没在跑 / 门牌过期）。"""


def _session(session: Any = None):
    cli = session
    if cli is None:
        if _session_factory is None:
            raise SessionUnavailable("没有接线：import genie 后才会注入端口发现")
        cli = _session_factory()
    if cli is None:
        raise SessionUnavailable(
            "没有在跑的 Studio —— 让用户先 `genie studio`（模型活在那个 worker 里）")
    return cli


# ── 等待与尾部输出（写是异步的；工具要等到落地再回答）─────────────────

def _out(cli, **kw) -> dict:
    return cli.output(**kw)


def _wait_idle(cli, timeout: float) -> dict:
    t0 = time.time()
    out = _out(cli)
    while out.get("busy") and time.time() - t0 < timeout:
        time.sleep(0.1)
        out = _out(cli)
    return out


def _since(out: dict) -> int:
    lines = out.get("lines") or []
    return max((int(l.get("seq") or 0) for l in lines), default=0)


def _tail(out: dict, since: int, limit: int = 4000) -> tuple[str, str]:
    """返回 (全部尾部文本, 错误文本)。"""
    texts, errs = [], []
    for ln in out.get("lines") or []:
        if int(ln.get("seq") or 0) <= since:
            continue
        t = str(ln.get("text") or "")
        texts.append(t)
        if ln.get("kind") == "error":
            errs.append(t)
    return "".join(texts)[-limit:], "".join(errs).strip()


# ── 工具描述（LLM 读的说明；第一句 = 什么时候用我）────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "edit_script",
        "description": (
            "**改编辑器里的脚本**（模型的配方），不运行。**省略 text = 读当前脚本**，"
            "所以改之前先不带参数调一次。用户说'把周期改成 5 个'这类要动代码的请求时用它——"
            "改完再调 run_script。文本是**整份**，不是补丁。只改一个数值、不碰脚本时，"
            "用 run_code 里的一行赋值。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "整份脚本文本（Python）；省略 = 只读当前脚本"},
            },
            "required": [],
        },
    },
    {
        "name": "run_script",
        "description": (
            "**跑当前那份脚本**（整份重建模型）。成功才替换用户的模型，"
            "失败时模型一个字节不动（错误在返回的 output 里，带行号）。"
            "这是'新模型/大改'的正路；只想在当前模型上试一行，用 run_code。"),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_code",
        "description": (
            "**在当前模型上就地执行一段 Python**（增量，像在 Studio 终端里敲）。"
            "适合：改一个数值、试一段表达式、跑一次扫描、读结果。"
            "会直接改用户的模型——动手前先确认用户要的就是这个。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "一段 Python 代码"},
            },
            "required": ["code"],
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


# ── 三个动词的实现 ──────────────────────────────────────────────────

def _do_edit_script(cli, args: dict) -> dict:
    text = args.get("text")
    if text is None:                      # 只读：改之前先看
        return {"ok": True, "script": cli.get_script().get("text", "")}
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": "text 必须是整份脚本（非空字符串）；省略 = 只读"}
    cli.set_script(text)
    out = _wait_idle(cli, 10)
    return {"ok": True, "chars": len(text),
            "script_now": out.get("script", "")}


def _run_and_collect(cli, start: dict, timeout: float) -> dict:
    out = _wait_idle(cli, timeout)
    text, err = _tail(out, _since(start))
    res: dict[str, Any] = {"ok": not err, "busy": bool(out.get("busy")),
                           "job_id": out.get("job_id") or "",
                           "output": text}
    if err:
        res["error"] = err
    return res


def _do_run_script(cli, args: dict) -> dict:
    start = _out(cli)
    cli.run_script()
    return _run_and_collect(cli, start, float(args.get("timeout_s") or 180))


def _do_run_code(cli, args: dict) -> dict:
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code 必填：一段 Python"}
    start = _out(cli)
    cli.exec(code, mode="run", label="agent")
    return _run_and_collect(cli, start, float(args.get("timeout_s") or 180))


def execute_tool(name: str, args: dict, *, session: Any = None) -> dict:
    """执行一个工具（供 MCP 与自测共用）。未知工具 → 显式错误。"""
    args = args or {}
    if name not in TOOL_NAMES:      # 先判名字：不因"没在跑的 Studio"遮蔽
        return {"ok": False,
                "error": f"未知工具 {name}（可用: {sorted(TOOL_NAMES)}）"}
    try:
        cli = _session(session)
        if name == "edit_script":
            return _do_edit_script(cli, args)
        if name == "run_script":
            return _do_run_script(cli, args)
        if name == "run_code":
            return _do_run_code(cli, args)
    except SessionUnavailable as e:
        return {"ok": False, "error": str(e), "session": False}
    except Exception as e:  # noqa: BLE001 —— 工具失败要说人话，不抛给 LLM
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}"}
    return {"ok": False,
            "error": f"工具 {name} 未接线（可用: {sorted(TOOL_NAMES)}）"}


def build_server():
    """构建 MCP 服务器（stdio）。mcp 包缺失 → 指路错误。

    两代 SDK 都要认：mcp 2.x 把 ``FastMCP`` 改名为 ``MCPServer``（构造参数、
    ``.tool()``、``.run()`` 同名），1.x 仍是 ``FastMCP``。
    """
    try:                                     # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        try:                                 # mcp 1.x
            from mcp.server.fastmcp import FastMCP
        except ImportError as e:  # pragma: no cover —— 依赖缺失路径
            raise RuntimeError(
                'MCP 服务器需要 mcp 包：pip install "genie-sdk[agent]"') from e

    srv = FastMCP(name="genie",
                  instructions=(
                      "GENIE 电磁仿真。你操作的是用户在 Studio 里正看着的那份"
                      "脚本与模型——你和用户是在同一个软件里写同一份东西。"
                      "只有三个动作：edit_script 改脚本、run_script 跑整份、"
                      "run_code 就地跑一段。`f` 的 API、单位与物理语义不在工具里："
                      "先读 skill（docs/agent.md），再动手。不要另起 Studio。"))

    for tool in TOOLS:
        def _make(tname: str):
            def _fn(**kwargs) -> str:
                return json.dumps(execute_tool(tname, kwargs),
                                  ensure_ascii=False, default=str)
            _fn.__name__ = tname
            _fn.__doc__ = tool["description"]
            return _fn
        srv.tool(name=tool["name"], description=tool["description"])(_make(tool["name"]))
    return srv


def main() -> int:
    build_server().run()          # stdio（MCP 标准传输）
    return 0


if __name__ == "__main__":        # pragma: no cover
    raise SystemExit(main())
