"use strict";

  const _realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    let url;
    try {
      url = (typeof input === "string") ? input : (input && input.url) || String(input);
      const u = new URL(url, location.href);
      if (u.origin === location.origin && u.pathname.indexOf("/api/") === 0) {
        return Promise.resolve(new Response(
          JSON.stringify({ detail: "v1 connector severed" }),
          { status: 404, headers: { "Content-Type": "application/json" } }));
      }
    } catch (e) { /* 解析失败照常放行 */ }
    return _realFetch(input, init);
  };

  async function request(path, opts) {
    const r = await _realFetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (!r.ok) {
      throw new Error((body && (body.detail || body.error)) || ("HTTP " + r.status));
    }
    return body;
  }
  const get = (p) => request(p, { method: "GET" });
  const post = (p, body) => request(p, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });

  /* 事件桥的挂起请求（同一时刻一个；搬自 app.js 时漏带的声明） */
  let _pending = null;

  function _awaitEvent(kind, match, timeoutMs) {    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (_pending && _pending.timer === timer) _pending = null;
        reject(new Error("result event timeout (worker busy?)"));
      }, timeoutMs || 15000);
      _pending = { kind, match, resolve, reject, timer };
    });
  }  function _settle(kind, data) {
    const p = _pending;
    if (!p || p.kind !== kind) return;
    if (p.match && !p.match(data)) return;
    clearTimeout(p.timer);
    _pending = null;
    p.resolve(data);
  }

  async function exec(code, opts) {
    opts = opts || {};
    return post("/exec", {
      code: String(code == null ? "" : code),
      fresh: !!opts.fresh,
      echo: opts.echo !== false,
    });
  }

export { exec, get, post, _awaitEvent as awaitEvent, _settle as settle,
         abandonWait };

function abandonWait() {
  if (_pending) { clearTimeout(_pending.timer); _pending = null; }
}
