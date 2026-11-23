import { $ } from "./util.js";
import { submitField } from "./edit.js";
import { FormPanel } from "../views/form.js";
import { previewEl } from "../views/previews.js";
import { addMonitorPlotCta, resultMode } from "./result.js";
import { initScene } from "./scene.js";
import { cfg, displayCfg, inspectorSel, scene, setInspectorSel, setTreeSel, toast } from "./state.js";
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
  if (i != null && !((cfg[k] || [])[i])) i = null;
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
    if (k === "mon" || k === "monitor")
      /* 结果标签卡入口（监视器专属）——仅结果态渲染（2026-10-14
       * 站长裁决：编辑态 monitor 不携带数据、不提供新建 Plot；
       * 已画的 Plot 标签卡跨模式保留）。 */
      if (resultMode) addMonitorPlotCta(i);
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
  note.textContent = "参数表 · 值里直接写单位（0.5[um] / 0.5um），右列是换算"
    + "后的 SI 值；表达式槽/sweep 轴引用这些名字（与脚本 genie.parameters 同源）";
  body.appendChild(note);
  if (!(cfg.parameters && typeof cfg.parameters === "object")) cfg.parameters = {};
  const params = cfg.parameters;
  const keys = Object.keys(params);

  const resolvedOf = (k) => {
    try {
      if (typeof displayCfg !== "undefined" && displayCfg
          && displayCfg.parameters) return displayCfg.parameters[k];
    } catch (e) { /* 快照未到不显示 */ }
    return undefined;
  };
  const fmtSi = (v) => (typeof v === "number" && Number.isFinite(v))
    ? v.toExponential(3) : "—";
  /* 只读 SI 列：解析当前文本（本地可算就本地算，表达式回落 resolved） */
  const siCell = (getSi) => {
    const el = document.createElement("span");
    el.className = "param-si-col";
    el.title = "SI 值（单位已换算；表达式取服务端解析值）";
    el.textContent = fmtSi(getSi());
    return el;
  };

  for (const k of keys) {
    const row = document.createElement("div");
    row.className = "frow";
    const l = document.createElement("input");
    l.className = "flabel param-name";
    l.value = k;
    l.readOnly = true;
    l.title = "参数名（改名 = 删除后重建）";
    row.appendChild(l);

    const v = document.createElement("input");
    v.className = "param-value";
    v.value = String(params[k]);
    v.title = "值：数字、带单位（0.5[um] / 0.5um / 0.5 um）或表达式（d/4）";
    const parsedSi = () => {
      const p = _parseParamInput(v.value);
      if (p.si != null) return p.si;
      const res = resolvedOf(k);
      return typeof res === "number" ? res : null;
    };
    const si = siCell(parsedSi);
    v.addEventListener("input", () => { si.textContent = fmtSi(parsedSi()); });
    v.addEventListener("change", () => {
      const p = _parseParamInput(v.value);
      if (p.err) {
        toast(p.err, "warn");
        v.value = String(params[k]);
        si.textContent = fmtSi(parsedSi());
        return;
      }
      submitField("parameters." + k, p.val);
    });
    row.appendChild(v);
    row.appendChild(si);

    const del = document.createElement("button");
    del.className = "mini";
    del.textContent = "✕";
    del.title = "删除参数 " + k;
    del.addEventListener("click", () => {
      /* 纯投影：删除 = 一条 param/remove 意图（界面等快照回显）。 */
      submitField("parameters." + k, null);
      toast("已提交删除参数 " + k, "ok");
    });
    row.appendChild(del);
    body.appendChild(row);
  }
  if (!keys.length) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = "尚无参数——在下方添加。";
    body.appendChild(empty);
  }

  const add = document.createElement("div");
  add.className = "frow";
  const nameIn = document.createElement("input");
  nameIn.className = "flabel param-name";
  nameIn.placeholder = "名称";
  const valIn = document.createElement("input");
  valIn.className = "param-value";
  valIn.placeholder = "0.5[um] / 2e-7";
  const addSi = siCell(() => {
    const p = _parseParamInput(valIn.value);
    return p.si != null ? p.si : null;
  });
  valIn.addEventListener("input", () => {
    addSi.textContent = fmtSi((() => {
      const p = _parseParamInput(valIn.value);
      return p.si != null ? p.si : null;
    })());
  });
  const addBtn = document.createElement("button");
  addBtn.className = "mini";
  addBtn.textContent = "＋";
  addBtn.title = "添加参数";
  addBtn.addEventListener("click", () => {
    const name = nameIn.value.trim();
    if (!/^[A-Za-z_]\w*$/.test(name)) {
      toast("参数名须字母/下划线开头，仅含字母数字下划线", "warn");
      return;
    }
    if (name in params) { toast("参数 " + name + " 已存在——直接改它的值即可", "warn"); return; }
    const p = _parseParamInput(valIn.value);
    if (p.err) { toast(p.err, "warn"); return; }
    mutate(() => { params[name] = p.val; }, "参数 " + name + " 已添加");
  });
  add.appendChild(nameIn);
  add.appendChild(valIn);
  add.appendChild(addSi);
  add.appendChild(addBtn);
  body.appendChild(add);
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
  code.title = "配方代码（def 函数集；用 d / results() / get_freq 等读结果）";
  body.appendChild(code);

  const callRow = document.createElement("div");
  callRow.className = "frow";
  const callLabel = document.createElement("span");
  callLabel.className = "flabel";
  callLabel.textContent = "调用行";
  const call = document.createElement("input");
  call.className = "param-value";
  call.value = rec.call || "";
  call.placeholder = "如 calc_q(a=1e-6)；省略则运行时再给";
  callRow.appendChild(callLabel);
  callRow.appendChild(call);
  body.appendChild(callRow);

  const btns = document.createElement("div");
  btns.className = "frow";
  const save = document.createElement("button");
  save.className = "mini";
  save.textContent = "保存";
  save.title = "把代码/调用行写进档（genie.set_named('analysis.<名>', …)）";
  save.addEventListener("click", () => {
    let cmd = "genie.set_named(" + JSON.stringify("analysis." + rec.name)
      + ", script=" + JSON.stringify(code.value);
    if (call.value.trim()) cmd += ", call=" + JSON.stringify(call.value.trim());
    cmd += ")";
    exec(cmd, { echo: false })
      .then(() => toast("配方已保存到档", "ok"))
      .catch((e) => toast("保存失败：" + (e && e.message ? e.message : e), "err"));
  });
  const run = document.createElement("button");
  run.className = "mini";
  run.textContent = "运行";
  run.title = "跑这条配方（genie.run_analysis('<名>')）";
  run.addEventListener("click", () => {
    exec("genie.run_analysis(" + JSON.stringify(rec.name) + ")",
                  { echo: true }).catch(() => {});
  });
  btns.appendChild(save);
  btns.appendChild(run);
  body.appendChild(btns);
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
