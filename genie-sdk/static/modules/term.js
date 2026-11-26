
import { enterResult, jobPhase, jobProgress, refreshResultChip, resultMode,
         setLastJobId, setResultStatus, sweepProgress } from "./result.js";

import { $, guiViewOf } from "./util.js";
import { apiNotice } from "./api.js";
import { exec } from "../core/bus.js";
import { pollOnce } from "../core/ring.js";
import { cancel, restart } from "./app.js";
import { FormPanel } from "../views/form.js";
import { renderInspector } from "./inspector.js";
import { updateScene } from "./scene.js";
import { cfg, setCfg, setDisplayCfg, toast } from "./state.js";
import { runBtnRefresh } from "./toolbar.js";
import { renderTree } from "./tree.js";
/* modules/term.js — Terminal + 模型同步器（SessionSync/termExec/空闲订阅/busy 排队）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ══ Terminal（terminal 战役）：worker 命令面 ═══════════════════
 * 三条命令腿同汇 /api/exec：①输入行逐条指令 ②脚本「运行」整体 exec
 * ③工具栏 Run = genie.run()。输出唯一滚在这里；cfg 快照驱动
 * 表单/树/3D 刷新。SDK/agent 与 terminal 同一执行层（一步到位）。 */
export let termBusy = false;
export let termPollTimer = null;
export const termHistory = [];
export let termHistIdx = -1, termDraft = "";
/* 停止钮（A2「长任务必须能掐」）：busy 期可见，点击 = POST /api/exec/stop
 * （服务端向 worker 发 SIGINT）——⏹/done 经输出环自然滚出，本地零状态。 */
export const termStopBtn = $("term-stop");
export const termResetBtn = $("term-reset");
export function setStopVisible(v) {
  if (termStopBtn) termStopBtn.hidden = !v;
  /* R31：清理重启钮与停止钮同显同隐（busy 期可见）——死循环卡 exec
   * 时 busy 恒 true，两钮都在场；先试 ⏹，投不进（无控制台）再 ↻。 */
  if (termResetBtn) termResetBtn.hidden = !v;
  const b = $("submit");
  if (b) b.disabled = v;
  const l = $("submit-label");
  if (v) { if (l) l.textContent = "Running"; }
  else runBtnRefresh();
}

export const SessionSync = {
  lineSeq: 0, cfgSeq: 0, _subs: [],
  _boot: true,   /* 首拍回放窗：历史里的 job-done 只静默入结果态，不弹 toast */
  onSnapshot(fn) { this._subs.push(fn); },
  ingest(j) {                       /* j = /api/exec/output 增量响应 */
    /* 生命周期快照（2026-10 站长裁决「状态监测架构收敛」）：按钮/停止/
     * 进度条 = worker 现值的纯投影——termBusy 降级为纯提交闸门（防本页
     * 双发），显示一律不信它（外部 exec/刷新恢复都靠这里的 server 态）。 */
    /* 脚本（配方）：worker 里那份变了（agent 改的 / 另一处改的）→ 回填编辑器。
     * 用户正在编辑器里敲字时不抢（焦点保护同 live.js 的纪律）。 */
    if (typeof j.script === "string") {
      const ed = document.getElementById("code-editor");
      if (ed && document.activeElement !== ed && ed.value !== j.script) {
        ed.value = j.script;
        if (typeof hlRefresh === "function") hlRefresh();
      }
    }
    if (typeof j.busy === "boolean") {
      setStopVisible(j.busy);
      jobPhase({ phase: j.phase || "idle", job_id: j.job_id || "" });
    }
    for (const l of (j.lines || [])) {
      this.lineSeq = Math.max(this.lineSeq, l.seq);
      const kind = l.kind || "stdout";   /* 兜底：旧服务窗口期无 kind */
      if (kind === "input") { renderTermInput(l); continue; }
      if (kind === "state") {
        /* 生命周期事件（worker → manager → 环）：无文本，喂进度条即可
         * （busy/按钮走上面的快照，此处只是亚 tick 内的相位细化）。 */
        jobPhase(l.data || {});
        continue;
      }
      if (kind === "job-progress") {
        /* 运行中步进进度（内核 runner 泵 → worker → 本事件）：不落终端
         * （防刷屏），只喂 result-chip 进度条（方案 A，2026-10-15）。 */
        jobProgress(l.data || {});
        continue;
      }
      if (kind === "sweep-progress") {
        /* G168/R34（站长裁"汇总为王，点级降噪"）：扫描级汇总
         * （X/N · 失败数）——不落终端，喂 result-chip；点级过程事件
         * 已在 worker 侧静默，每点终态行照常走文本环。 */
        sweepProgress(l.data || {});
        continue;
      }
      if (kind === "job-done" && l.data) {
        /* G13② 结果事件：worker 里任何键盘跑完（或失败了结）的 run 都推到
         * 本页——记 job_id、清结果缓存、刷新结果卡与已开曲线（结果=投影）。
         * 结果模式切换也挂这里（2026-10 架构收敛：废除 termLastCode
         * 正则旁证——「一次 run 尝试已了结」是事实，与谁发起、哪个页面、
         * 成败无关；失败也进结果态 = 2026-10-14 既有裁决）。 */
        if (l.data.job_id) {
          setLastJobId(l.data.job_id);
          setResultStatus(null);
        }
        if (!resultMode) enterResult(SessionSync._boot);
        refreshResultChip();
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
export function kindClass(kind) {
  if (kind === "error" || kind === "stderr") return "term-err";
  if (kind === "note") return "term-note";
  if (kind === "job") return "term-job";   /* 运行阶段提示（提交/运行中/下载） */
  return null;
}

/* input 行渲染（VS Code 式 echo 的服务端形态）：mark 非空先打一行来源
 * 标注（term-note），随后 "❯ " 前缀 + 原文（两个 span，与旧客户端 echo
 * 同款 DOM——标注来源时前缀 ❯⋯，视觉与旧 echoMark 通道一致）。不建
 * DOM 容器防过度工程：时序即块；环裁剪致首块无头 input 照常平铺。 */
export function renderTermInput(l) {
  const out = $("term-out");
  if (!out) return;
  _termLast = null;          /* 命令回显 = 合并链断开 */
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
export function termQueueScroll() {
  if (_termScrollQueued) return;
  _termScrollQueued = true;
  requestAnimationFrame(() => {
    _termScrollQueued = false;
    /* 滚动面 = #term-out（唯一 overflow-y:auto 的元素）。#term-dock 自身
     * overflow:hidden 且内容恒等于容器高，对它设 scrollTop 永远钳在 0。 */
    const out = $("term-out");
    if (out) out.scrollTop = out.scrollHeight;
  });
}

export let _termScrollQueued = false;

/* ── 长输出折叠（G45）──
 * 终端是 append-only 的流；跑一次仿真会灌几百行 stdout，把上面的
 * 命令/报错顶出视野。策略：把**连续的无着色 stdout 行**攒成一块
 * `.term-block`；块内超过 FOLD_AT 行时，只留头 FOLD_HEAD 行可见，
 * 其余塞进 `.term-block-body`（默认收起），并插一个「展开 N 行」按钮。
 * 非 stdout（note/job/err/input）一律先把当前块封口——折叠块不跨语义。 */
export const FOLD_AT = 40;
export const FOLD_HEAD = 3;      /* 终端面板常常只有 ~6 行高：留 3 行就够看出上下文，
                           * 再多会把「展开」按钮顶到可视区外 */
export let _termBlock = null;      /* {el, body, btn, lines} 当前 stdout 块 */

export function termCloseBlock() {
  _termBlock = null;
}

export function termAppendFoldable(text) {
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

/* ── 连续同文同色的行合并成一条 ×N（2026-11-25）──
 * 同一条 ValueError 连刷七行会把输出区占满，用户分不清是"同一次失败点了
 * 七次"还是"七种失败"。计数只活在 DOM；环仍是唯一记录（session 逐条留），
 * 重连按环回放会重算。断连续的任何行（输入/note/stdout）都会清空游标。 */
let _termLast = null;   // {cls, text, el, n}

export function termAppend(text, cls) {
  const out = $("term-out");
  if (!out || !text) return;
  if (!cls) {                     /* 无着色 = stdout → 可折叠流 */
    _termLast = null;
    termAppendFoldable(text);
    return;
  }
  termCloseBlock();
  const body = text.replace(/\n$/, "");
  if (_termLast && _termLast.cls === cls && _termLast.text === body
      && _termLast.el.parentNode) {
    _termLast.n += 1;
    _termLast.el.textContent = body + "  ×" + _termLast.n + "\n";
    termQueueScroll();
    return;
  }
  const sp = document.createElement("span");
  sp.className = cls;
  sp.textContent = text;
  out.appendChild(sp);
  _termLast = { cls, text: body, el: sp, n: 1 };
  termQueueScroll();
}


/* 执行主流程：排队 → 置忙 → 提交 → 轮询到 done。
 * 排队而非丢弃（G13①）：worker 先发 cfg 快照后发 done，丢弃语义曾静默
 * 吞命令；kernel 本就串行，单槽排队即正确语义（Jupyter 同款）。 */
/* busy 期提交的待执行命令（单槽排队；L-35 起 edit.js 已删，唯一消费者在此） */
let _pendingExec = null;
function setPendingExec(v) { _pendingExec = v; }

export async function _execFlow(label, submit) {
  if (termBusy) {
    setPendingExec({ label, submit });
    termAppend("… 上一段还在执行——已排队，跑完自动运行\n", "term-note");
    return;
  }
  termBusy = true;   /* 同步置位（防连点竞态）：置位前不得有任何 await */
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

export function termExec(code, opts = {}) {
  code = code || "";
  if (!code.trim()) { setStopVisible(false); return; }
  /* 用户代码腿（终端输入行 / 脚本编辑器 Run）：mode=build = fresh 整份
   * 换模型；输入回显在本地（L-35：execToTerminal 断链改为直连 exec）。 */
  const fresh = (opts.mode || "run") === "build";
  return _execFlow("exec", () => {
    renderTermInput({ text: code, mark: opts.label || "" });
    return exec(code, { fresh, echo: true });
  });
}

/* ── 工具栏执行腿（M1 重接，Q44：按钮 → exec → 公开动词）────────── */

/** FDTD 页 Run：提交当前 fdtd 模型（不改模型，回显进终端）。 */
export function runModel() {
  return _execFlow("run", () =>
    exec("genie.run_fdtd()", { fresh: false, echo: true }));
}

/** FDTD 页 Build：Q35 本地预演（网格/步数，不提交云端）。 */
export function runBuild() {
  return _execFlow("build", () =>
    exec("genie.build_fdtd()", { fresh: false, echo: true }));
}

/** MODE 页 Run：mode 求解（缺省实例名 mode；多实例走脚本）。 */
export function runMode() {
  return _execFlow("run", () =>
    exec("genie.run_mode()", { fresh: false, echo: true }));
}

export function _startPolling() {
  if (termPollTimer) clearInterval(termPollTimer);
  termPollTimer = setInterval(async () => {
    let busy;
    try {
      busy = await pollOnce();   /* v2 环投影一拍（/events?since=） */
    } catch (e) {
      /* 输出通道断了：显性报错并停轮。绝不静默。 */
      termAppend("✗ 输出通道 " + (e && e.message ? e.message : e) + "\n", "term-err");
      clearInterval(termPollTimer); termPollTimer = null; termBusy = false;
      setStopVisible(false);
      return;
    }
    setStopVisible(!!busy);         /* 停止钮可见性 = worker 忙闲 */
    if (!busy) {
      clearInterval(termPollTimer);
      termPollTimer = null;
      termBusy = false;
      if (_pendingExec) {                            /* 排队命令自动续跑 */
        const p = _pendingExec; setPendingExec(null);
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
export function applyCfgSnapshot(c, resolved, seq, origin, paths) {
  /* 纯投影（2026-11-23 终态）：GUI 不持可编辑副本——快照到达 → 投影一次 →
   * 重画。没有 diff 基准、没有回声抑制、没有本地编辑合并。
   * 唯一的状态保护：**正在编辑的输入框不被重建**（焦点/光标/半截输入）。
   */
  if (!c) return;
  c = guiViewOf(c);                            /* Q17 嵌套 → GUI 平铺视图 */
  if (resolved) resolved = guiViewOf(resolved);
  setCfg(c);
  setDisplayCfg(resolved || c);                /* 表达式槽 → 数值副本（3D/小注用） */
  syncSolverTabs(c);                           /* 求解器页签：声明了才显示（Q44） */
  FormPanel.setCfg(cfg);
  /* 唯一的状态保护：**正在敲字的输入框不被重建**（焦点/光标/半截输入）。
   * 2026-11-25 收窄：只保护 text input / textarea。此前把"焦点在 #config-content
   * 里"一律算编辑中——下拉框切完焦点还在 select 上，快照到了面板却不重画
   * （Boundary kind 切到 pml 后 Advanced 段不出现，就是这个洞）。 */
  const ae = document.activeElement;
  const typing = ae && (ae.tagName === "TEXTAREA"
    || (ae.tagName === "INPUT" && !ae.readOnly));
  const editing = typing && ae.closest
    && ae.closest(".inspector, #config-content");
  if (!editing) {
    renderInspector();
    renderTree();
  }
  updateScene();
  if (origin === "reject") {
    apiNotice("改动没被接受（原因见终端）", "err");
  }
}

SessionSync.onSnapshot(applyCfgSnapshot);   /* 投影订阅同步器（收敛③） */

/* 求解器页签显隐（Q44：一个求解器一页，声明了才显示）。
 * 数据源 = 快照的 solvers:[{name,kind}]（exec.py gui_snapshot）。
 * 当前页被隐藏时回落 DESIGN。 */
export function syncSolverTabs(c) {
  const kinds = new Set((c && c.solvers || []).map(s => s && s.kind));
  const fdtdTab = document.getElementById("tab-fdtd");
  const modeTab = document.getElementById("tab-mode");
  if (fdtdTab) fdtdTab.hidden = !kinds.has("fdtd");
  if (modeTab) modeTab.hidden = !kinds.has("mode");
  for (const t of [fdtdTab, modeTab]) {
    if (t && t.hidden && t.getAttribute("aria-selected") === "true")
      document.getElementById("tab-design")?.click();
  }
}

/* ── 空闲持续订阅（G13①）：GUI 是 worker 的投影，不是"执行期才睁眼的
 * 观察者"。exec 期间 termExec 的 350ms 轮询在跑；空闲期本 2s 节拍
 * 接力——同一 since 增量机制，两种节奏。外部键盘（另一窗口）
 * 的编辑与输出由此流进本页（含输入 echo）。────────────────────── */
setInterval(async () => {
  if (termBusy || termPollTimer) return;   /* exec 轮询在管，不抢线 */
  try {
    await pollOnce();               /* v2 环投影：空闲 2s 接力 */
  } catch { /* 网络抖动：下个节拍再试 */ }
}, 2000);

(() => {   /* 输入行：Enter 执行 · ↑↓ 历史 · 点面即聚焦（VS Code 手感） */
  const inp = $("term-in");
  if (!inp) return;
  const dock = $("term-dock");
  if (dock) dock.addEventListener("click", () => {
    if (!window.getSelection().toString()) inp.focus();
  });
  const submit = () => {
    const code = inp.value;
    if (!code.trim()) return;
    inp.value = "";
    termHistIdx = -1; termDraft = "";
    if (termHistory[termHistory.length - 1] !== code) termHistory.push(code);
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
  cancel().catch(() => {});   /* 取消在飞作业（genie.cancel 同门） */
});

if (termResetBtn) termResetBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  restart().catch(() => {});   /* 重启 worker */
});

/* 清屏（2026-11-25）：只清可见 DOM，不碰环/游标——刷新后按环回放自然恢复。
 * 与 ↻ 清理重启分工不同：那个换执行态，这个什么都不换。 */
const termClearBtn = $("term-clear");
if (termClearBtn) termClearBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const out = $("term-out");
  if (out) out.textContent = "";
  termCloseBlock();
  _termLast = null;
});

