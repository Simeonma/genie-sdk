/* Genie FDTD service — 参数面板（Phase B2 工作台版，2026-08-30）
 *
 * 旧 Studio 排版复活：config 栏一次只渲染 nav 选中的一节（Materials/
 * Geometry 的 nav 子项直达单张卡片）。表单 = 配置 JSON 的一种**视图**
 * （渐进披露）：覆盖常用字段，全量能力走第四栏 JSON 编辑器。
 *
 * 纪律：
 *  - 一律 createElement/textContent，零 innerHTML（XSS）
 *  - **字段编辑视图**（阶段 5b 单模式：AGENTS 铁律 1/2）——编辑实时
 *    作用于活动 SimConfig 并刷新全部视图；写回 = 最小换算（纯数 ×
 *    字段单位 → SI），表达式串/其余原样透传（实时校验唯一真源 =
 *    服务端 /api/validate）。显示用友好单位，值↔SI 换算挂 title。
 */
"use strict";

/* ═══ 字段默认单位(2026-09 定案)：表单友好单位，JSON/代码视图保持 SI ═══
 * 纯数字按此换算写回；后缀输入（UNIT_SUFFIXES）优先。hint 显示 SI 等效。 */
const FIELD_UNITS = {
  len:  { toSI: (v) => v * 1e-6,  toUser: (v) => v * 1e6,
          label: "µm", si: "m",  hint: "len" },
  wl:   { toSI: (v) => v * 1e-9,  toUser: (v) => v * 1e9,
          label: "nm", si: "m",  hint: "len" },
  time: { toSI: (v) => v * 1e-15, toUser: (v) => v * 1e15,
          label: "fs", si: "s",  hint: "time" },
  freq: { toSI: (v) => v * 1e12,  toUser: (v) => v / 1e12,
          label: "THz", si: "Hz", hint: "freq" },
  ang:  { toSI: (v) => v * 2 * Math.PI * 1e12,
          toUser: (v) => v / (2 * Math.PI * 1e12),
          label: "THz(ω=2πf)", si: "rad/s", hint: "ang" },
};

/* 可解析的友好显示：6 位有效数字、非极端量级用十进制（0.5 / 381.97） */
function fmtUserNum(x) {
  if (typeof x !== "number" || !isFinite(x)) return "";
  return String(parseFloat(x.toPrecision(6)));
}

function _dispValue(val, unit) {
  if (Array.isArray(val))
    return val.map((x) =>
      typeof x === "number" && unit ? fmtUserNum(unit.toUser(x))
      : x == null ? "" : String(x)).join(", ");
  if (typeof val === "number" && unit) return fmtUserNum(unit.toUser(val));
  return val == null ? "" : String(val);
}

/* ── 路径读写（点分；数组下标即路径段）── 阶段 5b 回装（最小写回） */
function getPath(obj, path) {
  let c = obj;
  for (const k of String(path).split(".")) {
    if (c == null) return undefined;
    c = c[k];
  }
  return c;
}

function setPath(obj, path, value) {
  const keys = String(path).split(".");
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i], nxt = keys[i + 1];
    if (cur[k] == null || typeof cur[k] !== "object")
      cur[k] = /^\d+$/.test(nxt) ? [] : {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
}

function deletePath(obj, path) {
  const keys = String(path).split(".");
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    cur = cur ? cur[keys[i]] : null;
    if (cur == null) return;
  }
  if (cur) delete cur[keys[keys.length - 1]];
}

/* ── 单位后缀（2026-10-21 用户实测）：注释一直写着"[单位后缀]优先"，
 * 但其实没有任何实现——输入 `1um` 会被当成**表达式字符串**原样写进
 * 模型，随后 genie.models 求值报语法错（`invalid decimal literal`），
 * 用户看到的是"值没生效/变成怪数字"。这里补上：
 *   裸数字  → 按字段单位换算（domain 里 1 = 1 µm）
 *   带后缀  → 后缀说了算，直接得 SI 绝对值（1um = 1e-6 m，与字段单位无关）
 * 认不出后缀又不像纯数字的（如 `lambda/2`、"1e-6*2"）照旧透传给服务端求值。 */
const _UNIT_SUFFIX = {
  m: 1, dm: 1e-1, cm: 1e-2, mm: 1e-3,
  um: 1e-6, "µm": 1e-6, "μm": 1e-6, nm: 1e-9, pm: 1e-12,
  s: 1, ms: 1e-3, us: 1e-6, "µs": 1e-6, "μs": 1e-6,
  ns: 1e-9, ps: 1e-12, fs: 1e-15,
  thz: 1e12, ghz: 1e9, mhz: 1e6, khz: 1e3, hz: 1,
};
function parseSuffixed(t) {
  const m = String(t).trim().match(
    /^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Zµμ]+)$/);
  if (!m) return NaN;
  const f = _UNIT_SUFFIX[m[2].toLowerCase()];
  return f === undefined ? NaN : Number(m[1]) * f;
}

/* 写回换算（最小版）：纯数 × 字段单位 → SI（15 位有效数字杀浮点噪声）；
 * 单位后缀见 parseSuffixed；其余非纯数（表达式串）原样透传——实时校验在
 * 服务端 /api/validate（genie.models 同一双手），浏览器零求值（阶段 5②）。 */
function writeBackValue(raw, kind, unitKey) {
  const txt = String(raw == null ? "" : raw).trim();
  const unit = FIELD_UNITS[unitKey] || null;
  const toSI = (t) => {
    const f = Number(t);
    if (t !== "" && !Number.isFinite(f)) {
      const s = parseSuffixed(t);            /* `1um` / `500 nm` / `2ns` */
      return Number.isFinite(s) ? parseFloat(s.toPrecision(15)) : NaN;
    }
    if (t === "" || !Number.isFinite(f)) return NaN;
    return unit ? parseFloat(unit.toSI(f).toPrecision(15)) : f;
  };
  if (kind === "num3") {
    const parts = txt.split(/[,\s]+/).filter(Boolean);
    if (parts.length === 3 && parts.every((t) => Number.isFinite(toSI(t))))
      return parts.map(toSI);
    const v = toSI(txt);
    return Number.isFinite(v) ? v : txt;
  }
  if (kind === "num") {
    const v = toSI(txt);
    return Number.isFinite(v) ? v : txt;
  }
  return txt;
}

const FormPanel = {
  container: null,
  cfg: null,
  onMutate: null,        // (cfg) => void —— 表单编辑 → app.js（徽标/3D/校验）
  current: "domain",    // 当前打开的节
  currentSub: -1,       // nav 子项高亮的卡片下标（-1 = 无）

  init(containerId, hooks) {
    this.container = document.getElementById(containerId);
    this.onMutate = (hooks && hooks.onMutate) || null;
  },

  setCfg(cfg) { this.cfg = cfg; },

  /* nav/组视图入口 → 复位为组视图（only=null 持久，直到 showInstance） */
  show(sectionKey, subIndex) {
    this.current = sectionKey;
    this.currentSub = subIndex == null ? -1 : subIndex;
    this.only = null;
    this._render();
  },

  /* 场景树 P1（2026-09-scene-editor）：只渲染列表中的选中实例——
   * 检查器 = 单对象参数视图；only 在 _render 期间生效后复位 */
  /* 场景树单对象视图：only 持久到下一次 show()/组切换——实例内的
   * _rerender（切形态、极点类型等）不丢上下文（2026-09 站长反馈） */
  showInstance(sectionKey, index) {
    if (this.cfg == null) return;
    this.current = sectionKey;
    this.currentSub = index == null ? -1 : index;
    this.only = index;
    this._render();
  },

  _items(list) {
    if (this.only != null && list[this.only] != null)
      return [[list[this.only], this.only]];
    return list.map((v, i) => [v, i]);
  },

  /* 结构变化（增删卡/切类型）后重建当前节 */
  _rerender() {
    this._render();
    this._notifyMutate();
  },

  _notifyMutate() {
    if (this.onMutate) this.onMutate(this.cfg);
  },

  _render() {
    const c = this.container;
    c.textContent = "";
    const panel = {
      domain: "domain", mesh: "mesh", materials: "materials",
      geometry: "geometry", source: "source", monitor: "monitor", run: "run",
    }[this.current];
    if (!panel) return;

    /* 2026-10-21 用户定案：不要大标题与信息摘要——面板直接从内容开始。
     * 单例面板（Domain / Mesh / Settings）开头补一行「图标 + 名字」，与对象
     * 卡的卡片头同一刻度：没有对象可指认时，这行就是面板身份。 */
    const body = document.createElement("div");
    body.className = "pane-body";
    c.appendChild(body);
    if (this.only == null) {
      const ID = { domain: ["Domain", "domain"], mesh: ["Mesh", "mesh"],
                   run: ["Settings", "run"] }[this.current];
      if (ID) body.appendChild(this._paneId(ID[0], ID[1]));
    }

    switch (this.current) {
      case "domain": this._domain(body); break;
      case "mesh": this._mesh(body); break;
      case "materials": this._materials(body); break;
      case "geometry": this._geometry(body); break;
      case "source": this._source(body); break;
      case "monitor": this._monitor(body); break;
      case "run": this._run(body); break;
    }

    /* 标签全文兜底（2026-10-21）：窄栏里长字段名（apodize_window /
     * sample_window …）会被省略号截断成 "apod wind…"，悬停必须能看到全名
     * ——统一把 title 补成 label 全文，不依赖逐字段记得加。 */
    body.querySelectorAll(".flabel").forEach((lb) => {
      if (!lb.title) lb.title = (lb.textContent || "").trim();
    });


    // 材料名 datalist（geometry 的 material 字段自动补全）
    const dl = document.getElementById("mat-names");
    if (dl) {
      dl.textContent = "";
      for (const n of this._matNames()) {
        const o = document.createElement("option");
        o.value = n;
        dl.appendChild(o);
      }
    }
  },

  /* 校验标错：错误路径 → 当前节内对应输入红框（其他节的错由 nav 红点提示）
   * 2026-09-02 审查修复：错误不再只进 hover title——行下插入内联错误
   * 文本（色+文字），并设 aria-invalid / aria-describedby 供读屏播报。 */
  /* 路径归一(08-31cd): 内核错误路径含中间类型段
   * (geometry.0.box.x_range), 表单字段路径没有 (geometry.0.x_range.0);
   * 剥掉中间段才能前缀匹配标红。 */
  _normErrPath(p) {
    return String(p).replace(
      /^(geometry|source|monitor|materials)\.\d+\.\w+\.(.+)$/,
      (m, sec, rest) => sec + "." + rest);
  },

  _clearFieldError(i) {
    i.classList.remove("field-err");
    i.removeAttribute("aria-invalid");
    i.removeAttribute("aria-describedby");
    i.title = "";
    const host = i.closest(".frow") || i.closest(".brow") || i.parentElement;
    if (host && host._errEl) {
      host._errEl.remove();
      host._errEl = null;
    }
  },

  markFieldErrors(errors) {
    const inputs = this.container.querySelectorAll("input[data-path], select[data-path]");
    inputs.forEach((i) => this._clearFieldError(i));
    for (const e of errors || []) {
      const p = this._normErrPath(e.path || "");
      if (!p) continue;
      inputs.forEach((i) => {
        const ip = i.dataset.path;
        if (ip === p || p.startsWith(ip + ".") || ip.startsWith(p + ".")) {
          i.classList.add("field-err");
          i.setAttribute("aria-invalid", "true");
          i.title = (i.title ? i.title + "\n" : "") + e.message;
          /* 内联错误：挂在行（frow/brow）下方，整行一条，多错合并 */
          const host = i.closest(".frow") || i.closest(".brow") || i.parentElement;
          if (host) {
            if (!host._errEl) {
              const msg = this._el("div", "field-err-msg");
              msg.id = "fem-" + (++this._fldSeq);
              host._errEl = msg;
              host.parentNode.insertBefore(msg, host.nextSibling);
            }
            const cur = host._errEl.textContent;
            const line = "⚠ " + e.message;
            if (!cur.includes(line))
              host._errEl.textContent = cur ? cur + "　" + line : line;
            i.setAttribute("aria-describedby", host._errEl.id);
          }
        }
      });
    }
  },

  /* ══════════════════ DOM 小工具 ══════════════════ */

  _el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  },

  /* 面板身份行（2026-10-21 用户定案）：单例面板（Domain / Mesh / Settings）
   * 也要像对象卡那样"开头先给图标 + 名字"。与 .card-head 同刻度（44px 缩略图
   * + 14/600 名字），不带摘要、不带 chip。 */
  _paneId(name, iconKey) {
    const row = this._el("div", "pane-id");
    const ic = this._el("span", "pane-id-ic");
    ic.appendChild(previewEl(iconKey));
    row.appendChild(ic);
    row.appendChild(this._el("span", "pane-id-name", name));
    return row;
  },

  /* 面板内空态：淡图标 + 一行标题 + 一行提示（列表为空时不再留白） */
  _paneEmpty(iconKey, title, hint) {
    const wrap = this._el("div", "empty-state pane-empty-state");
    const ic = this._el("span", "empty-ic");
    try { ic.appendChild(previewEl(iconKey)); } catch { /* 图标缺失不拦渲染 */ }
    wrap.appendChild(ic);
    wrap.appendChild(this._el("div", "empty-title", title));
    wrap.appendChild(this._el("div", "empty-hint", hint));
    return wrap;
  },

  /* 模型级校验错误横幅（2026-10-21）：没有字段路径的错误（如"几何名字重复"）
   * 挂在面板顶部一条警示带里；传空串即清除。 */
  showModelError(msg) {
    const host = this.container;
    if (!host) return;
    let bar = host.querySelector(".model-err");
    if (!msg) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = this._el("div", "model-err");
      bar.setAttribute("role", "alert");
      host.insertBefore(bar, host.firstChild);
    }
    bar.textContent = "⚠ " + msg;
  },

  /* R3 标签排版：label 尾部 "(unit)" 后缀拆为 .unit span（视觉降档，CSS 弱化）。
   * 纯视觉——title/aria-label/htmlFor 仍用完整 label 字符串，cfg 键/路径零改动；
   * 零 innerHTML（createElement + textContent）。无括号后缀时行为同旧版。 */
  _fillLabel(lab, label) {
    const m = typeof label === "string"
      ? label.match(/^(.*?)\s*\(([^()]*)\)\s*$/) : null;
    if (m && m[1]) {
      const t = this._el("span", null, m[1]);
      t.style.minWidth = "0";           /* 配合 .flabel flex：长名截省略号，单位永不截 */
      lab.appendChild(t);
      lab.appendChild(this._el("span", "unit", "(" + m[2] + ")"));
    } else if (label != null) {
      lab.textContent = label;
    }
    return lab;
  },

  _siHint(kind, v) {
    if (typeof v !== "number" || !isFinite(v) || v === 0) return "";
    const a = Math.abs(v);
    const fmt = (x) => {
      const s = x.toPrecision(4);
      if (s.includes("e")) return s;
      // 只剥小数尾零（"30.00"→"30"）；无小数点的整数串不动（"2000"≠"2"）
      return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
    };
    if (kind === "len") {
      if (a >= 1e-3) return fmt(v * 1e3) + " mm";
      if (a >= 1e-6) return fmt(v * 1e6) + " µm";
      return fmt(v * 1e9) + " nm";
    }
    if (kind === "freq") return fmt(v / 1e12) + " THz";
    if (kind === "time") {
      if (a >= 1e-6) return fmt(v * 1e6) + " µs";
      if (a >= 1e-9) return fmt(v * 1e9) + " ns";
      if (a >= 1e-12) return fmt(v * 1e12) + " ps";
      return fmt(v * 1e15) + " fs";
    }
    if (kind === "ang") return fmt(v / 1e12) + "×10¹² rad/s";
    return "";
  },

  /* autofill 卫生（2026-10 DevTools advisory）：动态字段补唯一 id/name
   * （Chrome Issues 面板指控「无 id 也无 name」）；配置表单非个人信
   * 息，autocomplete=off 一并显式声明。path 消毒成合法 id 片段，撞车
   * 时追加序号保证文档内唯一。 */
  _fid(path) {
    const base = "f-" + String(path).replace(/[^A-Za-z0-9_-]/g, "-");
    let id = base, n = 1;
    while (document.getElementById(id)) id = base + "-" + (++n);
    return id;
  },
  _tagField(el, path) {
    if (!path) return;
    const id = this._fid(path);
    el.id = id;
    el.name = id;
    el.setAttribute("autocomplete", "off");
  },

  _input(path, val, kind /* num|str */, hintKind) {
    /* 只读视图（阶段 5①）：值/单位换算只做展示；表达式槽原样显示文本 */
    const input = document.createElement("input");
    input.type = "text";
    if (path) input.dataset.path = path;
    this._tagField(input, path);
    input.dataset.kind = kind;
    if (kind === "num" || kind === "num3") input.inputMode = "decimal";
    const unit = FIELD_UNITS[hintKind] || null;
    if (unit) input.dataset.unit = hintKind;
    input.value = _dispValue(val, unit);
    const hint = this._el("span", "si-hint");
    if (unit) hint.style.display = "none";
    const f = parseFloat(input.value);
    if (unit && isFinite(f)) {
      const si = parseFloat(unit.toSI(f).toPrecision(15));
      input.title = `${f} ${unit.label} → cfg ${si} ${unit.si}`;
    } else {
      hint.textContent = this._siHint(hintKind, isFinite(f) ? f : null);
    }
    input.addEventListener("input", () => this._writeBack(input));
    const wrap = document.createElement("span");
    wrap.className = "inwrap";
    wrap.appendChild(input);
    wrap.appendChild(hint);
    return wrap;
  },

  _select(path, val, options, emptyDeletes) {
    const sel = document.createElement("select");
    if (path) sel.dataset.path = path;
    this._tagField(sel, path);
    sel.dataset.kind = "str";
    if (emptyDeletes) sel.dataset.emptyDeletes = "1";
    for (const o of options) {
      const opt = document.createElement("option");
      opt.value = o;
      opt.textContent = o === "" ? "（不设置）" : o;
      sel.appendChild(opt);
    }
    if (val != null && val !== "") {
      if (!options.includes(val)) {
        const opt = document.createElement("option");
        opt.value = val; opt.textContent = String(val);
        sel.appendChild(opt);
      }
      sel.value = val;
    }
    sel.addEventListener("change", () => this._writeBack(sel));
    return sel;
  },

  /* 三态布尔选择（真/false/未设置=删除字段）——bool 走 JSON 线格式 */
  _boolSelect(path, val, label) {
    const sel = document.createElement("select");
    if (path) sel.dataset.path = path;
    this._tagField(sel, path);
    sel.dataset.kind = "bool";
    sel.title = label;
    sel.setAttribute("aria-label", label);   /* P2-5 a11y：✓/✗/— 无文字，显式可访问名 */
    for (const [v, t] of [["", "—"], ["true", "✓"], ["false", "✗"]]) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = t;
      sel.appendChild(o);
    }
    sel.value = val === true ? "true" : val === false ? "false" : "";
    sel.addEventListener("change", () => this._writeBack(sel));
    return sel;
  },

  /* 参数区段头(08-31bz: COMSOL group header——色带 + 可折叠)
   * 2026-09-02 审查修复：键盘可达（role=button + Enter/Space + aria-expanded） */
  _sec(title, collapsed, count) {
    const head = this._el("div", "sec-head", title);
    const body = this._el("div", "sec-body");
    /* 折叠带可选计数 badge：让默认收起的分节"一眼可扫"（复用材料极点数法） */
    if (count != null) head.appendChild(this._el("span", "sec-badge", String(count)));
    head.setAttribute("role", "button");
    head.tabIndex = 0;
    head.setAttribute("aria-expanded", collapsed ? "false" : "true");
    if (collapsed) { head.classList.add("collapsed"); body.style.display = "none"; }
    const toggle = () => {
      head.classList.toggle("collapsed");
      const open = !head.classList.contains("collapsed");
      body.style.display = open ? "" : "none";
      head.setAttribute("aria-expanded", open ? "true" : "false");
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    return { head, body };
  },

  /* 状态化折叠组（材料卡 ε/μ/极点分组；复用 .sec-head/.sec-body 视觉，
   * open 状态按 key 持久——rerender/加 pole 后不自动收起，2026-09） */
  _msec(key, title, collapsedDefault) {
    const stored = (this._msecOpen && key in this._msecOpen)
      ? this._msecOpen[key] : collapsedDefault;
    const open = !stored;
    const head = this._el("div", "sec-head", title);
    const body = this._el("div", "sec-body");
    if (!open) { head.classList.add("collapsed"); body.style.display = "none"; }
    head.setAttribute("role", "button");
    head.tabIndex = 0;
    head.setAttribute("aria-expanded", open ? "true" : "false");
    const toggle = () => {
      head.classList.toggle("collapsed");
      const now = !head.classList.contains("collapsed");
      body.style.display = now ? "" : "none";
      head.setAttribute("aria-expanded", now ? "true" : "false");
      this._msecOpen[key] = !now;
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    return { head, body };
  },

  /* 材料场形态胶囊行（放折叠组体内；切换 live 读 cfg，不吞编辑） */
  _matPillsRow(path, mode) {
    const row = this._el("div", "frow matmode-row");
    const pills = this._el("span", "mode-pills");
    for (const [m, t] of [["iso", "各向同性"], ["diag", "对角"], ["tensor", "张量"]]) {
      const b = this._el("button", "mode-pill" + (mode === m ? " on" : ""), t);
      b.type = "button";
      b.addEventListener("click", () => this._matMode(path, m));
      pills.appendChild(b);
    }
    row.appendChild(pills);
    return row;
  },

  /* 材料场形态胶囊（frow 内；切换 live 读 cfg） */
  _matPills(path, mode) {
    const pills = this._el("span", "mode-pills");
    for (const [m, t] of [["iso", "各向同性"], ["diag", "对角"], ["tensor", "张量"]]) {
      const b = this._el("button", "mode-pill" + (mode === m ? " on" : ""), t);
      b.type = "button";
      b.addEventListener("click", () => this._matMode(path, m));
      pills.appendChild(b);
    }
    return pills;
  },

  /* 材料场编辑器（2026-09）：短名与输入同排左对齐；形态胶囊在值区上方
   * 独立小行(右对齐, 紧凑)——不与高矩阵同 flex 行, 防纵向拉伸 */
  _matEditor(path, val, name) {
    const frag = document.createDocumentFragment();
    const mode = this._matShape(val);
    const pillRow = this._el("div", "frow mfield-pills");
    pillRow.appendChild(this._matPills(path, mode));
    frag.appendChild(pillRow);
    const row = this._el("div", "frow mfield-row");
    /* R17 a11y：材料场短名标签升级为真 label（与 flat 面板 .flabel 同型），
     * iso 值输入经 htmlFor 关联；网格/矢量格各自保留格内可访问名。 */
    const lb = this._el("label", "flabel");
    lb.textContent = name;
    row.appendChild(lb);
    if (mode === "iso") {
      const inpWrap = this._input(path,
        typeof val === "number" ? val : undefined, "num");
      /* P2-5 a11y：iso 值输入无可视 label（短名即身份）——补可访问名 */
      const inp = inpWrap.querySelector("input");
      if (inp) {
        if (!inp.getAttribute("aria-label")) inp.setAttribute("aria-label", name);
        if (!inp.id) inp.id = "fld-" + (++this._fldSeq);
        lb.htmlFor = inp.id;
      }
      row.appendChild(inpWrap);
    } else {
      row.appendChild(this._matGrid(path, val, mode));
    }
    frag.appendChild(row);
    return frag;
  },

  /* 字段序号（label for/id 配对用，2026-09-02 审查修复 a11y） */
  _fldSeq: 0,
  _msecOpen: {},      // 折叠组 open 状态（key 持久，rerender 不复位）
  _poleOpen: {},      // 单 pole 折叠状态（两级折叠）

  _row(label, ...rest) {
    const controls = rest.filter((c) => c && c.nodeType);
    const tip = rest.find((c) => typeof c === "string");
    const row = this._el("div", "frow");
    const lab = this._fillLabel(this._el("label", "flabel"), label);
    /* label 兜底（P1-9）：.flabel 110px 定宽 + ellipsis 截断后悬停必显全文。
     * 只改 attribute（title）赋值，零 innerHTML：有 tip（语义提示）时
     * 拼接展示 label 全称，无 tip 时 title 直接取 label。 */
    if (label) lab.title = tip && tip !== label ? tip + " · " + label : label;
    row.appendChild(lab);
    for (const c of controls) row.appendChild(c);
    /* 可访问名称：label for → 第一个控件；其余控件 aria-label（向量槽）。
     * 控件可能是裸 select/button，也可能包在 .inwrap span 里 */
    const focusables = [];
    for (const c of controls) {
      if (["SELECT", "BUTTON", "INPUT", "TEXTAREA"].includes(c.tagName))
        focusables.push(c);
      else if (c.querySelectorAll)
        focusables.push(...c.querySelectorAll("input, select, textarea"));
    }
    focusables.forEach((ctl, idx) => {
      if (!ctl.id) ctl.id = "fld-" + (++this._fldSeq);
      if (idx === 0) lab.htmlFor = ctl.id;
      else if (!ctl.getAttribute("aria-label"))
        ctl.setAttribute("aria-label", label + " " + (idx + 1));
    });
    return row;
  },

  _vec(label, path, arr, n, hintKind) {
    const parts = [];
    for (let i = 0; i < n; i++) {
      parts.push(this._input(path + "." + i, arr ? arr[i] : 0, "num", hintKind));
    }
    return this._row(label, ...parts);
  },

  /* 范围编辑器(2026-09 站长裁决)：一行一轴，显式 min/max 两槽。
   * data-path 保持 .0/.1——cfg 仍是 [lo, hi] 数组（校验/线格式不受扰） */
  _rangeRow(label, path, arr, hintKind) {
    const row = this._el("div", "frow");
    /* P2-5 a11y：此前 .flabel 是 span 且 min/max 两槽无可访问名——
     * 改真 label，min 槽经 htmlFor 关联组标签；两槽各设显式可访问名
     * （"x_range (µm) min" / "x_range (µm) max"），id/值/写回不变。 */
    const lb = this._el("label", "flabel");
    this._fillLabel(lb, label);
    row.appendChild(lb);
    const mk = (slot, tag) => {
      const g = this._el("span", "rng");
      const t = this._el("span", "rng-tag");
      t.textContent = tag;
      g.appendChild(t);
      const inpWrap = this._input(path + "." + slot,
        Array.isArray(arr) ? arr[slot] : undefined, "num", hintKind);
      const inp = inpWrap.querySelector("input");
      if (inp) {
        if (!inp.id) inp.id = "fld-" + (++this._fldSeq);
        inp.setAttribute("aria-label", label + " " + tag);
        if (slot === 0) lb.htmlFor = inp.id;   // 组标签关联 min 槽
      }
      g.appendChild(inpWrap);
      return g;
    };
    row.appendChild(mk(0, "min"));
    row.appendChild(mk(1, "max"));
    return row;
  },

  /* ═══ ε/μ 三形态编辑器（2026-09 提案：值形状即形态） ═══
   * iso=标量 | diag=3元[xx,yy,zz] | tensor=3×3(行优先,互易对称镜像)
   * 切形态按迁移规则重写 cfg 值；有损静默（2026-09 站长裁决：不提醒不记忆）。 */
  _matShape(v) {
    if (Array.isArray(v)) return Array.isArray(v[0]) ? "tensor" : "diag";
    return "iso";
  },

  /* ε/μ 形态切换（2026-09）：live 从 cfg 读当前值——不吞组内已编辑；
   * 静默切换（站长裁决：有损不提醒、不记忆）。 */
  _matMode(path, to) {
    const cur = getPath(this.cfg, path);
    const mode = this._matShape(cur);
    if (cur === undefined || mode === to) return;
    const num = (x) => (typeof x === "number" && isFinite(x) ? x : null);
    const flat = (v) => (Array.isArray(v) && !Array.isArray(v[0]) ? v : null);
    const mat = (v) => (Array.isArray(v) && Array.isArray(v[0]) ? v : null);
    const f = flat(cur), M = mat(cur);
    const sv = num(cur) ?? (f ? num(f[0]) : null) ?? 1;
    let next = null;
    if (to === "iso") {
      if (mode === "diag") next = f[0] ?? 1;
      else if (mode === "tensor") next = num(M[0][0]) ?? 1;
      else next = sv;
    } else if (to === "diag") {
      if (mode === "iso") next = [sv, sv, sv];
      else next = [num(M[0][0]) ?? 1, num(M[1][1]) ?? 1, num(M[2][2]) ?? 1];
    } else {  // tensor
      if (mode === "iso") next = [[sv, 0, 0], [0, sv, 0], [0, 0, sv]];
      else next = [[f[0] ?? 1, 0, 0], [0, f[1] ?? 1, 0], [0, 0, f[2] ?? 1]];
    }
    setPath(this.cfg, path, next);
    this._rerender();
  },

  _matGrid(path, val, mode) {
    const AX = ["x", "y", "z"];
    const wrap = this._el("div", "tgrid");
    const cap = this._el("span", "tgh");
    cap.textContent = "";
    wrap.appendChild(cap);
    for (const a of AX) {
      const h = this._el("span", "tgh");
      h.textContent = a;
      wrap.appendChild(h);
    }
    for (let i = 0; i < 3; i++) {
      const rh = this._el("span", "tgh");
      rh.textContent = AX[i];
      wrap.appendChild(rh);
      for (let j = 0; j < 3; j++) {
        if (mode === "diag" && i !== j) {
          const z = this._el("span", "tcell locked");
          z.textContent = "0";
          z.title = "对角模式：非对角恒 0（切「张量」可编辑）";
          wrap.appendChild(z);
          continue;
        }
        const cell = this._el("span", "tcell");
        let v;
        if (mode === "diag") v = Array.isArray(val) ? val[i] : undefined;
        else v = (Array.isArray(val) && val[i]) ? val[i][j] : undefined;
        const p = mode === "diag" ? path + "." + i : path + "." + i + "." + j;
        const inp = this._input(p, v, "num");
        /* P2-5 a11y：矩阵格输入无可视 label——按路径末段+行/列补可访问名 */
        const cellInp = inp.querySelector("input");
        if (cellInp && !cellInp.getAttribute("aria-label"))
          cellInp.setAttribute("aria-label",
            (path.split(".").pop() || "值") + " " + AX[i] + " " + AX[j]);
        if (mode === "tensor" && i !== j) {
          const q = path + "." + j + "." + i;
          const me = inp.querySelector("input");
          me.addEventListener("input", () => {
            /* F1 A①：镜像格走同一写回换算（不再 parseFloat 部分解析——
             * 表达式/单位后缀按原样字符串镜像，纯数照常换算）。 */
            const t = writeBackValue(me.value, "num", null);
            setPath(this.cfg, q, t);                 // 互易对称自动镜像
            const twin = document.querySelector('[data-path="' + q + '"]');
            if (twin) twin.value = me.value;         // 同屏镜像格即时回显
            this._notifyMutate();
          });
          inp.title = "互易介质：输一侧自动镜像对称格";
        }
        cell.appendChild(inp);
        wrap.appendChild(cell);
      }
    }
    return wrap;
  },

  _writeBack(input) {
    /* null-path 控件（组视图/卡头类型下拉等纯 UI 选择器）无写回目标：
     * 防御短路，杜绝 setPath(cfg,"null",v) 污染顶层配置 */
    if (!input.dataset.path) return;
    const raw = input.value.trim();
    /* 三态布尔：— 删除 / true|false 写 JSON 布尔 */
    if (input.dataset.kind === "bool") {
      if (raw === "") deletePath(this.cfg, input.dataset.path);
      else setPath(this.cfg, input.dataset.path, raw === "true");
      this._notifyMutate();
      return;
    }
    /* 「空 = 不设置」的可选枚举（如 from_face）选回空时删除字段，
     * 而不是写出 ""（schema 是 Optional[Literal]，空串必报错） */
    if (raw === "" && input.dataset.emptyDeletes) {
      deletePath(this.cfg, input.dataset.path);
      this._notifyMutate();
      return;
    }
    const v = writeBackValue(raw, input.dataset.kind, input.dataset.unit || null);
    setPath(this.cfg, input.dataset.path, v);
    this._notifyMutate();
  },

  _card(headChildren, bodyEl) {
    const card = this._el("div", "card");
    const head = this._el("div", "card-head");
    for (const c of headChildren) head.appendChild(c);
    card.appendChild(head);
    const body = this._el("div", "card-body");
    body.appendChild(bodyEl);
    card.appendChild(body);
    return card;
  },

  /* 卡头身份名（2026 统一语言）：四个对象族（geometry/source/monitor/materials）
   * 共用同一可编辑 name 输入，写回 cfg.<list>.<i>.name；复用 `_input` 的
   * 写回/onChange 链（→ 树/F2/3D label 同步）。placeholder 兜底未命名
   * （沿用原 card-title 的 "type #n" 回退），空名不写死到 cfg。 */
  _headName(path, value, placeholder, ariaLabel) {
    const nw = this._input(path, value || "", "str");
    const inp = nw.querySelector("input");
    inp.classList.add("card-name");
    inp.placeholder = placeholder || "name";
    inp.setAttribute("aria-label", ariaLabel || (value || "name") + " 名称");
    nw.classList.add("card-name-wrap");
    return nw;
  },

  /* 渲染风卡头缩略图（previews.js R2 统一）：geometry/source/monitor 共用，
   * 包一层 .obj-preview span；svg 自身 aria-hidden（语义由卡头文本承担）。 */
  _preview(type) {
    const pv = this._el("span", "obj-preview");
    pv.appendChild(previewEl(type));
    return pv;
  },

  _delBtn(listPath, index, label) {
    const b = this._el("button", "mini danger", label || "删除");
    b.addEventListener("click", (e) => {
      if (typeof uiConfirm === "function" && !uiConfirm("确定删除此项？（不可恢复）", e)) return;
      const list = getPath(this.cfg, listPath);
      if (Array.isArray(list)) {
        list.splice(index, 1);
        this._rerender();
      }
    });
    return b;
  },

  _matNames() {
    return (this.cfg.materials || []).map((m) => m.name).filter(Boolean);
  },

  /* ══════════════════ 各节（渲染进传入的 body 容器） ══════════════════ */

  _domain(body) {
    const d = this.cfg.domain || (this.cfg.domain = {});
    /* 段 1: 范围 */
    const s1 = this._sec("范围");
    body.appendChild(s1.head); body.appendChild(s1.body);
    s1.body.appendChild(this._rangeRow("x_range (µm)", "domain.x_range", d.x_range, "len"));
    s1.body.appendChild(this._rangeRow("y_range (µm)", "domain.y_range", d.y_range, "len"));
    s1.body.appendChild(this._rangeRow("z_range (µm)", "domain.z_range", d.z_range, "len"));
    /* 段 2: 求解（时间/CFL）L4 渐进披露：高级参数默认折叠 */
    const s2 = this._sec("求解 · 时间/CFL", true);
    body.appendChild(s2.head); body.appendChild(s2.body);
    s2.body.appendChild(this._row("total_time (fs)",
      this._input("domain.total_time", d.total_time, "num", "time"),
      "决定频域积分窗：谱分辨率 ≈ 1/total_time；跑的步数超出它则超出部分零贡献——设够（陷阱#7）"));
    s2.body.appendChild(this._row("cfl", this._input("domain.cfl", d.cfl ?? 0.9, "num")));

    const bt = this._el("div", "btable");
    d.boundary = d.boundary || {};
    for (const f of ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]) {
      const b = d.boundary[f] || {};
      const line = this._el("div", "brow");
      line.appendChild(this._el("span", "bface", f));
      const kindSel = this._select("domain.boundary." + f + ".kind", b.kind || "pec",
        ["pec", "pmc", "periodic", "pml", "bloch"]);
      /* P2-5 a11y：bface 是 span 非 label——kind 下拉补可访问名 */
      kindSel.setAttribute("aria-label", f + " 边界类型");
      kindSel.setAttribute("title", f + " 边界类型（pec/pmc/periodic/pml/bloch）");
      // kind 切换 → 重渲染该节（清理切走 kind 的专属残留参数）
      kindSel.addEventListener("change", () => {
        const face = d.boundary[f] || {};
        const nk = face.kind || kindSel.value;
        if (nk !== "pml") { delete face.layers; delete face.kappa_max; delete face.order; }
        if (nk !== "bloch") { delete face.k; delete face.from_source; delete face.n_background; }
        this._rerender();
      });
      line.appendChild(kindSel);
      bt.appendChild(line);
      /* 2026-09 布局修复：kind 专属参数改到独立整行（.frow·label.flabel 两栏）
       * 下挂当前面——不再挤进 .brow 单行造成 k/from_src/n_bg 相互遮挡。
       * .bparams 提供轻缩进分组，参数标签落在 110px 轨道、值落 130px 列。 */
      const params = this._el("div", "bparams");
      if ((b.kind || "pec") === "pml") {
        const w = (lbl, key, val) => {
          const inp = this._input("domain.boundary." + f + "." + key, val, "num");
          inp.querySelector("input").setAttribute("aria-label", f + " pml " + lbl);
          return this._row(lbl, inp);
        };
        params.appendChild(w("layers", "layers", b.layers ?? 12));
        params.appendChild(w("κ (kappa_max)", "kappa_max", b.kappa_max ?? 1));
        params.appendChild(w("order", "order", b.order ?? 3));
      } else if (b.kind === "bloch") {
        params.appendChild(this._vec("k 波矢 (rad/m)", "domain.boundary." + f + ".k", b.k, 3));
        params.appendChild(this._row("跟随主源 (from_src)",
          this._boolSelect("domain.boundary." + f + ".from_source",
            b.from_source === true, "跟随主源 (from_src)")));
        const nb = this._input("domain.boundary." + f + ".n_background",
          b.n_background, "num");
        nb.querySelector("input").setAttribute("aria-label", f + " bloch n_background");
        params.appendChild(this._row("n_bg (n_background)", nb));
      }
      if (params.childNodes.length) bt.appendChild(params);
    }
    /* 段 3: 边界（六面边界类型）L4 渐进披露：默认折叠——6 面默认 pec 即可，高级再展开 */
    const s3 = this._sec("边界 · 六面", true);
    body.appendChild(s3.head); body.appendChild(s3.body);
    s3.body.appendChild(bt);
    s3.body.appendChild(this._el("div", "note",
      "pml: layers/κ/order；bloch: k 波矢(rad/m) 或 from_src（从主源取 k）+ n_bg。"));
  },

  _mesh(body) {
    const sM = this._sec("格距");
    body.appendChild(sM.head); body.appendChild(sM.body);
    // 网格预览工具（app.js 把常驻 DOM 挂进 mount，监听不丢）
    const mount = this._el("div");
    mount.id = "mesh-mount";
    body.appendChild(mount);

    const m = this.cfg.mesh || (this.cfg.mesh = {});
    sM.body.appendChild(this._row("dx (µm)", this._input("mesh.dx", m.dx, "num", "len")));
    sM.body.appendChild(this._row("dy (µm)", this._input("mesh.dy", m.dy, "num", "len")));
    sM.body.appendChild(this._row("dz (µm)", this._input("mesh.dz", m.dz, "num", "len")));
    sM.body.appendChild(this._row("max_refinement",
      this._input("mesh.max_refinement", m.max_refinement ?? 128, "num")));
    sM.body.appendChild(this._row("min_cells",
      this._input("mesh.min_cells", m.min_cells ?? 10, "num")));
    m.refine = m.refine || [];
    m.refine.forEach((r, i) => {
      const inner = document.createDocumentFragment();
      inner.appendChild(this._rangeRow("x_range (µm)", `mesh.refine.${i}.x_range`, r.x_range, "len"));
      inner.appendChild(this._rangeRow("y_range (µm)", `mesh.refine.${i}.y_range`, r.y_range, "len"));
      inner.appendChild(this._rangeRow("z_range (µm)", `mesh.refine.${i}.z_range`, r.z_range, "len"));
      inner.appendChild(this._row("factor",
        undefined,
        "⚠ 名义 factor ≠ 实际最细格（growth_rate 增长可达 26×）——以网格预览的实际格距为准（陷阱#4）",
        this._input(`mesh.refine.${i}.factor`, r.factor, "num")));
      inner.appendChild(this._row("growth_rate",
        this._input(`mesh.refine.${i}.growth_rate`, r.growth_rate ?? 1.2, "num")));
      body.appendChild(this._card(
        [this._el("span", "tag", "refine " + (i + 1)), this._delBtn("mesh.refine", i)],
        inner));
    });
    const add = this._el("button", "mini", "+ refine 区");
    add.addEventListener("click", () => {
      m.refine.push({ x_range: [-2e-7, 2e-7], y_range: [-2e-7, 2e-7],
                      z_range: [-2e-7, 2e-7], factor: 2, growth_rate: 1.2 });
      this._rerender();
    });
    body.appendChild(add);
  },

  /* 材料「显示色」行（2026-10-21）：wire 字段 color 的 GUI 入口。
   * 只影响 3D 视图材质色；未设置时显示"自动"取到的调色板色并可一键回收
   * 到自动配色（删字段 → 前端散列）。颜色改动走 onFormMutate 同一收口：
   * 3D 重绘 + 树刷新 + 校验 + 会话推送。 */
  _colorRow(mt) {
    const row = this._el("div", "frow color-row");
    row.appendChild(this._el("label", "flabel", "显示色"));
    const field = this._el("span", "color-field");
    const autoHex = "#" + (typeof matColor === "function" ? matColor(mt.name) : 0x8E9BB4)
      .toString(16).padStart(6, "0");
    const norm = (v) => (typeof v === "string" && /^#?[0-9a-fA-F]{6}$/.test(v.trim())
      ? "#" + v.trim().replace(/^#/, "") : null);
    const isAuto = !norm(mt.color);
    const cur = norm(mt.color) || autoHex;
    const inp = document.createElement("input");
    inp.type = "color";
    inp.className = "color-inp";
    inp.value = cur.toLowerCase();
    inp.setAttribute("aria-label", "材料显示色");
    inp.title = "3D 视图里这个材料的颜色";
    const hex = this._el("span", "color-hex", cur.toUpperCase());
    const autoBtn = this._el("button", "mini color-auto" + (isAuto ? " on" : ""), "自动");
    autoBtn.type = "button";
    autoBtn.title = "回到按材料名自动配色";
    inp.addEventListener("input", () => {
      hex.textContent = inp.value.toUpperCase();
      autoBtn.classList.remove("on");
    });
    inp.addEventListener("change", () => {
      mt.color = inp.value.toUpperCase();
      hex.textContent = mt.color;
      autoBtn.classList.remove("on");
      onFormMutate();
    });
    autoBtn.addEventListener("click", () => {
      delete mt.color;
      inp.value = autoHex.toLowerCase();
      hex.textContent = autoHex.toUpperCase();
      autoBtn.classList.add("on");
      onFormMutate();
    });
    field.appendChild(inp);
    field.appendChild(hex);
    field.appendChild(autoBtn);
    row.appendChild(field);
    return row;
  },

  _materials(body) {
    const list = this.cfg.materials || (this.cfg.materials = []);
    if (!list.length) {
      body.appendChild(this._paneEmpty("materials", "还没有材料",
        "从工具条 MATERIALS 添加，或在脚本里 f.mat.add_const()"));
      return;
    }
    this._items(list).forEach(([mt, i]) => {
      const inner = document.createDocumentFragment();
      /* 卡头 = 材料身份（v3）：名字输入 + 静态类型 chip */
      /* 卡头（统一语言）：可编辑身份名 + 类型 chip + 删除钮（四对象族同构） */
      const headChildren = [];
      headChildren.push(this._preview(
        "mat_" + mt.type));  /* 分型缩略图（R6；from_library 面已退役 R38） */
      headChildren.push(this._headName(`materials.${i}.name`, mt.name, "material name",
        (mt.name || "材料 " + (i + 1)) + " 名称"));
      const chip = this._el("span", "tag type",
        mt.type === "lorentz" ? "dispersive" : "const");
      chip.title = "材料类型（由工具栏按钮 / 代码决定）";
      headChildren.push(chip);
      headChildren.push(this._delBtn("materials", i));
      /* 显示色（2026-10-21）：材料节点上的 color 字段——只影响 3D 视图的
       * 材质色，与脚本 `.color('#RRGGBB')` 同一字段（不是 GUI 私有态）。 */
      inner.appendChild(this._colorRow(mt));
      if (mt.type === "const") {
        const bE = this._msec("mat" + i + ":eps", "ε · 介电常数", false);
        inner.appendChild(bE.head); inner.appendChild(bE.body);
        bE.body.appendChild(this._matEditor(`materials.${i}.eps`, mt.eps ?? 1, "ε"));

        const bM = this._msec("mat" + i + ":mu", "μ · 磁导率", true);
        inner.appendChild(bM.head); inner.appendChild(bM.body);
        bM.body.appendChild(this._matEditor(`materials.${i}.mu`, mt.mu ?? 1, "μ"));
      } else if (mt.type === "lorentz") {
        mt.poles = mt.poles || [];
        mt.mu_poles = mt.mu_poles || [];

        const sc = (v) => (typeof v === "number" && isFinite(v) ? v : null);
        const isArr = (v) => Array.isArray(v) && v.length === 3;

        /* 单 pole 渲染（含两级折叠：可收起整卡） */
        const poleItem = (listKey, pl, j) => {
          const P = `materials.${i}.${listKey}.${j}`;
          const isR1 = isArr(pl.alpha);
          const isLegacy = !isR1 && (isArr(pl.omega_0) || isArr(pl.omega_p));
          const shape = isR1 ? "r1" : (isLegacy ? "legacy" : "iso");
          const row = document.createDocumentFragment();

          if (!isLegacy) {
            const sr = this._el("div", "frow");
            sr.appendChild(this._el("label", "flabel", "形态"));
            const pills = this._el("span", "mode-pills");
            for (const [m, t] of [["iso", "各向同性"], ["r1", "rank-1"]]) {
              const bb = this._el("button", "mode-pill" + (shape === m ? " on" : ""), t);
              bb.type = "button";
              bb.addEventListener("click", () => {
                const cur = mt[listKey][j];
                const pick = sc(cur.omega_0) ?? sc(cur.omega_p) ?? 1e15;
                const g = sc(cur.gamma) ?? 1e13;
                const d = sc(cur.delta_eps) ?? 1;
                const next = m === "r1"
                  ? (cur.kind === "drude"
                      ? { kind: cur.kind, omega_p: pick, gamma: g,
                          alpha: [0, 0, 1], beta: [0, 0, 1] }
                      : { kind: cur.kind, omega_0: pick, gamma: g,
                          delta_eps: d, alpha: [0, 0, 1], beta: [0, 0, 1] })
                  : (cur.kind === "drude"
                      ? { kind: cur.kind, omega_p: pick, gamma: g }
                      : { kind: cur.kind, omega_0: pick, gamma: g,
                          delta_eps: d });
                mt[listKey][j] = next;
                this._rerender();
              });
              pills.appendChild(bb);
            }
            sr.appendChild(pills);
            row.appendChild(sr);
          }

          const kindSel = this._select(P + ".kind", pl.kind, ["lorentz", "drude"]);
          kindSel.addEventListener("change", () => {
            const keep = isR1
              ? { alpha: pl.alpha.slice(), beta: (pl.beta ? pl.beta.slice() : pl.alpha.slice()) }
              : {};
            mt[listKey][j] = kindSel.value === "drude"
              ? Object.assign({ kind: "drude", omega_p: 1e16,
                                gamma: pl.gamma ?? 1e13 }, keep)
              : Object.assign({ kind: "lorentz", omega_0: 1e15,
                                gamma: pl.gamma ?? 1e13,
                                delta_eps: pl.delta_eps ?? 1 }, keep);
            this._rerender();
          });
          row.appendChild(this._row("kind", kindSel));

          const pk = isLegacy ? "num3" : "num";
          if (pl.kind === "drude") {
            row.appendChild(this._row("ω_p",
              this._input(P + ".omega_p", pl.omega_p, pk, "ang")));
          } else {
            row.appendChild(this._row("ω_0",
              this._input(P + ".omega_0", pl.omega_0, pk, "ang")));
            row.appendChild(this._row("Δε",
              this._input(P + ".delta_eps", pl.delta_eps ?? 1, pk)));
          }
          row.appendChild(this._row("γ",
            this._input(P + ".gamma", pl.gamma, pk, "ang")));

          if (shape === "r1") {
            row.appendChild(this._vec("α (x,y,z)", P + ".alpha",
              pl.alpha || [0, 0, 1], 3));
            row.appendChild(this._vec("β (x,y,z)", P + ".beta",
              pl.beta || pl.alpha || [0, 0, 1], 3));
            row.appendChild(this._el("div", "note",
              "rank-1 = VectorPole(α,β)：β 缺省=α（互易单轴）；α≠β 为非对称耦合，" +
              "引擎按 α·F / β·P 原样消费；分量自动归一。"));
          } else if (shape === "legacy") {
            row.appendChild(this._el("div", "note",
              "旧「三轴」速记（3 元参数，内核 1.4.0 已移除）。拆成多个" +
              "各向同性/rank-1 pole 自行叠加："));
            const split = this._el("button", "mini");
            /* ▸ 为装饰性前缀（P2-3）：语义由文字「拆成…」承载 → aria-hidden 包裹 */
            const sg = this._el("span");
            sg.setAttribute("aria-hidden", "true");
            sg.textContent = "▸ ";
            split.appendChild(sg);
            split.appendChild(document.createTextNode("拆成三个各向同性 pole"));
            split.addEventListener("click", () => {
              const arr = (pl.omega_0 || pl.omega_p);
              const gA = isArr(pl.gamma) ? pl.gamma
                : (sc(pl.gamma) != null ? [pl.gamma, pl.gamma, pl.gamma]
                    : [1e13, 1e13, 1e13]);
              const dA = isArr(pl.delta_eps) ? pl.delta_eps
                : (sc(pl.delta_eps) != null
                    ? [pl.delta_eps, pl.delta_eps, pl.delta_eps] : [1, 1, 1]);
              const out = [];
              for (let a = 0; a < 3; a++) {
                const po = { kind: pl.kind, gamma: gA[a] };
                if (pl.kind === "drude") po.omega_p = arr[a];
                else { po.omega_0 = arr[a]; po.delta_eps = dA[a]; }
                out.push(po);
              }
              mt[listKey].splice(j, 1, ...out);
              this._rerender();
            });
            row.appendChild(split);
          }

          /* 审美打磨(2026-09)：pole 平铺项（不再卡中卡） */
          const item = this._el("div", "poleitem");
          const ihead = this._el("div", "pi-head");
          const pchev = this._el("span", "pchev", "▾");
          pchev.setAttribute("aria-hidden", "true");   /* 装饰性折叠箭头（P2-3）：pi-head 无 widget 语义，字形不承载名称 */
          ihead.appendChild(pchev);
          ihead.appendChild(this._el("span", "tag",
            (listKey === "mu_poles" ? "μ" : "ε") + " pole " + (j + 1)));
          ihead.appendChild(this._el("span", "tag tagdim", pl.kind + " · " + shape));
          ihead.appendChild(this._delBtn(`materials.${i}.${listKey}`, j));
          const ibody = this._el("div", "pi-body");
          ibody.appendChild(row);
          item.appendChild(ihead); item.appendChild(ibody);
          const pkey = "mat" + i + ":" + listKey + ":" + j;
          const folded = !!(this._poleOpen && this._poleOpen[pkey]);
          if (folded) { ibody.style.display = "none"; ihead.querySelector(".pchev").classList.add("cl"); }
          ihead.addEventListener("click", (ev) => {
            if (ev.target.closest("button")) return;
            const now = ibody.style.display === "none";
            ibody.style.display = now ? "" : "none";
            ihead.querySelector(".pchev").classList.toggle("cl", !now);
            this._poleOpen[pkey] = !now;
          });
          return item;
        };

        const addPoleBtn = (listKey) => {
          const b = this._el("button", "mini",
            "+ " + (listKey === "poles" ? "ε pole" : "μ pole"));
          b.addEventListener("click", () => {
            mt[listKey].push({ kind: "lorentz", omega_0: 1e15,
                               gamma: 1e13, delta_eps: 1 });
            this._rerender();
          });
          return b;
        };

        /* G20：色散材料卡导出（逃生口，非库正典——库存 nk 数据，
         * pole 是按波段现算的暂态产物；文件名带波段/极点数可当溯源）。 */
        if (mt.type === "lorentz" && mt.name) {
          const dl = document.createElement("button");
          dl.className = "mini";
          dl.textContent = "导出 .material.json";
          dl.title = "下载本材料节点（裸 wire JSON，可直接粘回 materials）";
          dl.addEventListener("click", () => {
            const blob = new Blob([JSON.stringify(mt, null, 2)],
                                  { type: "application/json" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = (mt.name || "material") + ".material.json";
            a.click();
            URL.revokeObjectURL(a.href);
          });
          inner.appendChild(dl);
        }

        /* 两级折叠·一级：ε 整块（ε_inf + ε 极点）常开；μ 整块默认收起 */
        const bE = this._msec("mat" + i + ":eps", "ε · 介电常数（含色散）", false);
        if (mt.poles.length) bE.head.appendChild(this._el("span", "sec-badge", String(mt.poles.length)));
        inner.appendChild(bE.head); inner.appendChild(bE.body);
        bE.body.appendChild(this._matEditor(`materials.${i}.eps_inf`, mt.eps_inf ?? 1, "ε∞"));
        const peList = this._el("div", "pole-list");
        mt.poles.forEach((pl, j) => peList.appendChild(poleItem("poles", pl, j)));
        bE.body.appendChild(peList);
        if (!mt.poles.length) bE.body.appendChild(this._el("div", "note",
          "暂无 ε 极点 —— 独立参数的多个 pole 请自行叠加（各向同性 / rank-1）"));
        bE.body.appendChild(addPoleBtn("poles"));

        const bM = this._msec("mat" + i + ":mu", "μ · 磁导率（含色散）", true);
        if (mt.mu_poles.length) bM.head.appendChild(this._el("span", "sec-badge", String(mt.mu_poles.length)));
        inner.appendChild(bM.head); inner.appendChild(bM.body);
        bM.body.appendChild(this._matEditor(`materials.${i}.mu_inf`, mt.mu_inf ?? 1, "μ∞"));
        const pmList = this._el("div", "pole-list");
        mt.mu_poles.forEach((pl, j) => pmList.appendChild(poleItem("mu_poles", pl, j)));
        bM.body.appendChild(pmList);
        if (!mt.mu_poles.length) bM.body.appendChild(this._el("div", "note",
          "暂无 μ 极点 —— 需要时添加（μ 默认折叠，多数材料无需设置）"));
        bM.body.appendChild(addPoleBtn("mu_poles"));
      }
      /* from_library 编辑分支已删（G20 2026-10-16）：内核拒收该节点
       * （类别错误——nk 数据引用不属 cfg），库材料走工具条拟合对话框。 */
      body.appendChild(this._card(headChildren, inner));
    });
    // 组级 + 材料 已删（2026-09 站长反馈：工具栏 Material 是唯一新增入口）
  },

  _geometry(body) {
    const list = this.cfg.geometry || (this.cfg.geometry = []);
    if (!list.length) {
      body.appendChild(this._paneEmpty("structure", "还没有几何体",
        "从工具条 STRUCTURES 选一个形状添加，或在脚本里 f.geom.add_box()"));
      return;
    }
    this._items(list).forEach(([g, i]) => {
      const inner = document.createDocumentFragment();
      /* 命名单一位置 = 卡头（可编辑名输入），此处不再重复 body name 行；
       * 未命名时树/3D label 与卡头同回退 type #n（见卡头 _headName placeholder） */
      /* material 字段改为下拉(已有材料+可选, 08-31cb) */
      const matSel = document.createElement("select");
      matSel.dataset.path = `geometry.${i}.material`;
      this._tagField(matSel, matSel.dataset.path);
      matSel.dataset.kind = "str";
      matSel.style.cssText = this._select(null, null, []).style.cssText;
      const matNames = this._matNames();
      const curMat = g.material || "";
      const defOpt = document.createElement("option");
      defOpt.value = ""; defOpt.textContent = "（选择材料）";
      matSel.appendChild(defOpt);
      for (const mn of matNames) {
        const o = document.createElement("option");
        o.value = mn; o.textContent = mn;
        if (mn === curMat) o.selected = true;
        matSel.appendChild(o);
      }
      if (curMat && !matNames.includes(curMat)) {
        const o = document.createElement("option");
        o.value = curMat; o.textContent = curMat + " (未定义)";
        o.selected = true;
        matSel.appendChild(o);
      }
      matSel.addEventListener("change", () => {
        g.material = matSel.value || undefined;
        this._notifyMutate();
      });
      /* 材料节（物理因果 RoundE）：几何身份=材料（名在卡头）；空间量入
       * 「位置与尺寸」，优先级（mesh 覆盖序）独立成节，与其余对象族同构。 */
      const sGeo1 = this._sec("材料", false);
      inner.appendChild(sGeo1.head); inner.appendChild(sGeo1.body);
      sGeo1.body.appendChild(this._row("material", matSel));

      /* 位置与尺寸：折叠默认收起 + 计数 chip（与源/监视器/域同款节律） */
      const posCount = g.type === "box" ? 3
        : g.type === "polygon_z" ? 2
        : g.type === "polygon_swept" ? 3
        : g.type === "sphere" ? 2
        : g.type === "cylinder" ? 3
        : g.type === "ngon_z" ? 4
        : g.type === "ellipse_z" ? 4
        : g.type === "ring_z" ? 4
        : 1;
      const sGeo2 = this._sec("位置与尺寸", true, posCount);
      inner.appendChild(sGeo2.head); inner.appendChild(sGeo2.body);
      if (g.type === "box") {
        sGeo2.body.appendChild(this._rangeRow("x_range (µm)", `geometry.${i}.x_range`, g.x_range, "len"));
        sGeo2.body.appendChild(this._rangeRow("y_range (µm)", `geometry.${i}.y_range`, g.y_range, "len"));
        sGeo2.body.appendChild(this._rangeRow("z_range (µm)", `geometry.${i}.z_range`, g.z_range, "len"));
      } else if (g.type === "polygon_z" || g.type === "polygon_swept") {
        /* 顶点编辑器：逐行 "x, y"（µm）；哨兵行写 "nan, nan" 或空行 */
        const ta = document.createElement("textarea");
        ta.className = "verts-editor";
        ta.id = `f-geometry-${i}-vertices`;
        ta.name = ta.id;
        ta.setAttribute("autocomplete", "off");
        ta.spellcheck = false;
        ta.rows = Math.min(Math.max((g.vertices || []).length + 1, 4), 12);
        ta.value = (g.vertices || [])
          .map(p => (p[0] !== p[0] && p[1] !== p[1]) ? "nan, nan"
            : `${fmtUserNum(p[0] * 1e6)}, ${fmtUserNum(p[1] * 1e6)}`).join("\n");
        ta.addEventListener("input", () => {
          const verts = [];
          for (const ln of ta.value.split("\n")) {
            const t = ln.trim();
            if (!t) continue;                       // 空行忽略
            if (/^nan\s*,\s*nan$/i.test(t)) {       // 哨兵 → [null, null]（线格式）
              verts.push([null, null]);
              continue;
            }
            const parts = t.split(/[,\s]+/).filter(Boolean);
            const x = parseFloat(parts[0]), y = parseFloat(parts[1]);
            if (!isFinite(x) || !isFinite(y)) continue;  // 未完成行不打扰
            verts.push([x * 1e-6, y * 1e-6]);            // µm → m
          }
          if (verts.length >= 3) {
            g.vertices = verts;
            this._notifyMutate();
          }
        });
        sGeo2.body.appendChild(this._row("vertices (µm)", ta));
        sGeo2.body.appendChild(this._rangeRow("z_range (µm)", `geometry.${i}.z_range`, g.z_range, "len"));
        if (g.type === "polygon_swept") {
          sGeo2.body.appendChild(this._vec("tilt (dx/dz, dy/dz)",
            `geometry.${i}.tilt`, g.tilt ?? [0, 0], 2, ""));
        }
      } else {
        sGeo2.body.appendChild(this._vec("center (µm)", `geometry.${i}.center`, g.center, 3, "len"));
        if (g.type === "ngon_z") {
          sGeo2.body.appendChild(this._row("n_sides",
            this._input(`geometry.${i}.n_sides`, g.n_sides ?? 6, "num")));
          sGeo2.body.appendChild(this._row("circumradius (µm)",
            this._input(`geometry.${i}.circumradius`, g.circumradius, "num", "len")));
        } else if (g.type === "sphere" || g.type === "cylinder") {
          /* M6 盘点抓到的真缺口：cylinder 的 radius 从来没人渲染过——
           * 从工具条加一根柱（radius=1e-7）之后，界面上改不了它的半径，
           * 只能去写脚本。两个类型在线上格式里都叫 radius。 */
          sGeo2.body.appendChild(this._row("radius (µm)",
            this._input(`geometry.${i}.radius`, g.radius, "num", "len")));
        } else if (g.type === "ellipse_z") {
          sGeo2.body.appendChild(this._row("rx (µm)",
            this._input(`geometry.${i}.rx`, g.rx, "num", "len")));
          sGeo2.body.appendChild(this._row("ry (µm)",
            this._input(`geometry.${i}.ry`, g.ry, "num", "len")));
        } else if (g.type === "ring_z") {
          sGeo2.body.appendChild(this._row("inner_radius (µm)",
            this._input(`geometry.${i}.inner_radius`, g.inner_radius ?? 0, "num", "len")));
          sGeo2.body.appendChild(this._row("outer_radius (µm)",
            this._input(`geometry.${i}.outer_radius`, g.outer_radius, "num", "len")));
        }
        if (g.type === "cylinder" || g.type === "ngon_z"
            || g.type === "ellipse_z" || g.type === "ring_z") {
          sGeo2.body.appendChild(this._row("height (µm)",
            this._input(`geometry.${i}.height`, g.height, "num", "len")));
        }
      }
      /* 优先级 · 网格（物理因果 RoundE）：mesh 覆盖序属求解参数，非空间量 */
      const sGeo3 = this._sec("优先级 · 网格", true, 1);
      inner.appendChild(sGeo3.head); inner.appendChild(sGeo3.body);
      sGeo3.body.appendChild(this._row("priority",
        this._input(`geometry.${i}.priority`, g.priority ?? 0, "num")));
      /* 卡片头（统一语言 2026）：类型下拉彻底移除——类型只在 topbar
       * 「＋几何」添加时定死，改类型 = 删除重建。
       * 2026-10-21 用户定案「信息够用就好」：去掉类型 chip——左侧渲染缩略图
       * 已经说明形状、名字占位符也带类型（"N-gon #1"），chip 是第三遍重复。
       * 头 = 缩略图 + 可编辑名 + 材料 chip（无材料时不渲染）+ 删除钮。 */
      const gHead = [];
      /* 渲染风缩略图（previews.js）：卡头左侧 44×44 预览（R1；R2 走 _preview 统一） */
      gHead.push(this._preview(g.type));
      gHead.push(this._headName(`geometry.${i}.name`, g.name,
        `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`, (g.name || `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`) + " 名称"));
      if (g.material) gHead.push(this._el("span", "tag", g.material));
      gHead.push(this._delBtn("geometry", i));
      body.appendChild(this._card(gHead, inner));
    });
  },

  _source(body) {
    const list = this.cfg.source || (this.cfg.source = []);
    if (!list.length) {
      body.appendChild(this._paneEmpty("soft_source", "还没有源",
        "到 FDTD 页的 SOURCES 添加，或在脚本里 f.src.add_plane_wave()"));
      return;
    }
    const FIELDS = {
      soft_source: ["name", "freq", "bandwidth", "center", "size", "polarization"],
      plane_wave: ["name", "freq", "bandwidth", "direction", "theta", "phi", "psi", "from_face"],
      gaussian_beam: ["name", "freq", "bandwidth", "direction", "theta", "phi", "psi",
                      "from_face", "radius"],
    };
    this._items(list).forEach(([s, i]) => {
      const inner = document.createDocumentFragment();
      /* 命名单一位置 = 卡头（可编辑名输入，见 _headName） */
      /* 波形与频谱节（物理因果 RoundE）：源的光谱/时序/幅度——freq/带宽/delay/
       * phase/amplitude 收进首节；传播方向与注入位置各自成节，源面板物理因果化。 */
      const sSrc1 = this._sec("波形与频谱", false);
      inner.appendChild(sSrc1.head); inner.appendChild(sSrc1.body);
      sSrc1.body.appendChild(this._row("freq (THz)",
        this._input(`source.${i}.freq`, s.freq, "num", "freq")));
      sSrc1.body.appendChild(this._row("bandwidth (THz)",
        this._input(`source.${i}.bandwidth`, s.bandwidth, "num", "freq")));
      /* 公共时序/幅度（两入口都有） */
      sSrc1.body.appendChild(this._row("delay (fs)",
        this._input(`source.${i}.delay`, s.delay ?? 0, "num", "time")));
      sSrc1.body.appendChild(this._row("phase (rad)",
        this._input(`source.${i}.phase`, s.phase ?? 0, "num")));
      sSrc1.body.appendChild(this._row("amplitude",
        this._input(`source.${i}.amplitude`, s.amplitude ?? 1, "num")));
      if (FIELDS[s.type] && FIELDS[s.type].includes("center")) {
        /* 复偏振：实部/虚部各一组，直接合并写回 [[re,im]×3]（线格式） */
        const pol = (s.polarization && Array.isArray(s.polarization[0]))
          ? s.polarization.map(p => [p[0] || 0, p[1] || 0])
          : [[1, 0], [0, 0], [0, 0]];
        const polRow = (label, which, tip) => {
          const row = this._el("div", "frow");
          const ld = this._el("label", "flabel", label);
          /* F3 修复段 4（陷阱#1）：本分支的提示此前缺失——Ey 提醒只写在
           * plane_wave/gaussian_beam 分支（下方 theta/phi/psi 行）。tip 走
           * 既有体例（label title，同 _row 的 tip 落点），可见文案另见紧随
           * 两行后的 .note。 */
          if (tip) ld.title = tip;
          row.appendChild(ld);
          const wrap = this._el("div", "inwrap");
          for (let k = 0; k < 3; k++) {
            const inp = document.createElement("input");
            inp.type = "text";
            inp.inputMode = "decimal";
            inp.value = String(pol[k][which]);
            inp.id = `f-source-polarization-${which}-${k}`;
            inp.name = inp.id;
            inp.setAttribute("autocomplete", "off");
            inp.setAttribute("aria-label", label + " " + ["x", "y", "z"][k]);
            inp.addEventListener("input", () => {
              const v = parseFloat(inp.value);
              pol[k][which] = isFinite(v) ? v : 0;
              s.polarization = pol.map(p => [p[0], p[1]]);
              this._notifyMutate();
            });
            wrap.appendChild(inp);
          }
          row.appendChild(wrap);
          return row;
        };
        /* 传播/偏振：点源以复偏振定取向（real/imag）+ kind（电/磁偶极子） */
        const sSrcProp = this._sec("传播/偏振", true, 3);
        inner.appendChild(sSrcProp.head); inner.appendChild(sSrcProp.body);
        const POL_TIP = "默认偏振 = Ex（[1,0,0]）；平面波/高斯束默认才是 Ey。"
          + "监视器 field 默认 Ey——两者不对齐会读到零场（陷阱#1：场分量要对齐）";
        sSrcProp.body.appendChild(polRow("polar re", 0, POL_TIP));
        sSrcProp.body.appendChild(polRow("polar im", 1, POL_TIP));
        sSrcProp.body.appendChild(this._el("div", "note",
          "默认偏振 = Ex（[1,0,0]）——与平面波/高斯束的默认 Ey 不同；"
          + "监视器 field 默认 Ey，须与主偏振轴对齐，否则读到零场（陷阱#1）。"));
        sSrcProp.body.appendChild(this._row("kind",
          this._select(`source.${i}.kind`, s.kind || "electric", ["electric", "magnetic"])));
        /* 位置与注入面 */
        const sSrc2 = this._sec("位置与注入面", true, 2);
        inner.appendChild(sSrc2.head); inner.appendChild(sSrc2.body);
        sSrc2.body.appendChild(this._vec("center (µm)", `source.${i}.center`, s.center, 3, "len"));
        sSrc2.body.appendChild(this._vec("size (µm)", `source.${i}.size`, s.size, 3, "len"));
      } else {
        const dirSel = this._select(`source.${i}.direction`, s.direction || "+", ["+", "-"]);
        /* 语义可读（P 系列 a11y）：下拉只显示 "+"/"-"，无 UI 不给含义 →
         * 显式可访问名 + 悬停提示（_select 无 title/aria-label 透传，
         * 在返回元素上 setAttribute，零 innerHTML） */
        dirSel.setAttribute("aria-label", "传播方向（+ = 默认正向，− = 反向）");
        dirSel.setAttribute("title", "传播方向：+ 默认正方向，- 反方向");
        const paSel = this._select(`source.${i}.propagation_axis`, s.propagation_axis || "",
          ["", "x", "y", "z"], true);
        paSel.setAttribute("aria-label", "传播轴（x/y/z；留空=删除该字段）");
        paSel.setAttribute("title",
          "传播轴：显式定轴（x/y/z）；留空=删除该字段，改由 size 恰一非正槽推断");
        /* 传播/偏振：方向角（theta/phi/psi）+ 显式传播轴 */
        const sSrcProp = this._sec("传播/偏振", true, 3);
        inner.appendChild(sSrcProp.head); inner.appendChild(sSrcProp.body);
        sSrcProp.body.appendChild(this._row("direction", dirSel));
        sSrcProp.body.appendChild(this._row("theta/phi/psi (°)",
          this._input(`source.${i}.theta`, s.theta ?? 0, "num"),
          "默认 (0,0,0) = 沿 +z 传播、电场沿 Ey（不是 Ex！陷阱#1）；theta 极角 [0,180]，phi/psi 方位 [0,360)",
          this._input(`source.${i}.phi`, s.phi ?? 0, "num"),
          this._input(`source.${i}.psi`, s.psi ?? 0, "num")));
        sSrcProp.body.appendChild(this._row("propagation_axis", paSel));
        /* 位置与注入面 */
        const sSrc2 = this._sec("位置与注入面", true,
          s.type === "gaussian_beam" ? 4 : 3);
        inner.appendChild(sSrc2.head); inner.appendChild(sSrc2.body);
        sSrc2.body.appendChild(this._vec("center (µm)", `source.${i}.center`, s.center, 3, "len"));
        sSrc2.body.appendChild(this._vec("size (µm)", `source.${i}.size`, s.size, 3, "len"));
        if (s.type === "gaussian_beam") {
          sSrc2.body.appendChild(this._row("radius (µm)",
            this._input(`source.${i}.radius`, s.radius, "num", "len")));
        }
        // from_face 仅平面波/高斯束有此字段（soft_source 选了会被服务端静默忽略）
        const ff = this._select(`source.${i}.from_face`, s.from_face || "",
          ["", "x_min", "x_max", "y_min", "y_max", "z_min", "z_max"], true);
        ff.setAttribute("aria-label", "注入面（贴面注入；留空=删除该字段）");
        ff.setAttribute("title",
          "注入面：一键贴面注入并钉轴/方向；留空=删除该字段，改用手动域内注入");
        sSrc2.body.appendChild(this._row("from_face", ff,
          "⚠ 贴面注入的定量透过率目前退化（T≈0.14 而非 ~1）——定量 T 请用 direction+size+center 手动配注入面于域内（陷阱#5）"));
      }
      body.appendChild(this._card(
        [this._preview(s.type),                     /* 渲染风缩略图（R2） */
         this._headName(`source.${i}.name`, s.name, "source name",
           (s.name || `源 ${i + 1}`) + " 名称"),
         this._el("span", "tag type", s.type),
         this._delBtn("source", i)],
        inner));
    });
    const addRow = this._el("div", "frow");
    const typeSel = this._select(null, "soft_source",
      ["soft_source", "plane_wave", "gaussian_beam"]);
    const add = this._el("button", "mini", "+ 源");
    add.addEventListener("click", () => {
      const t = typeSel.value;
      list.push(t === "soft_source"
        ? { type: "soft_source", freq: 300e12, bandwidth: 60e12, name: "s" + (list.length + 1),
            center: [0, 0, -0.3e-6], polarization: [1, 0, 0] }
        : t === "plane_wave"
          ? { type: "plane_wave", freq: 300e12, bandwidth: 60e12,
              name: "pw" + (list.length + 1), from_face: "z_min" }
          : { type: "gaussian_beam", freq: 300e12, bandwidth: 60e12,
              name: "gb" + (list.length + 1), from_face: "z_min", radius: 1e-6 });
      this._rerender();
    });
    typeSel.setAttribute("aria-label", "新源类型");
    addRow.appendChild(this._el("label", "flabel", "新类型"));
    addRow.appendChild(typeSel);
    addRow.appendChild(add);
    if (this.only == null) body.appendChild(addRow);
  },

  _monitor(body) {
    const list = this.cfg.monitor || (this.cfg.monitor = []);
    if (!list.length) {
      body.appendChild(this._paneEmpty("mon_time", "还没有监视器",
        "到 FDTD 页的 MONITORS 添加，或在脚本里 f.mon.add_freq()"));
      return;
    }
    this._items(list).forEach(([m, i]) => {
      const inner = document.createDocumentFragment();
      /* 命名单一位置 = 卡头（可编辑名输入）；原「基本参数」节只含 name，
       * 随之一并移除（不再有空节头） */
      /* 位置与尺寸（物理因果 RoundE：先定观测面/域，再定观测什么、怎么采样） */
      const sMon2 = this._sec("位置与尺寸", true, 2);
      inner.appendChild(sMon2.head); inner.appendChild(sMon2.body);
      sMon2.body.appendChild(this._vec("center (µm)", `monitor.${i}.center`, m.center, 3, "len"));
      sMon2.body.appendChild(this._vec("size (µm)", `monitor.${i}.size`, m.size, 3, "len"));
      sMon2.body.appendChild(this._el("div", "note",
        "size 槽：0=单格 · 留空=保持 null（该轴全域）· 三槽全全域直接删 size 走默认。"));
      /* 观测节（RoundE 统一）：场分量/频率采样/窗函数收进观测节，与物理因果一致 */
      const sMon1 = this._sec("观测", false);
      inner.appendChild(sMon1.head); inner.appendChild(sMon1.body);
      const fieldOpts = m.type === "index" ? ["eps", "mu", "both"]
        : ["Ex", "Ey", "Ez", "Hx", "Hy", "Hz", "all"];
      sMon1.body.appendChild(this._row("field",
        this._select(`monitor.${i}.field`, m.field, fieldOpts)));
      if (m.type === "freq") {
        m.freq = m.freq || { center: 300e12, bw: 120e12, n: 200 };
        sMon1.body.appendChild(this._row("freq c/bw (THz) · n",
          this._input(`monitor.${i}.freq.center`, m.freq.center, "num", "freq"),
          this._input(`monitor.${i}.freq.bw`, m.freq.bw, "num", "freq"),
          this._input(`monitor.${i}.freq.n`, m.freq.n, "num")));
        /* apodize：side + 窗 + 宽（定量谱学的首程窗——陷阱 #5/#7）；
         * 不预创建空 dict——用户不动就不写进 cfg（避免 apodize:{} 噪音） */
        const ap = m.apodize || {};
        sMon1.body.appendChild(this._row("apodize side",
          this._select(`monitor.${i}.apodize.side`, ap.side || "",
            ["", "start", "stop", "both"], true)));
        sMon1.body.appendChild(this._vec("apod window (fs)",
          `monitor.${i}.apodize.time_window`, ap.time_window, 2, "time"));
        sMon1.body.appendChild(this._row("apodize width (fs)",
          this._input(`monitor.${i}.apodize.width`, ap.width, "num", "time")));
      }
      if (m.type === "time" || m.type === "freq") {
        /* down_sample：窗 + 步长（长仿真采样量控制——守卫第二道闸） */
        const ds = m.down_sample || {};
        sMon1.body.appendChild(this._vec("sample window (fs)",
          `monitor.${i}.down_sample.time_window`, ds.time_window, 2, "time"));
        sMon1.body.appendChild(this._row("stride",
          this._input(`monitor.${i}.down_sample.stride`, ds.stride ?? 1, "num")));
      }
      /* 卡头（统一语言 2026）：类型固定于添加时（同 geometry/source 模式），
       * 改类型 = 删除重建；移除原「类型下拉」就地切换（旧行为会静默重建
       * 对象丢参、二次确认），语义不变——类型仍由 topbar 或底部「+ 监视器」
       * 行选择。 */
      body.appendChild(this._card(
        [this._preview("mon_" + m.type),            /* 渲染风缩略图（R2）：time/freq/index → mon_* 键 */
         this._headName(`monitor.${i}.name`, m.name, "monitor name",
           (m.name || `监视器 ${i + 1}`) + " 名称"),
         this._el("span", "tag type", m.type),
         this._delBtn("monitor", i)],
        inner));
    });
    const addRow = this._el("div", "frow");
    const typeSel = this._select(null, "time", ["time", "freq", "index"]);
    const add = this._el("button", "mini", "+ 监视器");
    add.addEventListener("click", () => {
      const t = typeSel.value;
      // 平面波默认偏振 Ey —— 新监视器 field 默认 Ey 更友好（已知坑位）
      list.push(t === "time"
        ? { type: "time", name: "pt" + (list.length + 1), center: [0, 0, 0],
            size: [0, 0, 0], field: "Ey" }
        : t === "freq"
          ? { type: "freq", name: "sp" + (list.length + 1), center: [0, 0, 0],
              size: [0, 0, 0], field: "Ey", freq: { center: 300e12, bw: 120e12, n: 200 } }
          : { type: "index", name: "ix" + (list.length + 1), center: [0, 0, 0],
              size: [null, null, null], field: "eps" });
      this._rerender();
    });
    typeSel.setAttribute("aria-label", "新监视器类型");
    addRow.appendChild(this._el("label", "flabel", "新类型"));
    addRow.appendChild(typeSel);
    addRow.appendChild(add);
    if (this.only == null) body.appendChild(addRow);
  },

  /* GPU 卡数不由用户选（G78 2026-10-25 站长裁决）：内核只报需求、网关按
   * 等级上限与机型容量决定给几张卡，run.gpu 退化成可选上限。表单因此
   * 不再提供任何选卡控件——需要抬高上限的走配置文件/脚本。 */

  _run(body) {
    // G18 单旋钮：时长唯一在 domain.total_time（domain 节编辑）——run 节
    // 只剩超时；卡数由系统定（G78），步数是 build 后派生量。
    const r = this.cfg.run || (this.cfg.run = {});
    body.appendChild(this._row("超时 (s)",
      this._input("run.timeout_s", r.timeout_s ?? 3600, "num")));
    // 提交面板挂载点（app.js 常驻 DOM 移进来; 2026-08-31au 方案A：
    // 执行动作归 run 节，与生成代码的 f.run(...) 同构）
    // 2026-09-10：顶栏 Simulate 框已有常驻 #run-mount（index.html），
    // 此处只在缺位时补挂点——避免同页重复 id（getElementById 取首个，
    // 重复 id 是非法 HTML 且会让 remountTools 目标含糊）。
    if (!document.getElementById("run-mount")) {
      const mount = this._el("div");
      mount.id = "run-mount";
      body.appendChild(mount);
    }
  },
};
