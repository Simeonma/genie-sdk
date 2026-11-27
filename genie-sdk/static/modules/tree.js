import { $ } from "./util.js";
import { exec } from "../core/bus.js";
import { FormPanel } from "../views/form.js";
import { iconEl } from "../icons.js";
import { selectObj } from "./inspector.js";
import { GEO_TYPE_LABEL, previewEl } from "../views/previews.js";
import { matColor } from "../views/scene3d.js";
import { TREE_META, cfg, toast, treeGroupOpen, treePreviewIcon, treeSel } from "./state.js";
/* modules/tree.js — 左树（普通树：一行一渲染器，2026-11-24 站长拍）
 *
 * 统一规则（替代旧 band/组头/项 三层三套样式）：
 * - 每行同一 DOM 形状（箭头槽 + 图标 + 标签 + 计数）同一套 CSS；
 * - folder 行（有子行）：箭头与行点击都是折叠/展开；
 * - 叶子行：点击 = 选中进检查器（selectObj）；
 * - 缩进按深度 --d 档位递进；虚拟滚动沿用（行高恒 28px）。 */

/* 未命名对象行退回的可读类型名（几何走 previews.js 的 GEO_TYPE_LABEL，
 * 源/监视器走本表——行内只留一个词，见 mkRowEl 的命名注释）。 */
export const KIND_TYPE_LABEL = {
  soft_source: "Dipole", plane_wave: "Plane wave", gaussian_beam: "Gaussian",
  time: "Time", freq: "Freq", index: "Index",
};

/* ══ 树（普通树） ═══════════════════════════════════════════════ */
export function renderTree() {
  const root = $("obj-tree");
  if (!root) return;
  root.setAttribute("role", "tree");
  root.setAttribute("aria-label", "场景内容树");

  const TREE_VIRTUAL_THRESHOLD = 250;
  const TREE_ROW_H = 28;          /* 虚拟模式行高（CSS 同步钉死，见 style.css） */
  const OVERSCAN = 6;

  const model = [];
  const open = (key) => treeGroupOpen[key] !== false;

  /* folder 行：{key, count?, select?}——select 给了则行点击 = 选中进检查器
   * （箭头仍折叠），不给则行点击 = 折叠。叶子行：kind/i 进 selectObj，
   * 图标三路（pv=渲染风预览键 / ic=线稿键 / 缺省=按对象类型 treePreviewIcon）。 */
  /* 返回 spec 本身（不要用 model.push 的返回值——那是数组长度，上一版
   * 把数字当 spec 写属性，严格模式下当场 TypeError、整棵树不渲染）。 */
  const folder = (label, key, depth, iconKey, count, select) => {
    const sp = { t: "row", depth, label, folder: { key, count, select }, iconKey };
    model.push(sp);
    return sp;
  };
  const leaf = (kind, i, label, depth, pv, ic) => {
    const sp = { t: "row", depth, kind, i, label, pv, ic };
    model.push(sp);
    return sp;
  };

  /* Parameters / fdtd 同款 select-folder：行点击进表单，箭头折叠。
   * 计数 0 时也保留节点（空参数页 = 参数系统入口）。 */
  folder("Parameters", "params", 0, "params",
    (cfg && cfg.parameters && Object.keys(cfg.parameters).length) || null,
    "params");

  folder(TREE_META.materials[1], "setup", 0, "materials",
    (cfg && cfg.materials || []).length);
  if (open("setup")) {
    ((cfg && cfg.materials) || []).forEach((item, i) =>
      leaf("materials", i, item.name || "?", 1));
  }

  /* 共享结构（不属于任何求解器——Q15 分层）：几何单独一节 */
  folder("Structures", "structs", 0, "geometry",
    (cfg && cfg.geometry || []).length);
  if (open("structs")) {
    ((cfg && cfg.geometry) || []).forEach((item, i) => {
      const fallback = GEO_TYPE_LABEL[item.type] || item.type;
      leaf("geometry", i, item.name || fallback, 1);
    });
  }

  const solvers = (cfg && cfg.solvers) || [];
  folder("Physics", "solvers", 0, "structure", solvers.length || null);
  if (open("solvers")) {
    /* 全局仿真设置（文档级，2026-11-26）：与 solver 卡同级，站 Physics 下 */
    leaf("global_source", null, "Global setting", 1, null, "params");
    for (const sv of solvers) {
      if (sv.kind !== "fdtd") {
        /* mode：与 fdtd 对称——行点击进 mode 卡片（数据走快照
         * solvers[].config 的 mode 槽）；mode 无 source/monitor（本征
         * 问题不吃激励），加密区子行待 L-13 mode 表单分段再长 */
        leaf("mode", null, sv.name, 1, "solver_" + (sv.kind || "mode"));
        continue;
      }
      /* fdtd：domain 是包络单件（每求解器一枚、不可 add），不设树节点——
       * 行点击直达 domain 表单（2026-11-24 站长拍；M5 升级为 solver 卡片）；
       * mesh = 带名加密区集合（Q30），**平铺**在 fdtd 下（不设容器层，
       * 基础网格 dx/dy/dz 住在 domain）；source/monitor 同款平铺 */
      const key = "solver:" + sv.name;
      folder(sv.name, key, 1, "solver_fdtd", null, "domain");
      if (!open(key)) continue;
      const conf = sv.config || {};
      (conf.mesh || []).forEach((m, i) =>
        leaf("mesh", i, m.name || `refine ${i + 1}`, 2, "mesh"));
      (conf.source || []).forEach((item, i) => {
        const fallback = KIND_TYPE_LABEL[item.type] || item.type;
        leaf("source", i, item.name || fallback, 2);
      });
      (conf.monitor || []).forEach((item, i) => {
        const fallback = KIND_TYPE_LABEL[item.type] || item.type;
        leaf("monitor", i, item.name || fallback, 2);
      });
    }
    if (!solvers.length) model.push({ t: "hint" });
  }

  /* 后处理配方（Q11）：模型级具名对象，不属于任何求解器——只读消费
   * 结果，不创建任何模型对象。配方全文随 cfg 快照来（gui_snapshot）。 */
  folder(TREE_META.analysis[1], "analysis", 0, "results",
    (cfg && cfg.analysis || []).length);
  if (open("analysis")) {
    ((cfg && cfg.analysis) || []).forEach((rec, i) =>
      leaf("analysis", i, rec.name || "?", 1));
    if (!((cfg && cfg.analysis) || []).length) model.push({ t: "hint" });
  }

  /* 高级研究：具名流程对象（有地址、可增删，与模型对象同权进树）。
   * 旧 Run ▸ Settings 叶随 Q38 退役（run 资源是提交参数，不是文档状态）。
   * PSO 占位：API 随 L-24 落地，先给节点不造假入口。 */
  folder("Advanced study", "adv", 0, "sweep", null);
  if (open("adv")) {
    leaf("sweep", null, "Sweep", 1, null, "sweep");
    const pso = leaf(null, null, "PSO", 1, null, "menu");
    pso.placeholder = "PSO 优化器随 L-24 落地后接线";
  }

  /* ══ 物化器：一行一渲染器 ═══════════════════════════════════ */
  /* 树内改名（2026-11-25 站长拍）：双击名字 → contenteditable；Enter/失焦提交，
   * Esc 取消。地址按家族取当前名（solver 名已是字段，直接当地址）。 */
  const solverNamed = (kind) => {
    const sv = ((cfg && cfg.solvers) || []).find(
      (s) => (s.kind || "fdtd") === kind);
    return sv ? sv.name : null;
  };
  const renameTarget = (sp) => {
    const at = (list, i) => (((list || [])[i] || {}).name || null);
    if (sp.folder && sp.folder.select === "domain") {
      return { addr: solverNamed("fdtd"), name: solverNamed("fdtd") };
    }
    if (sp.kind === "mode") return { addr: sp.label, name: sp.label };
    if (sp.kind === "materials")
      return { addr: "material." + at(cfg.materials, sp.i), name: at(cfg.materials, sp.i) };
    if (sp.kind === "geometry")
      return { addr: "geometry." + at(cfg.geometry, sp.i), name: at(cfg.geometry, sp.i) };
    if (sp.kind === "analysis")
      return { addr: "analysis." + at(cfg.analysis, sp.i), name: at(cfg.analysis, sp.i) };
    if (sp.kind === "source" || sp.kind === "monitor" || sp.kind === "mesh") {
      const fd = solverNamed("fdtd");
      const name = at(cfg[sp.kind], sp.i);
      return fd && name ? { addr: fd + "." + name, name } : null;
    }
    return null;
  };
  const makeRenameable = (el, target, row) => {
    el.title = "点一下选中，再点名字改名（或双击）";
    const startEdit = (e) => {
      e.stopPropagation();
      e.preventDefault();
      const cur = target();
      if (!cur || !cur.name) return;
      el.contentEditable = "true";
      el.focus();
      try {
        const sel = window.getSelection();
        sel.selectAllChildren(el);
      } catch { /* selection unavailable */ }
      let cancelled = false;
      const done = () => {
        el.contentEditable = "false";
        const next = (el.textContent || "").trim();
        if (cancelled || !next || next === cur.name) {
          el.textContent = cur.name;
          return;
        }
        try {
          exec("genie.set_named(" + JSON.stringify(cur.addr) + ", name="
            + JSON.stringify(next) + ")", { echo: false }).catch(() => {
            el.textContent = cur.name;
          });
        } catch {                      /* 总线不可用：还原，不留假名 */
          el.textContent = cur.name;
        }
      };
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); el.blur(); }
        else if (ev.key === "Escape") { ev.preventDefault(); cancelled = true; el.blur(); }
      });
      el.addEventListener("blur", done, { once: true });
    };
    el.addEventListener("dblclick", startEdit);
    el.addEventListener("click", (e) => {
      /* 首次点击是选中（selectObj 会重画树）——只在“已经选中”的行上进入编辑 */
      if (!row.classList.contains("active")) return;
      startEdit(e);
    });
  };

  const mkRowEl = (sp) => {
    const row = document.createElement("div");
    /* 宽松等号：单件叶子的 i 是 undefined，选中态的 i 是 null——二者同义；
     * select-folder（如 fdtd→domain）随其选中目标点亮 */
    const active = sp.folder
      ? (!!sp.folder.select && treeSel.kind === sp.folder.select)
      : (sp.kind != null && treeSel.kind === sp.kind && treeSel.i == sp.i);
    row.className = "tree-item" + (sp.folder ? " tree-folder" : "")
      + (sp.placeholder ? " tree-ph" : "")
      + (active ? " active" : "");
    row.style.setProperty("--d", sp.depth || 0);
    row.tabIndex = 0;
    row.setAttribute("role", sp.folder ? "button" : "treeitem");
    if (sp.folder) {
      const openNow = open(sp.folder.key);
      row.setAttribute("aria-expanded", openNow ? "true" : "false");
      const ar = document.createElement("span");
      ar.className = "tree-arrow";
      ar.textContent = openNow ? "▾" : "▸";
      ar.setAttribute("aria-hidden", "true");
      if (sp.folder.select) {
        /* select-folder：箭头自己折叠，别冒泡成行选中 */
        ar.addEventListener("click", (e) => {
          e.stopPropagation();
          treeGroupOpen[sp.folder.key] = !open(sp.folder.key);
          renderTree();
        });
      }
      row.appendChild(ar);
    } else {
      /* 箭头槽占位：叶子无箭头，但图标列与 folder 行对齐 */
      const pad = document.createElement("span");
      pad.className = "tree-arrow-pad";
      pad.setAttribute("aria-hidden", "true");
      row.appendChild(pad);
      row.setAttribute("aria-selected", active ? "true" : "false");
    }
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); }
    });
    const ic = document.createElement("span");
    ic.className = "tree-ic";
    ic.setAttribute("aria-hidden", "true");
    if (sp.folder) {
      const key = sp.iconKey || "";
      if (key.startsWith("solver_")) {
        ic.classList.add("tree-pv");
        ic.appendChild(previewEl(key));      /* fdtd 用自家图标，不退回三角 */
      } else {
        ic.appendChild(iconEl(key));
      }
    } else if (sp.pv) {
      ic.classList.add("tree-pv");
      if (TREE_META[sp.kind]) ic.classList.add(TREE_META[sp.kind][0]);
      ic.appendChild(previewEl(sp.pv));
    } else if (sp.ic) {
      if (TREE_META[sp.kind]) ic.classList.add(TREE_META[sp.kind][0]);
      ic.appendChild(iconEl(sp.ic));
    } else {
      if (TREE_META[sp.kind]) ic.classList.add(TREE_META[sp.kind][0]);
      treePreviewIcon(ic, sp.kind, sp.i);
    }
    row.appendChild(ic);
    /* 材料色标（2026-10-21）：材料行在名字前列一枚 8×8 色块——3D 里
     * 实体按材料上色，树是"颜色↔材料"的对照表；色源同 3D（color 字段
     * 优先，缺省散列调色板）。几何行不重复显示（避免一屏色块噪声）。 */
    if (sp.kind === "materials") {
      const mt = (cfg && cfg.materials || [])[sp.i] || {};
      let hex = "#" + (typeof matColor === "function" ? matColor(mt.name) : 0x8E9BB4)
        .toString(16).padStart(6, "0");
      if (typeof mt.color === "string" && /^#?[0-9a-fA-F]{6}$/.test(mt.color.trim())) {
        hex = "#" + mt.color.trim().replace(/^#/, "");
      }
      const dot = document.createElement("span");
      dot.className = "tree-swatch";
      dot.style.background = hex;
      dot.title = "材料显示色 " + hex.toUpperCase();
      dot.setAttribute("aria-hidden", "true");
      row.appendChild(dot);
    }
    const nm = document.createElement("span");
    nm.className = "tree-label";
    nm.textContent = sp.label;
    const rt = renameTarget(sp);
    if (rt && rt.name) makeRenameable(nm, () => renameTarget(sp), row);
    row.appendChild(nm);
    if (sp.folder && sp.folder.count != null) {
      const c = document.createElement("span");
      c.className = "tree-count";
      c.textContent = sp.folder.count;
      row.appendChild(c);
    }
    if (sp.folder) {
      if (sp.folder.select) {
        /* 行点击 = 选中进检查器（fdtd → domain 表单；M5 升级 solver 卡片） */
        row.addEventListener("click", () => selectObj(sp.folder.select));
      } else {
        row.addEventListener("click", () => {
          treeGroupOpen[sp.folder.key] = !open(sp.folder.key);
          renderTree();
        });
      }
    } else if (sp.kind != null) {
      /* 复制/删除叉已删（L-35）：写腿（意图/拼码）随 M4/M7 重接 */
      row.addEventListener("click", () => selectObj(sp.kind, sp.i));
    }
    if (sp.placeholder) row.title = sp.placeholder;
    return row;
  };

  const mkHintEl = () => {
    const hint = document.createElement("div");
    hint.className = "tree-empty-hint";
    const ic = iconEl("brandmark");
    ic.setAttribute("aria-hidden", "true");
    ic.classList.add("tree-hint-ic");
    hint.appendChild(ic);
    hint.appendChild(document.createTextNode(
      cfg ? "空模型——终端里 add_material / add_geometry，或在检查器里填 domain"
          : "登录后即可编辑；也可以粘贴脚本按 Run"));
    return hint;
  };

  const materialize = (sp) => {
    if (sp.t === "row") return mkRowEl(sp);
    return mkHintEl();
  };

  /* ══ 小配置：一次性物化 ═════════════════════════════════════ */
  if (model.length <= TREE_VIRTUAL_THRESHOLD) {
    root.classList.remove("tree-virtual");
    root.textContent = "";
    for (const sp of model) root.appendChild(materialize(sp));
    return;
  }

  /* ══ 虚拟滚动：垫层 + 可视窗口物化（万级行 ~40 个 DOM）═══ */
  root.classList.add("tree-virtual");
  root.textContent = "";
  const vspacer = document.createElement("div");
  vspacer.className = "tree-vspacer";
  vspacer.style.height = model.length * TREE_ROW_H + "px";
  const vwin = document.createElement("div");
  vwin.className = "tree-vwin";
  root.appendChild(vspacer);
  root.appendChild(vwin);

  const drawSlice = () => {
    const start = Math.max(0, Math.floor(root.scrollTop / TREE_ROW_H) - OVERSCAN);
    const end = Math.min(model.length,
      start + Math.ceil(root.clientHeight / TREE_ROW_H) + OVERSCAN * 2);
    vwin.innerHTML = "";
    vwin.style.transform = "translate3d(0," + start * TREE_ROW_H + "px,0)";
    for (let i = start; i < end; i++) {
      const el = materialize(model[i]);
      el.style.position = "absolute";
      el.style.top = (i - start) * TREE_ROW_H + "px";
      el.style.left = "0"; el.style.right = "0";
      vwin.appendChild(el);
    }
  };
  let rafPending = false;
  root.addEventListener("scroll", () => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; drawSlice(); });
  });
  drawSlice();
}

/* ══ 检查器（FormPanel 只读驱动） ═════════════════════════════ */
