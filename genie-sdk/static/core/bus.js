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

  async function exec(code, opts) {
    opts = opts || {};
    try {
      return await post("/exec", {
        code: String(code == null ? "" : code),
        fresh: !!opts.fresh,
        echo: opts.echo !== false,
      });
    } finally {
      /* 写完成（成功/被拒都算）= "该读一拍"的事实（2026-11-28，站长拍）：
       * GUI 只有一条读通道（环快照），写腿自己不会把结果带回前端——不
       * 通知读一拍，回显只能等 2s 空闲节拍（体感延迟）。这里发信号，
       * 由持有 pollOnce 的 ring.js 接（bus 不依赖 ring，用事件解耦）；
       * 任何卡/按钮/脚本写腿自动获得回显，延迟 = 一次往返。 */
      window.dispatchEvent(new CustomEvent("genie-exec-settled"));
    }
  }

  export { exec, get, post };
