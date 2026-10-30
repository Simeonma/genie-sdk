/* modules/inspector.js — 检查器（FormPanel 只读驱动 + 参数面板 + 选中路由）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
function renderInspector() {
  let k = inspectorSel.kind, i = inspectorSel.i;
  if (k === "params") { renderParamsPane(); return; }
  /* G182 P6：扫参页（参数页底部入口进来；不是模型节点，故不进树选择） */
  if (k === "sweep") { renderSweepPane(); return; }
  if (i != null && !((cfg[k] || [])[i])) i = null;
  inspectorSel = { kind: k, i };
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
      if (window.__resultMode) addMonitorPlotCta(i);
  }
}

/* 参数页（G60① R24c 收口：可编辑表）——加/删/改值走 M3b 同一通道：
 * 直接改本地 cfg.parameters → onFormMutate → diff 出 patch 增量
 * （改名 = 删后重建；非法名前端即拒，与协议校验同一正则口径）。
 *
 * 单位（2026-10-30 站长两次定稿）：**值里直接写单位**——`0.5[um]`，也接受
 * `0.5um` / `0.5 um`（落 cfg 统一成协议串 `0.5[um]`）；第三列是**只读
 * SI 显示**（本地立刻换算，不等服务端回声）——"0.5 µm 到底是几米"一眼可见。
 * 表达式（`d/4`）原样存字符串，SI 列回落到 resolved 快照，取不到显示 —。 */
const PARAM_NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
/* 单位 SI 尺度（与协议 ``genie_protocol.expr._UNITS`` 的长度/时间/频率
 * 三族对齐）。GUI 要**立刻**给出 "0.5 µm = 5e-7"，不能等 resolved 回声
 * （表单改动后 displayCfg 是 raw 副本，见 edit.js onFormMutate）。 */
const PARAM_UNIT_SCALE = {
  m: 1, mm: 1e-3, um: 1e-6, nm: 1e-9, pm: 1e-12,
  s: 1, ms: 1e-3, us: 1e-6, ns: 1e-9, ps: 1e-12, fs: 1e-15,
  Hz: 1, kHz: 1e3, MHz: 1e6, GHz: 1e9, THz: 1e12, PHz: 1e15,
};
/* 带单位的值：数字 + （[unit] | unit），如 0.5[um] / 0.5um / 0.5 um。 */
const PARAM_UNIT_SUFFIX_RE =
  /^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?:\[\s*([A-Za-z\u00b5\u03bc]+)\s*\]|([A-Za-z\u00b5\u03bc]+))$/;

/* 值文本 → {val: 要存进 cfg 的值, si: 本地可算的 SI 值}；
 * 表达式返回 {val} 无 si（SI 列由 resolved 快照补）；坏单位返回 {err}。 */
function _parseParamInput(text) {
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

function renderParamsPane() {
  const c = $("config-content");
  if (!cfg) return;
  c.textContent = "";
  const body = document.createElement("div");
  body.className = "pane-body";
  c.appendChild(body);
  const note = document.createElement("div");
  note.className = "pane-note";
  note.textContent = "参数表 · 值里直接写单位（0.5[um] / 0.5um），右列是换算"
    + "后的 SI 值；表达式槽/sweep 轴引用这些名字（与脚本 f.parameters 同源）";
  body.appendChild(note);
  if (!(cfg.parameters && typeof cfg.parameters === "object")) cfg.parameters = {};
  const params = cfg.parameters;
  const keys = Object.keys(params);

  const mutate = (fn, okMsg) => {
    fn();
    onFormMutate();
    renderInspector();               /* 离散动作后重建行集 */
    if (okMsg) toast(okMsg, "ok");
  };
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
      mutate(() => { params[k] = p.val; });
    });
    row.appendChild(v);
    row.appendChild(si);

    const del = document.createElement("button");
    del.className = "mini";
    del.textContent = "✕";
    del.title = "删除参数 " + k;
    del.addEventListener("click", () => {
      /* 显式 verb=remove（G60①）：M3b 的 diff 通道对删除发的是
       * {path, value:null}（set-null 歧义），dict 键删除必须走
       * remove verb。成功后本地先行，服务端回声随后并入。 */
      fetch("/api/model/patch", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ops: [{ path: "parameters." + k,
                                       verb: "remove" }] }) })
        .then(r => r.json().then(j => ({ ok: r.ok, j }))
                      .catch(() => ({ ok: r.ok, j: {} })))
        .then(({ ok, j }) => {
          if (!ok) { toast("删除被拒：" + (j.detail || ""), "err"); return; }
          delete params[k];
          renderInspector();
          toast("参数 " + k + " 已删除", "ok");
        })
        .catch(e => toast("删除失败：" + e, "err"));
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

function selectObj(kind, i) {
  treeSel = i == null ? { kind } : { kind, i: +i };
  renderTree();
  inspectorSel = { kind, i: i == null ? null : +i };
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
