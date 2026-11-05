"""genie.host.cli — ``genie`` 命令行（五动词，Q6.9 / Q6.6）。

全是「解析 + 调主人 + 打印」，**零领域逻辑**；三禁令（Q6.9，测试锁）：
不碰模型（不 import core / worker）、不直连网关（不 import net——拨号
归 host/gw，cli 连词表都不需要）、不自己写凭据文件（写仓只经
``host/cred`` 的函数）。裸 ``genie`` 等同 ``genie studio``。
``console`` 已移除（2026-11-04 核图裁定：未拍过语义）。
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
    verb = argv[0] if argv else "studio"          # 裸 genie 等同 studio
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
        return _mcp()
    print(f"未知动词 {verb!r}（可用：{' / '.join(VERBS)}）")
    return 2


def _login(rest) -> int:
    token = rest[0] if rest else getpass.getpass("cloud token: ")
    if not token:
        print("空 token，未登录")
        return 1
    cred.save_token(token)                        # 写仓只经 cred（一写函数）
    print(f"已登录（凭据写入 {cred.DEFAULT_PATH}，0600）")
    return 0


def _logout() -> int:
    cred.clear()
    print("已退出（云凭据已撤）")
    return 0


def _connect() -> int:
    """报接入信息（门牌在即报；不拨号验证——真连通性由调用方测）。"""
    data = read_plate(DEFAULT_PLATE)
    if not data:
        print("session: false（连不上——先 `genie studio`，Q3.2）")
        return 1
    print(json.dumps({"base_url": f"http://127.0.0.1:{data['port']}",
                      "token": data["token"]}))
    return 0


def _studio(rest) -> int:
    port = 0                                      # 无默认端口（Q3.2）：系统分配
    if rest and rest[0] == "--port":
        if len(rest) < 2 or not rest[1].isdigit():
            print("用法：genie studio [--port N]")
            return 2
        port = int(rest[1])
    gw = os.environ.get("GENIE_BACKEND") or DEFAULT_GATEWAY
    studio = App(os.getcwd(), port=port, gateway_url=gw)
    studio.start()
    print(f"Genie Studio: http://127.0.0.1:{studio.port}"
          f"（门牌 {studio.plate_path}；Ctrl+C 退出）")
    try:
        threading.Event().wait()                  # 阻塞服务
    except KeyboardInterrupt:
        pass
    finally:
        studio.stop()
    return 0


def _mcp() -> int:
    """agent 适配器入口（Q6.6）——随 ``agent/`` 包落地，占位不预铺。"""
    print("genie mcp: agent 适配器未落（Q6.6——随 agent/ 包落地）")
    return 2
