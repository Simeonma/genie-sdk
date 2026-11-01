"""session_client — ``/session`` 面的客户端 + Studio 门面的路由注册（公共）。

（名字里的 "session" 指这个本地端口，不是某个实体；五名词见
``studio/session/MODMAP.md`` §0。）

事件契约（G13① 起正式化——所有脸按这张表说话，改动词 = 改这里）：
  环条目 {seq, text, kind, origin, ts[, mark][, data]}：
    kind ∈ input（输入 echo）| stdout | stderr | error | note（提示）
         | job（作业进度；data.phase="submit" = 新仿真提交 → 日志覆盖
           前一次）| job-done（作业完成，data 带 {job_id}）
         | job-progress（运行中步进，data={job_id,step,n_steps}，
           ≈5% 一跳；终端不落屏，只喂 GUI 进度条/ETA）
         | state（生命周期相位，data={phase[,job_id]}，phase ∈
           exec|submit|queue|run|download|idle；GUI 状态监测用）
    origin ∈ manager（管理器 / 父层）| worker（执行面）
  /output 响应 {lines, busy, cfg, resolved, cfg_seq, phase, job_id}：
    cfg/resolved = **worker 里那份模型**的快照（GUI 投影数据源）；cfg_seq 单调，
    客户端以 since 游标增量消费——重放安全（B2 seq 续接）；
    phase/job_id = 作业状态监测（GUI 进度条数据源）。

M2/O1+O2（2026-10-22）后的三条界：
  1. **地址 = 部署细节**：本模块不再从环境变量猜地址；进程主入口把
     ``SessionClient(url)`` 显式注入 Studio 工厂（``create_app(session=…)``）。
     环境变量只在跨进程客户端（console / 外部工具）里作为缺省值。
  2. **两条生命周期 + 一份模型**：工作区（一个进程一片目录，登录不参与）/
     token（能不能提交）互不决定；模型一个进程只有一份，刷新/重登都保留它，
     ``new_model()`` 才丢弃（请求面既没有 owner 也没有 session_id）。
  3. **唯一网关出口**：Studio 不再自己拨号后端。``gateway()`` 把
     /api/auth/*、/api/jobs/* 交给本端口转发（路径白名单在这里），于是
     "上游不可达"只有一种措辞、一个实现。
     /api/tools/*（库清单/材料拟合）自 G19b 起为 Studio 本地实现，
     不经此转发面（G111 清账 R38）。

本模块仍是**唯一一份**拨号/错误翻译实现：Studio 与跨进程客户端
都经它说话。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request



def session_url() -> str:
    """**没有默认端口地址**——独立会话服务（旧 :8123）已随 G63 退役。

    这个函数故意改成"响亮失败"：以前它硬编码 `127.0.0.1:8123`，而那个端口
    早就没人听，于是所有忘了注入地址的调用方都会静默去拨一个空端口（宿主
    `/api/exec` 因此 503 了很久才被发现）。现在缺地址就当场报错，指路 =
    门牌 `~/.genie/studio.json`（`SessionClient.discover()`）或显式 `base_url`。
    """
    raise RuntimeError(
        "没有 /session 地址：用 SessionClient.discover() 读门牌，或显式传 base_url"
        "（独立会话服务已退役，不再有 127.0.0.1:8123 这个默认值）")


class SessionClient:
    """``/session`` 面客户端（唯一一份）。

    ``token`` = 本地端口的访问口令（O1：端口带口令，写进
    rendezvous 文件；未配置 = 退回"本机信任面"形态）。
    """

    def __init__(self, base_url: str | None = None, *, token: str = "",
                 timeout: float = 8.0):
        self.base_url = (base_url or session_url()).rstrip("/")
        self.token = token or os.environ.get("GENIE_SESSION_TOKEN", "")
        self.timeout = timeout

    @classmethod
    def discover(cls) -> "SessionClient | None":
        """接上**正在跑的本地 Studio**（读门牌）；没门牌 → None。

        只给跨进程客户端用（``genie console`` / agent 脚本 / 第二个 Studio）。
        本机 Studio 自己持有 worker 对象、不走 HTTP（O1/O3），所以这条路上
        不存在"探测端口并复用"的旧毛病：门牌是 Studio 写的，只有一份。
        """
        from genie.studio.session import rendezvous as rv

        card = rv.read() or {}
        if not card.get("url"):
            return None
        return cls(card["url"], token=card.get("token", ""))

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
        except Exception as e:  # noqa: BLE001 — 端口不可达必须显性
            raise HTTPException(
                status_code=503,
                detail=f"本地端口不可达（{self.base_url}）：{e}") from e

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

    # ---- /session 面 ----

    def exec(self, code: str, *,
             mode: str = "run", label: str = "", key: str = "") -> dict:
        """``mode``：run=在当前命名空间执行（终端）；build=构造新模型（脚本）。"""
        return self._json("POST", "/exec", json_body={
            "code": code, "mode": mode, "label": label, "key": key})

    def run(self, *,
            gpu: int | None = None, timeout_s: int | None = None,
            key: str = "") -> dict:
        """提交当前模型（不改模型）。"""
        return self._json("POST", "/run", json_body={
            "gpu": gpu, "timeout_s": timeout_s, "key": key})

    def sweep(self, axes: list, *,
              max_parallel: int = 1, on_failure: str = "continue",
              name: str = "", key: str = "") -> dict:
        """跑一次参数扫描（扫参页 Run）——与 ``run`` 同一模式。"""
        return self._json("POST", "/sweep", json_body={
            "axes": list(axes or []), "max_parallel": max_parallel,
            "on_failure": on_failure, "name": name, "key": key})

    def sweep_plot(self, mon: str, *, field: str = "",
                   key: str = "") -> dict:
        """把最近一次扫描的结果推到 Plot 面板。"""
        return self._json("POST", "/sweep/plot", json_body={
            "mon": mon, "field": field, "key": key})

    def patch(self, ops: list, *,
              key: str = "") -> dict:
        """模型路径级增量：一条 op = ``{path, value, verb?}``。

        ``verb`` ∈ ``set``（缺省）/ ``append``（往数组尾加一个）/ ``remove``
        （删数组里的一个，path 指到元素）。**一批 op 全对或全错**——被拒时
        模型一个字节不动，worker 回声 ``origin=reject``。
        """
        return self._json("POST", "/model/patch", json_body={
            "ops": list(ops or []), "key": key})

    def replace(self, model: dict, *,
                key: str = "") -> dict:
        """整份模型替换。"""
        return self._json("POST", "/model/replace", json_body={
            "model": model, "key": key})

    def new_model(self) -> dict:
        """丢弃当前模型（2026-11-01：取代"换 session_id"）。"""
        return self._json("POST", "/model/new")

    def output(self, *, since: int = 0) -> dict:
        return self._json("GET", "/output", params={"since": since})

    def stop(self) -> dict:
        return self._json("POST", "/stop")

    def reset(self) -> dict:
        """重启 worker（R31：stop 治不了时的进程级兜底）。"""
        return self._json("POST", "/reset")

    def attach(self, *, key: str = "") -> dict:
        """凭据换绑（幂等）：**换 key 不换模型**。首次调用即建空模型。"""
        return self._json("POST", "/attach", json_body={"key": key})

    def result_meta(self, *, job_id: str) -> dict:
        """结果结构清单。**job_id 必填**（G58/G66 定案 2026-10-29：
        服务端不猜"最近一次"）。"""
        return self._json("GET", "/result/meta", params={
            "job_id": job_id})

    def result_series(self, *, job_id: str, mon: str,
                      field: str,
                      maxpoints: int = 1200,
                      axes: str = "", slices: str = "") -> dict:
        return self._json("GET", "/result/series", params={
            "job_id": job_id, "mon": mon, "field": field,
            "maxpoints": maxpoints,
            "axes": axes, "slices": slices})

    def result_inject(self, *,
                      job_id: str, mon: str, field: str = "",
                      data: bytes) -> dict:
        """外部结果注入（G148 批 4b 腿 B）：h5 字节上传 + plot 事件
        （外部 SDK 的 f.visualize 走这里——直连本地端口，不经 studio）。"""
        return self._json("POST", "/result/inject", data=bytes(data),
                          content_type="application/octet-stream", params={
                              "job_id": job_id, "mon": mon, "field": field})

    def health(self) -> dict:
        return self._json("GET", "/health")

    # ---- 网关面（唯一出口）----

    def gateway(self, method: str, path: str, *, key: str = "", data=None,
                content_type: str = "application/json", params=None,
                timeout: float = 120.0):
        """经本地端口转发一次后端调用 → (status, bytes, content_type)。

        非 2xx **原样返回**（调用方决定措辞）——本端已完成唯一一次
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


def register_session_routes(
        app: FastAPI, *, exec_credentials_of=None,
        client: SessionClient | None = None) -> None:
    """注册 /api/exec 四端点 + /api/session/attach + /api/model/new。

    请求面只有"要做什么"，没有身份字段：工作区由进程决定（无 owner），
    模型也只有一份（无 session_id）。**新模型 = POST /api/model/new**。

    鉴权交调用方的登录闸/本地信任面——本函数不做认证。
    须在静态兜底路由之前调用（GET 路由落在兜底之后会被吞成 404，A1 教训）。
    """
    from fastapi.concurrency import run_in_threadpool

    cli = client or SessionClient()

    @app.post("/api/exec")
    async def api_exec(req: Request) -> dict:
        """Terminal/脚本运行：整段代码在 worker 里执行（真 SDK +
        绑定 key + 上游 backend）。表单字段级修改走
        /api/exec/load_config 推进同一份模型。

        ``mode``：run=在当前命名空间执行（终端）；build=构造新模型（脚本，
        失败零副作用；``reset`` 保留为旧名）。
        """
        body = await req.json()
        code = str(body.get("code") or "")
        if not code.strip():
            raise HTTPException(status_code=422, detail="code 必填")
        label = str(body.get("label") or "")   # 执行来源标注（环 input.mark）
        # mode 只解析旧名别名（reset → build），**归一在正典**
        # （session_service.*_action，G115 收拢 2026-10-29）——本转发层
        # 不再抄第二份 "build if ... else run"。
        mode = "build" if body.get("reset") else str(body.get("mode") or "run")
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.exec, code,
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
            cli.run,
            gpu=body.get("gpu"), timeout_s=body.get("timeout_s"), key=key)
        return {"id": out["id"]}

    @app.post("/api/sweep")
    async def api_sweep(req: Request) -> dict:
        """跑一次参数扫描（扫参页 Run）：worker 展开 + 逐点 run + 等终态。"""
        body = await req.json()
        axes = body.get("axes")
        if not isinstance(axes, list) or not axes:
            raise HTTPException(status_code=422,
                                detail="axes 须非空列表（每条轴 "
                                       "{name, values|linspace|logspace}）")
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.sweep, axes,
            max_parallel=int(body.get("max_parallel") or 1),
            on_failure=str(body.get("on_failure") or "continue"),
            name=str(body.get("name") or ""), key=key)
        return {"id": out["id"]}

    @app.post("/api/sweep/plot")
    async def api_sweep_plot(req: Request) -> dict:
        """把最近一次扫描的结果推到 Plot 面板（与单跑 visualize 同一路）。"""
        body = await req.json()
        mon = str(body.get("mon") or "")
        if not mon.strip():
            raise HTTPException(status_code=422, detail="mon 必填")
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(
            cli.sweep_plot, mon, field=str(body.get("field") or ""),
            key=key)
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
            cli.patch, ops,
            key=key)
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
            cli.replace, model,
            key=key)
        return {"id": out["id"]}

    async def _attach(req: Request) -> dict:
        """凭据换绑 + 首次物化（幂等）。

        **换 key 不换模型**：token 轮换/改密/过期后重登，模型原样保留；
        要新模型走 ``POST /api/model/new``。未登录（取不到 key）时不建模型
        ——没凭据不建模型。
        """
        key = _key_of(exec_credentials_of, req)
        out = await run_in_threadpool(cli.attach, key=key)
        return {"ok": True, "attached": out}

    @app.post("/api/session/attach")
    async def api_session_attach(req: Request) -> dict:
        return await _attach(req)

    @app.post("/api/model/new")
    async def api_model_new() -> dict:
        """丢弃当前模型 → 下一个请求从空模型开始（GUI 退出/换账号时调）。"""
        return await run_in_threadpool(cli.new_model)

    @app.get("/api/exec/output")
    async def api_exec_output(req: Request, since: int = 0) -> dict:
        """Terminal 输出增量拉取 + worker 忙闲 + cfg 快照回流。"""
        return await run_in_threadpool(
            cli.output, since=since)

    @app.post("/api/exec/stop")
    async def api_exec_stop(req: Request) -> dict:
        """停止当前执行（A2「长任务必须能掐」）：经 /stop 向
        worker 发 SIGINT（exec 掐断+孤儿作业清理；空闲忽略）。"""
        return await run_in_threadpool(
            cli.stop)

    @app.post("/api/exec/reset")
    async def api_exec_reset(req: Request) -> dict:
        """重启 worker（R31 站长裁：死循环/宿主污染停不掉时的
        进程级兜底——杀 worker 重起，只清执行态，模型与历史保留）。"""
        return await run_in_threadpool(
            cli.reset)


def register_result_routes(app, *,
                           client: SessionClient | None = None) -> None:
    """代理 /api/result/meta + /api/result/series → 本地端口（G13②：
    结果 = 模型状态，GUI 经自己的 origin 代理读取）。GET 路由，须在
    静态兜底之前注册（A1 教训）；鉴权交调用方守卫面。"""
    from fastapi import Request
    from fastapi.concurrency import run_in_threadpool

    cli = client or SessionClient()

    @app.get("/api/result/meta")
    async def api_result_meta(req: Request, job_id: str) -> dict:
        """结果结构清单。**job_id 必填**——服务端不猜"最近一次"
        （G58/G66 定案 2026-10-29）。"""
        return await run_in_threadpool(
            cli.result_meta,
            job_id=job_id)

    @app.get("/api/result/series")
    async def api_result_series(req: Request, job_id: str, mon: str,
                                field: str, maxpoints: int = 1200,
                                axes: str = "",
                                slices: str = "") -> dict:
        return await run_in_threadpool(
            cli.result_series,
            job_id=job_id,
            mon=mon, field=field, maxpoints=maxpoints,
            axes=axes, slices=slices)
