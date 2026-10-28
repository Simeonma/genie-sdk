"""HTTP 客户端 — SDK 唯一碰网络的地方（genie-service：草稿镜像面已裁）。

职责（其余一切都在 _config.py 的本地 builder 里）::

    submit(cfg)      POST /api/jobs          → job_id（202）
    job_status(id)   GET  /api/jobs/{id}     → 状态 dict
    wait(id)         轮询 job_status 直到终态（同步语义的胶水）
    fetch_result(id) GET  /api/jobs/{id}/result → 结果文件字节（h5）
    summary(id)      GET  /api/jobs/{id}/summary
    log(id)          GET  /api/jobs/{id}/log
    health()         GET  /api/health        （connect 排障用）

错误翻译：FastAPI 的 422 体是 ``{"detail": [{loc, msg, type}, …]}``、
400 守卫是 ``{"detail": "…"}`` —— 统一拼成可读消息并保留原始体
（``GenieHTTPError.detail``），不吞服务器信息。
"""
from __future__ import annotations

import json
import os
import time as _time
from typing import Any, Callable

from ..core.job_state import TERMINAL_BAD, TERMINAL_OK
from ..models.errors import GenieHTTPError, GenieJobError
from ._transport import TransportError, make_transport

# 终态词表正典 = core.job_state（G24 裁决口径；G91 清账 2026-10-27 起
# 与 core/sweep 共用同一对常量——本行只是透传，别在这里再写字面量）。


def format_detail(d: Any) -> str:
    """服务端错误体 → 可读单行/多行字符串（原样信息不丢）。"""
    if isinstance(d, str):
        return d
    if isinstance(d, list):  # pydantic 422: [{"loc": ["body","domain",...], "msg": …}]
        parts = []
        for it in d:
            if isinstance(it, dict):
                loc = ".".join(str(x) for x in it.get("loc", [])
                               if x not in ("body",))
                msg = str(it.get("msg", it))
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(it))
        return "; ".join(parts)
    try:
        return json.dumps(d, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(d)


class Client:
    """极薄 HTTP 会话封装（传输实现可换：本机 requests / 网页 fetch）。所有方法可被测试替身覆盖（SDK 测试与
    前端代码生成器的 round-trip 测试都靠这一点注入假传输层）。"""

    def __init__(self, base_url: str, api_key: str | None = None,
                 username: str | None = None, timeout: float = 30.0,
                 timeout_download: float | None = None,
                 extra_headers: dict | None = None):
        if not base_url:
            raise ValueError(
                "connect() 需要 base_url（或设置环境变量 GENIE_URL）")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # 结果下载独立超时（audit 批 α）：fetch_result 拉大结果文件
        # 不该被 30s 常规请求超时掐断——None=不限，显式数值可覆写。
        self._timeout_download = timeout_download
        self._s = make_transport()
        # audit 批 β 账 3：key 缺省（None/空）时**不发** X-API-Key 头。
        # 服务端 require_api_key 对「缺头」与「空头」等价（均 401
        # "缺少 X-API-Key 请求头"，fdtd.serve.auth `if not x_api_key`），
        # 故此前 `api_key or ""` 的空头是纯噪音——现在不发更诚实。
        if api_key:
            self._s.headers["X-API-Key"] = api_key
        if username:
            # P1 用户名+key 双输入：服务器校验与 key 条目 name 配对
            self._s.headers["X-Username"] = username
        if extra_headers:
            # 会话轨（G72）：经 /gw 代理时带 X-Session-Token 等本机口令。
            self._s.headers.update(extra_headers)

    # ---- 基石 ----

    def _req(self, method: str, path: str, *, ok: tuple[int, ...] = (200,),
             json_body: Any = None,
             timeout: float | None = None) -> Any:
        eff = self.timeout if timeout is None else timeout
        # 会话轨的代理按 X-Upstream-Timeout 控制上游超时（直连时网关
        # 忽略未知头，无害）——否则 /gw 的 120s 缺省会盖掉本地设置。
        self._s.headers["X-Upstream-Timeout"] = str(eff)
        try:
            r = self._s.request(method, self.base_url + path,
                                json=json_body,
                                timeout=eff)
        except TransportError as e:
            # S3（audit 批 α）：不加自动重试（YAGNI），只提示可重入——
            # 网络瞬断对该 API 是安全操作（幂等读 / 提交前重试）。
            raise GenieHTTPError(
                f"网络错误 {method} {path}: {e}（网络瞬断可重试该操作）") from e
        if r.status_code not in ok:
            try:
                detail = r.json().get("detail")
            except Exception:  # noqa: BLE001 —— 非 JSON 错误体退回文本
                detail = r.text[:500]
            raise GenieHTTPError(
                f"HTTP {r.status_code} {method} {path}: {format_detail(detail)}",
                status=r.status_code, detail=detail)
        return r

    # ---- API 面（与 v100/backend/app/main.py 路由一一对应）----

    def login(self, username: str, password: str) -> str:
        """账号密码登录：POST /api/auth/login {username,password} →
        {token}；token 即后续请求的 X-API-Key（会话期绑定本 client）。
        返回 token，便于调用方持久化。凭据错误（401）照常抛
        GenieHTTPError，不吞服务器信息。"""
        tok = self._req(
            "POST", "/api/auth/login", ok=(200,),
            json_body={"username": username, "password": password},
        ).json().get("token")
        if not tok:
            raise GenieHTTPError(
                "登录响应缺少 token 字段", status=200, detail=None)
        self._s.headers["X-API-Key"] = tok
        if username:
            self._s.headers["X-Username"] = username
        return tok

    def set_key(self, api_key: str | None, username: str | None = None) -> None:
        """换发凭据（**不换会话、不丢模型**）。

        会话的授权寿命与模型寿命正交（M2/O2）：token 轮换、改密码、过期后
        重新登录，都只更新这一层头，实例与配置原样保留。空 key = 摘掉头
        （未登录/已退出的诚实形态，而不是发一个空头）。
        """
        if api_key:
            self._s.headers["X-API-Key"] = api_key
        else:
            self._s.headers.pop("X-API-Key", None)
        if username:
            self._s.headers["X-Username"] = username

    def health(self) -> dict:
        return self._req("GET", "/api/health").json()

    def submit(self, config: dict) -> str:
        """SimConfig JSON → job_id。400=守卫拒、422=形状错、401=key 无效。"""
        r = self._req("POST", "/api/jobs", ok=(200, 202), json_body=config)
        return r.json()["job_id"]

    def preflight(self, config: dict) -> dict:
        """需求预检（只读，**不提交作业**）→ 这个模型要多少资源。

        返回 ``{available, cap, caliber, demand{"1"..cap}, grid{n_tot, axes}}``：

        - ``demand[n]`` = n 张卡时**每 rank** 需要多少 MiB（含峰值因子与
          CUDA context；口径字符串在 ``caliber`` 里，数字自带口径才可对账）；
        - ``grid`` = 含 PML 的格数 + 三条 1D 格心（单位 µm，内核工作单位）。

        **给几张卡不在这里决定**：内核只报需求，卡数由网关按"等级上限 +
        需求 ≤ 机型容量"定（G78，见内核 `docs/LEDGER.md`「GPU 归属」）。
        所以 ``run.gpu`` 只是可选上限，不是卡数。

        老内核没有该端点（404）→ ``{"available": False}``：调用方按"未知"
        处理，别把它当"装不下"。
        """
        r = self._req("POST", "/api/config/preflight", ok=(200, 404),
                      json_body={"cfg": config})
        if r.status_code == 404:
            return {"available": False}
        body = r.json()
        body["available"] = True
        return body

    def job_status(self, job_id: str) -> dict:
        return self._req("GET", f"/api/jobs/{job_id}").json()

    def jobs(self) -> list:
        """本人作业列表（按 key 归属过滤；admin 见全量）：每项含
        job_id/state/created_at。脚本重启丢 job_id 后找回作业的入口。"""
        return self._req("GET", "/api/jobs").json()

    def summary(self, job_id: str) -> dict:
        return self._req("GET", f"/api/jobs/{job_id}/summary").json()

    def log(self, job_id: str) -> str:
        return self._req("GET", f"/api/jobs/{job_id}/log").text

    def fetch_result(self, job_id: str) -> bytes:
        """GET /api/jobs/{id}/result → 结果文件字节（genie-results-h5/1，
        h5py.File(io.BytesIO(...)) 直接可开。npz 时代的旧别名 fetch_npz
        已随 G106 硬摘轮删除——2026-10-28 前零调用实证在案）。"""
        return self._req("GET", f"/api/jobs/{job_id}/result",
                         timeout=self._timeout_download).content

    # ---- agent 车道端点（P1/P2：MCP 服务器与 agent 运行时消费；
    #      有意不进 Simulation 人类表面——极简定案由
    #      test_ui_domain_endpoints_are_consciously_excluded 守卫）----
    # （G17/G3 2026-10-16：v1 车道孤儿方法 get_config/validate_config/
    #   mesh_preview 摘除——零消费者；SimConfig 直操作与网格估算不属
    #   公开 SDK 面，估算真源 = genie.models.estimate，内核守卫同尺）

    # （2026-10-27 G19b 翻转：material_fit / nk_library 两方法随网关
    #   tools 面撤除而摘除——材质库/拟合回归 SDK 本地 genie.core.material；
    #   UI 下拉与拟合走 Studio 本地 /api/tools/*。）

    # （G3 2026-10-16 站长裁决：client.examples() 摘除——GUI 示例面板走
    #   static/examples 静态文件，SDK 用户看手册，端点无真实价值）

    def cancel(self, job_id: str) -> dict:
        """取消作业（queued 落 killed / running killpg）。"""
        return self._req("POST", f"/api/jobs/{job_id}/cancel",
                         ok=(200,)).json()

    def digest(self, job_id: str) -> dict:
        """结果数值摘要（峰频/幅值/透过率，读取零 GPU）。"""
        return self._req("GET", f"/api/jobs/{job_id}/digest").json()

    # ---- 专家会话面（/expert/*，阶段 7；公共服务统一专家端点）----

    def expert_post(self, path: str, body: dict) -> dict:
        r = self._s.post(self.base_url + path, json=body,
                         timeout=self.timeout)
        if r.status_code >= 400:
            from ..models.errors import GenieHTTPError
            raise GenieHTTPError(
                f"专家端点 {path} 失败（{r.status_code}）",
                status=r.status_code, detail=r.text[:400])
        return r.json()

    # ---- Sweep 父作业（P2-δ；子作业结果复用上面 /api/jobs 面）----

    def whoami(self) -> dict:
        """GET /whoami：key → Principal（name/admin/quota——G29b 滑动
        窗口查 max_concurrent_runs 用；网关侧已合并缺省）。"""
        return self._req("GET", "/whoami").json()

    def wait(self, job_id: str, poll_interval: float = 2.0,
             progress: Callable[[dict], None] | None = None,
             local_timeout_s: float | None = None) -> dict:
        """轮询到终态。succeeded → 返回状态 dict；failed/killed →
        GenieJobError（带 error 与日志尾）。``progress(status)`` 每轮回调。

        ``local_timeout_s``：本地兜底等待上限（audit 批 α，默认
        None=不限，跟随服务端作业超时）。超时抛 GenieError——作业
        **仍在服务端跑**，可 ``f.run()`` 无参重入续查（已提交实例
        不带 gpu/timeout_s 再 run() 即纯续查；时长 = domain.total_time。
        """
        if poll_interval < 0:
            raise ValueError("poll_interval 须 ≥ 0")
        if local_timeout_s is not None and local_timeout_s < 0:
            raise ValueError("local_timeout_s 须 ≥ 0")
        t0 = _time.monotonic()
        if progress is True:   # 打进度条约定（同 SweepHandle.wait：True=打印一行态）
            progress = lambda st: print(  # noqa: E731
                f"[job {job_id}] {st.get('state')} "
                + " ".join(f"{k}={v}" for k, v in
                           (st.get("progress") or {}).items()))
        while True:
            st = self.job_status(job_id)
            if progress is not None:
                progress(st)
            state = st.get("state")
            if state == TERMINAL_OK:
                return st
            if state in TERMINAL_BAD:
                err = st.get("error")
                if state == "archived" and not err:
                    # 归档作业取不到 410 明细时，至少把产物位置指出来（G24）
                    where = st.get("archive_path") or st.get("archive_url")
                    err = ("作业已被归档（终态）：结果不再在服务端热存储"
                           + (f"，产物见 {where}" if where else ""))
                raise GenieJobError(job_id, state, err, st.get("log_tail"))
            if (local_timeout_s is not None
                    and _time.monotonic() - t0 > local_timeout_s):
                raise GenieHTTPError(
                    f"本地等待超时（>{local_timeout_s}s）：作业 {job_id} "
                    f"可能仍在服务端（当前 {state}）——可 f.run() 无参"
                    "重入续查，或 get_job_status/summary 查看")
            _time.sleep(poll_interval)
