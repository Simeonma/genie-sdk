/* modules/result.js — 结果模式 + 结果标签卡（Plot 多卡 + 折线图 + 结果卡）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ── 结果模式（results-surface P2/P3；2026-10-09 站长裁决正名）────
 * Run → 作业终态 → 落在「结果模式」（看结果，非 layout——layout 指
 * 可编辑态）：输入件（input/select/textarea）禁用 + 工具栏四个加对象
 * 组（Material/Geometry/Source/Monitor）禁用；Simulate(=Switch to
 * setting)/Agent/左树/中间栏面板本体照常。按钮语义：结果态显示
 * Switch to setting（点了回编辑），编辑态显示 Run（点了开跑）。
 * 左树监视器右键 → 该监视器曲线（宿主 latest.h5 降采样 JSON，
 * /api/result/meta + /api/result/series——G13②：结果=会话状态，从
 * 会话工作区读；host 私藏管道已退役）。徽标点击 = 同 Switch to setting。*/
window.__resultMode = false;

/* 结果态锁：输入件 disabled 防键盘 + 工具栏加对象按钮 disabled；
 * CSS(body.result-mode) 补 pointer-events+视觉降权；MutationObserver
 * 兜表单重渲染（树上点对象→FormPanel 重建→新输入件再上锁） */
let __resultLockMO = null;
const __RESULT_LOCK_TB = ["tb-mat-btns", "tb-geo-btns", "tb-src-btns", "tb-mon-btns"];
function __lockConfigInputs() {
  const c = document.getElementById("config-content");
  if (c) {
    c.querySelectorAll("input, select, textarea")
      .forEach(el => { el.disabled = true; });
    /* 面板内改 cfg 的按钮（+refine/删除/模式 pill…）同锁——cfg 只读；
     * 折叠头是 div(role=button) 不受影响照常可点；新建 Plot 例外 */
    c.querySelectorAll("button").forEach(b => {
      if (!b.closest(".plot-cta")) b.disabled = true;
    });
  }
  for (const id of __RESULT_LOCK_TB) {
    const g = document.getElementById(id);
    if (g) g.querySelectorAll("button").forEach(b => { b.disabled = true; });
  }
}
function __startResultLock() {
  __lockConfigInputs();
  const c = document.getElementById("config-content");
  if (!c || __resultLockMO) return;
  __resultLockMO = new MutationObserver(__lockConfigInputs);
  __resultLockMO.observe(c, { childList: true, subtree: true });
}
function __stopResultLock() {
  if (__resultLockMO) { __resultLockMO.disconnect(); __resultLockMO = null; }
  renderInspector();   // 重建当前表单 → 恢复各控件的本来 disabled 逻辑
  for (const id of __RESULT_LOCK_TB) {
    const g = document.getElementById(id);
    if (g) g.querySelectorAll("button").forEach(b => { b.disabled = false; });
  }
}

function enterResult(silent = false) {
  if (window.__resultMode) return;   /* 已在结果态：刷新 chip 即可，勿重弹 */
  document.body.classList.add("result-mode");
  window.__resultMode = true;
  renderInspector();        /* CTA 仅结果态（2026-10-14）——重渲染现身 */
  __startResultLock();
  runBtnRefresh();          /* 模式状态由按钮文案(Switch to setting⇄Run)
                               + 工具栏灰锁表达——徽标退役(2026-10-10 站长裁决) */
  refreshResultChip();
  if (!silent)
    toast("已切结果模式——树选监视器 → 检查器「新建 Plot」；Switch to setting 回编辑", "ok");
}

function exitResult() {
  document.body.classList.remove("result-mode");
  window.__resultMode = false;
  __stopResultLock();
  runBtnRefresh();
  toast("已回编辑模式——点 Run 开始新仿真（Plot 标签常驻可看）", "ok");
}

/* ── Visualize 模块页签（2026-10 站长裁决：Plot 升格为与 3D/文件同级
 * 的面板）────────────────────────────────────────────────────
 * 页签条只管 Plot（每个 Plot 一签，可关）；3D Scene 不再是页签——
 * 场景区回归纯 3D，dock 层负责模块切换。右端 chip = 作业摘要/流水进度。
 * 建 Plot：左树选中监视器 → 检查器「新建 Plot」→ Dock.activatePanel
 * 顶起 Visualize。Plot 规格常驻（编辑模式也显示）；本期固定格式
 * （峰值格点 |场| 曲线，数据 = /api/result/series 降采样）。 */
window.__plots = [];        /* [{id, name, mon, field}] */
window.__lastJobId = "";    /* 最近完成的作业（job-done 事件维护）*/
window.__plotSeq = 0;
window.__activeTab = null;

function plotDefaultField(mon) {
  const st = window.__resultStatus;
  const m = st && st.ok && (st.monitors || []).find(x => x.name === mon);
  return (m && m.fields && m.fields[0]) || "Ex";
}

function addMonitorPlotCta(i) {
  const m = ((cfg && cfg.monitor) || [])[i];
  if (!m || !m.name) return;
  const c = document.getElementById("config-content");
  if (!c) return;
  const bar = document.createElement("div");
  bar.className = "plot-cta";
  const b = document.createElement("button");
  b.className = "mini plot-cta-btn";
  b.textContent = "📈 新建 Plot";
  b.title = "为该监视器建一张结果图（Visualize 模块页签）";
  b.addEventListener("click", () => addPlot(m.name));
  const hint = document.createElement("span");
  hint.className = "plot-cta-hint";
  hint.textContent = "…";
  bar.appendChild(b); bar.appendChild(hint);
  c.appendChild(bar);
  /* 数据态提示：Plot 画的是最近一次 Run 的 latest.h5——新加的监视器
   * 必须重新 Run 才有数据（2026-10-10 站长实测困惑点，标出来） */
  const ready = (st) => {
    const hit = st && st.ok && (st.monitors || []).some(x => x.name === m.name);
    hint.textContent = hit ? "✓ 数据就绪（最近一次 Run）"
      : "⚠ 不在最近一次结果里——重新 Run 后可画";
  };
  const st = window.__resultStatus;
  if (st && st.ok) { ready(st); return; }
  if (!window.__lastJobId) { hint.textContent = ""; return; }   /* 从未 Run：不发空查询 */
  const KEYH = { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" };
  fetch("/api/result/meta?job_id=" + encodeURIComponent(window.__lastJobId || ""),
        { headers: KEYH }).then(r => r.json())
    .then(s2 => { window.__resultStatus = s2; if (s2.ok) refreshResultChip(); ready(s2); })
    .catch(() => { hint.textContent = ""; });
}

async function addPlot(mon, field) {
  let st = window.__resultStatus;
  if (!st || !st.ok) {          /* 冷会话懒拉（boot 时可能还没登录） */
    if (!window.__lastJobId) { toast("暂无结果数据——先 Run 一次", "warn"); return; }
    const KEYH = { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" };
    st = await fetch("/api/result/meta?job_id=" + encodeURIComponent(window.__lastJobId || ""),
                     { headers: KEYH })
      .then(r => r.json()).catch(() => null);
    window.__resultStatus = st;
    if (st && st.ok) refreshResultChip();
  }
  if (!st || !st.ok) { toast("暂无结果数据——先 Run 一次", "warn"); return; }
  if (!(st.monitors || []).some(m => m.name === mon)) {
    toast("「" + mon + "」不在最近一次结果里——Plot 画的是最近一次 Run 的数据，重跑后再建", "warn");
    return;
  }
  const spec = { id: "plot" + (++window.__plotSeq),
                 name: "Plot " + window.__plotSeq,
                 mon, field: field || plotDefaultField(mon) };
  window.__plots.push(spec);
  renderTabbar();
  if (typeof Dock !== "undefined") Dock.activatePanel("visualize-panel");   /* 顶起模块 */
  activateTab(spec.id);
  toast(spec.name + "：" + spec.mon + " · |" + spec.field + "|", "ok");
}

function closePlot(id) {
  window.__plots = window.__plots.filter(p => p.id !== id);
  const next = window.__plots[0];
  if (next) { activateTab(next.id); return; }
  /* 全关 = 空态（引导页），页签条清空 */
  window.__activeTab = null;
  const pv = document.getElementById("plot-view");
  if (pv) pv.classList.add("hidden");
  const empty = document.getElementById("viz-empty");
  if (empty) empty.classList.remove("hidden");
  renderTabbar();
}

function renderTabbar() {
  const bar = document.getElementById("view-tabs");
  if (!bar) return;
  bar.innerHTML = "";
  const mk = (id, label, closable) => {
    const t = document.createElement("div");
    t.className = "view-tab" + (window.__activeTab === id ? " active" : "");
    t.setAttribute("role", "tab");
    t.setAttribute("aria-selected", window.__activeTab === id ? "true" : "false");
    const sp = document.createElement("span");
    sp.textContent = label;
    t.appendChild(sp);
    if (closable) {
      const x = document.createElement("span");
      x.className = "tab-close"; x.textContent = "×";
      x.title = "关闭 " + label;
      x.addEventListener("click", (e) => { e.stopPropagation(); closePlot(id); });
      t.appendChild(x);
    }
    t.addEventListener("click", () => activateTab(id));
    bar.appendChild(t);
    if (window.__activeTab === id)
      t.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  for (const p of window.__plots) mk(p.id, p.name, true);
  /* 无 Plot = 页签条隐藏（chip 仍挂在 tabbar 右端），空态引导页现身 */
  bar.classList.toggle("single", bar.children.length === 0);
  const empty = document.getElementById("viz-empty");
  if (empty) empty.classList.toggle("hidden", window.__plots.length > 0);
}

function activateTab(id) {
  const spec = window.__plots.find(p => p.id === id);
  const pv = document.getElementById("plot-view");
  if (!spec || !pv) return;
  window.__activeTab = id;
  pv.classList.remove("hidden");
  const empty = document.getElementById("viz-empty");
  if (empty) empty.classList.add("hidden");
  renderPlotInto(pv, spec);
  renderTabbar();
}

function renderPlotInto(view, spec) {
  if (!spec) { view.textContent = ""; return; }
  view.innerHTML = "";
  const head = document.createElement("div");
  head.className = "plot-head";
  head.textContent = spec.name + " — " + spec.mon + " · |" + spec.field
    + "|（峰值格点）";
  view.appendChild(head);
  const cv = document.createElement("canvas");
  cv.className = "plot-canvas";
  view.appendChild(cv);
  const KEYH = { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" };
  fetch("/api/result/series?job_id=" + encodeURIComponent(window.__lastJobId || "")
        + "&mon=" + encodeURIComponent(spec.mon)
        + "&field=" + encodeURIComponent(spec.field) + "&maxpoints=1200",
        { headers: KEYH })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { view.textContent = d.detail || "无数据"; return; }
      _plotLine(cv, d.x, d.y, d.x_label, "|" + spec.field + "|");
    })
    .catch(e => { view.textContent = "加载失败: " + e; });
}

function refreshResultChip() {
  const chip = document.getElementById("result-chip");
  if (!chip) return;
  if (!window.__lastJobId) { chip.classList.add("hidden"); chip.innerHTML = ""; return; }
  const KEYH = { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" };
  fetch("/api/result/meta?job_id=" + encodeURIComponent(window.__lastJobId),
        { headers: KEYH })
    .then(r => r.json())
    .then(st => {
      window.__resultStatus = st;
      if (!chip) return;
      if (!st.ok) { chip.classList.add("hidden"); chip.innerHTML = ""; return; }
      chip.classList.remove("hidden");
      chip.innerHTML = "";
      const sp = document.createElement("span");
      sp.textContent = "作业 " + (st.job_id || "?") + " · "
        + (st.executed_steps || "?") + " 步 · dt="
        + (st.dt != null ? Number(st.dt).toExponential(2) : "?") + " s · 网格 "
        + (st.grid_shape || []).join("×");
      sp.title = sp.textContent;
      chip.appendChild(sp);
      const dl = document.createElement("a");
      dl.href = "/api/jobs/" + encodeURIComponent(st.job_id || "") + "/result";
      dl.download = (st.job_id || "results") + "_results.h5";
      dl.textContent = "⬇ result.h5";
      chip.appendChild(dl);
    })
    .catch(() => {});
}

/* 首屏：渲染页签条（空态引导）+ 拉一次结果摘要 */
renderTabbar();
refreshResultChip();

/* ── 运行中进度（方案 A：result-chip 原位变形，2026-10-15 站长定案）──
 * 数据链：内核 runner stdout 泵 → status.json(step/n_steps) → SDK 轮询
 * → worker job-progress 事件 → term.js 转喂本入口。ETA = 首尾样本步速
 * 外推（样本跨 ≥5s 才开口，之前显示"测速中…"）；job_id 变了=新作业，
 * 样本清零。收尾无需显式清理——job-done 事件路径的 refreshResultChip
 * 整体覆写 chip 内容，进度形态自然切回摘要+下载。 */
/* ── 仿真流水进度（2026-10 架构收敛：阶段 = 会话 state 事件的事实）──
 * 提交（上传 payload）→ 排队（云端繁忙）→ 计算（步进%）→ 取回（下载）。
 * 阶段由 worker state 事件 / output 快照驱动（term.js 转喂本入口）；
 * 计算段的百分比/ETA 仍由 job-progress 样本外推（下方 __jobProgress）。
 * 非计算段 = 不定宽滑动条（不知道总量，只让用户知道「活着、在哪步」）。 */
/* ── 取消按钮（G4b 2026-10-21）：queue/run 态可取消作业 ──
 * 链路：浏览器 → 宿主 POST /api/jobs/{id}/cancel（会话守卫+key 回退）
 * → 网关 → 内核 runner.cancel（queued 落 killed / running killpg）。 */
function _cancelJobButton(jobId) {
  const b = document.createElement("button");
  b.className = "jobprog-cancel";
  b.textContent = "✕ 取消";
  b.title = "取消作业 " + (jobId || "?");
  b.style.cssText = "margin-left:8px;padding:1px 8px;font-size:11px;"
    + "border:1px solid #d0d7de;background:#fff;border-radius:6px;"
    + "cursor:pointer;color:#c0392b";
  b.onclick = () => {
    b.disabled = true;
    b.textContent = "取消中…";
    const KEYH = { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" };
    fetch("/api/jobs/" + encodeURIComponent(jobId || "") + "/cancel",
          { method: "POST", headers: KEYH })
      .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); })
      .then(() => { b.textContent = "已取消"; })
      .catch(() => { b.disabled = false; b.textContent = "✕ 取消(重试)"; });
  };
  return b;
}

/* ── 排队 ETA 文案（G30，站长定案）──
 * 排队态只回答"还要等多久"，**不显示"第几位"**——位置对用户没有行动价值，
 * 反而让人盯着数字。数据源 = 宿主 /api/jobs/{id} 代理的网关状态（内核
 * status 带 eta_range，单位秒）；拿不到就退回裸标签，绝不编数字。 */
function _etaText(lo, hi) {
  const hasLo = typeof lo === "number" && isFinite(lo) && lo >= 0;
  const hasHi = typeof hi === "number" && isFinite(hi) && hi >= 0;
  if (!hasLo && !hasHi) return "";
  const a = hasLo ? lo : hi;
  const b = hasHi ? hi : lo;
  if (b < 60) return "还需不到 1 分钟";
  const useHour = b >= 3600;
  const unit = useHour ? " 小时" : " 分钟";
  const conv = (s) => (useHour ? s / 3600 : Math.round(s / 60));
  const fmtN = (n) => (useHour ? (n >= 10 ? String(Math.round(n)) : n.toFixed(1)) : String(n));
  const A = conv(a), B = conv(b);
  if (B - A < (useHour ? 0.2 : 1)) return "还需约 " + fmtN(B) + unit;
  return "还需约 " + fmtN(A) + "–" + fmtN(B) + unit;
}
window.__queueEtaText = _etaText;   /* 单测/自检入口 */

/* 从任意形状的状态里捞 ETA：eta_range / queue.eta_range / status.* / eta_s */
function _etaFromStatus(st) {
  if (!st || typeof st !== "object") return [NaN, NaN];
  const cands = [st.eta_range, st.queue && st.queue.eta_range,
                 st.status && st.status.eta_range, st.eta_s, st.eta_seconds,
                 st.queue_eta_s, st.queue && st.queue.eta_s];
  for (const c of cands) {
    if (Array.isArray(c)) return [Number(c[0]), Number(c[1])];
    if (typeof c === "number") return [Number.NaN, c];
    if (typeof c === "string" && c.trim()) {
      const m = c.match(/(\d+(?:\.\d+)?)\s*(?:s|秒)?(?:\s*[-~]\s*(\d+(?:\.\d+)?))?/);
      if (m) return [Number.NaN, Number(m[2] || m[1])];
    }
  }
  return [NaN, NaN];
}

window.__jobPhase = (function () {
  let phase = "idle", job = "";
  let timer = null, probed = "";
  const LABEL = { submit: "提交作业中…", queue: "云端排队中",
                  run: "计算中", download: "取回结果中…" };
  const stopProbe = () => {
    if (timer) { clearInterval(timer); timer = null; }
    probed = "";
  };
  /* 排队期轮询状态只要 ETA（8s 一次；401/网络失败立即停，不刷屏） */
  function probe(jobId, txt) {
    if (probed === jobId) return;
    stopProbe();
    probed = jobId;
    const tick = async () => {
      if (phase !== "queue") { stopProbe(); return; }
      try {
        const r = await fetch("/api/jobs/" + encodeURIComponent(jobId),
          { headers: { "X-API-Key": localStorage.getItem("genie_v2_api_key") || "" } });
        if (!r.ok) { stopProbe(); return; }
        const [lo, hi] = _etaFromStatus(await r.json());
        const t = _etaText(lo, hi);
        if (t && txt && txt.isConnected) txt.textContent = LABEL.queue + "——" + t;
      } catch { stopProbe(); }
    };
    tick();
    timer = setInterval(tick, 8000);
  }
  return function (d) {
    const chip = document.getElementById("result-chip");
    if (!chip) return;
    if (d && d.phase) phase = d.phase;
    if (d && d.job_id) job = d.job_id;
    if (!(phase in LABEL)) { stopProbe(); return; }  /* idle/exec：chip 交还摘要 */
    chip.classList.remove("hidden");
    chip.innerHTML = "";
    const wrap = document.createElement("span");
    wrap.className = "jobprog";
    wrap.title = "作业 " + (job || "?") + " · " + LABEL[phase];
    const bar = document.createElement("span");
    bar.className = "jobprog-bar ind";
    const fill = document.createElement("i");
    bar.appendChild(fill);
    const txt = document.createElement("span");
    txt.className = "jobprog-txt";
    txt.textContent = LABEL[phase];
    wrap.appendChild(bar);
    wrap.appendChild(txt);
    if (phase === "queue" && job) probe(job, txt);
    else stopProbe();
    if (phase === "queue" || phase === "run")
      wrap.appendChild(_cancelJobButton(job));
    chip.appendChild(wrap);
  };
})();

window.__jobProgress = (function () {
  let job = "", samples = [];   /* [{t, step}] */
  return function (d) {
    const chip = document.getElementById("result-chip");
    const step = Number(d && d.step), nTot = Number(d && d.n_steps);
    if (!chip || !isFinite(step) || !isFinite(nTot) || nTot <= 0) return;
    if (d.job_id && d.job_id !== job) { job = d.job_id; samples = []; }
    const now = Date.now() / 1000;
    samples.push({ t: now, step: step });
    if (samples.length > 40) samples.shift();
    const pct = Math.min(100, Math.round(step / nTot * 100));
    let eta = "";
    const s0 = samples[0];
    if (samples.length >= 2 && now - s0.t >= 5) {
      const rate = (step - s0.step) / (now - s0.t);   /* 步/s（首尾滑窗） */
      if (rate > 0) {
        const rem = (nTot - step) / rate;
        eta = " · 剩 " + (rem >= 90 ? Math.round(rem / 60) + " 分"
                                    : Math.round(rem) + " s");
      }
    } else {
      eta = " · 测速中…";
    }
    chip.classList.remove("hidden");
    chip.innerHTML = "";
    const wrap = document.createElement("span");
    wrap.className = "jobprog";
    wrap.title = "作业 " + (d.job_id || "?") + " 运行中";
    const bar = document.createElement("span");
    bar.className = "jobprog-bar";
    const fill = document.createElement("i");
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    const txt = document.createElement("span");
    txt.className = "jobprog-txt";
    txt.textContent = pct + "% · " + step + "/" + nTot + " 步" + eta;
    wrap.appendChild(bar);
    wrap.appendChild(txt);
    /* 进度态 = 作业确实在跑：撤销钮恒在（与 __jobPhase 同序位）。
     * 2026-10-25 修：这里原来引用了 __jobPhase 闭包里的 `phase`——不是本
     * 函数的变量，每个进度事件都抛 ReferenceError（真机 G82 亲验抓到，
     * 页面功能看着正常、控制台每次滚一条）；同时 cancel 钮被 append 了两次。 */
    wrap.appendChild(_cancelJobButton(d.job_id));
    chip.appendChild(wrap);
  };
})();

/* ── 极简折线图（结果标签卡曲线）：坐标轴 + 悬停读数 ──────────
 * 2026-10-10 站长实测踩坑修复：旧版 mousemove 增量叠画（连续移动=
 * 游标线/读数残影涂抹成一片），mouseleave 又 clearRect 曲线区（曲线
 * 被擦没）。改为 redraw(hover) 全量重绘闭包——每帧先清屏，游标和
 * 读数作为参数画进去，两个鼠标事件都走它，天然幂等。 */
function _plotLine(canvas, xs, ys, xLabel, yLabel) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 320, H = canvas.clientHeight || 170;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext("2d");
  const pad = { l: 56, r: 12, t: 10, b: 30 };
  const x0 = pad.l, x1 = W - pad.r, y0 = pad.t, y1 = H - pad.b;
  let yMax = 0;
  for (const v of ys) if (v > yMax) yMax = v;
  if (yMax <= 0) yMax = 1;
  const xMin = xs.length ? xs[0] : 0, xMax = xs.length ? xs[xs.length - 1] : 1;
  const X = i => x0 + (xs.length > 1 ? (xs[i] - xMin) / (xMax - xMin || 1) : 0.5) * (x1 - x0);
  const Y = v => y1 - (v / yMax) * (y1 - y0);
  const redraw = (hover) => {

    g.setTransform(dpr, 0, 0, dpr, 0, 0);   /* 重置坐标系（幂等防叠加） */
    g.clearRect(0, 0, W, H);
    g.strokeStyle = "#d0d7de"; g.lineWidth = 1;
    g.strokeRect(x0, y0, x1 - x0, y1 - y0);
    g.fillStyle = "#57606a"; g.font = "10px sans-serif"; g.textAlign = "center";
    for (let t = 0; t <= 4; t++) {
      const xv = xMin + (xMax - xMin) * t / 4;
      g.fillText(Number(xv).toExponential(1), x0 + (x1 - x0) * t / 4, y1 + 12);
      g.strokeStyle = "#eaeef2"; g.beginPath();
      g.moveTo(x0 + (x1 - x0) * t / 4, y0); g.lineTo(x0 + (x1 - x0) * t / 4, y1); g.stroke();
    }
    g.textAlign = "right";
    for (let t = 0; t <= 3; t++)
      g.fillText((yMax * t / 3).toExponential(1), x0 - 4, Y(yMax * t / 3) + 3);
    g.strokeStyle = "#1f6feb"; g.lineWidth = 1.4; g.beginPath();
    for (let i = 0; i < ys.length; i++)
      i ? g.lineTo(X(i), Y(ys[i])) : g.moveTo(X(i), Y(ys[i]));
    g.stroke();
    g.fillStyle = "#1f2328"; g.textAlign = "left";
    g.fillText(yLabel + (hover == null ? "  （悬停读数）" : ""), x0, y0 - 1);
    if (hover != null) {
      g.strokeStyle = "#d0d7de"; g.beginPath();
      g.moveTo(X(hover), y0); g.lineTo(X(hover), y1); g.stroke();
      g.fillStyle = "#1f6feb";
      g.fillRect(X(hover) - 2, Math.max(y0, Y(ys[hover]) - 2), 4, 4);
      g.fillStyle = "#1f2328"; g.textAlign = "left";
      g.fillText(`${xLabel}=${Number(xs[hover]).toExponential(3)}  ${yLabel}=${ys[hover].toExponential(3)}`,
                 x0 + 4, y0 + 12);
    }
  };
  redraw(null);
  canvas.onmousemove = (ev) => {
    const r = canvas.getBoundingClientRect();
    const mx = ev.clientX - r.left;
    if (mx < x0 || mx > x1) { redraw(null); return; }   /* 出界=回干净图 */
    redraw(Math.max(0, Math.min(ys.length - 1,
      Math.round((mx - x0) / (x1 - x0) * (ys.length - 1)))));
  };
  canvas.onmouseleave = () => redraw(null);
}
