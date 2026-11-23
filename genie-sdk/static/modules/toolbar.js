import { $ } from "./util.js";
import { exec } from "../core/bus.js";
import { openLibraryDialog } from "../faces/material.js";
import { addIntents, sendIntents } from "./edit.js";
import { selectObj } from "./inspector.js";
import { GEO_TYPE_LABEL, previewEl } from "../views/previews.js";
import { exitResult, resultMode } from "./result.js";
import { cfg, toast } from "./state.js";
import { runModel } from "./term.js";
/* modules/toolbar.js — 工具条（加对象组 → 默认对象入 cfg；Run 按钮两态）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ══ 工具条（阶段 7 后接线：点击 → 默认对象入活动 cfg → 全视图联动） ══ */
export let _autoN = 0;

export function _matNames() {
  return ((cfg && cfg.materials) || []).map((m) => m && m.name).filter(Boolean);
}

/* 加对象 = 一条动作 → 身份 + 字段意图直发（2026-11-23 纯投影终态：
 * 不写本地模型、不靠 diff 反推；界面等快照回来重画）。
 * 结构/源/监视器需要材料——没有就明说，不悄悄补一个（服务端会拒）。 */
export function addObject(kind, type) {
  const key = kind === "mat" ? "materials" : kind === "geo" ? "geometry"
    : kind === "src" ? "source" : "monitor";
  const list = (cfg && cfg[key]) || [];
  let mat = null;
  if (kind !== "mat") {
    const mats = _matNames();
    if (!mats.length) {
      toast("先添加一个材料（Materials → const）", "warn");
      return;
    }
    mat = mats[0];
  }
  const n = ++_autoN;
  let item = null;
  if (kind === "mat") {
    item = type === "lorentz"
      ? { type: "lorentz", eps_inf: 1.5, mu_inf: 1.0, poles: [], mu_poles: [], name: `auto_lorentz_${n}` }
      : { type: "const", eps: 2.1, mu: 1.0, name: `auto_const_${n}` };
  } else if (kind === "geo") {
    const base = { priority: 0, material: mat };
    const H = 2e-7;
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
    const F = 3e14, BW = 6e13;
    const S = {
      soft_source:   () => ({ type: "soft_source", delay: 0, phase: 0, name: `auto_dipole_${n}`, center: [0, 0, -2e-7], size: [0, 0, 0], polarization: [[1, 0], [0, 0], [0, 0]], amplitude: 1, kind: "electric", freq: F, bandwidth: BW }),
      plane_wave:    () => ({ type: "plane_wave", delay: 0, phase: 0, name: `auto_pw_${n}`, direction: "+", amplitude: 1, center: null, theta: 0, phi: 0, psi: 0, size: null, propagation_axis: null, from_face: null, freq: F, bandwidth: BW }),
      gaussian_beam: () => ({ type: "gaussian_beam", delay: 0, phase: 0, name: `auto_gb_${n}`, center: [0, 0, -2e-7], size: [4e-7, 4e-7, 0], amplitude: 1, direction: "+", radius: 2e-7, freq: F, bandwidth: BW }),
    };
    item = (S[type] || S.soft_source)();
  } else if (kind === "mon") {
    const M = {
      time:  () => ({ type: "time", center: [0, 0, 2e-7], size: [0, 0, 0], down_sample: null, name: `auto_time_${n}`, field: "Ex" }),
      freq:  () => ({ type: "freq", center: [0, 0, 2e-7], size: [2e-7, 2e-7, 2e-7], down_sample: null, apodize: null, name: `auto_freq_${n}`, field: "all", freq: { center: 3e14, bw: 1e14, n: 21 } }),
      index: () => ({ type: "index", center: [0, 0, 0], size: [null, null, null], down_sample: null, name: `auto_index_${n}`, field: "eps" }),
    };
    item = (M[type] || M.time)();
  }
  if (!item) return;
  if (!item.name) {
    const base = "auto_" + type + "_";
    const taken = new Set(list.map((x) => x && x.name).filter(Boolean));
    let k = n;
    while (taken.has(base + k)) k += 1;
    item.name = base + k;
  }
  sendIntents(addIntents(kind, item.name, type, item));
  const name = item.name;
  /* 快照回来后才在树里选中（索引由服务端决定） */
  setTimeout(() => {
    const arr = (cfg && cfg[key]) || [];
    const idx = arr.findIndex((x) => x && x.name === name);
    if (idx >= 0) selectObj(key, idx);
  }, 700);
}

export async function toolbarRun() {
  const c = getCfg();
  if (!c) { toast("还没有可运行的仿真——先运行脚本，或在表单里搭好配置", "warn"); return; }
  await runModel();
}

export function buildToolbar() {
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
    libBtn.title = "库材料：genie.add_material(name=名, type='library', library=名)";
    libBtn.appendChild(previewEl("mat_library"));
    libBtn.appendChild(document.createTextNode("library"));
    libBtn.addEventListener("click", () => openLibraryDialog());
    matBtns.appendChild(libBtn);
  }
  const solverBtns = $("tb-solver-btns");
  if (solverBtns) {
    for (const t of ["fdtd", "mode"]) {
      const b = document.createElement("button");
      b.className = "tb-btn tb-add";
      b.type = "button";
      b.title = "添加一个 " + t + " 求解器（genie.add_solver(type='"
        + t + "')）";
      b.appendChild(document.createTextNode(t));
      b.addEventListener("click", () => {
        exec("genie.add_solver(type='" + t + "')",
                      { echo: true }).catch(() => {});
      });
      solverBtns.appendChild(b);
    }
  }
  const anaBtns = $("tb-ana-btns");
  if (anaBtns) {
    /* 2026-11-22：后处理配方（Q11）——一句命令；实例名由 SDK 起
     * （add_analysis 省 name = analysis/analysis_2/…）。 */
    const b = document.createElement("button");
    b.className = "tb-btn tb-add";
    b.type = "button";
    b.title = "添加后处理配方（genie.add_analysis()）";
    b.appendChild(document.createTextNode("analysis"));
    b.addEventListener("click", () => {
      exec("genie.add_analysis()", { echo: true }).catch(() => {});
    });
    anaBtns.appendChild(b);
  }
  const srcBtns = $("tb-src-btns");  if (srcBtns) {
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

  /* VIEW 页的 3D 显示开关镜像（2026-10-17 四页签）：真身仍是 3D 浮条上的
   * #btn-*，这里只做第二入口——点击转发给真身，aria-pressed 由
   * syncViewMirror() 在切页/点击后同步。 */
  const viewBtns = $("tb-view-btns");
  if (viewBtns) {
    for (const [src, icon, label] of [
      ["btn-fitcam", "view_fit", "复位视角"],
      ["btn-proj", "view_proj", "透视 / 正交"],
      ["btn-sec", "view_section", "剖面切片"],
      ["btn-wire", "view_wire", "结构线框"],
      ["btn-ruler", "view_ruler", "边框坐标"],
      ["btn-xray", "view_xray", "X 光"],
      ["btn-grid", "view_grid", "参考网格"],
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
      if (resultMode) exitResult();   /* 结果态 = Switch to setting */
      else toolbarRun();                        /* 编辑态 = Run 真提交 */
    });
    runMount.appendChild(b);
    runBtnRefresh();
  }
}

/* VIEW 镜像状态同步：真身的 aria-pressed 是唯一真源，镜像只读它。 */
export function syncViewMirror() {
  for (const id of ["btn-fitcam", "btn-proj", "btn-sec", "btn-wire",
                    "btn-ruler", "btn-xray", "btn-grid", "btn-shot"]) {
    const a = $(id), b = $("view-" + id);
    if (!a || !b) continue;
    const p = a.getAttribute("aria-pressed");
    if (p !== null) b.setAttribute("aria-pressed", p);
  }
}

export function runBtnRefresh() {
  const l = $("submit-label");
  if (l) l.textContent = resultMode ? "Switch to setting" : "Run";
  const b = $("submit");
  if (b) b.title = resultMode
    ? "返回编辑模式（不提交）——解锁表单继续调整"
    : "运行当前配置的仿真（域/网格/结构/源/监视器/步数——即表单所见；进度滚在下方 terminal）";
}

/* 校验条（v1 val-bar 复用为 preview 状态条） */
