"""genie.host.cli — the ``genie`` command line (the five verbs).

Everything is "parse + call the host + print", **zero domain logic**; the
three bans (locked by tests): no model access (no importing core / worker),
no direct gateway dialing (no importing net — dialing belongs to host/gw,
cli needs not even the vocabulary), no credential-file writes (writing the
store goes only through ``host/cred`` functions). Bare ``genie`` is the
same as ``genie studio``.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import threading

from . import cred
from .app import DEFAULT_GATEWAY, DEFAULT_PLATE, App, read_plate

__all__ = ["main", "VERBS"]

VERBS = ("login", "logout", "connect", "studio", "mcp")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv else "studio"          # bare genie is the same as studio
    rest = argv[1:]
    if verb == "login":
        return _login(rest)
    if verb == "logout":
        return _logout()
    if verb == "connect":
        return _connect()
    if verb == "studio":
        return _studio(rest)
    if verb == "mcp":
        return _mcp(rest)
    print(f"unknown verb {verb!r} (available: {' / '.join(VERBS)})")
    return 2


def _login(rest) -> int:
    token = rest[0] if rest else getpass.getpass("cloud token: ")
    if not token:
        print("empty token; not logged in")
        return 1
    cred.save_token(token)                        # store writes go only through cred (single write function)
    print(f"logged in (credentials written to {cred.DEFAULT_PATH}, 0600)")
    return 0


def _logout() -> int:
    cred.clear()
    print("logged out (cloud credentials cleared)")
    return 0


def _connect() -> int:
    """Report connection info (report whenever the studio plate is present; no dial-out verification — the caller tests real connectivity)."""
    data = read_plate(DEFAULT_PLATE)
    if not data:
        print("session: false (unreachable — start `genie studio` first)")
        return 1
    print(json.dumps({"base_url": f"http://127.0.0.1:{data['port']}",
                      "token": data["token"]}))
    return 0


def _studio(rest) -> int:
    """Studio = a **native window** (pywebview / WebView2) by default
    (2026-11-22, 站长拍 A): same static GUI, no address bar. ``--browser``
    keeps the old server-in-a-browser mode (dev self-test: static edits
    need a page refresh only); no pywebview installed → loud fallback."""
    port = 0                                      # no default port: system-assigned
    use_webview = True
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--port" and i + 1 < len(rest) and rest[i + 1].isdigit():
            port = int(rest[i + 1])
            i += 2
        elif a == "--browser":
            use_webview = False
            i += 1
        elif a == "--debug":                       # devtools (F12) in the window
            i += 1
        else:
            print("usage: genie studio [--port N] [--browser] [--debug]")
            return 2
    gw = os.environ.get("GENIE_BACKEND") or DEFAULT_GATEWAY
    studio = App(os.getcwd(), port=port, gateway_url=gw)
    try:
        studio.start()                             # single-instance guard lives here
    except RuntimeError as e:                      # already running → that window
        print(str(e))
        return 0
    url = f"http://127.0.0.1:{studio.port}"

    win = None
    debug = "--debug" in rest
    if use_webview:
        try:
            import webview                     # pywebview (WebView2 shell)
            from webview.menu import Menu, MenuAction
        except ImportError:
            print("pywebview not installed — falling back to the browser "
                  "(pip install pywebview for the native window)")
        else:
            # 尺寸按 **CSS 像素**给（WebView2 会按系统 DPI 再放大）：150% 缩放下
            # 1440×900 会变成 2160 物理宽、越界出屏（2026-11-22 实测）。
            import ctypes
            u32 = ctypes.windll.user32
            try:
                scale = u32.GetDpiForSystem() / 96.0
            except Exception:                  # noqa: BLE001 — pre-1607 fallback
                scale = 1.0
            sw = u32.GetSystemMetrics(0) / scale
            sh = u32.GetSystemMetrics(1) / scale

            def _reload(window):
                """窗口内刷新（调试静态文件用）：重载当前 URL。"""
                try:
                    window.evaluate_js("location.reload()")
                except Exception:              # noqa: BLE001 — window gone
                    pass

            menu = Menu("View", [
                MenuAction("Reload page", _reload),
            ])
            win = webview.create_window(
                "GENIE-OPTICS Studio", url,
                width=int(min(1440, sw * 0.92)),
                height=int(min(900, sh * 0.92)),
                menu=[menu])               # pywebview 6: a list, not one Menu
    print(f"Genie Studio: {url}"
          f" (plate: {studio.plate_path}"
          + ("" if win is not None else "; Ctrl+C to stop") + ")")
    try:
        if win is not None:
            # 窗口/任务栏图标 = 品牌 favicon（部署产物，母版在 20-market/brand）；
            # pywebview 6 的 icon 在 start() 上（winforms 后端生效），不在 create_window。
            icon = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "static", "brand", "favicon.ico")
            webview.start(debug=debug,
                          icon=icon if os.path.isfile(icon) else None)
        else:
            threading.Event().wait()           # browser mode: block to keep serving
    except KeyboardInterrupt:
        pass
    finally:
        studio.stop()
    return 0


def _mcp(rest) -> int:
    """Agent adapter (Q6.6): hand off to ``genie.agent`` on stdio.

    Imported lazily and on purpose — the adapter is Studio's *client*, not
    part of the host; a plain Studio run never loads it. Locked by a test:
    importing this module must not import ``genie.agent``.
    """
    from ..agent.mcp import main as agent_main
    return agent_main(rest)
