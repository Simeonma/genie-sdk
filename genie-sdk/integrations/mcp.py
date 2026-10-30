"""genie-mcp v3 — 会话模型的 MCP 工具面（stdio，2026-10-23 agent 轨道）。

定位
----
本体是**会话里那份模型**（``f``），不是脚本文本。所以工具面按"能改一处就改
一处"排：

  · ``get_model``    看一眼现在是什么（线格式 SimConfig + 忙闲/作业）
  · ``edit_model``   改一处（路径级增量：set / append / remove）
  · ``run_script``   发一段脚本（循环/扫参/程序化建模；mode=run|build）
  · ``run_model``    提交当前模型（**不改模型**）
  · ``get_results``  结果清单 / 曲线
  · ``get_starter_script`` / ``list_materials``  样板与材料库

v2 的两个工具（``current_script`` / ``publish_script``）已退役：脚本不是本体，
"读脚本文本"改成读模型，"推脚本文本"改成把脚本**跑进**会话（``run_script``）。
（本地 SDK 的 drafts 镜像通道也已整体退役——G53 裁决，2026-10-23。）
``check_script``（本地零网络体检）于 2026-10-28 随 G174 退役：自纠 =
``run_script(mode="build")`` 读带行号的错误，"看模型不跑" = build +
``get_model``——会话执行原生覆盖，无需第二个执行引擎。

设计纪律
--------
· **会话从哪来**：``discover()`` 读门牌找正在跑的 Studio——由组合根在
  ``genie/__init__`` 注入（``set_session_factory``），本模块不 import studio
  （分层契约：integrations 不得依赖 studio）。
· **工具描述是 prompt**，不是文档：第一句写"什么时候该用我"，参数约束写进
  schema，报错要能自我纠正。
· 写是**异步**的：``edit_model`` / ``run_script`` 会等到落地再返回，省掉
  "改完不知道成没成"的轮次。

用法::

    pip install "genie-sdk[agent]"          # mcp>=2.0
    genie-mcp                            # stdio 服务器（MCP 客户端拉起）
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from .agent_tools import starter_script
from ..models import MAX_GPU_PER_RUN

__all__ = ["TOOLS", "TOOL_NAMES", "execute_tool", "build_server", "main",
           "set_session_factory", "SessionUnavailable"]

# 组合根注入：返回一个"带 owner/session_id 的会话客户端"，没有会话时返回 None。
# （为什么不在这里 import studio：integrations 层不得依赖组合层。）
_session_factory: Callable[[], Any] | None = None


def set_session_factory(fn: Callable[[], Any] | None) -> None:
    """由 ``genie/__init__`` 注入会话发现函数（懒 import，避免拉进 fastapi）。"""
    global _session_factory
    _session_factory = fn


class SessionUnavailable(RuntimeError):
    """没有可连的会话（Studio 没在跑 / 门牌过期）。"""


def _session(session: Any = None):
    cli = session
    if cli is None:
        if _session_factory is None:
            raise SessionUnavailable("没有接线：import genie 后才会注入会话发现")
        cli = _session_factory()
    if cli is None:
        raise SessionUnavailable(
            "没有在跑的 Studio —— 让用户先 `genie studio`（模型活在那个会话里，"
            "不是另起一份）")
    return cli


# ── 等待（写入是异步的；工具要等到落地再回答）─────────────────────────

def _out(cli, **kw) -> dict:
    return cli.output(**kw)


def _wait_idle(cli, timeout: float) -> dict:
    """等到不忙（exec / run 用：模型未必变，所以只等 busy）。"""
    t0 = time.time()
    out = _out(cli)
    while out.get("busy") and time.time() - t0 < timeout:
        time.sleep(0.1)
        out = _out(cli)
    return out


def _wait_cfg(cli, seq: int, timeout: float) -> dict:
    """等到模型动过（patch / replace 用：cfg_seq 必须前进）。"""
    t0 = time.time()
    out = _out(cli)
    while time.time() - t0 < timeout:
        if out.get("cfg_seq") != seq and not out.get("busy"):
            return out
        time.sleep(0.1)
        out = _out(cli)
    return out


def _tail_lines(out: dict, since: int, kinds=("error",)) -> str:
    text = [str(l.get("text") or "") for l in out.get("lines") or []
            if l.get("seq", 0) > since and l.get("kind") in kinds]
    return "".join(text).strip()


# ── 工具描述（LLM 读的说明；第一句 = 什么时候用我）────────────────────

_OPS_SHAPE = (
    '一条 op = {"path": "...", "value": ..., "verb": "set|append|remove"}；'
    'verb 缺省 set。例：{"path":"mesh.dx","value":5e-8} ／ '
    '{"verb":"append","path":"monitor","value":{"type":"time","name":"t1",'
    '"field":"Ey"}} ／ {"verb":"remove","path":"geometry":"grating"}。'
    "path 是点分路径；数组那一段**可以是下标，也可以是名字**"
    "（geometry.grating.priority 与 geometry.2.priority 等价，名字更稳："
    "下标会因为别人刚插了一个而错位）。"
    "一批 op 全对或全错——有一条不成立，整批不提交。"
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_model",
        "description": (
            "**先调这个**：看一眼会话里现在的模型（线格式 SimConfig）和它忙不忙。"
            "改任何东西之前先读——用户可能刚动过。"
            "给 section 只取一节（materials / geometry / source / monitor / "
            "domain / mesh / run），大模型别整份拉。"
            "返回 {ok, has_model, model, busy, phase, job_id, session_id}。"
            "字段语义见随包手册（manual/commands/ 参考页）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "section": {"type": "string",
                            "description": "只取这一节（缺省=整份）"},
            },
        },
    },
    {
        "name": "edit_model",
        "description": (
            "改一处模型（**默认用它**）。只发改了什么，服务端是权威；"
            "不要为了改一个字段去整份重写。" + _OPS_SHAPE +
            "返回 {ok, applied_paths, changed}——changed 是"
            "{路径: 改后的值}（remove 记 \"removed\"），够核对，不刷整份模型。"
            "被拒时模型一个字节没动，按 error 改完再试。"),
        "parameters": {
            "type": "object",
            "properties": {
                "ops": {"type": "array", "items": {"type": "object"},
                        "description": "op 列表（见描述里的形状）"},
                "timeout_s": {"type": "number",
                              "description": "等落地的最长秒数（缺省 15）"},
            },
            "required": ["ops"],
        },
    },
    {
        "name": "run_script",
        "description": (
            "把一段 Python 发进会话执行——**只要出现循环、扫参、按数据生成几何，"
            "就用它**（原子操作表达不了这些）。会话里已绑好 f（当前模型）、genie、"
            "connect、run_model：跑了 model 内建循环/参数化就用这些，"
            "不用 import genie、不用 connect。"
            "mode=run（缺省）改 f 就是改模型；mode=build 在新命名空间执行、"
            "成功才整体提交（要么全成要么别动）。"
            "提交作业用 run_model()，**别用 f.run()**（一个实例只能提交一次）。"
            "返回 {ok, output}——ok=false 时 output 是要改的那条错误。"),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "完整 Python 脚本"},
                "mode": {"type": "string", "enum": ["run", "build"],
                         "description": "run=改当前模型（缺省）；build=事务式重建"},
                "timeout_s": {"type": "number",
                              "description": "等执行结束的最长秒数（缺省 120）"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "run_model",
        "description": (
            "提交**当前模型**（不改模型）。要跑的是会话里那份，不是你手里的副本。"
            "提交是一次性的：一个模型实例只能提交一次，改完再跑是新提交。"
            "gpu / timeout_s 可选（缺省用模型 run 节里的）。"
            "返回 {ok, job_id, busy}——**提交失败会报 ok=false**（别把失败当成功），"
            "进度用 get_model 看 phase。"),
        "parameters": {
            "type": "object",
            "properties": {
                "gpu": {"type": "integer", "description": (
                    f"GPU 数上限 1..{MAX_GPU_PER_RUN}（可选；实际给几张"
                    "由服务端按需求与配额裁定）")},
                "timeout_s": {"type": "integer", "description": "作业超时（秒）"},
            },
        },
    },
    {
        "name": "get_results",
        "description": (
            "取结果。不给 mon → 返回结构清单（有哪些监视器/字段，"
            "用 /api/result/meta）；给 mon+field → 返回曲线数据。"
            "**job_id 必填**（从 get_model 的返回值里拿；服务端不猜"
            "'最近一次'——G58/G66 定案 2026-10-29）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string",
                           "description": "必填：从 get_model 的 job_id 拿"},
                "mon": {"type": "string", "description": "监视器名"},
                "field": {"type": "string", "description": "如 Ey / Ex"},
                "maxpoints": {"type": "integer", "description": "抽稀到多少点（缺省 1200）"},
            },
        },
    },
    {
        "name": "get_starter_script",
        "description": (
            "返回一份已验证可跑的最小 genie 脚本样板。拿到后按需求改数值、"
            "增删对象即可——**不要自己发明 API 写法**（实测：不照抄样板会写出"
            "不存在的方法）。"),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_materials",
        "description": (
            "可用 n-k 材料库名单（写 `.add_from_library('Au', name='Au')` "
            "时用库名——name 是 keyword-only 参数）。"
            "返回 {models: [...], count}。"),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "login_status",
        "description": (
            "查本机登录/配置状态：网关地址来源（env/配置文件/缺省）、是否已存 "
            "token（只报有无与用户名，**绝不返回 token 本身**）。401/未配置时"
            "先调我，按结果指引用户跑 `genie login` 或 `genie connect <地址>`。"),
        "parameters": {"type": "object", "properties": {}},
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _list_materials() -> dict:
    """n-k 材料库名单（正典 = core.material.list_library_materials，
    与 Studio 的 /api/tools/nk_library 同一份实现；零网络）。"""
    try:
        from ..core.material import list_library_materials
        models = sorted(list_library_materials())
    except Exception:  # noqa: BLE001 —— 库结构变动不炸 agent
        models = []
    return {"models": models, "count": len(models)}


def _login_status() -> dict:
    """本机登录/配置状态（零网络；token 本身绝不出现）。

    网关链与 logged_in 判定都走真源（net.config.gateway_source /
    resolve_credential——G101 清账 2026-10-27，此前手抄份不 strip/不去
    尾斜杠，报给 agent 的地址可与实际拨号地址字形不同）。
    """
    from ..net.config import gateway_source, load_config, resolve_credential

    cfg = load_config()
    gw, src = gateway_source()
    logged_in = bool(resolve_credential(None, "GENIE_API_KEY", "api_key"))
    return {
        "ok": True,
        "gateway": gw,
        "gateway_source": src,
        "logged_in": logged_in,
        "username": cfg.get("username") if logged_in else None,
        "next_action": (
            None if (gw and logged_in) else
            ("run `genie login` (after `genie connect <gateway url>` "
             "if the gateway is not configured)"
             if gw else "run `genie connect <gateway url>` first, then `genie login`")
        ),
    }


def _at(cfg, path: str):
    """按点分路径取值（读回执用；定位不到返回 None）。"""
    cur = cfg
    for k in str(path).split("."):
        if isinstance(cur, list):
            cur = cur[int(k)]
        elif isinstance(cur, dict):
            cur = cur[k]
        else:
            return None
    return cur


_SECTIONS = ("domain", "mesh", "materials", "geometry", "source", "monitor",
             "run", "parameters")


def _do_get_model(cli, args: dict) -> dict:
    out = _out(cli)
    cfg = out.get("cfg") or None
    section = str(args.get("section") or "")
    if section:
        if section not in _SECTIONS:
            return {"ok": False,
                    "error": f"section 只认 {list(_SECTIONS)}；整份就别给 section"}
        cfg = {section: (cfg or {}).get(section)} if cfg else None
    return {"ok": True, "has_model": bool(cfg), "model": cfg,
            "busy": bool(out.get("busy")), "phase": out.get("phase"),
            "job_id": out.get("job_id"), "session_id": out.get("session_id")}


def _do_edit_model(cli, args: dict) -> dict:
    ops = args.get("ops")
    if not isinstance(ops, list) or not ops:
        return {"ok": False, "error": "ops 须非空列表"}
    timeout = float(args.get("timeout_s") or 15.0)
    before = _out(cli)
    seq, since = before.get("cfg_seq"), max(
        [l.get("seq", 0) for l in before.get("lines") or []] or [0])
    cli.patch(ops)
    out = _wait_cfg(cli, seq, timeout)
    if out.get("cfg_seq") == seq and out.get("busy"):
        return {"ok": False, "error": f"{timeout:g}s 内没落地（会话还忙）"}
    if out.get("origin") == "reject" or (
            out.get("cfg_seq") == seq and not out.get("busy")):
        return {"ok": False, "rejected": True,
                "error": _tail_lines(out, since) or "这批改动被拒（模型未改动）"}
    applied = out.get("paths") or []
    cfg = out.get("cfg") or {}
    changed: dict[str, Any] = {}
    for i, ap in enumerate(applied):
        verb = str((ops[i] or {}).get("verb") or "set") if i < len(ops) else "set"
        changed[ap] = "removed" if verb == "remove" else _at(cfg, ap)
    return {"ok": True, "applied_paths": applied, "changed": changed}


def _do_run_script(cli, args: dict) -> dict:
    script = str(args.get("script") or "")
    if not script.strip():
        return {"ok": False, "error": "script 为空"}
    mode = "build" if args.get("mode") == "build" else "run"
    timeout = float(args.get("timeout_s") or 120.0)
    before = _out(cli)
    since = max([l.get("seq", 0) for l in before.get("lines") or []] or [0])
    cli.exec(script, mode=mode, label="mcp")
    out = _wait_idle(cli, timeout)
    err = _tail_lines(out, since)
    text = "".join(str(l.get("text") or "") for l in out.get("lines") or []
                   if l.get("seq", 0) > since)
    return {"ok": not err, "output": (err or text)[-4000:]}


def _do_run_model(cli, args: dict) -> dict:
    before = _out(cli)
    since = max([l.get("seq", 0) for l in before.get("lines") or []] or [0])
    cli.run(gpu=args.get("gpu"), timeout_s=args.get("timeout_s"))
    out = _wait_idle(cli, float(args.get("timeout_s") or 90.0))
    err = _tail_lines(out, since)
    res = {"job_id": out.get("job_id"), "busy": bool(out.get("busy")),
           "phase": out.get("phase")}
    if err:
        # 提交失败（网关不可达/凭据过期/模型被拒）不能报成功——那会让 agent
        # 以为作业在跑，然后一直等一个不存在的作业。
        res.update({"ok": False, "error": err})
        return res
    res["ok"] = True
    if not res["job_id"]:
        res["note"] = "已发起提交，job 号稍后出现（用 get_model 的 job_id/phase 跟）"
    return res


def _do_get_results(cli, args: dict) -> dict:
    job_id = str(args.get("job_id") or "")
    if not job_id:
        # G58/G66 定案 2026-10-29：不猜"最近一次"——显式要 id，指路给它。
        return {"ok": False, "error":
                "job_id 必填——从 get_model 返回的 job_id（或作业日志/"
                "job-done 事件）里拿；服务端不替你猜'最近一次'"}
    mon = str(args.get("mon") or "")
    if not mon:
        return {"ok": True, "meta": cli.result_meta(job_id=job_id)}
    field = str(args.get("field") or "")
    if not field:
        return {"ok": False, "error": "给 mon 就要给 field（如 Ey；清单见不给 mon 时的返回）"}
    series = cli.result_series(job_id=job_id, mon=mon, field=field,
                               maxpoints=int(args.get("maxpoints") or 1200))
    return {"ok": True, "series": series}


def execute_tool(name: str, args: dict, *, session: Any = None) -> dict:
    """执行一个工具（供 MCP 与自测共用）。未知工具 → 显式错误。"""
    args = args or {}
    if name not in TOOL_NAMES:      # 先判名字：不因"没在跑的 Studio"遮蔽
        return {"ok": False,
                "error": f"未知工具 {name}（可用: {sorted(TOOL_NAMES)}）"}
    if name == "get_starter_script":
        return starter_script()
    if name == "list_materials":
        return _list_materials()
    if name == "login_status":
        return _login_status()
    # ── 会话面（要连上正在跑的 Studio）────────────────────────────────
    try:
        cli = _session(session)
        if name == "get_model":
            return _do_get_model(cli, args)
        if name == "edit_model":
            return _do_edit_model(cli, args)
        if name == "run_script":
            return _do_run_script(cli, args)
        if name == "run_model":
            return _do_run_model(cli, args)
        if name == "get_results":
            return _do_get_results(cli, args)
    except SessionUnavailable as e:
        return {"ok": False, "error": str(e), "session": False}
    except Exception as e:  # noqa: BLE001 —— 工具失败要说人话，不抛给 LLM
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}"}
    return {"ok": False,
            "error": f"工具 {name} 未接线（可用: {sorted(TOOL_NAMES)}）"}


def _openai_tools() -> list[dict]:
    """OpenAI function-calling 形态（agent 运行时消费）。"""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in TOOLS]


def build_server():
    """构建 MCP 服务器（stdio）。mcp 包缺失 → 指路错误。

    两代 SDK 都要认：mcp 2.x 把 ``FastMCP`` 改名为 ``MCPServer``（构造参数、
    ``.tool()``、``.run()`` 同名），1.x 仍是 ``FastMCP``。pyproject 声明的是
    ``mcp>=2.0,<3``，所以 2.x 这条是主路径，1.x 保留兼容。
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
                      "GENIE 电磁仿真。**本体是会话里那份模型**（用户在 Studio "
                      "里看着的就是它），不是脚本文本。工作流：get_model 看现状 → "
                      "能改一处就 edit_model（路径级增量）→ 出现循环/扫参/"
                      "程序化建模才 run_script → run_model 提交 → get_results 取数。"
                      "不要整份重写模型，也不要自己起新会话。"))

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
