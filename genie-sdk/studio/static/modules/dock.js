/* modules/dock.js — 工作台 dock 内核（2026-10-15 站长裁决：五面板模块化）
 *
 * 模型：布局 = 一棵 split 树。
 *   叶子 = 面板堆栈 { stack: [面板id...], active: i }（单面板也统一此形）；
 *   内节点 = { dir: "row"|"col", sizes: [px|null...], children: [...] }，
 *   sizes 里唯一的 null = 弹性子（吸收余量）。
 *   面板 id：tree-panel / config-panel / scene-panel / term-dock / json-panel；
 *   DOM 原件被收养进 .dock-leaf 包裹层（render 先收养再清空——innerHTML
 *   会连原件一起销毁，2026-10-15 实测翻车）。
 *
 * 持久化纪律：布局只是「脸」的摆法（铁律 1 的边界）——存浏览器
 * localStorage（genie.dock.layout.v1），绝不进服务端/会话状态。
 * 载入时校验（面板集合不齐即弃用回默认），防旧版残留脏数据。
 *
 * 期①：splitter 拖拽调宽高 + 持久化。
 * 期②：面板拖拽 dock——手柄/标签拖出 → 落区四边 dock 或中心合堆；
 *       树结构变更 = 纯树变换（detach→prune→insert），render 全量重建。
 * 期③：布局预设（Dock.preset(name)），见文件尾 PRESETS。
 */
"use strict";

const Dock = (function () {
  const STORAGE_KEY = "genie.dock.layout.v1";
  const SAVED_KEY = "genie.dock.saved.v1";   /* 用户命名布局：{name: tree} */
  /* 结构｜属性工作面（2026-10 站长裁决）：tree/config 结构上合为
   * #struct-panel 一个原件（内部 42:58 + 拖拽分割），dock 只认它。 */
  const PANEL_IDS = ["struct-panel", "scene-panel", "visualize-panel", "files-view", "term-dock", "json-panel"];
  const TITLE = { "struct-panel": "Objects",
                  "scene-panel": "3D Scene", "visualize-panel": "Visualize",
                  "files-view": "Files", "term-dock": "Terminal", "json-panel": "Script" };

  /* ── 布局只有一档（2026-10-30 站长裁：移动端整体删除）──────────────
   * 桌面形态按视口比例分配；窄窗只影响比例（fitSizes 会按比例收），
   * 不再有第二套形态/第二套导航。MIN 只读。 */
  const DESKTOP_MIN = { "struct-panel": 310, "scene-panel": 200,
                        "visualize-panel": 160, "term-dock": 80, "json-panel": 200 };
  const MIN = DESKTOP_MIN;

  /* 默认布局按视口比例分配。 */
  function defaultTree() {
    /* 桌面唯一形态（2026-10 case 收敛）：左 结构｜属性，右 3D/结果/文件
     * + Script，下 Terminal；宽度只影响比例，不再有第二套形态 */
    const w = window.innerWidth, h = window.innerHeight;
    {
      const left = Math.max(DESKTOP_MIN["struct-panel"] + 8, Math.round(w * 0.34));
      const right = Math.max(DESKTOP_MIN["json-panel"], Math.round(w * 0.28));
      return norm({ dir: "col", sizes: [null, Math.min(150, Math.round(h * 0.17))],
        children: [
          { dir: "row", sizes: [left, null, right], children: [
            "struct-panel",
            { stack: ["scene-panel", "visualize-panel", "files-view"], active: 0 },
            "json-panel"] },
          "term-dock",
        ] });
    }
    return norm({ dir: "row", sizes: [left, null, right],
      children: ["struct-panel", {
        dir: "col", sizes: [null, Math.min(230, Math.round(h * 0.25))],
        children: [
          { dir: "row", sizes: [null, right],
            children: [["scene-panel", "visualize-panel", "files-view"], "json-panel"] },
          "term-dock",
        ],
      }] });
  }
  const isLeaf = (n) => !n.dir;   /* 叶子 = {stack:[...]}；内节点有 dir */
  /* "id" ↔ {stack:[id], active:0} 归一化（含 localStorage 旧格式） */
  function norm(n) {
    if (Array.isArray(n)) return { stack: n, active: 0 };
    if (typeof n === "string") return { stack: [n], active: 0 };
    if (n.stack) return n;                       /* 已是归一化叶（loadLayout 回读） */
    n.children = n.children.map(norm);
    return n;
  }

  let tree = null;
  let ro = null;                 // ResizeObserver：dock 改容器尺寸 → 3D 重投影
  let uidSeq = 0;
  const adopted = new Map();     // id → 面板 DOM 原件
  const adoptedActions = new Map(); // id → 动作钮原件（同收养纪律）
  /* load-py/download-py 归脚本面板头（语义针对 script，站长裁决不随
   * 迁）；term-dl/import-cfg/export-cfg 在 File 下拉，不进收养清单。 */
  const ACTION_IDS = ["run-py", "load-py", "download-py", "term-stop",
                      "term-reset"];
  const leafByUid = new Map();   // uid → 叶子对象（drop 落点 → 树节点）

  /* ── 持久化 ── */
  function load() {
    try {
      const t = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (!t || leafSet(t).size !== PANEL_IDS.length) return null;
      return norm(t);
    } catch { return null; }
  }
  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tree)); } catch {}
  }
  function leafSet(t) {
    const s = new Set();
    (function walk(n) {
      if (typeof n === "string") { s.add(n); return; }
      if (Array.isArray(n)) { n.forEach((x) => s.add(x)); return; }
      if (n.stack) { n.stack.forEach((x) => s.add(x)); return; }   /* 对象叶（norm 形态） */
      n.children.forEach(walk);
    })(t);
    return s;
  }

  /* ── 渲染 ── */
  function el(tag, cls) {
    const d = document.createElement(tag);
    if (cls) d.className = cls;
    return d;
  }

  function render(node, root) {
    PANEL_IDS.forEach((id) => {
      if (!adopted.has(id)) {
        const p = document.getElementById(id);
        if (p) { p.classList.add("dock-adopted"); adopted.set(id, p); }
      }
    });
    ACTION_IDS.forEach((id) => {
      if (!adoptedActions.has(id)) {
        const b = document.getElementById(id);
        if (b) { b.classList.add("dock-adopted"); adoptedActions.set(id, b); }
      }
    });
    /* 收养原件先摘离 main-layout——render 的 innerHTML="" 会销毁一切
     * 未摘离的子树；面板由 build 重新挂载尚可幸免，但「当前活动面板
     * 头部用不到的动作钮」（如移动模式 3D 激活时的 download-py）会
     * 游离被毁 → boot 按钮接线空引用（2026-10 移动模式实测）。 */
    adopted.forEach((p) => { if (p.parentNode) p.remove(); });
    adoptedActions.forEach((b) => { if (b.parentNode) b.remove(); });
    root.innerHTML = "";
    /* 未入当前树的面板 + 未被当前头部收养的动作钮 → 停放区
     * （main-layout 外，永不销毁；换模式/切面板时从停放区复活） */
    const parking = document.getElementById("dock-action-parking");
    const stacked = leafSet(tree);
    PANEL_IDS.forEach((id) => {
      const p = adopted.get(id);
      if (p && !p.parentNode && !stacked.has(id) && parking) parking.appendChild(p);
    });
    adoptedActions.forEach((b) => {
      if (!b.parentNode && parking) parking.appendChild(b);
    });
    root.classList.add("dock-root");
    leafByUid.clear();
    build(node, root);
    notifyScene();
  }

  function childStyle(dir, size, isSpring, node) {
    const axis = dir === "row" ? "width" : "height";
    const floor = minExtent(node, axis);
    if (isSpring) return `flex: 1 1 auto; min-${axis}: ${floor}px;`;
    return `flex: 0 0 ${size}px; min-${axis}: ${floor}px;`;
  }
  /* 子树最小伸展量（沿指定轴）：叶 = 面板 MIN（宽度语义；作为高度下界时取
   * 保守小值 CROSS_MIN）; 同轴节点 = 子取和（并排/叠放的累加）; 跨轴 = 取大
   * （最高的子决定容器下限）。
   * 2026-10-21 修：旧实现只算"沿 dir 轴"，却被 childStyle 拿去当交叉轴
   * （高度）下界用——横向三栏的 min-height 被写成 710px，把下方 Terminal
   * 的 220px 挤爆，输入行整整沉出视口。 */
  const CROSS_MIN = 120;
  function minExtent(node, axis = "height") {
    if (isLeaf(node)) {
      const m = MIN[node.stack[0]] ?? 80;
      return axis === "width" ? m : CROSS_MIN;
    }
    const same = (node.dir === "row") === (axis === "width");
    const mins = node.children.map((c) => minExtent(c, axis));
    return same ? mins.reduce((a, b) => a + b, 0) : Math.max(...mins);
  }

  function activateSideEffect(panelId) {
    if (panelId === "files-view" && window.loadWorkspaceFiles) loadWorkspaceFiles();
  }
  function build(node, host) {
    if (isLeaf(node)) {
      const leaf = el("div", "dock-leaf");
      const uid = "L" + (++uidSeq);
      leaf.dataset.uid = uid;
      leafByUid.set(uid, node);
      leaf.appendChild(makeHead(node));
      node.stack.forEach((id, i) => {
        const p = adopted.get(id);
        if (!p) return;
        p.classList.toggle("dock-hidden", i !== node.active);
        if (i === node.active) {
          p.classList.remove("hidden");            /* files-view 初始 hidden，点亮即揭 */
          activateSideEffect(id);
        }
        leaf.appendChild(p);
      });
      host.appendChild(leaf);
      return leaf;
    }
    const box = el("div", `dock-node dock-${node.dir}`);
    const spring = node.sizes.indexOf(null);
    node.children.forEach((c, i) => {
      if (i > 0) box.appendChild(makeSplitter(node, i));
      const w = el("div", "dock-cell");
      w.style.cssText = childStyle(node.dir, node.sizes[i], i === spring, node.children[i]);
      box.appendChild(w);
      build(c, w);
    });
    host.appendChild(box);
    return box;
  }

  /* ── 统一面板标题栏（2026-10-15 站长裁决②）──
   * 每个可 dock 的叶子一条 34px 头：单面板 = 标题 + 动作钮右组；
   * 合堆 = 标签排（与 view-tab 同款式）。拖拽 = 抓头/抓标签。
   * （scene 的 view-tabbar 仅在多页签——有 Plot——时出现，见 result.js） */
  const PANEL_ACTIONS = {                              /* 面板 → 动作钮（原件收养） */
    /* 动作钮右组：文件 I/O 已上收 File 菜单，面板头各留唯一动作钮 */
    /* 动作钮右组：仿真 open/save 在 File 菜单，脚本头留 Run+脚本两钮 */
    "json-panel": ["run-py", "load-py", "download-py"],
    "term-dock": ["term-stop", "term-reset"],   /* R31：清理重启随停止收养 */
  };
  function makeHead(leafObj) {
    const head = el("div", "dock-head");
    const active = leafObj.stack[leafObj.active];
    if (leafObj.stack.length > 1) {
      head.appendChild(makeTabs(leafObj));
    } else {
      const title = el("span", "dock-head-title");
      title.textContent = TITLE[active] || active;
      /* 抓标题文字 = 拖拽（裁决③）；按下即拖（与页签同款阈值在 beginDrag
       * 之前的 makeTabs 才有——标题这里直接进拖拽，激活无二义） */
      title.addEventListener("pointerdown", (e) => {
        if (e.target.closest("button")) return;
        beginDrag(e, active, leafObj);
      });
      head.appendChild(title);
    }
    // 动作钮右组：随活动面板换装（按钮原件全局唯一，收养进头）
    const ids = PANEL_ACTIONS[active] || [];
    if (ids.length) {
      const acts = el("div", "dock-head-actions");
      ids.forEach((id) => { const b = adoptedActions.get(id); if (b) acts.appendChild(b); });
      head.appendChild(acts);
    }
    /* 拖拽手势（2026-10 站长裁决③）：只认「标签文字」——单面板叶 =
     * 抓标题文字，合堆叶 = 抓页签（makeTabs 内置）。头部空白处不再
     * 是抓手（误拖整窗的来源），按钮照常排除。 */
    return head;
  }
  function makeTabs(leafObj) {
    const bar = el("div", "dock-tabs");
    leafObj.stack.forEach((id, i) => {
      const t = el("div", "dock-tab" + (i === leafObj.active ? " active" : ""));
      t.textContent = TITLE[id] || id;
      t.dataset.panel = id;
      let dragged = false, suppress = false;
      const activate = () => { leafObj.active = i; save(); renderAll(); activateSideEffect(leafObj.stack[i]); };
      t.addEventListener("pointerdown", (e) => {
        dragged = false;
        const onMove = (ev) => { if (Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) > 4) { dragged = true; suppress = true; cleanup(); beginDrag(ev, id, leafObj); } };
        const onUp = () => { cleanup(); if (!dragged) activate(); };
        const cleanup = () => { window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onUp); };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
      });
      /* 程序化 .click() 不产生 pointer 事件（e2e ⑰ 同款）——click 通路补齐；
       * suppress = 刚拖拽完的余波 click 不回卷激活 */
      t.addEventListener("click", () => { if (!suppress) activate(); suppress = false; });
      bar.appendChild(t);
    });
    return bar;
  }

  /* ── 期② 拖拽 dock ── */
  let drag = null;   // { panelId, fromLeaf, ghost }

  function beginDrag(e, panelId, fromLeaf) {
    if (drag) return;
    e.preventDefault();
    const ghost = el("div", "dock-drag-ghost");
    ghost.textContent = TITLE[panelId] || panelId;
    document.body.appendChild(ghost);
    drag = { panelId, fromLeaf, ghost };
    document.body.classList.add("dock-dragging-panel");
    moveGhost(e);
    window.addEventListener("pointermove", onDragMove);
    window.addEventListener("pointerup", onDragUp);
  }
  function moveGhost(e) {
    drag.ghost.style.left = e.clientX + 10 + "px";
    drag.ghost.style.top = e.clientY + 8 + "px";
  }
  function onDragMove(e) {
    if (!drag) return;
    moveGhost(e);
    const hit = dropTargetAt(e.clientX, e.clientY);
    showHint(hit);
  }
  function dropTargetAt(x, y) {
    const under = document.elementFromPoint(x, y);
    const leafEl = under && under.closest ? under.closest(".dock-leaf") : null;
    if (!leafEl || !leafByUid.has(leafEl.dataset.uid)) return null;
    if (leafEl.dataset.uid === uidOfLeaf(drag && drag.fromLeaf)) {
      /* 源叶自落：单面板叶 = 无处可落；合堆叶 = 允许（把页签拆到本窗
       * 四边是正当操作，2026-10 站长实测交互缺陷①） */
      const lf = leafByUid.get(leafEl.dataset.uid);
      if (!lf || lf.stack.length <= 1) return null;
    }
    const r = leafEl.getBoundingClientRect();
    const dx = (x - r.left) / r.width, dy = (y - r.top) / r.height;
    /* 五落区（2026-10 站长裁决②）：上/下/左/右 25% 边带 = 四向 dock，
     * 其余（含角区按最近边归属）= 中心合堆。阴影始终画「半边」或全格。 */
    const m = Math.min(dx, 1 - dx, dy, 1 - dy);
    let zone = "center";
    if (m < 0.25) {
      const cands = [["west", dx], ["east", 1 - dx], ["north", dy], ["south", 1 - dy]];
      cands.sort((a, b) => a[1] - b[1]);
      zone = cands[0][0];
    }
    return { leafEl, uid: leafEl.dataset.uid, zone };
  }
  function showHint(hit) {
    let h = document.getElementById("dock-drop-hint");
    if (!h) { h = el("div", "dock-drop-hint"); h.id = "dock-drop-hint"; document.body.appendChild(h); }
    if (!hit) { h.style.display = "none"; return; }
    const r = hit.leafEl.getBoundingClientRect();
    const inset = { west: [0, 0.5, 0, 1], east: [0.5, 1, 0, 1],
                    north: [0, 1, 0, 0.5], south: [0, 1, 0.5, 1], center: [0, 1, 0, 1] }[hit.zone];
    /* west 曾误写 [0,0,0.5,1]（right 分量=0）——阴影宽度恒 0、只剩
     * 边框 4px 的下半截（2026-10 站长目检缺陷①的实锤根因） */
    h.style.display = "block";
    h.style.left = r.left + r.width * inset[0] + "px";
    h.style.right = window.innerWidth - (r.left + r.width * inset[1]) + "px";
    h.style.top = r.top + r.height * inset[2] + "px";
    h.style.bottom = window.innerHeight - (r.top + r.height * inset[3]) + "px";
  }
  function onDragUp(e) {
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", onDragUp);
    const hit = dropTargetAt(e.clientX, e.clientY);
    showHint(null);
    document.body.removeChild(drag.ghost);
    document.body.classList.remove("dock-dragging-panel");
    const d = drag; drag = null;
    /* 同叶落点放行给 applyDrop（合堆叶四边拆出 = 正当操作；center 原地
     * 由 applyDrop 自行 no-op）——这里不能再拦源叶，否则同窗拖出被吞
     * （2026-10 站长实测缺陷②的第二处根因） */
    if (!hit) return;
    applyDrop(d.panelId, hit, d.fromLeaf);
  }
  function uidOfLeaf(leafObj) {
    if (!leafObj) return null;
    for (const [uid, l] of leafByUid) if (l === leafObj) return uid;
    return null;
  }

  /* 树变换：先 detach（空栈剪枝、单子节点塌缩）再按落区插入 */
  function detach(node, panelId) {
    if (isLeaf(node)) {
      if (!node.stack.includes(panelId)) return node;
      const stack = node.stack.filter((x) => x !== panelId);
      if (!stack.length) return null;
      return { stack, active: 0 };
    }
    const kids = node.children.map((c) => detach(c, panelId));
    const kept = [], sizes = [];
    kids.forEach((c, i) => {
      if (!c) return;
      kept.push(c); sizes.push(node.sizes[i]);
    });
    if (!kept.length) return null;
    if (kept.length === 1) return kept[0];
    return { dir: node.dir, sizes, children: kept };
  }
  function insert(node, targetLeaf, panelId, zone, half) {
    if (isLeaf(node)) {
      if (node !== targetLeaf) return node;
      if (zone === "center") {
        node.stack.push(panelId);
        node.active = node.stack.length - 1;
        return node;
      }
      // 四边 dock：目标叶一分为二（沿落区轴向）。尺寸守恒：新叶取一半但
      // 双方都不得跌破各自最小值——否则连续分栏会把窗口一路减半成缝
      // （2026-10-15 站长目检『拖动导致窗口不见』的实锤根因）。
      // half 由 applyDrop 从 DOM 预量传入——同窗拆出时 targetLeaf 是
      // detach 新建的对象，此处再查 DOM 必然扑空（2026-10 缺陷②根因）。
      const dir = zone === "west" || zone === "east" ? "row" : "col";
      const before = zone === "west" || zone === "north";
      return { dir, sizes: before ? [half, null] : [null, half],
               children: before ? [{ stack: [panelId], active: 0 }, node]
                                : [node, { stack: [panelId], active: 0 }] };
    }
    // 递归：目标叶在哪个子分支，就在该分支上做插入
    return { dir: node.dir,
             sizes: node.sizes.map((s) => s),
             children: node.children.map((c) => insert(c, targetLeaf, panelId, zone, half)) };
  }
  function findByStack(node, ids) {
    if (isLeaf(node)) return node.stack.join("|") === ids.join("|") ? node : null;
    for (const c of node.children) { const r = findByStack(c, ids); if (r) return r; }
    return null;
  }
  function applyDrop(panelId, hit, fromLeaf) {
    const targetLeaf = leafByUid.get(hit.uid);
    if (!targetLeaf) return;
    const wasSource = targetLeaf === fromLeaf;
    if (wasSource && hit.zone === "center") return;   /* 原地合堆 = 无事 */
    /* 源叶含 panelId 时 detach 会重建该叶对象——先记「剩余成员」，
     * 落树后按成员集合重定位目标叶（同窗拖出，2026-10 站长缺陷②） */
    const remain = targetLeaf.stack.filter((x) => x !== panelId);
    let t = detach(tree, panelId);
    if (!t) return;
    let dst = targetLeaf;
    if (wasSource) {
      dst = findByStack(t, remain);
      if (!dst) return;
    }
    /* 半宽预量：趁 DOM 还对得上当前树（hit.uid 的叶几何 = 拆出前窗口），
     * detach/insert 全程不再摸 DOM */
    const dir = hit.zone === "west" || hit.zone === "east" ? "row" : "col";
    const domLeaf = document.querySelector(`.dock-leaf[data-uid="${hit.uid}"]`);
    const full = domLeaf ? (dir === "row" ? domLeaf.offsetWidth
                                          : domLeaf.offsetHeight) : 300;
    const half = Math.max(120, Math.round(full / 2));   /* 弹性子由 min-floor 兜底 */
    t = insert(t, dst, panelId, hit.zone, half);
    tree = t;
    clampSizes(tree);
    save();
    renderAll();
  }
  /* 全树钳制：固定尺寸一律 >= 该叶首面板最小值（历史脏布局一并矫正） */
  function clampSizes(node) {
    if (isLeaf(node)) return;
    const spring = node.sizes.indexOf(null);
    node.children.forEach((c, i) => {
      if (i !== spring && Array.isArray(c)) {
        const m = MIN[c.stack[0]] ?? 80;
        if (node.sizes[i] !== null && node.sizes[i] < m) node.sizes[i] = m;
      }
      clampSizes(c);
    });
  }

  /* ── splitter：拖拽 = 相邻两 cell 沿轴让渡 px（弹性子只被动吸收） ── */
  function makeSplitter(node, idx) {
    const sp = el("div", `dock-split dock-split-${node.dir}`);
    sp.setAttribute("role", "separator");
    sp.setAttribute("aria-label", "拖拽调整面板大小");
    sp.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      sp.setPointerCapture(e.pointerId);
      const horizontal = node.dir === "row";
      const cells = [...sp.parentNode.children].filter((c) => c.classList.contains("dock-cell"));
      const before = cells[idx - 1], after = cells[idx];
      const start = horizontal ? e.clientX : e.clientY;
      const b0 = (horizontal ? before.offsetWidth : before.offsetHeight);
      const a0 = (horizontal ? after.offsetWidth : after.offsetHeight);
      const leafIds = cells.map(panelIdOf);
      const move = (ev) => {
        let d = (horizontal ? ev.clientX : ev.clientY) - start;
        const bMin = MIN[leafIds[idx - 1]] ?? 80, aMin = MIN[leafIds[idx]] ?? 80;
        d = Math.max(d, bMin - b0, -(a0 - aMin));
        applySize(before, node.dir, b0 + d);
        applySize(after, node.dir, a0 - d);
      };
      const up = () => {
        sp.removeEventListener("pointermove", move);
        sp.removeEventListener("pointerup", up);
        document.body.classList.remove("dock-dragging");
        saveFromDom(node);
        notifyScene();
      };
      document.body.classList.add("dock-dragging");
      sp.addEventListener("pointermove", move);
      sp.addEventListener("pointerup", up);
    });
    return sp;
  }

  function panelIdOf(cell) {
    const l = cell.querySelector(":scope > .dock-leaf");
    return l ? (leafByUid.get(l.dataset.uid)?.stack[0] ?? null) : null;
  }
  function applySize(cell, dir, px) {
    cell.style.flex = "0 0 " + Math.round(px) + "px";
    cell.style.minWidth = "0"; cell.style.minHeight = "0";
  }

  /* 拖拽后把 DOM 实测尺寸写回树的 sizes（弹性子保持 null） */
  function saveFromDom(node) {
    (function walk(n, host) {
      if (isLeaf(n)) return;
      const box = host.querySelector(":scope > .dock-node") || host;
      const cells = [...box.children].filter((c) => c.classList.contains("dock-cell"));
      const spring = n.sizes.indexOf(null);
      n.sizes = cells.map((c, i) => i === spring ? null
        : Math.round(n.dir === "row" ? c.offsetWidth : c.offsetHeight));
      n.children.forEach((c, i) => walk(c, cells[i]));
    })(tree, document.getElementById("main-layout"));
  }

  /* ── 场景重投影 ── */
  function notifyScene() {
    if (!ro) ro = new ResizeObserver(() => { if (typeof scene !== "undefined" && scene) scene.onResize(); });
    const c = document.getElementById("three-container");
    if (c) ro.observe(c);
  }

  function renderAll() {
    render(tree, document.getElementById("main-layout"));
  }
  /* 把某面板顶到前台（激活其所在叶的对应页签）。Plot 新建时调——
   * Visualize 可能不是当前活动页签，甚至被预设精简掉了：找不到时
   * 合堆进 3D 所在叶（Plot 的语义近邻），再退而求全局第一叶。 */
  function activatePanel(id) {
    let host = null;
    (function walk(n) {
      if (host || isLeaf(n)) { if (!host && isLeaf(n) && n.stack.includes(id)) host = n; return; }
      n.children.forEach(walk);
    })(tree);
    if (!host) {
      (function walk2(n) {
        if (host) return;
        if (isLeaf(n)) { if (n.stack.includes("scene-panel")) host = n; return; }
        n.children.forEach(walk2);
      })(tree);
    }
    if (!host) { const l = leafByUid.values().next().value; if (!l) return false; host = l; }
    const i = host.stack.indexOf(id);
    if (i >= 0) host.active = i;
    else { host.stack.push(id); host.active = host.stack.length - 1; }
    save();
    renderAll();
    activateSideEffect(id);
    return true;
  }

  /* ── #struct-split：结构｜属性内部比例拖拽（genie.struct.ratio）──
   * 与 dock 分离：这是面板内部分割，不入 dock 树、不随布局存档。 */
  (function wireStructSplit() {
    const wrap = document.getElementById("struct-panel");
    const split = document.getElementById("struct-split");
    const tp = document.getElementById("tree-panel");
    if (!wrap || !split || !tp) return;
    const KEY = "genie.struct.ratio";
    const apply = (r) => { tp.style.flexBasis = (r * 100).toFixed(1) + "%"; };
    let r = parseFloat(localStorage.getItem(KEY));
    apply(isFinite(r) && r > 0.15 && r < 0.85 ? r : 0.42);
    split.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      split.classList.add("active");
      const move = (ev) => {
        const rect = wrap.getBoundingClientRect();
        const nr = Math.min(0.85, Math.max(0.15, (ev.clientX - rect.left) / rect.width));
        apply(nr);
      };
      const up = (ev) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        split.classList.remove("active");
        const rect = wrap.getBoundingClientRect();
        const nr = (ev.clientX - rect.left) / rect.width;
        if (isFinite(nr) && nr > 0.05 && nr < 0.95) {
          try { localStorage.setItem(KEY, String(nr)); } catch {}
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  })();

  /* ── 进度 chip 归位（唯一形态：tabbar）──────────────────────────── */
  function applyMode() {
    const chip = document.getElementById("result-chip");
    const bar = document.getElementById("view-tabbar");
    if (chip && bar && chip.parentElement !== bar) bar.appendChild(chip);
  }
  /* 窗口变窄时按比例缩固定列（消横向溢出——935px 实测缺陷的收口） */
  let fitRaf = 0;
  function fitSizes() {
    if (!tree || isLeaf(tree)) return;
    const walk = (node, box) => {
      if (isLeaf(node) || !box) return;
      const nodeEl = box.querySelector(":scope > .dock-node");
      if (!nodeEl) return;
      const avail = node.dir === "row" ? nodeEl.clientWidth : nodeEl.clientHeight;
      const cells = [...nodeEl.children].filter((c) => c.classList.contains("dock-cell"));
      const fixed = node.sizes.reduce((a, s) => a + (s || 0), 0);
      if (avail > 0 && fixed > avail) {
        const k = avail / fixed;
        node.sizes = node.sizes.map((s) => s ? Math.max(60, Math.round(s * k)) : s);
        cells.forEach((c, i) => {           /* 同步 DOM，深层测量不失真 */
          if (node.sizes[i] != null) applySize(c, node.dir, node.sizes[i]);
        });
      }
      node.children.forEach((c, i) => walk(c, cells[i]));
    };
    walk(tree, document.getElementById("main-layout"));
  }
  window.addEventListener("resize", () => {
    clearTimeout(fitRaf);
    fitRaf = setTimeout(fitSizes, 250);
  });

  /* ── 对外 ── */
  function init() {
    const host = document.getElementById("main-layout");
    if (!host) return;
    applyMode();
    tree = load() || defaultTree();
    render(tree, host);
    window.addEventListener("beforeunload", save);
  }
  function reset() {
    localStorage.removeItem(STORAGE_KEY);
    tree = defaultTree();
    renderAll();
  }
  /* 期③ 预设入口：名字 → 树；存档并重排 */
  const PRESETS = {
    default: defaultTree,
    wide3d() {  /* 宽 3D：Script 收窄、Terminal 压低 */
      return norm({ dir: "row", sizes: [490, null, 300],
        children: ["struct-panel", {
          dir: "col", sizes: [null, 140],
          children: [{ dir: "row", sizes: [null, 300], children: [["scene-panel", "visualize-panel"], "json-panel"] }, "term-dock"] }] });
    },
    bigterm() { /* 大 Terminal：会话排障时用 */
      return norm({ dir: "row", sizes: [490, null, 300],
        children: ["struct-panel", {
          dir: "col", sizes: [null, 480],
          children: [{ dir: "row", sizes: [null, 300], children: [["scene-panel", "visualize-panel"], "json-panel"] }, "term-dock"] }] });
    },
  };
  function preset(name) {
    if (!PRESETS[name]) return false;
    tree = PRESETS[name]();
    save();
    renderAll();
    return true;
  }
  /* ── 用户命名布局（2026-10-15 站长提议：存/取自己调好的界面）──
   * 与自动记忆同一纪律：只存浏览器（脸的摆法），不进服务端。 */
  function getSaved() {
    try { return JSON.parse(localStorage.getItem(SAVED_KEY)) || {}; }
    catch { return {}; }
  }
  function saveLayout(name) {
    if (!name) return false;
    const all = getSaved();
    all[name] = tree;
    try { localStorage.setItem(SAVED_KEY, JSON.stringify(all)); } catch {}
    return true;
  }
  function deleteLayout(name) {
    const all = getSaved();
    if (!all[name]) return false;
    delete all[name];
    localStorage.setItem(SAVED_KEY, JSON.stringify(all));
    return true;
  }
  function loadLayout(name) {
    if (PRESETS[name]) return preset(name);
    const saved = getSaved()[name];
    if (!saved) return false;
    tree = norm(saved);
    clampSizes(tree);
    save();
    renderAll();
    return true;
  }

  return { init, reset, preset, PRESETS, saveLayout, deleteLayout, loadLayout,
           activatePanel, getSaved, get tree() { return tree; } };
})();
