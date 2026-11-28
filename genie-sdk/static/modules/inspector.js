import { $, fdtdConfig, py } from "./util.js";
import { exec } from "../core/bus.js";
import { FormPanel } from "../views/form.js";
import { previewEl } from "../views/previews.js";
import { cfg, inspectorSel, scene, setInspectorSel, setTreeSel } from "./state.js";
import { renderSweepPane } from "./sweep.js";
import { renderTree } from "./tree.js";
/* modules/inspector.js — 检查器（FormPanel 只读驱动 + 参数面板 + 选中路由）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
export function renderInspector() {
  let k = inspectorSel.kind, i = inspectorSel.i;
  if (k === "params") { renderParamsPane(); return; }
  /* G182 P6：扫参页（参数页底部入口进来；不是模型节点，故不进树选择） */
  if (k === "sweep") { renderSweepPane(); return; }
  /* 后处理配方（Q11）：模型级具名对象——编辑代码/调用行 + 跑一条 */
  if (k === "analysis") { renderAnalysisPane(i); return; }
  /* 快照 v2（2026-11-28）：fdtd 子对象（source/monitor/mesh）住
   * solvers[].config，不在顶层——顶层读法会让单实例下标失效退回组视图。 */
  const _list = (k) => (k === "source" || k === "monitor" || k === "mesh")
    ? (fdtdConfig(cfg)[k] || []) : (cfg[k] || []);
  if (i != null && !(_list(k))[i]) i = null;
  setInspectorSel({ kind: k, i });
  if (!cfg) {
    const c = $("config-content");
    c.textContent = "";
    const body = document.createElement("div");
    body.className = "pane-body";
    const es = document.createElement("div");
    es.className = "empty-state pane-empty-state";
    const ic = document.createElement("span");
    ic.className = "empty-ic";
    try { ic.appendChild(previewEl("domain")); } catch { /* 图标缺失忽略 */ }
    es.appendChild(ic);
    const t = document.createElement("div");
    t.className = "empty-title";
    t.textContent = "还没有模型";
    es.appendChild(t);
    const h = document.createElement("div");
    h.className = "empty-hint";
    h.textContent = "写一段脚本（或载入示例），按 Run / Ctrl+Enter 预览——"
      + "树、表单与 3D 会一起出现。";
    es.appendChild(h);
    body.appendChild(es);
    c.appendChild(body);
    return;
  }
  if (i == null) FormPanel.show(k);
  else {
    FormPanel.showInstance(k, i);
    /* 结果态的监视器 Plot 入口已删（L-35，随 M8 重接） */
  }
}

export const PARAM_NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
/* 单位 SI 尺度（与协议 ``genie_protocol.expr._UNITS`` 的长度/时间/频率
 * 三族对齐）。GUI 要**立刻**给出 "0.5 µm = 5e-7"，不能等 resolved 回声
 * （displayCfg 是快照里的 resolved 副本）。 */
export const PARAM_UNIT_SCALE = {
  m: 1, mm: 1e-3, um: 1e-6, nm: 1e-9, pm: 1e-12,
  s: 1, ms: 1e-3, us: 1e-6, ns: 1e-9, ps: 1e-12, fs: 1e-15,
  Hz: 1, kHz: 1e3, MHz: 1e6, GHz: 1e9, THz: 1e12, PHz: 1e15,
};
/* 带单位的值：数字 + （[unit] | unit），如 0.5[um] / 0.5um / 0.5 um。 */
export const PARAM_UNIT_SUFFIX_RE =
  /^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?:\[\s*([A-Za-z\u00b5\u03bc]+)\s*\]|([A-Za-z\u00b5\u03bc]+))$/;

/* 值文本 → {val: 要存进 cfg 的值, si: 本地可算的 SI 值}；
 * 表达式返回 {val} 无 si（SI 列由 resolved 快照补）；坏单位返回 {err}。 */
export function _parseParamInput(text) {
  const t = String(text).trim();
  if (t === "") return { err: "值不能为空" };
  const m = t.match(PARAM_UNIT_SUFFIX_RE);
  if (m) {
    const num = m[1];
    const unit = (m[2] || m[3]).replace(/[\u00b5\u03bc]/g, "u");
    const scale = PARAM_UNIT_SCALE[unit];
    if (scale == null) {
      return { err: "未知单位 " + unit + "（支持 "
               + Object.keys(PARAM_UNIT_SCALE).join("/") + "）" };
    }
    return { val: num + "[" + unit + "]", si: Number(num) * scale };
  }
  if (PARAM_NUM_RE.test(t)) return { val: Number(t), si: Number(t) };
  return { val: t };                    /* 表达式：单位写进字面量 */
}

export function renderParamsPane() {
  const c = $("config-content");
  if (!cfg) return;
  c.textContent = "";
  const body = document.createElement("div");
  body.className = "pane-body";
  c.appendChild(body);
  const note = document.createElement("div");
  note.className = "pane-note";
  note.textContent = "参数表 · 值里直接写单位（0.5[um]），表达式槽/sweep 轴"
    + "引用这些名字（与脚本 genie.parameters 同源）";
  body.appendChild(note);
  const params = (cfg.parameters && typeof cfg.parameters === "object")
    ? cfg.parameters : {};
  const names = Object.keys(params);
  const setValue = (k, val) => exec(
    "genie.set_named(" + JSON.stringify("parameter." + k) + ", value="
    + py(val) + ")", { echo: false }).catch(() => {});
  const removeParam = (k) => exec(
    "genie.remove(" + JSON.stringify("parameter." + k) + ")",
    { echo: true }).catch(() => {});
  const addParam = () => {
    const taken = new Set(names);
    let n = 1, name = "param_" + n;
    while (taken.has(name)) { n += 1; name = "param_" + n; }
    exec("genie.add_parameter(" + JSON.stringify(name) + ")",
      { echo: true }).catch(() => {});
  };
  FormPanel.renderPage(PARAM_SCHEMA(names, params, setValue,
    removeParam, addParam), body);
}

/* 参数卡（2026-11-29 站长拍）：一张表（Name 只读 / Value 可编 / 行尾 ✕），
 * 页内 Add。不显示 SI 列——单位直接写在值里（0.5[um]），与脚本同源。
 * Name 不改名——改名会断所有引用它的表达式与 sweep 轴（要换名 = 删 + 建）。
 * Value 三类：纯数 / 带单位 / 表达式（表达式原样存，求值归服务端）。 */
export function PARAM_SCHEMA(names, params, setValue, removeParam,
                             addParam) {
  const rows = names.map((k) => ({ cells: [
    { kind: "str", path: "param." + k + ".name", value: k },
    { kind: "str", path: "param." + k + ".value",
      value: params[k] == null ? "" : String(params[k]),
      hint: "数字 / 带单位（0.5[um]）/ 表达式（d/4）",
      validate: (t) => _parseParamInput(t).err || null,
      onSet: (t) => {
        const p = _parseParamInput(t);
        if (!p.err) setValue(k, p.val);
      } },
    { kind: "button", text: "✕",
      title: "删除参数 " + k + "（引用它的表达式需要另行处理）",
      aria: "删除参数 " + k,
      onClick: () => removeParam(k) },
  ] }));
  const items = names.length
    ? [{ type: "table", caption: "Parameters",
        cols: [{ label: "name" }, { label: "value" }, { label: "" }],
        rows }]
    : [{ type: "note", text: "尚无参数——点下面的 Add parameter 加一个。" }];
  items.push({ type: "row", label: "",
    controls: [{ kind: "button", text: "Add parameter", cls: "mini",
      onClick: () => addParam() }] });
  return { sections: [{ type: "section", key: "params",
    title: "Parameters", open: true, items }] };
}

/* 后处理配方面板（Q11）：代码正本在档，编辑即写档（set_named script），
 * 运行 = run_analysis（只读消费结果：白名单只许读动词，不碰模型）。 */
export function renderAnalysisPane(i) {
  const c = $("config-content");
  const rec = (cfg && cfg.analysis || [])[i];
  if (!c) return;
  c.textContent = "";
  if (!rec) return;
  const body = document.createElement("div");
  body.className = "pane-body";

  const head = document.createElement("div");
  head.className = "sec-head";
  head.textContent = "Analysis · " + rec.name;
  body.appendChild(head);
  const hint = document.createElement("div");
  hint.className = "pane-empty";
  hint.textContent = "只读消费已有监视器结果（白名单动词）；不创建模型对象，"
    + "不属于任何求解器。代码正本随档保存。";
  body.appendChild(hint);

  const code = document.createElement("textarea");
  code.className = "analysis-code";
  code.value = rec.script || "";
  code.rows = 14;
  code.spellcheck = false;
  code.readOnly = true;                 /* L-35：保存/运行腿随 M7 重接 */
  code.title = "配方代码（def 函数集；用 d / results() / get_freq 等读结果）——只读展示";
  body.appendChild(code);

  const callRow = document.createElement("div");
  callRow.className = "frow";
  const callLabel = document.createElement("span");
  callLabel.className = "flabel";
  callLabel.textContent = "调用行";
  const call = document.createElement("input");
  call.className = "param-value";
  call.value = rec.call || "";
  call.readOnly = true;
  call.placeholder = "如 calc_q(a=1e-6)；省略则运行时再给";
  callRow.appendChild(callLabel);
  callRow.appendChild(call);
  body.appendChild(callRow);
  c.appendChild(body);
}

export function selectObj(kind, i) {  setTreeSel(i == null ? { kind } : { kind, i: +i });
  renderTree();
  setInspectorSel({ kind, i: i == null ? null : +i });
  renderInspector();
  /* 3D 联动高亮（2026-10-21 用户需求）：选中什么，3D 里就圈出什么。
   * 组/单例面板（domain/mesh/run/params）没有 3D 对象 → 清掉高亮。 */
  if (typeof scene !== "undefined" && scene && scene.highlightSelection) {
    const OBJ_KINDS = ["geometry", "source", "monitor", "materials"];
    if (i != null && OBJ_KINDS.indexOf(kind) >= 0) scene.highlightSelection(kind, i);
    else scene.clearHighlight();
  }
}

/* ══ 3D（v1 initScene 同款：WebGL→2D 降级 + 显示工具钮接线） ══ */
