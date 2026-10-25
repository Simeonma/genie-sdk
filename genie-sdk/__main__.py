"""``genie`` 命令行入口——软件的一键形态（G12/G8，2026-10-13 定案）。

- ``genie studio``：本地拉起整套软件——一个持有 f 的会话（含干活子
  进程 worker）+ 浏览器 GUI；
- ``genie console``：你 shell 里的终端客户端——与 GUI terminal 平等
  的键盘（Jupyter console 语义）。

**拓扑（M2/O1+O3，2026-10-22）：两个进程，一个端口。**

    genie studio
      ① 会话对象与 GUI **同进程**（零 HTTP 跳、零第二个端口）
      ② 模型住在 worker 子进程里（故障域隔离 + 可中断 + 独立 cwd）
      ③ 跨进程客户端（console/外部工具）走 GUI 端口的 ``/session``，
         口令写在门牌 ~/.genie/studio.json；指纹之外一律不认
      ④ 全局唯一网络出口 = 会话（``/gw`` 白名单）

于是：关掉再开 = 一个干净的会话（模型靠快照恢复，不靠进程常驻）；
``--attach URL`` 显式连外部会话（跨机/云端调试，此时 Studio 退化成纯客户端）。
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import webbrowser

# Windows 控制台缺省 GBK/OEM 代码页，打印 ✓/✗ 这类符号会直接
# UnicodeEncodeError（真机实测踩中：配置写成功了、提示却崩成一屏
# traceback）。修法是**保留控制台原编码**（中文在 GBK 下原生可显），
# 只把编码外的字符降级为 ?——不强制 UTF-8，那会把中文打成乱码。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (OSError, ValueError):   # 已被重定向的非常规流：尽力而为
            pass


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def _next_free_port(host: str, start: int) -> int:
    port = start
    while port < start + 50:
        if _port_free(host, port):
            return port
        port += 1
    raise SystemExit(f"端口 {start}~{start + 49} 均被占用——用 --port 指定")


def _start_local_session() -> tuple[object, str, str]:
    """起本地会话（OS 分配端口 + 本机口令）→ (server, url, token)。

    **不做存活探测、不复用**：复用会把上一个后端/上一个凭据的旧会话
    带进来，正是"模型莫名其妙存在/消失"的根源（M2/O1）。
    """
    try:
        from genie.studio.session_service import serve
    except ImportError:  # pragma: no cover —— 指路不裸栈
        raise SystemExit('缺 server extra：pip install "genie-sdk[server]"')
    token = secrets.token_urlsafe(24)
    try:
        server, port = serve(0, token=token)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"✗ 本地会话启动失败：{e}")
    return server, f"http://127.0.0.1:{port}", token


def cmd_studio(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover —— 指路不裸栈（同下方 session_service）
        raise SystemExit('缺 server extra：pip install "genie-sdk[server]"')

    from genie.studio import rendezvous as rv
    from genie.studio import session_service as ss
    from genie.studio.server import create_app
    from genie.studio.session_client import SessionClient
    from genie.studio.session_local import LocalSessionHost

    attach = (args.attach or "").rstrip("/")
    if not attach:
        ss.enable_cred_broker()     # 本机单用户形态：登录截获/凭据代拨开启
    gui_port = args.port if args.port else _next_free_port("127.0.0.1", 8777)
    gui_url = f"http://127.0.0.1:{gui_port}"
    require_auth = not (args.open
                        or os.environ.get("GENIE_STUDIO_OPEN") == "1")

    def _on_attach(owner: str, session_id: str) -> None:
        """把"当前活跃会话"记进门牌（console 据此接上同一个模型）。"""
        rv.update(session_id=session_id, owner=owner)

    if attach:
        # 外部会话（跨机/云端调试）：Studio 只是个客户端，不持有会话
        token = os.environ.get("GENIE_SESSION_TOKEN", "")
        host, share = SessionClient(attach, token=token), False
        print(f"· 使用外部会话（{attach}）")
    else:
        # 同进程：会话对象与 GUI 在同一个进程里，**没有 HTTP 跳、没有第二个
        # 端口**（O3）。给跨进程客户端（console）留的门 = /session，带口令。
        token = secrets.token_urlsafe(24)
        ss.set_token(token)
        host, share = LocalSessionHost(), True
        rv.write(url=gui_url + "/session", token=token)
        print(f"· 会话与 GUI 同进程（跨进程客户端见 {gui_url}/session）")

    print(f"· GENIE 启动中 → {gui_url}   （Ctrl-C 退出）")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(gui_url)).daemon = True
        print("  提示：未登录不能做任何操作——先在启动页用账号密码登录"
              "（--open 或 env GENIE_STUDIO_OPEN=1 可退回本机免登录形态）")

    app = create_app(session=host,
                     mount_session=True,
                     share_session=share,
                     require_auth=require_auth,
                     on_attach=_on_attach)
    try:
        uvicorn.run(app, host="127.0.0.1", port=gui_port, log_level="warning")
    finally:
        if share:
            rv.clear()
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    from genie.studio import rendezvous as rv
    from genie.studio.console import run_console
    from genie.studio.session_client import SessionClient

    url = (args.attach or "").rstrip("/")
    token = os.environ.get("GENIE_SESSION_TOKEN", "")
    sid = (args.session or "").strip()
    server = None
    cli = None
    if not url:
        cli = SessionClient.discover()      # 读门牌：GUI 正在用的那个会话
    if cli is None and url:
        cli = SessionClient(url, token=token)
    if cli is not None:
        print(f"· 连接会话（{cli.base_url}）"
              + (f" · session={cli.session_id or 'default'}"))
    else:
        server, url, token = _start_local_session()
        rv.write(url=url, token=token)
        cli = SessionClient(url, token=token)
        print(f"· 本机会话已启动（{url}）")
    from genie.studio.session_client import resolve_owner
    try:
        return run_console(
            cli,
            session_id=(sid or cli.session_id or "default"),
            owner=(cli.owner or resolve_owner()))
    finally:
        if server is not None:
            rv.clear()


def cmd_connect(args: argparse.Namespace) -> int:
    """``genie connect``：写/看本机身份配置（G72a 零配置面的入口动作）。"""
    from genie.net.config import (config_path, gateway_source, load_config,
                                  save_config)

    url = (args.url or "").strip().rstrip("/")
    if not url:
        # 无 url = 展示当前解析结果（不泄露 key）。链与归一 = 真源
        # gateway_source（G101 清账：手抄链不归一时，展示的地址会和
        # 实际拨号地址字形不同）。
        cfg = load_config()
        gw, src = gateway_source()
        _SRC_ZH = {"env": "env GENIE_URL", "config": "credentials.json",
                   "default": "内置缺省"}
        if gw:
            print(f"✓ 当前网关：{gw}   （来源：{_SRC_ZH.get(src, src)}）")
        else:
            print("✗ 未配置网关地址（genie connect <服务地址>）")
        print(f"  配置文件：{config_path()}")
        user = cfg.get("username")
        if user:
            print(f"  已存用户名：{user}")
        return 0
    save_config(gateway=url, api_key=args.api_key or None,
                username=args.username or None)
    print(f"✓ 已写入 {config_path()}")
    if args.api_key:
        print("  api_key 已存（文件权限 0600）")
    # 轻探活：只告诉你状态，写配置不因网络失败而回滚
    try:
        import requests
        r = requests.get(url + "/api/health", timeout=3)
        print(f"✓ 探活 {url}/api/health → {r.status_code}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ 探活失败（配置已保存，不影响）：{e}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """``genie login``：人输一次账号密码 → 直连 ``/api/auth/login`` 换 token
    存本机（G72 终稿：复用 ``Client.login`` 同一条轨，无设备码/轮询）。

    密码 getpass 无回显、只过一遍手、永不写盘——本机只落 token。"""
    import getpass

    from genie.models.errors import GenieHTTPError
    from genie.net.client import Client
    from genie.net.config import resolve_gateway, save_config

    url = resolve_gateway(getattr(args, "gateway", None) or None)
    username = args.username or input("用户名（或邮箱）：").strip()
    password = getpass.getpass("密码：")
    try:
        token = Client(url, None, username=username).login(username, password)
    except GenieHTTPError as e:
        print(f"✗ 登录失败：{e}")
        print("  （没账号？先到网关注册；密码错误有次数限制，稍安勿躁）")
        return 1
    save_config(api_key=token, username=username, gateway=url)
    print(f"✓ 已登录为 {username}，token 已存本机"
          "（活跃即不过期；闲置 7 天后重跑本命令即可）")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    """``genie logout``：吊销本机这把 token（``DELETE /api/auth/tokens/self``，
    按 key 吊销自己，同账号其它设备不受影响）+ 清本机凭据。"""
    from genie.net.client import Client
    from genie.net.config import clear_config, resolve_credential, resolve_gateway

    key = resolve_credential(None, "GENIE_API_KEY", "api_key")
    if not key:
        print("· 本机没有已存的 token，无需登出")
        return 0
    url = resolve_gateway(getattr(args, "gateway", None) or None)
    try:
        r = Client(url, key)._s.delete(f"{url}/api/auth/tokens/self",
                                       timeout=10)
        if r.status_code < 300:
            print("✓ 网关侧已吊销本机 token（同账号其它设备不受影响）")
        else:
            print(f"⚠ 远端吊销失败（HTTP {r.status_code}）——本地仍会清除，"
                  "该 token 到期前在远端仍有效")
    except Exception as e:  # noqa: BLE001 —— 本地清理不因网络失败而跳过
        print(f"⚠ 远端吊销失败（{e}）——本地仍会清除")
    clear_config("api_key", "username")
    print("✓ 已清除本机凭据")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="genie", description="GENIE——GPU 电磁仿真软件"
        "（界面+配置器+后处理器；内核在服务端）")
    sub = ap.add_subparsers(dest="cmd")   # 裸敲 genie = studio（一键形态）
    st = sub.add_parser("studio", help="本地拉起整套软件（会话 + GUI）")
    st.add_argument("--no-browser", action="store_true",
                    help="不自动打开浏览器")
    st.add_argument("--port", type=int, default=None,
                    help="GUI 端口（缺省 8777，被占自动顺延）")
    st.add_argument("--attach", default="",
                    help="连一个已在跑的会话（http://host:port），不自己起")
    st.add_argument("--open", action="store_true",
                    help="本机免登录形态（调试用；等价 GENIE_STUDIO_OPEN=1）")
    st.set_defaults(fn=cmd_studio)
    co = sub.add_parser("console", help="shell 里的终端客户端（与 GUI 平等）")
    co.add_argument("--attach", default="",
                    help="连一个已在跑的会话（缺省读本机门牌）")
    co.add_argument("--session", default="",
                    help="会话 id（缺省读门牌里 GUI 正在用的那个）")
    co.set_defaults(fn=cmd_console)
    cn = sub.add_parser(
        "connect", help="写本机网关与凭据配置（一次即可，之后 connect() 无参即通）")
    cn.add_argument("url", nargs="?", default="",
                    help="网关地址（https://…）；缺省= 显示当前配置")
    cn.add_argument("--api-key", default=None, help="API key（也可后补）")
    cn.add_argument("--username", default=None, help="用户名（password 登录用）")
    cn.set_defaults(fn=cmd_connect)
    lg = sub.add_parser("login", help="登录并保存 token（输一次账号密码）")
    lg.add_argument("--username", default="", help="用户名或邮箱（缺省交互输入）")
    lg.add_argument("--gateway", default="", help="网关地址（缺省走解析链）")
    lg.set_defaults(fn=cmd_login)
    lo = sub.add_parser("logout", help="吊销本机 token（/tokens/self）并清除本机凭据")
    lo.add_argument("--gateway", default="", help="网关地址（缺省走解析链）")
    lo.set_defaults(fn=cmd_logout)
    ns = ap.parse_args(argv)
    if ns.cmd is None:                  # 裸敲 genie = studio（一键形态）
        ns.cmd = "studio"
        ns.fn = cmd_studio
        ns.no_browser = False
        ns.port = None
        ns.attach = ""
        ns.open = False
    return ns.fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
