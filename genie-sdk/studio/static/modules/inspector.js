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
 * 值解析：可转有限数字则存数字，否则存字符串（表达式槽语义）。 */
function _parseParamText(text) {
  const t = String(text).trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : t;
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
  note.textContent = "参数表 · 表达式槽/sweep 轴引用这些名字（与脚本 f.parameters 同源）";
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
    v.title = "数值或表达式串（如 d/4）";
    v.addEventListener("change", () => {
      const val = _parseParamText(v.value);
      if (val === null) { toast("值不能为空", "warn"); v.value = String(params[k]); return; }
      mutate(() => { params[k] = val; });
    });
    row.appendChild(v);
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
  valIn.placeholder = "2e-7 或表达式";
  const addBtn = document.createElement("button");
  addBtn.className = "mini";
  addBtn.textContent = "＋ 添加";
  addBtn.addEventListener("click", () => {
    const name = nameIn.value.trim();
    if (!/^[A-Za-z_]\w*$/.test(name)) {
      toast("参数名须字母/下划线开头，仅含字母数字下划线", "warn");
      return;
    }
    if (name in params) { toast("参数 " + name + " 已存在——直接改它的值即可", "warn"); return; }
    const val = _parseParamText(valIn.value);
    if (val === null) { toast("值不能为空（数值或表达式串）", "warn"); return; }
    mutate(() => { params[name] = val; }, "参数 " + name + " 已添加");
  });
  add.appendChild(nameIn);
  add.appendChild(valIn);
  add.appendChild(addBtn);
  body.appendChild(add);

  /* G182 P6 定稿入口：参数页底部 Parameter Sweep → 中间栏扫参页。
   * 参数页仍是"单次仿真的单值表"——扫参不内联，独立一副手套。 */
  const cta = document.createElement("div");
  cta.className = "pane-sweep-cta";
  const sweepBtn = document.createElement("button");
  sweepBtn.className = "mini";
  sweepBtn.textContent = "⇢ Parameter Sweep";
  sweepBtn.title = "用参数表里的参数做一次扫描（每条轴一组取值）";
  sweepBtn.addEventListener("click", openSweepPane);
  cta.appendChild(sweepBtn);
  body.appendChild(cta);
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
