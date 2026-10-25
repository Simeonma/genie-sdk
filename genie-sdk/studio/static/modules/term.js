/* modules/term.js — Terminal + 会话同步器（SessionSync/termExec/空闲订阅/busy 排队）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ══ Terminal（terminal 战役）：会话命令面 ═══════════════════
 * 三条命令腿同汇 /api/exec：①输入行逐条指令 ②脚本「运行」整体 exec
 * ③工具栏 Run = f.run()。输出唯一滚在这里；cfg 快照驱动
 * 表单/树/3D 刷新。SDK/agent 与 terminal 同一执行层（一步到位）。 */
let termBusy = false;
let termPollTimer = null;
const termHistory = [];
let termHistIdx = -1, termDraft = "";
/* 停止钮（A2「长任务必须能掐」）：busy 期可见，点击 = POST /api/exec/stop
 * （服务端向 worker 发 SIGINT）——⏹/done 经输出环自然滚出，本地零状态。 */
const termStopBtn = $("term-stop");
function setStopVisible(v) {
  if (termStopBtn) termStopBtn.hidden = !v;
  /* 主钮 busy 态（2026-10-14 站长裁决）：Running + 禁用（按钮不得
   * 撒谎——运行中不可点，停止走 ⏹ 钮）。非忙时**委托 runBtnRefresh**
   * 按模式定文案（此前无条件写回 "Run" 会盖掉结果态的
   * Switch to setting——0cd3c4a 引入、本笔修复的文案冲突）。 */
  const b = $("submit");
  if (b) b.disabled = v;
  const l = $("submit-label");
  if (v) { if (l) l.textContent = "Running"; }
  else runBtnRefresh();
}

/* ══ 会话同步器（2026-10-11 第一性收敛③）：GUI 全部同步状态 =
 * 两个游标（lineSeq=输出行游标 / cfgSeq=cfg 版本游标）。谁落后谁拉；
 * 投影（表单/树/3D/Plot）只订阅 onSnapshot——轮询层只剩「拉增量、
 * 喂同步器」，刷新判据不再散在闭包里。 */
const SessionSync = {
  lineSeq: 0, cfgSeq: 0, _subs: [],
  _boot: true,   /* 首拍回放窗：历史里的 job-done 只静默入结果态，不弹 toast */
  onSnapshot(fn) { this._subs.push(fn); },
  ingest(j) {                       /* j = /api/exec/output 增量响应 */
    /* 生命周期快照（2026-10 站长裁决「状态监测架构收敛」）：按钮/停止/
     * 进度条 = 会话现值的纯投影——termBusy 降级为纯提交闸门（防本页
     * 双发），显示一律不信它（外部 exec/刷新恢复都靠这里的 server 态）。 */
    if (typeof j.busy === "boolean") {
      setStopVisible(j.busy);
      if (typeof window.__jobPhase === "function")
        window.__jobPhase({ phase: j.phase || "idle", job_id: j.job_id || "" });
      if (typeof window.__mobilePhase === "function")
        window.__mobilePhase({ busy: j.busy, phase: j.phase || "idle", job_id: j.job_id || "" });
    }
    for (const l of (j.lines || [])) {
      this.lineSeq = Math.max(this.lineSeq, l.seq);
      const kind = l.kind || "stdout";   /* 兜底：旧服务窗口期无 kind */
      if (kind === "input") { renderTermInput(l); continue; }
      if (kind === "state") {
        /* 生命周期事件（worker → manager → 环）：无文本，喂进度条即可
         * （busy/按钮走上面的快照，此处只是亚 tick 内的相位细化）。 */
        if (typeof window.__jobPhase === "function")
          window.__jobPhase(l.data || {});
        continue;
      }
      if (kind === "job-progress") {
        /* 运行中步进进度（内核 runner 泵 → worker → 本事件）：不落终端
         * （防刷屏），只喂 result-chip 进度条（方案 A，2026-10-15）。 */
        if (typeof window.__jobProgress === "function")
          window.__jobProgress(l.data || {});
        if (typeof window.__mobileProgress === "function")
          window.__mobileProgress(l.data || {});
        continue;
      }
      if (kind === "job-done" && l.data) {
        /* G13② 结果事件：会话里任何键盘跑完（或失败了结）的 run 都推到
         * 本页——记 job_id、清结果缓存、刷新结果卡与已开曲线（结果=投影）。
         * 结果模式切换也挂这里（2026-10 架构收敛：废除 termLastCode
         * 正则旁证——「一次 run 尝试已了结」是事实，与谁发起、哪个页面、
         * 成败无关；失败也进结果态 = 2026-10-14 既有裁决）。 */
        if (l.data.job_id) {
          window.__lastJobId = l.data.job_id;
          window.__resultStatus = null;
        }
        if (!window.__resultMode) enterResult(SessionSync._boot);
        if (typeof window.__mobileJobDone === "function")
          window.__mobileJobDone(l.data);
        refreshResultChip();
        /* 活动页签的 Plot 重画（新数据到了）。原为 renderPlot(sp)——
         * 该函数从未存在（2026-10 重构走查补漏：__plots 非空 + job-done
         * 即 ReferenceError 炸 ingest）。 */
        const act = (window.__plots || []).find(p => p.id === window.__activeTab);
        if (act) {
          const pv = document.getElementById("plot-view");
          if (pv && !pv.classList.contains("hidden")) renderPlotInto(pv, act);
        }
      }
      termAppend(l.text, kindClass(kind));
    }
    if (j.cfg && j.cfg_seq !== this.cfgSeq) {
      this.cfgSeq = j.cfg_seq;
      for (const fn of this._subs)
        fn(j.cfg, j.resolved, j.cfg_seq, j.origin, j.paths);
    }
    if (this._boot) this._boot = false;   /* 首拍=历史回放：job-done 静默入态 */
  },
};

/* kind → 着色类（环条目结构化 2026-10-11）：报错/进程级 stderr 红、
 * 管理 note 灰；stdout 与其余不着色。input 不走这里（特殊处理）。 */
function kindClass(kind) {
  if (kind === "error" || kind === "stderr") return "term-err";
  if (kind === "note") return "term-note";
  if (kind === "job") return "term-job";   /* 运行阶段提示（提交/运行中/下载） */
  return null;
}

/* input 行渲染（VS Code 式 echo 的服务端形态）：mark 非空先打一行来源
 * 标注（term-note），随后 "❯ " 前缀 + 原文（两个 span，与旧客户端 echo
 * 同款 DOM——标注来源时前缀 ❯⋯，视觉与旧 echoMark 通道一致）。不建
 * DOM 容器防过度工程：时序即块；环裁剪致首块无头 input 照常平铺。 */
function renderTermInput(l) {
  const out = $("term-out");
  if (!out) return;
  termCloseBlock();          /* 命令回显 = 上一段输出的封口 */
  if (l.mark) termAppend("⋯ " + l.mark + "\n", "term-note");
  const sym = document.createElement("span");
  sym.className = "term-prompt-mark";
  sym.textContent = l.mark ? "❯⋯ " : "❯ ";
  const tx = document.createElement("span");
  tx.textContent = l.text + "\n";
  out.appendChild(sym);
  out.appendChild(tx);
  termQueueScroll();   /* 与 termAppend 同一 rAF 合批（逐行强读 = 逐行 reflow）*/
}

/* 终端滚动合批：一帧至多一次强读排版（大环回放的主线程杀手） */
function termQueueScroll() {
  if (_termScrollQueued) return;
  _termScrollQueued = true;
  requestAnimationFrame(() => {
    _termScrollQueued = false;
    const dock = $("term-dock");
    if (dock) dock.scrollTop = dock.scrollHeight;
  });
}

let _termScrollQueued = false;

/* ── 长输出折叠（G45）──
 * 终端是 append-only 的流；跑一次仿真会灌几百行 stdout，把上面的
 * 命令/报错顶出视野。策略：把**连续的无着色 stdout 行**攒成一块
 * `.term-block`；块内超过 FOLD_AT 行时，只留头 FOLD_HEAD 行可见，
 * 其余塞进 `.term-block-body`（默认收起），并插一个「展开 N 行」按钮。
 * 非 stdout（note/job/err/input）一律先把当前块封口——折叠块不跨语义。 */
const FOLD_AT = 40;
const FOLD_HEAD = 3;      /* 终端面板常常只有 ~6 行高：留 3 行就够看出上下文，
                           * 再多会把「展开」按钮顶到可视区外 */
let _termBlock = null;      /* {el, body, btn, lines} 当前 stdout 块 */

function termCloseBlock() {
  _termBlock = null;
}

function termAppendFoldable(text) {
  const out = $("term-out");
  if (!out) return;
  if (!_termBlock) {
    const el = document.createElement("div");
    el.className = "term-block";
    const body = document.createElement("div");
    body.className = "term-block-body";
    el.appendChild(body);
    out.appendChild(el);
    _termBlock = { el, body, btn: null, fold: null, lines: 0 };
  }
  const b = _termBlock;
  const line = document.createElement("span");
  line.textContent = text;
  b.lines += 1;
  if (b.fold) {
    /* 已折叠：新行直接进折叠体（展开时能看到全部），收着时刷新计数 */
    b.fold.appendChild(line);
    if (b.fold.classList.contains("hidden")) {
      b.btn.textContent = "▸ 展开其余 " + b.fold.children.length + " 行";
    }
    termQueueScroll();
    return;
  }
  b.body.appendChild(line);
  if (b.lines === FOLD_AT) {
    /* 到阈值：第 FOLD_HEAD 行之后的全收进折叠体，并给一个开关 */
    const fold = document.createElement("div");
    fold.className = "term-fold hidden";
    while (b.body.children.length > FOLD_HEAD) {
      fold.appendChild(b.body.children[FOLD_HEAD]);
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "term-fold-btn";
    const setLabel = (open) => {
      btn.textContent = open
        ? "▾ 收起输出"
        : "▸ 展开其余 " + fold.children.length + " 行";
      btn.setAttribute("aria-expanded", String(open));
    };
    setLabel(false);
    btn.addEventListener("click", () => {
      const open = fold.classList.toggle("hidden") === false;
      setLabel(open);
      termQueueScroll();
    });
    b.body.appendChild(fold);
    b.body.appendChild(btn);
    b.btn = btn;
    b.fold = fold;
  }
  termQueueScroll();
}

function termAppend(text, cls) {
  const out = $("term-out");
  if (!out || !text) return;
  if (!cls) {                     /* 无着色 = stdout → 可折叠流 */
    termAppendFoldable(text);
    return;
  }
  termCloseBlock();
  const sp = document.createElement("span");
  sp.className = cls;
  sp.textContent = text;
  out.appendChild(sp);
  termQueueScroll();
}

/* ── 两个执行入口，一条流程（M3）────────────────────────────────
 * termExec(code)  = 敲代码（mode=run：在当前会话命名空间里执行）
 * runScript()     = 脚本运行（mode=build：构造新模型，成功才替换）
 * runModel()      = 提交当前模型（POST /api/run：**不改模型**）
 *
 * 以前工具栏 Run 是 `termExec("f.run()")`——把一个按钮的意图写成另一
 * 个界面里的文本，于是它依赖"命名空间里恰好有个没被换掉的 f"。现在
 * 它是一个独立的意图，走自己的端点。 */
async function _postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(apiFailText(r, j));
  return j;
}

/* 执行主流程：排队 → 置忙 → flush 未决表单编辑 → 提交 → 轮询到 done。
 * 排队而非丢弃（G13①）：worker 先发 cfg 快照后发 done，丢弃语义曾静默
 * 吞命令；kernel 本就串行，单槽排队即正确语义（Jupyter 同款）。 */
async function _execFlow(label, submit) {
  if (termBusy) {
    _pendingExec = { label, submit };
    termAppend("… 上一段还在执行——已排队，跑完自动运行\n", "term-note");
    return;
  }
  termBusy = true;   /* 同步置位（防连点竞态）：置位前不得有任何 await */
  await flushPendingCfgPush();
  try {
    await submit();
  } catch (e) {
    termAppend("✗ " + (e && e.message ? e.message : e) + "\n", "term-err");
    termBusy = false;
    setStopVisible(false);
    return;
  }
  _startPolling();
}

function termExec(code, opts = {}) {
  code = code || "";
  if (!code.trim()) { setStopVisible(false); return; }
  return _execFlow("exec", () => _postJSON("/api/exec", {
    code, mode: opts.mode || "run", label: opts.label || "" }));
}

/** 提交当前会话模型（工具栏 Run）。**不改模型**，也不经终端命名空间。 */
function runModel() {
  return _execFlow("run", () => _postJSON("/api/run", {}));
}

function _startPolling() {
  if (termPollTimer) clearInterval(termPollTimer);
  termPollTimer = setInterval(async () => {
    let r, j;
    try {
      r = await fetch("/api/exec/output?since=" + SessionSync.lineSeq);
      j = await r.json();
    } catch { return; }   /* 网络抖动：留下个 tick 再试 */
    if (!r.ok) {
      /* 输出通道断了：显性报错并停轮。绝不静默——静默 = cfg 不回流、
       * GUI 不同步还看似正常。措辞走统一出口（O2）。 */
      termAppend("✗ 输出通道 " + apiFailed("读取输出", r, j) + "\n", "term-err");
      clearInterval(termPollTimer); termPollTimer = null; termBusy = false;
      setStopVisible(false);
      return;
    }
    SessionSync.ingest(j);          /* 拉增量、喂同步器——投影自己订阅 */
    setStopVisible(!!j.busy);       /* 停止钮可见性 = 会话忙闲（轮询响应驱动） */
    /* 完成判据（O2 收敛为一句话）：**会话不忙了** = 我这条 op 回了 done。
     * busy 由在途 op 账本投影（kernel_session._inflight），不再有
     * "另一条 done 也算我完成"的旁路——那条旁路依赖环里出现 done 条目，
     * 而它从来没出现过（死代码，G71 顺手删除）。 */
    if (!j.busy) {
      clearInterval(termPollTimer);
      termPollTimer = null;
      termBusy = false;
      /* 结果模式切换已上收 job-done 事件（ingest 内）——这里不再
       * 正则猜 termLastCode（2026-10 架构收敛：跑没跑仿真看事实，
       * 不看提交文本长什么样）。 */
      if (_pendingExec) {                            /* 排队命令自动续跑 */
        const p = _pendingExec; _pendingExec = null;
        _execFlow(p.label, p.submit);
      }
    }
  }, 350);
}

/* cfg 快照 → 全视图刷新（表单是 f 的只读投影；不重置用户选中位）
 *
 * M3b：快照分两类——
 *   变更型（exec/build/restore/attach/replace/load）：替换本地缓冲 + 整面板重渲；
 *   回声型（patch）：只刷新"服务端已知状态"（patch 的 diff 基准），
 *                   **不动编辑缓冲、不重渲染**——本地已经是对的。
 * 这一条就是"敲字段时面板被重建、焦点被抽走"那个老毛病的根治点。 */
/* 把**别人**写进来的路径同步进本地编辑缓冲（M3b 双标签修正）。
 *
 * 规则：本地有未提交编辑的路径 → 本地优先（最后写入者生效，与文档一致）；
 * 其余的照抄服务端。少了这一步，第二个标签会因为"回声被跳过"而停在旧值，
 * 它下一次编辑求出的 diff 会把别人刚改的字段**一起打回去**。 */
function _mergeForeignPaths(serverCfg, paths) {
  if (!cfg || typeof cfg !== "object") return false;
  let dirty = [];
  try { dirty = diffCfg(_serverCfg || {}, cfg).map((o) => o.path); }
  catch { dirty = []; }
  const dirtySet = new Set(dirty);
  let touched = false;
  for (const p of paths || []) {
    if (!p) continue;
    if (dirtySet.has(p)) continue;              /* 我手上还没提交的，优先 */
    if (dirty.some((d) => d.startsWith(p + ".") || p.startsWith(d + ".")))
      continue;
    let v;
    try { v = getPath(serverCfg, p); } catch { continue; }
    try { setPath(cfg, p, v); touched = true; } catch { /* 定位不到就跳过 */ }
  }
  if (!touched) return false;
  displayCfg = cfg;
  FormPanel.setCfg(cfg);
  renderTree();          /* 名字/颜色/几何可能变了；不碰检查器（焦点在里面） */
  updateScene();
  scheduleValidate();
  return true;
}

function applyCfgSnapshot(c, resolved, seq, origin, paths) {
  if (!c) return;
  /* 深拷贝：表单是**原地改** cfg 的，若 _serverCfg 与 cfg 指向同一个
   * 对象，diff 永远是空（这是 patch 通道最容易踩的坑）。 */
  const snap = (typeof structuredClone === "function")
    ? structuredClone(c) : JSON.parse(JSON.stringify(c));
  if (origin === "reject") {
    /* 服务端**拒了**这批改动（一批 op 全对或全错）。必须把本地那份按服务端
     * 拉回来：否则那个非法值会一直留在表单里，之后每次提交都带上它、每次
     * 都整批失败——表单从此一个字也存不进去。 */
    cfg = c;
    _serverCfg = snap;
    displayCfg = resolved || cfg;
    FormPanel.setCfg(cfg);
    renderTree();
    renderInspector();
    updateScene();
    scheduleValidate();
    apiNotice("改动没被接受，已还原（原因见终端）", "err");
    return;
  }
  /* 回声抑制仅当本地已有编辑缓冲才成立：刚加载的页面 cfg 还是 null，
   * origin=patch 的首拍快照是初始模型而非回声——须走下方整份应用，
   * 否则刷新后界面永远空模型（会话模型其实是好的）。 */
  if (origin === "patch" && cfg) {
    _mergeForeignPaths(c, paths);   /* 只并入"不是我改的"那些路径 */
    _serverCfg = snap;              /* 刷新 diff 基准，不重建面板 */
    return;
  }
  _serverCfg = snap;
  cfg = c;
  displayCfg = resolved || cfg;   /* 表达式槽→数值副本（3D 渲染用），cfg 原文不动 */
  FormPanel.setCfg(cfg);
  renderTree();
  renderInspector();
  updateScene();
  /* r12：会话配置到账（恢复/外部编辑）→ 复评 3D 页空态覆盖层
   * （mobile.js 判定吃 getCfg()，配置非空 = 有场景 = 撤覆盖层） */
  if (typeof window.__mobileSync3dEmpty === "function")
    window.__mobileSync3dEmpty();
}

SessionSync.onSnapshot(applyCfgSnapshot);   /* 投影订阅同步器（收敛③） */

/* ── 空闲持续订阅（G13①）：GUI 是会话的投影，不是"执行期才睁眼的
 * 观察者"。exec 期间 termExec 的 350ms 轮询在跑；空闲期本 2s 节拍
 * 接力——同一 since 增量机制，两种节奏。外部键盘（console/另一窗口）
 * 的编辑与输出由此流进本页（含输入 echo）。────────────────────── */
setInterval(async () => {
  if (termBusy || termPollTimer) return;   /* exec 轮询在管，不抢线 */
  if (!genieToken()) return;               /* 未登录：服务端必挡，别刷 401 */
  try {
    const r = await fetch("/api/exec/output?since=" + SessionSync.lineSeq);
    if (!r.ok) {
      if (r.status >= 500) {               /* 会话掉了：说一次（6s 去重） */
        const j = await r.json().catch(() => ({}));
        apiFailed("会话", r, j);
      }
      return;                              /* 401 = 未登录，正常静默 */
    }
    SessionSync.ingest(await r.json());
  } catch { /* 网络抖动：下个节拍再试 */ }
}, 2000);

(() => {   /* 输入行：Enter 执行 · ↑↓ 历史 · 点面即聚焦（VS Code 手感） */
  const inp = $("term-in");
  if (!inp) return;
  const dock = $("term-dock");
  if (dock) dock.addEventListener("click", () => {
    if (!window.getSelection().toString()) inp.focus();
  });
  /* r12：移动模式输入框自适应高度（min 36px，封顶 4 行 ~96px，超出内部
   * 滚动）。桌面零改动——只在 body.mobile-mode 下写 inline height。 */
  const fitInput = () => {
    if (!document.body.classList.contains("mobile-mode")) { inp.style.height = ""; return; }
    inp.style.height = "auto";
    inp.style.height = Math.min(Math.max(inp.scrollHeight, 36), 96) + "px";
    inp.style.overflowY = inp.scrollHeight > 96 ? "auto" : "hidden";
  };
  inp.addEventListener("input", fitInput);
  window.__termFitInput = fitInput;   /* 回填 chips / 模式切换后复评 */
  /* r12：横向「历史 chips」条（#term-chips，样式只在移动媒体块显形）。
   * 数据源 = 既有 termHistory；渲染最近 5 条，点击回填输入框。 */
  const chipsBar = $("term-chips") || (() => {
    const row = $("term-in-row"), card = $("term-card");
    if (!row || !card) return null;
    const bar = document.createElement("div");
    bar.id = "term-chips";
    card.insertBefore(bar, row);
    return bar;
  })();
  function renderTermChips() {
    if (!chipsBar) return;
    chipsBar.innerHTML = "";
    termHistory.slice(-5).forEach((code) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "m-term-chip";
      b.textContent = code.length > 24 ? code.slice(0, 24) + "…" : code;
      b.title = code;
      b.addEventListener("click", (e) => {
        e.stopPropagation();   /* dock 点击有聚焦逻辑，不被抢焦点 */
        inp.value = code;
        if (typeof window.__termFitInput === "function") window.__termFitInput();
        inp.focus();
      });
      chipsBar.appendChild(b);
    });
  }
  window.__termRenderChips = renderTermChips;
  const submit = () => {
    const code = inp.value;
    if (!code.trim()) return;
    inp.value = "";
    fitInput();
    termHistIdx = -1; termDraft = "";
    if (termHistory[termHistory.length - 1] !== code) termHistory.push(code);
    renderTermChips();
    termExec(code);
    inp.focus();
  };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    else if (e.key === "ArrowUp") {
      if (!termHistory.length) return;
      if (termHistIdx === -1) { termDraft = inp.value; termHistIdx = termHistory.length - 1; }
      else if (termHistIdx > 0) termHistIdx -= 1;
      inp.value = termHistory[termHistIdx];
    } else if (e.key === "ArrowDown") {
      if (termHistIdx === -1) return;
      termHistIdx += 1;
      if (termHistIdx >= termHistory.length) { termHistIdx = -1; inp.value = termDraft; }
      else inp.value = termHistory[termHistIdx];
    }
  });
})();

/* 停止当前执行（A2）：即时动作、无本地状态变更——worker 的 ⏹ 提示与
 * done 由输出环自然滚出并收尾（busy 翻 false 后停止钮随轮询自隐）。
 * stopPropagation：dock 点击有聚焦逻辑，点停止钮不把焦点抢回输入行。 */
$("term-stop").addEventListener("click", (e) => {
  e.stopPropagation();
  fetch("/api/exec/stop", { method: "POST" })
    .catch(() => {});   /* 网络失败静默：下一轮询 tick 仍可再点 */
});

