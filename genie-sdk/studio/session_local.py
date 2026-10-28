"""genie/studio/session_local.py — 会话的**同进程**适配器（O3）。

一个模型一个 actor（`SessionKernel`），它的"地址"是部署细节：

- **同进程**（本地默认）：`LocalSessionHost` 直接调 `session_service` 的
  函数——零 HTTP、零端口、零序列化。Studio 与它的会话之间没有"边"，
  因为不存在任何需要跨的边界（同机、同用户、同信任域、同生命周期）。
- **跨进程**（云宿主 / `genie console` / 外部工具）：
  `session_client.SessionClient` 走 `/session/*`（挂在 Studio 上的同一套
  路由，带本机口令）——**独立会话服务已退役**（G63：会话与 GUI 同进程）。

两个适配器实现同一张方法表，所以门面（`register_session_routes` /
`register_result_routes` / `gateway`）对"会话在哪"完全无知。
"""
from __future__ import annotations

from . import session_service as _svc
from .session_client import DEFAULT_SESSION


class LocalSessionHost:
    """同进程会话适配器（与 SessionClient 同形，可互换）。"""

    #: 门面/日志用：不指向任何网络地址
    base_url = "in-process://session"
    token = ""

    # ---- 会话面 ----

    def exec(self, code: str, *, owner: str,
             session_id: str = DEFAULT_SESSION, mode: str = "run",
             label: str = "", key: str = "") -> dict:
        k = _svc.get_kernel(owner, session_id)
        if key:
            k.attach(key)          # 幂等：换 key 不换模型
        return {"id": k.submit(code, mode=mode, label=label)}

    def run(self, *, owner: str, session_id: str = DEFAULT_SESSION,
            gpu: int | None = None, timeout_s: int | None = None,
            key: str = "") -> dict:
        k = _svc.get_kernel(owner, session_id)
        if key:
            k.attach(key)
        return {"id": k.run(gpu=gpu, timeout_s=timeout_s)}

    def patch(self, ops: list, *, owner: str,
              session_id: str = DEFAULT_SESSION, key: str = "") -> dict:
        k = _svc.get_kernel(owner, session_id)
        if key:
            k.attach(key)
        return {"id": k.patch(ops)}

    def replace(self, model: dict, *, owner: str,
                session_id: str = DEFAULT_SESSION, key: str = "") -> dict:
        k = _svc.get_kernel(owner, session_id)
        if key:
            k.attach(key)
        return {"id": k.replace(model)}

    def output(self, *, owner: str, session_id: str = DEFAULT_SESSION,
               since: int = 0) -> dict:
        k = _svc.get_kernel(owner, session_id)
        k.ensure()             # 读即唤起：Studio 重启后已开页面靠这条恢复
        return k.output(since)

    def stop(self, *, owner: str, session_id: str = DEFAULT_SESSION) -> dict:
        return {"stopped": _svc.get_kernel(owner, session_id).stop()}

    def reset(self, *, owner: str, session_id: str = DEFAULT_SESSION) -> dict:
        """清理重启（R31）：与 SessionClient.reset 同一鸭子面——本地
        部署的 /api/exec/reset 走这里（修前 cli 无此方法 → 500）。"""
        return _svc.get_kernel(owner, session_id).reset()

    def attach(self, *, owner: str, session_id: str = DEFAULT_SESSION,
               key: str = "") -> dict:
        return _svc.get_kernel(owner, session_id).attach(key)

    def result_meta(self, *, owner: str,
                    session_id: str = DEFAULT_SESSION, job_id: str = "") -> dict:
        return _svc.result_meta(job_id=job_id, owner=owner,
                                session_id=session_id)

    def result_series(self, *, owner: str, job_id: str, mon: str, field: str,
                      session_id: str = DEFAULT_SESSION,
                      maxpoints: int = 1200,
                      axes: str = "", slices: str = "") -> dict:
        # G148 批 3：axes/slices 选轴视图透传（G115 双写面的既声明代价
        # ——改 /result/series 签名须同轮改本镜像，e2e 亲验抓出）。
        return _svc.result_series(job_id=job_id, mon=mon, field=field,
                                  maxpoints=maxpoints, owner=owner,
                                  session_id=session_id,
                                  axes=axes, slices=slices)

    def health(self) -> dict:
        return _svc.health()

    # ---- 网关面（唯一出口，同进程直接调）----

    def gateway(self, method: str, path: str, *, key: str = "", data=None,
                content_type: str = "application/json", params=None,
                timeout: float = 120.0):
        return _svc.gateway_forward(method, path, key=key, data=data,
                                    content_type=content_type, params=params,
                                    timeout=timeout)
