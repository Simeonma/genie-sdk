/* modules/toolbar.js — 工具条（加对象组 → 默认对象入 cfg；Run 按钮两态）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ══ 工具条（阶段 7 后接线：点击 → 默认对象入活动 cfg → 全视图联动） ══ */
let _autoN = 0;

function _firstMatName() {
  if (cfg && cfg.materials && cfg.materials.length) return cfg.materials[0].name;
  /* 无材料时自动补一个 const */
  if (!cfg) cfg = { domain: {}, mesh: {}, materials: [], geometry: [], source: [], monitor: [], parameters: {}, run: {} };
  if (!cfg.materials) cfg.materials = [];
  const m = { type: "const", eps: 2.1, mu: 1.0, name: "auto_const_1" };
  cfg.materials.push(m);
  return m.name;
}

function _ensureCfg() {
  if (!cfg) cfg = { domain: {}, mesh: {}, materials: [], geometry: [], source: [], monitor: [], parameters: {}, run: {} };
  for (const k of ["materials", "geometry", "source", "monitor"])
    if (!cfg[k]) cfg[k] = [];
}

/** 往活动 cfg 追加默认对象 → 全视图联动（铁律 1 同一条编辑通道）。 */
function addObject(kind, type) {
  _ensureCfg();
  const n = ++_autoN;
  const mat = _firstMatName();
  let item = null, arr = null;
  if (kind === "mat") {
    arr = cfg.materials;
    item = type === "lorentz"
      ? { type: "lorentz", eps_inf: 1.5, mu_inf: 1.0, poles: [], mu_poles: [], name: `auto_lorentz_${n}` }
      : type === "from_library"
      ? { type: "from_library", name: `auto_lib_${n}`, library: "au",
          fit_range: [4e-7, 8e-7], n_drude: 0, n_lorentz: 2, maxfev: 5000, quiet: true }
      : { type: "const", eps: 2.1, mu: 1.0, name: `auto_const_${n}` };
  } else if (kind === "geo") {
    arr = cfg.geometry;
    const base = { priority: 0, material: mat };
    /* 2026-10-21 修：默认参数必须与线格式（genie/models/_wire.py 的 8 个
     * GeometrySpec）逐字段对齐——此前 cylinder 缺 height、ngon_z 写 radius+
     * z_range、ellipse_z 写 a/b、ring_z 写 inner/outer，加了就 422 或被 3D
     * 当成缺省尺寸渲染（"加了不显示/尺寸不对"）。字段名以 spec 为准：
     *   cylinder/sphere/ngon_z/ellipse_z/ring_z → center + height(=z 挤出长度)
     *   ngon_z → n_sides + circumradius；ellipse_z → rx/ry；ring_z → inner/outer_radius */
    const H = 2e-7;                      /* 挤出高度（z 方向）统一用 200 nm */
    const G = {
      box:           () => ({ ...base, type: "box",           x_range: [-1e-7, 1e-7], y_range: [-1e-7, 1e-7], z_range: [-1e-7, 1e-7] }),
      sphere:        () => ({ ...base, type: "sphere",        center: [0, 0, 0], radius: 1e-7 }),
      cylinder:      () => ({ ...base, type: "cylinder",      center: [0, 0, 0], radius: 1e-7, height: H }),
      ngon_z:        () => ({ ...base, type: "ngon_z",        center: [0, 0, 0], n_sides: 6, circumradius: 1.2e-7, height: H }),
      ellipse_z:     () => ({ ...base, type: "ellipse_z",     center: [0, 0, 0], rx: 1.2e-7, ry: 6e-8, height: H }),
      ring_z:        () => ({ ...base, type: "ring_z",        center: [0, 0, 0], inner_radius: 5e-8, outer_radius: 1.1e-7, height: H }),
      polygon_z:     () => ({ ...base, type: "polygon_z",     vertices: [[0, 0], [8e-8, 0], [4e-8, 7e-8]], z_range: [-1e-7, 1e-7] }),
      polygon_swept: () => ({ ...base, type: "polygon_swept", vertices: [[0, 0], [8e-8, 0], [4e-8, 7e-8]], z_range: [-1e-7, 1e-7] }),
    };
    item = (G[type] || G.box)();
  } else if (kind === "src") {
    arr = cfg.source;
    /* 2026-10-21 修：源默认参数同样按线格式对齐——`freq` 是标量（Hz）+
     * `bandwidth`（Hz）为必填，此前写成 `freq: {center,bw}` 对象且漏
     * bandwidth，从工具条加任何源都 422；高斯束的孔径字段在 spec 里叫
     * `radius`（不是 waist_radius）。 */
    const F = 3e14, BW = 6e13;
    const S = {
      soft_source:   () => ({ type: "soft_source", delay: 0, phase: 0, name: `auto_dipole_${n}`, center: [0, 0, -2e-7], size: [0, 0, 0], polarization: [[1, 0], [0, 0], [0, 0]], amplitude: 1, kind: "electric", freq: F, bandwidth: BW }),
      plane_wave:    () => ({ type: "plane_wave", delay: 0, phase: 0, name: `auto_pw_${n}`, direction: "+", amplitude: 1, center: null, theta: 0, phi: 0, psi: 0, size: null, propagation_axis: null, from_face: null, freq: F, bandwidth: BW }),
      gaussian_beam: () => ({ type: "gaussian_beam", delay: 0, phase: 0, name: `auto_gb_${n}`, center: [0, 0, -2e-7], size: [4e-7, 4e-7, 0], amplitude: 1, direction: "+", radius: 2e-7, freq: F, bandwidth: BW }),
    };
    item = (S[type] || S.soft_source)();
  } else if (kind === "mon") {
    arr = cfg.monitor;
    const M = {
      time:  () => ({ type: "time", center: [0, 0, 2e-7], size: [0, 0, 0], down_sample: null, name: `auto_time_${n}`, field: "Ex" }),
      freq:  () => ({ type: "freq", center: [0, 0, 2e-7], size: [2e-7, 2e-7, 2e-7], down_sample: null, apodize: null, name: `auto_freq_${n}`, field: "all", freq: { center: 3e14, bw: 1e14, n: 21 } }),
      index: () => ({ type: "index", center: [0, 0, 0], size: [null, null, null], down_sample: null, name: `auto_index_${n}`, field: "eps" }),
    };
    item = (M[type] || M.time)();
  }
  if (!item || !arr) return;
  arr.push(item);
  onFormMutate();
  /* 树选中新条目 + 表单联动（selectObj 是树/表单的选中真源） */
  const kindFull = kind === "mat" ? "materials"
    : kind === "geo" ? "geometry"
    : kind === "src" ? "source" : "monitor";
  selectObj(kindFull, arr.length - 1);
}

/** Run 按钮 = 提交**当前会话模型**（POST /api/run）。
 *
 * M3 起这是一等的意图，不是"往终端里打一行 f.run()"：那个写法让按钮的
 * 语义取决于终端命名空间里恰好有个没被换掉的 f（还要操心 f 是否已提交、
 * 终端是否在忙）。现在它提交的是模型本身，模型一个字节不动。
 * 无 cfg（没跑过脚本也没动过表单）时给指引——这是「点了没反应」的历史成因。 */
async function toolbarRun() {
  const c = (typeof window.getCfg === "function") ? window.getCfg() : null;
  if (!c) { toast("还没有可运行的仿真——先运行脚本，或在表单里搭好配置", "warn"); return; }
  await runModel();
}

function buildToolbar() {
  const mkShape = (type) => {
    const b = document.createElement("button");
    b.className = "tb-btn tb-shape";
    const glabel = GEO_TYPE_LABEL[type] || type;
    b.title = "添加 " + glabel + "（默认参数入活动 cfg）";
    b.setAttribute("aria-label", "添加 " + glabel);
    b.appendChild(previewEl(type));
    b.addEventListener("click", () => addObject("geo", type));
    return b;
  };
  const geoBtns = $("tb-geo-btns");
  if (geoBtns)
    ["box", "sphere", "cylinder", "ngon_z", "ellipse_z", "ring_z",
     "polygon_z", "polygon_swept"].forEach((t) => geoBtns.appendChild(mkShape(t)));
  const mkLabeled = (kind, type, iconKey, label) => {
    const b = document.createElement("button");
    b.className = "tb-btn tb-add";
    b.title = "添加 " + label + "（默认参数入活动 cfg）";
    b.appendChild(previewEl(iconKey));
    b.appendChild(document.createTextNode(label));
    b.addEventListener("click", () => addObject(kind, type));
    return b;
  };
  const matBtns = $("tb-mat-btns");
  if (matBtns) {
    for (const [t, label] of [["const", "const"], ["lorentz", "lorentz"]])
      matBtns.appendChild(mkLabeled("mat", t, "mat_" + t, label));
    /* G20：library = 拟合对话框（pole 入 cfg）。不能复用 mkLabeled——
     * 它挂的 addObject 会塞进线格式明确拒收的 from_library 死节点。 */
    const libBtn = document.createElement("button");
    libBtn.className = "tb-btn tb-add";
    libBtn.title = "库材料（nk → pole 拟合，按当前波段现算）";
    libBtn.appendChild(previewEl("mat_library"));
    libBtn.appendChild(document.createTextNode("library"));
    libBtn.addEventListener("click", () => openNkFitDialog());
    matBtns.appendChild(libBtn);
  }
  const srcBtns = $("tb-src-btns");
  if (srcBtns) {
    srcBtns.appendChild(mkLabeled("src", "soft_source", "soft_source", "dipole"));
    srcBtns.appendChild(mkLabeled("src", "plane_wave", "plane_wave", "plane wave"));
    srcBtns.appendChild(mkLabeled("src", "gaussian_beam", "gaussian_beam", "gaussian"));
  }
  const monBtns = $("tb-mon-btns");
  if (monBtns) {
    monBtns.appendChild(mkLabeled("mon", "time", "mon_time", "time"));
    monBtns.appendChild(mkLabeled("mon", "freq", "mon_freq", "freq"));
    monBtns.appendChild(mkLabeled("mon", "index", "mon_index", "index"));
  }
  /* Structure 区已删（2026-10 站长裁决：固定结构组合以示例脚本库提供，
     载入示例即入口；无 structure 概念——v1 结构子程序被脚本整体吸收）。
     Agent 已迁顶栏（2026-10-17 站长裁决）：不再进 ribbon。 */

  /* VIEW 页的 3D 显示开关镜像（2026-10-17 四页签）：真身仍是 3D 浮条上的
   * #btn-*，这里只做第二入口——点击转发给真身，aria-pressed 由
   * syncViewMirror() 在切页/点击后同步。 */
  const viewBtns = $("tb-view-btns");
  if (viewBtns) {
    for (const [src, icon, label] of [
      ["btn-fitcam", "view_fit", "复位视角"],
      ["btn-proj", "view_proj", "透视 / 正交"],
      ["btn-prio", "view_priority", "按优先级着色"],
      ["btn-sec", "view_section", "剖面切片"],
      ["btn-wire", "view_wire", "结构线框"],
      ["btn-ruler", "view_ruler", "边框坐标"],
      ["btn-xray", "view_xray", "X 光"],
      ["btn-grid", "view_grid", "参考网格"],
      ["btn-labels", "view_labels", "标签"],
      ["btn-shot", "view_shot", "截图"],
    ]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tb-btn view-mirror-btn";
      b.id = "view-" + src;
      b.dataset.preview = icon;
      b.title = label;
      b.setAttribute("aria-label", label);
      b.addEventListener("click", () => {
        const orig = $(src);
        if (!orig) return;
        orig.click();
        setTimeout(syncViewMirror, 0);   /* 真身先翻转 aria-pressed，再回填镜像 */
      });
      viewBtns.appendChild(b);
    }
    syncViewMirror();
  }
  /* Run 按钮：单按钮双语义（2026-10-14 站长裁决，替代 2026-10-09/10-11
   * 的"主钮恒 Run + 返回编辑小钮"形态）：编辑态 = Run（真提交）；结果态
   * = Switch to setting（回编辑，零提交）。重跑 = 回编辑后再点 Run。 */
  const runMount = $("run-mount");
  if (runMount && !runMount.firstChild) {
    const b = document.createElement("button");
    b.className = "run-btn";
    b.id = "submit";
    b.appendChild(previewEl("run"));   /* 灰蓝 3D 双 play（Lumerical 形制，无蓝底） */
    const span = document.createElement("span");
    span.id = "submit-label";
    span.textContent = "Run";
    b.appendChild(span);
    b.addEventListener("click", () => {
      if (window.__resultMode) exitResult();   /* 结果态 = Switch to setting */
      else toolbarRun();                        /* 编辑态 = Run 真提交 */
    });
    runMount.appendChild(b);
    runBtnRefresh();
  }
}

/* VIEW 镜像状态同步：真身的 aria-pressed 是唯一真源，镜像只读它。 */
function syncViewMirror() {
  for (const id of ["btn-fitcam", "btn-proj", "btn-prio", "btn-sec", "btn-wire",
                    "btn-ruler", "btn-xray", "btn-grid", "btn-labels", "btn-shot"]) {
    const a = $(id), b = $("view-" + id);
    if (!a || !b) continue;
    const p = a.getAttribute("aria-pressed");
    if (p !== null) b.setAttribute("aria-pressed", p);
  }
}
window.syncViewMirror = syncViewMirror;

/* Run 按钮文案恒定（2026-10-11 站长裁决：点 Run 就真提交——
 * 结果模式只是展示视图，新一轮提交自动离开它） */
function runBtnRefresh() {
  /* 单按钮双语义的文案面（2026-10-14 站长裁决）：结果态显示
   * Switch to setting，编辑态显示 Run。 */
  const l = $("submit-label");
  if (l) l.textContent = window.__resultMode ? "Switch to setting" : "Run";
  const b = $("submit");
  if (b) b.title = window.__resultMode
    ? "返回编辑模式（不提交）——解锁表单继续调整"
    : "运行当前配置的仿真（域/网格/结构/源/监视器/步数——即表单所见；进度滚在下方 terminal）";
}

/* 校验条（v1 val-bar 复用为 preview 状态条） */
