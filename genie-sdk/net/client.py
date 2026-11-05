"""genie.net.client — worker→host ``/gw`` 作业面（五方法）。

作业面五方法（net/MODMAP §1 / 禁令 6）：``submit / job_status /
job_statuses / cancel / stream_result``；每个请求带 worker token 头
（Q2.1；空 token 不发头）。**没有 wait**——等待循环、终态判定、
``GenieJobError`` 抛点在 worker/run（Q6.10）。包络错误
``{code, message, detail}`` → ``GenieHTTPError`` 携 ``.code/.status/.detail``
（Q2.2-5A）。

批 3 线格式（2026-11-04 拍定）：submit **整批一次交**——体始终
``{"configs": [cfg, ...]}``（单 = 批 1，公开面 / client / 网关同形）、
响应 ``{"job_ids": [...]}``；``job_statuses`` 批量查态——体
``{"job_ids": [...]}``、响应 ``{"jobs": [...]}``（每项与单查态同形，
``progress`` 可选、只喂显示不判终态）。
"""
from __future__ import annotations

import json

from ._http import call
from .errors import GW_CODES, GenieHTTPError

__all__ = ["Client", "WORKER_TOKEN_HEADER"]

#: worker token 头名（host/gw 校验侧 import 这一个常量，不手抄）
WORKER_TOKEN_HEADER = "X-Genie-Worker-Token"

_CHUNK = 65536


class Client:
    """worker 侧的作业面客户端。``base_url`` 显式传入（session 经 spawn
    env 递送；递送对接表挂 net 图 §7 待落）。"""

    def __init__(self, base_url: str, worker_token: str | None = None):
        self._base = base_url.rstrip("/")
        self._token = worker_token

    def _headers(self) -> dict:
        h = {}
        if self._token:
            h[WORKER_TOKEN_HEADER] = self._token
        return h

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        h = self._headers()
        if data is not None:
            h["Content-Type"] = "application/json"
        resp = call(method, self._base + path, headers=h, body=data)
        if resp.status >= 400:
            raw = resp.read()
            resp.close()
            raise self._envelope_error(resp.status, raw)
        return resp

    @staticmethod
    def _envelope_error(status: int, raw: bytes) -> GenieHTTPError:
        try:
            env = json.loads(raw.decode("utf-8"))
            code = env.get("code")
            message = env.get("message") or GW_CODES.get(code) or ""
            detail = env.get("detail")
        except ValueError:
            code = None
            message = raw[:200].decode("utf-8", "replace")
            detail = None
        return GenieHTTPError(f"{status} {message}".strip(),
                              status=status, detail=detail, code=code)

    # -- 作业面五方法 -------------------------------------------------

    def submit(self, cfg_or_cfgs) -> str | list[str]:
        """提交一份或一批 SimConfig dict（**整批一次交**，批 3 形状：线
        格式始终 ``{"configs": [...]}``，单 = 批 1；返回与参数同形——单
        ``str`` / 批 ``list[str]``）。不校验——校验归 validate_for_ui，
        调用方在提交前跑。"""
        single = isinstance(cfg_or_cfgs, dict)
        cfgs = [cfg_or_cfgs] if single else list(cfg_or_cfgs)
        with self._request("POST", "/api/jobs", body={"configs": cfgs}) as resp:
            job_ids = json.loads(resp.read().decode("utf-8"))["job_ids"]
        return job_ids[0] if single else job_ids

    def job_status(self, job_id: str) -> dict:
        """作业状态 dict（真相在网关，Q2.2-1A；终态判定不在这；
        ``progress`` 可选——只喂显示，不参与判终态）。"""
        with self._request("GET", f"/api/jobs/{job_id}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def job_statuses(self, job_ids) -> list[dict]:
        """批量查态（批 3：worker 等待循环每秒的口粮）。体
        ``{"job_ids": [...]}`` → ``{"jobs": [...]}``，每项与
        :meth:`job_status` 同形；任一 id 未知 = 整请求 404 包络（与单
        查态同语义——等待循环只查自己 submit 拿到的 id，错误路径）。"""
        with self._request("POST", "/api/jobs/statuses",
                           body={"job_ids": list(job_ids)}) as resp:
            return json.loads(resp.read().decode("utf-8"))["jobs"]

    def cancel(self, job_id: str) -> None:
        """取消作业（Q2.2-3：取消 = 网关远端取消；公开入口带外直达
        session/host，worker 零停止机制）。"""
        with self._request("POST", f"/api/jobs/{job_id}/cancel"):
            pass

    def stream_result(self, job_id: str):
        """结果字节块迭代器——链路全程不整包缓冲（Q2.2-1）；消费方
        （worker/run）直接灌进 archive.Writer.stream_result。"""
        resp = self._request("GET", f"/api/jobs/{job_id}/result")

        def _iter():
            try:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                resp.close()

        return _iter()
