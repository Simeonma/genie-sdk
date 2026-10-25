"""genie.studio — 随包分发的 GUI。

本地形态：``genie studio``（一条命令，会话与 GUI 同进程；本机 127.0.0.1）。
网页形态（G80）：``studio/web.py`` 在 Pyodide 里跑同一个 app 工厂与全部
``/api/*`` 路由。原"云端宿主"已于 2026-10-25 随服务端宿主整包退役。
"""
