"""genie.studio.server — Studio 服务（静态 + 本地面 + 会话门面）。

架构（2026-10-22 M2/O1+O2 收敛后，一句话）：
**Studio 是"本机的一个网页门"——静态资源 + 本地磁盘 + 会话门面；它自己
不拨后端。** 网络出口只有一个：会话（见 ``session_service``）。

三条由此而来的规矩：

1. **地址是部署细节**：会话地址由进程主入口显式注入（``create_app(session=…)``），
   不经环境变量泄漏进产品语义；本地默认由 ``genie studio`` 自己起会话、
   自己注入，不探测端口、不复用旧进程（O1）。
2. **唯一网关出口**：``/api/auth/*``、``/api/jobs/*`` 经
   ``session.gateway()`` 转发（会话侧有路径白名单 + 唯一一次"上游不可达"
   翻译）。Studio 不再持有后端地址、不再自己发请求（O2）。
   ``/api/tools/*``（库清单/材料拟合）**不在转发面**——G19b 起纯本地
   实现（genie.core.material + 本地 scipy，见下方工具面端点）。
3. **代理面是可选的**：``mount_session=True`` 才挂"会话门面 + 网关门面"
   （本地 ``genie studio`` 与 Pyodide 网页端 ``studio/web.py`` 共用）；
   ``False`` = 纯静态壳。

脚本一律经会话执行面（/api/exec）驱动——G174 后**唯一执行引擎**
（无独立 preview 端点：G50 删路由，2026-10-28 R33 连本地零网络
引擎一并退役）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

import genie
from genie.models import LIBRARY_FIT_DEFAULTS

# 工作区根**唯一解析点**（M2）：此前 studio 侧另有一份 GENIE_SCRIPTS_DIR
# 解析，只设 GENIE_SESSION_CWD 时两处得出不同根目录——"文件面板看不到
# 脚本/结果"的根源。现在双方都调 kernel_session.workspace_root()。
from .kernel_session import workspace_root
from .session_client import (SessionClient, register_result_routes,
                                         register_session_routes)

_STUDIO = Path(__file__).resolve().parent / "static"
_TITLE = "genie-studio"
# 内容面（随包分发，不是 GUI 实现）：手册站点与示例脚本住在包根，
# 见 ARCHITECTURE §1「包结构」。static_dir 参数只覆盖 GUI 壳，不覆盖它们。
_PKG = Path(__file__).resolve().parents[1]
_MANUAL = _PKG / "manual"
_EXAMPLES = _PKG / "examples"
_MAX_SCRIPT = 256 * 1024
# B3/B4 安全两洞先钉（调研裁决）：① /api/scripts/save 原本无大小上限
# （_MAX_SCRIPT 只管 drafts）——save/上传文本类统一钉 256KiB（超限 413）；
# ② \w 裸用连非 ASCII 名都放行（re 无 re.ASCII，实测 "中文脚本.py" 通过）
# ——所有名字白名单统一 flags=re.ASCII。二进制/数据（multipart 流式）上
# 限 512MiB：工作区文件是用户仿真产物（savefig 图 / npz/h5 场数据），
# 够放一次真作业的输出，又不至于打满盘。
_MAX_BINARY = 512 * 1024 * 1024


class DraftRing:
    """环形脚本文本环（/api/drafts ↔ /__drafts 的服务端存储）。

    通道里只有脚本文本——模型由会话执行脚本派生（脚本是本体，cfg 不是
    同伴负载）。本地单环（进程级 ``_DEFAULT_RING``）。
    """

    def __init__(self, keep: int = 50):
        self._keep = keep
        self._items: list[dict] = []

    def push(self, label: str, script: str) -> dict:
        item = {"seq": time.time_ns(), "ts": time.time(),
                "label": label, "script": script}
        self._items.append(item)
        del self._items[:-self._keep]
        return item

    def since(self, seq: int) -> list[dict]:
        return [d for d in self._items if d["seq"] > seq]


_DEFAULT_RING = DraftRing()


def create_app(*, static_dir: str | Path | None = None,
               owner_fn=None,
               session: SessionClient | None = None,
               mount_session: bool = False,
               share_session: bool = False,
               on_attach=None,
               require_auth: bool = False) -> FastAPI:
    """Studio app 工厂。

    ``mount_session=True``：挂会话门面（/api/exec*、/api/session/*、
    /api/result/*）与网关门面（/api/auth/*、/api/jobs/*）——本地
    ``genie studio`` 与 Pyodide 网页端（``studio/web.py``）共用本工厂。

    ``session``：会话客户端（地址 + 端口口令）。缺省 ``SessionClient()``
    （读 env，跨进程形态用）；本地入口显式注入——地址不进产品语义。

    ``require_auth=True``（站长裁决 2026-10-21）＝**未登录什么都不给做**：
    validate / model / scripts / workspace / sim / exec 这些本地
    能力一律要求 `X-API-Key`（GUI 登录 token）。env ``GENIE_STUDIO_OPEN=1``
    可显式退回"本机免登录"形态（调试/纯本地离线场景）。
    """
    static = Path(static_dir) if static_dir else _STUDIO
    # B1 按人隔离：req → owner。缺省 = 本地形态固定 owner（M1 遗留：本地
    # 单用户，owner 不跟随登录账号；见 LEDGER）。
    from .session_client import resolve_owner
    owner_of = owner_fn or (lambda request: resolve_owner())
    key_of = req_key
    if session is not None:
        sess = session
    elif mount_session:
        raise ValueError(
            "create_app(mount_session=True) 需要显式 session：地址来自门牌"
            " discover() 或 LocalSessionHost（独立会话服务已退役，没有默认地址）")
    else:
        sess = None

    app = FastAPI(title=_TITLE, version=genie.__version__)

    @app.middleware("http")
    async def no_stale_static(request: Request, call_next):
        """阶段 5 修（用户实测踩坑）：静态资源无 Cache-Control 时浏览器
        走启发式缓存——部署新代码后旧 app.js/index.html 还能活数小时~
        数天，旧代码引用已删除的资源即整页挂死。no-cache = 每次回验
        ETag（未变仍 304，代价一次 RTT），改动即时可见。"""
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ── 登录闸（站长裁决 2026-10-21：未登录什么都不给做）──
    # G110 清账 2026-10-27：原黑名单式 _GATED_PREFIX 只列八族，漏了
    # /api/run、/api/model/*、/api/result/*——未登录可整份替换会话模型、
    # 以代持凭据提交 GPU 作业并读结果。黑名单的结构性缺陷 = 新增端点
    # 天然漏闸，反转成"默认全闸 + 显式放行"：放行只给健康检查与登录面
    # （不放行登录页自己都加载不出来）；静态资源不在 /api/ 下，不受影响。
    _OPEN_PREFIX = ("/api/health", "/api/auth/")

    @app.middleware("http")
    async def require_signin(request: Request, call_next):
        p = request.url.path
        gated = p.startswith("/api/") or p.startswith("/__drafts")
        if (require_auth and gated
                and not any(p == o or p.startswith(o) for o in _OPEN_PREFIX)):
            if not (request.headers.get("X-API-Key") or "").strip():
                return JSONResponse(
                    {"detail": "未登录——请先登录后再操作"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        """服务自述：上游地址**问会话**（Studio 不再持有后端配置）。"""
        out: dict = {"ok": True, "service": _TITLE,
                     "sdk_version": genie.__version__,
                     "auth_required": bool(require_auth),
                     "upstream": None,
                     "session": False}
        if sess is None:          # 纯静态角色的 app：没有会话门可报
            out["session"] = None
            return out
        try:
            h = sess.health()
            out["session"] = bool(h.get("ok"))
            out["upstream"] = h.get("upstream")
        except Exception:  # noqa: BLE001 — 会话没起也要能自述（GUI 靠它判断闸）
            pass
        return out

    # ══ validate：实时校验（唯一真源 = genie.models，阶段 5b）════════
    @app.post("/api/validate")
    async def api_validate(req: Request) -> dict:
        body = await req.json()
        cfg = body.get("cfg")
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=422, detail="cfg 须 dict")

        # 判定本体 = genie.models.resolve.validate_for_ui（全仓唯一实现；
        # 本地 Studio / 公共服务网关 / agent 工具共用）。
        from genie.models.resolve import validate_for_ui
        errors = await run_in_threadpool(validate_for_ui, cfg)
        return {"ok": not errors, "errors": errors}

    # ══ SDK → Studio 实时通道（进程级单环 _DEFAULT_RING）════════════
    @app.post("/api/drafts")
    async def push_draft(req: Request) -> dict:
        body = await req.json()
        label = str(body.get("label") or "sdk")[:64]
        script = body.get("script")
        if not isinstance(script, str) or not script.strip():
            raise HTTPException(status_code=422, detail="script 必须是非空文本")
        if len(script.encode("utf-8")) > _MAX_SCRIPT:
            raise HTTPException(status_code=413, detail="script 超 256KiB 上限")
        item = _DEFAULT_RING.push(label, script)
        return {"ok": True, "seq": item["seq"]}

    @app.get("/__drafts")
    def pull_drafts(req: Request, since: int = 0) -> list[dict]:
        return _DEFAULT_RING.since(since)

    # ══ 会话门面 + 网关门面（mount_session=True 时挂）═══════════════
    if mount_session:
        register_session_routes(app, owner_of=owner_of,
                                exec_credentials_of=key_of,
                                client=sess, on_attach=on_attach)
        register_result_routes(app, owner_of=owner_of, client=sess)

        async def _gw_fwd(req: Request, up: str, *, timeout: float) -> Response:
            """转发一次后端调用（体字节透传、状态码穿透）。

            **本函数是 Studio 里唯一碰网关的地方**——它不解析业务、不改
            写错误，唯一那一次"上游不可达"翻译在会话侧
            （session_service._upstream_down）。所以"后端没起"在全局只有
            一种措辞。
            """
            method = req.method
            body = await req.body() if method != "GET" else None
            ctype = req.headers.get("content-type", "application/json")
            key = key_of(req) or ""

            def _do():
                return sess.gateway(method, up, key=str(key), data=body,
                                    content_type=ctype,
                                    params=dict(req.query_params),
                                    timeout=timeout)

            status, content, ct = await run_in_threadpool(_do)
            return Response(content=content, status_code=status,
                            media_type=ct or "application/json")

        # 账号面：浏览器同源调本地，本地转发会话 → 网关（规避跨域）。
        # whoami 在网关上挂在根路径，其余在 /api/auth/* 下。
        @app.api_route("/api/auth/{path:path}", methods=["GET", "POST"])
        async def api_auth_fwd(path: str, req: Request) -> Response:
            up = "whoami" if path == "whoami" else f"api/auth/{path}"
            return await _gw_fwd(req, up, timeout=30.0)

        # 登出对称（G72 收尾）：GUI 登出时清**本机**凭据仓。纯本地动作，
        # 不打网关（远端吊销走 genie logout --token-id / 令牌管理页）。
        @app.post("/api/identity/clear")
        async def identity_clear() -> dict:
            from genie.net.config import clear_config
            from genie.studio import session_service as _ss
            clear_config("api_key", "username")
            _ss._CRED = {}
            return {"ok": True}

        # 工具面（库清单 / 材料拟合）——2026-10-27 起纯本地实现（G19b
        # 翻转：材质库回归 SDK，网关 tools 面随协议层手术撤除）。路径
        # 保持 /api/tools/* 不变（前端零改动）；拟合走本地 scipy（懒加载，
        # 缺失时报错指路 genie[fit]），重活进线程池不堵事件环。
        @app.get("/api/tools/nk_library")
        async def tools_nk_library(name: str | None = None) -> dict:
            from genie.core import material

            def _lib():
                if not name:
                    models = material.list_library_materials()
                    return {"models": models, "count": len(models)}
                # 库名未命中/非法是资源不存在：404 + load_library_nk 的
                # 指路文案（R38 修复：此前裸 ValueError 穿透成 500 纯文本，
                # 前端 r.json() 当场 SyntaxError）。
                try:
                    wl, n, k = material.load_library_nk(name)
                except ValueError as e:
                    raise HTTPException(404, detail=str(e))
                return {"name": name,
                        "wavelength_m": [float(x) for x in wl],
                        "n": [float(x) for x in n],
                        "k": [float(x) for x in k]}
            return await run_in_threadpool(_lib)

        @app.post("/api/tools/material_fit")
        async def tools_material_fit(req: Request) -> dict:
            body = await req.json()

            def _int_param(key):
                # 整型参数契约（R38）：非整数（含 2.5 静默截断、null、
                # 字符串）给干净 422，而不是 int() 的 ValueError/TypeError
                # 裸穿 500。
                v = body.get(key, LIBRARY_FIT_DEFAULTS[key])
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise HTTPException(
                        422, detail=f"{key} must be an integer, got {v!r}")
                if isinstance(v, float):
                    if not v.is_integer():
                        raise HTTPException(
                            422, detail=f"{key} must be an integer, got {v!r}")
                    v = int(v)
                return int(v)

            def _range_param():
                # fit_range 契约（R38）：None = 缺省（正典）；给了就必须是
                # 两个有限数（此前 `or 默认` + tuple() 会把非 2 元裸穿
                # 500；0 值合法但 lo<hi 由 fit_nk 再判）。
                v = body.get("fit_range")
                if v is None:
                    return LIBRARY_FIT_DEFAULTS["fit_range"]
                if (not isinstance(v, (list, tuple)) or len(v) != 2
                        or not all(isinstance(x, (int, float))
                                   and not isinstance(x, bool) for x in v)):
                    raise HTTPException(
                        422, detail="fit_range must be [lo, hi] in meters")
                return (float(v[0]), float(v[1]))

            def _fit():
                from genie.core.material import fit_nk, load_library_nk
                lib = body.get("library")
                nk = body.get("nk")
                if (lib is None) == (nk is None):
                    raise HTTPException(422, detail="library 与 nk 恰给其一")
                if lib is not None:
                    try:
                        data = load_library_nk(lib)
                    except ValueError as e:
                        raise HTTPException(422, detail=str(e))
                else:
                    # nk 契约 = {wavelength_m, n, k} 等长三列；形状错给
                    # 干净 422，而不是裸 AttributeError 的 500。
                    nk = nk if isinstance(nk, dict) else {}
                    cols = [nk.get(c) for c in ("wavelength_m", "n", "k")]
                    if (not all(isinstance(c, list) for c in cols)
                            or len(set(map(len, cols))) != 1):
                        raise HTTPException(
                            422, detail="nk 须 {wavelength_m, n, k} 且三列等长")
                    data = tuple(cols)
                name = body.get("name")
                if name is not None and not isinstance(name, str):
                    raise HTTPException(422, detail="name must be a string")
                try:
                    return fit_nk(
                        data,
                        fit_range=_range_param(),
                        n_drude=_int_param("n_drude"),
                        n_lorentz=_int_param("n_lorentz"),
                        maxfev=_int_param("maxfev"),
                        name=name)
                except ValueError as e:
                    # fit_nk 的全部输入契约错（含窗内非有限值，R38 G86）
                    # 都带可操作文案——422 透传，不许 500。
                    raise HTTPException(422, detail=str(e))
            return await run_in_threadpool(_fit)

        # 作业面（状态 / 取消 / 摘要 / 日志 / 结果元数据）
        @app.api_route("/api/jobs/{path:path}", methods=["GET", "POST"])
        async def api_jobs_fwd(path: str, req: Request) -> Response:
            return await _gw_fwd(req, f"api/jobs/{path}", timeout=120.0)

    # ── 会话端口（只给**跨进程客户端**用：genie console / 外部工具）──
    # 同进程的 GUI 走上面的门面直接调对象（零 HTTP）；这里挂同一套会话
    # 路由，带本机口令（session_service.set_token 由入口设置）。于是
    # "一个进程、一个端口"：GUI 的 8777 同时也是 console 的会话入口。
    if share_session:
        from genie.studio import session_service as _session_service
        app.mount("/session", _session_service.app)

    # ── 脚本工作区 ────────────────────────────────────────────────
    # 脚本落在 workspace_root()/<owner>/；认证交本地信任面；owner 用作
    # 目录名，白名单 = session_client.check_owner。
    import re as _re

    from genie.studio.session_client import check_owner

    _scripts_dir = workspace_root()
    _scripts_dir.mkdir(parents=True, exist_ok=True)

    def _owner_scripts_dir(req: Request) -> Path:
        """本请求的 owner 脚本目录 = 根/<owner>/（懒落位 0700；白名单
        = session_client.check_owner 全仓唯一一份——owner 用作目录名，
        路径穿越/非 ASCII 一律拒）。"""
        owner = str(owner_of(req) or "local")
        check_owner(owner)
        d = _scripts_dir / owner
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)   # mkdir 的 mode 受 umask 影响，落位后显式收紧
        return d

    # B3 工作区放宽面（调研裁决）：基名 [\w\-]{1,64}（re.ASCII）+ 扩展名
    # 白名单。scripts 三端点与工作区端点族共用同一守卫——名字白名单防
    # 路径穿越的口径全仓只有这一份。
    _WORKSPACE_EXTS = ("py", "png", "jpg", "jpeg", "npz", "csv", "h5",
                       "json", "txt")
    _IMAGE_EXTS = ("png", "jpg", "jpeg")
    # 文本类走 256KiB（同 save 通道）；二进制/数据类 512MiB（multipart
    # 流式）。csv 归数据类（表数据可以很大，下载口径与 npz/h5 同）。
    _TEXT_EXTS = ("py", "json", "txt")

    def _workspace_file(owner_dir: Path, name: str,
                        exts: tuple[str, ...] = _WORKSPACE_EXTS,
                        detail: str | None = None) -> Path:
        """共享名守卫 + 路径拼装：基名+扩展名白名单（fullmatch，路径
        穿越/子目录/隐藏文件一律 422）→ owner_dir/name。"""
        pat = r"[\w\-]{1,64}\.(?:%s)" % "|".join(exts)
        if not _re.fullmatch(pat, str(name or ""), _re.ASCII):
            raise HTTPException(status_code=422, detail=detail or (
                "文件名不合规：基名限字母/数字/_/-（1-64 字符），"
                "扩展名限 " + "/".join("." + e for e in exts)))
        return owner_dir / name

    def _workspace_kind(name: str) -> str:
        """kind 分类（files 列表用）：script/image/data/other。"""
        ext = Path(name).suffix.lower().lstrip(".")
        if ext == "py":
            return "script"
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in ("npz", "csv", "h5"):
            return "data"
        return "other"

    _RAW_MEDIA = {".py": "text/x-python", ".png": "image/png",
                  ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".npz": "application/octet-stream", ".csv": "text/csv",
                  ".h5": "application/x-hdf5",
                  ".json": "application/json", ".txt": "text/plain"}

    @app.get("/api/scripts/read")
    async def scripts_read(req: Request, name: str) -> dict:
        p = _workspace_file(_owner_scripts_dir(req), name, ("py",),
                            detail="文件名须为 xxx.py（字母/数字/_/-）")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="脚本不存在")
        return {"name": name, "text": p.read_text(encoding="utf-8")}

    # ── 工作区文件面板（B3/B4）：脚本 + savefig 图片 + 数据文件统一列、
    # 看、传、下。与 /api/scripts 同款认证；owner 一律 owner_fn(req)。
    # 只列根一层不递归（目录树/配额明确越界不做）。.py 删除也放行——
    # 产品裁决：工作区文件用户全权。
    @app.get("/api/workspace/files")
    async def workspace_files(req: Request) -> dict:
        files = []
        for p in sorted(_owner_scripts_dir(req).iterdir(), key=lambda x: x.name):
            if not p.is_file() or p.name.startswith(".") \
                    or p.name.endswith(".part"):   # 上传半截临时件不见客
                continue
            files.append({"name": p.name, "size": p.stat().st_size,
                          "kind": _workspace_kind(p.name)})
        return {"files": files}

    @app.get("/api/workspace/raw")
    def workspace_raw(req: Request, name: str) -> FileResponse:
        """文件取用：图片 media_type 内联预览；其余（含 .py——编辑走
        /api/scripts）Content-Disposition attachment 只下载不预览。"""
        p = _workspace_file(_owner_scripts_dir(req), name)
        if not p.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        media = _RAW_MEDIA.get(p.suffix.lower(), "application/octet-stream")
        if p.suffix.lower().lstrip(".") in _IMAGE_EXTS:
            return FileResponse(p, media_type=media)   # 不带 filename=内联
        return FileResponse(p, media_type=media, filename=p.name)

    @app.post("/api/workspace/upload")
    async def workspace_upload(req: Request, file: UploadFile,
                               name: str | None = None) -> dict:
        """B4 上传：multipart 流式落盘（1MiB 块，省内存），可选 name 覆盖
        文件名。超限 413；半途失败/超限不留 .part 半截。文本类 256KiB、
        二进制/数据 512MiB（与 save 通道同一白名单口径）。"""
        p = _workspace_file(_owner_scripts_dir(req),
                            name or file.filename or "")
        cap = _MAX_SCRIPT if p.suffix.lower().lstrip(".") in _TEXT_EXTS \
            else _MAX_BINARY
        tmp = p.with_name(p.name + ".part")
        size = 0
        try:
            with tmp.open("wb") as out:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(
                            status_code=413,
                            detail="文件超 %dMiB 上限" % (cap >> 20))
                    out.write(chunk)
            tmp.replace(p)
        finally:
            if tmp.exists():
                tmp.unlink()
        await file.close()
        return {"ok": True, "name": p.name, "size": size}

    @app.post("/api/workspace/delete")
    async def workspace_delete(req: Request) -> dict:
        """删除工作区文件（.py 也可删，删前无脑执行——产品裁决：工作区
        文件用户全权；GUI 层自行 confirm）。"""
        body = await req.json()
        p = _workspace_file(_owner_scripts_dir(req), str(body.get("name") or ""))
        if not p.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        p.unlink()
        return {"ok": True}

    # ── 仿真文件 .gensim（2026-10 站长裁决转正）─────────────────────
    # zip 容器 = 根级 simconfig.json + workspace/<工作区文件>；G148 起
    # 结果态仿真**自动带结果**（results/<job>_results.h5 + state.json，
    # 站长裁"有结果就自动带"）——导入端据此还原结果态（按钮 =
    # Switch to setting）。存储 = POST cfg → 服务端流式打包（大 h5 不过
    # 浏览器内存）；打开 = 上传 zip → 先验 cfg（genie.models 同一双手）
    # 再解包落回工作区（同款名守卫/上限）。会话 f 仍是唯一真源——
    # .gensim 只是它的可携快照，不构成第二持久化中心。
    @app.post("/api/sim/export")
    async def sim_export(req: Request) -> FileResponse:
        body = await req.json()
        cfg = body.get("cfg")
        if not isinstance(cfg, dict) or not cfg:
            raise HTTPException(status_code=422,
                                detail="还没有可存储的配置——先运行脚本或在表单里搭好配置")
        ws = _owner_scripts_dir(req)
        job_id = str(body.get("job_id") or "")
        result_p = None
        state = None
        if job_id:
            from genie.studio import results_view
            from .kernel_session import workspace_root
            from .session_client import DEFAULT_SESSION
            sid = req.headers.get("X-Session-Id") or DEFAULT_SESSION
            try:
                rp = results_view.result_path(workspace_root() / owner_of(req),
                                              job_id, sid)
            except ValueError:
                rp = None
            if rp is not None and rp.is_file():
                result_p = rp
                st = results_view.meta(rp)
                state = {"job_id": st["job_id"], "state": "done",
                         "executed_steps": st["executed_steps"],
                         "dt": st["dt"], "grid_shape": st["grid_shape"]}

        def _pack(tmp_path: str) -> None:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
                zf.writestr("simconfig.json",
                            json.dumps(cfg, ensure_ascii=False, indent=2))
                for p in sorted(ws.iterdir(), key=lambda x: x.name):
                    if p.is_file() and not p.name.startswith(".") \
                            and not p.name.endswith(".part"):
                        zf.write(p, arcname="workspace/" + p.name)
                if result_p is not None:
                    zf.write(result_p, arcname="results/" + result_p.name)
                    zf.writestr("state.json", json.dumps(
                        state, ensure_ascii=False, indent=2))

        tmp = tempfile.NamedTemporaryFile(suffix=".gensim", delete=False)
        tmp.close()
        try:
            await run_in_threadpool(_pack, tmp.name)
        except BaseException:
            os.unlink(tmp.name)
            raise
        return FileResponse(tmp.name, media_type="application/zip",
                            filename="genie_sim.gensim",
                            background=BackgroundTask(os.unlink, tmp.name))

    @app.post("/api/sim/import")
    async def sim_import(req: Request, file: UploadFile) -> dict:
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()                        # 只借路径——写出重开（同 export 口径）
        size = 0
        cap = _MAX_BINARY + _MAX_SCRIPT   # 容器 ≈ 最大数据件 + 配置文本
        try:
            with open(tmp.name, "wb") as out:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(status_code=413,
                                            detail=".gensim 超 %dMiB 上限" % (cap >> 20))
                    out.write(chunk)
        finally:
            await file.close()
        try:
            try:
                zf = zipfile.ZipFile(tmp.name)
            except zipfile.BadZipFile:
                raise HTTPException(status_code=422,
                                    detail="不是合法的仿真文件（.gensim 应为 zip 容器）")
            with zf:
                names = zf.namelist()
                if "simconfig.json" not in names:
                    raise HTTPException(status_code=422,
                                        detail="仿真文件缺 simconfig.json（不是 .gensim 容器？）")
                try:
                    cfg = json.loads(zf.read("simconfig.json").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    raise HTTPException(status_code=422,
                                        detail="simconfig.json 不是合法 JSON（%s）" % e)
                if not isinstance(cfg, dict):
                    raise HTTPException(status_code=422,
                                        detail="simconfig.json 须是 SimConfig JSON 对象")

                # 先验 cfg（genie.models 同一双手——与 /api/validate 同源）
                from genie.models.resolve import validate_for_ui
                errors = await run_in_threadpool(validate_for_ui, cfg)
                if errors:
                    first = "；".join(
                        (e["loc"] + ": " if e["loc"] else "") + e["msg"]
                        for e in errors[:3])
                    raise HTTPException(status_code=422,
                                        detail="配置未通过校验：" + first)

                # 后解包：逐成员流式落盘（校验先行，失败零落盘）
                ws = _owner_scripts_dir(req)
                restored: list[str] = []
                result_job = None
                state_obj = None
                if "state.json" in names:
                    try:
                        state_obj = json.loads(
                            zf.read("state.json").decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as e:
                        raise HTTPException(
                            status_code=422,
                            detail="state.json 不是合法 JSON（%s）" % e)
                    result_job = str(state_obj.get("job_id") or "")
                res_names = [n for n in names if n.startswith("results/")]
                if res_names and not result_job:
                    raise HTTPException(
                        status_code=422,
                        detail="容器含 results/ 但缺 state.json（job_id）")
                from genie.studio import results_view
                from .kernel_session import workspace_root
                from .session_client import DEFAULT_SESSION
                sid = req.headers.get("X-Session-Id") or DEFAULT_SESSION
                for n in names:
                    if n == "simconfig.json" or n == "state.json" or not n:
                        continue
                    if n == "results/":
                        continue
                    if n.startswith("results/"):
                        # G148：结果 h5 回落会话结果目录（结果态还原的
                        # 数据源；job_id 白名单由 result_path 守卫）
                        if "/" in n[len("results/"):]:
                            raise HTTPException(
                                status_code=422,
                                detail="仿真文件含容器外成员（%s）——拒绝" % n)
                        base = n[len("results/"):]
                        try:
                            rp = results_view.result_path(
                                workspace_root() / owner_of(req),
                                result_job, sid)
                        except ValueError as e:
                            raise HTTPException(status_code=422, detail=str(e))
                        info = zf.getinfo(n)
                        if info.file_size > _MAX_BINARY:
                            raise HTTPException(
                                status_code=413,
                                detail="%s 超 %dMiB 上限"
                                       % (base, _MAX_BINARY >> 20))
                        rp.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(n) as src, \
                                rp.with_name(rp.name + ".part") \
                                .open("wb") as out:
                            while True:
                                chunk = src.read(1 << 20)
                                if not chunk:
                                    break
                                out.write(chunk)
                        rp.with_name(rp.name + ".part").replace(rp)
                        os.chmod(rp, 0o600)
                        continue
                    if not n.startswith("workspace/") or "/" in n[len("workspace/"):]:
                        raise HTTPException(status_code=422,
                                            detail="仿真文件含容器外成员（%s）——拒绝" % n)
                    base = n[len("workspace/"):]
                    p = _workspace_file(ws, base)   # 名守卫：基名白名单，违者 422
                    cap_f = _MAX_SCRIPT \
                        if p.suffix.lower().lstrip(".") in _TEXT_EXTS else _MAX_BINARY
                    info = zf.getinfo(n)
                    if info.file_size > cap_f:
                        raise HTTPException(status_code=413,
                                            detail="%s 超 %dMiB 上限"
                                                   % (base, cap_f >> 20))
                    with zf.open(n) as src, p.with_name(p.name + ".part") \
                            .open("wb") as out:
                        while True:
                            chunk = src.read(1 << 20)
                            if not chunk:
                                break
                            out.write(chunk)
                    p.with_name(p.name + ".part").replace(p)
                    restored.append(base)
                out_resp: dict = {"ok": True, "cfg": cfg, "restored": restored}
                if res_names:
                    out_resp["has_results"] = True
                    out_resp["job_id"] = result_job
                    out_resp["state"] = state_obj
                return out_resp
        finally:
            os.unlink(tmp.name)

    # ── 当前模型 → Python 脚本（G60 出口，2026-10-30 站长裁）──────
    @app.post("/api/sim/script")
    async def sim_script(req: Request) -> dict:
        """当前模型渲染成 Python 脚本文本（File → Export .py）。

        只读投影：入参 = 前端手里的当前模型 cfg（与 /api/sim/export 同源），
        渲染器 = `core/script.render`（golden 字节锁定的唯一一份）——参数
        保留为变量、表达式槽保留为表达式，生成的脚本可独立运行重建同一模型。
        **不碰会话、不写盘、不改编辑器**（脚本是脚本、仿真是仿真；这是
        模型→代码的一次性交接）。
        """
        body = await req.json()
        cfg = body.get("cfg")
        if not isinstance(cfg, dict) or not cfg:
            raise HTTPException(
                status_code=422,
                detail="还没有可导出的模型——先运行脚本或在表单里搭好配置")
        from genie.core.script import render
        try:
            text = render(cfg)
        except Exception as e:  # noqa: BLE001 —— 渲染失败回 422（形状错）
            raise HTTPException(status_code=422,
                                detail=f"脚本渲染失败：{e}") from e
        return {"ok": True, "script": text}

    # ── 静态资产版本号（防缓存根治）：static 目录最大 mtime 作版本，
    # "/" 返回的 index.html 里所有 js/css 引用追加 ?v=——文件一改版本
    # 即变，浏览器必然拉新（no-cache 只保证回验，改不掉启发式缓存的
    # 旧模块存活窗口；实测用户被旧 boot.js 反复坑过）。
    def _asset_ver() -> str:
        mt = 0
        for f in static.rglob("*"):
            if f.is_file():
                try:
                    mt = max(mt, int(f.stat().st_mtime))
                except OSError:
                    pass
        return str(mt)

    _asset_version = _asset_ver()

    @app.get("/", include_in_schema=False)
    def studio_index() -> "Response":
        html = (static / "index.html").read_text(encoding="utf-8")
        html = _re.sub(r'(src|href)="/([^"]+\.(?:js|css))"',
                       rf'\1="/\2?v={_asset_version}"', html)
        return Response(html, media_type="text/html")

    app.mount("/vendor", StaticFiles(directory=static / "vendor"), name="vendor")

    # 内容面挂载：URL 命名空间与拆分前完全一致（/manual/…、/examples/…），
    # 所以 index.html 的手册入口、boot.js 的 /examples/manifest.json、手册
    # 内部的全相对链接都不用动。check_dir=False：裁剪安装（没装手册）时
    # 退化为 404，不阻断服务启动。
    app.mount("/manual", StaticFiles(directory=_MANUAL, check_dir=False),
              name="manual")
    app.mount("/examples", StaticFiles(directory=_EXAMPLES, check_dir=False),
              name="examples")

    @app.get("/{fname:path}")
    def studio_file(req: Request, fname: str) -> FileResponse:
        """Studio 其余静态文件（GUI 壳内的一切；手册/示例已各自挂载）。

        路径守卫：解析后必须仍落在 static/ 内（防 ../ 逃逸）。
        """
        if fname == "":
            return FileResponse(static / "index.html")
        p = (static / fname).resolve()
        if not str(p).startswith(str(static.resolve())) or not p.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(p)

    return app


def req_key(request: Request) -> str:
    """本请求的 GUI 凭据（X-API-Key 头，登录框产物）。"""
    return (request.headers.get("X-API-Key") or "").strip()
