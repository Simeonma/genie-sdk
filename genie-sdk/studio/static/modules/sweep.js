/* modules/sweep.js — 扫参页（G182 P6 / G4a 剩件；2026-10-30）
 *
 * 形态（站长定稿）：参数页底部 Parameter Sweep → 中间栏**扫参页**；每条
 * 轴 = 一个参数 + 一组取值（线性 / 对数(10^x) / 显式列表），总点数 =
 * 各轴点数之积。参数页本身保持"单次仿真的单值表"，不做内联扫参。
 *
 * 数据链（零新概念）：
 *   Run  → POST /api/sweep → worker `f.sweep(axes).wait()`（与终端同一条
 *          原语）；进度吃既有 `sweep-progress` 事件（term.js 转喂
 *          __sweepProgress，本页在它旁边再吃一份）。
 *   Plot → POST /api/sweep/plot → worker 把最近一次扫描的**合并结果**
 *          （数据多 k 维）落会话结果目录并发 `plot` 事件——面板零改动：
 *          参数轴就是轴分配表里的普通一行（freqs × 第一个参数 = 二维图，
 *          其余参数 = 切片/滑条）。
 *
 * 依赖的运行期全局：cfg / $ / toast / inspectorSel / renderInspector /
 * selectObj / apiFailText（经典 script 按序加载，见 index.html）。 */
"use strict";

const SWEEP_AXIS_LIMIT = 32;      /* 协议面：≤32 轴 */
const SWEEP_POINT_LIMIT = 512;    /* 协议面：缺省 ≤512 点 */

const sweepState = {
  axes: [],
  maxParallel: 1,
  onFailure: "continue",
  plotMon: "",
  running: false,
  sweepId: "",
  status: "",
  _runBtn: null, _plotBtn: null, _statusEl: null, _totalEl: null,
};

function _sweepNewAxis() {
  return { name: "", mode: "linspace", a: "", b: "", n: "5", values: "" };
}

/* 参数页底部入口：进扫参页 */
function openSweepPane() {
  if (!sweepState.axes.length) sweepState.axes = [_sweepNewAxis()];
  inspectorSel = { kind: "sweep", i: null };
  renderInspector();
}

function _sweepLab(text, cls) {
  const el = document.createElement("span");
  el.className = cls || "flabel";
  el.textContent = text;
  return el;
}

/* 轴 → f.sweep 的轴规格；不完整 → null（页面本地先拦，错误远离原因） */
function sweepAxisPayload(a) {
  if (!a || !a.name) return null;
  if (a.mode === "values") {
    const vals = String(a.values || "").split(/[,;\s]+/)
      .map(s => s.trim()).filter(Boolean).map(Number);
    if (!vals.length || vals.some(v => !Number.isFinite(v))) return null;
    return { name: a.name, values: vals };
  }
  const x = Number(a.a), y = Number(a.b), n = parseInt(a.n, 10);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(n)) return null;
  if (n < 2 || x === y) return null;
  return a.mode === "logspace"
    ? { name: a.name, logspace: [x, y, n] }
    : { name: a.name, linspace: [x, y, n] };
}

function sweepAxisCount(a) {
  const p = sweepAxisPayload(a);
  if (!p) return 0;
  return p.values ? p.values.length : (p.linspace || p.logspace)[2];
}

function sweepTotal() {
  let t = 1;
  for (const a of sweepState.axes) {
    const n = sweepAxisCount(a);
    if (!n) return 0;
    t *= n;
  }
  return t;
}

function sweepUpdateTotal() {
  const el = sweepState._totalEl;
  if (!el) return;
  const total = sweepTotal();
  el.textContent = total ? String(total) : "—";
  el.classList.toggle("over", total > SWEEP_POINT_LIMIT);
}

function sweepUpdateControls() {
  const run = sweepState._runBtn, plot = sweepState._plotBtn,
        st = sweepState._statusEl;
  if (run) {
    run.disabled = sweepState.running;
    run.textContent = sweepState.running ? "… 扫描中" : "▶ Run";
  }
  if (plot) plot.disabled = sweepState.running || !sweepState.sweepId;
  if (st) st.textContent = sweepState.status || "";
}

/* 事件入口（result.js 的 __sweepProgress 先喂这里一份） */
function sweepPaneEvent(d) {
  if (!d || typeof d !== "object") return;
  if (d.error) {                        /* 根本没跑起来（规格/模型问题） */
    sweepState.running = false;
    sweepState.status = "✗ " + d.error;
    sweepUpdateControls();
    return;
  }
  if (!d.sweep_id) return;
  const total = Number(d.total) || 0;
  const r = d.rollup || {};
  const pending = (r.pending || 0) + (r.running || 0);
  const finished = Math.max(0, total - pending);
  const bad = Math.max(0, finished - (r.succeeded || 0));
  const terminal = d.state && d.state !== "running" && d.state !== "pending";
  /* 只有"有成功点"的扫描才值得 Plot（全失败 → 立方没有一片真数据；
     但跑完后从历史回放里恢复"最近一次可画"的扫描是想要的行为）。 */
  if (!terminal || (r.succeeded || 0) > 0) sweepState.sweepId = d.sweep_id;
  if (terminal) {
    sweepState.running = false;
    sweepState.status = (d.state === "done" ? "✓ 完成 " : "✗ 已中止 ")
      + finished + "/" + total + (bad ? " · 失败 " + bad : "");
  } else {
    sweepState.running = true;
    sweepState.status = "扫描中 " + finished + "/" + total
      + (bad ? " · 失败 " + bad : "");
  }
  sweepUpdateControls();
}
window.__sweepPaneEvent = sweepPaneEvent;

async function sweepRun() {
  if (sweepState.running) return;
  if (!Object.keys((cfg && cfg.parameters) || {}).length) {
    toast("参数表是空的——先在参数页加一个参数", "warn");
    return;
  }
  const used = new Set();
  for (const a of sweepState.axes) {
    if (used.has(a.name)) {
      toast("参数「" + a.name + "」被两条轴同时用了——一条轴扫一个参数", "warn");
      return;
    }
    used.add(a.name);
  }
  const axes = [];
  for (const a of sweepState.axes) {
    const p = sweepAxisPayload(a);
    if (!p) {
      toast("轴不完整：选参数并填好取值（对数轴填 10 的指数）", "warn");
      return;
    }
    axes.push(p);
  }
  const total = sweepTotal();
  if (!total) { toast("总点数为 0——检查各轴取值", "warn"); return; }
  if (total > SWEEP_POINT_LIMIT) {
    toast("总点数 " + total + " 超过上限 " + SWEEP_POINT_LIMIT, "warn");
    return;
  }
  sweepState.running = true;
  sweepState.sweepId = "";
  sweepState.status = "提交扫描…";
  sweepUpdateControls();
  try {
    const r = await fetch("/api/sweep", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ axes,
                             max_parallel: sweepState.maxParallel,
                             on_failure: sweepState.onFailure }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiFailText(r, j));
    sweepState.status = "扫描中 0/" + total;
  } catch (e) {
    sweepState.running = false;
    sweepState.status = "✗ " + (e.message || e);
    toast("扫描提交失败：" + (e.message || e), "err");
  }
  sweepUpdateControls();
}

async function sweepPlot() {
  const mons = ((cfg && cfg.monitor) || [])
    .map(m => m && m.name).filter(Boolean);
  if (!mons.length) { toast("模型里没有监视器——加一个再扫", "warn"); return; }
  const mon = sweepState.plotMon && mons.includes(sweepState.plotMon)
    ? sweepState.plotMon : mons[0];
  sweepState.status = "推送到 Plot…";
  sweepUpdateControls();
  try {
    const r = await fetch("/api/sweep/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mon }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiFailText(r, j));
    sweepState.status = "✓ 已在 Plot 面板打开（参数轴在轴分配表里）";
  } catch (e) {
    sweepState.status = "✗ " + (e.message || e);
    toast("Plot 失败：" + (e.message || e), "err");
  }
  sweepUpdateControls();
}

function _sweepAxisRow(i, names) {
  const a = sweepState.axes[i];
  const box = document.createElement("div");
  box.className = "sweep-axis";

  /* 头行：参数 + 方式 + 删除。中栏只有 ~315px，轴行必须分两行放——
   * 单行塞「参数 + 方式 + 三个取值输入」必然横向溢出，浏览器一聚焦
   * 输入就把容器滚右，左边的 select 被裁掉（2026-10-30 截图实证）。 */
  const head = document.createElement("div");
  head.className = "frow sweep-axis-head";

  const sel = document.createElement("select");
  sel.className = "sweep-param";
  sel.title = "要扫描的参数";
  const ph = document.createElement("option");
  ph.value = ""; ph.textContent = "参数…";
  sel.appendChild(ph);
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  sel.value = a.name;
  sel.addEventListener("change", () => { a.name = sel.value; });
  head.appendChild(sel);

  const mode = document.createElement("select");
  mode.className = "sweep-mode";
  mode.title = "取值方式：线性 / 对数(10 的指数) / 显式列表";
  [["linspace", "线性"], ["logspace", "对数"], ["values", "列表"]]
    .forEach(([v, l]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = l;
      mode.appendChild(o);
    });
  mode.value = a.mode;
  mode.addEventListener("change", () => {
    a.mode = mode.value;
    renderSweepPane();      /* 取值控件形态变了：整页重建（同一份 state） */
  });
  head.appendChild(mode);

  const del = document.createElement("button");
  del.className = "mini sweep-del";
  del.textContent = "✕";
  del.title = "删掉这条轴";
  del.addEventListener("click", () => {
    sweepState.axes.splice(i, 1);
    if (!sweepState.axes.length) sweepState.axes = [_sweepNewAxis()];
    renderSweepPane();
  });
  head.appendChild(del);
  box.appendChild(head);

  /* 取值行：独立一行，输入占满，不挤 select */
  const vals = document.createElement("div");
  vals.className = "frow sweep-axis-vals";
  if (a.mode === "values") {
    const inp = document.createElement("input");
    inp.className = "param-value sweep-values";
    inp.placeholder = "1e-7, 2e-7, 3e-7";
    inp.value = a.values;
    inp.addEventListener("input", () => {
      a.values = inp.value;
      sweepUpdateTotal();
    });
    vals.appendChild(inp);
  } else {
    const mk = (key, placeholder) => {
      const inp = document.createElement("input");
      inp.className = "param-value sweep-num";
      inp.placeholder = placeholder;
      inp.title = placeholder;
      inp.value = a[key];
      inp.addEventListener("input", () => {
        a[key] = inp.value;
        sweepUpdateTotal();
      });
      return inp;
    };
    const log = a.mode === "logspace";
    vals.appendChild(mk("a", log ? "起(指数)" : "起点"));
    vals.appendChild(mk("b", log ? "止(指数)" : "终点"));
    vals.appendChild(mk("n", "点数"));
  }
  box.appendChild(vals);
  return box;
}

function renderSweepPane() {
  const c = $("config-content");
  if (!c) return;
  c.textContent = "";
  const body = document.createElement("div");
  body.className = "pane-body sweep-page";
  c.appendChild(body);

  const head = document.createElement("div");
  head.className = "sweep-head";
  const back = document.createElement("button");
  back.className = "mini";
  back.textContent = "← 参数";
  back.title = "返回参数表";
  back.addEventListener("click", () => selectObj("params"));
  head.appendChild(back);
  const title = document.createElement("span");
  title.className = "sweep-title";
  title.textContent = "参数扫描";
  head.appendChild(title);
  body.appendChild(head);

  const note = document.createElement("div");
  note.className = "pane-note";
  note.textContent = "每条轴 = 一个参数 + 一组取值；总点数 = 各轴点数相乘"
    + "（≤" + SWEEP_POINT_LIMIT + "，轴 ≤" + SWEEP_AXIS_LIMIT + "）。"
    + "参数页保持单值表，扫描是独立一副手套。";
  body.appendChild(note);

  const names = Object.keys((cfg && cfg.parameters) || {});
  if (!names.length) {
    const warn = document.createElement("div");
    warn.className = "pane-note sweep-warn";
    warn.textContent = "参数表是空的——先回参数页加一个参数，否则没有可扫的轴。";
    body.appendChild(warn);
  }
  /* 换模型后旧轴名可能已不存在：清掉——否则下拉落成空白（选中项不在
   * 选项里 = selectedIndex -1），点了 Run 还必然在服务端报轴名不存在。 */
  for (const a of sweepState.axes)
    if (a.name && !names.includes(a.name)) a.name = "";
  for (let i = 0; i < sweepState.axes.length; i++)
    body.appendChild(_sweepAxisRow(i, names));

  const addRow = document.createElement("div");
  addRow.className = "frow";
  const addBtn = document.createElement("button");
  addBtn.className = "mini";
  addBtn.textContent = "＋ 再加一条轴";
  addBtn.addEventListener("click", () => {
    if (sweepState.axes.length >= SWEEP_AXIS_LIMIT) {
      toast("轴数上限 " + SWEEP_AXIS_LIMIT, "warn");
      return;
    }
    sweepState.axes.push(_sweepNewAxis());
    renderSweepPane();
  });
  addRow.appendChild(addBtn);
  body.appendChild(addRow);

  const totalRow = document.createElement("div");
  totalRow.className = "frow sweep-total-row";
  totalRow.appendChild(_sweepLab("总点数"));
  const totalEl = document.createElement("span");
  totalEl.className = "sweep-total";
  totalRow.appendChild(totalEl);
  body.appendChild(totalRow);

  const optRow = document.createElement("div");
  optRow.className = "frow";
  optRow.appendChild(_sweepLab("并发", "sweep-lab"));
  const conc = document.createElement("select");
  conc.className = "param-value sweep-opt";
  for (const n of [1, 2, 3, 4]) {
    const o = document.createElement("option");
    o.value = String(n); o.textContent = String(n);
    conc.appendChild(o);
  }
  conc.value = String(sweepState.maxParallel || 1);
  conc.addEventListener("change", () => { sweepState.maxParallel = +conc.value; });
  optRow.appendChild(conc);
  optRow.appendChild(_sweepLab("失败", "sweep-lab"));
  const fail = document.createElement("select");
  fail.className = "param-value sweep-opt";
  [["continue", "继续跑"], ["abort", "首败即停"]].forEach(([v, l]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = l;
    fail.appendChild(o);
  });
  fail.value = sweepState.onFailure;
  fail.addEventListener("change", () => { sweepState.onFailure = fail.value; });
  optRow.appendChild(fail);
  body.appendChild(optRow);

  const act = document.createElement("div");
  act.className = "sweep-actions";
  const run = document.createElement("button");
  run.className = "mini sweep-run";
  run.textContent = "▶ Run";
  run.addEventListener("click", sweepRun);
  act.appendChild(run);
  const status = document.createElement("span");
  status.className = "sweep-status";
  act.appendChild(status);
  body.appendChild(act);

  const plotRow = document.createElement("div");
  plotRow.className = "sweep-plot-row";
  const mons = ((cfg && cfg.monitor) || [])
    .map(m => m && m.name).filter(Boolean);
  if (mons.length > 1) {
    const ms = document.createElement("select");
    ms.className = "param-value sweep-mon";
    for (const n of mons) {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      ms.appendChild(o);
    }
    ms.value = mons.includes(sweepState.plotMon) ? sweepState.plotMon : mons[0];
    sweepState.plotMon = ms.value;
    ms.addEventListener("change", () => { sweepState.plotMon = ms.value; });
    plotRow.appendChild(ms);
  }
  const plot = document.createElement("button");
  plot.className = "mini sweep-plot";
  plot.textContent = "📈 Plot";
  plot.title = "把这次扫描的结果推到 Plot 面板（和单跑同一个面板）";
  plot.addEventListener("click", sweepPlot);
  plotRow.appendChild(plot);
  body.appendChild(plotRow);

  sweepState._totalEl = totalEl;
  sweepState._runBtn = run;
  sweepState._plotBtn = plot;
  sweepState._statusEl = status;
  sweepUpdateTotal();
  sweepUpdateControls();
}
