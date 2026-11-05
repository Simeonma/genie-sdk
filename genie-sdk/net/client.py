"""genie.net.client — the worker→host ``/gw`` job plane (five methods).

The five job-plane methods: ``submit / job_status / job_statuses / cancel /
stream_result``; every request carries the worker token header (no header
is sent when the token is empty). **There is no wait** — the wait loop,
terminal-state determination, and the ``GenieJobError`` raise point live in
worker/run. Envelope errors ``{code, message, detail}`` → ``GenieHTTPError``
carrying ``.code/.status/.detail``.

Wire format: submit **submits the whole batch
at once** — the payload is always batch-shaped ``{"configs": [cfg, ...]}`` (a
single submission is a batch of one; same shape across the public surface /
client / gateway), response ``{"job_ids": [...]}``; ``job_statuses`` is the
batch status query — body ``{"job_ids": [...]}``, response ``{"jobs": [...]}``
(each item has the same shape as the single-job status query; ``progress`` is
optional and only feeds display, not terminal-state determination).
"""
from __future__ import annotations

import json

from ._http import call
from .errors import GW_CODES, GenieHTTPError

__all__ = ["Client", "WORKER_TOKEN_HEADER"]

#: Worker token header name (the host/gw validation side imports this one constant — not hand-copied).
WORKER_TOKEN_HEADER = "X-Genie-Worker-Token"

_CHUNK = 65536


class Client:
    """The job-plane client on the worker side. ``base_url`` is passed in
    explicitly (the session delivers it via spawn env)."""

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

    # -- the five job-plane methods -------------------------------------------------

    def submit(self, cfg_or_cfgs) -> str | list[str]:
        """Submit one or a batch of SimConfig dicts (**submit the whole
        batch at once**; the wire format is always
        ``{"configs": [...]}``, a single submission is a batch of one; the
        return matches the argument shape — single ``str`` / batch
        ``list[str]``). No validation here — validation belongs to
        validate_for_ui, which the caller runs before submitting."""
        single = isinstance(cfg_or_cfgs, dict)
        cfgs = [cfg_or_cfgs] if single else list(cfg_or_cfgs)
        with self._request("POST", "/api/jobs", body={"configs": cfgs}) as resp:
            job_ids = json.loads(resp.read().decode("utf-8"))["job_ids"]
        return job_ids[0] if single else job_ids

    def job_status(self, job_id: str) -> dict:
        """Job status dict (the source of truth lives at the gateway;
        terminal-state determination is not here; ``progress`` is
        optional — display feed only, not part of terminal-state
        determination)."""
        with self._request("GET", f"/api/jobs/{job_id}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def job_statuses(self, job_ids) -> list[dict]:
        """Batch status query (the per-second feed for the worker wait
        loop). Body ``{"job_ids": [...]}`` → ``{"jobs": [...]}``; each
        item has the same shape as :meth:`job_status`; any unknown id = the
        whole request gets a 404 envelope (same semantics as the single-job
        status query — the wait loop only queries ids it got from submit, an
        error path)."""
        with self._request("POST", "/api/jobs/statuses",
                           body={"job_ids": list(job_ids)}) as resp:
            return json.loads(resp.read().decode("utf-8"))["jobs"]

    def cancel(self, job_id: str) -> None:
        """Cancel a job (cancellation = remote cancellation at the gateway;
        the public entry reaches session/host out of band, and the
        worker has no stop mechanism)."""
        with self._request("POST", f"/api/jobs/{job_id}/cancel"):
            pass

    def stream_result(self, job_id: str):
        """Chunked iterator over result bytes — no whole-payload buffering
        anywhere along the chain; the consumer (worker/run) pours it
        straight into archive.Writer.stream_result."""
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
