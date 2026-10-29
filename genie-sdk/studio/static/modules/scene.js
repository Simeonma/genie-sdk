/* modules/scene.js — 3D 场景（WebGL→2D 降级 + 显示工具钮 + 渲染循环）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
function initScene() {
  try {
    scene = new Scene3D("three-container");
    scene.init();
  } catch (e1) {
    console.warn("WebGL 不可用，降级 Canvas 2D 等距投影:", e1);
    if (scene && scene.renderer && scene.renderer.domElement
        && scene.renderer.domElement.parentNode)
      scene.renderer.domElement.parentNode.removeChild(scene.renderer.domElement);
    try { if (scene && scene.renderer && scene.renderer.dispose) scene.renderer.dispose(); }
    catch { /* 清场尽力而为 */ }
    scene = null;
    try {
      scene = new Scene2D("three-container");
      scene.init();
    } catch (e2) {
      console.error("Scene2D init failed:", e2);
      const tip = document.createElement("div");
      tip.className = "scene-fatal";
      tip.textContent = "3D/2D 预览均初始化失败。页面其余功能不受影响。错误: "
        + (e2 && e2.message ? e2.message : e2);
      $("three-container").appendChild(tip);
      return;
    }
  }
  /* 显示类工具钮（纯视图开关——显示模式允许接线的类别）。
   * 2026-10-21：PML 吸收壳改为常显，按钮退役；新增「边框坐标」与「X 光」。 */
  $("btn-fitcam").addEventListener("click", () => scene.resetCamera());
  const rulerBtn = $("btn-ruler");
  if (rulerBtn) rulerBtn.addEventListener("click", (e) => {
    scene.setRulers(!scene.showRulers);
    e.currentTarget.classList.toggle("active", scene.showRulers);
    e.currentTarget.setAttribute("aria-pressed", String(scene.showRulers));
  });
  const xrayBtn = $("btn-xray");
  if (xrayBtn) xrayBtn.addEventListener("click", (e) => {
    scene.setXray(!scene.showXray);
    e.currentTarget.classList.toggle("active", scene.showXray);
    e.currentTarget.setAttribute("aria-pressed", String(scene.showXray));
  });
  /* 透视 / 正交（2026-10-21）：一个按钮两态，按下 = 透视 */
  const projBtn = $("btn-proj");
  if (projBtn) projBtn.addEventListener("click", (e) => {
    const next = scene.projection === "persp" ? "ortho" : "persp";
    scene.setProjection(next);
    const on = scene.projection === "persp";
    e.currentTarget.classList.toggle("active", on);
    e.currentTarget.setAttribute("aria-pressed", String(on));
  });
  /* 剖面切片：开启时浮出轴 + 位置控制板 */
  const secBtn = $("btn-sec");
  const secPanel = $("sec-panel");
  const secPos = $("sec-pos");
  const syncSecPanel = () => {
    const on = !!scene.showClip;
    if (secBtn) {
      secBtn.classList.toggle("active", on);
      secBtn.setAttribute("aria-pressed", String(on));
    }
    if (secPanel) {
      if (on && secBtn) secPanel.style.top = (secBtn.offsetTop || 0) + "px";
      secPanel.classList.toggle("hidden", !on);
    }
  };
  if (secBtn) secBtn.addEventListener("click", (e) => {
    if (e.currentTarget.disabled) return;
    scene.setSection(!scene.showClip);
    syncSecPanel();
  });
  if (secPanel) {
    secPanel.querySelectorAll(".sec-ax").forEach((b) => {
      b.addEventListener("click", () => {
        scene.setClipAxis(b.dataset.axis);
        secPanel.querySelectorAll(".sec-ax").forEach((o) => {
          const on = o === b;
          o.classList.toggle("is-on", on);
          o.setAttribute("aria-pressed", String(on));
        });
      });
    });
  }
  if (secPos) secPos.addEventListener("input", () => {
    scene.setClipPos(Number(secPos.value) / 100);
  });
  $("btn-grid").addEventListener("click", (e) => {
    scene.setGridVisible(!scene.showGrid);
    e.currentTarget.classList.toggle("active", scene.showGrid);
    e.currentTarget.setAttribute("aria-pressed", String(scene.showGrid));
  });
  /* 结构着色：实体 → 实体+棱线 → 纯线框（三态循环） */
  const wireBtn = $("btn-wire");
  const WIRE_SEQ = ["edges", "wire", "solid"];
  const WIRE_LABEL = { solid: "实体", edges: "实体 + 线框", wire: "纯线框" };
  const syncWireBtn = () => {
    if (!wireBtn) return;
    const mode = scene.wireMode || "edges";
    const on = mode !== "solid";
    wireBtn.classList.toggle("active", on);
    wireBtn.setAttribute("aria-pressed", String(on));
    wireBtn.setAttribute("data-mode", mode);
    wireBtn.title = "结构着色：" + WIRE_LABEL[mode] + "（点击切换）";
    wireBtn.setAttribute("aria-label", "结构着色：" + WIRE_LABEL[mode]);
    /* G38：纯线框下没有面，剖面盖板无处可填 → 自动收掉剖面并禁用按钮 */
    const wireOnly = mode === "wire";
    if (secBtn && !secBtn.dataset.capOff) {
      const blocked = wireOnly && !!scene.setSection;
      if (blocked && scene.showClip) {
        scene.setSection(false);
        if (typeof toast === "function") {
          toast("纯线框模式下已关闭剖面（没有面可截）", "warn");
        }
      }
      secBtn.disabled = !!blocked;
      secBtn.classList.toggle("is-disabled", !!blocked);
      secBtn.title = blocked ? "剖面切片（纯线框模式下不可用）" : "剖面切片";
      if (blocked) syncSecPanel();
    }
  };
  if (wireBtn) wireBtn.addEventListener("click", () => {
    if (wireBtn.disabled) return;
    const seq = (wireBtn.dataset.seq || "edges,wire,solid").split(",");
    const cur = scene.wireMode || "edges";
    const next = seq[(seq.indexOf(cur) + 1) % seq.length] || "edges";
    scene.setWireframeMode(next);
    syncWireBtn();
  });
  const lbl = $("btn-labels");
  if (lbl) lbl.addEventListener("click", (e) => {
    scene.setLabels(!scene.showLabels);
    e.currentTarget.classList.toggle("active", scene.showLabels);
    e.currentTarget.setAttribute("aria-pressed", String(scene.showLabels));
  });
  /* btn-shot：3D 画布导出 PNG（对称批接线）。 */
  const shotBtn = $("btn-shot");
  if (shotBtn) shotBtn.addEventListener("click", () => {
    const cv = document.querySelector("#three-container canvas");
    if (!cv) { toast("无 3D 画布", "warn"); return; }
    const a = document.createElement("a");
    a.download = "genie_3d_" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".png";
    a.href = cv.toDataURL("image/png");
    a.click();
    toast("已导出 3D 截图", "ok");
  });
  /* results 工具钮已随抽屉退役（2026-10-10 站长裁决：结果=标签卡+信息条，
   * 历史查看是文件管理的事）。 */

  /* ── G39 能力表闸门（2026-10-21）──
   * 2D 降级（WebGL 不可用）只画等距投影，坐标尺/线框三态/X 光/剖面/透视/
   * 选中高亮都没有实现。以前按钮照样能点、点了没反应；现在按
   * `scene.capabilities` 置灰并写明原因——"不可用"要看得见。
   * （G41 2026-10-30：优先级着色按钮随存在权裁决删除——每个几何体本来
   *   就有材料色，叠一层色阶是多余的认知负担。） */
  const cap = scene.capabilities || {};
  const gate = (id, ok, why) => {
    const b = $(id);
    if (!b || ok) return;
    b.disabled = true;
    b.classList.add("is-disabled");
    b.dataset.capOff = "1";
    if (why) b.title = b.title + "（" + why + "）";
  };
  const no3D = "2D 降级不可用";
  gate("btn-xray", cap.xray !== false, no3D);
  gate("btn-ruler", cap.rulers !== false, no3D);
  gate("btn-proj", cap.projection !== false, no3D);
  gate("btn-sec", cap.section !== false, no3D);
  if (cap.wireModes && cap.wireModes.indexOf("wire") < 0 && wireBtn) {
    /* 2D 只有实体/线框两态：循环序列砍掉纯线框 */
    wireBtn.dataset.seq = "edges,solid";
  }
  syncWireBtn();
}

function updateScene() {
  if (!scene) return;
  try { scene.updateScene(displayCfg || cfg || { domain: {}, mesh: {} }); } catch { /* 渲染失败不拦主流程 */ }
  renderRibbonMeta();
}

/* Ribbon 右端遥测（2026-10-21）：把模型的"仪器读数"写到工具条右端——
 * 域尺寸（µm）/ 对象数 / 材料数。纯显示，不接任何交互（pointer-events:none）。
 * 挂在 updateScene 上：脚本预览与表单改动都汇聚到这里，读数恒新。
 * 渲染纪律：逐节点拼装（零 innerHTML）。 */
function renderRibbonMeta() {
  const el = $("ribbon-meta");
  if (!el) return;
  if (!cfg) { el.replaceChildren(); return; }
  const dom = cfg.domain || {};
  const span = (r) => {
    if (!Array.isArray(r) || r.length < 2) return null;
    const v = Math.abs(Number(r[1]) - Number(r[0]));
    return isFinite(v) ? v * 1e6 : null;
  };
  const dims = [span(dom.x_range), span(dom.y_range), span(dom.z_range)]
    .filter((v) => v != null);
  const n = (k) => ((cfg && cfg[k]) || []).length;
  const rows = [];
  if (dims.length === 3) {
    const fmt = (v) => (v >= 10 ? v.toFixed(1) : v.toFixed(2));
    rows.push(["Domain", dims.map(fmt).join(" × ") + " µm"]);
  }
  rows.push(["Objects", String(n("geometry") + n("source") + n("monitor"))]);
  rows.push(["Materials", String(n("materials"))]);
  const frag = document.createDocumentFragment();
  rows.forEach(([label, value], i) => {
    if (i) {
      const sep = document.createElement("i");
      sep.textContent = "·";
      frag.appendChild(sep);
    }
    const b = document.createElement("b");
    b.textContent = label;
    frag.appendChild(b);
    frag.appendChild(document.createTextNode(value));
  });
  el.replaceChildren(frag);
}

/* ══ 表单编辑通道（阶段 5b 单模式）═══════════════════════════
 * 表单字段修改 → 活动 cfg（FormPanel 直写）→ 此处统一收口：
 * 3D/树/JSON 刷新 + 「模型已改」徽标 + debounce 服务端校验
 * （/api/validate，genie.models 同一双手）。重跑脚本 = 整体重建，
 * 表单改动被覆盖（语义如此，徽标随之熄灭）。 */
/* 轻提示（右下角，自动消失）——Run/Agent/工具条操作反馈 */


let _lastT = 0, fpsEMA = 0, fpsAccum = 0;
function animate(t) {
  if (scene) {
    scene.render();
    const dt = t - _lastT;
    if (dt > 0 && dt < 1000) {
      const f = 1000 / dt;
      fpsEMA = fpsEMA ? fpsEMA * 0.9 + f * 0.1 : f;
    }
    fpsAccum += dt;
    if (fpsAccum > 500) {
      fpsAccum = 0;
      const el = $("scene-fps");
      if (el) el.textContent = "FPS: " + Math.round(fpsEMA);
    }
  }
  _lastT = t;
  requestAnimationFrame(animate);
}
