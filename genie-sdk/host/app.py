"""genie.host.app — HTTP 面（浏览器 + agent）+ 门牌 + 会话装配。

Q6.4 内容面 URL 三条（``/`` / ``/manual/`` / ``/examples/``）+ ``/static/*``；
会话面（环投影 / exec 下行 / 取消 / 重启 / 脚本读写——经 session；
「重启 worker」入口 = ``POST /restart``）；agent 面 = 同一组动词挂
``/session/*`` 前缀、带本地 session token（门牌递送，Q2.1）；``/gw``
归 ``host/gw``（纯代理）。门牌 ``~/.genie/studio.json``（Q3.2：0600、
只删自己 pid、无默认端口——绑 ``127.0.0.1:0`` 系统分配，不扫描）。

表单 delta 生成（Q1.1 / Q1.1.2 / **Q1.1.4：生成住本处**）：浏览器只发
结构化薄意图（collection / kind / id / field / value）→ ``form_delta``
产一行 Python（值一律 ``repr`` 字面量、不拼字符串；**字段类型问
protocol——反射真源，不抄第二份表**：collection→SimConfig 字段、
kind→判别 union 成员、tuple 注解→JSON list 收成 tuple）→ session 发
``exec(echo=False)``（Q3.6 终端不回显）。加结构 / 删结构意图随 GUI 壳
真需求长（Q1.1：``f.geom.add_box('name')`` 后逐字段赋值）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Union, get_args, get_origin
from urllib.parse import parse_qs, urlparse

from pydantic.fields import FieldInfo
from genie_protocol import _wire

from . import card, cred, gw
from .script import ScriptState
from genie.net.client import WORKER_TOKEN_HEADER
from genie.session import Session

__all__ = ["App", "form_delta", "write_plate", "read_plate", "remove_plate",
           "SESSION_TOKEN_HEADER", "DEFAULT_PLATE"]

#: agent 面的本地 session token 头（浏览器同源不带——Q2.1）
SESSION_TOKEN_HEADER = "X-Genie-Session-Token"

DEFAULT_PLATE = os.path.join(os.path.expanduser("~"), ".genie", "studio.json")

_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


# ── 门牌（Q3.2）──────────────────────────────────────────────────

def write_plate(path, port: int, token: str) -> None:
    data = {"pid": os.getpid(), "port": port, "token": token}
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(path, 0o600)


def read_plate(path) -> dict | None:
    """读门牌（agent/cli 的口粮）；无牌 / 坏牌 → None。"""
    p = str(path)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remove_plate(path) -> None:
    """只删自己 pid 的牌（别人的 / 坏牌不动）。"""
    p = str(path)
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("pid") == os.getpid():
        os.remove(p)


# ── 表单 delta 生成（Q1.1.2 / Q1.1.4）───────────────────────────

#: f 集合属性名 → SimConfig 字段名（反射入口；不抄模型字段清单）
_F_ATTR = {"domain": "domain", "mesh": "mesh", "geom": "geometry",
           "mat": "materials", "src": "source", "mon": "monitor"}


def _resolve_spec(coll: str, kind):
    """collection（+kind）→ protocol 的 Spec 类（全部反射真源）。"""
    ann = _wire.SimConfig.model_fields[_F_ATTR[coll]].annotation
    if get_origin(ann) is list:                 # 集合字段：list[Annotated[...]]
        ann = get_args(ann)[0]
    args = get_args(ann)
    if args and len(args) == 2 and isinstance(args[1], FieldInfo):
        disc = args[1].discriminator            # Annotated[Union, Field(...)]
        for cls in get_args(args[0]):
            fld = cls.model_fields.get(disc)
            if fld is not None and fld.default == kind:
                return cls
        raise ValueError(f"unknown kind {kind!r} for {coll}")
    return ann                                 # 单例：注解即模型类


def _coerce(ann, value):
    """JSON 值 → 模型注解形（tuple 注解收 list 成 tuple；其余原样）。"""
    if get_origin(ann) is Union:
        for a in get_args(ann):
            if get_origin(a) is tuple:
                return _coerce(a, value)
        return value
    if get_origin(ann) is tuple and isinstance(value, list):
        args = get_args(ann)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(value)
        return tuple(_coerce(a, v) for a, v in zip(args, value))
    return value


def form_delta(intent: dict) -> str:
    """浏览器薄意图 → 一行 Python delta（唯一公开面；脏值锁见 §5）。"""
    coll = intent.get("collection")
    field = intent.get("field")
    if coll not in _F_ATTR or not isinstance(field, str) \
            or not field.isidentifier():
        raise ValueError(f"bad form intent: {intent!r}")
    spec = _resolve_spec(coll, intent.get("kind"))
    if field not in spec.model_fields:
        raise ValueError(f"unknown field {field!r} on {coll}")
    value = _coerce(spec.model_fields[field].annotation, intent.get("value"))
    if intent.get("id") is None:
        return f"f.{coll}.{field} = {value!r}"
    return f"f.{coll}[{int(intent['id'])}].{field} = {value!r}"


# ── App（HTTP 面 + session 装配）────────────────────────────────

_PLACEHOLDER = {
    "/": "Genie Studio",
    "/manual/": "Genie Manual",
    "/examples/": "Genie Examples",
}


#: 云网关缺省（公开线 1.0.0a1，站长拍 2026-11-05）：生产唯一入口；
#: 换网关走 env ``GENIE_BACKEND``（cli 层读）。
DEFAULT_GATEWAY = "https://genie-optics.com"


class App:
    """一个 Studio 进程的全部装配。用法::

        app = App(workspace, gateway_url="https://gw.example")  # None = 未接
        app.start()                  # 绑 127.0.0.1:0 → session → 门牌
        ...                          # http://127.0.0.1:{app.port}
        app.stop()                   # 撤自己的牌 + 收 worker + 关服务

    ``worker_cmd`` 是测试缝（假 worker；生产只有 ``python -m genie.worker``）。
    """

    def __init__(self, workspace, *, gateway_url=None, port: int = 0,
                 plate_path=None, cred_path=None, worker_cmd=None):
        self.workspace = str(workspace)
        self.gateway_url = gateway_url
        self.port_hint = port
        self.plate_path = str(plate_path) if plate_path else DEFAULT_PLATE
        self.cred_path = str(cred_path) if cred_path else cred.DEFAULT_PATH
        self.worker_cmd = worker_cmd
        self.token = secrets.token_hex(16)          # 本地 session token（随机）
        self.worker_token = secrets.token_hex(16)   # spawn 随机（Q2.1）
        self.script = ScriptState(self.workspace)
        self.session = None
        self.port = None
        self._srv = None

    # -- 生命周期 ----------------------------------------------------

    def start(self) -> None:
        outer = self

        class _Handler(_RouteHandler):
            pass

        _Handler.app = outer
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port_hint),
                                        _Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        # 先绑端口再 spawn：GENIE_GW_BASE_URL 要含真端口（session 图 §1）
        self.session = Session(
            self.workspace,
            gw_base_url=f"http://127.0.0.1:{self.port}/gw",
            worker_token=self.worker_token,
            on_cancel=self._cancel_via_gw,
            worker_cmd=self.worker_cmd)
        self.session.start()
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        write_plate(self.plate_path, self.port, self.token)

    def stop(self) -> None:
        remove_plate(self.plate_path)               # 只删自己 pid
        if self.session is not None:
            self.session.close()
            self.session = None
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # -- 取消回调（session → /gw 同一扇门）--------------------------

    def _cancel_via_gw(self, job_ids) -> None:
        hdrs = gw.upstream_headers(cred.load_token(path=self.cred_path))
        for jid in job_ids:
            res = gw.handle(
                "POST", f"/api/jobs/{jid}/cancel",
                {WORKER_TOKEN_HEADER: self.worker_token}, None,
                worker_token=self.worker_token, gateway_url=self.gateway_url,
                upstream_headers=hdrs)
            if res.status >= 400:
                raise RuntimeError(f"cancel {jid}: HTTP {res.status}")


class _RouteHandler(BaseHTTPRequestHandler):
    """路由薄层：内容面 / 会话面（浏览器）+ /session（agent）+ /gw。"""
    app: "App"
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):                      # 静默
        pass

    # -- 应答助手 ----------------------------------------------------

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json")

    def _html(self, status: int, text: str, ctype: str) -> None:
        self._send(status, text.encode("utf-8"), ctype)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # -- 分派 --------------------------------------------------------

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str):
        try:
            self._dispatch(method)
        except BrokenPipeError:
            pass
        except Exception as e:                      # noqa: BLE001 —— 单请求兜底
            try:
                self._json(500, {"error": str(e)})
            except OSError:
                pass

    def _dispatch(self, method: str):
        app = self.app
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/gw" or path.startswith("/gw/"):
            return self._gw(app, method, path[3:] or "/")
        if path.startswith("/session/"):
            if self.headers.get(SESSION_TOKEN_HEADER) != app.token:
                return self._json(401, {"error": "session token required"})
            return self._verbs(app, method,
                               path[len("/session"):] or "/", parsed)
        if method == "POST":                        # 浏览器面防跨站（同源不带）
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{app.port}":
                return self._json(403, {"error": "cross-origin"})
        return self._verbs(app, method, path, parsed)

    def _gw(self, app, method: str, path: str):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        res = gw.handle(method, path, self.headers, body,
                        worker_token=app.worker_token,
                        gateway_url=app.gateway_url,
                        upstream_headers=gw.upstream_headers(
                            cred.load_token(path=app.cred_path)))
        self.send_response(res.status)
        self.send_header("Content-Type", res.content_type)
        self.end_headers()
        for chunk in res.chunks:                    # 流式透传
            self.wfile.write(chunk)

    def _verbs(self, app, method: str, path: str, parsed):
        q = parse_qs(parsed.query)
        if method == "GET":
            if path == "/events":                   # 环投影（300ms 单拍）
                since = int(q.get("since", ["0"])[0])
                epoch, nxt, evs = app.session.events(since)
                return self._json(200, {"epoch": epoch, "since": nxt,
                                        "events": evs})
            if path == "/script":                   # 编辑器回填（Q3.5）
                focused = q.get("focus", ["0"])[0] == "1"
                return self._json(200, app.script.read(focused=focused))
            if path in _PLACEHOLDER:                # 内容面三条（Q6.4）
                name = _PLACEHOLDER[path]
                return self._html(200, _page(name), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            return self._json(404, {"error": "no route"})

        body = self._read_json()
        if path == "/exec":                         # exec 下行（表单/终端/Run）
            rid = app.session.submit_exec(
                body.get("code", ""), fresh=bool(body.get("fresh")),
                echo=bool(body.get("echo", True)))
            return self._json(200,
                              {"id": rid} if rid is not None else {"busy": True})
        if path == "/cancel":                       # 卡片停止键 / f.cancel 同门
            return self._json(200, {"canceled": card.stop(app.session)})
        if path == "/restart":                      # 「重启 worker」入口
            app.session.restart_worker()
            return self._json(200, {"ok": True})
        if path == "/script":                       # 打字即落盘 / Ctrl+S
            return self._json(200, {"version": app.script.write(
                body.get("text", ""))})
        if path == "/script/target":                # 打开 / 另存为
            ver = app.script.open_as(body.get("path", ""))
            return self._json(200, {"version": ver,
                                    "path": str(app.script.path)})
        return self._json(404, {"error": "no route"})

    def _static(self, rel: str):
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ("..", "") for p in parts):
            return self._json(404, {"error": "no route"})
        f = _STATIC_ROOT.joinpath(*parts)
        if not f.is_file():
            return self._json(404, {"error": "no route"})
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        data = f.read_bytes()
        if ctype.startswith("text/"):
            data, ctype = data.decode("utf-8", errors="replace"), \
                ctype + "; charset=utf-8"
        self._send(200, data, ctype)


def _page(name: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{name}</title></head><body><h1>{name}</h1>"
            f"<p>GUI 壳随 static/ 落地（Q6.4）。</p></body></html>")
