import { abandonWait, awaitEvent, exec, get, post } from "../core/bus.js";
import { pollOnce } from "../core/ring.js";
import { lastJobId, refreshResultChip, setLastJobId, setResultStatus }
  from "./result.js";
import { sweepRun } from "./state.js";
/* modules/app.js — 账号 / 结果读桥 / 扫参 / 自动网格 / 脚本文档 / 启动
 *
 * 2026-11-23 拆解：环投影下沉 core/ring.js；终端底层（渲染+exec）回
 * term.js；GenieApp 聚合层删除——消费方直接从归属模块 import。
 */
"use strict";

/* ── 账号 ────────────────────────────────────────────────────── */
export async function login(username, password) {
  const r = await post("/auth/login", { username, password });
  try { localStorage.setItem("genie_v2_username", username); } catch (e) {}
  return r;
}
export const logout = () => post("/auth/logout", {});
export const status = () => get("/auth");

/* ── 结果读桥：发一句命令，等环里的结构化事件 ────────────────── */
function _py(v) { return JSON.stringify(v); }

export async function resultMeta(opts) {
  const probe = !!(opts && opts.probe);
  const wait = awaitEvent("meta", null, probe ? 2500 : undefined);
  const code = probe
    ? "try:\n    genie.result_meta()\nexcept RuntimeError:\n    pass"
    : "genie.result_meta()";
  try {
    await exec(code, { fresh: false, echo: false });
  } catch (e) {
    abandonWait();
    throw e;
  }
  return wait;
}

export async function resultSeries(q) {
  q = q || {};
  let code = "genie.visualize(" + _py(String(q.mon || ""));
  if (q.field) code += ", field=" + _py(String(q.field));
  if (Array.isArray(q.axes) && q.axes.length) code += ", axes=" + _py(q.axes);
  if (q.slices && Object.keys(q.slices).length) code += ", slices=" + _py(q.slices);
  code += ")";
  const wait = awaitEvent("series", (d) => !q.mon || !d || d.mon === q.mon);
  try {
    await exec(code, { fresh: false, echo: false });
  } catch (e) {
    abandonWait();
    throw e;
  }
  return wait;
}

/* ── 扫参（展开轴 → 两行 genie 命令）──────────────────────────── */
function _linspace(a, b, n) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(n === 1 ? a : a + (b - a) * i / (n - 1));
  return out;
}
function _logspace(a, b, n) { return _linspace(a, b, n).map((e) => Math.pow(10, e)); }

export function expandAxes(axes) {
  const params = {};
  let total = 1;
  for (const ax of axes || []) {
    let vals;
    if (ax.values) {
      vals = ax.values.slice();
    } else if (ax.logspace) {
      vals = _logspace(ax.logspace[0], ax.logspace[1], ax.logspace[2]);
    } else if (ax.linspace) {
      vals = _linspace(ax.linspace[0], ax.linspace[1], ax.linspace[2]);
    } else {
      throw new Error("axis " + (ax.name || "?") + " has no values");
    }
    if (!vals.length) throw new Error("axis " + (ax.name || "?") + " is empty");
    params[ax.name] = vals;
    total *= vals.length;
  }
  return { params, total };
}

export async function runSweep(spec) {
  spec = spec || {};
  const { params, total } = expandAxes(spec.axes);
  const name = String(spec.name || ("gui_" + Date.now().toString(36)));
  const onFailure = spec.onFailure === "abort" ? "abort" : "continue";
  const code =
    "genie.add_param_sweep(" + JSON.stringify(name) + ", " + JSON.stringify(params) +
    ", mode='product', on_failure='" + onFailure + "')\n" +
    "genie.run_param_sweep(" + JSON.stringify(name) + ")";
  sweepRun.name = name;
  sweepRun.total = total;
  return exec(code, { fresh: false, echo: true });
}

export async function getSweeps() {
  return get("/sweeps");
}

/* ── 自动网格（按钮意图，产物随快照回显）────────────────────── */
export async function autoGrid(n) {
  await post("/form", { op: "auto_grid", ...(n ? { n } : {}) });
  return true;
}

/* ── 脚本文档（工作区 script.py）──────────────────────────────── */
export const getScript = () => get("/script?focus=0");
export const saveScript = (text) => post("/script", { text: String(text || "") });

/* ── 取消/重启（会话级动作）──────────────────────────────────── */
export const cancel = () => post("/cancel", {});
export const restart = () => post("/restart", {});

/* ── 启动（DOM 就绪后：脚本载入 + 打字即落盘 + 首拍 + 结果探测）── */
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
  setTimeout(() => {
    resultMeta({ probe: true }).then((m) => {
      if (!m || !m.ok) return;
      setLastJobId(m.job_id || lastJobId);
      setResultStatus(m);
      if (typeof refreshResultChip === "function") refreshResultChip();
    }).catch(() => {});
  }, 600);
});
