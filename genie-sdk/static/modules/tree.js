/* modules/tree.js — 左树（v1 renderTree 同款 DOM）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */

/* 未命名对象行退回的可读类型名（几何走 previews.js 的 GEO_TYPE_LABEL，
 * 源/监视器走本表——行内只留一个词，见 renderTree 的命名注释）。 */
const KIND_TYPE_LABEL = {
  soft_source: "Dipole", plane_wave: "Plane wave", gaussian_beam: "Gaussian",
  time: "Time", freq: "Freq", index: "Index",
};

/* ══ 树（v1 renderTree 同款 DOM；器官在、接线无） ═════════════ */
function renderTree() {
  const root = $("obj-tree");
  if (!root) return;
  root.setAttribute("role", "tree");
  root.setAttribute("aria-label", "场景内容树");

  /* ══ 行规格模型（2026-10-15 站长裁决：树虚拟滚动）═══
   * 先建"行描述"模型（不碰 DOM），再按规模二选一：
   *   ≤ TREE_VIRTUAL_THRESHOLD 行 → 一次性物化（小配置零行为变化）
   *   超阈值 → 虚拟滚动：只物化可视窗口 ± overscan 的行，
   *   万级几何也只要 ~40 个 DOM 行（预览图标/按钮随窗口按需建） */
  const TREE_VIRTUAL_THRESHOLD = 250;
  const TREE_ROW_H = 28;          /* 虚拟模式行高（CSS 同步钉死，见 style.css） */
  const OVERSCAN = 6;

  const model = [];
  const pushHead = (label, key, count, band) =>
    model.push({ t: "head", label, key, count, band });
  const pushInfo = (label, kind) => model.push({ t: "info", label, kind });
  const pushObj = (kind, i, label) => model.push({ t: "obj", kind, i, label });

  const open = (key) => treeGroupOpen[key] !== false;

  pushHead("Global", "global", null, true);
  if (open("global")) {
    const n = (cfg && cfg.parameters && Object.keys(cfg.parameters).length) || 0;
    pushInfo(n ? `Parameters (${n})` : "Parameters", "params");
  }

  pushHead(TREE_META.materials[1], "setup",
    (cfg && cfg.materials || []).length, true);
  if (open("setup")) {
    ((cfg && cfg.materials) || []).forEach((item, i) =>
      pushObj("materials", i, item.name || "?"));
  }

  pushHead("Scene", "content", null, true);
  if (open("content")) {
    pushInfo(TREE_META.domain[1], "domain");
    pushInfo(TREE_META.mesh[1], "mesh");
    for (const kind of ["geometry", "source", "monitor"]) {
      const list = (cfg && cfg[kind]) || [];
      list.forEach((item, i) => {
        /* 2026-10-21 用户定案：「对象行只给一个名字」——不再拼 "名字 · 类型 ·
         * 材料" 三段。有名字用名字，没名字退回可读类型名。 */
        const fallback = GEO_TYPE_LABEL[item.type]
          || KIND_TYPE_LABEL[item.type] || item.type;
        pushObj(kind, i, item.name || fallback);
      });
    }
    const hasSceneObject = ["geometry", "source", "monitor"].some(
      (k) => ((cfg && cfg[k]) || []).length > 0);
    if (!hasSceneObject) model.push({ t: "hint" });
  }

  pushHead("Run", "run", null, true);
  if (open("run")) {
    /* 扫参与 Settings 并列（2026-10-30 站长裁）：扫描是"怎么跑"的事，
     * 不是参数表的事——入口挂在 Run 组里，参数页回归纯单值表。 */
    pushInfo("Settings", "run");
    pushInfo("Sweep", "sweep");
  }

  /* ══ 物化器：spec → DOM 行（与原 mk* 逐一对应，行为零变化）═══ */
  const mkGroupHeadEl = (sp) => {
    const openNow = treeGroupOpen[sp.key] !== false;
    const g = document.createElement("div");
    g.className = "tree-group-head" + (openNow ? "" : " closed")
      + (sp.band ? " tree-band" : "");
    g.tabIndex = 0;
    g.setAttribute("role", "button");
    g.setAttribute("aria-expanded", openNow ? "true" : "false");
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); g.click(); }
    });
    const ar = document.createElement("span");
    ar.className = "tree-arrow";
    ar.textContent = openNow ? "▾" : "▸";
    ar.setAttribute("aria-hidden", "true");
    g.appendChild(ar);
    const lb = document.createElement("span");
    lb.className = "tree-group-label";
    lb.textContent = sp.label;
    g.appendChild(lb);
    if (sp.count != null) {
      const c = document.createElement("span");
      c.className = "tree-count";
      c.textContent = sp.count;
      g.appendChild(c);
    }
    g.addEventListener("click", () => {
      treeGroupOpen[sp.key] = !openNow;
      renderTree();
    });
    return g;
  };

  const mkInfoEl = (sp) => {
    const row = document.createElement("div");
    const active = treeSel.kind === sp.kind && treeSel.i == null;
    row.className = "tree-item" + (active ? " active" : "");
    row.tabIndex = 0;
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-selected", active ? "true" : "false");
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); }
    });
    const ic = document.createElement("span");
    ic.className = "tree-ic";
    ic.setAttribute("aria-hidden", "true");
    ic.classList.add(TREE_META[sp.kind][0]);
    /* Domain/Mesh 与对象类型同族（2026-10-17 图标族统一）：渲染风预览；
     * 其余单例（run/params）仍是 18px 线稿。 */
    if (["domain", "mesh", "run", "params"].includes(sp.kind)) {
      ic.classList.add("tree-pv");
      ic.appendChild(previewEl(sp.kind));
    } else {
      ic.appendChild(iconEl(TREE_META[sp.kind][0]));
    }
    row.appendChild(ic);
    const nm = document.createElement("span");
    nm.className = "tree-label";
    nm.textContent = sp.label;
    row.appendChild(nm);
    row.addEventListener("click", () => selectObj(sp.kind));
    return row;
  };

  const mkObjectRowEl = (sp) => {
    const row = document.createElement("div");
    const active = treeSel.kind === sp.kind && treeSel.i === sp.i;
    row.className = "tree-item" + (active ? " active" : "");
    row.tabIndex = 0;
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-selected", active ? "true" : "false");
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); }
    });
    const ic = document.createElement("span");
    ic.className = "tree-ic";
    ic.setAttribute("aria-hidden", "true");
    ic.classList.add(TREE_META[sp.kind][0]);
    treePreviewIcon(ic, sp.kind, sp.i);
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
    row.appendChild(nm);
    const cp = document.createElement("button");
    cp.className = "tree-copy mini";
    cp.appendChild(iconEl("copy"));
    cp.title = `复制 ${sp.label}（插入副本到下一行）`;
    cp.setAttribute("aria-label", `复制 ${sp.label}`);
    cp.addEventListener("click", (e) => {
      e.stopPropagation();
      const list = cfg && cfg[sp.kind];
      if (!Array.isArray(list) || !list[sp.i]) return;
      const copy = JSON.parse(JSON.stringify(list[sp.i]));
      if (copy && copy.name) copy.name = `${copy.name}_copy`;
      list.splice(sp.i + 1, 0, copy);
      onFormMutate();
      selectObj(sp.kind, sp.i + 1);
      toast(`已复制 ${sp.label}`, "ok");
    });
    row.appendChild(cp);
    const x = document.createElement("button");
    x.className = "tree-del mini";
    x.appendChild(iconEl("close"));
    x.title = `删除 ${sp.label}`;
    x.setAttribute("aria-label", `删除 ${sp.label}`);
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      const list = cfg && cfg[sp.kind];
      if (!Array.isArray(list) || !list[sp.i]) return;
      list.splice(sp.i, 1);
      onFormMutate();
      toast(`已删除 ${sp.label}（重跑脚本将整体重建）`, "info");
    });
    row.appendChild(x);
    row.addEventListener("click", () => selectObj(sp.kind, sp.i));
    return row;
  };

  const mkHintEl = () => {
    const hint = document.createElement("div");
    hint.className = "tree-empty-hint";
    const ic = iconEl("sparkle");
    ic.setAttribute("aria-hidden", "true");
    ic.classList.add("tree-hint-ic");
    hint.appendChild(ic);
    hint.appendChild(document.createTextNode(
      /* 2026-10-21：新建模型现在是**空文档**（无任何默认值），不再是
       * "脚本没跑"——引导语要指向表单与工具栏，而不是让人去写脚本。 */
      cfg ? "空模型——用上方工具栏加材料 / 结构，或在检查器里填 domain"
          : "登录后即可编辑；也可以粘贴脚本按 Run"));
    return hint;
  };

  const materialize = (sp) => {
    if (sp.t === "head") return mkGroupHeadEl(sp);
    if (sp.t === "info") return mkInfoEl(sp);
    if (sp.t === "obj") return mkObjectRowEl(sp);
    return mkHintEl();
  };

  /* ══ 小配置：一次性物化（原路径，零行为变化）═══ */
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
