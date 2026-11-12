/* modules/app.js — Genie Studio 唯一连线层（v2）。
 *
 * v1 的界面（DOM/CSS/图标/3D/表单渲染）原样保留；本文件是界面上所有
 * 按钮与数据的**唯一**后端接点，只认 v2 host 的端点：
 *
 *   GET  /auth                  登录态（host 凭据仓的服务端真相）
 *   POST /auth/login|logout     登录 / 退出（host 代拨 /gw，云 token 不出 host）
 *   GET  /events?since=         环投影（300ms 单拍；输出与模型快照的唯一来源）
 *   POST /exec {code,fresh,echo}      worker 执行（终端 / 脚本 Run / 提交）
 *   POST /form                   表单薄意图（host 生成 Python delta）
 *   GET/POST /script             工作区 script.py（打字即落盘）
 *   POST /cancel|/restart        取消在飞作业 / 重启 worker
 *
 * 所有 v1 的旧远端（/api/*：identity / attach / patch / sim / workspace /
 * result / sweep / usage …）被本文件开头的闸统一掐断——前端不再有任何
 * v1 后端连线；需要的能力逐线在下面重新接。
 */
(function () {
  "use strict";

  /* ── ① 断线闸：v1 的全部 /api/* 一律 404（不再有对端）─────────── */
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

  /* ── ② v2 传输（唯一一层）────────────────────────────────────── */
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

  /* ── ③ 环投影：一次拉取 → 喂显示层（v1 渲染函数不动）──────────── */
  const S = { epoch: null, since: 0, cfgSeq: 0, origin: "exec", busy: false };

  const nl = (t) => {
    const s = String(t == null ? "" : t);
    return /[\r\n]$/.test(s) ? s : s + "\n";
  };

  /* ── 作业进度卡：v2 rollup → v1 的 jobprog 皮（__jobProgress）──────
   * v1 的进度条吃 {step, n_steps, job_id}；把 rollup 的 done+failed/total
   * 翻译成同形喂进去——进度条、ETA、取消钮都复用 v1 的渲染。停止键在
   * v1 的 _cancelJobButton 里已改为 /cancel 同门。 */
  function feedJobProgress(data) {
    if (!data) return;
    const jobs = data.jobs || {};
    const ids = Object.keys(jobs);
    const total = Number(data.total) || ids.length;
    let terminal = 0, failed = Number(data.failed) || 0;
    for (const id of ids) {
      const st = (jobs[id] || {}).state;
      if (st === "done" || st === "failed" || st === "killed" || st === "canceled") terminal += 1;
    }
    const finished = Math.max((Number(data.done) || 0) + failed, terminal);
    const allTerminal = total > 0 && finished >= total;
    /* 扫参面板同拍：v1 的 __sweepPaneEvent 吃 {sweep_id,total,rollup,state} */
    if (S_SWEEP.name && typeof window.__sweepPaneEvent === "function") {
      window.__sweepPaneEvent({
        sweep_id: S_SWEEP.name,
        total: total || S_SWEEP.total,
        rollup: { succeeded: Math.max(0, finished - failed), failed },
        state: allTerminal ? "done" : "running",
      });
      if (allTerminal) S_SWEEP.name = "";
    }
    if (allTerminal) {
      /* 结果视图：作业终态 = 结果就绪——记 job id 并刷 v1 的结果摘要
       * （meta 走 f.result_meta()，Q4.5 公开读动词）。 */
      if (ids[0]) window.__lastJobId = ids[0];
      if (typeof refreshResultChip === "function") refreshResultChip();
      /* Q4.5：结果成员在 .gensim 里；曲线走 f.visualize（Visualize 页签）。 */
      const chip = document.getElementById("result-chip");
      if (chip && !window.__resultStatus) {
        chip.classList.remove("hidden");
        chip.textContent = "";
        const sp = document.createElement("span");
        sp.className = "jobprog-txt";
        sp.textContent = (failed > 0 ? "✗ " + failed + " failed · " : "✓ ")
          + "job " + (ids[0] || "?")
          + " · results/" + (ids[0] || "?") + ".h5";
        sp.title = "结果成员在 .gensim 里";
        chip.appendChild(sp);
      }
      return;
    }
    if (typeof window.__jobProgress === "function") {
      window.__jobProgress({ step: finished, n_steps: total, job_id: ids[0] || "" });
    }
  }

  /* ── 结果视图请求桥：GUI 发一句 f 命令，等环里对应的结构化事件 ──────
   * f.result_meta() → out(kind="meta")；f.visualize(...) → out(kind="series")。
   * 同一时刻只挂一个请求（读动词本就走 worker 的 exec 通道，串行）。 */
  let _pending = null;                            // {kind, match, resolve, reject, timer}
  function _awaitEvent(kind, match, timeoutMs) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (_pending && _pending.timer === timer) _pending = null;
        reject(new Error("result event timeout (worker busy?)"));
      }, timeoutMs || 15000);
      _pending = { kind, match, resolve, reject, timer };
    });
  }
  function _settle(kind, data) {
    const p = _pending;
    if (!p || p.kind !== kind) return;
    if (p.match && !p.match(data)) return;
    clearTimeout(p.timer);
    _pending = null;
    p.resolve(data);
  }

  function _py(v) { return JSON.stringify(v); }   // JSON literals are valid Python

  async function resultMeta() {
    const wait = _awaitEvent("meta", null);
    try {
      await exec("f.result_meta()", { fresh: false, echo: false });
    } catch (e) {
      if (_pending) { clearTimeout(_pending.timer); _pending = null; }
      throw e;
    }
    return wait;
  }

  async function resultSeries(q) {
    q = q || {};
    let code = "f.visualize(" + _py(String(q.mon || ""));
    if (q.field) code += ", field=" + _py(String(q.field));
    if (Array.isArray(q.axes) && q.axes.length) code += ", axes=" + _py(q.axes);
    if (q.slices && Object.keys(q.slices).length) code += ", slices=" + _py(q.slices);
    code += ")";
    const wait = _awaitEvent("series",
      (d) => !q.mon || !d || d.mon === q.mon);
    try {
      await exec(code, { fresh: false, echo: false });
    } catch (e) {
      if (_pending) { clearTimeout(_pending.timer); _pending = null; }
      throw e;
    }
    return wait;
  }

  function snapshot(batch) {
    const evs = batch.events || [];
    const base = (batch.since || 0) - evs.length;
    const lines = [];
    let cfg = null, cfgSeq = S.cfgSeq, lastProgress = null;
    for (let i = 0; i < evs.length; i++) {
      const ev = evs[i], seq = base + i;
      if (ev.ev === "out") {
        const kind = ev.kind || null;
        if (kind === "meta" || kind === "series" || kind === "matfit" ||
            kind === "matlib") {
          _settle(kind, ev.data || {});           /* 结果视图 / 材料回程（不进终端） */
          continue;
        }
        if (kind === "progress") {
          const data = ev.data || {};
          lastProgress = data;
          lines.push({ seq, kind: "job-progress", text: ev.text || "", data });
          continue;
        }
        if (kind === "plot") continue;            /* 旧引用事件（v1 遗留） */
        if (kind === "error") { lines.push({ seq, kind: "stderr", text: nl(ev.text) }); continue; }
        lines.push({ seq, kind: "stdout", text: nl(ev.text) });
      } else if (ev.ev === "done") {
        if (!ev.ok && ev.err) lines.push({ seq, kind: "stderr", text: nl(ev.err) });
      } else if (ev.ev === "cfg") {
        cfg = ev.cfg; cfgSeq = seq;
      }
    }
    const origin = S.origin;
    S.origin = "exec";
    return {
      lines,
      busy: !!batch.busy,
      phase: batch.busy ? "run" : "idle",
      job_id: "",
      cfg,
      resolved: null,
      cfg_seq: cfg ? cfgSeq : S.cfgSeq,
      origin,
      paths: [],
      job_progress: lastProgress,
    };
  }

  async function _pollOnce() {
    const batch = await get("/events?since=" + S.since);
    if (!batch) return S.busy;
    if (S.epoch !== null && batch.epoch !== S.epoch) {
      const out = document.getElementById("term-out");
      if (out) out.textContent = "";              /* host 重启：清屏整份重取 */
      S.epoch = batch.epoch; S.since = 0; S.cfgSeq = 0;
      return S.busy;
    }
    S.epoch = batch.epoch;
    S.since = batch.since;
    S.busy = !!batch.busy;
    const snap = snapshot(batch);
    if (snap.cfg) S.cfgSeq = snap.cfg_seq;
    /* 显示层：v1 的 SessionSync（终端/表单/树/3D 的同步器）原样复用 */
    if (typeof SessionSync !== "undefined" && SessionSync.ingest) {
      SessionSync.ingest(snap);
    } else {
      if (typeof setStopVisible === "function") setStopVisible(S.busy);
    }
    /* 进度条必须在 ingest 之后喂：__jobPhase（ingest 内）会把 chip 写成
     * "计算中"，随后 __jobProgress 才能把百分比盖上去（v1 的同一块皮）。 */
    if (snap.job_progress) feedJobProgress(snap.job_progress);
    return S.busy;
  }

  /* 串行化：开屏有多个调用者（闸门/开屏对齐/空闲轮询），同一个 since
   * 若被并发拉取会把同一批事件渲染两遍——一次只允许一拍在飞。 */
  let _inFlight = null;
  function pollOnce() {
    if (_inFlight) return _inFlight;
    _inFlight = _pollOnce().finally(() => { _inFlight = null; });
    return _inFlight;
  }

  /* ── ④ 动作 ─────────────────────────────────────────────────── */
  async function login(username, password) {
    const r = await post("/auth/login", { username, password });
    try { localStorage.setItem("genie_v2_username", username); } catch (e) {}
    return r;
  }
  const logout = () => post("/auth/logout", {});
  const status = () => get("/auth");

  async function exec(code, opts) {
    opts = opts || {};
    return post("/exec", {
      code: String(code == null ? "" : code),
      fresh: !!opts.fresh,
      echo: opts.echo !== false,
    });
  }

  function termExec(code, opts) {
    opts = opts || {};
    code = String(code || "");
    if (!code.trim()) { if (typeof setStopVisible === "function") setStopVisible(false); return; }
    if (typeof renderTermInput === "function") {
      renderTermInput({ text: code, mark: opts.mark || "" });
    }
    exec(code, { fresh: !!opts.fresh, echo: true }).catch((e) => {
      if (typeof termAppend === "function") termAppend("✗ " + e.message + "\n", "term-err");
    });
  }

  function runModel() {
    exec("genie.fdtd.run()", { fresh: false, echo: true }).catch((e) => {
      if (typeof termAppend === "function") termAppend("✗ " + e.message + "\n", "term-err");
    });
  }

  function runScript() {
    const ed = document.getElementById("code-editor");
    const code = ed ? ed.value : "";
    if (!code.trim()) return;
    termExec(code, { fresh: true, mark: "script" });
  }

  /* ── 库材料：按钮 = worker 里一句 genie 命令 ────────────────────
   * genie.mat_from_nk(名, band=(lo,hi), max_poles=n, imaginary_weight=w) 从材料
   * 库读 nk、在**用户给的波长窗口**里拟合成整节点（max_poles 是 0-n 自动
   * 选的上限，缺省 6；没有源带又不给窗口时 worker 明报）；窗口/上限/权重
   * 记在 localStorage 里，下次点按钮直接带出来。 */
  const FIT_LS = { win: "v2-fit-window", poles: "v2-fit-poles",
                   imw: "v2-fit-imaginary-weight", tol: "v2-fit-tolerance" };
  function fitPref(key, fallback) {
    try {
      const v = window.localStorage.getItem(FIT_LS[key]);
      return v === null || v === "" ? fallback : v;
    } catch (e) { return fallback; }          /* private mode / disabled storage */
  }
  function fitRemember(key, value) {
    try { window.localStorage.setItem(FIT_LS[key], String(value)); } catch (e) { /* ignore */ }
  }

  /* ── 材料库：两段式（选材料 → 拟合预览窗口）────────────────────
   * 第一段只挑材料；第二段给三个参数（波长窗口 / 极点上限 / 强制无源）
   * 并把**原始数据点**与**拟合曲线**画出来。拟合跑在 worker 里
   * （f.fit_preview → out(kind="matfit")，只读、不碰模型）；"加入材料"
   * 仍走今天那条 mat_from_nk + add_lorentz，不新增落模路径。 */
  const GM_CSS = `
  .gm-bd{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;
    display:flex;align-items:center;justify-content:center;font:13px/1.5 system-ui,sans-serif}
  .gm-card{background:#16191d;color:#d8dee6;border:1px solid #2c333a;border-radius:10px;
    box-shadow:0 18px 60px rgba(0,0,0,.6);padding:16px 18px;min-width:360px;max-width:92vw}
  .gm-card h3{margin:0 0 10px;font-size:14px;font-weight:600;color:#eaf0f7}
  .gm-row{display:flex;gap:8px;align-items:center;margin:8px 0}
  .gm-row label{color:#9fb0c2;min-width:96px;font-size:12px}
  .gm-card select,.gm-card input[type=number],.gm-card input[type=text]{
    background:#0e1116;color:#d8dee6;border:1px solid #2c333a;border-radius:6px;
    padding:5px 7px;font:12px system-ui,sans-serif;outline:none}
  .gm-card input[type=number]{width:82px}
  .gm-card select{min-width:200px}
  .gm-btns{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
  .gm-btn{background:#222932;color:#cfd8e3;border:1px solid #33404d;border-radius:6px;
    padding:6px 14px;font-size:12px;cursor:pointer}
  .gm-btn:hover{background:#2b3541}
  .gm-btn.pri{background:#2f6feb;border-color:#2f6feb;color:#fff}
  .gm-btn.pri:hover{background:#3b7bf5}
  .gm-btn:disabled{opacity:.45;cursor:default}
  .gm-fit{display:flex;gap:16px;align-items:flex-start}
  .gm-plot{background:#0e1116;border:1px solid #222a33;border-radius:8px;padding:6px}
  .gm-meta{margin-top:8px;color:#8fa2b6;font-size:12px;max-width:560px}
  .gm-warn{color:#e0a33e}
  .gm-err{color:#ff6b6b}
  `;
  function _gmCss() {
    if (document.getElementById("gm-css")) return;
    const st = document.createElement("style");
    st.id = "gm-css"; st.textContent = GM_CSS;
    document.head.appendChild(st);
  }
  function _gmModal(title) {
    _gmCss();
    const bd = document.createElement("div");
    bd.className = "gm-bd";
    const card = document.createElement("div");
    card.className = "gm-card";
    const h = document.createElement("h3");
    h.textContent = title;
    const body = document.createElement("div");
    const btns = document.createElement("div");
    btns.className = "gm-btns";
    card.appendChild(h); card.appendChild(body); card.appendChild(btns);
    bd.appendChild(card);
    document.body.appendChild(bd);
    const close = () => { bd.remove(); };
    bd.addEventListener("mousedown", (e) => { if (e.target === bd) close(); });
    const onKey = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } };
    document.addEventListener("keydown", onKey);
    bd._close = close;
    return { root: bd, body, btns, close };
  }
  function _gmBtn(label, pri, onclick) {
    const b = document.createElement("button");
    b.className = "gm-btn" + (pri ? " pri" : "");
    b.textContent = label;
    b.addEventListener("click", onclick);
    return b;
  }

  /* n、k 两格：原始散点 + 拟合实线，共享波长轴（nm）。 */
  function _gmPlot(d) {
    const W = 460, H = 150, PADL = 46, PADR = 10, PADT = 12, PADB = 22;
    const wl0 = d.band[0] * 1e9, wl1 = d.band[1] * 1e9;
    const xs = (nm) => PADL + (nm - wl0) / Math.max(wl1 - wl0, 1e-9) * (W - PADL - PADR);
    function panel(ytitle, ys, yf, yRaw) {
      let lo = Infinity, hi = -Infinity;
      ys.forEach((v) => { if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      yf.forEach((v) => { if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
      if (hi - lo < 1e-9) { hi = lo + 1; }
      const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      const yy = (v) => PADT + (hi - v) / (hi - lo) * (H - PADT - PADB);
      const pts = d.wl_fit.map((w, i) => xs(w * 1e9) + "," + yy(yf[i])).join(" ");
      const dots = d.wl.map((w, i) => "<circle cx=\"" + xs(w * 1e9) + "\" cy=\"" + yy(ys[i]) +
        "\" r=\"2.6\" fill=\"#ffb74d\"/>").join("");
      return "<g><rect x=\"" + PADL + "\" y=\"" + PADT + "\" width=\"" + (W - PADL - PADR) +
        "\" height=\"" + (H - PADT - PADB) + "\" fill=\"none\" stroke=\"#222a33\"/>" +
        "<text x=\"6\" y=\"" + (PADT + 10) + "\" fill=\"#8fa2b6\" font-size=\"11\">" + ytitle + "</text>" +
        "<text x=\"6\" y=\"" + (PADT + 22) + "\" fill=\"#5f7183\" font-size=\"9\">" + hi.toFixed(2) + "</text>" +
        "<text x=\"6\" y=\"" + (H - PADB) + "\" fill=\"#5f7183\" font-size=\"9\">" + lo.toFixed(2) + "</text>" +
        "<polyline points=\"" + pts + "\" fill=\"none\" stroke=\"#4ea1ff\" stroke-width=\"1.6\"/>" +
        dots + "</g>";
    }
    const s1 = panel("n", d.n, d.n_fit);
    const s2 = panel("k", d.k, d.k_fit);
    return "<svg width=\"" + W + "\" height=\"" + (H * 2 + 18) + "\" viewBox=\"0 0 " + W + " " + (H * 2 + 18) + "\">" +
      s1 + "<g transform=\"translate(0," + (H + 14) + ")\">" + s2 + "</g>" +
      "<text x=\"" + ((W + PADL) / 2) + "\" y=\"" + (H * 2 + 14) + "\" fill=\"#8fa2b6\" font-size=\"10\">" +
      wl0.toFixed(0) + "–" + wl1.toFixed(0) + " nm</text></svg>";
  }

  async function fitPreview(name, win, poles, passive, tol) {
    const wait = _awaitEvent("matfit", (d) => !d || d.name === name, 120000);
    let code = "genie.fit_preview(" + JSON.stringify(name);
    const m = /^\s*(\d+(?:\.\d+)?)\s*[-~,，]\s*(\d+(?:\.\d+)?)\s*$/.exec(win || "");
    if (m) code += ", band=(" + (parseFloat(m[1]) * 1e-9) + ", " + (parseFloat(m[2]) * 1e-9) + ")";
    if (/^\s*\d+\s*$/.test(poles || "")) code += ", max_poles=" + parseInt(poles, 10);
    if (/^\s*\d*\.?\d+(?:[eE][-+]?\d+)?\s*$/.test(tol || "")) code += ", tolerance=" + parseFloat(tol);
    if (passive) code += ", make_passive=True";
    code += ")";
    try {
      await exec(code, { fresh: false, echo: false });
    } catch (e) {
      if (_pending) { clearTimeout(_pending.timer); _pending = null; }
      throw e;
    }
    return wait;
  }

  /* 第二段：拟合窗口（参数 + 曲线 + 加入材料） */
  function openFitWindow(name, win0, poles0, tol0) {
    const w = _gmModal("拟合 — " + name);
    const row = document.createElement("div");
    row.className = "gm-row";
    row.innerHTML = "<label>波长窗口 nm</label>";
    const win = document.createElement("input");
    win.type = "text"; win.value = win0 || "400-800"; win.style.width = "110px";
    const lb = document.createElement("label");
    lb.textContent = "极点上限"; lb.style.minWidth = "66px";
    const poles = document.createElement("input");
    poles.type = "number"; poles.min = "0"; poles.max = "30"; poles.value = poles0 || "10";
    const pLab = document.createElement("label");
    pLab.textContent = "强制无源";
    const passive = document.createElement("input");
    passive.type = "checkbox";
    row.appendChild(win); row.appendChild(lb); row.appendChild(poles);
    const tLab = document.createElement("label"); tLab.textContent = "tolerance"; tLab.style.minWidth = "66px"; const tol = document.createElement("input"); tol.type = "number"; tol.step = "0.001"; tol.min = "0"; tol.value = (tol0 || "0.1"); tol.style.width = "82px";
    row.appendChild(pLab); row.appendChild(passive); row.appendChild(tLab); row.appendChild(tol);
    const plotWrap = document.createElement("div");
    plotWrap.className = "gm-plot";
    plotWrap.textContent = "点击「拟合」…";
    const meta = document.createElement("div");
    meta.className = "gm-meta";
    const left = document.createElement("div");
    left.appendChild(row); left.appendChild(meta);
    const flex = document.createElement("div");
    flex.className = "gm-fit";
    flex.appendChild(left); flex.appendChild(plotWrap);
    w.body.appendChild(flex);

    const doFit = _gmBtn("拟合", true, async () => {
      doFit.disabled = true;
      plotWrap.textContent = "拟合中…";
      meta.textContent = "";
      try {
        const d = await fitPreview(name, win.value.trim(), poles.value.trim(),
                                   passive.checked, tol.value.trim());
        plotWrap.innerHTML = _gmPlot(d);
        meta.innerHTML = "";
        const line = document.createElement("div");
        line.textContent = d.summary;
        meta.appendChild(line);
        if (d.quality && d.quality.rms !== undefined) {
          const abs = document.createElement("div");
          abs.style.color = "#5f7183";
          abs.textContent = "绝对 ε-RMS = " + Number(d.quality.rms).toPrecision(4) +
            "（加权，跨材料不可比；上面的 rms% 才是可比的那个）";
          meta.appendChild(abs);
        }
        if (d.warnings && d.warnings.length) {
          const wl2 = document.createElement("div");
          wl2.className = "gm-warn";
          wl2.textContent = "⚠ " + d.warnings.join(" · ");
          meta.appendChild(wl2);
        }
        fitRemember("win", win.value.trim());
        fitRemember("poles", poles.value.trim());
        fitRemember("tol", tol.value.trim());
        apply.disabled = false;
      } catch (e) {
        plotWrap.textContent = "";
        meta.className = "gm-meta gm-err";
        meta.textContent = "✗ " + e.message;
      } finally {
        doFit.disabled = false;
      }
    });
    const apply = _gmBtn("加入材料", true, () => {
      let parts = [JSON.stringify(name)];
      const m = /^\s*(\d+(?:\.\d+)?)\s*[-~,，]\s*(\d+(?:\.\d+)?)\s*$/.exec(win.value || "");
      if (m) parts.push("band=(" + (parseFloat(m[1]) * 1e-9) + ", " + (parseFloat(m[2]) * 1e-9) + ")");
      if (/^\s*\d+\s*$/.test(poles.value || "")) parts.push("max_poles=" + parseInt(poles.value, 10));
      if (/^\s*\d*\.?\d+(?:[eE][-+]?\d+)?\s*$/.test(tol.value || "")) parts.push("tolerance=" + parseFloat(tol.value));
      if (passive.checked) parts.push("make_passive=True");
      termExec("genie.materials.add_lorentz(node=genie.mat_from_nk(" + parts.join(", ") + "))");
      w.close();
    });
    apply.disabled = true;
    w.btns.appendChild(_gmBtn("取消", false, w.close));
    w.btns.appendChild(doFit);
    w.btns.appendChild(apply);
    doFit.click();
  }

  /* 第一段：选材料 */
  function openLibraryDialog() {
    const w = _gmModal("新建材料（来自材料库）");
    const row = document.createElement("div");
    row.className = "gm-row";
    const lb = document.createElement("label");
    lb.textContent = "材料";
    const sel = document.createElement("select");
    sel.innerHTML = "<option value=''>载入中…</option>";
    row.appendChild(lb); row.appendChild(sel);
    w.body.appendChild(row);
    const go = _gmBtn("拟合", true, () => {
      const name = sel.value;
      if (!name) return;
      w.close();
      openFitWindow(name, fitPref("win", "400-800"), fitPref("poles", "10"), fitPref("tol", "0.1"));
    });
    go.disabled = true;
    w.btns.appendChild(_gmBtn("取消", false, w.close));
    w.btns.appendChild(go);
    const wait = _awaitEvent("matlib", null, 20000);
    exec("genie.material_library()", { fresh: false, echo: false })
      .then(() => wait)
      .then((d) => {
        const names = (d && d.materials) || [];
        sel.innerHTML = "";
        names.forEach((n) => {
          const o = document.createElement("option");
          o.value = n; o.textContent = n;
          sel.appendChild(o);
        });
        go.disabled = names.length === 0;
        if (!names.length) sel.innerHTML = "<option value=''>（库为空）</option>";
      })
      .catch((e) => {
        sel.innerHTML = "<option value=''>库清单读取失败</option>";
        console.error(e);
      });
  }

  function addLibraryMaterial() { openLibraryDialog(); }

  /* ── 扫参：按钮 = worker 里 genie.add_param_sweep(...) + genie.run_param_sweep(...)
   * v1 面板给的是「轴 + linspace/logspace/值列表」；这里在前端展开成
   * v2 的 parameters 字典（参数名 → 值列表），再拼两行 genie 命令一次 exec。 */
  const S_SWEEP = { name: "", total: 0 };

  function _linspace(a, b, n) {
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push(n === 1 ? a : a + (b - a) * i / (n - 1));
    }
    return out;
  }
  function _logspace(a, b, n) {                 /* a,b = 10 的指数（v1 约定） */
    return _linspace(a, b, n).map((e) => Math.pow(10, e));
  }

  function expandAxes(axes) {
    const params = {};
    let total = 1;
    for (const ax of axes || []) {
      let vals;
      if (ax.values) { vals = ax.values.slice(); }
      else if (ax.logspace) { vals = _logspace(ax.logspace[0], ax.logspace[1], ax.logspace[2]); }
      else if (ax.linspace) { vals = _linspace(ax.linspace[0], ax.linspace[1], ax.linspace[2]); }
      else throw new Error("axis " + (ax.name || "?") + " has no values");
      if (!vals.length) throw new Error("axis " + (ax.name || "?") + " is empty");
      params[ax.name] = vals;
      total *= vals.length;
    }
    return { params, total };
  }

  async function runSweep(spec) {
    spec = spec || {};
    const { params, total } = expandAxes(spec.axes);
    const name = String(spec.name || ("gui_" + Date.now().toString(36)));
    const onFailure = spec.onFailure === "abort" ? "abort" : "continue";
    const code =
      "genie.add_param_sweep(" + JSON.stringify(name) + ", " + JSON.stringify(params) +
      ", mode='product', on_failure='" + onFailure + "')\n" +
      "genie.run_param_sweep(" + JSON.stringify(name) + ")";
    S_SWEEP.name = name;
    S_SWEEP.total = total;
    if (typeof renderTermInput === "function") {
      renderTermInput({ text: code, mark: "sweep" });
    }
    return exec(code, { fresh: false, echo: true });
  }

  async function getSweeps() {
    /* 扫参账 = 工作区 parameter_sweep_<名>/index.json（点文档列表） */
    return get("/sweeps");
  }

  /* ── ⑤ 表单薄意图（v1 的路径 diff → v2 意图）──────────────────── */
  const COLL_OF = { materials: "mat", geometry: "geom", source: "src", monitor: "mon" };

  function serverItems(head) {
    /* Q17 嵌套户型：materials/geometry 住 cfg.model，source/monitor 住
     * cfg.config（fdtd 缺省）；GUI fdtd 绑定不变。 */
    const c = (typeof _serverCfg !== "undefined" && _serverCfg) || {};
    const arr = (c.model && c.model[head]) != null ? c.model[head]
      : ((c.config && c.config[head]) != null ? c.config[head]
         : c[head]);
    return Array.isArray(arr) ? arr : [];
  }

  function intentsFromOps(ops) {
    const intents = [];
    for (const op of ops || []) {
      const parts = String(op.path || "").split(".");
      const head = parts[0];
      if (head === "parameters") {
        /* 参数表 → genie.parameters[<k>] = v / pop(k)（扫参轴引用的就是这些键） */
        if (parts.length === 2 && parts[1]) {
          if (op.value === null) intents.push({ op: "param", name: parts[1], remove: true });
          else intents.push({ op: "param", name: parts[1], value: op.value });
        } else if (parts.length === 1 && op.value && typeof op.value === "object") {
          for (const k of Object.keys(op.value)) {
            intents.push({ op: "param", name: k, value: op.value[k] });
          }
        }
        continue;
      }
      if (head === "run") continue;              /* 动作参数不属表单线 */
      if (head === "domain" || head === "mesh") {
        intents.push({ collection: head, id: null,
                       field: parts.slice(1).join("."), value: op.value });
        continue;
      }
      const coll = COLL_OF[head];
      if (!coll) continue;
      if (parts.length === 1) {                  /* 整表替换 → add/remove/set */
        const oldArr = serverItems(head);
        const newArr = Array.isArray(op.value) ? op.value : [];
        const oldByName = new Map(oldArr.filter(x => x && x.name).map(x => [x.name, x]));
        const newByName = new Map(newArr.filter(x => x && x.name).map(x => [x.name, x]));
        for (const [name, it] of oldByName) {
          if (!newByName.has(name)) intents.push({ op: "remove", collection: coll, id: it.id });
        }
        for (const [name, it] of newByName) {
          const old = oldByName.get(name);
          if (!old) { intents.push({ op: "add", collection: coll, kind: it.type, name }); continue; }
          for (const k of Object.keys(it)) {
            if (k === "type" || k === "name" || k === "id") continue;
            if (JSON.stringify(old[k]) !== JSON.stringify(it[k])) {
              intents.push({ collection: coll, id: old.id, field: k, value: it[k] });
            }
          }
        }
        continue;
      }
      const idx = Number(parts[1]);
      const localArr = (typeof cfg !== "undefined" && cfg && cfg[head]) || [];
      const local = localArr[idx];
      if (!local) continue;
      const server = serverItems(head).find(x => x.name === local.name);
      if (!server || server.id === undefined) continue;
      intents.push({ collection: coll, id: server.id,
                     field: parts.slice(2).join("."), value: op.value });
    }
    return intents;
  }

  async function formIntents(ops) {
    const intents = intentsFromOps(ops);
    for (const intent of intents) {
      await post("/form", intent);
      S.origin = "patch";                        /* 下一拍 cfg = 我的回声 */
    }
    return intents.length;
  }

  /* ── ⑥ 脚本（工作区 script.py）────────────────────────────────── */
  const getScript = () => get("/script?focus=0");
  const saveScript = (text) => post("/script", { text: String(text || "") });

  /* ── ⑦ 启动（DOM 就绪后：脚本载入 + 打字即落盘 + 首拍）────────── */
  document.addEventListener("DOMContentLoaded", function () {
    const ed = document.getElementById("code-editor");
    if (ed) {
      getScript().then((s) => {
        if (s && typeof s.text === "string" && s.text && document.activeElement !== ed) {
          ed.value = s.text;
          if (typeof hlRefresh === "function") hlRefresh();
        }
      }).catch(() => {});
      let timer = null;
      ed.addEventListener("input", () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { saveScript(ed.value).catch(() => {}); }, 400);
      });
      ed.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          saveScript(ed.value).catch(() => {});
        }
      });
    }
    setTimeout(() => { pollOnce().catch(() => {}); }, 0);
    /* 结果就绪探测：档里已有结果时，让结果面板/Plot 不必先 Run 一次 */
    setTimeout(() => {
      resultMeta().then((m) => {
        if (!m || !m.ok) return;
        window.__lastJobId = m.job_id || window.__lastJobId;
        window.__resultStatus = m;
        if (typeof refreshResultChip === "function") refreshResultChip();
      }).catch(() => {});
    }, 600);
  });

  /* Q17 嵌套户型 → GUI 平铺视图的一次性投影（视图层零改动）：
   * worker 的 cfg 快照现在是 {model:{materials,geometry,parameters},
   * config:{domain,mesh,source,monitor}, run}；表单/树/3D/检查器仍按
   * 平铺键读写本地 cfg。投影保留 model/config 原键（serverItems 的
   * diff 基准两态都能读），顶层平铺键从两段浅抄。 */
  window.guiViewOf = function (c) {
    if (!c || typeof c !== "object") return c;
    if (!c.model && !c.config) return c;
    const m = c.model || {}, k = c.config || {};
    return Object.assign({}, c, m, k);
  };

  window.GenieApp = {
    status, login, logout,
    exec, termExec, runModel, runScript, addLibraryMaterial,
    openLibraryDialog, fitPreview,
    runSweep, getSweeps,
    resultMeta, resultSeries,
    formIntents, intentsFromOps,
    getScript, saveScript,
    pollOnce,
    cancel: () => post("/cancel", {}),
    restart: () => post("/restart", {}),
    get busy() { return S.busy; },
  };
})();
