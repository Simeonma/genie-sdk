"""会话唯一家（2026-10-11 站长裁决，第一性收敛；2026-10-22 M2/O1+O2 收敛）。

一个服务、按 (owner, session_id) 一份 kernel 管理器、一个 worker、一个输出环。

    host ─┐
          ├─(HTTP, 仅本机)→ session_service ──→ kernel_worker 子进程
    studio┘

**M2/O1**（确定性启动）：本地 ``genie studio`` 自己起这个服务（端口由
OS 分配、地址与口令经内存 + rendezvous 文件显式传递），**不再"探测 8123
并复用"**——复用会把上一个后端/上一个凭据的旧会话继承进来，是"模型莫名
其妙存在/消失"的根源。跨进程形态（``genie console``、外部工具）仍走本
服务的端口。

**M2/O2**（三生命周期正交）：
  - ``owner``    = 隔离到哪个目录（工作区根/<owner>/）；
  - ``key``      = 能不能提交（``/attach`` 幂等换绑，**换 key 不换模型**）；
  - ``session_id`` = 哪一份模型（目录 session/<owner>/<session_id>/）。
  三者互不决定：token 轮换/改密/过期重登只碰 key；新模型 = 新 session_id。

**唯一网关出口**：``/gw/{path}`` 代所有客户端（studio/host/console）拨后端，
带路径白名单。上游不可达的措辞只有这一处实现。

本服务只听 127.0.0.1；身份判断只发生在 host/studio 客户端。可选口令
（``GENIE_SESSION_TOKEN``）用于本机会话端口——未配置即旧的"本机信任面"。
"""
from __future__ import annotations

import json
import os
import threading

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# owner / session_id 白名单 = 全仓唯一一份（genie.studio.session_client）
from .kernel_session import workspace_root
from .session_client import DEFAULT_SESSION, check_owner, check_session_id

_kernels: dict[tuple[str, str], object] = {}   # (owner, session_id) → SessionKernel
_lock = threading.Lock()
_TOKEN = ""            # 会话端口口令（serve()/set_token() 注入；空 = 不校验）


def set_token(token: str) -> None:
    """设置本进程会话端口的口令（O3：Studio 同进程挂载时由入口设置）。"""
    global _TOKEN
    _TOKEN = token or ""


def get_kernel(owner: str = "user", session_id: str = DEFAULT_SESSION):
    """按 (owner, session_id) 取/建 kernel（缺省建：一 owner 一会话一 worker）。

    owner 缺省回退 "user"（与 services/auth.py 的 name 缺失回退同一约定）；
    测试可 monkeypatch 本函数注入假 kernel。
    """
    owner = check_owner(owner)
    session_id = check_session_id(session_id or DEFAULT_SESSION)
    with _lock:
        k = _kernels.get((owner, session_id))
        if k is None:
            from genie.studio.kernel_session import SessionKernel
            k = _kernels[(owner, session_id)] = SessionKernel(
                owner=owner, session_id=session_id)
        return k


app = FastAPI(title="genie-session", docs_url=None, redoc_url=None)


# ── 会话端口口令（可选）────────────────────────────────────────────
@app.middleware("http")
async def _session_token(request: Request, call_next):
    tok = _TOKEN or os.environ.get("GENIE_SESSION_TOKEN", "")
    if tok:
        # 挂载在 Studio 的 /session 下时，request.url.path 带前缀（root_path）
        # ——先剥掉再比，否则 /session/health 会被当成受保护路径。
        root = request.scope.get("root_path") or ""
        path = request.url.path
        if root and path.startswith(root):
            path = path[len(root):] or "/"
        if path not in ("/health",):
            if request.headers.get("X-Session-Token") != tok:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "会话端口口令缺失"},
                                    status_code=401)
    return await call_next(request)


class ExecIn(BaseModel):
    code: str
    mode: str = "run"        # M3：run=在当前命名空间执行 / build=构造新模型
    key: str = ""
    label: str = ""    # 执行来源标注（GUI 透传，如「选中 L2-2」）→ 环 input.mark
    owner: str = "user"          # B1：归属身份
    session_id: str = DEFAULT_SESSION   # O2：哪一份模型


class RunIn(BaseModel):
    """提交当前模型（M3：Run 是**读**操作，不改模型）。"""
    gpu: int | None = None
    timeout_s: int | None = None
    key: str = ""
    owner: str = "user"
    session_id: str = DEFAULT_SESSION


class PatchIn(BaseModel):
    """模型路径级增量（M3b）。``ops`` = [{path, value}]，路径点分、下标即段。"""
    ops: list = []
    key: str = ""
    owner: str = "user"
    session_id: str = DEFAULT_SESSION


class ReplaceIn(BaseModel):
    """整份模型替换（导入/搬运）。"""
    model: dict = {}
    key: str = ""
    owner: str = "user"
    session_id: str = DEFAULT_SESSION


class AttachIn(BaseModel):
    key: str = ""
    url: str = ""      # 旧 wire 兼容：后端归会话 env，此字段忽略
    owner: str = "user"
    session_id: str = DEFAULT_SESSION


def _kernel(body) -> object:
    """写端点前置正典（G112 收敛 2026-10-27）：取/建 kernel + 可选 attach。

    「带 key 才换绑」是写路径（exec/run/patch/replace）的口径；``/attach``
    是换绑的正名端点，**无条件** attach（含空 key）、要回执，不走本
    helper——两种口径别再合回去。attach 幂等：同 key 不动模型，换 key
    只换凭据（O2）。
    """
    k = get_kernel(body.owner, body.session_id)
    if body.key:
        k.attach(body.key)
    return k


@app.post("/exec")
def exec_(body: ExecIn) -> dict:
    if not body.code.strip():
        raise HTTPException(status_code=422, detail="code 必填")
    mode = "build" if body.mode == "build" else "run"
    eid = _kernel(body).submit(body.code, mode=mode, label=body.label)
    return {"id": eid}


@app.post("/run")
def run_(body: RunIn) -> dict:
    """提交当前模型（**不改模型**）。GUI 的 Run 按钮走这里。"""
    eid = _kernel(body).run(gpu=body.gpu, timeout_s=body.timeout_s)
    return {"id": eid}


@app.post("/model/patch")
def model_patch(body: PatchIn) -> dict:
    """模型路径级增量——表单/树/工具条的一次改动（M3b）。"""
    if not body.ops:
        raise HTTPException(status_code=422, detail="ops 不能为空")
    return {"id": _kernel(body).patch(body.ops)}


@app.post("/model/replace")
def model_replace(body: ReplaceIn) -> dict:
    """整份模型替换（导入 .gensim / 搬运）。"""
    if not isinstance(body.model, dict) or not body.model:
        raise HTTPException(status_code=422, detail="model 须为非空 dict")
    return {"id": _kernel(body).replace(body.model)}


@app.get("/output")
def output(since: int = 0, owner: str = "user",
           session_id: str = DEFAULT_SESSION) -> dict:
    """读取增量 + 快照。**读即唤起**（见 SessionKernel.ensure docstring）：
    Studio 重启后已开页面只靠这条轮询就能恢复，不必手动刷新。"""
    k = get_kernel(owner, session_id)
    k.ensure()
    return k.output(since)


@app.post("/stop")
def stop(owner: str = "user", session_id: str = DEFAULT_SESSION) -> dict:
    """停止当前执行（A2「长任务必须能掐」）：SIGINT 掐 exec（空闲
    忽略）——worker 的 ⏹/清理输出走既有环，done 收尾照发。"""
    return {"stopped": get_kernel(owner, session_id).stop()}


@app.post("/reset")
def session_reset(owner: str = "user",
                  session_id: str = DEFAULT_SESSION) -> dict:
    """清理并重启会话进程（R31 站长裁：stop 治不了时的进程级兜底——
    死循环卡 exec、脚本污染宿主，CTRL_BREAK 投递不进就杀进程重起）。
    **只清执行态**：在途 op 作废、新进程宿主干净；模型状态与终端
    输出历史保留（worker 重放 session_state.json / 环落盘）。"""
    return get_kernel(owner, session_id).reset()


# ── 凭据换绑（O2 核心）：换 key 不换模型 ─────────────────────────
@app.post("/attach")
def attach(body: AttachIn) -> dict:
    k = get_kernel(body.owner, body.session_id)
    return k.attach(body.key)


# ── 结果读取（G13②：结果 = 会话状态，从 owner 工作区读 h5）──────
# 纯函数逻辑 = results_view（可单测）；此处只做 owner 白名单与 404。
# 结果按 session 分目录（genie_results/<session_id>/）：两个模型的结果不混。

@app.get("/result/meta")
def result_meta(job_id: str = "", owner: str = "user",
                session_id: str = DEFAULT_SESSION) -> dict:
    """结果结构清单。job_id 缺省 = 最近一次（页面重开后无事件时的
    回退数据源）。"""
    from pathlib import Path
    from genie.studio import results_view
    owner = check_owner(owner)   # R25 确凿 5：修前 ../ 跨目录读他人 h5
    d = Path(workspace_root()) / owner
    try:
        p = (results_view.result_path(d, job_id, session_id) if job_id
             else results_view.latest_result(d, session_id))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if p is None or not p.is_file():
        raise HTTPException(status_code=404,
                            detail="尚无结果——先完成一次 Run")
    return results_view.meta(p)


@app.get("/result/series")
def result_series(job_id: str, mon: str, field: str,
                  maxpoints: int = 1200, owner: str = "user",
                  session_id: str = DEFAULT_SESSION,
                  axes: str = "", slices: str = "") -> dict:
    """1D 曲线（缺省峰值列折叠）或选轴 1D/2D 视图（G148 批 2：
    axes="x,y" / slices="z:1,time:0"）。"""
    from pathlib import Path
    from genie.studio import results_view
    owner = check_owner(owner)   # R25 确凿 5：修前 ../ 跨目录读他人 h5
    try:
        p = results_view.result_path(Path(workspace_root()) / owner,
                                     job_id, session_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not p.is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    try:
        view = results_view.parse_view(axes, slices)
        return results_view.series(p, mon, field, maxpoints,
                                   axes=(view or {}).get("axes"),
                                   slices=(view or {}).get("slices"))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/result/inject")
async def result_inject(request: Request, owner: str = "user",
                        session_id: str = DEFAULT_SESSION,
                        job_id: str = "", mon: str = "",
                        field: str = "") -> dict:
    """外部结果注入（G148 批 4b 腿 B）：body = results h5 字节 →
    落会话工作区结果目录 + ``plot`` 事件入输出环（GUI Plot 面板
    自动开卡）。为什么上传字节：外部 SDK 作业的落盘在**用户机器**
    （``_auto_save_result``），会话工作区读不到——推图即推数。"""
    from pathlib import Path
    from genie.studio import results_view
    # R25 确凿 5：修前 owner 先拼路径写盘、事后才在 get_kernel 里
    # 校验（写已经发生）——校验必须先于任何文件系统动作。
    owner = check_owner(owner)
    if not job_id or not mon:
        raise HTTPException(status_code=422, detail="job_id/mon 必填")
    try:
        p = results_view.result_path(Path(workspace_root()) / owner,
                                     job_id, session_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422,
                            detail="body = 结果 h5 字节，不能为空")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    os.chmod(p, 0o600)
    get_kernel(owner, session_id).emit_external(
        "plot", {"job_id": job_id, "mon": mon, "field": field or None})
    return {"ok": True, "job_id": job_id}


# ── 唯一网关出口 ────────────────────────────────────────────────
def _backend() -> str:
    """解析链：env GENIE_BACKEND（部署覆盖项）> 凭据仓 gateway > 本机缺省。

    （G63 收敛 2026-10-23：旧同义 env `GENIE_INTERNAL_BACKEND` 已删——
    三仓 grep 零消费者；同名 env 在服务仓指内核后端 :8001，语义不同。）
    （G179 收敛 2026-10-29：链身唯一真源 = kernel_session.backend()，
    本函数原为第二份手抄——改缺省地址要改两处、漂移无锁；相等锁 =
    tests/sdk/test_net_config.py::test_backend_chain_single_source。）
    """
    from .kernel_session import backend
    return backend()


# 白名单：会话只代拨这几族。别的路径一律 403（不做通用代理）。
# （api/health：Client.health() 经 /gw 走会话轨时需要。网关 tools 面
# 已随 G19b 撤除——/api/tools/* 是 Studio 纯本地实现，不经 /gw。）
_GW_ALLOW = ("whoami", "api/auth/", "api/jobs", "api/health",
             "api/config/")


# ── 会话代持凭据（G72 终稿：token 只住会话，脚本零凭据）────────────
# 进程内缓存，首用从凭据仓（~/.genie/credentials.json）载入；
# 登录截获（下方 _capture_login）即时更新内存 + 落盘。
# **仅本机单用户入口启用**（enable_cred_broker）：多用户云宿主上开它会
# 把"第一个登录者"的 token 变成全机共享兜底——跨用户串号，绝对不行。
_CRED: dict | None = None
_CRED_BROKER = False


def enable_cred_broker(on: bool = True) -> None:
    """启用凭据代持（``genie studio`` / console 等**本机**入口调）。

    网关直连登录（gateway_forward 的截获与空凭据代拨）只在开着时工作；
    云宿主（多用户共享一个进程）**永不启用**。
    """
    global _CRED_BROKER
    _CRED_BROKER = bool(on)


def _stored_cred() -> dict:
    """会话当前持有的凭据 {api_key, username}（无则空 dict）。"""
    global _CRED
    if _CRED is None:
        from genie.net.config import load_config
        cfg = load_config()
        _CRED = ({"api_key": cfg["api_key"], "username": cfg.get("username")}
                 if cfg.get("api_key") else {})
    return _CRED


def _capture_login(data: bytes, content: bytes) -> None:
    """登录成功的**单一截获点**：POST api/auth/login 200 → token 入仓。

    GUI 登录闸与任何经 /gw 的登录都从这儿过（gateway_forward 是唯一咽喉），
    于是"人在哪个面输密码"对凭据仓透明。
    """
    global _CRED
    try:
        body = json.loads(data or b"{}")
        resp = json.loads(content)
        token = resp.get("token") or resp.get("api_key")
        if not token:
            return
        username = str(body.get("username") or body.get("name") or "")
        from genie.net.config import save_config
        save_config(api_key=token, username=username or None)
        _CRED = {"api_key": token, "username": username}
    except Exception:   # noqa: BLE001 —— 截获失败绝不拦登录本身
        pass


def _gw_allowed(path: str) -> bool:
    p = path.lstrip("/")
    return any(p == a or p.startswith(a) for a in _GW_ALLOW)


def _upstream_down(e: Exception) -> HTTPException:
    """上游不可达的**唯一**措辞（原 server.py 里那份搬来这里）。

    requests 的原始异常是 "Max retries exceeded with url: /api/auth/login
    (Caused by NewConnectionError...)" —— 用户只看到 "max retry"，完全
    不知道是"服务端没跑"。这里翻成人话，原始原因留在括号里。"""
    import requests as _rq
    conn = (_rq.exceptions.ConnectionError, _rq.exceptions.Timeout,
            ConnectionError, TimeoutError)
    if isinstance(e, conn):
        cause = str(e).split("(Caused by")[0][:120].strip()
        return HTTPException(
            status_code=502,
            detail=f"上游不可达：{_backend()} 没有响应。"
                   "本机网关没启动？或用 GENIE_BACKEND=<服务地址> 指过去。"
                   f"（{cause}）")
    return HTTPException(status_code=502,
                         detail=f"上游不可达({_backend()}): {e}")


def gateway_forward(method: str, path: str, *, key: str = "",
                    data: bytes | None = None,
                    content_type: str = "application/json",
                    params=None, timeout: float = 120.0):
    """代拨后端一次（**唯一实现**）→ (status, bytes, content_type)。

    HTTP 路由（`/gw/{path}`）与同进程适配器（`LocalSessionHost`）都调它
    ——所以"上游不可达"的翻译只有一处，无论调用方在不在本进程。

    凭据代持（G72，仅本机形态——``enable_cred_broker`` 开启时）：调用方
    没带 key 时用**会话持有的凭据**代拨——脚本经 /gw 的请求从头到尾不碰
    token。多用户宿主不开代持，行为与从前完全一致（无 key 即裸拨）。
    """
    if not _gw_allowed(path):
        raise HTTPException(status_code=403,
                            detail=f"会话不代拨该路径：/{path}")
    cred = (_stored_cred() if (_CRED_BROKER and not key) else {})
    headers = {}
    # 体存在才挂 Content-Type——此前本参数是**死参数**（收了从没挂上）：
    # 经 /gw 的 POST（裸 connect 脚本/console）到网关时无 CT，内核的
    # FastAPI Body(dict) 会把 body 读成字符串 → 422 dict_type（2026-10-27
    # D2 结构修法终验踩出；GET 无体不挂）。
    if data is not None:
        headers["Content-Type"] = content_type
    if key:
        headers["X-API-Key"] = key
    elif cred.get("api_key"):
        headers["X-API-Key"] = cred["api_key"]
        if cred.get("username"):
            headers["X-Username"] = cred["username"]
    import requests as _rq
    try:
        r = _rq.request(
            method, f"{_backend()}/{path.lstrip('/')}",
            data=data or None, params=params,
            headers=headers or None,
            timeout=float(timeout))
    except Exception as e:  # noqa: BLE001 — 唯一一次翻译
        raise _upstream_down(e) from e
    if (_CRED_BROKER and method.upper() == "POST"
            and path.lstrip("/") == "api/auth/login" and r.status_code == 200):
        _capture_login(data or b"", r.content)     # 登录截获（单一咽喉点）
    return r.status_code, r.content, r.headers.get("content-type", "")


@app.api_route("/gw/{path:path}", methods=["GET", "POST", "DELETE"])
async def gw(path: str, request: Request):
    """HTTP 形态的网关出口（跨进程客户端用：宿主、console、外部工具）。"""
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import Response

    body = await request.body()
    kwargs = dict(
        key=request.headers.get("X-Upstream-Key", ""),
        data=body,
        content_type=request.headers.get("content-type", "application/json"),
        params=dict(request.query_params),
        timeout=float(request.headers.get("X-Upstream-Timeout", "120")))
    status, content, ct = await run_in_threadpool(
        lambda: gateway_forward(request.method, path, **kwargs))
    return Response(content=content, status_code=status,
                    media_type=ct or "application/json")


@app.get("/health")
def health() -> dict:
    """服务自述（studio 的 /api/health 就投影这一份）。"""
    import genie
    return {"ok": True, "service": "genie-session", "upstream": _backend(),
            "sdk_version": genie.__version__,
            "sessions": len(_kernels)}


# ── 启动 ────────────────────────────────────────────────────────


def serve(port: int = 0, *, host: str = "127.0.0.1",
          token: str = ""):
    """后台线程起本服务 → (uvicorn.Server, 实际端口)。

    ``port=0`` 让 OS 分配（O1：不猜端口、不抢端口）。返回实际端口供调用方
    显式注入客户端——地址不再经环境变量泄漏进语义。
    """
    import time

    import uvicorn

    global _TOKEN
    if token:
        _TOKEN = token
    enable_cred_broker()        # serve() 只被本机入口调用（console/studio）
    workspace_root().mkdir(parents=True, exist_ok=True)
    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True,
                     name="genie-session").start()
    for _ in range(200):               # 等就绪（≤10s）
        if server.started:
            break
        time.sleep(0.05)
    else:                              # pragma: no cover —— 起不来就是致命的
        raise RuntimeError(f"会话服务启动失败（{host}:{port}）")
    socks = []
    for srv in (getattr(server, "servers", None) or []):
        socks.extend(getattr(srv, "sockets", None) or [])
    actual = socks[0].getsockname()[1] if socks else port
    return server, actual
