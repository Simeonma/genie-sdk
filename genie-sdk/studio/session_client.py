"""sdk/genie/studio/session_client — 会话服务的客户端与路由注册（公共）。

事件契约（G13① 起正式化——所有脸按这张表说话，改动词 = 改这里）：
  环条目 {seq, text, kind, origin, ts[, mark][, data]}：
    kind ∈ input（输入 echo）| stdout | stderr | error | note（提示）
         | job（作业进度；data.phase="submit" = 新仿真提交 → 日志覆盖
           前一次）| job-done（作业完成，data 带 {job_id}）
         | job-progress（运行中步进，data={job_id,step,n_steps}，
           ≈5% 一跳；终端不落屏，只喂 GUI 进度条/ETA）
         | state（生命周期相位，data={phase[,job_id]}，phase ∈
           exec|submit|queue|run|download|idle；GUI 状态监测用）
    origin ∈ manager（会话管理层）| worker（执行面）
  /output 响应 {lines, busy, cfg, resolved, cfg_seq, phase, job_id}：
    cfg/resolved = 会话 f 的快照（GUI 投影数据源）；cfg_seq 单调，
    客户端以 since 游标增量消费——重放安全（B2 seq 续接）；
    phase/job_id = 会话级作业状态监测（GUI 进度条数据源）。

M2/O1+O2（2026-10-22）后的三条界：
  1. **会话地址 = 部署细节**：本模块不再从环境变量猜地址；进程主入口把
     ``SessionClient(url)`` 显式注入 Studio 工厂（``create_app(session=…)``）。
     环境变量只在独立会话服务这种跨进程形态（console、外部工具）里作为缺省值。
  2. **三生命周期正交**：owner（隔离到哪个目录）/ token（能不能提交）/
     session_id（哪一份模型）互不决定对方。换 token 只走 ``attach``，
     **不换模型**；换 session_id = 新会话 = 新模型（由前端用户在登录时生成）。
  3. **唯一网关出口**：Studio 不再自己拨号后端。``gateway()`` 把
     /api/auth/*、/api/tools/*、/api/jobs/* 交给会话服务转发（会话侧有
     路径白名单），于是"上游不可达"只有一种措辞、一个实现。

本模块仍是**唯一一份**拨号/错误翻译/owner 白名单实现：host(:8843) 与
studio(:8777) 都经它说话。
"""
from __future__ import annotations

import os
import re

from fastapi import FastAPI, HTTPException, Request

# owner 白名单：同 script 名规则 [\w\-]{1,64}——owner 会用作工作目录名
# （workspace 根/<owner>/），路径穿越/空串/点号/非 ASCII 一律拒
# （re.ASCII：\w 裸用连中文都放行，B3 调研裁决②钉死）。
OWNER_RE = re.compile(r"[\w\-]{1,64}", re.ASCII)
# session_id 同规则（它也是目录名：session/<owner>/<session_id>/）
SESSION_ID_RE = re.compile(r"[\w\-]{1,64}", re.ASCII)

DEFAULT_SESSION = "default"


def check_owner(owner: str) -> str:
    """owner 白名单校验（全仓唯一一份；不合规 422）。"""
    if not OWNER_RE.fullmatch(owner or ""):
        raise HTTPException(status_code=422,
                            detail="owner 须为字母/数字/_/-（1-64 字符）")
    return owner


def check_session_id(session_id: str) -> str:
    """session_id 白名单校验（目录名；不合规 422）。"""
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(status_code=422,
                            detail="session_id 须为字母/数字/_/-（1-64 字符）")
    return session_id


def resolve_owner() -> str:
    """工作区 owner 的**唯一读取点**（env `GENIE_SCRIPTS_OWNER` 缺省 "local"）。

    G63 收敛（2026-10-23）：此前 __main__/console/server 三处各自读 env 各自
    兜底——同一事实三个地址。现在只有这里读，其余位置调本函数。
    """
    return os.environ.get("GENIE_SCRIPTS_OWNER") or "local"


def session_url() -> str:
    """**没有默认会话地址**——独立会话服务（旧 :8123）已随 G63 退役。

    这个函数故意改成"响亮失败"：以前它硬编码 `127.0.0.1:8123`，而那个端口
    早就没人听，于是所有忘了注入地址的调用方都会静默去拨一个空端口（宿主
    `/api/exec` 因此 503 了很久才被发现）。现在缺地址就当场报错，指路 =
    门牌 `~/.genie/studio.json`（`SessionClient.discover()`）或显式 `base_url`。
    """
    raise RuntimeError(
        "没有会话地址：用 SessionClient.discover() 读门牌，或显式传 base_url"
        "（独立会话服务已退役，不再有 127.0.0.1:8123 这个默认值）")


class SessionClient:
    """会话服务客户端（唯一一份）。

    ``token`` = 本机会话端口的访问口令（O1：会话端口带口令，写进
    rendezvous 文件；未配置 = 保持旧的"本机信任面"形态，云宿主不受影响）。
    """

    def __init__(self, base_url: str | None = None, *, token: str = "",
                 timeout: float = 8.0, owner: str = "",
                 session_id: str = ""):
        self.base_url = (base_url or session_url()).rstrip("/")
        self.token = token or os.environ.get("GENIE_SESSION_TOKEN", "")
        self.timeout = timeout
        # 缺省 owner/session_id：``discover()`` 会把门牌里的值填进来，于是
        # 跨进程调用方（console / agent 脚本）不必每次重抄这两个参数。
        self.owner = owner
        self.session_id = session_id

    @classmethod
    def discover(cls) -> "SessionClient | None":
        """接上**正在跑的本地 Studio**（读门牌）；没门牌 → None。

        只给跨进程客户端用（``genie console`` / agent 脚本 / 第二个 Studio）。
        本机 Studio 自己持有会话对象、不走 HTTP（O1/O3），所以这条路上
        不存在"探测端口并复用"的旧毛病：门牌是会话拥有者写的，只有一份。
        """
        from genie.studio import rendezvous as rv

        card = rv.read() or {}
        if not card.get("url"):
            return None
        return cls(card["url"], token=card.get("token", ""),
                   owner=card.get("owner", ""),
                   session_id=card.get("session_id", ""))

    def _owner(self, owner: str) -> str:
        o = owner or self.owner
        if not o:
            raise HTTPException(
                status_code=422,
                detail="owner 必填：门牌里还没有 owner——通常意味着界面还没"
                       "登录过（登录一次就会写进去），或该显式给出 owner")
        return o

    def _sid(self, session_id: str) -> str:
        return session_id or self.session_id or DEFAULT_SESSION

    # ---- 传输 ----

    def _headers(self) -> dict:
        return {"X-Session-Token": self.token} if self.token else {}

    def _request(self, method: str, path: str, *, json_body=None,
                 data=None, content_type: str = "", params=None, headers=None,
                 timeout=None):
        import requests
        hdr = dict(self._headers())
        if content_type:
            hdr["Content-Type"] = content_type
        if headers:
            hdr.update({k: v for k, v in headers.items() if v})
        try:
            return requests.request(
                method, self.base_url + path, json=json_body, data=data,
                params=params, headers=hdr or None,
                timeout=self.timeout if timeout is None else timeout)
        except Exception as e:  # noqa: BLE001 — 会话掉线必须显性
            raise HTTPException(
                status_code=503,
                detail=f"会话服务不可达（{self.base_url}）：{e}") from e

    def _json(self, method: str, path: str, *, json_body=None, params=None,
              data=None, content_type="", timeout=None) -> dict:
        r = self._request(method, path, json_body=json_body, data=data,
                          content_type=content_type, params=params,
                          timeout=timeout)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code,
                                detail=str(r.text)[:300])
        return r.json()

    def _raw(self, method: str, path: str, *, data=None, content_type="",
             params=None, headers=None, timeout=None):
        """原样返回 (status, bytes, content_type)——网关转发用（状态码穿透）。"""
        r = self._request(method, path, data=data, content_type=content_type,
                          params=params, headers=headers, timeout=timeout)
        return r.status_code, r.content, r.headers.get("Content-Type", "")

    # ---- 会话面 ----

    def exec(self, code: str, *, owner: str = "", session_id: str = "",
             mode: str = "run", label: str = "", key: str = "") -> dict:
        """``mode``：run=在当前命名空间执行（终端）；build=构造新模型（脚本）。"""
        return self._json("POST", "/exec", json_body={
            "code": code, "mode": mode, "label": label,
            "owner": self._owner(owner), "session_id": self._sid(session_id),
            "key": key})

    def run(self, *, owner: str = "", session_id: str = "",
            gpu: int | None = None, timeout_s: int | None = None,
            key: str = "") -> dict:
        """提交当前模型（不改模型）。"""
        return self._json("POST", "/run", json_body={
            "gpu": gpu, "timeout_s": timeout_s, "key": key,
            "owner": self._owner(owner), "session_id": self._sid(session_id)})

    def patch(self, ops: list, *, owner: str = "",
              session_id: str = "", key: str = "") -> dict:
        """模型路径级增量：一条 op = ``{path, value, verb?}``。

        ``verb`` ∈ ``set``（缺省）/ ``append``（往数组尾加一个）/ ``remove``
        （删数组里的一个，path 指到元素）。**一批 op 全对或全错**——被拒时
        模型一个字节不动，会话回声 ``origin=reject``。
        """
        return self._json("POST", "/model/patch", json_body={
            "ops": list(ops or []), "key": key,
            "owner": self._owner(owner), "session_id": self._sid(session_id)})

    def replace(self, model: dict, *, owner: str = "",
                session_id: str = "", key: str = "") -> dict:
        """整份模型替换。"""
        return self._json("POST", "/model/replace", json_body={
            "model": model, "key": key,
            "owner": self._owner(owner), "session_id": self._sid(session_id)})

    def output(self, *, owner: str = "", session_id: str = "",
               since: int = 0) -> dict:
        return self._json("GET", "/output", params={
            "since": since, "owner": self._owner(owner),
            "session_id": self._sid(session_id)})

    def stop(self, *, owner: str = "", session_id: str = "") -> dict:
        return self._json("POST", "/stop", params={
            "owner": self._owner(owner), "session_id": self._sid(session_id)})

    def attach(self, *, owner: str = "", session_id: str = "",
               key: str = "") -> dict:
        """凭据换绑（幂等）：**换 key 不换模型**。首次调用即建空模型。"""
        return self._json("POST", "/attach", json_body={
            "key": key, "owner": self._owner(owner),
            "session_id": self._sid(session_id)})

    def result_meta(self, *, owner: str = "",
                    session_id: str = "", job_id: str = "") -> dict:
        return self._json("GET", "/result/meta", params={
            "job_id": job_id, "owner": self._owner(owner),
            "session_id": self._sid(session_id)})

    def result_series(self, *, owner: str = "", job_id: str, mon: str,
                      field: str, session_id: str = "",
                      maxpoints: int = 1200,
                      axes: str = "", slices: str = "") -> dict:
        return self._json("GET", "/result/series", params={
            "job_id": job_id, "mon": mon, "field": field,
            "maxpoints": maxpoints, "owner": self._owner(owner),
            "session_id": self._sid(session_id),
            "axes": axes, "slices": slices})

    def result_inject(self, *, owner: str = "", session_id: str = "",
                      job_id: str, mon: str, field: str = "",
                      data: bytes) -> dict:
        """外部结果注入（G148 批 4b 腿 B）：h5 字节上传 + plot 事件
        （外部 SDK 的 f.visualize 走这里——直连会话端口，不经 studio）。"""
        return self._json("POST", "/result/inject", data=bytes(data),
                          content_type="application/octet-stream", params={
                              "job_id": job_id, "mon": mon, "field": field,
                              "owner": self._owner(owner),
                              "session_id": self._sid(session_id)})

    def health(self) -> dict:
        return self._json("GET", "/health")

    # ---- 网关面（唯一出口）----

    def gateway(self, method: str, path: str, *, key: str = "", data=None,
                content_type: str = "application/json", params=None,
                timeout: float = 120.0):
        """经会话转发一次后端调用 → (status, bytes, content_type)。

        非 2xx **原样返回**（调用方决定措辞）——会话侧已完成唯一一次
        "上游不可达"翻译（见 session_service._upstream_down）。
        体走原始字节：JSON / 空体 / 将来的表单都不需要在这里分叉。
        """
        return self._raw(
            method, "/gw/" + path.lstrip("/"),
            data=data, content_type=content_type if data else "",
            params=params,
            headers={"X-Upstream-Key": key,
                     "X-Upstream-Timeout": str(int(timeout))},
            timeout=max(timeout, self.timeout) + 5.0)


# ---------------------------------------------------------------------------
# 路由注册（Studio 工厂用；GET 路由须在静态兜底之前注册——A1 教训）
# ---------------------------------------------------------------------------


def _key_of(exec_credentials_of, req: Request) -> str:
    """凭据取用：函数返回 key（X-API-Key 头的产物，见 server.req_key）。"""
    if exec_credentials_of is None:
        return ""
    return str(exec_credentials_of(req) or "")


def _sid_of(req: Request) -> str:
    """session_id 取用：读 X-Session-Id 头（缺省 = 单会话）。"""
    return check_session_id(req.headers.get("X-Session-Id")
                            or DEFAULT_SESSION)


def register_session_routes(
        app: FastAPI, *, owner_of, exec_credentials_of=None,
        client: SessionClient | None = None, on_attach=None) -> None:
    """注册 /api/exec 四端点 + /api/session/attach（会话转发）。

    ``on_attach(owner, session_id)``：attach 成功后回调（Studio 用它把
    "当前活跃会话"写进门牌，让 ``genie console`` 能接上同一个模型）。

    鉴权交调用方的会话守卫/本地信任面——本函数不做认证。
    须在静态兜底路由之前调用（GET 路由落在兜底之后会被吞成 404，A1 教训）。
    """
    from fastapi.concurrency import run_in_threadpool

    cli = client or SessionClient()

    @app.post("/api/exec")
    async def api_exec(req: Request) -> dict:
        """Terminal/脚本运行：整段代码在会话 kernel 里执行（真 SDK +
        绑定 key + 上游 backend）。表单字段级修改走
        /api/exec/load_config 推进同一份会话 f。

        ``mode``：run=在当前命名空间执行（终端）；build=构造新模型（脚本，
        失败零副作用；``reset`` 保留为旧名）。
        """
        body = await req.json()
        code = str(body.get("code") or "")
        if not code.strip():
            raise HTTPException(status_code=422, detail="code 必填")
        label = str(body.get("label") or "")   # 执行来源标注（环 input.mark）
        mode = "build" if (body.get("mode") == "build"
                           or body.get("reset")) else "run"
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.exec, code, owner=owner_of(req),
            session_id=_sid_of(req),
            mode=mode, label=label, key=key)
        return {"id": out["id"]}

    @app.post("/api/run")
    async def api_run(req: Request) -> dict:
        """提交**当前模型**（GUI 的 Run 按钮；不改模型）。"""
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001 — 空体 = 全用模型自己的设置
            body = {}
        if not isinstance(body, dict):
            body = {}
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.run, owner=owner_of(req),
            session_id=_sid_of(req),
            gpu=body.get("gpu"), timeout_s=body.get("timeout_s"), key=key)
        return {"id": out["id"]}

    @app.post("/api/model/patch")
    async def api_model_patch(req: Request) -> dict:
        """模型**路径级增量**（表单/树/工具条/agent 的一次改动）。

        一条 op = ``{path, value, verb?}``，verb ∈ set（缺省）/append/remove
        （remove 删数组元素或 dict 键——G60① 起 parameters 表删除走它）。
        客户端只发它改了什么——服务端是模型的唯一权威。一批 op 全对或全错；
        回声快照带 ``origin=patch``（已落地）或 ``origin=reject``（被拒）。
        """
        body = await req.json()
        ops = body.get("ops")
        if not isinstance(ops, list) or not ops:
            raise HTTPException(status_code=422, detail="ops 须非空列表")
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.patch, ops, owner=owner_of(req),
            session_id=_sid_of(req), key=key)
        return {"id": out["id"]}

    @app.post("/api/model/replace")
    async def api_model_replace(req: Request) -> dict:
        """整份模型替换（导入 .gensim 等搬运）。"""
        body = await req.json()
        model = body.get("model")
        if not isinstance(model, dict) or not model:
            raise HTTPException(status_code=422, detail="model 须为非空 dict")
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.replace, model, owner=owner_of(req),
            session_id=_sid_of(req), key=key)
        return {"id": out["id"]}

    async def _attach(req: Request) -> dict:
        """凭据换绑 + 首次物化（幂等）。

        O2 语义：**换 key 不换模型**。所以 token 轮换/改密/过期后重登，
        模型原样保留；要新模型走新的 session_id（前端在登录时生成）。
        未登录（取不到 key）时不建模型——没凭据的会话没有意义。
        """
        key = _key_of(exec_credentials_of, req)
        sid = _sid_of(req)
        out = await run_in_threadpool(cli.attach, owner=owner_of(req),
                                     session_id=sid, key=key)
        if on_attach is not None:
            try:
                on_attach(owner_of(req), sid)
            except Exception:  # noqa: BLE001 —— 记门牌失败不拦登录
                pass
        return {"ok": True, "session_id": sid, "attached": out}

    @app.post("/api/session/attach")
    async def api_session_attach(req: Request) -> dict:
        return await _attach(req)

    @app.get("/api/exec/output")
    async def api_exec_output(req: Request, since: int = 0) -> dict:
        """Terminal 输出增量拉取 + 会话忙闲 + cfg 快照回流。"""
        return await run_in_threadpool(
            cli.output, owner=owner_of(req),
            session_id=_sid_of(req), since=since)

    @app.post("/api/exec/stop")
    async def api_exec_stop(req: Request) -> dict:
        """停止当前执行（A2「长任务必须能掐」）：经会话 /stop 向
        worker 发 SIGINT（exec 掐断+孤儿作业清理；空闲忽略）。"""
        return await run_in_threadpool(
            cli.stop, owner=owner_of(req),
            session_id=_sid_of(req))


def register_result_routes(app, *, owner_of,
                           client: SessionClient | None = None) -> None:
    """代理 /api/result/meta + /api/result/series → 会话服务（G13②：
    结果 = 会话状态，GUI 经自己的 origin 代理读取）。GET 路由，须在
    静态兜底之前注册（A1 教训）；鉴权交调用方守卫面。"""
    from fastapi import Request
    from fastapi.concurrency import run_in_threadpool

    cli = client or SessionClient()

    @app.get("/api/result/meta")
    async def api_result_meta(req: Request, job_id: str = "") -> dict:
        """结果结构清单（job_id 缺省 = 最近一次——页面重开的回退）。"""
        return await run_in_threadpool(
            cli.result_meta, owner=owner_of(req),
            session_id=_sid_of(req), job_id=job_id)

    @app.get("/api/result/series")
    async def api_result_series(req: Request, job_id: str, mon: str,
                                field: str, maxpoints: int = 1200,
                                axes: str = "",
                                slices: str = "") -> dict:
        return await run_in_threadpool(
            cli.result_series, owner=owner_of(req),
            session_id=_sid_of(req), job_id=job_id,
            mon=mon, field=field, maxpoints=maxpoints,
            axes=axes, slices=slices)
