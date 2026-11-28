/* views/kit.js — 页面层唯一实现（2026-11-25 站长拍：抽公共件）
 * 行 / 二维表 / 节 / 分页渲染器：页面内容只写 schema（数据），不写 DOM。
 * 用法：views/form.js 里 `Object.assign(FormPanel, KIT)`；卡片只写
 * `renderPage(SCHEMA(cfg, commit), body)`。基元（_el/_input/_select/
 * _numFromInput/状态位）仍住在 FormPanel——kit 通过 this 调它们。
 */
"use strict";

export const KIT = {

  _clearFieldError(i) {
    i.classList.remove("field-err");
    i.removeAttribute("aria-invalid");
    i.removeAttribute("aria-describedby");
    /* 不擦 title：单位换算提示（_input 的 "x µm → cfg y m"）住在那儿 */
    const host = i.closest(".frow") || i.closest(".brow") || i.closest(".mtx-scroll");
    if (host && host._errEl) {
      host._errEl.remove();
      host._errEl = null;
    }
  },

  /* 本地非法值：当场说，不等下一次快照（旧版静默 return，用户以为提交了）。 */
  _reject(inp, msg) {
    if (!inp) return;
    inp.classList.add("field-err");
    inp.setAttribute("aria-invalid", "true");
    const host = inp.closest(".frow") || inp.closest(".mtx-scroll");
    if (!host) return;
    if (!host._errEl) {
      const m = this._el("div", "field-err-msg");
      host._errEl = m;
      host.parentNode.insertBefore(m, host.nextSibling);
    }
    const line = "⚠ " + msg;
    const cur = host._errEl.textContent;
    if (!cur.includes(line))
      host._errEl.textContent = cur ? cur + "　" + line : line;
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


  _row(label, ...rest) {
    const controls = rest.filter((c) => c && c.nodeType);
    const tip = rest.find((c) => typeof c === "string");
    const row = this._el("div", "frow");
    /* 说明在上、控件在下（2026-11-25 站长拍）；没有说明就不占位。 */
    const lab = label
      ? this._fillLabel(this._el("label", "flabel"), label)
      : null;
    /* 2026-11-25（COMSOL 式两栏）：**≤2 字段的行单位进输入框做灰后缀**（标签
     * 只留名字）；**≥3 字段且标签自带单位**（如 center (µm)）→ 单位回标签、
     * 字段不挂后缀（54px 的框塞不下）。标签没单位的横排（如 Solve 三个数）
     * 仍用框内后缀。 */
    const unitWraps = controls.filter(
      (c) => c.querySelector && c.querySelector("input[data-unit]"));
    const labelHasUnit = !!(lab && lab.querySelector(".unit"));
    if (unitWraps.length >= 3 && labelHasUnit) {
      for (const w of unitWraps) {
        const u = w.querySelector(".unit");
        if (u) u.remove();
      }
    } else if (unitWraps.length >= 1) {
      const u = lab.querySelector(".unit");
      if (u) u.remove();
    }
    if (lab) {
      lab.title = tip && tip !== label ? tip + " · " + label : label;
      row.appendChild(lab);
    }
    /* 控件组：铺满第二栏（.fctl），窄面板下输入框按 flex 让宽不碎行 */
    const fctl = this._el("span", "fctl");
    for (const c of controls) fctl.appendChild(c);
    row.appendChild(fctl);
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
      if (idx === 0) { if (lab) lab.htmlFor = ctl.id; }
      else if (label && !ctl.getAttribute("aria-label"))
        ctl.setAttribute("aria-label", label + " " + (idx + 1));
    });
    return row;
  },

  /* 打开写腿的唯一入口：可编辑 + 回到 Tab 序列 + 输入即清错误态。
   * 直接写 el.readOnly=false 也能改，但会留在 Tab 序列外——别绕过这里。 */
  _edit(el) {
    if (!el) return el;
    el.readOnly = false;
    el.tabIndex = 0;
    el.addEventListener("input", () => this._clearFieldError(el));
    return el;
  },

  _vec(label, path, arr, n, hintKind) {
    const parts = [];
    for (let i = 0; i < n; i++) {
      parts.push(this._input(path + "." + i, arr ? arr[i] : undefined,
        "num", hintKind));
    }
    return this._row(label, ...parts);
  },

  /* 范围两槽：行与单位规则全走 _row（唯一行资产），这里只补 min/max 角色
   * （placeholder + aria）——旧版自建 label/fctl 是第二套行实现。
   * data-path 保持 .0/.1：cfg 仍是 [lo, hi] 数组（校验/线格式不受扰）。 */
  _rangeRow(label, path, arr, hintKind) {
    const mk = (slot, tag) => {
      const wrap = this._input(path + "." + slot,
        Array.isArray(arr) ? arr[slot] : undefined, "num", hintKind);
      const inp = wrap.querySelector("input");
      if (inp) {
        inp.placeholder = tag;
        inp.setAttribute("aria-label", label + " " + tag);
        inp.title = label + " " + tag;
      }
      return wrap;
  };
    return this._row(label, mk(0, "min"), mk(1, "max"));
  },

  /* L-35 拔线：_matMode/_writeBack 的写腿已删（随 M4 重接）。 */

  _card(headChildren, bodyEl) {
    const card = this._el("div", "card");
    const head = this._el("div", "card-head");
    for (const c of headChildren) if (c) head.appendChild(c);
    card.appendChild(head);
    const body = this._el("div", "card-body");
    body.appendChild(bodyEl);
    card.appendChild(body);
    return card;
  },


  /* ══════════════════ 各节（渲染进传入的 body 容器） ══════════════════ */

  /* ══ 表格资产（2026-11-25 站长拍，第一性原理）═══════════════════════
   * 面板内容只有两类：单控件行（_row）与二维表（_matrix）。
   * 表 = 列头（含单位）+ 行名 + **紧贴格子**（发丝线分隔、无表单控件外框、
   * 无格间空隙）；列宽 1fr 随面板宽度伸缩，窄于格下限（--min）才横向滚动。
   * 单位进列头/行名，格内只放数值——这是消除"格子之间空当"的根。
   * spec = { cols:[{label, unit}], rows:[{label, unit, cells:[<input|select>]}],
   *          min } ；单元格一律由 _mtxInput / _select 构造后传入。 */
  _matrix(spec) {
    /* 行头列只在"有行名"时存在：全空行名的表（如 Boundary 的 Kind 单行）
     * 不画 stub——那一列本来就只是给"Kind"两个字占位（2026-11-25 站长拍）。 */
    const hasLab = (spec.rows || []).some((r) => r.label);
    const scroll = this._el("div", "mtx-scroll");
    const grid = this._el("div", "mtx" + (hasLab ? "" : " mtx-nolab"));
    grid.style.setProperty("--n", String((spec.cols || []).length));
    if (spec.min) grid.style.setProperty("--min", spec.min + "px");
    if (hasLab) grid.appendChild(this._el("span", "mtx-corner"));
    for (const c of spec.cols || []) {
      grid.appendChild(this._el("span", "mtx-h",
        c.unit ? `${c.label} (${c.unit})` : String(c.label)));
    }
    for (const r of spec.rows || []) {
      if (hasLab) {
        const lab = this._el("span", "mtx-l", r.label || "");
        if (r.unit) lab.appendChild(this._el("span", "unit", `(${r.unit})`));
        grid.appendChild(lab);
      }
      for (const el of r.cells || []) grid.appendChild(el);
    }
    scroll.appendChild(grid);
    return scroll;
  },

  /* ═══════════════════ 页面描述符渲染器（B 批 2026-11-25）═══════════════
   * spec 是数据（section / row / table / pills / note）；DOM 只在这里生成。
   * 加内容 = 往页面 schema 加一项；写字段只经 spec.commit，动作用 item.run。 */
  renderPage(spec, body) {
    for (const it of (spec && spec.sections) || []) this._item(spec, it, body);
  },

  _item(spec, it, host) {
    if (it.type === "section") {
      const s = this._msec(it.key, it.title, it.open === false);
      host.appendChild(s.head); host.appendChild(s.body);
      for (const sub of it.items || []) this._item(spec, sub, s.body);
      return;
    }
    if (it.type === "table") { host.appendChild(this._table(spec, it)); return; }
    if (it.type === "range") { host.appendChild(this._rangeTable(spec, it)); return; }
    if (it.type === "note") {
      host.appendChild(this._el("div", "note", it.text)); return;
    }
    if (it.type === "row") {
      const built = (it.controls || []).map((c) => this._ctl(spec, c, false));
      host.appendChild(this._row(it.label, ...built.map((b) => b.el)));
      if (it.onRow) this._bindRowChange(built, it);
    }
  },

  /* 一种控件一条生成规则。返回 {el, input, desc}：
   * el = 进 _row/_matrix 的节点，input = 收 change 的原件。
   * inTable = 表格格：走 _mtxInput（无框紧贴格），不套 .inwrap。
   *
   * 无值 = 占位 `—`；值只来自快照（GUI 零默认，L-62）；清空是否写 None
   * 由卡片的 `onClear` 决定，kit 不替它发明值。 */
  _ctl(spec, c, inTable) {
    if (c.kind === "tag") return { el: this._el("span", "tag", c.text), desc: c };
    if (c.kind === "text") return { el: this._el("span", "note", c.text), desc: c };
    if (c.kind === "str") {
      /* 可编辑文本（多值、枚举字符串）：校验失败就地报错，不惊动 worker */
      if (inTable) {
        /* 表格格（如 mode 波长）：裸输入贴格，左对齐，无框 */
        const inp = document.createElement("input");
        inp.type = "text";
        inp.className = "mtx-in mtx-in-str";
        if (c.path) inp.dataset.path = c.path;
        inp.value = c.value == null ? "" : String(c.value);
        if (c.aria) inp.setAttribute("aria-label", c.aria);
        if (c.hint) inp.title = c.hint;
        if (c.onSet) {
          this._edit(inp);
          inp.addEventListener("change", () => {
            const msg = c.validate ? c.validate(inp.value) : null;
            if (msg) { this._reject(inp, msg); return; }
            this._clearFieldError(inp);
            c.onSet(inp.value, inp);
          });
        } else { inp.readOnly = true; inp.tabIndex = -1; }
        return { el: inp, input: inp, desc: c };
      }
      const wrap = this._input(c.path || "x.str", c.value, "str");
      const inp = wrap.querySelector("input");
      if (c.aria) inp.setAttribute("aria-label", c.aria);
      if (c.hint) inp.title = c.hint;
      if (c.onSet) {
        this._edit(inp);                     /* _input 默认只读：可编辑要显式打开 */
        inp.addEventListener("change", () => {
          const msg = c.validate ? c.validate(inp.value) : null;
          if (msg) { this._reject(inp, msg); return; }
          this._clearFieldError(inp);
          c.onSet(inp.value, inp);
        });
      }
      return { el: wrap, input: inp, desc: c };
    }
    if (c.kind === "select") {
      const sel = this._select(c.path, c.value, c.options || []);
      if (inTable) sel.classList.add("mtx-sel");   /* 表格格：与行内下拉共用一套长相 */
      if (c.unsetLabel && sel.options[0]) sel.options[0].textContent = c.unsetLabel;
      if (c.aria) sel.setAttribute("aria-label", c.aria);
      if (c.disabled) sel.disabled = true;
      /* flatLocked（Polarization 表 all=on 的锁定格）：去 chevron 的纯文本
       * 长相；解锁走 DOM（monitor 卡 lockComps）。 */
      if (c.flatLocked) sel.classList.add("flat-locked");
      /* 监听照挂（disabled 元素不触发 change）：monitor Polarization 表的
       * all 开关会在 DOM 层解锁同行的分量 select，解锁后要能写。 */
      if (c.onSet || c.onPick) {
        sel.disabled = false;
        sel.addEventListener("change", () => {
          if (c.confirm && !window.confirm(c.confirm)) {
            sel.value = c.value;                 /* 取消 → 显示回当前值 */
            return;
          }
          (c.onPick || c.onSet)(sel.value, sel);
          if (c.rerender) this._rerender();
        });
      }
      return { el: sel, input: sel, desc: c };
    }
    /* num（默认）：表格格 = _mtxInput（无框紧贴格）；行内 = _input（框内后缀）
     * 只读 = 无框不进 Tab；可编辑 = _edit + 写腿 */
    if (inTable) {
      const inp = this._mtxInput(c.value, c.unit);
      if (c.path) inp.dataset.path = c.path;
      if (c.aria) inp.setAttribute("aria-label", c.aria);
      if (c.hint) inp.title = c.hint;
      if (c.disabled) {
        inp.disabled = true;
        inp.placeholder = "—";
      } else if (c.onSet) {
        this._edit(inp);
        this._bindNum(c, inp);
      }
      return { el: inp, input: inp, desc: c };
    }
    const wrap = this._input(c.path, c.value, "num", c.unit);
    const inp = wrap.querySelector("input");
    if (c.aria) inp.setAttribute("aria-label", c.aria);
    if (c.hint) inp.title = c.hint;
    if (c.disabled) {
      inp.disabled = true;
      inp.placeholder = "—";
    } else if (c.onSet) {
      this._edit(inp);
      this._bindNum(c, inp);
    }
    return { el: wrap, input: inp, desc: c };
  },

  /* 单格写回：空文本 ≠ 非法（空 = 未设置/清除），两者分开报。 */
  _bindNum(c, inp) {
    inp.addEventListener("change", () => {
      const v = this._numFromInput(inp, this._fieldUnit(c.unit));
      if (v == null) {
        if (String(inp.value).trim())
          this._reject(inp, c.badMsg || "not a number — try 0.5um or 500nm");
        else if (c.onClear) c.onClear(inp);
        return;
      }
      c.onSet(v, inp);
    });
  },

  /* 行级写回：一行多个槽合成一个字段（range 元组 / k 向量）。 */
  _bindRowChange(built, it) {
    const ins = built.filter((b) => b.desc && b.desc.kind === "num"
      && b.desc.group !== false).map((b) => b.input);
    if (!ins.length) return;
    for (const i of ins) this._edit(i);   /* 行级槽由这里打开写腿 */
    const unit = this._fieldUnit(it.unit);
    const fire = () => {
      const vals = ins.map((i) => this._numFromInput(i, unit));
      if (vals.some((v) => v == null)) {
        ins.forEach((i) => {
          if (String(i.value).trim() && this._numFromInput(i, unit) == null)
            this._reject(i, it.badMsg || "not a number — try 0.5um or 500nm");
        });
        return;
      }
      const bad = it.validate ? it.validate(vals) : null;
      if (bad) { this._reject(ins[bad.index], bad.msg); return; }
      it.onRow(vals, ins);
  };
    for (const i of ins) i.addEventListener("change", fire);
  },

  /* 范围编辑器（2026-11-25 站长拍，公共件）：min / max / center / size 四列
   * **同时可编辑**——改任一个，另两个派生值立即重算；写回仍是 (lo, hi) 元组。
   * fdtd 域、mode 截面、monitor/source/geometry 的窗口都用它。 */
  _rangeTable(spec, it) {
    const unitKind = it.unit || null;
    const u = this._fieldUnit(unitKind);
    const ul = u ? u.label : "";
    /* 列序是**卡片内容**（2026-11-25 站长拍）：必须由 spec.order 给出，
     * kit 不预设、不兜底（缺了就是 schema 写错，直接大声报）。 */
    const ORDER = it.order;
    if (!Array.isArray(ORDER) || !ORDER.length)
      throw new Error("range item needs `order` (e.g. [\"center\",\"size\",\"min\",\"max\"])");
    /* 第二列两种叫法：size（fdtd 域范围）或 span（频段）；同一套换算。 */
    const WIDTH = ORDER.includes("span") ? "span" : "size";
    /* 可选首格（2026-11-27 站长拍）：一行 5 列——首格放一个控件（如
     * frequency/wavelength 下拉），后面仍是 range 四列互算。 */
    const lead = it.lead || null;
    const cols = (lead ? [{ label: lead.header || "" }] : [])
      .concat(ORDER.map((label) => ({ label, unit: ul })));
    const rows = (it.rows || []).map((r) => {
      if (r.centerOnly) {
        /* 传播轴那一行：只有 center 可编（它就是这个轴的 position），
         * 其余三格不提供输入（空白、不可编辑，2026-11-25 站长拍）。 */
        const cells = ORDER.map((name) => {
          const inp = this._mtxInput(
            name === "center" ? r.position : undefined, unitKind);
          if (name !== "center") {
            inp.disabled = true;
            inp.placeholder = "—";   /* 不提供的栏：横杠，不留空 */
          }
          return inp;
        });
        const center = cells[ORDER.indexOf("center")];
        this._edit(center);
        center.addEventListener("change", () => {
          const v = this._numFromInput(center, u);
          if (v == null) {
            this._reject(center, "not a number — try 0.5um or 500nm");
            return;
          }
          this._clearFieldError(center);
          r.onCenter(v);
        });
        if (lead) cells.unshift(this._ctl(it, lead, true).el);
        return { label: r.label, cells };
      }
      const v0 = Array.isArray(r.value) ? r.value.map(Number) : [NaN, NaN];
      const val = {
        center: (v0[0] + v0[1]) / 2, size: v0[1] - v0[0],
        span: v0[1] - v0[0], min: v0[0], max: v0[1],
      };
      const ins = {};
      for (const name of ORDER) ins[name] = this._mtxInput(val[name], unitKind);
      const apply = (lo, hi) => {
        ins.min.value = this._dispVal(unitKind, lo);
        ins.max.value = this._dispVal(unitKind, hi);
        ins.center.value = this._dispVal(unitKind, (lo + hi) / 2);
        ins[WIDTH].value = this._dispVal(unitKind, hi - lo);
        for (const name of ORDER) this._clearFieldError(ins[name]);
      };
      const fire = (which) => () => {
        const v = {};
        for (const name of ORDER) v[name] = this._numFromInput(ins[name], u);
        if (ORDER.some((name) => v[name] == null)) {
          for (const name of ORDER) {
            if (String(ins[name].value).trim() && v[name] == null)
              this._reject(ins[name], "not a number — try 0.5um or 500nm");
          }
          return;
        }
        let lo = v.min, hi = v.max;
        if (which === "center" || which === WIDTH) {    /* 改中心/宽度 → 反算边界 */
          const s = v[WIDTH];
          if (!(s > 0)) { this._reject(ins[WIDTH], "span must be > 0"); return; }
          lo = v.center - s / 2; hi = v.center + s / 2;
        } else if (!(hi > lo)) {                        /* 改边界 → 必须 lo < hi */
          this._reject(ins[which], "min must be < max");
          return;
        }
        apply(lo, hi);
        r.onSet([lo, hi]);
      };
      for (const name of ORDER) {
        this._edit(ins[name]);
        ins[name].addEventListener("change", fire(name));
      }
      const cells = ORDER.map((name) => ins[name]);
      if (lead) cells.unshift(this._ctl(it, lead, true).el);
      return { label: r.label, cells };
    });
    const mtx = this._matrix({ cols, rows, min: 52 });
    const wrap = this._el("div", "tbl");
    if (it.caption) wrap.appendChild(this._el("div", "mtx-cap", it.caption));
    wrap.appendChild(mtx);
    return wrap;
  },

  _table(spec, t) {
    const rows = (t.rows || []).map((r) => {
      const built = (r.cells || []).map((c) => this._ctl(spec, c, true));
      if (r.onRow) this._bindRowChange(built, Object.assign({ unit: r.unit }, r));
      return { label: r.label, unit: r.unitLabel,
               cells: built.map((b) => b.el) };
    });
    const mtx = this._matrix({ cols: t.cols, rows, min: t.min });
    if (!t.caption) return mtx;
    /* 每张表有自己的题目（2026-11-25 站长拍）：与字段说明同一套字（12px 次级）。 */
    const wrap = this._el("div", "tbl");
    wrap.appendChild(this._el("div", "mtx-cap", t.caption));
    wrap.appendChild(mtx);
    return wrap;
  },
};
