import { get, post } from "../core/bus.js";
import { pollOnce } from "../core/ring.js";
/* modules/app.js — 账号 / 脚本文档 / 启动
 *
 * L-35 拔线（2026-11-23）：结果读桥（resultMeta/resultSeries）、扫参
 * （runSweep/expandAxes）、自动网格（autoGrid）全部移除，随各自模块
 * 重接（LEDGER L-37/L-38/L-43）。存活面 = 账号、脚本文档、取消/重启、
 * 启动序列（脚本回填 + 首拍轮询）。
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

/* ── 扫参账（REST 只读清单，非拼码）──────────────────────────── */
export async function getSweeps() {
  return get("/sweeps");
}

/* ── 脚本文档（工作区 script.py）──────────────────────────────── */
export const getScript = () => get("/script?focus=0");
export const saveScript = (text) => post("/script", { text: String(text || "") });

/* ── 取消/重启（会话级动作）──────────────────────────────────── */
export const cancel = () => post("/cancel", {});
export const restart = () => post("/restart", {});

/* ── 启动（DOM 就绪后：脚本载入 + 打字即落盘 + 首拍）──────────── */
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
});
