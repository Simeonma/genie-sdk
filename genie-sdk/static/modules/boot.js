import { runScript } from "./script.js";
import { login, logout, status } from "./app.js";
import { SessionSync } from "./term.js";
import { onBatch, pollOnce } from "../core/ring.js";
import { jobProgress, refreshResultChip, resultStatus,
         setLastJobId } from "./result.js";

import { $ } from "./util.js";
import { genieForgetUser } from "./api.js";
import { Dock } from "./dock.js";
import { FormPanel } from "../views/form.js";
import { iconEl } from "../icons.js";
import { renderInspector } from "./inspector.js";
import { previewEl } from "../views/previews.js";
import { animate, initScene } from "./scene.js";
import { renderPythonHighlight } from "./script.js";
import { cfg, scene, toast } from "./state.js";

import { buildToolbar, syncViewMirror } from "./toolbar.js";
import { renderTree } from "./tree.js";
/* modules/boot.js — 接线与启动（FormPanel/工具条/场景 init、gutter、快捷键、连接框、开屏对齐、自动恢复）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */



/* ══ 接线（显示链路 + 表单编辑通道；工具条等器官仍不接线） ══════ */
/* 阶段 5b 单模式：表单编辑实时写入活动 SimConfig 并刷新全部视图；
 * 「模型已改」徽标提示与脚本分叉（重跑脚本即整体重建，语义如此）。 */
FormPanel.init("config-content");

buildToolbar();
initScene();
Dock.init();   /* dock 工作台（2026-10-15）：面板收养 + splitter + 布局持久化 */
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
(() => {
  const TABS = ["file", "view", "design", "fdtd", "mode"];
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
    if (name === "view") syncViewMirror();
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
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch {}
  activate(TABS.includes(saved) ? saved : "design");
})();

/* Agent（2026-10-17 迁顶栏右端）：宿主模式才有真面板，本地 Studio 先提示。 */
$("agent-open-btn")?.addEventListener("click", () => {
  toast("Agent 面板：阶段 6 宿主模式提供（本地 Studio 暂无）", "info");
});

/* Domain / Mesh 直达钮已删（2026-11-24 站长拍）：入口在左树，直达是冗余。 */

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

/* 文件 I/O 直上工具栏的转发块已删（L-35）：$("import-cfg") 在
 * index.html 里本就不存在——原块点击即抛。文件腿随 M6 重接。 */

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
$("download-py").addEventListener("click", () => {
  const blob = new Blob([$("code-editor").value], { type: "text/x-python" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "genie_sim.py";
  a.click();
  URL.revokeObjectURL(a.href);
});
/* ── FILE：空壳（2026-11-24 站长拍清空；文件腿随 M6/L-41 重接）──────── */

/* load-py 本机文件上岗（G17 走查补漏 2026-10-16）：f4d1e23 只上了钮
 * 没接线——出生即死钮。语义同工作区点行载入：File API 读用户笔记本
 * （零上传），ed.value + input 事件（未存态，Run 前必存进工作区）。 */
export function loadPyText(text, name) {
  const ed = $("code-editor");
  ed.value = text;
  ed.dispatchEvent(new Event("input", { bubbles: true }));
  toast("Loaded " + (name || "local file") + "（未存态——Run 前必存）", "info");
}
export function loadPyLocal() {
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
$("load-py").addEventListener("click", loadPyLocal);
/* 账号对话框（M1）：Sign in（未登录）/ Account（已登录）/ 子视图
 * （Create account、Reset password、Change password）。
 * 视觉：统一 gc-* 类（一次注入 style，替代散落 inline style）；
 * 主状态不设标题（内容自明），子视图保留小标题区分上下文。
 * 交互全走 wrap 事件委托（closest 分发）。 */
export const GC_STYLE_ID = "v2-conn-style";
export function ensureConnStyle() {
  if (document.getElementById(GC_STYLE_ID)) return;
  const st = document.createElement("style");
  st.id = GC_STYLE_ID;
  st.textContent = `
#v2-conn{position:fixed;inset:0;z-index:10000;background:rgba(16,21,26,.34);
  display:flex;align-items:center;justify-content:center;
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
#v2-conn .gc-box{background:var(--bg-elevated,#fff);border:1px solid var(--border,#DCE0E6);
  border-radius:var(--radius-lg,6px);padding:0 22px 18px;width:368px;position:relative;
  box-sizing:border-box;box-shadow:var(--shadow-3);
  font-size:var(--fs-sm,14px);color:var(--text-primary,#000)}
/* 关闭钮（2026-10-21）：右上角 24px 静默图标，hover 才浮底——与主界面
 * 面板动作钮同一语言；也补上「看得见的退出路径」（Esc 之外）。 */
#v2-conn .gc-x{position:absolute;top:8px;right:8px;width:24px;height:24px;
  display:inline-flex;align-items:center;justify-content:center;
  background:none;border:1px solid transparent;border-radius:var(--radius-sm,3px);
  color:var(--text-muted,#5E6A76);cursor:pointer;padding:0;
  transition:background .12s,color .12s,border-color .12s}
#v2-conn .gc-x:hover{background:var(--bg-hover,#EDF0F4);color:var(--text-primary,#000);
  border-color:var(--border,#DCE0E6)}
#v2-conn .gc-head{padding:18px 0 12px;margin-bottom:14px;
  border-bottom:1px solid var(--border-soft,#EDEFF2)}
#v2-conn .gc-title{font-size:var(--fs-xl,17px);font-weight:600;margin:6px 0 0;
  letter-spacing:.01em;color:var(--text-primary,#000)}
#v2-conn .gc-field{margin-top:13px}
#v2-conn .gc-field:first-of-type{margin-top:0}
#v2-conn .gc-label{display:flex;justify-content:space-between;align-items:baseline;
  font-size:var(--fs-xs,12px);font-weight:500;color:var(--text-secondary,#39434E);
  margin-bottom:5px;letter-spacing:.01em}
#v2-conn .gc-input{width:100%;box-sizing:border-box;height:30px;padding:0 9px;
  font-size:var(--fs-xs,12px);color:var(--text-primary,#000);background:var(--bg-input,#fff);
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
#v2-conn .gc-actions-btns{display:flex;gap:8px}
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
#v2-conn .gc-btn.secondary:hover{background:var(--bg-tertiary,#F6F7F9);color:var(--text-primary,#000)}
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
/* 本月账（2026-11-22）：key-value 三列行——量 / 配额 / 笔数；发丝线分行，
  等宽数字，不占输入框的形。 */
#v2-conn .gc-usage-head{margin:2px 0 6px;font-size:var(--fs-2xs,11px);
  font-weight:600;letter-spacing:.1em;text-transform:uppercase;
  color:var(--text-muted,#5E6A76)}
#v2-conn .gc-usage{border:1px solid var(--border-soft,#EDEFF2);
  border-radius:var(--radius-sm,3px);overflow:hidden}
#v2-conn .gc-usage-row{display:grid;grid-template-columns:52px 1fr auto;
  gap:10px;align-items:baseline;padding:6px 9px;
  border-top:1px solid var(--border-soft,#EDEFF2);font-size:var(--fs-xs,12px)}
#v2-conn .gc-usage-row:first-child{border-top:0}
#v2-conn .gc-usage-row .k{color:var(--text-muted,#5E6A76);
  font-weight:600;letter-spacing:.04em}
#v2-conn .gc-usage-row .v{color:var(--text-primary,#000);
  font-variant-numeric:tabular-nums;text-align:right}
#v2-conn .gc-usage-row .n{color:var(--text-muted,#5E6A76);
  font-variant-numeric:tabular-nums;min-width:52px;text-align:right}
`;
  document.head.appendChild(st);
}

/* ── 登出（v2 语义）：只撤 host 的云凭据（Q2.1），模型与脚本保留。 ── */

export function connDialog(initialView) {
  ensureConnStyle();
  const old = document.getElementById("v2-conn");
  if (old) old.remove();

  const wrap = document.createElement("div");
  wrap.id = "v2-conn";
  /* v2：登录态只认 host（GET /auth），浏览器没有 token 可看。 */
  const loggedIn = !!(_genieAuth && _genieAuth.logged_in);
  wrap.innerHTML = `
    <div class="gc-box">
      <button class="gc-x" id="v2-conn-x" type="button" aria-label="Close" title="Close (Esc)">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
          stroke-width="1.6" stroke-linecap="round"><path d="M3.6 3.6 L12.4 12.4 M12.4 3.6 L3.6 12.4"/></svg>
      </button>
      <div id="v2-view-login">
        <div class="gc-head">
          <div class="gc-title">Sign in</div>
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-conn-user">Username or email</label>
          <input id="v2-conn-user" class="gc-input"
            autocomplete="username" placeholder="username or email">
        </div>
        <div class="gc-field">
          <label class="gc-label" for="v2-conn-pass">Password
            <a class="gc-link" href="https://genie-optics.com/account/reset/" target="_blank" rel="noopener">Forgot password?</a></label>
          <input id="v2-conn-pass" class="gc-input" type="password"
            autocomplete="current-password">
        </div>
        <div id="v2-conn-msg"></div>
        <div class="gc-links" style="justify-content:space-between">
          <a class="gc-link" href="https://genie-optics.com/account/reset/" target="_blank" rel="noopener">Change password</a>
          <a class="gc-link" href="https://genie-optics.com/account/register/" target="_blank" rel="noopener">Create account →</a>
        </div>
        <div class="gc-actions">
          <button id="v2-conn-cancel" class="gc-btn secondary">Cancel</button>
          <button id="v2-conn-save" class="gc-btn primary">Sign in</button>
        </div>
      </div>
      <div id="v2-view-account" style="display:none">
        <div class="gc-head">
          <div class="gc-title">Account</div>
        </div>
        <!-- 账本区块（2026-11-22 接通）：key-value 行，不是输入框 -->
        <div id="v2-acct-usage" style="display:none">
          <div class="gc-usage-head" id="v2-acct-usage-head">Usage</div>
          <div class="gc-usage">
            <div class="gc-usage-row">
              <span class="k">GPU</span>
              <span class="v" id="v2-u-gpu">—</span>
              <span class="n" id="v2-u-gpu-n"></span>
            </div>
            <div class="gc-usage-row">
              <span class="k">CPU</span>
              <span class="v" id="v2-u-cpu">—</span>
              <span class="n" id="v2-u-cpu-n"></span>
            </div>
          </div>
        </div>
        <div class="gc-actions" style="justify-content:space-between">
          <a id="v2-acct-cp" class="gc-link"
             href="https://genie-optics.com/account/reset/"
             target="_blank" rel="noopener">Change password</a>
          <span class="gc-actions-btns">
            <button id="v2-acct-cancel" class="gc-btn secondary">Cancel</button>
            <button id="v2-acct-signout" class="gc-btn primary">Sign out</button>
          </span>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const $ = (id) => wrap.querySelector("#" + id);
  let onKey = null;
  const close = () => {
    if (onKey) document.removeEventListener("keydown", onKey);
    onKey = null;
    wrap.remove();
  };
  onKey = (e) => { if (e.key === "Escape" && !_authGate) close(); };
  document.addEventListener("keydown", onKey);
  /* 闸门态：登录框不可关（没有可用的工作区可回） */
  if (_authGate) {
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
  /* 账本区块（2026-11-22 接通）：host `/usage` → 网关 `/api/usage`（唯一
   * 拨号在 host）。key-value 三列行（量 / 配额 / 笔数），等宽数字右对齐；
   * 拿不到（未登录 / 网关不在 / 老宿主）整块隐去，不留半截。 */
  function fillAcctUsage() {
    const box = $("v2-acct-usage");
    if (!box) return;
    box.style.display = "none";
    /* 配额 ≥999999 = 不限（与网关硬闸同约定）：分母写 ∞，与有限配额同列 */
    const cap = (v, c) => (typeof v === "number" ? v.toFixed(2) : "—")
      + (Number(c) >= 999999 ? " / ∞" : (" / " + (typeof c === "number" ? c : "?")));
    fetch("/usage").then((r) => r.json()).then((u) => {
      if (!u || u.gateway === false || u.logged_in === false) return;
      const q = u.quota || {};
      $("v2-u-gpu").textContent = cap(u.gpu_hours, q.gpu_hours_monthly) + " h";
      $("v2-u-cpu").textContent = cap(u.cpu_hours, q.cpu_hours_monthly) + " h";
      $("v2-u-gpu-n").textContent =
        typeof u.count === "number" ? u.count + " jobs" : "";
      $("v2-u-cpu-n").textContent =
        typeof u.cpu_count === "number" ? u.cpu_count + " jobs" : "";
      $("v2-acct-usage-head").textContent = "Usage · " + (u.month || "this month");
      box.style.display = "block";
    }).catch(() => { /* 拿不到就不显示：不编数字 */ });
  }
  const showView = (v) => {
    ["login", "account"].forEach((k) => {
      const el = $("v2-view-" + k);
      if (el) el.style.display = k === v ? "block" : "none";
    });
    if (v === "account") fillAcctUsage();
  };

  /* ── Sign in（v2：host 代拨 /gw 并截获云 token；浏览器零 token）── */
  async function doLogin(u, p) {
    if (!u || !p) { msg("Enter your username and password"); return false; }
    try {
      const j = await login(u, p);
      if (j && j.ok) { close(); location.reload(); return true; }
      msg(j && j.detail ? j.detail : "Sign-in failed");
    } catch (e) { msg("Login request failed: " + e); }
    return false;
  }
  function doConnect() {
    const u = ($("v2-conn-user") || {}).value ? $("v2-conn-user").value.trim() : "";
    const p = ($("v2-conn-pass") || {}).value || "";
    doLogin(u, p);
  }


  /* ── 事件委托 ── */
  wrap.addEventListener("click", (e) => {
    /* 闸门态：点遮罩/取消都不关（必须登录才能进） */
    if (_authGate
        && (e.target === wrap
            || (e.target.closest && (e.target.closest("#v2-conn-cancel")
                || e.target.closest("#v2-conn-x"))))) return;
    if (e.target === wrap) { close(); return; }
    const hit = (id) => e.target.closest && e.target.closest("#" + id);
    if (hit("v2-conn-cancel") || hit("v2-acct-cancel")
        || hit("v2-conn-x")) { close(); return; }
    if (hit("v2-acct-signout")) {
      logout().catch(() => {});
      localStorage.removeItem("genie_v2_api_key");
      localStorage.removeItem("genie_v2_username");
      localStorage.removeItem("genie_v2_script");
      genieForgetUser();
      close(); location.reload();
      return;
    }
    if (hit("v2-conn-save")) doConnect();
  });
  $("v2-conn-pass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doConnect();
  });
  showView(initialView === "account" && loggedIn ? "account" : "login");
}
$("login-toggle").addEventListener("click", () => {
  /* 站长裁决：已登录 = 按钮显示用户名，点击弹 Account 框（Sign out 主按钮）；
   * 未登录 = Connect，点击弹登录框。v2 登录态 = host 凭据仓（GET /auth）。 */
  connDialog(_genieAuth && _genieAuth.logged_in ? "account" : "login");
});
$("login-toggle").title = "Sign in — the host holds the cloud token";

let _authGate = false;
let _genieAuth = null;
(function applyAuthGate() {
  const show = () => {
    _authGate = true;
    document.body.classList.add("auth-locked");
    const gate = document.getElementById("auth-gate");
    if (gate) gate.classList.remove("hidden");
    connDialog("login");
  };
  status()
    .then((a) => {
      _genieAuth = a || null;
      if (a && a.logged_in) return;            /* 已登录：闸不出现 */
      if (a && a.gateway === false) return;    /* 未配置网关：本地形态 */
      show();
    })
    .catch(show);
})();

/* 「运行」接线（terminal 战役：整个脚本在 worker 里执行） */
$("run-py").addEventListener("click", runScript);
/* title 已由 index.html 声明（英文），无需运行期覆盖 */



/* ── 作业进度卡：rollup → 进度条（P1-21 步进/批形两种喂法）── */
function feedJobProgress(data) {
  if (!data) return;
  const jobs = data.jobs || {};
  const ids = Object.keys(jobs);
  const total = Number(data.total) || ids.length;
  let terminal = 0, failed = Number(data.failed) || 0;
  for (const id of ids) {
    const st = (jobs[id] || {}).state;
    if (st === "done" || st === "failed" || st === "killed" || st === "canceled") {
      terminal += 1;
    }
  }
  const finished = Math.max((Number(data.done) || 0) + failed, terminal);
  const allTerminal = total > 0 && finished >= total;
  if (!allTerminal) {
    const running = ids.filter((id) => {
      const st = (jobs[id] || {}).state;
      return st !== "done" && st !== "failed" && st !== "killed"
        && st !== "canceled";
    });
    const dg = running.length === 1 && jobs[running[0]]
      && jobs[running[0]].digest || null;
    const steps = dg && dg.compute && dg.compute.steps;
    if (dg && steps && Number(steps.total) > 0) {
      jobProgress({
        step: Number(steps.done) || 0, n_steps: Number(steps.total),
        job_id: running[0], digest: dg,
        batch: total > 1 ? { finished, total } : null,
      });
      return;
    }
    jobProgress({ step: finished, n_steps: total, job_id: ids[0] || "" });
    return;
  }
  if (ids[0]) setLastJobId(ids[0]);
  refreshResultChip();
  const chip = document.getElementById("result-chip");
  if (chip && !resultStatus) {
    chip.classList.remove("hidden");
    chip.textContent = "";
    const sp = document.createElement("span");
    sp.className = "jobprog-txt";
    sp.textContent = (failed > 0 ? "✗ " + failed + " failed · " : "✓ ")
      + "job " + (ids[0] || "?") + " · results/" + (ids[0] || "?") + ".h5";
    sp.title = "结果成员在 .gensim 里";
    chip.appendChild(sp);
  }
}

/* 环批次 → 显示层：终端/表单/树/3D 由 SessionSync 统一收口 */
onBatch((snap, epochChanged) => {
  if (epochChanged) {
    const out = document.getElementById("term-out");
    if (out) out.textContent = "";
  }
  SessionSync.ingest(snap);
  if (snap.job_progress) feedJobProgress(snap.job_progress);
});

/* 窗口内刷新（2026-11-22）：Ctrl+R / F5 重载。
 * Ctrl+N/O/S 文件快捷键已删（L-35，文件腿随 M6 重接）。 */
document.addEventListener("keydown", (e) => {
  const ctrl = e.ctrlKey || e.metaKey;
  const k = e.key.toLowerCase();
  if ((ctrl && !e.shiftKey && !e.altKey && k === "r") || e.key === "F5") {
    e.preventDefault();
    location.reload();
  }
});

/* ── 启动 ── */
renderTree();          /* 首屏就画树（无模型给空态引导，不留白板） */
renderInspector();
/* 编辑器文本 = 工作区 script.py，由连线层（modules/app.js）载入/即敲即存。 */

/* ── 开屏对齐：v2 环投影一拍（输出与 cfg 快照的唯一来源）────────── */
export function bootSyncSession() {
  return pollOnce().catch(() => {});
}

/* ── 启动登录态回显（v2：GET /auth 的服务端真相；无浏览器 token）── */
(() => {
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
  status()
    .then((a) => {
      _genieAuth = a || null;
      if (a && a.logged_in) {
        setLoginLabel(a.username
          || (localStorage.getItem("genie_v2_username") || "user"));
      }
    }, () => {})
    .then(bootSyncSession, bootSyncSession)
    .catch(() => {});
})();
