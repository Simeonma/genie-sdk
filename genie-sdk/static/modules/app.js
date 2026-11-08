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
        if (kind === "meta" || kind === "series") {
          _settle(kind, ev.data || {});           /* 结果视图回程（不进终端） */
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
    exec("f.submit()", { fresh: false, echo: true }).catch((e) => {
      if (typeof termAppend === "function") termAppend("✗ " + e.message + "\n", "term-err");
    });
  }

  function runScript() {
    const ed = document.getElementById("code-editor");
    const code = ed ? ed.value : "";
    if (!code.trim()) return;
    termExec(code, { fresh: true, mark: "script" });
  }

  /* ── 库材料：按钮 = worker 里一句 f 命令 ───────────────────────
   * f.mat_from_nk(名, band=(lo,hi), max_poles=n) 从材料库读 nk、在**用户给
   * 的波长窗口**里拟合成整节点（缺省 1 根；没有源带又不给窗口时 worker 明
   * 报，不再全域硬拟）；f.mat.add_lorentz(node=…) 整装入模。 */
  function addLibraryMaterial() {
    const name = window.prompt("材料库名称（如 Au / SiO2 / ZnO）：", "");
    if (name === null) return;
    const n = name.trim();
    if (!n) return;
    const win = window.prompt("拟合波长窗口 nm（lo-hi，如 400-800）：", "400-800");
    if (win === null) return;
    const poles = window.prompt("极点预算 max_poles（先试 1，不够再加）：", "1");
    if (poles === null) return;
    const parts = [JSON.stringify(n)];
    const m = /^\s*(\d+(?:\.\d+)?)\s*[-~,，]\s*(\d+(?:\.\d+)?)\s*$/.exec(win || "");
    if (m) {
      parts.push("band=(" + (parseFloat(m[1]) * 1e-9) + ", " +
                 (parseFloat(m[2]) * 1e-9) + ")");
    }
    if (/^\s*\d+\s*$/.test(poles || "")) {
      parts.push("max_poles=" + parseInt(poles, 10));
    }
    termExec("f.mat.add_lorentz(node=f.mat_from_nk(" + parts.join(", ") + "))");
  }

  /* ── 扫参：按钮 = worker 里 f.add_param_sweep(...) + f.run_param_sweep(...)
   * v1 面板给的是「轴 + linspace/logspace/值列表」；这里在前端展开成
   * v2 的 parameters 字典（参数名 → 值列表），再拼两行 f 命令一次 exec。 */
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
      "f.add_param_sweep(" + JSON.stringify(name) + ", " + JSON.stringify(params) +
      ", mode='product', on_failure='" + onFailure + "')\n" +
      "f.run_param_sweep(" + JSON.stringify(name) + ")";
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
    const arr = (typeof _serverCfg !== "undefined" && _serverCfg && _serverCfg[head]) || [];
    return Array.isArray(arr) ? arr : [];
  }

  function intentsFromOps(ops) {
    const intents = [];
    for (const op of ops || []) {
      const parts = String(op.path || "").split(".");
      const head = parts[0];
      if (head === "parameters") {
        /* 参数表 → f.parameters[<k>] = v / pop(k)（扫参轴引用的就是这些键） */
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

  window.GenieApp = {
    status, login, logout,
    exec, termExec, runModel, runScript, addLibraryMaterial,
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
