import { renderInspector } from "./inspector.js";
import { cancel } from "./app.js";
import { toast } from "./state.js";
import { runBtnRefresh } from "./toolbar.js";
import { sweepPaneEvent } from "./sweep.js";
/* modules/result.js — 结果模式 + 进度 chip（L-35 拔线后 = 纯投影）
 *
 * Plot 多卡/折线/热图机器已删（站长拍 2026-11-23，随 L-43/M8 重接——
 * 取数腿 resultMeta/resultSeries 属拼码，且 ring 侧本就丢弃 plot 事件）。
 * 存活面：结果态进出与只读锁、lastJobId、进度/取消 chip、扫参进度。 */
export let resultMode = false;

/* 结果态锁：输入件 disabled 防键盘 + 工具栏加对象按钮 disabled；
 * CSS(body.result-mode) 补 pointer-events+视觉降权；MutationObserver
 * 兜表单重渲染（树上点对象→FormPanel 重建→新输入件再上锁） */
export let __resultLockMO = null;
export const __RESULT_LOCK_TB = ["tb-mat-btns", "tb-geo-btns", "tb-src-btns", "tb-mon-btns"];
export function __lockConfigInputs() {
  const c = document.getElementById("config-content");
  if (c) {
    /* 扫参面（参数页底部的入口 + 扫参页本身）是**只读模型上的新动作**：
     * 扫描不写当前 cfg，所以结果态的读锁不该锁它。 */
    c.querySelectorAll("input, select, textarea").forEach(el => {
      if (el.closest(".sweep-page")) return;
      el.disabled = true;
    });
    c.querySelectorAll("button").forEach(b => {
      if (b.closest(".sweep-page")) return;
      b.disabled = true;
    });
  }
  for (const id of __RESULT_LOCK_TB) {
    const g = document.getElementById(id);
    if (g) g.querySelectorAll("button").forEach(b => { b.disabled = true; });
  }
}
export function __startResultLock() {
  __lockConfigInputs();
  const c = document.getElementById("config-content");
  if (!c || __resultLockMO) return;
  __resultLockMO = new MutationObserver(__lockConfigInputs);
  __resultLockMO.observe(c, { childList: true, subtree: true });
}
export function __stopResultLock() {
  if (__resultLockMO) { __resultLockMO.disconnect(); __resultLockMO = null; }
  renderInspector();   // 重建当前表单 → 恢复各控件的本来 disabled 逻辑
  for (const id of __RESULT_LOCK_TB) {
    const g = document.getElementById(id);
    if (g) g.querySelectorAll("button").forEach(b => { b.disabled = false; });
  }
}

export function enterResult(silent = false) {
  if (resultMode) return;   /* 已在结果态：刷新 chip 即可，勿重弹 */
  document.body.classList.add("result-mode");
  resultMode = true;
  renderInspector();
  __startResultLock();
  runBtnRefresh();          /* 模式状态由按钮文案(Switch to setting⇄Run)
                               + 工具栏灰锁表达 */
  refreshResultChip();
  if (!silent)
    toast("已切结果模式——Switch to setting 回编辑", "ok");
}

export function exitResult() {
  document.body.classList.remove("result-mode");
  resultMode = false;
  __stopResultLock();
  runBtnRefresh();
  toast("已回编辑模式——脚本编辑器可开新仿真", "ok");
}

export let resultStatus = null;
export let lastJobId = "";    /* 最近完成的作业（job-done 事件维护）*/
export function setResultStatus(v) { resultStatus = v; }
export function setLastJobId(v) { lastJobId = v; }

export function refreshResultChip() {
  /* L-35：结果摘要（步数/dt/网格）来自已拔的 resultMeta——重接（M8）
   * 前只显示作业号；进度段（下方 jobPhase/jobProgress）照常覆盖 chip。 */
  const chip = document.getElementById("result-chip");
  if (!chip) return;
  if (!lastJobId) { chip.classList.add("hidden"); chip.innerHTML = ""; return; }
  chip.classList.remove("hidden");
  chip.innerHTML = "";
  const sp = document.createElement("span");
  sp.textContent = "作业 " + lastJobId;
  sp.title = sp.textContent;
  chip.appendChild(sp);
}

/* ── 仿真流水进度（阶段 = worker state 事件的事实）──
 * 提交（上传 payload）→ 排队（云端繁忙）→ 计算（步进%）→ 取回（下载）。
 * 阶段由 worker state 事件 / output 快照驱动（term.js 转喂本入口）；
 * 计算段的百分比/ETA 仍由 job-progress 样本外推（下方 __jobProgress）。 */
/* ── 取消按钮（G4b 2026-10-21）：queue/run 态可取消作业 ──
 * 链路：浏览器 → 宿主 POST /cancel（session 拦截 → /gw 直拨，整批一颗；
 * 逐作业取消不公开）。 */
export function _cancelJobButton(jobId) {
  const b = document.createElement("button");
  b.className = "jobprog-cancel";
  b.textContent = "✕ 取消";
  b.title = "取消作业 " + (jobId || "?");
  b.style.cssText = "margin-left:8px;padding:1px 8px;font-size:11px;"
    + "border:1px solid #d0d7de;background:#fff;border-radius:6px;"
    + "cursor:pointer;color:#c0392b";
  b.onclick = () => {
    b.disabled = true;
    b.textContent = "取消请求已发送…";
    cancel()
      .then(() => { b.textContent = "等待作业终态…"; })
      .catch(() => { b.disabled = false; b.textContent = "✕ 取消(重试)"; });
  };
  return b;
}


export const jobPhase = (function () {
  let phase = "idle", job = "";
  const LABEL = { submit: "提交作业中…", queue: "云端排队中",
                  run: "计算中", download: "取回结果中…" };
  return function (d) {
    const chip = document.getElementById("result-chip");
    if (!chip) return;
    if (d && d.phase) phase = d.phase;
    if (d && d.job_id) job = d.job_id;
    if (!(phase in LABEL)) return;   /* idle/exec：chip 交还摘要 */
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
    if (phase === "queue" || phase === "run")
      wrap.appendChild(_cancelJobButton(job));
    chip.appendChild(wrap);
  };
})();

export const jobProgress = (function () {
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
    /* P1-21 叙述帧（genie-digest/1）：ETA 优先吃内核权威值，样本外推
     * 只作旧帧回退；能量比仅在离开注入期（<1）后显示。 */
    const dg = d.digest || null;
    const comp = (dg && dg.compute) || {};
    const dEta = Number(comp.eta_s);
    let eta = "";
    if (isFinite(dEta) && dEta >= 0) {
      eta = dEta >= 90 ? " · 剩 " + Math.round(dEta / 60) + " 分"
                       : " · 剩 " + Math.round(dEta) + " s";
    } else {
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
    }
    const eR = Number(comp.energy_ratio);
    const eTxt = (isFinite(eR) && eR > 0 && eR < 1)
      ? " · E↘ " + (eR < 0.001 ? eR.toExponential(0) : eR.toFixed(2)) : "";
    const phase = ((dg && dg.job) || {}).phase;
    chip.classList.remove("hidden");
    chip.innerHTML = "";
    const wrap = document.createElement("span");
    wrap.className = "jobprog";
    wrap.title = "作业 " + (d.job_id || "?") + " 运行中"
      + (phase ? " · " + phase : "")
      + (d.batch ? " · 批 " + d.batch.finished + "/" + d.batch.total : "");
    const bar = document.createElement("span");
    bar.className = "jobprog-bar";
    const fill = document.createElement("i");
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    const txt = document.createElement("span");
    txt.className = "jobprog-txt";
    txt.textContent = pct + "% · " + step + "/" + nTot + " 步" + eta + eTxt;
    wrap.appendChild(bar);
    wrap.appendChild(txt);
    wrap.appendChild(_cancelJobButton(d.job_id));
    chip.appendChild(wrap);
  };
})();

/* ── 扫描进度（G168/R34，站长裁"汇总为王，点级降噪"）──
 * 数据链：SweepHandle 点/扫描终态 → worker sweep-progress 事件 →
 * term.js 转喂本入口；扫参页（sweep.js）也吃一份。 */
export const sweepProgress = function (d) {
  sweepPaneEvent(d);
  const chip = document.getElementById("result-chip");
  if (!chip || !d || !d.sweep_id) return;
  const total = Number(d.total) || 0;
  if (!total) return;
  if (d.state && d.state !== "running") {   /* 终态：chip 交还摘要 */
    refreshResultChip();
    return;
  }
  const r = d.rollup || {};
  const done = (r.succeeded || 0) + (r.failed || 0);
  const bad = done - (r.succeeded || 0);
  chip.classList.remove("hidden");
  chip.innerHTML = "";
  const wrap = document.createElement("span");
  wrap.className = "jobprog";
  wrap.title = "参数扫描 " + d.sweep_id + " · 共 " + total + " 点";
  const bar = document.createElement("span");
  bar.className = "jobprog-bar";
  const fill = document.createElement("i");
  fill.style.width = Math.min(100, Math.round(done / total * 100)) + "%";
  bar.appendChild(fill);
  const txt = document.createElement("span");
  txt.className = "jobprog-txt";
  txt.textContent = "扫描 " + done + "/" + total
    + (bad ? " · 失败 " + bad : "");
  wrap.appendChild(bar);
  wrap.appendChild(txt);
  chip.appendChild(wrap);
};
