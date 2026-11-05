"""genie.host — Studio 进程（浏览器 HTTP 面 / 云凭据仓 / 唯一网关出口）。

公开面与禁令 → ``MODMAP.md``；裁决 → ``docs/DECISIONS.md``（Q2.1 /
Q2.2-5A / Q3.2 / Q5.3 / Q6.9）。六模块：app / gw / cred / script /
card / cli；session 住本进程（host 只跟 session 说话，不 import
ipc / worker / core）。
"""
