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
 * 值解析：可转有限数字则存数字，否则存字符串（表达式槽语义）。
 *
 * 单位（2026-10-30 站长裁"参数表要能配单位"）：数字 + 单位下拉 → 存协议
 * 的单位串 ``0.5[um]``（求值域 = 纯数值换算，服务端解析成 5e-7）。表达式
 * （如 d/4）不走下拉——单位要写进表达式自己的字面量。行下 ``= 5e-7`` 是
 * resolved 快照，用户能直接看到"0.5 µm 到底是几米"。 */
const PARAM_UNITS = ["", "m", "mm", "um", "nm", "pm",
                     "s", "ms", "us", "ns", "ps", "fs",
                     "Hz", "kHz", "MHz", "GHz", "THz"];
const PARAM_NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
/* 单位 SI 尺度（与协议 ``genie_protocol.expr._UNITS`` 的长度/时间/频率
 * 三族对齐）。GUI 要**立刻**给出 "0.5 µm = 5e-7"，不能等 resolved 回声
 * （表单改动后 displayCfg 是 raw 副本，见 edit.js onFormMutate）。 */
const PARAM_UNIT_SCALE = {
  m: 1, mm: 1e-3, um: 1e-6, nm: 1e-9, pm: 1e-12,
  s: 1, ms: 1e-3, us: 1e-6, ns: 1e-9, ps: 1e-12, fs: 1e-15,
  Hz: 1, kHz: 1e3, MHz: 1e6, GHz: 1e9, THz: 1e12, PHz: 1e15,
};

function _parseParamText(text) {
  const t = String(text).trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : t;
}

function _splitParamUnit(raw) {
  const s = String(raw == null ? "" : raw).trim();
  const m = s.match(
    /^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\[([A-Za-z\u00b5\u03bc]+)\]$/);
  if (!m) return { num: s, unit: "" };
  const u = m[2].replace(/[\u00b5\u03bc]/g, "u");
  return { num: m[1], unit: u };
}

function _paramUnitSelect(unit, onChange) {
  const sel = document.createElement("select");
  sel.className = "param-unit";
  sel.title = "单位（换算成 SI 后存进 cfg；表达式请把单位写进字面量）";
  const opts = PARAM_UNITS.slice();
  if (unit && !opts.includes(unit)) opts.push(unit);   /* 保留未知/别名单位 */
  for (const u of opts) {
    const o = document.createElement("option");
    o.value = u;
    o.textContent = u || "—";
    sel.appendChild(o);
  }
  sel.value = unit || "";
  sel.addEventListener("change", onChange);
  return sel;
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
  note.textContent = "参数表 · 值可带单位（0.5 + µm = 5e-7 m）；表达式槽/sweep 轴"
    + "引用这些名字（与脚本 f.parameters 同源）";
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

  /* 把"数值框 + 单位下拉"合成要存的协议值：有单位 → "0.5[um]"；
   * 无单位 → 数字/表达式串（旧语义不变）。 */
  const compose = (numText, unit) => {
    const t = String(numText).trim();
    if (t === "") return { err: "值不能为空" };
    if (unit) {
      if (!PARAM_NUM_RE.test(t))
        return { err: "带单位时值必须是数字（表达式请把单位写进表达式）" };
      return { val: t + "[" + unit + "]" };
    }
    const val = _parseParamText(t);
    return val === null ? { err: "值不能为空" } : { val };
  };
  const resolvedOf = (k) => {
    try {
      if (typeof displayCfg !== "undefined" && displayCfg
          && displayCfg.parameters) return displayCfg.parameters[k];
    } catch (e) { /* 快照未到不显示小注 */ }
    return undefined;
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

    const { num, unit } = _splitParamUnit(params[k]);
    const v = document.createElement("input");
    v.className = "param-value";
    v.value = num;
    v.title = "数值（可配右侧单位）或表达式串（如 d/4）";
    const us = _paramUnitSelect(unit, () => {
      const out = compose(v.value, us.value);
      if (out.err) { toast(out.err, "warn"); renderInspector(); return; }
      mutate(() => { params[k] = out.val; });
    });
    /* 表达式不走单位下拉：单位必须在表达式字面量里 */
    if (!PARAM_NUM_RE.test(num.trim()) && !unit) {
      us.disabled = true;
      us.title = "表达式不支持单位下拉——单位写进表达式字面量（如 0.5[um]/2）";
    }
    v.addEventListener("change", () => {
      const out = compose(v.value, us.value);
      if (out.err) {
        toast(out.err, "warn");
        const old = _splitParamUnit(params[k]);
        v.value = old.num;
        return;
      }
      mutate(() => { params[k] = out.val; });
    });
    row.appendChild(v);
    row.appendChild(us);

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

    /* resolved 小注：单位/表达式换算后的 SI 值（"0.5 µm 到底是几米"）。
     * 带单位的数字本地立刻算（不等回声）；表达式才回落 resolved 快照。 */
    let si;
    if (unit && PARAM_NUM_RE.test(num.trim())
        && PARAM_UNIT_SCALE[unit] != null) {
      si = Number(num) * PARAM_UNIT_SCALE[unit];
    } else {
      const res = resolvedOf(k);
      if (typeof res === "number") si = res;
    }
    if (si != null && String(si) !== num.trim()) {
      const hint = document.createElement("div");
      hint.className = "param-si";
      hint.textContent = "= " + si.toExponential(4) + "（SI）";
      body.appendChild(hint);
    }
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
  valIn.placeholder = "2e-7 / 0.5 + 单位";
  const unitIn = _paramUnitSelect("", () => {});
  unitIn.title = "新参数的单位";
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
    const out = compose(valIn.value, unitIn.value);
    if (out.err) { toast(out.err, "warn"); return; }
    mutate(() => { params[name] = out.val; }, "参数 " + name + " 已添加");
  });
  add.appendChild(nameIn);
  add.appendChild(valIn);
  add.appendChild(unitIn);
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
