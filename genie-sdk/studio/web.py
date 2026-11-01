"""studio/web — 网页形态的入口（本机对应的是 ``studio/__main__.py``）。

和 ``genie studio`` 的差别只有两条，都来自浏览器：

1. **没有 uvicorn**（没有 socket）：前端把一次请求交进来，这里用 ASGI
   **直调**同一个 ``create_app`` —— 路由、校验、错误措辞一字不差；
2. **worker 跑在同进程**：Pyodide 没有子进程，``make_runtime`` 会选
   ``InprocRuntime``（见 `_runtime.py`）。

于是"网页用户和本机用户跑同一份代码"在入口这一层也成立：两个入口只是
把请求送进同一个 app 的方式不同。
"""
from __future__ import annotations

import json
from typing import Any


def _run(coro: Any) -> Any:
    """跑一个协程：浏览器里用 Pyodide 的 run_sync，本机用 asyncio.run。

    Pyodide 的 asyncio 跑在 JS 事件循环上（已运行），所以 ``asyncio.run``
    在里面会拒绝；``run_sync`` 正是为"Worker 里同步等待"准备的（spike
    实测 `run_sync(pyfetch)` 可用，见 LEDGER G80）。
    """
    try:
        from pyodide.ffi import run_sync        # 只在浏览器里存在
    except ImportError:
        import asyncio
        return asyncio.run(coro)
    return run_sync(coro)


async def _call_asgi(app, method: str, path: str, query: str,
                     headers: dict, body: bytes) -> tuple[int, str, bytes]:
    """把一次请求直接喂给 ASGI app（不走网络）。"""
    raw_headers = [(k.lower().encode(), str(v).encode())
                   for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "asgi": {"version": "3.0"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": raw_headers,
        "server": ("127.0.0.1", 0), "client": ("127.0.0.1", 0),
        "root_path": "",
    }
    inbox = [{"type": "http.request", "body": body, "more_body": False}]
    out: dict = {"status": 500, "headers": [], "body": b""}

    async def receive():
        return inbox.pop() if inbox else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = message.get("headers") or []
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    await app(scope, receive, send)
    ctype = ""
    for k, v in out["headers"]:
        if k.lower() == b"content-type":
            ctype = v.decode("latin-1")
    return out["status"], ctype, out["body"]


def dispatch(app, method: str, path: str, body: bytes = b"",
             headers: dict | None = None) -> tuple[int, str, bytes]:
    """JS 调用面：一次请求 → ``(status, content_type, body)``（不抛异常）。"""
    from urllib.parse import urlsplit
    u = urlsplit(path)
    return _run(_call_asgi(app, method, u.path or "/", u.query,
                           headers or {}, body or b""))


def serve(*, static_dir: str | None = None):
    """建 app（= 本机 ``genie studio`` 的同一份），返回它供 JS 调 ``dispatch``。"""
    from .server import create_app
    from .session.session_local import LocalSessionHost

    app = create_app(session=LocalSessionHost(), mount_session=True,
                     share_session=True, **({"static_dir": static_dir}
                                            if static_dir else {}))
    return app


def mount_to_js(app=None, *, name: str = "genieWeb") -> Any:
    """把入口挂到浏览器全局（JS 里直接 ``genieWeb.dispatch(...)``）。

    返回挂上去的对象；没有 ``js`` 模块（本机测试）时只返回它、不挂。
    """
    app = app or serve()

    def _dispatch(method, path, body=None, headers=None):
        status, ctype, raw = dispatch(app, method, path,
                                      body or b"", headers or {})
        return json.dumps({"status": status, "content_type": ctype,
                           "body": raw.decode("utf-8", "replace")})

    api = {"dispatch": _dispatch, "app": app}
    try:
        import js                              # Pyodide 才有的桥
    except ImportError:
        return api
    setattr(js, name, api)
    return api
