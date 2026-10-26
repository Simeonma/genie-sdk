/* modules/boot.js — 接线与启动（FormPanel/工具条/场景 init、gutter、快捷键、连接框、开屏对齐、自动恢复）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */

/* ── 登录态 fetch 包装（2026-10-21 站长裁决 B 方案；M2/O2 扩成双头）──
 * 服务端 require_signin 中间件要求本地能力接口带 X-API-Key；界面里几十处
 * fetch 不逐个改，这里统一补头（只补同源 /api/*，/api/auth/* 登录族除外——
 * 它们本身不需要 token，登录流程也不该被旧 token 干扰）。
 * O2：同时补 X-Session-Id（哪一份模型）——身份/凭据/会话三件事分开，
 * 调用点无需知道，也就不会漏。 */
(function patchFetchForAuth() {
  const orig = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const sameOrigin = url.startsWith("/") || url.startsWith(location.origin);
      if (sameOrigin && url.indexOf("/api/") >= 0
          && url.indexOf("/api/auth/") < 0) {
        const tok = genieToken();
        const sid = genieSid();
        const opts = Object.assign({}, init || {});
        const h = opts.headers;
        const set = (obj) => {
          if (tok) obj["X-API-Key"] = tok;
          if (sid) obj["X-Session-Id"] = sid;
          return obj;
        };
        if (h && typeof h.Headers === "undefined" && !(h instanceof Headers)) {
          opts.headers = set(Object.assign({}, h));
        } else {
          const hh = new Headers(h || {});
          if (tok) hh.set("X-API-Key", tok);
          if (sid) hh.set("X-Session-Id", sid);
          opts.headers = hh;
        }
        return orig.call(window, input, opts);
      }
    } catch { /* 包装失败退回原 fetch */ }
    return orig.call(window, input, init);
  };
})();

/* ══ 接线（显示链路 + 表单编辑通道；工具条等器官仍不接线） ══════ */
/* 阶段 5b 单模式：表单编辑实时写入活动 SimConfig 并刷新全部视图；
 * 「模型已改」徽标提示与脚本分叉（重跑脚本即整体重建，语义如此）。 */
FormPanel.init("config-content", { onMutate: onFormMutate });

buildToolbar();
initScene();
Dock.init();   /* dock 工作台（2026-10-15）：面板收养 + splitter + 布局持久化 */
/* Layout 预设 + 用户命名布局（2026-10-15 站长提议；2026-10-17 收敛为
 * 单一菜单 chip）：按钮标签 = 当前布局名，菜单内 = 内置预设 / 用户自存
 * 布局 / Save layout… / Delete layout。原 select+Save+Del 三件套的问题：
 * 视觉层级不一、Save 与 File>Save 撞名、≤1280 组内换行、1024 被右缘
 * 裁切、原生 select 的焦点环被全局 input 规则压掉——单一菜单全解。
 * 选项 = 内置预设 + 用户自存布局（genie.dock.saved.v1，只存浏览器）。 */
(() => {
  const btn = document.getElementById("dock-layout-btn");
  const menu = document.getElementById("dock-layout-menu");
  const label = document.getElementById("dock-layout-label");
  if (!btn || !menu || !label) return;   /* 旧 DOM/移动兜底：无声退出 */
  const BUILTIN = { default: "Default", wide3d: "Wide 3D" };
  let current = "default";
  let open = false;

  const nameOf = (v) => v.startsWith("user:") ? v.slice(5) : (BUILTIN[v] || v);
  const setLabel = () => {
    label.textContent = nameOf(current);
    btn.title = "布局：" + nameOf(current) + "（切换 / 保存 / 删除）";
  };
  const setOpen = (v) => {
    open = v;
    menu.classList.toggle("hidden", !v);
    btn.setAttribute("aria-expanded", String(v));
  };
  const close = (focusBtn) => { setOpen(false); if (focusBtn) btn.focus(); };
  const choose = (v) => {
    if (Dock.loadLayout(v.startsWith("user:") ? v.slice(5) : v)) {
      current = v; setLabel();
    }
    close(true);
  };
  const saveAs = () => {
    const name = window.prompt("Save current layout as…", "My layout");
    if (!name || !name.trim()) { close(true); return; }
    Dock.saveLayout(name.trim());
    current = "user:" + name.trim(); setLabel();
    close(true);
  };
  const del = () => {
    if (!current.startsWith("user:")) return;   /* 内置预设不可删 */
    const name = current.slice(5);
    if (!window.confirm('Delete layout "' + name + '"?')) return;
    Dock.deleteLayout(name);
    Dock.loadLayout("default");               /* 删掉当前布局 → 回默认，不留空壳 */
    current = "default"; setLabel();
    close(true);
  };
  const mkItem = (text, { checked = false, disabled = false, onPick }) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tb-menu-item";
    b.setAttribute("role", "menuitem");
    b.textContent = text;
    if (checked) b.setAttribute("aria-checked", "true");
    b.disabled = disabled;
    b.addEventListener("click", (e) => { e.stopPropagation(); onPick(); });
    return b;
  };
  const mkSep = () => {
    const d = document.createElement("div");
    d.className = "tb-menu-sep"; d.setAttribute("role", "separator");
    return d;
  };
  const build = () => {
    menu.textContent = "";
    for (const name of Object.keys(BUILTIN)) {
      menu.appendChild(mkItem(nameOf(name), {
        checked: current === name, onPick: () => choose(name) }));
    }
    const saved = Object.keys(Dock.getSaved());
    if (saved.length) {
      menu.appendChild(mkSep());
      for (const name of saved) {
        menu.appendChild(mkItem(name, {
          checked: current === "user:" + name, onPick: () => choose("user:" + name) }));
      }
    }
    menu.appendChild(mkSep());
    menu.appendChild(mkItem("Save layout…", { onPick: saveAs }));
    menu.appendChild(mkItem("Delete layout", {
      disabled: !current.startsWith("user:"), onPick: del }));
  };

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!open) build();
    setOpen(!open);
  });
  btn.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown") return;
    e.preventDefault();
    if (!open) { build(); setOpen(true); }
    menu.querySelector(".tb-menu-item:not(:disabled)")?.focus();
  });
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(true); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...menu.querySelectorAll(".tb-menu-item:not(:disabled)")];
    const i = items.indexOf(document.activeElement);
    const step = e.key === "ArrowDown" ? 1 : -1;
    items[(i + step + items.length) % items.length]?.focus();
  });
  document.addEventListener("click", (e) => {
    if (open && !e.target.closest("#tb-layout-box")) close(false);
  });
  document.addEventListener("keydown", (e) => {
    if (open && e.key === "Escape") close(true);
  });
  build();
  setLabel();
})();
/* ══ 功能区页签（2026-10-17 站长裁决：Lumerical 式 FILE/VIEW/DESIGN/FDTD）══
 * 默认 Design（站长裁决）；选择存 localStorage（只存"脸的摆法"）。
 * 键盘：←/→ 巡航、Home/End 首末（WAI-ARIA tablist 惯例）。 */
(() => {
  const TABS = ["file", "view", "design", "fdtd"];
  const KEY = "genie.tab.v1";
  const tabs = [...document.querySelectorAll(".app-tab")];
  const pages = new Map(TABS.map((k) => [k, document.getElementById("page-" + k)]));
  let current = null;
  const activate = (name, focus) => {
    if (!pages.get(name)) name = "design";
    current = name;
    for (const t of tabs) {
      const on = t.dataset.page === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    }
    for (const [k, p] of pages) if (p) p.hidden = k !== name;
    try { localStorage.setItem(KEY, name); } catch {}
    /* VIEW 页的 3D 开关镜像以真身 aria-pressed 为准，切页时回填 */
    if (name === "view" && typeof window.syncViewMirror === "function") window.syncViewMirror();
    if (focus) document.getElementById("tab-" + name)?.focus();
  };
  for (const t of tabs) t.addEventListener("click", () => activate(t.dataset.page));
  document.getElementById("tab-bar")?.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(current);
    if (e.key === "ArrowRight") { e.preventDefault(); activate(TABS[(i + 1) % TABS.length], true); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); activate(TABS[(i - 1 + TABS.length) % TABS.length], true); }
    else if (e.key === "Home") { e.preventDefault(); activate(TABS[0], true); }
    else if (e.key === "End") { e.preventDefault(); activate(TABS[TABS.length - 1], true); }
  });
  window.genieSetTab = (name) => activate(name);
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch {}
  activate(TABS.includes(saved) ? saved : "design");
})();

/* Agent（2026-10-17 迁顶栏右端）：宿主模式才有真面板，本地 Studio 先提示。 */
$("agent-open-btn")?.addEventListener("click", () => {
  toast("Agent 面板：阶段 6 宿主模式提供（本地 Studio 暂无）", "info");
});

/* Domain / Mesh 直达（FDTD 页）：选中树内对应单例并激活 Objects 工作面。 */
for (const [id, kind] of [["domain-edit-btn", "domain"], ["mesh-edit-btn", "mesh"]]) {
  $(id)?.addEventListener("click", () => {
    Dock.activatePanel("struct-panel");
    selectObj(kind);
  });
}

requestAnimationFrame(animate);
window.addEventListener("resize", () => scene && scene.onResize());

/* 全站 [data-icon] 注入（v1 app.js:5553 同款；v2 重建时遗失——场景
 * 工具条 .tool-btn 与停止钮的图标全靠它）。已有 svg 跳过防二次注入；
 * 纯图标钮的 a11y 语义由 aria-label 承担（svg 自身 aria-hidden）。 */
document.querySelectorAll("[data-icon]").forEach((b) => {
  if (b.querySelector("svg")) return;
  const ic = iconEl(b.dataset.icon);
  if (ic) b.insertBefore(ic, b.firstChild);
});

/* [data-preview] 注入（2026-10-17 图标族统一）：与 [data-icon] 并行的第二条
 * 导轨——工具条对象类/File/View 用渲染风预览，树/检查器继续用线稿。 */
document.querySelectorAll("[data-preview]").forEach((b) => {
  if (b.querySelector("svg")) return;
  const pv = previewEl(b.dataset.preview);
  if (pv) b.insertBefore(pv, b.firstChild);
});

/* 文件 I/O 直上工具栏（2026-10 站长裁决：File▾ 下拉取消）——
 * 打开/存储按钮直接触发原菜单项 click（import-cfg / export-cfg
 * 的处理器语义不变，仅入口从菜单项换成栏上按钮）。 */
{
  const open = $("file-open-btn"), save = $("file-save-btn");
  open?.addEventListener("click", () => $("import-cfg").click());
  save?.addEventListener("click", () => $("export-cfg").click());
}

/* 行号 gutter（站长反馈找回）：white-space:pre 保证逻辑行=视觉行，
 * scrollTop 同步零漂移。行数变化才重建数字，滚动只做 transform。
 * 2026-10-21：同一钩子兼管「语法高亮镜像层」——<pre id="code-hl"> 与
 * textarea 同字号同 padding 叠在底层，滚动/输入/程序化赋值三条路径统一刷新。 */
{
  const ta = $("code-editor");
  const gutter = document.createElement("div");
  gutter.id = "code-gutter";
  gutter.setAttribute("aria-hidden", "true");
  const inner = document.createElement("div");
  inner.className = "gutter-inner";
  gutter.appendChild(inner);
  ta.parentNode.insertBefore(gutter, ta);

  const hl = document.createElement("pre");
  hl.id = "code-hl";
  hl.className = "code-hl";
  hl.setAttribute("aria-hidden", "true");
  ta.parentNode.insertBefore(hl, ta);

  let lastLines = -1;
  const gutterSync = () => {
    const n = ta.value.split("\n").length;
    if (n !== lastLines) {
      const frag = document.createDocumentFragment();
      for (let i = 1; i <= n; i++) {
        const d = document.createElement("div");
        d.textContent = i;
        frag.appendChild(d);
      }
      inner.replaceChildren(frag);
      lastLines = n;
    }
    inner.style.transform = "translateY(" + (-ta.scrollTop) + "px)";
    try { renderPythonHighlight(hl, ta.value); } catch { /* 着色失败不影响编辑 */ }
    hl.scrollTop = ta.scrollTop;
    hl.scrollLeft = ta.scrollLeft;
  };
  ta.addEventListener("scroll", gutterSync, { passive: true });
  ta.addEventListener("input", gutterSync);
  /* 程序化赋值（示例载入/放弃编辑恢复）不触发 input——拦 value setter */
  const taVal = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value");
  Object.defineProperty(ta, "value", {
    get() { return taVal.get.call(this); },
    set(v) { taVal.set.call(this, v); gutterSync(); },
  });
  gutterSync();
}

/* 脚本面板：唯一活编辑面（terminal 战役：运行 = 整体 exec，运行即生效）。
 * 快捷键（2026-10-14 精简）：Ctrl/Cmd+Enter = 运行；Ctrl+S 仅拦截浏览器
 * 默认保存（脚本由 worker 在每次执行时自动存档——无 GUI 保存概念）。 */
$("code-editor").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === "Enter") { e.preventDefault(); runScript(); }
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); }
});
/* 无害 QoL：下载（复制钮已退役——2026-10-14 精简） */
$("download-py").addEventListener("click", () => {
  const blob = new Blob([$("code-editor").value], { type: "text/x-python" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "genie_sim.py";
  a.click();
  URL.revokeObjectURL(a.href);
});
/* ── 仿真文件 .gensim（2026-10 站长裁决转正，承 G14）───────────
 * 「存储/打开仿真」取代「导出/导入状态」：一个 .gensim（zip 容器）
 * = simconfig.json + 工作区快照（脚本+结果），用户感知 = 一个文件
 * 就是一个仿真。服务端 /api/sim/export 打包、/api/sim/import 先验
 * cfg（genie.models 同一双手）再解包；GUI 拿到 cfg 后仍走
 * onFormMutate()（同一防抖通道推进会话 f）+ 工作区面板刷新。
 * importCfgText 保留 = 纯 JSON 校验+推进（e2e 函数面直驱用）。 */
/* 存储文件名记忆（2026-10 站长定案补全）：localStorage 记上次名字，
 * 下次「存储仿真」默认存回去。路径选择 = File System Access API 的
 * showSaveFilePicker（Chrome 系原生「另存为」对话框，用户自选目录，
 * API 自己记忆上次位置）；无此 API 的浏览器回退 prompt 问名 +
 * a.download（落浏览器下载目录）。askName=false = 函数面直驱
 * （e2e headless 无对话框），走老下载通道。 */
const _SIM_NAME_KEY = "genie-sim-filename";
const _SIM_NAME_DEFAULT = "genie_sim.gensim";

async function exportCfg(askName = true) {
  const c = (typeof window.getCfg === "function") ? window.getCfg() : null;
  if (!c || !Object.keys(c).length) { toast("还没有可存储的仿真——先运行脚本或在表单里搭好配置", "warn"); return null; }
  let fname = localStorage.getItem(_SIM_NAME_KEY) || _SIM_NAME_DEFAULT;
  if (!fname.endsWith(".gensim")) fname += ".gensim";

  /* 先弹「另存为」（必须在 click 用户激活窗口内），拿到落点再打包。
   * 取消（AbortError）= 静默返回；不支持 = 老通道（prompt + a.download）。 */
  let handle = null;
  if (askName && window.showSaveFilePicker) {
    try {
      handle = await window.showSaveFilePicker({
        suggestedName: fname,
        types: [{ description: "GENIE 仿真文件（配置 + 工作区快照）",
                  accept: { "application/zip": [".gensim"] } }],
      });
      fname = handle.name;
    } catch (e) {
      if (e && e.name === "AbortError") return null;
      handle = null;                        /* 选择器故障 → 回退老通道 */
    }
  }
  if (askName && !handle) {
    const asked = window.prompt("存储仿真为…", fname);
    if (asked === null) return null;            /* 用户取消 */
    fname = asked.trim() || _SIM_NAME_DEFAULT;
    if (!fname.endsWith(".gensim")) fname += ".gensim";
    if (!/^[\w\-]{1,64}\.gensim$/.test(fname)) {
      toast("文件名不合规：限字母/数字/_/-（≤64 字符）+ .gensim", "err");
      return null;
    }
  }
  try {
    const r = await fetch("/api/sim/export", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cfg: c, job_id: window.__lastJobId || "" }) });
    if (!r.ok) {
      let detail = String(r.status);
      try { detail = (await r.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    if (handle) {
      /* 流式直写用户选定的落点（大快照不过内存） */
      await r.body.pipeTo(await handle.createWritable());
    } else {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await r.blob());
      a.download = fname;
      a.click();
      URL.revokeObjectURL(a.href);
    }
    localStorage.setItem(_SIM_NAME_KEY, fname);
    toast("仿真已存储为 " + fname + "（配置 + 工作区快照）", "ok");
    return c;
  } catch (e) {
    toast("存储仿真失败：" + e.message, "err");
    return null;
  }
}

async function importCfgText(text) {
  let parsed;
  try { parsed = JSON.parse(text); }
  catch (e) { toast("导入失败：不是合法 JSON（" + e.message + "）", "err"); return false; }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    toast("导入失败：内容须是 SimConfig JSON 对象", "err"); return false; }
  let out;
  try {
    const r = await fetch("/api/validate", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cfg: parsed }) });
    out = await r.json();
  } catch (e) { toast("导入失败：校验服务不可达", "err"); return false; }
  if (!out.ok) {
    const first = (out.errors || []).slice(0, 3)
      .map((e) => (e.loc ? e.loc + ": " : "") + e.msg).join("；");
    toast("导入被拒（配置未通过校验）：" + (first || "未知错误"), "err");
    return false;
  }
  /* M3b：导入不是"本地改一堆字段然后推整份文档"，是**整份模型替换**。
   * replaceModel 会同步对齐 _serverCfg，所以不会再补发一坨 patch。 */
  const ok = await replaceModel(parsed, "json");
  toast(ok ? "配置已导入（已通过校验并替换当前模型）" : "配置导入失败",
        ok ? "ok" : "err");
  return ok;
}

function importCfg() {
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = ".gensim,.zip,application/zip";
  inp.addEventListener("change", async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await fetch("/api/sim/import", { method: "POST", body: fd });
      const out = await r.json();
      if (!r.ok) throw new Error(out.detail || String(r.status));
      await replaceModel(out.cfg, "gensim");   /* 整份替换（不再逐字段推） */
      if (window.loadWorkspaceFiles) loadWorkspaceFiles();
      /* G148（2026-10-27 站长定）：含结果的仿真存档 → 还原**结果态**
       * （按钮 = Switch to setting；Plot 面板数据源已随包回落会话）。
       * 无结果的存档维持编辑态（历史行为）。 */
      if (out.has_results && out.job_id) {
        window.__lastJobId = out.job_id;
        window.__resultStatus = null;
        if (typeof enterResult === "function") enterResult(true);
        if (typeof refreshResultChip === "function") refreshResultChip();
      }
      toast("仿真已打开（模型已替换 + " +
            out.restored.length + " 个文件回工作区" +
            (out.has_results ? "；含结果——已进结果态" : "") + "）", "ok");
    } catch (e) {
      toast("打开仿真失败：" + e.message, "err");
    }
  });
  inp.click();
}

/* e2e 钩子（headless 无下载/文件选择对话框——直接调函数面） */
window.__g14 = { exportCfg, importCfgText };

/* 直钮绑定（File▾ 下拉已删）：处理器零事件参数依赖，直接挂 */
$("file-save-btn").addEventListener("click", exportCfg);
$("file-open-btn").addEventListener("click", importCfg);
/* load-py 本机文件上岗（G17 走查补漏 2026-10-16）：f4d1e23 只上了钮
 * 没接线——出生即死钮。语义同工作区点行载入：File API 读用户笔记本
 * （零上传），ed.value + input 事件（未存态，Run 前必存进工作区）。 */
function loadPyText(text, name) {
  const ed = $("code-editor");
  ed.value = text;
  ed.dispatchEvent(new Event("input", { bubbles: true }));
  toast("Loaded " + (name || "local file") + "（未存态——Run 前必存）", "info");
}
function loadPyLocal() {
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = ".py,text/x-python";
  inp.addEventListener("change", async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    loadPyText(await f.text(), f.name);
  });
  inp.click();
}
window.__loadPyText = loadPyText;      /* e2e 钩子 */
$("load-py").addEventListener("click", loadPyLocal);
/* 账号对话框（M1）：Sign in（未登录）/ Account（已登录）/ 子视图
 * （Create account、Reset password、Change password）。
 * 视觉：统一 gc-* 类（一次注入 style，替代散落 inline style）；
 * 主状态不设标题（内容自明），子视图保留小标题区分上下文。
 * 交互全走 wrap 事件委托（closest 分发）。 */
const GC_STYLE_ID = "v2-conn-style";
function ensureConnStyle() {
  if (document.getElementById(GC_STYLE_ID)) return;
  const st = document.createElement("style");
  st.id = GC_STYLE_ID;
  /* 全部使用界面设计 token（--radius/--accent/--border-*…）——
   * 形状/配色与主界面同源，全局换肤对话盒自动跟随。
   * 2026-10-21：加上「铭牌式」头（eyebrow + 标题 + 发丝线），输入/按钮
   * 全部落到 30px 刻度与 3px 圆角；链接改静默墨色（不再是三条并列亮蓝，
   * 旧观感像 GitHub 登录页而非仪器控件）。 */
  st.textContent = `
#v2-conn{position:fixed;inset:0;z-index:10000;background:rgba(16,21,26,.34);
  display:flex;align-items:center;justify-content:center;
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
#v2-conn .gc-box{background:var(--bg-elevated,#fff);border:1px solid var(--border,#DCE0E6);
  border-radius:var(--radius-lg,6px);padding:0 22px 18px;width:368px;position:relative;
  box-sizing:border-box;box-shadow:var(--shadow-3);
  font-size:var(--fs-sm,14px);color:var(--text-primary,#10151A)}
/* 关闭钮（2026-10-21）：右上角 24px 静默图标，hover 才浮底——与主界面
 * 面板动作钮同一语言；也补上「看得见的退出路径」（Esc 之外）。 */
#v2-conn .gc-x{position:absolute;top:8px;right:8px;width:24px;height:24px;
  display:inline-flex;align-items:center;justify-content:center;
  background:none;border:1px solid transparent;border-radius:var(--radius-sm,3px);
  color:var(--text-muted,#5E6A76);cursor:pointer;padding:0;
  transition:background .12s,color .12s,border-color .12s}
#v2-conn .gc-x:hover{background:var(--bg-hover,#EDF0F4);color:var(--text-primary,#10151A);
  border-color:var(--border,#DCE0E6)}
#v2-conn .gc-head{padding:18px 0 12px;margin-bottom:14px;
  border-bottom:1px solid var(--border-soft,#EDEFF2)}
#v2-conn .gc-eyebrow{font-size:var(--fs-2xs,11px);font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--text-muted,#5E6A76);line-height:1}
#v2-conn .gc-title{font-size:var(--fs-xl,17px);font-weight:600;margin:6px 0 0;
  letter-spacing:.01em;color:var(--text-primary,#10151A)}
#v2-conn #v2-view-register .gc-title,#v2-conn #v2-view-recover .gc-title,
#v2-conn #v2-view-passwd .gc-title{margin:18px 0 14px;padding-bottom:12px;
  border-bottom:1px solid var(--border-soft,#EDEFF2)}
#v2-conn .gc-field{margin-top:13px}
#v2-conn .gc-field:first-of-type{margin-top:0}
#v2-conn .gc-label{display:flex;justify-content:space-between;align-items:baseline;
  font-size:var(--fs-xs,12px);font-weight:500;color:var(--text-secondary,#39434E);
  margin-bottom:5px;letter-spacing:.01em}
#v2-conn .gc-input{width:100%;box-sizing:border-box;height:30px;padding:0 9px;
  font-size:var(--fs-xs,12px);color:var(--text-primary,#10151A);background:var(--bg-input,#fff);
  border:1px solid var(--border-raise,#C3CAD3);border-radius:var(--radius-sm,3px);
  transition:border-color .15s,box-shadow .15s;font-family:inherit}
#v2-conn .gc-input:hover{border-color:var(--text-muted,#5E6A76)}
#v2-conn .gc-input:focus{outline:none;border-color:var(--border-active,#1F62B0);
  box-shadow:0 0 0 3px var(--accent-glow,rgba(31,98,176,.16))}
#v2-conn .gc-input[readonly]{background:var(--bg-tertiary,#F6F7F9);
  color:var(--text-muted,#5E6A76)}
#v2-conn .gc-hint{min-height:16px;margin-top:5px;font-size:var(--fs-xs,12px);
  color:var(--text-muted,#5E6A76)}
#v2-conn .gc-hint:empty{min-height:0;margin:0}
#v2-conn .gc-msg{display:none;margin-top:12px;padding:7px 10px;
  border-radius:var(--radius-sm,3px);font-size:var(--fs-xs,12px);line-height:1.45;
  background:var(--danger-soft,rgba(192,57,43,.10));color:var(--danger,#C0392B);
  border:1px solid var(--danger-line,rgba(192,57,43,.40))}
#v2-conn .gc-msg.gc-ok{background:var(--success-soft,rgba(31,122,77,.10));
  color:var(--success,#1F7A4D);border-color:var(--success-line,rgba(31,122,77,.35))}
#v2-conn .gc-links{display:flex;justify-content:flex-end;gap:16px;margin-top:14px}
#v2-conn .gc-link{color:var(--text-muted,#5E6A76);font-size:var(--fs-xs,12px);
  text-decoration:none;cursor:pointer;background:none;border:0;padding:0;font-family:inherit;
  transition:color .12s}
#v2-conn .gc-link:hover{color:var(--accent,#1F62B0);text-decoration:underline;
  text-underline-offset:2px}
#v2-conn .gc-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px;
  margin-top:18px}
/* 退出确认（2026-10-21）：一句话说清"重新登录=空模型"，不吓人但要看见。 */
#v2-conn .gc-warn{margin-top:14px;padding:10px 12px;border-radius:var(--radius-sm,3px);
  background:#FDF6EC;border:1px solid #E8C9A0}
#v2-conn .gc-warn-t{font-size:var(--fs-xs,12px);font-weight:600;color:#8A5A1E}
#v2-conn .gc-warn-b{margin-top:3px;font-size:var(--fs-xs,12px);line-height:1.5;color:#7A6242}
#v2-conn .gc-btn{height:30px;padding:0 14px;border-radius:var(--radius-sm,3px);
  white-space:nowrap;
  font-size:var(--fs-xs,12px);font-weight:600;font-family:inherit;cursor:pointer;
  letter-spacing:.01em;border:1px solid transparent;
  transition:background .15s,border-color .15s,color .15s}
#v2-conn .gc-btn.secondary{background:var(--bg-secondary,#fff);
  border-color:var(--border-raise,#C3CAD3);color:var(--text-secondary,#39434E)}
#v2-conn .gc-btn.secondary:hover{background:var(--bg-tertiary,#F6F7F9);color:var(--text-primary,#10151A)}
#v2-conn .gc-btn.primary{background:var(--accent,#1F62B0);color:#fff;
  border-color:var(--accent,#1F62B0)}
#v2-conn .gc-btn.primary:hover{background:var(--accent-2,#1A518F);border-color:var(--accent-2,#1A518F)}
#v2-conn .gc-btn:disabled{opacity:.5;cursor:default;filter:none}
#v2-conn .gc-row{display:flex;gap:8px;align-items:stretch}
#v2-conn .gc-row .gc-input{flex:1}
#v2-conn .gc-row .gc-btn{height:30px;align-self:center}
#v2-conn .gc-muted{color:var(--text-muted,#5E6A76);font-size:var(--fs-2xs,11px)}
#v2-conn .gc-note{color:var(--text-muted,#5E6A76);font-size:var(--fs-xs,12px);
  line-height:1.55;margin:0 0 4px}
`;
  document.head.appendChild(st);
}

/* ── 退出前确认用：会话里到底有没有"值得挽留"的东西 ──
 * 2026-10-21 站长裁决：重新登录 = 新会话 = 空模型，所以退出会丢模型。
 * 域范围、材料、结构、源、监视器任一非空就算"有模型"；全空直接放行，
 * 不拿一个空文档吓唬人。 */
function hasUnsavedModel() {
  const c = (typeof window.getCfg === "function") ? window.getCfg()
    : (typeof cfg !== "undefined" ? cfg : null);
  if (!c) return false;
  const dom = c.domain || {};
  const hasRange = ["x_range", "y_range", "z_range"].some(
    (k) => Array.isArray(dom[k]) && dom[k].length === 2);
  const n = (c.materials || []).length + (c.geometry || []).length
    + (c.source || []).length + (c.monitor || []).length;
  return hasRange || n > 0;
}

function connDialog(initialView) {
  ensureConnStyle();
  const old = document.getElementById("v2-conn");
  if (old) old.remove();
  let _signoutArmed = false;      /* 退出确认：第一次点只提示，第二次才退 */

  /* 先存再退：导出成功后留在确认态，让用户自己点 "Sign out anyway"。 */
  async function doExportBeforeSignout() {
    const w = $("v2-acct-warn");
    const b = w && w.querySelector(".gc-warn-b");
    try {
      const saved = await exportCfg(true);
      if (saved && b) {
        b.textContent = "Saved. You can sign out now.";
      }
    } catch (e) {
      if (b) b.textContent = "Export failed: " + (e && e.message ? e.message : e);
    }
  }
  const wrap = document.createElement("div");
  wrap.id = "v2-conn";
  const user = localStorage.getItem("genie_v2_username") || "";
  const hasToken = !!localStorage.getItem("genie_v2_api_key");
  wrap.innerHTML = `
    <div class="gc-box">
      <button class="gc-x" id="v2-conn-x" type="button" aria-label="Close" title="Close (Esc)">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
          stroke-width="1.6" stroke-linecap="round"><path d="M3.6 3.6 L12.4 12.4 M12.4 3.6 L3.6 12.4"/></svg>
      </button>
      <div id="v2-view-login">
        <div class="gc-head">
          <div class="gc-eyebrow">GENIE ACCOUNT</div>
          <div class="gc-title">Sign in</div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-conn-user">Username or email</label>
          <input id="v2-conn-user" class="gc-input" value="${user.replace(/"/g, "&quot;")}"
            autocomplete="username">
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-conn-pass">Password
            <a id="v2-recover-switch" class="gc-link">Forgot password?</a></label>
          <input id="v2-conn-pass" class="gc-input" type="password"
            autocomplete="current-password">
        </div>
        <div id="v2-conn-msg"></div>
        <div class="gc-links" style="justify-content:space-between">
          <a id="v2-cp-switch" class="gc-link">Change password</a>
          <a id="v2-reg-switch" class="gc-link">Create account →</a>
        </div>
        <div class="gc-actions">
          <button id="v2-conn-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-conn-save" class="gc-btn primary">Sign in</button>
        </div>
      </div>
      <div id="v2-view-account" style="display:none">
        <div class="gc-head">
          <div class="gc-eyebrow">GENIE ACCOUNT</div>
          <div class="gc-title">Account</div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-acct-user">Signed in as</label>
          <input id="v2-acct-user" class="gc-input" readonly>
        </div>
        <div class="gc-links" style="justify-content:flex-start">
          <a id="v2-acct-cp" class="gc-link">Change password</a>
        </div>
        <!-- 退出确认（2026-10-21 站长裁决）：重新登录 = 新会话 = 空模型，
             所以退出会丢掉当前模型。英文提示（产品面国际化）。 -->
        <div id="v2-acct-warn" class="gc-warn" style="display:none">
          <div class="gc-warn-t">Your model will not be kept</div>
          <div class="gc-warn-b">Signing in again starts a new, empty model.
            Export this one first if you need it.</div>
        </div>
        <div class="gc-actions">
          <button id="v2-acct-export" class="gc-btn secondary" style="display:none">
            Export .gensim</button>
          <button id="v2-acct-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-acct-signout" class="gc-btn primary">Sign out</button>
        </div>
      </div>
      <div id="v2-view-register" style="display:none">
        <div class="gc-title">Create account</div>
        <div class="gc-field">
          <label class="gc-label" for="v2-reg-email">Email</label>
          <input id="v2-reg-email" class="gc-input" type="email" autocomplete="email"
            placeholder="you@example.com">
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-reg-code">Verification code
            <span class="gc-muted">10 min</span></label>
          <div class="gc-row">
            <input id="v2-reg-code" class="gc-input" maxlength="6"
              autocomplete="one-time-code" placeholder="6 digits">
            <button id="v2-reg-send" class="gc-btn secondary">Send code</button>
          </div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-reg-name">Username</label>
          <input id="v2-reg-name" class="gc-input" autocomplete="username">
          <div id="v2-name-hint" class="gc-hint"></div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-reg-pass">Password
            <span class="gc-muted">min 8 characters</span></label>
          <input id="v2-reg-pass" class="gc-input" type="password"
            autocomplete="new-password">
        </div>
        <div id="v2-reg-msg"></div>
        <div class="gc-links"><a id="v2-login-switch" class="gc-link">← Back to sign in</a></div>
        <div class="gc-actions">
          <button id="v2-reg-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-reg-save" class="gc-btn primary">Register &amp; sign in</button>
        </div>
      </div>
      <div id="v2-view-recover" style="display:none">
        <div class="gc-title">Reset password</div>
        <div class="gc-note">Enter your account email — we'll send a code.
          Resetting revokes all existing sessions.</div>
        <div class="gc-field">
          <label class="gc-label" for="v2-rec-email">Email</label>
          <input id="v2-rec-email" class="gc-input" type="email" autocomplete="email"
            placeholder="you@example.com">
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-rec-code">Verification code
            <span class="gc-muted">10 min</span></label>
          <div class="gc-row">
            <input id="v2-rec-code" class="gc-input" maxlength="6"
              autocomplete="one-time-code" placeholder="6 digits">
            <button id="v2-rec-send" class="gc-btn secondary">Send code</button>
          </div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-rec-new">New password
            <span class="gc-muted">min 8 characters</span></label>
          <input id="v2-rec-new" class="gc-input" type="password"
            autocomplete="new-password">
        </div>
        <div id="v2-rec-msg"></div>
        <div class="gc-links"><a id="v2-rec-back" class="gc-link">← Back</a></div>
        <div class="gc-actions">
          <button id="v2-rec-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-rec-save" class="gc-btn primary">Reset password</button>
        </div>
      </div>
      <div id="v2-view-passwd" style="display:none">
        <div class="gc-title">Change password</div>
        <div class="gc-note">Changing your password revokes all existing sessions —
          you will stay signed in on this device with a fresh token.</div>
        <div class="gc-field">
          <label class="gc-label" for="v2-pwd-old">Current password</label>
          <input id="v2-pwd-old" class="gc-input" type="password"
            autocomplete="current-password">
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-pwd-new">New password
            <span class="gc-muted">min 8 characters</span></label>
          <input id="v2-pwd-new" class="gc-input" type="password"
            autocomplete="new-password">
        </div>
        <div id="v2-pwd-msg"></div>
        <div class="gc-links"><a id="v2-pwd-back" class="gc-link">← Back</a></div>
        <div class="gc-actions">
          <button id="v2-pwd-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-pwd-save" class="gc-btn primary">Change password</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const $ = (id) => wrap.querySelector("#" + id);
  let onKey = null;
  const close = () => {
    if (onKey) document.removeEventListener("keydown", onKey);
    if (nameTimer) clearTimeout(nameTimer);
    if (recTimer) clearTimeout(recTimer);
    onKey = null;
    wrap.remove();
  };
  onKey = (e) => { if (e.key === "Escape" && !window.__authGate) close(); };
  document.addEventListener("keydown", onKey);
  /* 闸门态：登录框不可关（没有可用的工作区可回） */
  if (window.__authGate) {
    const x = $("v2-conn-x");
    if (x) x.style.display = "none";
    const c = $("v2-conn-cancel");   /* 没有必要给"取消"——没登录没有可去的地方 */
    if (c) c.style.display = "none";
  }
  const msgOf = (id) => (t, good) => {
    const m = $(id); if (!m) return;
    m.className = "gc-msg" + (good ? " gc-ok" : "");
    m.textContent = t; m.style.display = "block";
  };
  const msg = msgOf("v2-conn-msg");
  const regMsg = msgOf("v2-reg-msg");
  const pwdMsg = msgOf("v2-pwd-msg");
  const recMsg = msgOf("v2-rec-msg");
  const showView = (v) => {
    ["login", "register", "passwd", "recover", "account"].forEach((k) => {
      const el = $("v2-view-" + k);
      if (el) el.style.display = k === v ? "block" : "none";
    });
  };
  fetch("/api/health").then(r => r.json()).then(h => {
    if (h && h.upstream) console.info("[genie] submit upstream:", h.upstream);
  }).catch(() => {});

  /* ── Sign in ── */
  async function doLogin(u, p) {
    if (!u || !p) { msg("Enter your username and password"); return false; }
    try {
      const r = await fetch("/api/auth/login", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.token) {
        localStorage.setItem("genie_v2_api_key", j.token);
        localStorage.setItem("genie_v2_username", u);
        /* O2：确定"哪一份模型"。同账号再登录（改密码/过期后重登）沿用它
         * → 模型保住；换账号或从未登录过 → 新 id → 新模型。 */
        genieClaimSession(u);
        close(); location.reload();
        return true;
      }
      msg(j.detail || `Sign-in failed (${r.status})`);
    } catch (e) { msg(`Login request failed: ${e}`); }
    return false;
  }
  function doConnect() {
    const u = ($("v2-conn-user") || {}).value ? $("v2-conn-user").value.trim() : "";
    const p = ($("v2-conn-pass") || {}).value || "";
    doLogin(u, p);
  }

  /* ── Register ── */
  let sendLeft = 0, sendTimer = null, nameTimer = null, nameOk = false;
  const tick = () => {
    const btn = $("v2-reg-send");
    if (!btn) return;
    if (sendLeft > 0) {
      btn.disabled = true;
      btn.textContent = `Resend (${sendLeft}s)`;
      sendLeft -= 1;
      sendTimer = setTimeout(tick, 1000);
    } else {
      btn.disabled = false;
      btn.textContent = "Send code";
    }
  };
  async function sendCode() {
    const email = (($("v2-reg-email") || {}).value || "").trim();
    if (!email || email.indexOf("@") < 1 || email.length > 254) {
      regMsg("Enter a valid email address"); return;
    }
    try {
      const r = await fetch("/api/auth/register", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        regMsg("Verification code sent (valid for 10 minutes) — check your inbox", true);
        sendLeft = 60; tick();
        return;
      }
      regMsg(j.detail || `Send failed (${r.status})`);
    } catch (e) { regMsg(`Send failed: ${e}`); }
  }
  function checkName() {
    const name = (($("v2-reg-name") || {}).value || "").trim();
    const hint = $("v2-name-hint");
    if (!hint) return;
    nameOk = false;
    if (!name) { hint.textContent = ""; return; }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
      hint.style.color = "#cf222e";
      hint.textContent = "Letters, digits, _ and - only (1-64 characters)";
      return;
    }
    hint.style.color = "#57606a"; hint.textContent = "Checking…";
    fetch("/api/auth/name-available?name=" + encodeURIComponent(name))
      .then(r => r.json())
      .then(j => {
        if (j && j.ok) { hint.style.color = "#1a7f37"; hint.textContent = "✓ Available"; nameOk = true; }
        else { hint.style.color = "#cf222e"; hint.textContent = (j && j.reason) || "Unavailable"; nameOk = false; }
      }).catch(() => { hint.textContent = ""; });
  }
  async function doRegister() {
    const email = (($("v2-reg-email") || {}).value || "").trim();
    const code = (($("v2-reg-code") || {}).value || "").trim();
    const name = (($("v2-reg-name") || {}).value || "").trim();
    const pass = (($("v2-reg-pass") || {}).value || "");
    if (!email || email.indexOf("@") < 1) { regMsg("Enter a valid email address"); return; }
    if (!/^\d{6}$/.test(code)) { regMsg("Enter the 6-digit verification code"); return; }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) { regMsg("Username: letters, digits, _ and - only (1-64 characters)"); return; }
    if (pass.length < 8) { regMsg("Password must be at least 8 characters"); return; }
    try {
      const r = await fetch("/api/auth/verify", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, name, password: pass }) });
      const j = await r.json().catch(() => ({}));
      if (!(r.ok && j.ok)) { regMsg(j.detail || `Registration failed (${r.status})`); return; }
      showView("login");
      const ok = await doLogin(name, pass);
      if (!ok) {
        $("v2-conn-user").value = name;
        msg("Account created — please sign in");
      }
    } catch (e) { regMsg(`Registration request failed: ${e}`); }
  }

  /* ── Change password ── */
  async function doPasswd() {
    const oldP = (($("v2-pwd-old") || {}).value || "");
    const newP = (($("v2-pwd-new") || {}).value || "");
    const tok = localStorage.getItem("genie_v2_api_key") || "";
    if (!tok) { pwdMsg("Not signed in — sign in first"); return; }
    if (!oldP) { pwdMsg("Enter your current password"); return; }
    if (newP.length < 8) { pwdMsg("New password must be at least 8 characters"); return; }
    if (oldP === newP) { pwdMsg("New password must differ from the current one"); return; }
    try {
      const r = await fetch("/api/auth/passwd", { method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": tok },
        body: JSON.stringify({ old: oldP, new: newP }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.token) {
        localStorage.setItem("genie_v2_api_key", j.token);
        close(); location.reload();
        return;
      }
      pwdMsg(j.detail || `Password change failed (${r.status})`);
    } catch (e) { pwdMsg(`Password change request failed: ${e}`); }
  }

  /* ── Reset password ── */
  let recLeft = 0, recTimer = null;
  const recTick = () => {
    const btn = $("v2-rec-send");
    if (!btn) return;
    if (recLeft > 0) {
      btn.disabled = true;
      btn.textContent = `Resend (${recLeft}s)`;
      recLeft -= 1;
      recTimer = setTimeout(recTick, 1000);
    } else {
      btn.disabled = false;
      btn.textContent = "Send code";
    }
  };
  async function sendRecCode() {
    const email = (($("v2-rec-email") || {}).value || "").trim();
    if (!email || email.indexOf("@") < 1 || email.length > 254) {
      recMsg("Enter a valid email address"); return;
    }
    try {
      const r = await fetch("/api/auth/recover", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        recMsg("If the email exists, a code is on its way (valid for 10 minutes)", true);
        recLeft = 60; recTick();
        return;
      }
      recMsg(j.detail || `Send failed (${r.status})`);
    } catch (e) { recMsg(`Send failed: ${e}`); }
  }
  async function doRecover() {
    const email = (($("v2-rec-email") || {}).value || "").trim();
    const code = (($("v2-rec-code") || {}).value || "").trim();
    const newP = (($("v2-rec-new") || {}).value || "");
    if (!email || email.indexOf("@") < 1) { recMsg("Enter a valid email address"); return; }
    if (!/^\d{6}$/.test(code)) { recMsg("Enter the 6-digit verification code"); return; }
    if (newP.length < 8) { recMsg("New password must be at least 8 characters"); return; }
    try {
      const r = await fetch("/api/auth/recover-verify", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, new: newP }) });
      const j = await r.json().catch(() => ({}));
      if (!(r.ok && j.ok)) { recMsg(j.detail || `Reset failed (${r.status})`); return; }
      showView("login");
      if (j.name && $("v2-conn-user")) $("v2-conn-user").value = j.name;
      msg("Password reset — sign in with your new password", true);
    } catch (e) { recMsg(`Reset request failed: ${e}`); }
  }

  /* ── 事件委托 ── */
  wrap.addEventListener("click", (e) => {
    /* 闸门态：点遮罩/取消都不关（必须登录才能进） */
    if (window.__authGate
        && (e.target === wrap
            || (e.target.closest && (e.target.closest("#v2-conn-cancel")
                || e.target.closest("#v2-reg-cancel")
                || e.target.closest("#v2-pwd-cancel")
                || e.target.closest("#v2-rec-cancel")
                || e.target.closest("#v2-conn-x"))))) return;
    if (e.target === wrap) { close(); return; }
    const hit = (id) => e.target.closest && e.target.closest("#" + id);
    if (hit("v2-conn-cancel") || hit("v2-reg-cancel") || hit("v2-pwd-cancel")
        || hit("v2-rec-cancel") || hit("v2-acct-cancel") || hit("v2-conn-x")) { close(); return; }
    if (hit("v2-acct-cancel")) { close(); return; }
    if (hit("v2-acct-export")) { doExportBeforeSignout(); return; }
    if (hit("v2-acct-signout")) {
      /* 两段式退出（2026-10-21）：有模型先摆出"不会保留"的英文提示，
       * 再点一次才真退——重新登录=新会话=空模型是不可逆的。 */
      if (hasUnsavedModel() && !_signoutArmed) {
        _signoutArmed = true;
        const w = $("v2-acct-warn");
        if (w) w.style.display = "block";
        const ex = $("v2-acct-export");
        if (ex) ex.style.display = "inline-flex";
        const so = $("v2-acct-signout");
        if (so) so.textContent = "Sign out anyway";
        return;
      }
      /* G72：登出同时清本机凭据仓（会话代持的 token）——sendBeacon
       * 在 unload 后仍能送达；失败不拦登出（大不了仓里 token 留到过期）。
       * G64 站长裁定 2026-10-23：换账号 = 干净进场——脚本与凭据/模型身份
       * 同批清光（编辑器文本不在下一个账号的屏幕上出现）。 */
      try { navigator.sendBeacon("/api/identity/clear"); } catch (e) {}
      localStorage.removeItem("genie_v2_api_key");
      localStorage.removeItem("genie_v2_username");
      localStorage.removeItem("genie_v2_script");
      genieDropSession();      /* O2：退出 = 模型身份作废（下次登录新模型） */
      close(); location.reload();
      return;
    }
    if (hit("v2-conn-save")) doConnect();
    if (hit("v2-reg-switch")) showView("register");
    if (hit("v2-login-switch")) showView("login");
    if (hit("v2-recover-switch")) showView("recover");
    if (hit("v2-cp-switch")) {
      showView(localStorage.getItem("genie_v2_api_key") ? "passwd" : "recover");
      return;
    }
    if (hit("v2-acct-cp")) { showView("passwd"); return; }
    if (hit("v2-pwd-back")) {
      showView(localStorage.getItem("genie_v2_api_key") ? "account" : "login");
      return;
    }
    if (hit("v2-rec-back")) showView("login");
    if (hit("v2-reg-send")) sendCode();
    if (hit("v2-reg-save")) doRegister();
    if (hit("v2-pwd-save")) doPasswd();
    if (hit("v2-rec-send")) sendRecCode();
    if (hit("v2-rec-save")) doRecover();
  });
  wrap.addEventListener("input", (e) => {
    if (e.target && e.target.id === "v2-reg-name") {
      const cleaned = e.target.value.replace(/[^A-Za-z0-9_-]/g, "");
      if (cleaned !== e.target.value) e.target.value = cleaned;
      if (nameTimer) clearTimeout(nameTimer);
      nameTimer = setTimeout(checkName, 300);
    }
  });
  $("v2-conn-pass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doConnect();
  });
  $("v2-reg-pass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doRegister();
  });
  $("v2-pwd-new").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doPasswd();
  });
  $("v2-rec-new").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doRecover();
  });
  if (initialView === "account") {
    const au = $("v2-acct-user");
    if (au) au.value = localStorage.getItem("genie_v2_username") || "";
  }
  showView(initialView === "recover" ? "recover"
    : (initialView === "passwd" && hasToken) ? "passwd"
    : (initialView === "account" && hasToken) ? "account" : "login");
}
$("login-toggle").addEventListener("click", () => {
  /* 站长裁决：已登录 = 按钮显示用户名，点击弹 Account 框（Sign out 主按钮）；
   * 未登录 = Connect，点击弹登录框 */
  connDialog(localStorage.getItem("genie_v2_api_key") ? "account" : "login");
});
$("login-toggle").title = "Sign in — exchange credentials for a submit token";

/* ── 未登录闸门（B 方案：未登录什么都不给做）──
 * health.auth_required 由服务端上报（本地默认 true；GENIE_STUDIO_OPEN=1 为
 * 假）。没有 token 且服务端要求登录 → 铺满遮罩 + 强制弹登录框，且该框
 * 不可关（Esc/X/取消全失效）。登录成功走既有 close()+reload，闸自然消失。 */
window.__authGate = false;
(function applyAuthGate() {
  const tok = localStorage.getItem("genie_v2_api_key");
  if (tok) return;
  const show = () => {
    window.__authGate = true;
    document.body.classList.add("auth-locked");
    const gate = document.getElementById("auth-gate");
    if (gate) gate.classList.remove("hidden");
    connDialog("login");
  };
  fetch("/api/health")
    .then((r) => (r.ok ? r.json() : null))
    .then((h) => {
      if (h && h.auth_required === false) return;   /* 免登录形态（OPEN=1） */
      show();
    })
    .catch(show);   /* health 都拿不到：保守按需要登录处理 */
})();

/* ── 登录即绑定会话（2026-10-21 站长裁决；O2 换名 attach）──
 * 以前只有 exec 才会把凭据交给会话，于是"不跑代码 = 没有模型"。
 * 这里在启动时补一次 attach（幂等）：会话随即物化一个**空模型**（无任何
 * 默认值），并回流 cfg → 树/表单/3D 立刻可用；**换 token 不会顶掉已有
 * 模型**（改密码/过期重登后回来，模型还在）。 */
(function attachSessionOnBoot() {
  if (!genieToken()) return;
  fetch("/api/session/attach", { method: "POST" })
    .then(async (r) => {
      if (r.ok) return r.json();
      const j = await r.json().catch(() => ({}));
      apiFailed("会话连接", r, j);
      return null;
    })
    .catch(() => apiNotice("会话连接失败：本机服务没响应（Studio 还在起？）", "err"));
})();

/* 「运行」接线（terminal 战役：整个脚本在会话里执行） */
$("run-py").addEventListener("click", runScript);
/* title 已由 index.html 声明（英文），无需运行期覆盖 */

window.getCfg = () => structuredClone(cfg);
window.__scene3dCount = () => scene && scene.structureGroup
  ? scene.structureGroup.children.length : -1;
/* G59 已核实（2026-10-23）：termBusy 是 term.js 的**纯提交闸门**（防连点），
 * 不是忙闲镜像（进度条=会话现值投影，kernel_worker 头注的"废除旁证"
 * 指的是后者）。本导出仅供 e2e 探针（m2_gui_smoke）与 mobile.js 消费。 */
window.__termState = () => ({ busy: termBusy, seq: SessionSync.lineSeq, cfgSeq: SessionSync.cfgSeq });
window.__termText = () => (document.getElementById("term-out") || {}).textContent || "";

/* ── 启动 ── */
renderTree();          /* 首屏就画树（无模型给空态引导，不留白板） */
renderInspector();
/* 启动只装载脚本文本（terminal 战役：编辑器=纯文本编辑器，
 * 运行才生效）。载入过就恢复上次文本，否则首屏自动装第一个示例。 */
const saved = localStorage.getItem("genie_v2_script");
if (saved) {
  $("code-editor").value = saved;
} else {
  /* 首访自动装第一份示例（无已存脚本时）——编辑器即刻有可跑的东西。
   * （原由 loadExamples 顺带返回清单；下拉退役后内联在此。） */
  fetch("/examples/manifest.json")
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(list => {
      if (!list || !list.length) return;
      return fetch("/examples/" + list[0].file)
        .then(r => r.ok ? r.text() : Promise.reject())
        .then(tx => { $("code-editor").value = tx; });
    })
    .catch(() => { /* 无示例清单不算错 */ });
}

/* ── 开屏对齐（2026-10-11 站长裁决：输入输出按时间序）────────
 * 游标一次性对齐共享环，把既有输出按原序落进 terminal——更早的
 * 在前；此后新输入的输出只排在自己 echo 之后（旧行为：开屏游标=0，
 * 第一次 Run 才拉环 → 全部旧史被塞在新 echo 后面，时序倒挂）。
 * cfg 投影同步渲染一次：打开页面即见活 f（GUI=f 只读投影）。 */
function bootSyncSession() {
  return fetch("/api/exec/output?since=0")
    .then(async (r) => {
      if (r.ok) return r.json();
      if (r.status >= 500) {          /* 会话/上游挂了：说一次，别静默 */
        const j = await r.json().catch(() => ({}));
        apiFailed("会话", r, j);
      }
      return null;                    /* 401 = 还没登录，属正常 */
    })
    .then(j => { if (j) SessionSync.ingest(j); })
    .catch(() => {});
}

/* ── 启动登录态校验（M1：token 永久有效，无需换发——whoami 校验
 * 已存 token；登录态只在连接按钮上回显（按钮=用户名，无多余状态
 * 提示）。失效/被吊销则清掉残留凭据回未登录态，用户走登录框；
 * 失败静默不打扰。──────────────────────────────────────────── */
(() => {
  /* 长用户名防御：按钮限宽截断（省略号），title 留全名。
   * 2026-10-21：标签包一层 span（按钮保持 inline-flex 定心，截断交给 span）——
   * 旧写法把 inline-block/overflow 直接打到按钮上，会顶掉布局盒。 */
  const setLoginLabel = (name) => {
    const b = document.getElementById("login-toggle");
    if (!b) return;
    b.textContent = "";
    const s = document.createElement("span");
    s.className = "hdr-btn-name";
    s.textContent = name;
    b.appendChild(s);
    b.classList.add("on");
    b.title = "Account (" + name + ")";
    window.dispatchEvent(new CustomEvent("genie-login",
      { detail: { on: true, name } }));
  };
  window.__setLoginLabel = setLoginLabel;   /* 登录框 reload 前后共用 */
  const k = genieToken();
  /* 未登录：不必对齐会话——服务端本来就把执行面挡住了（401 只会在
   * 控制台留一串红字噪音）。登录成功后的 reload 会走下面的分支。 */
  if (!k) return;
  /* /api/auth/* 不经过 fetch 包装（登录族不吃旧 token）→ 显式带头 */
  fetch("/api/auth/whoami", { headers: { "X-API-Key": k } })
    .then((r) => {
      if (r.ok) return r.json();
      if (r.status === 401) {           /* token 失效/被吊销：清凭据，
                                         * **保留 session_id**——同账号再登录
                                         * 回到同一个模型（O2） */
        genieDropToken();
        const b = document.getElementById("login-toggle");
        if (b) {
          b.textContent = "Connect";
          b.classList.remove("on");
          b.classList.add("err");
          b.title = "Session expired — sign in again";
        }

        window.dispatchEvent(new CustomEvent("genie-login", { detail: { on: false } }));
      }
      return null;
    })
    .then((j) => {
      if (j && j.principal) setLoginLabel(j.principal);
    }, () => {})
    .then(bootSyncSession, bootSyncSession)
    .catch(() => {});
})();
