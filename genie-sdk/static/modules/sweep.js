import { $ } from "./util.js";
import { getSweeps } from "./app.js";
"use strict";
/* modules/sweep.js — 扫参页（L-35 拔线后 = 纯投影）
 *
 * 轴编辑 / Run / Plot 已删（站长拍 2026-11-23，随 L-37/M2 重接）；
 * 存活面 = 扫参事件的状态投影（sweepPaneEvent）+ 扫参账清单
 * （REST 只读，无拼码）。当前跑扫参 = 脚本编辑器里写
 * add_sweep / set_named / run_sweep（见 README）。 */

export const sweepState = {
  running: false,
  sweepId: "",
  status: "",
  _statusEl: null,
};

export function sweepUpdateControls() {
  const st = sweepState._statusEl;
  if (st) st.textContent = sweepState.status || "";
}

/* 事件入口（result.js 的 sweepProgress 喂这里一份） */
export function sweepPaneEvent(d) {
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

export function renderSweepPane() {
  const c = $("config-content");
  if (!c) return;
  c.textContent = "";
  const body = document.createElement("div");
  body.className = "pane-body sweep-page";
  c.appendChild(body);

  const head = document.createElement("div");
  head.className = "sweep-head";
  const title = document.createElement("span");
  title.className = "sweep-title";
  title.textContent = "参数扫描";
  head.appendChild(title);
  body.appendChild(head);

  const note = document.createElement("div");
  note.className = "pane-note";
  note.textContent = "扫参定义与运行界面重接中——当前在脚本编辑器里用 "
    + "genie.add_sweep / set_named / run_sweep 跑扫参（见 README）；"
    + "跑过的账在下表。";
  body.appendChild(note);

  const act = document.createElement("div");
  act.className = "sweep-actions";
  const status = document.createElement("span");
  status.className = "sweep-status";
  act.appendChild(status);
  body.appendChild(act);

  _renderSweepAccounts(body);

  sweepState._statusEl = status;
  sweepUpdateControls();
}

/* 扫参账清单：目录名 + 点数 + 完成/失败 + 点文档列表（host 读 index.json） */
export function _renderSweepAccounts(body) {
  const wrap = document.createElement("div");
  wrap.className = "sweep-accounts";
  const head = document.createElement("div");
  head.className = "sweep-head";
  const title = document.createElement("span");
  title.className = "sweep-title";
  title.textContent = "扫参账";
  head.appendChild(title);
  const refresh = document.createElement("button");
  refresh.className = "mini";
  refresh.textContent = "刷新";
  head.appendChild(refresh);
  wrap.appendChild(head);
  const list = document.createElement("div");
  list.className = "sweep-acc-list";
  wrap.appendChild(list);
  body.appendChild(wrap);

  async function load() {
    list.textContent = "";
    try {
      const r = await getSweeps();
      const sweeps = (r && r.sweeps) || [];
      if (!sweeps.length) {
        list.className = "pane-empty";
        list.textContent = "还没有扫参账——跑一次扫参后 parameter_sweep_<名>/ 出现在这里";
        return;
      }
      list.className = "sweep-acc-list";
      for (const sw of sweeps) {
        const row = document.createElement("div");
        row.className = "sweep-acc-row";
        const nm = document.createElement("span");
        nm.className = "sweep-acc-name";
        nm.textContent = sw.name;
        const cnt = document.createElement("span");
        cnt.className = "sweep-acc-cnt";
        cnt.textContent = sw.total + " 点 · ✓" + sw.done
          + (sw.failed ? " · ✗" + sw.failed : "");
        const docs = document.createElement("span");
        docs.className = "sweep-acc-docs";
        const files = (sw.jobs || []).map((j) => j.doc).filter(Boolean);
        docs.textContent = files.slice(0, 3).join(", ")
          + (files.length > 3 ? " …" : "");
        docs.title = files.join("\n");
        row.appendChild(nm);
        row.appendChild(cnt);
        row.appendChild(docs);
        list.appendChild(row);
      }
    } catch (e) {
      list.className = "pane-empty";
      list.textContent = "读扫参账失败：" + (e && e.message ? e.message : e);
    }
  }
  refresh.addEventListener("click", load);
  load();
}
