import { $ } from "./util.js";
import { exec } from "../core/bus.js";
import { previewEl } from "../views/previews.js";
import { cfg } from "./state.js";
import { exitResult, resultMode } from "./result.js";
import { runBuild, runMode, runModel } from "./term.js";
/* modules/toolbar.js — 工具条（M1 重接：按钮 → exec → 公开动词）
 *
 * 本批接回（Q44 白名单：add_solver / run_fdtd / build_fdtd / run_mode）：
 * - DESIGN ▸ Solver：fdtd / mode 声明按钮（genie.add_solver，省 name）
 * - FDTD ▸ Simulation：Run（run_fdtd）+ Build（build_fdtd 预演）
 * - MODE ▸ Simulation：Run（run_mode 缺省实例）
 * 加对象组（材料/几何/源/监视器/分析）仍随 M3-M7 逐模块重接。
 * 页签显隐由 term.js syncSolverTabs 按 cfg.solvers 投影。 */
"use strict";

export function buildToolbar() {
  /* DESIGN ▸ Solver：声明按钮（站长拍 2026-11-24：没有缺省求解器）。 */
  const solverBtns = $("tb-solver-btns");
  if (solverBtns) {
    for (const t of ["fdtd", "mode"]) {
      const b = document.createElement("button");
      b.className = "tb-btn tb-add";
      b.type = "button";
      b.title = "声明一个 " + t + " 求解器（genie.add_solver(type='" + t + "')）";
      b.appendChild(previewEl("solver_" + t));
      b.appendChild(document.createTextNode(t));
      b.addEventListener("click", () => {
        /* 声明 = 只建包络；默认值是 payload/view 边界的纯函数（L-62），
         * GUI 不写种子。 */
        exec("genie.add_solver(type='" + t + "')",
          { echo: true }).catch(() => {});
      });
      solverBtns.appendChild(b);
    }
  }

  /* FDTD ▸ Mesh：带名加密区（genie.<fdtd>.add_mesh(name)）；名字自动编号，
   * 因为 add_mesh 必须带名字（不像 add_solver 能省略）。 */
  const meshBtns = $("tb-mesh-btns");
  if (meshBtns) {
    const b = document.createElement("button");
    b.className = "tb-btn tb-add";
    b.type = "button";
    b.title = "加一个带名加密区（genie.<fdtd>.add_mesh(name)）：box 区间 + 每轴 factor 或绝对步长";
    b.appendChild(previewEl("mesh"));
    b.appendChild(document.createTextNode("Refinement"));
    b.addEventListener("click", () => {
      const sv = ((cfg && cfg.solvers) || []).find((x) => x.kind === "fdtd");
      const solver = (sv && sv.name) || "fdtd";
      const taken = new Set((((sv && sv.config) || {}).mesh || [])
        .map((m) => m.name));
      let name = "mesh", i = 1;
      while (taken.has(name)) { i += 1; name = "mesh_" + i; }
      exec("genie." + solver + ".add_mesh(" + JSON.stringify(name) + ")",
        { echo: true }).catch(() => {});
    });
    meshBtns.appendChild(b);
  }

  /* FDTD ▸ Sources：soft source（偶极子式点源）+ plane wave（平面波）。
   * 名字自动编号；声明只建对象，默认值由 payload/view 的 resolver 给（L-62）。 */
  const srcBtns = $("tb-src-btns");
  if (srcBtns) {
    const mkSourceBtn = (type, label, prefix) => {
      const b = document.createElement("button");
      b.className = "tb-btn tb-add";
      b.type = "button";
      b.title = `加一个${label}（genie.<fdtd>.add_source(name, '${type}')）`;
      b.appendChild(previewEl(type));
      b.appendChild(document.createTextNode(label));
      b.addEventListener("click", () => {
        const sv = ((cfg && cfg.solvers) || []).find((x) => x.kind === "fdtd");
        const solver = (sv && sv.name) || "fdtd";
        const taken = new Set((((sv && sv.config) || {}).source || [])
          .map((x) => x.name));
        let name = prefix, i = 1;
        while (taken.has(name)) { i += 1; name = prefix + "_" + i; }
        exec("genie." + solver + ".add_source(" + JSON.stringify(name)
          + ", '" + type + "')", { echo: true }).catch(() => {});
      });
      return b;
    };
    srcBtns.appendChild(mkSourceBtn("soft_source", "Soft source", "source"));
    srcBtns.appendChild(mkSourceBtn("plane_wave", "Plane wave", "source"));
    srcBtns.appendChild(mkSourceBtn("gaussian_beam", "Gaussian beam", "source"));
    srcBtns.appendChild(mkSourceBtn("mode", "Mode source", "source"));
  }

  /* FDTD ▸ Simulation：Run（主 CTA，两态）+ Build（预演，伴生钮）。 */
  const fdtdRun = $("tb-fdtd-run-btns");
  if (fdtdRun) {
    const run = document.createElement("button");
    run.className = "run-btn";
    run.id = "submit";
    run.appendChild(previewEl("play"));
    const span = document.createElement("span");
    span.id = "submit-label";
    span.textContent = "Run";
    run.appendChild(span);
    run.addEventListener("click", () => {
      if (resultMode) exitResult();   /* 结果态 = Switch to setting */
      else runModel();                /* 编辑态 = 提交当前 fdtd 模型 */
    });
    fdtdRun.appendChild(run);

    const build = document.createElement("button");
    build.className = "tb-btn tb-add";
    build.type = "button";
    build.title = "本地预演（genie.build_fdtd()）：网格/步数检查，不提交云端";
    build.appendChild(previewEl("build"));
    build.appendChild(document.createTextNode("Build"));
    build.addEventListener("click", () => runBuild());
    fdtdRun.appendChild(build);
    runBtnRefresh();
  }

  /* MODE ▸ Simulation：Run（genie.run_mode()，缺省实例名 mode）。 */
  const modeRun = $("tb-mode-run-btns");
  if (modeRun) {
    const b = document.createElement("button");
    b.className = "run-btn";
    b.type = "button";
    b.title = "运行 mode 求解（genie.run_mode()；多实例走脚本给名）";
    b.appendChild(previewEl("play"));
    b.appendChild(document.createTextNode("Run"));
    b.addEventListener("click", () => runMode());
    modeRun.appendChild(b);
  }

  /* VIEW 页的 3D 显示开关镜像：真身仍是 3D 浮条上的 #btn-*。 */
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
        setTimeout(syncViewMirror, 0);
      });
      viewBtns.appendChild(b);
    }
    syncViewMirror();
  }
  pruneEmptyGroups();
}

/* 空组不占位（2026-11-25）：组的存在由按钮决定——没有按钮就不画组名与
 * 发丝线。M3-M7 把按钮接进 #tb-*-btns 后重跑本函数，组自动出现；不要用
 * display 硬隐藏（组间发丝线靠 :not(.is-empty) 相邻选择器）。 */
export function pruneEmptyGroups() {
  document.querySelectorAll("#workspace-bar .tb-box").forEach((b) => {
    const btns = b.querySelector(".tb-box-btns");
    b.classList.toggle("is-empty", !btns || !btns.children.length);
  });
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
    : "提交当前 fdtd 模型（genie.run_fdtd()；进度滚在终端）";
}
