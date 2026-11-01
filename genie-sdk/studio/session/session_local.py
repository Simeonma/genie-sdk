"""genie/studio/session_local.py — ``/session`` 面的**同进程**适配器（O3）。

一份模型、两个适配器，区别只在"调函数的地址"：

- **同进程**（本地默认）：`LocalSessionHost` 直接调 `session_service` 的
  函数——零 HTTP、零端口、零序列化。Studio 与持有 `f` 的 worker 之间没有
  "边"，因为不存在需要跨的边界（同机、同用户、同信任域、同生命周期）。
- **跨进程**（agent / 外部工具）：
  `session_client.SessionClient` 走 `/session/*`（挂在 Studio 上的同一套
  路由，带本地口令）——**独立服务已退役**（G63：worker 与 GUI 同进程）。

两个适配器实现同一张方法表，所以门面（`register_session_routes` /
`register_result_routes` / `gateway`）对"调用落在哪"完全无知。

**身份维度为零**（2026-11-01）：没有 owner（工作区由进程决定）、没有
session_id（一个进程一份模型，新模型 = ``new_model()``）。适配器只搬运
凭据（`key`）。
"""
from __future__ import annotations

from . import session_service as _svc


class LocalSessionHost:
    """同进程适配器（与 SessionClient 同形，可互换）。"""

    #: 门面/日志用：不指向任何网络地址
    base_url = "in-process://session"
    token = ""

    # ---- /session 面 ----
    # （G115 收拢 2026-10-29：接待规则——attach-then-<动作> + mode 归一——
    # 唯一真源 = session_service 的 *_action 函数；本适配器只做参数搬运，
    # 带凭据换绑/归一的判断一律不住这里。锁 = test_session_reception。）

    def exec(self, code: str, *, mode: str = "run",
             label: str = "", key: str = "") -> dict:
        return {"id": _svc.exec_action(
            code=code, mode=mode, label=label, key=key)}

    def run(self, *, gpu: int | None = None,
            timeout_s: int | None = None, key: str = "") -> dict:
        return {"id": _svc.run_action(
            gpu=gpu, timeout_s=timeout_s, key=key)}

    def sweep(self, axes: list, *, max_parallel: int = 1,
              on_failure: str = "continue", name: str = "",
              key: str = "") -> dict:
        return {"id": _svc.sweep_action(
            axes=axes, max_parallel=max_parallel,
            on_failure=on_failure, name=name, key=key)}

    def sweep_plot(self, mon: str, *, field: str = "",
                   key: str = "") -> dict:
        return {"id": _svc.sweep_plot_action(mon=mon, field=field, key=key)}

    def patch(self, ops: list, *, key: str = "") -> dict:
        return {"id": _svc.patch_action(ops=ops, key=key)}

    def replace(self, model: dict, *, key: str = "") -> dict:
        return {"id": _svc.replace_action(model=model, key=key)}

    def set_script(self, text: str) -> dict:
        return {"id": _svc.get_kernel().set_script(text)}

    def get_script(self) -> dict:
        return {"text": _svc.get_kernel().script}

    def run_script(self) -> dict:
        return {"id": _svc.get_kernel().run_script()}

    def new_model(self) -> dict:
        """丢弃当前模型（下一个请求从空开始）。"""
        return _svc.new_model()

    def output(self, *, since: int = 0) -> dict:
        k = _svc.get_kernel()
        k.ensure()             # 读即唤起：Studio 重启后已开页面靠这条恢复
        return k.output(since)

    def stop(self) -> dict:
        return {"stopped": _svc.get_kernel().stop()}

    def reset(self) -> dict:
        """清理重启（R31）：与 SessionClient.reset 同一鸭子面——本地
        部署的 /api/exec/reset 走这里（修前 cli 无此方法 → 500）。"""
        return _svc.get_kernel().reset()

    def respawn_for_paths(self) -> dict:
        """切工作区后重挂 worker（G4a）：与 SessionKernel 同名方法同鸭子面。"""
        return _svc.get_kernel().respawn_for_paths()

    def attach(self, *, key: str = "") -> dict:
        return _svc.get_kernel().attach(key)

    def result_meta(self, *, job_id: str) -> dict:
        return _svc.result_meta(job_id=job_id)

    def result_series(self, *, job_id: str, mon: str, field: str,
                      maxpoints: int = 1200,
                      axes: str = "", slices: str = "") -> dict:
        # G148 批 3：axes/slices 选轴视图透传（G115 双写面的既声明代价
        # ——改 /result/series 签名须同轮改本镜像，e2e 亲验抓出）。
        return _svc.result_series(job_id=job_id, mon=mon, field=field,
                                  maxpoints=maxpoints,
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
