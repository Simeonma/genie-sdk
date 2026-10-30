/* modules/state.js — 共享状态与通用 UI（cfg/displayCfg/选中态/场景柄/toast）+ 树元数据
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* GENIE Studio v2 — 显示型工作台（terminal 战役修订，2026-10-10 定案）。
 *
 * 唯一仿真主体 = 会话 SimConfig（会话进程里的 f，真 SDK + 登录 key）。
 * 三条编辑通道同汇执行层 /api/exec：①下方 Terminal 逐条指令 ②脚本
 * 编辑器「运行」整体 exec（脚本文本只是程序，运行才生效）③工具栏
 * Run = f.run()。表单/树/3D/Plot 全是 f 的只读投影：exec 后 cfg 快照
 * 回流刷新全视图；表单字段级修改防抖 400ms 经 load_config 推回会话。
 * 脚本文本永不跟随 cfg（它是程序，不是状态）。
 *
 * 渲染机器直接复用 v1 资产：form.js（FormPanel）、scene3d/scene2d、
 * icons/previews（含 GEO_TYPE_LABEL）。树/工具条按 v1 renderTree/
 * buildToolbar 同款 DOM 结构重写（去掉拖拽/右键/增删的**接线**，器官保留）。
 */
"use strict";


/* 树标签命名公约（2026-10-21 用户定案）：「一行只一个词」。
 * 旧版每条都写成「English 中文」（Domain 域 / Materials 材料 / 场景内容…），
 * 单行信息量翻倍、树看起来啰嗦——现在结构词一律用脚本 API 的英文单名，
 * 中文留给检查器的字段标签与说明。 */
const TREE_META = {
  domain: ["domain", "Domain"], mesh: ["mesh", "Mesh"],
  materials: ["materials", "Materials"], geometry: ["geometry", "Structures"],
  structure: ["structure", "Structure"], source: ["source", "Sources"],
  monitor: ["monitor", "Monitors"],
  run: ["run", "Settings"],
  sweep: ["sweep", "Sweep"],
  params: ["params", "Parameters"],
};
const TREE_PV_KINDS = { geometry: 1, source: 1, monitor: 1, materials: 1 };
function treePreviewKey(kind, i) {
  const it = (cfg && cfg[kind]) ? cfg[kind][i] : null;
  const t = it && it.type;
  if (kind === "materials")
    return it && it.type === "const" ? "mat_const"
         : it && it.type === "lorentz" ? "mat_lorentz"
         : "materials";   /* from_library 渲染已随 Q4=B 退役（R38） */
  if (kind === "monitor") return t ? "mon_" + t : "mon_time";
  if (kind === "source") return t || "soft_source";
  return t || "box";
}
function treePreviewIcon(ic, kind, i) {
  ic.classList.add("tree-pv");
  ic.appendChild(previewEl(treePreviewKey(kind, i)));
}

/* ── 状态（模型每次预览整体替换；无写门） ───────────────────── */
let cfg = null;                        // preview 模型（只读消费；v1 组件吃这个形状）
let displayCfg = null;                 // 3D 显示副本（resolved：表达式槽已解析；缺则回退 cfg）
let treeSel = { kind: "domain", i: null };
let inspectorSel = { kind: "domain", i: null };
let treeGroupOpen = {};
let scene = null;


/* 轻提示（2026-10-21 重刻）：白卡 + 状态点 + 发丝边 + shadow-2，走 CSS 类
 * （不再用内联样式写死 #1f6feb / GitHub 色）。状态点承担"什么级别"，
 * 卡片本身保持中性——一排提示同时出现时不会变成彩条墙。 */
function toast(msg, kind = "info") {
  let host = document.getElementById("v2-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "v2-toasts";
    document.body.appendChild(host);
  }
  const k = kind === "err" || kind === "error" ? "err" : kind === "ok" ? "ok"
    : kind === "warn" ? "warn" : "info";
  const el = document.createElement("div");
  el.className = "toast toast-" + k;
  el.setAttribute("role", "status");
  const dot = document.createElement("span");
  dot.className = "toast-dot";
  dot.setAttribute("aria-hidden", "true");
  const tx = document.createElement("span");
  tx.className = "toast-msg";
  tx.textContent = msg;
  el.appendChild(dot);
  el.appendChild(tx);
  host.appendChild(el);
  window.setTimeout(() => {
    el.classList.add("toast-out");
    window.setTimeout(() => el.remove(), 200);
  }, k === "err" ? 6000 : 3500);
}
