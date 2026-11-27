import { apiNotice } from "../modules/api.js";
import { exec } from "../core/bus.js";
import { py, parseComplex, formatComplex } from "../modules/util.js";
import { GEO_TYPE_LABEL, previewEl } from "./previews.js";
import { matColor } from "./scene3d.js";
import { cfg, scene } from "../modules/state.js";
import { KIT } from "./kit.js";
import { BOUNDARY_DEFAULTS, MODE_TRANSVERSE, modeSeed, fieldsArg,
         C0 } from "../modules/defaults.js";
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
export const FIELD_UNITS = {
  len:  { toSI: (v) => v * 1e-6,  toUser: (v) => v * 1e6,
          label: "µm", suffix: "µm", si: "m",  hint: "len" },
  wl:   { toSI: (v) => v * 1e-9,  toUser: (v) => v * 1e9,
          label: "nm", suffix: "nm", si: "m",  hint: "len" },
  time: { toSI: (v) => v * 1e-15, toUser: (v) => v * 1e15,
          label: "fs", suffix: "fs", si: "s",  hint: "time" },
  freq: { toSI: (v) => v * 1e12,  toUser: (v) => v / 1e12,
          label: "THz", suffix: "THz", si: "Hz", hint: "freq" },
  /* ang 的值口径是 THz(ω=2πf)，后缀写不下也不该误读——框内不出后缀，
   * 单位仍在标签里说明 */
  ang:  { toSI: (v) => v * 2 * Math.PI * 1e12,
          toUser: (v) => v / (2 * Math.PI * 1e12),
          label: "THz(ω=2πf)", suffix: null, si: "rad/s", hint: "ang" },
};

/* 可解析的友好显示：6 位有效数字、非极端量级用十进制（0.5 / 381.97） */
export function fmtUserNum(x) {
  if (typeof x !== "number" || !isFinite(x)) return "";
  return String(parseFloat(x.toPrecision(6)));
}

export function _dispValue(val, unit) {
  if (Array.isArray(val))
    return val.map((x) =>
      typeof x === "number" && unit ? fmtUserNum(unit.toUser(x))
      : x == null ? "" : String(x)).join(", ");
  if (typeof val === "number" && unit) return fmtUserNum(unit.toUser(val));
  return val == null ? "" : String(val);
}

/* ── 路径读写（点分；数组下标即路径段）── 阶段 5b 回装（最小写回） */
export function getPath(obj, path) {
  let c = obj;
  for (const k of String(path).split(".")) {
    if (c == null) return undefined;
    c = c[k];
  }
  return c;
}


/* ── 单位后缀（2026-10-21 用户实测）：注释一直写着"[单位后缀]优先"，
 * 但其实没有任何实现——输入 `1um` 会被当成**表达式字符串**原样写进
 * 模型，随后 genie.models 求值报语法错（`invalid decimal literal`），
 * 用户看到的是"值没生效/变成怪数字"。这里补上：
 *   裸数字  → 按字段单位换算（domain 里 1 = 1 µm）
 *   带后缀  → 后缀说了算，直接得 SI 绝对值（1um = 1e-6 m，与字段单位无关）
 * 认不出后缀又不像纯数字的（如 `lambda/2`、"1e-6*2"）照旧透传给服务端求值。 */
export const _UNIT_SUFFIX = {
  m: 1, dm: 1e-1, cm: 1e-2, mm: 1e-3,
  um: 1e-6, "µm": 1e-6, "μm": 1e-6, nm: 1e-9, pm: 1e-12,
  s: 1, ms: 1e-3, us: 1e-6, "µs": 1e-6, "μs": 1e-6,
  ns: 1e-9, ps: 1e-12, fs: 1e-15,
  thz: 1e12, ghz: 1e9, mhz: 1e6, khz: 1e3, hz: 1,
};
export function parseSuffixed(t) {
  const m = String(t).trim().match(
    /^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Zµμ]+)$/);
  if (!m) return NaN;
  const f = _UNIT_SUFFIX[m[2].toLowerCase()];
  return f === undefined ? NaN : Number(m[1]) * f;
}

/* ═══ fdtd 设置页 = 一份描述符（B 批 2026-11-25）══════════════════════
 * 这张卡的全部内容都在下面：加字段 / 换分组只加数据，DOM 由
 * FormPanel.renderPage 生成；写字段只经 commit（exec set_named + py）。
 * DOMAIN_FIELDS / BOUNDARY_FIELDS = 本卡覆盖 wire 的哪些字段，测试按
 * DomainSpec / BoundarySpec 反射校验（不抄类型、不抄默认值）。 */
export const DOMAIN_FIELDS = ["x_range", "y_range", "z_range", "dx", "dy",
  "dz", "x_spacing", "y_spacing", "z_spacing", "total_time", "cfl",
  "auto_shutoff", "boundary"];
export const BOUNDARY_FIELDS = ["kind", "layers", "kappa_max", "order",
  "k", "from_source"];
const DOMAIN_FACES = ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"];

function _fmtSeg(t) {
  return t.map((s) => `${(s.d * 1e9).toPrecision(3)}nm`).join(" | ");
}

/* mode 卡覆盖的字段（测试按 ModeDomainSpec / ModeConfigSpec 反射校验）。 */
export const MODE_FIELDS = ["axis", "position", "x_range", "y_range", "z_range",
  "dx", "dy", "dz", "x_spacing", "y_spacing", "z_spacing",
  "boundary", "wavelength", "n_modes", "n_ref",
  "rotate_axis", "rotate_angle", "rotate_radius"];

const MODE_FACES = { x: ["y_min", "y_max", "z_min", "z_max"],
                     y: ["x_min", "x_max", "z_min", "z_max"],
                     z: ["x_min", "x_max", "y_min", "y_max"] };

export function DOMAIN_SCHEMA(cfg, commit) {
  const d = (cfg && cfg.domain) || {};
  const range = (a) => {
    const r = d[`${a}_range`];
    if (!Array.isArray(r)) return null;
    const lo = Number(r[0]), hi = Number(r[1]);
    return isFinite(lo) && isFinite(hi) ? [lo, hi] : null;
  };
  const hasTable = ["x", "y", "z"].some((a) => d[`${a}_spacing`]);
  const bnd = d.boundary || {};
  const kindOf = (f) => (bnd[f] || {}).kind || "";
  const commitFace = (f, face) => {
    const cur = d.boundary || {};      /* 每次读最新表：连改两面不互相覆盖 */
    const nb = {};
    for (const k of Object.keys(cur)) nb[k] = Object.assign({}, cur[k]);
    nb[f] = face;                      /* 整面替换：旧 kind 的专属参数自然消失 */
    d.boundary = nb;
    commit("boundary", py(nb));
  };
  /* 换 kind = 丢掉旧 kind 的参数 + 补新 kind 的默认值（GUI 侧规则） */
  const setFaceKind = (f, kind) => {
    const face = { kind };
    if (BOUNDARY_DEFAULTS[kind]) Object.assign(face, BOUNDARY_DEFAULTS[kind]);
    commitFace(f, face);
  };

  /* 1. General settings —— 域范围（min/max/center/size 同时可编）+ 时间步进 */
  const rangeRows = ["x", "y", "z"].map((a) => ({
    label: a,
    value: range(a),
    onSet: (v) => commit(`${a}_range`, `(${py(v[0])}, ${py(v[1])})`),
  }));

  const sections = [{
    type: "section", key: "domain:general", title: "General settings", open: true,
    items: [
      { type: "range", caption: "Domain range", unit: "len",
        order: ["center", "size", "min", "max"], rows: rangeRows },
      { type: "table", caption: "fdtd solver control",
        cols: [{ label: "time", unit: "fs" }, { label: "CFL" },
               { label: "auto shutoff" }],
        rows: [{
          cells: [
            { kind: "num", path: "domain.total_time", value: d.total_time,
              unit: "time", onSet: (v) => commit("total_time", py(v)) },
            { kind: "num", path: "domain.cfl", value: d.cfl,
              onSet: (v) => commit("cfl", py(v)) },
            { kind: "num", path: "domain.auto_shutoff", value: d.auto_shutoff,
              hint: "0 = off (field-energy ratio; recommended 1e-6)",
              onSet: (v) => commit("auto_shutoff", py(v)),
              onClear: () => commit("auto_shutoff", "0.0") },
          ],
        }],
      },
    ],
  }];

  /* 2. Base mesh —— Manual（均匀步长）/ Auto（auto_grid 段表）互斥 */
  const gridItems = [
    { type: "row", label: "Grid mode", controls: [{
        kind: "select", value: hasTable ? "Auto" : "Manual",
        options: ["Manual", "Auto"],
        confirm: hasTable
          ? "Switching to Manual clears the auto-mesh segment tables. Continue?"
          : null,
        onPick: (v) => {
          if (v === "Auto") {
            if (!hasTable) exec("genie.auto_grid()", { echo: true }).catch(() => {});
          } else if (hasTable) {
            exec("genie.set_named('fdtd', x_spacing=None, y_spacing=None, "
              + "z_spacing=None)", { echo: false }).catch(() => {});
          }
        } }] },
  ];
  if (hasTable) {
    gridItems.push({ type: "row", label: "Mesh level",
      controls: [{ kind: "text", text: "not implemented yet" }] });
  } else {
    gridItems.push({ type: "table", caption: "Base mesh step",
      cols: [{ label: "dx", unit: "µm" }, { label: "dy", unit: "µm" },
             { label: "dz", unit: "µm" }],
      rows: [{ cells: ["x", "y", "z"].map((a) => ({
        kind: "num", path: `domain.d${a}`, value: d[`d${a}`], unit: "len",
        onSet: (v) => commit(`d${a}`, py(v)),
      })) }] });
  }
  sections.push({ type: "section", key: "domain:grid", title: "Base mesh",
    open: true, items: gridItems });

  if (hasTable) {
    const items = ["x", "y", "z"].filter((a) => d[`${a}_spacing`]).map((a) => ({
      type: "row", label: `${a}_spacing`,
      controls: [{ kind: "text",
        text: `${d[`${a}_spacing`].length} segments · ${_fmtSeg(d[`${a}_spacing`])}` }],
    }));
    items.push({ type: "note",
      text: "Segment tables are the nonuniform base mesh (mutually exclusive "
        + "with dx); switching to Manual clears them." });
    sections.push({ type: "section", key: "domain:segments",
      title: "Auto mesh · segments", open: false, items });
  }

  /* 4. Boundary —— 顺序固定：Boundary type → Bloch → PML（2026-11-25 站长拍） */
  const bItems = [];
  let blochItem = null;
  const blochFaces = DOMAIN_FACES.filter((f) => kindOf(f) === "bloch");
  if (blochFaces.length) {
    const b0 = bnd[blochFaces[0]] || {};
    const k0 = Array.isArray(b0.k) ? b0.k : [];
    /* Bloch 的 k / from_source 是**全局量**（对所有 bloch 面同时成立，
     * 2026-11-25 站长拍）：读第一个 bloch 面，写广播到全部 bloch 面。 */
    const commitBloch = (patch) => {
      const cur = d.boundary || {};
      const nb = {};
      for (const kk of Object.keys(cur)) nb[kk] = Object.assign({}, cur[kk]);
      for (const f of blochFaces) nb[f] = Object.assign({}, nb[f] || {}, patch);
      d.boundary = nb;
      commit("boundary", py(nb));
    };
    blochItem = {
      type: "table", caption: "Bloch",
      cols: [{ label: "from source" },
             { label: "kx", unit: "rad/m" }, { label: "ky", unit: "rad/m" },
             { label: "kz", unit: "rad/m" }],
      rows: [{
        cells: [
          { kind: "select",
            path: `domain.boundary.${blochFaces[0]}.from_source`,
            value: b0.from_source === true ? "yes" : "no",
            options: ["no", "yes"],
            onSet: (v) => commitBloch({ from_source: v === "yes" }) },
          ...["0", "1", "2"].map((i) => ({
            kind: "num", group: true,
            path: `domain.boundary.${blochFaces[0]}.k.${i}`, value: k0[i],
          })),
        ],
        onRow: (v) => commitBloch({ k: v }),
      }],
    };
  }

  const hasPml = DOMAIN_FACES.some((f) => kindOf(f) === "pml");
  const pmlRows = [
    ["layers", "layers"], ["κ", "kappa_max"], ["order", "order"],
  ].map(([label, key]) => ({
    label,
    cells: DOMAIN_FACES.map((f) => {
      const isPml = kindOf(f) === "pml";
      const b = bnd[f] || {};
      return {
        kind: "num", path: `domain.boundary.${f}.${key}`,
        /* 值来自模型（2026-11-25）：pml 面在切换那一刻由 kernel 播种 layers/κ/order */
        value: isPml ? b[key] : undefined,
        disabled: !isPml,
        aria: `${f} pml ${label}`,
        hint: isPml ? `${f} · pml ${label}` : `${f} is not PML`,
        onSet: (v) => commitFace(f, Object.assign({}, bnd[f] || {}, { [key]: v })),
      };
    }),
  }));

  /* Kind 表：行就是"边界类型"本身，不再要左边的 "Kind" 行头列
   * （2026-11-25 站长拍）；列头 = 六面。 */
  const faceCols = DOMAIN_FACES.map((f) => ({ label: f.replace("_", " ") }));
  bItems.push({
    type: "table", caption: "Boundary type", cols: faceCols,
    rows: [{ cells: DOMAIN_FACES.map((f) => ({
      kind: "select", path: `domain.boundary.${f}.kind`, value: kindOf(f),
      options: ["", "pec", "pmc", "periodic", "pml", "bloch"],
      unsetLabel: "—",          /* 6 面挤在 300px：空值用破折号，不写长词 */
      aria: `${f} boundary type`,
      onSet: (v) => setFaceKind(f, v),
    })) }],
  });
  if (blochItem) bItems.push(blochItem);
  /* PML 配置单独一张表：只在有 pml 面时出现，行 layers / κ / order。 */
  if (hasPml) {
    bItems.push({ type: "table", caption: "PML", cols: faceCols,
      rows: pmlRows });
  }

  sections.push({ type: "section", key: "domain:boundary", title: "Boundary",
    open: true, items: bItems });

  return { sections };
}

/* ══════════════════ mode 卡（2026-11-25 站长拍）══════════════════
 * 与 fdtd 同一副面板：节 → 题目 → 控件/表；范围四列公共件；Boundary
 * type → Bloch → PML；Auto/Manual 基础网格。数据来自 mode 求解器自己的
 * 包络（cfg.solvers 里 kind=mode），不是扁平 config。 */
export function MODE_SCHEMA(cfg, commit, solverName) {
  const sv = ((cfg && cfg.solvers) || []).find((s) => s && s.kind === "mode") || {};
  const name = solverName || sv.name || "mode";
  const mc = sv.config || {};
  const d = mc.domain || {};
  const axis = ["x", "y", "z"].includes(d.axis) ? d.axis : "z";
  const [t1, t2] = MODE_TRANSVERSE[axis];
  const edges = MODE_FACES[axis];
  const bo = mc.boundary || {};
  const kindOf = (f) => (bo[f] || {}).kind || "";
  const range = (a) => {
    const r = d[`${a}_range`];
    if (!Array.isArray(r)) return null;
    const lo = Number(r[0]), hi = Number(r[1]);
    return isFinite(lo) && isFinite(hi) ? [lo, hi] : null;
  };
  const commitFace = (f, face) => {
    const cur = mc.boundary || {};
    const nb = {};
    for (const k of Object.keys(cur)) nb[k] = Object.assign({}, cur[k]);
    nb[f] = face;
    mc.boundary = nb;
    commit("boundary", py(nb));
  };
  const setFaceKind = (f, kind) => {
    const face = { kind };
    if (BOUNDARY_DEFAULTS[kind]) Object.assign(face, BOUNDARY_DEFAULTS[kind]);
    commitFace(f, face);
  };
  const hasSpacing = [t1, t2].some((a) => Array.isArray(d[`${a}_spacing`]));
  const nRef = (v) => (typeof v === "string" ? v : (v == null ? "" : String(v)));

  const IDX = { x: 0, y: 1, z: 2 };
  /* 换轴 = 布局重排（2026-11-25 站长拍，**面板规范**）：一条 set_named 同时
   * 写下新轴与整套默认值——传播轴清窗口/网格，两条横轴回 ±1 µm 与 0.05 µm；
   * 失效的旋转轴清成"无旋转"；边界换成新轴四缘（已有 kind 保留，缺席 pec）。 */
  const switchAxis = (v) => {
    /* 换轴 = 布局重排（2026-11-26 站长拍，**面板规范**）：整套默认值来自
     * defaults.js——新轴清窗口/网格、横轴回 ±1 µm 与 0.05 µm、边界换四缘。 */
    exec("genie.set_named(" + JSON.stringify(name) + ", "
      + fieldsArg(modeSeed(v)) + ")", { echo: false }).catch(() => {});
    if (mc.rotate_axis && !MODE_TRANSVERSE[v].includes(mc.rotate_axis)) {
      exec("genie.set_named(" + JSON.stringify(name)
        + ", rotate_axis=None, rotate_angle=0.0, rotate_radius=0.0)",
        { echo: false }).catch(() => {});
    }
  };

  const sections = [{
    type: "section", key: "mode:general", title: "General settings", open: true,
    items: [
      { type: "row", label: "Propagation axis", controls: [{
          kind: "select", value: axis, options: ["x", "y", "z"], rerender: true,
          onPick: (v) => switchAxis(v) }] },
      { type: "range", caption: "Domain range", unit: "len",
        order: ["center", "size", "min", "max"],
        rows: ["x", "y", "z"].map((a) => (a === axis
          ? { label: a, centerOnly: true, position: d.position,
              onCenter: (v) => commit("position", py(v)) }
          : { label: a, value: range(a),
              onSet: (v) => commit(`${a}_range`,
                `(${py(v[0])}, ${py(v[1])})`) })) },
      { type: "table", caption: "mode solver control",
        cols: [{ label: "Wavelength", unit: "µm" }, { label: "Modes" },
               { label: "Search index" }],
        rows: [{ cells: [
          { kind: "str", path: "mode.wavelength",
            value: wavelengthText(mc.wavelength),
            hint: "µm; comma-separated for several wavelengths (ascending)",
            validate: wavelengthError,
            onSet: (s) => commit("wavelength", wavelengthLiteral(s)) },
          { kind: "num", path: "mode.n_modes", value: mc.n_modes,
            onSet: (v) => commit("n_modes", py(v)) },
          /* 空 = 内核取截面最大折射率（n_ref=None），不需要 auto 状态 */
          { kind: "num", path: "mode.n_ref", value: mc.n_ref,
            hint: "empty = largest index in the cross-section (kernel default)",
            onSet: (v) => commit("n_ref", py(v)),
            onClear: () => commit("n_ref", "None") },
        ] }] },
      { type: "table", caption: "Rotation",
        cols: [{ label: "Axis" }, { label: "Angle" },
               { label: "Radius", unit: "µm" }],
        rows: [{ cells: [
          /* axis 默认 none；选了轴才把 angle/radius 落成 0（2026-11-25 站长拍） */
          { kind: "select", path: "mode.rotate_axis", value: mc.rotate_axis || "",
            options: ["", t1, t2], rerender: true,
            onSet: (v) => {
              if (!v) {
                exec("genie.set_named(" + JSON.stringify(name) + ", rotate_axis=None,"
                  + " rotate_angle=None, rotate_radius=None)",
                  { echo: false }).catch(() => {});
              } else {
                exec("genie.set_named(" + JSON.stringify(name) + ", rotate_axis="
                  + py(v) + ", rotate_angle=0.0, rotate_radius=0.0)",
                  { echo: false }).catch(() => {});
              }
            } },
          { kind: "num", path: "mode.rotate_angle",
            value: mc.rotate_axis ? mc.rotate_angle : undefined,
            disabled: !mc.rotate_axis,
            hint: "degrees (right-hand rule; |angle| < 90)",
            onSet: (v) => commit("rotate_angle", py(v)) },
          { kind: "num", path: "mode.rotate_radius",
            value: mc.rotate_axis ? mc.rotate_radius : undefined,
            disabled: !mc.rotate_axis, unit: "len",
            hint: "rotation axis offset from the plane centre",
            onSet: (v) => commit("rotate_radius", py(v)) },
        ] }] },
    ],
  }];

  /* Base mesh：Manual（每横轴一个 d）/ Auto（auto_grid 的段表） */
  const gridItems = [{ type: "row", label: "Grid mode", controls: [{
      kind: "select", value: hasSpacing ? "Auto" : "Manual",
      options: ["Manual", "Auto"],
      confirm: hasSpacing
        ? "Switching to Manual clears the auto-mesh segment tables. Continue?"
        : null,
      onPick: (v) => {
        if (v === "Auto") {
          if (!hasSpacing)
            exec("genie.auto_grid(solver=" + JSON.stringify(name) + ")",
              { echo: true }).catch(() => {});
        } else if (hasSpacing) {
          const clear = [t1, t2].map((a) => `${a}_spacing=None`).join(", ");
          exec("genie.set_named(" + JSON.stringify(name) + ", " + clear + ")",
            { echo: false }).catch(() => {});
        }
      } }] }];
  if (hasSpacing) {
    gridItems.push({ type: "row", label: "Mesh level",
      controls: [{ kind: "text", text: "not implemented yet" }] });
  } else {
    gridItems.push({ type: "table", caption: "Base mesh step",
      cols: [t1, t2].map((a) => ({ label: `d${a}`, unit: "µm" })),
      rows: [{ cells: [t1, t2].map((a) => ({
        kind: "num", path: `mode.domain.d${a}`, value: d[`d${a}`], unit: "len",
        onSet: (v) => commit(`d${a}`, py(v)),
      })) }] });
  }
  sections.push({ type: "section", key: "mode:mesh", title: "Base mesh",
    open: true, items: gridItems });

  if (hasSpacing) {
    const items = [t1, t2].filter((a) => Array.isArray(d[`${a}_spacing`]))
      .map((a) => ({ type: "row", label: `${a}_spacing`,
        controls: [{ kind: "text",
          text: `${d[`${a}_spacing`].length} segments · `
            + `${_fmtSeg(d[`${a}_spacing`])}` }] }));
    items.push({ type: "note",
      text: "Segment tables are the nonuniform base mesh (mutually exclusive "
        + "with d); switching to Manual clears them." });
    sections.push({ type: "section", key: "mode:segments",
      title: "Auto mesh · segments", open: false, items });
  }

  /* Boundary：4 条横截面边缘 → Boundary type → Bloch → PML */
  const bItems = [];
  let blochItem = null;
  const blochFaces = edges.filter((f) => kindOf(f) === "bloch");
  if (blochFaces.length) {
    const b0 = bo[blochFaces[0]] || {};
    const k0 = Array.isArray(b0.k) ? b0.k : [];
    const commitBloch = (patch) => {
      const cur = mc.boundary || {};
      const nb = {};
      for (const kk of Object.keys(cur)) nb[kk] = Object.assign({}, cur[kk]);
      for (const f of blochFaces) nb[f] = Object.assign({}, nb[f] || {}, patch);
      mc.boundary = nb;
      commit("boundary", py(nb));
    };
    blochItem = {
      type: "table", caption: "Bloch",
      /* 2D 问题：Bloch k 只有截面内两个分量（传播轴分量恒 0，不提供） */
      cols: [{ label: "from source" },
             { label: `k${t1}`, unit: "rad/m" },
             { label: `k${t2}`, unit: "rad/m" }],
      rows: [{
        cells: [
          { kind: "select",
            path: `mode.boundary.${blochFaces[0]}.from_source`,
            value: b0.from_source === true ? "yes" : "no",
            options: ["no", "yes"],
            onSet: (v) => commitBloch({ from_source: v === "yes" }) },
          ...[t1, t2].map((a) => ({
            kind: "num", group: true,
            path: `mode.boundary.${blochFaces[0]}.k.${IDX[a]}`,
            value: k0[IDX[a]],
          })),
        ],
        onRow: (v) => {
          const kk = [0, 0, 0];
          kk[IDX[t1]] = v[0];
          kk[IDX[t2]] = v[1];
          commitBloch({ k: kk });
        },
      }],
    };
  }
  const hasPml = edges.some((f) => kindOf(f) === "pml");
  const pmlRows = [["layers", "layers"], ["κ", "kappa_max"], ["order", "order"]]
    .map(([label, key]) => ({
      label,
      cells: edges.map((f) => ({
        kind: "num", path: `mode.boundary.${f}.${key}`,
        value: kindOf(f) === "pml" ? (bo[f] || {})[key] : undefined,
        disabled: kindOf(f) !== "pml",
        aria: `${f} pml ${label}`,
        hint: kindOf(f) === "pml" ? `${f} · pml ${label}` : `${f} is not PML`,
        onSet: (v) => commitFace(f, Object.assign({}, bo[f] || {}, { [key]: v })),
      })),
    }));
  bItems.push({
    type: "table", caption: "Boundary type",
    cols: edges.map((f) => ({ label: f.replace("_", " ") })),
    rows: [{ cells: edges.map((f) => ({
      kind: "select", path: `mode.boundary.${f}.kind`, value: kindOf(f),
      options: ["", "pec", "pmc", "periodic", "pml", "bloch"],
      unsetLabel: "—",
      aria: `${f} boundary type`,
      onSet: (v) => setFaceKind(f, v),
    })) }],
  });
  if (blochItem) bItems.push(blochItem);
  if (hasPml) bItems.push({ type: "table", caption: "PML", cols:
    edges.map((f) => ({ label: f.replace("_", " ") })), rows: pmlRows });
  sections.push({ type: "section", key: "mode:boundary", title: "Boundary",
    open: true, items: bItems });

  return { sections };
}

/* 波长：显示文本（µm）、解析、字面量。单值 = float，多值 = 升序 list。 */
function wavelengthText(v) {
  if (Array.isArray(v)) return v.map((x) => (x * 1e6).toPrecision(6)).join(", ");
  if (typeof v === "number") return (v * 1e6).toPrecision(6);
  return "";
}
function wavelengthList(s) {
  const toks = String(s).split(",").map((x) => x.trim()).filter(Boolean);
  const out = [];
  for (const tk of toks) {
    const m = /^([0-9.]+(?:[eE][-+]?[0-9]+)?)\s*(um|nm|m)?$/.exec(tk);
    if (!m) return null;
    let x = Number(m[1]);
    if (!isFinite(x)) return null;
    const u = m[2];
    if (u === "nm") x *= 1e-9;
    else if (u === "m") x = x;
    else x *= 1e-6;                 /* 缺省单位 = µm */
    if (x <= 0) return null;
    out.push(x);
  }
  return out.length ? out : null;
}
function wavelengthError(s) {
  const xs = wavelengthList(s);
  if (!xs) return "expect µm values like 1.55 or 1.5, 1.55";
  for (let i = 1; i < xs.length; i++) {
    if (xs[i] <= xs[i - 1]) return "wavelengths must be ascending";
  }
  return null;
}
function wavelengthLiteral(s) {
  const xs = wavelengthList(s);
  if (!xs) return "None";
  return xs.length === 1 ? py(xs[0]) : py(xs);
}


/* ══════════════════ FDTD ▸ mesh（2026-11-25 站长拍）═════════════════
 * 与 fdtd/mode 同一副面板：General settings = Domain range（四列）+ Refine
 * method（factor | absolute）+ Refinement（一行 x/y/z）+ Growth。 */
let MESH_METHOD = null;
let SIM_UNIT = "frequency";      /* 视图偏好：factor | absolute（不落模型） */

export function MESH_SCHEMA(cfg, commit, m) {
  const useAbs = MESH_METHOD
    ? MESH_METHOD === "absolute"
    : ["x", "y", "z"].some((a) => m[`d${a}`] != null);
  const method = useAbs ? "absolute" : "factor";
  const cell = (a) => (useAbs
    ? { kind: "num", path: `mesh.${a}.d`, value: m[`d${a}`], unit: "len",
        hint: "绝对步长（≤ 该轴基础步长）",
        onSet: (v) => commit(`d${a}`, py(v)),
        onClear: () => commit(`d${a}`, "None") }
    : { kind: "num", path: `mesh.${a}.factor`, value: m[`factor_${a}`],
        hint: "factor ≥ 1（相对基础步长；1 = 不变）",
        onSet: (v) => commit(`factor_${a}`, py(v)),
        onClear: () => commit(`factor_${a}`, "None") });
  return { sections: [{
    type: "section", key: "mesh:general", title: "General settings", open: true,
    items: [
      { type: "range", caption: "Range", unit: "len",
        order: ["center", "size", "min", "max"],
        rows: ["x", "y", "z"].map((a) => ({
          label: a,
          value: Array.isArray(m[`${a}_range`]) ? m[`${a}_range`] : null,
          onSet: (v) => commit(`${a}_range`, `(${py(v[0])}, ${py(v[1])})`),
        })) },
      /* 方法与渐变率放在同一张表（2026-11-25 站长拍）：都是"怎么加密"的控制量 */
      { type: "table", caption: "Mesh control",
        cols: [{ label: "Refine method" }, { label: "Growth rate" }],
        rows: [{ cells: [
          { kind: "select", value: method, options: ["factor", "absolute"],
            rerender: true,
            onPick: (v) => {
              MESH_METHOD = v;
              if (v === "absolute") {
                /* 切到绝对步长：空轴补 50 nm（已有值的轴不动） */
                for (const a of ["x", "y", "z"]) {
                  if (m[`d${a}`] == null) commit(`d${a}`, py(5e-8));
                }
              }
            } },
          { kind: "num", path: "mesh.growth_rate", value: m.growth_rate,
            hint: "过渡带渐变率（1.0 = 硬跳）",
            onSet: (v) => commit("growth_rate", py(v)) },
        ] }] },
      { type: "table", caption: useAbs ? "Refine step" : "Refine factor",
        cols: ["x", "y", "z"].map((a) => ({
          label: a, unit: useAbs ? "µm" : "" })),
        rows: [{ cells: ["x", "y", "z"].map(cell) }] },
    ],
  }] };
}

/* ══════════════════ FDTD ▸ soft source（2026-11-26 站长拍）══════════════
 * 与 fdtd/mode/mesh 同一副面板：General settings（时域四件套 + 幅度 + 种类）
 * → Placement（Center / Size 各一行 xyz）→ Polarization（实部/虚部 × xyz）。 */
export function SOURCE_SCHEMA(cfg, commit, s) {
  const vec = (key, fallback) => {
    const v = Array.isArray(s[key]) ? s[key] : fallback;
    return {
      cells: ["x", "y", "z"].map((_, k) => ({
        kind: "num", value: v[k] ?? 0, unit: "len",
        path: `source.${key}.${k}`,
        onSet: (val) => {
          const a = [v[0] ?? 0, v[1] ?? 0, v[2] ?? 0];
          a[k] = val;
          commit(key, py(a));
        },
      })),
    };
  };
  const pol = (Array.isArray(s.polarization) && Array.isArray(s.polarization[0]))
    ? s.polarization : [[1, 0], [0, 0], [0, 0]];
  /* 全局频段继承（2026-11-27）：选 global 时 freq/bw 显示解析值且只读。 */
  const sim = (cfg && cfg.simulation) || {};
  const bandOk = (sim.band_lo != null && sim.band_hi != null);
  const bound = s.freq_from === "global" && bandOk;
  const freqVal = bound ? (sim.band_lo + sim.band_hi) / 2 : s.freq;
  const bwVal = bound ? (sim.band_hi - sim.band_lo) : s.bandwidth;
  return { sections: [{
    type: "section", key: "src:general", title: "General settings", open: true,
    items: [
      /* 位置第一（2026-11-26 站长拍）：wire 字段仍叫 center */
      { type: "table", caption: "Position",
        cols: ["x", "y", "z"].map((a) => ({ label: a, unit: "µm" })),
        rows: [vec("center", [0, 0, 0])] },
      { type: "row", label: "Type", controls: [{
          kind: "select", value: s.kind || "electric",
          options: ["electric", "magnetic"], rerender: true,
          onPick: (v) => commit("kind", py(v)) }] },
      { type: "row", label: "Band", controls: [{
          kind: "select", value: s.freq_from === "global" ? "global" : "own",
          options: ["own", "global"], rerender: true,
          onPick: (v) => commit("freq_from", py(v)) }] },
      /* 复数直接输入（2026-11-26 站长拍）：xyz 三列，一格一个复数
       * （`1` / `2j` / `1-0.5j`），写回线格式的 [[re,im]×3]。 */
      { type: "table", caption: "Polarization",
        cols: ["x", "y", "z"].map((a) => ({ label: a })),
        rows: [{ cells: [0, 1, 2].map((k) => ({
          kind: "str", path: `source.polarization.${k}`,
          value: formatComplex(pol[k] ? pol[k][0] : 0, pol[k] ? pol[k][1] : 0),
          hint: "复数：1 / 2j / 1-0.5j",
          validate: (v) => (parseComplex(v) ? null
            : "复数形如 1、2j、1-0.5j"),
          onSet: (v) => {
            const c = parseComplex(v);
            if (!c) return;
            const m = [[0, 0], [0, 0], [0, 0]];
            for (let i = 0; i < 3; i++)
              for (let j = 0; j < 2; j++)
                m[i][j] = Number(pol[i] && pol[i][j]) || 0;
            m[k] = [c.re, c.im];
            commit("polarization", py(m));
          },
        })) }] },
    ],
  }, {
    type: "section", key: "src:wave", title: "Waveform & spectrum",
    open: true,
    items: [
      { type: "table", caption: "Spectrum",
        cols: [{ label: "freq", unit: "THz" }, { label: "bw", unit: "THz" }],
        rows: [{ cells: [
          bound
            ? { kind: "num", path: "source.freq", value: freqVal, unit: "freq" }
            : { kind: "num", path: "source.freq", value: s.freq, unit: "freq",
                onSet: (v) => commit("freq", py(v)) },
          bound
            ? { kind: "num", path: "source.bandwidth", value: bwVal, unit: "freq" }
            : { kind: "num", path: "source.bandwidth", value: s.bandwidth,
                unit: "freq", onSet: (v) => commit("bandwidth", py(v)) },
        ] }] },
      { type: "table", caption: "Timing",
        cols: [{ label: "delay", unit: "fs" }, { label: "phase", unit: "rad" },
               { label: "amp" }],
        rows: [{ cells: [
          { kind: "num", path: "source.delay", value: s.delay ?? 0,
            unit: "time", onSet: (v) => commit("delay", py(v)) },
          { kind: "num", path: "source.phase", value: s.phase ?? 0,
            onSet: (v) => commit("phase", py(v)) },
          { kind: "num", path: "source.amplitude", value: s.amplitude ?? 1,
            onSet: (v) => commit("amplitude", py(v)) },
        ] }] },
    ],
  }] };
}

/* ══════════════════ 全局 Simulation（2026-11-26 站长拍）══════════════════
 * 文档级（model.simulation）：source/monitor 可选继承它；提交时解析成具体值，
 * wire 没有这个字段。 */
export function GLOBAL_SCHEMA(cfg, setBand, sim) {
  const s = sim || {};
  const wl = SIM_UNIT === "wavelength";
  const band = (s.band_lo != null && s.band_hi != null)
    ? [s.band_lo, s.band_hi] : null;
  const pair = band ? (wl ? [C0 / band[1], C0 / band[0]] : band) : null;
  return { sections: [{
    type: "section", key: "sim:general",
    title: "Global source setting", open: true,
    items: [
      /* 一张 1×5 表（2026-11-27 站长拍）：首列 = 量（frequency/wavelength），
       * 后四列 = center/span/min/max 互算；真身 band_lo/band_hi（Hz）。 */
      { type: "range", caption: "Frequency/Wavelength", unit: wl ? "len" : "freq",
        order: ["center", "span", "min", "max"],
        lead: { kind: "select", value: wl ? "wavelength" : "frequency",
                options: ["frequency", "wavelength"], rerender: true,
                header: "", onPick: (v) => { SIM_UNIT = v; } },
        rows: [{ value: pair,
          onSet: (v) => {
            let lo, hi;
            if (wl) {
              if (!(v[0] > 0) || !(v[1] > 0)) return;
              lo = C0 / v[1]; hi = C0 / v[0];
            } else { lo = v[0]; hi = v[1]; }
            if (hi > lo) setBand(lo, hi);
          } }] },
    ],
  }] };
}

export const FormPanel = {
  container: null,
  cfg: null,
  current: "domain",    // 当前打开的节
  currentSub: -1,       // nav 子项高亮的卡片下标（-1 = 无）

  init(containerId) {
    this.container = document.getElementById(containerId);
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

  _rerender() {
    this._render();
  },

  _render() {
    const c = this.container;
    c.textContent = "";
    const panel = {
      domain: "domain", mesh: "mesh", materials: "materials",
      geometry: "geometry", source: "source", monitor: "monitor", run: "run",
      mode: "mode", simulation: "simulation",
    }[this.current];
    if (!panel) return;

    /* 2026-10-21 用户定案：不要大标题与信息摘要——面板直接从内容开始。
     * 单例面板（Domain / Mesh / Settings）开头补一行「图标 + 名字」，与对象
     * 卡的卡片头同一刻度：没有对象可指认时，这行就是面板身份。 */
    const body = document.createElement("div");
    body.className = "pane-body";
    c.appendChild(body);
    if (this.only == null) {
      /* domain = fdtd 求解器卡片（2026-11-24 站长拍：选中 fdtd 出这张，
       * dx/dy/dz 就在这里——domain 是求解器包络的单件，不单独成页）；
       * mode = mode 求解器卡片（与 fdtd 对称，2D）。2026-11-25：身份行
       * 只留名字（图标多余，删）。 */
      const ID = { domain: "Settings", mode: "Settings",
                   mesh: "Mesh", run: "Settings",
                   simulation: "Settings" }[this.current];
      if (ID) body.appendChild(this._paneId(ID));
    }

    switch (this.current) {
      case "domain": this._domain(body); break;
      case "mode": this._mode(body); break;
      case "mesh": this._mesh(body); break;
      case "materials": this._materials(body); break;
      case "geometry": this._geometry(body); break;
      case "source": this._source(body); break;
      case "monitor": this._monitor(body); break;
      case "run": this._run(body); break;
      case "simulation": this._simulation(body); break;
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

  /* 路径归一(08-31cd): 内核错误路径含中间类型段
   * (geometry.0.box.x_range), 表单字段路径没有 (geometry.0.x_range.0);
   * 剥掉中间段才能前缀匹配标红。 */
  _normErrPath(p) {
    return String(p).replace(
      /^(geometry|source|monitor|materials)\.\d+\.\w+\.(.+)$/,
      (m, sec, rest) => sec + "." + rest);
  },

  /* ══════════════════ DOM 小工具 ══════════════════ */

  _el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  },

  /* 面板身份行（2026-10-21 定案；2026-11-25 去图标）：单例面板开头补一行
   * 名字（与对象卡同刻度），图标删——多余装饰，名字本身就是身份。 */
  _paneId(name) {
    const row = this._el("div", "pane-id");
    row.appendChild(this._el("span", "pane-id-name", name));
    return row;
  },

  _paneEmpty(iconKey, title, hint) {
    const wrap = this._el("div", "empty-state pane-empty-state");
    const ic = this._el("span", "empty-ic");
    try { ic.appendChild(previewEl(iconKey)); } catch { /* 图标缺失不拦渲染 */ }
    wrap.appendChild(ic);
    wrap.appendChild(this._el("div", "empty-title", title));
    wrap.appendChild(this._el("div", "empty-hint", hint));
    return wrap;
  },

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

  /* 只读视图（L-35 拔线后）：值/单位换算只做展示；写腿随 M4 重接。
   * 2026-11-25：只读件不进 Tab 序列（tabIndex=-1），打开写腿只经 _edit()。 */
  _input(path, val, kind /* num|str */, hintKind) {
    const input = document.createElement("input");
    input.type = "text";
    input.readOnly = true;
    input.tabIndex = -1;
    if (path) input.dataset.path = path;
    this._tagField(input, path);
    input.dataset.kind = kind;
    if (kind === "num" || kind === "num3") input.inputMode = "decimal";
    const unit = FIELD_UNITS[hintKind] || null;
    /* dataset.unit 只标"框内真有后缀"的单位——ang（THz(ω=2πf)）没有后缀，
     * 标了会让 _row 把标签里的单位也剥掉，单位就丢了。 */
    if (unit && unit.suffix) input.dataset.unit = hintKind;
    input.value = _dispValue(val, unit);
    const f = parseFloat(input.value);
    if (unit && isFinite(f)) {
      const si = parseFloat(unit.toSI(f).toPrecision(15));
      input.title = `${f} ${unit.label} → cfg ${si} ${unit.si}`;
    }
    const wrap = document.createElement("span");
    wrap.className = "inwrap";
    wrap.appendChild(input);
    if (unit && unit.suffix)
      wrap.appendChild(this._el("span", "unit", unit.suffix));
    return wrap;
  },

  _select(path, val, options, emptyDeletes) {
    const sel = document.createElement("select");
    sel.disabled = true;                    /* L-35：写腿随 M4 重接 */
    if (path) sel.dataset.path = path;
    this._tagField(sel, path);
    sel.dataset.kind = "str";
    if (emptyDeletes) sel.dataset.emptyDeletes = "1";
    for (const o of options) {
      const opt = document.createElement("option");
      opt.value = o;
      opt.textContent = o === "" ? "(none)" : o;
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
    return sel;
  },

  /* 三态布尔选择（真/false/未设置=删除字段）——bool 走 JSON 线格式 */
  _boolSelect(path, val, label) {
    const sel = document.createElement("select");
    sel.disabled = true;                    /* L-35：写腿随 M4 重接 */
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
    return sel;
  },

  /* 材料场编辑器（L-35 拔线后 = 只读；写腿随 M4）。2026-11-25 收编：
   * 形态与值行都走 _row（表单行唯一资产），不再手搓 .frow。 */
  _matEditor(path, mt, base, name) {
    const frag = document.createDocumentFragment();
    const AX = ["xx", "yy", "zz"];
    const mode = AX.some((a) => mt[base + "_" + a] !== undefined)
      ? "diag" : "iso";
    frag.appendChild(this._row("Shape", this._el("span", "tag",
      mode === "diag" ? "diagonal" : "isotropic")));
    if (mode === "iso") {
      frag.appendChild(this._row(name,
        this._input(path + "." + base, mt[base] ?? 1, "num")));
    } else {
      const bv = mt[base] ?? 1;
      const wraps = AX.map((a) => {
        const key = base + "_" + a;
        const inpWrap = this._input(path + "." + key, mt[key], "num");
        const inp = inpWrap.querySelector("input");
        inp.placeholder = String(bv);   /* 空槽 = 回落基值 */
        inp.dataset.emptyDeletes = "1"; /* 清空 = 删命名键 */
        inp.title = "empty = base value " + bv;
        inp.setAttribute("aria-label", name + " " + a);
        return inpWrap;
      });
      frag.appendChild(this._row(name, ...wraps));
    }
    return frag;
  },

  /* 字段序号（label for/id 配对用，2026-09-02 审查修复 a11y） */
  _fldSeq: 0,
  _msecOpen: {},      // 折叠组 open 状态（key 持久，rerender 不复位）
  _poleOpen: {},      // 单 pole 折叠状态（两级折叠）

  /* 卡头身份名（2026 统一语言）：四个对象族（geometry/source/monitor/materials）
   * 共用同一可编辑 name 输入，写回 cfg.<list>.<i>.name；复用 `_input` 的
   * 写回/onChange 链（→ 树/F2/3D label 同步）。placeholder 兜底未命名
   * （沿用原 card-title 的 "type #n" 回退），空名不写死到 cfg。 */
  _headName(path, value, placeholder, ariaLabel) {
    /* 身份名只读：name 是身份不是字段（改名 = 断引用，见上）；要换名就
     * 重建对象（或脚本里删+建）。 */
    const span = this._el("span", "card-name card-name-ro",
                          value || placeholder || "(unnamed)");
    span.title = "Rename in the tree (click to select, then click the name)";
    span.setAttribute("aria-label", ariaLabel || (value || "name") + " name");
    return span;
  },

  /* 渲染风卡头缩略图（previews.js R2 统一）：geometry/source/monitor 共用，
   * 包一层 .obj-preview span；svg 自身 aria-hidden（语义由卡头文本承担）。 */
  _preview(type) {
    const pv = this._el("span", "obj-preview");
    pv.appendChild(previewEl(type));
    return pv;
  },

  _delBtn(listPath, index, label) {
    /* L-35：删除腿已拔（随 M4 重接）——渲染为空，调用点原位保留签名。 */
    return null;
  },

  _matNames() {
    return (this.cfg.materials || []).map((m) => m.name).filter(Boolean);
  },

  /* fdtd 域卡：内容 = DOMAIN_SCHEMA（模块级数据），这里只剩接线。 */
  _simulation(body) {
    /* 全局频段卡：真身 band_lo/band_hi（Hz）；显示单位/写法是 GUI 的事。 */
    const setBand = (lo, hi) => {
      exec("genie.set_named('simulation', band_lo=" + py(lo)
        + ", band_hi=" + py(hi) + ")", { echo: false }).catch(() => {});
    };
    const sim = (this.cfg && this.cfg.simulation) || {};
    this.renderPage(GLOBAL_SCHEMA(this.cfg || {}, setBand, sim), body);
  },

  _domain(body) {
    const commit = (field, literal) => {
      exec("genie.set_named('fdtd', " + field + "=" + literal + ")",
        { echo: false }).catch(() => {});
    };
    this.renderPage(DOMAIN_SCHEMA(this.cfg || {}, commit), body);
  },

  /* 输入框 → SI 数（裸数按字段单位换算；带后缀 0.5um 后缀优先）；
   * 解析不了返回 null——调用方据此不发命令（半截输入不惊动 worker）。 */
  /* 表格单元格输入（基元，住面板；kit 的 _ctl 通过 this 调）：
   * 显示按 unitKind 换友好单位，读回给调用方 SI。默认只读。 */
  _mtxInput(value, unitKind) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.inputMode = "decimal";
    inp.readOnly = true;
    inp.tabIndex = -1;                 /* 只读格不进 Tab；可编辑由 _edit() 打开 */
    inp.className = "mtx-in";
    inp.value = _dispValue(value, FIELD_UNITS[unitKind] || null);
    inp.dataset.unitKind = unitKind || "";
    return inp;
  },
  /* 单位表入口：kit 不认识 FIELD_UNITS（不在它那边），经这里取。 */
  _fieldUnit(kind) { return FIELD_UNITS[kind] || null; },
  /* 值显示入口（同理）：SI → 友好单位字符串。 */
  _dispVal(kind, v) { return _dispValue(v, FIELD_UNITS[kind] || null); },

  _numFromInput(inp, unit) {
    const t = String(inp && inp.value != null ? inp.value : "").trim();
    if (!t) return null;
    const suf = parseSuffixed(t);
    if (!isNaN(suf)) return suf;
    const f = parseFloat(t);
    if (!isFinite(f)) return null;
    return unit ? unit.toSI(f) : f;
  },

  /* mode 卡片（2026-11-24 站长拍：与 fdtd 卡片对称，2D）：
   * 波导截面（轴/位置/两横轴窗口/背景 n）/ 基础格距（两横轴 d*，传播轴
   * 禁止网格）/ 边界·四缘（横截面四真轴边）/ 求解（λ、n_modes、n_ref）。
   * 数据源 = 快照 solvers[].config 里 mode 槽自己的包络——GUI 压平键只
   * 装 fdtd，此卡片不读压平键。mode 无 source/monitor（本征问题）。 */
  _mode(body) {
    const sv = ((this.cfg && this.cfg.solvers) || [])
      .find((s) => s && s.kind === "mode") || {};
    const name = sv.name || "mode";
    const commit = (field, literal) => {
      exec("genie.set_named(" + JSON.stringify(name) + ", " + field + "="
        + literal + ")", { echo: false }).catch(() => {});
    };
    this.renderPage(MODE_SCHEMA(this.cfg || {}, commit, name), body);
  },

  _mesh(body) {
    /* mesh 卡片 = MESH_SCHEMA + kit（2026-11-25）：每项一张卡，卡内是标准节。
     * 地址 = <当前 fdtd 名>.<mesh 名>（改名走树）。 */
    const sv = ((this.cfg && this.cfg.solvers) || [])
      .find((x) => (x.kind || "fdtd") === "fdtd");
    const solver = (sv && sv.name) || "fdtd";
    for (const [m, i] of this._items(this.cfg.mesh || [])) {
      const addr = solver + "." + m.name;
      const commit = (field, literal) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", " + field + "="
          + literal + ")", { echo: false }).catch(() => {});
      };
      const wrap = this._el("div", "pane-body");
      this.renderPage(MESH_SCHEMA(this.cfg || {}, commit, m), wrap);
      body.appendChild(this._card(
        [this._el("span", "pane-id-name", "Settings")], wrap));
    }
  },

  /* 材料「显示色」行（L-35 拔线后 = 只读展示当前色）。 */
  _colorRow(mt, path) {
    const field = this._el("span", "color-field");
    const autoHex = "#" + (typeof matColor === "function" ? matColor(mt.name) : 0x8E9BB4)
      .toString(16).padStart(6, "0");
    const norm = (v) => (typeof v === "string" && /^#?[0-9a-fA-F]{6}$/.test(v.trim())
      ? "#" + v.trim().replace(/^#/, "") : null);
    const cur = norm(mt.color) || autoHex;
    const swatch = this._el("span", "color-hex", cur.toUpperCase());
    swatch.style.background = cur.toLowerCase();
    swatch.title = "Color of this material in the 3D view (editing returns with M4)";
    field.appendChild(swatch);
    return this._row("Display color", field);
  },

  _materials(body) {
    const list = this.cfg.materials || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("materials", "No materials yet",
        "Add them in the script (rewiring pending)"));
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
        (mt.name || "material " + (i + 1)) + " name"));
      const chip = this._el("span", "tag type", mt.type || "material");
      chip.title = "Material type (decided by the toolbar button / script)";
      headChildren.push(chip);
      headChildren.push(this._delBtn("materials", i));
      /* 显示色（2026-10-21）：材料节点上的 color 字段——只影响 3D 视图的
       * 材质色，与脚本 `.color('#RRGGBB')` 同一字段（不是 GUI 私有态）。 */
      inner.appendChild(this._colorRow(mt, `materials.${i}`));
      if (mt.type === "const") {
        const bE = this._msec("mat" + i + ":eps", "ε · permittivity", false);
        inner.appendChild(bE.head); inner.appendChild(bE.body);
        bE.body.appendChild(this._matEditor(`materials.${i}`, mt, "eps", "ε"));

        const bM = this._msec("mat" + i + ":mu", "μ · permeability", true);
        inner.appendChild(bM.head); inner.appendChild(bM.body);
        bM.body.appendChild(this._matEditor(`materials.${i}`, mt, "mu", "μ"));
      } else if (mt.type === "lorentz") {
        /* 存量 bug 修正（2026-11-25）：原 `Array.isArray(poles)` 自引用
         * （TDZ ReferenceError），lorentz 卡一直渲染不出来——真源是
         * cfg 材料节点自己的 poles / mu_poles。 */
        const poles = Array.isArray(mt.poles) ? mt.poles : [];
        const muPoles = Array.isArray(mt.mu_poles) ? mt.mu_poles : [];

        const sc = (v) => (typeof v === "number" && isFinite(v) ? v : null);

        /* 单 pole 渲染（含两级折叠：可收起整卡） */
        const poleItem = (listKey, pl, j) => {
          const P = `materials.${i}.${listKey}.${j}`;
          const isAx = typeof pl.axis === "string";
          const shape = isAx ? "axis" : "iso";
          const row = document.createDocumentFragment();

          row.appendChild(this._row("Shape", this._el("span", "tag",
            shape === "axis" ? "axial" : "isotropic")));

          const kindSel = this._select(P + ".kind", pl.kind, ["lorentz", "drude"]);
          row.appendChild(this._row("kind", kindSel));

          const pk = "num";
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

          if (shape === "axis") {
            row.appendChild(this._row("axis",
              this._select(P + ".axis", pl.axis, ["x", "y", "z"])));
            row.appendChild(this._el("div", "note",
              "Axial pole: pinned to one coordinate axis (diagonal anisotropy); each "
              + "axis may have its own pole set. Isotropic = no axis "
              + "(expands to all three axes). Off-diagonal / complex "
              + "coupling was removed — it pumps energy in broadband "
              + "time domain (phase conjugation / half-cell mismatch)."));
          }
          const item = this._el("div", "poleitem");
          const ihead = this._el("div", "pi-head");
          const pchev = this._el("span", "pchev", "▾");
          pchev.setAttribute("aria-hidden", "true");   /* 装饰性折叠箭头（P2-3）：pi-head 无 widget 语义，字形不承载名称 */
          ihead.appendChild(pchev);
          ihead.appendChild(this._el("span", "tag",
            (listKey === "mu_poles" ? "μ" : "ε") + " pole " + (j + 1)));
          ihead.appendChild(this._el("span", "tag tagdim", pl.kind + " · " + shape));
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

        /* ＋pole 按钮已删（L-35，随 M4 重接） */

        /* G20：色散材料卡导出（逃生口，非库正典——库存 nk 数据，
         * pole 是按波段现算的暂态产物；文件名带波段/极点数可当溯源）。 */
        if (mt.type === "lorentz" && mt.name) {
          const dl = document.createElement("button");
          dl.className = "mini";
          dl.textContent = "Export .material.json";
          dl.title = "Download this material node (raw wire JSON, paste it back into materials)";
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
        const bE = this._msec("mat" + i + ":eps", "ε · permittivity (dispersive)", false);
        if (poles.length) bE.head.appendChild(this._el("span", "sec-badge", String(poles.length)));
        inner.appendChild(bE.head); inner.appendChild(bE.body);
        bE.body.appendChild(this._matEditor(`materials.${i}`, mt, "eps_inf", "ε∞"));
        const peList = this._el("div", "pole-list");
        poles.forEach((pl, j) => peList.appendChild(poleItem("poles", pl, j)));
        bE.body.appendChild(peList);
        if (!poles.length) bE.body.appendChild(this._el("div", "note",
          "No ε poles yet — stack independent poles as needed (isotropic / axial)"));

        const bM = this._msec("mat" + i + ":mu", "μ · permeability (dispersive)", true);
        if (muPoles.length) bM.head.appendChild(this._el("span", "sec-badge", String(muPoles.length)));
        inner.appendChild(bM.head); inner.appendChild(bM.body);
        bM.body.appendChild(this._matEditor(`materials.${i}`, mt, "mu_inf", "μ∞"));
        const pmList = this._el("div", "pole-list");
        muPoles.forEach((pl, j) => pmList.appendChild(poleItem("mu_poles", pl, j)));
        bM.body.appendChild(pmList);
        if (!muPoles.length) bM.body.appendChild(this._el("div", "note",
          "No μ poles yet — add when needed (μ is collapsed by default; most materials need none)"));
      }
      body.appendChild(this._card(headChildren, inner));
    });
  },

  _geometry(body) {
    const list = this.cfg.geometry || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("structure", "No geometry yet",
        "Add them in the script (rewiring pending)"));
      return;
    }
    this._items(list).forEach(([g, i]) => {
      const inner = document.createDocumentFragment();
      /* material 字段改为下拉(已有材料+可选, 08-31cb) */
      const matSel = document.createElement("select");
      matSel.dataset.path = `geometry.${i}.material`;
      this._tagField(matSel, matSel.dataset.path);
      matSel.dataset.kind = "str";
      matSel.style.cssText = this._select(null, null, []).style.cssText;
      const matNames = this._matNames();
      const curMat = g.material || "";
      const defOpt = document.createElement("option");
      defOpt.value = ""; defOpt.textContent = "(choose material)";
      matSel.appendChild(defOpt);
      for (const mn of matNames) {
        const o = document.createElement("option");
        o.value = mn; o.textContent = mn;
        if (mn === curMat) o.selected = true;
        matSel.appendChild(o);
      }
      if (curMat && !matNames.includes(curMat)) {
        const o = document.createElement("option");
        o.value = curMat; o.textContent = curMat + " (undefined)";
        o.selected = true;
        matSel.appendChild(o);
      }
      matSel.disabled = true;               /* L-35：写腿随 M4 重接 */
      /* 材料节（物理因果 RoundE）：几何身份=材料（名在卡头）；空间量入
       * 「位置与尺寸」，优先级（mesh 覆盖序）独立成节，与其余对象族同构。 */
      const sGeo1 = this._sec("Material", false);
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
      const sGeo2 = this._sec("Position & size", true, posCount);
      inner.appendChild(sGeo2.head); inner.appendChild(sGeo2.body);
      if (g.type === "box") {
        sGeo2.body.appendChild(this._rangeRow("x_range (µm)", `geometry.${i}.x_range`, g.x_range, "len"));
        sGeo2.body.appendChild(this._rangeRow("y_range (µm)", `geometry.${i}.y_range`, g.y_range, "len"));
        sGeo2.body.appendChild(this._rangeRow("z_range (µm)", `geometry.${i}.z_range`, g.z_range, "len"));
      } else if (g.type === "polygon_z" || g.type === "polygon_swept") {
        /* 顶点编辑器（L-35 拔线后只读展示）；写腿随 M4 重接 */
        const ta = document.createElement("textarea");
        ta.className = "verts-editor";
        ta.id = `f-geometry-${i}-vertices`;
        ta.name = ta.id;
        ta.setAttribute("autocomplete", "off");
        ta.spellcheck = false;
        ta.readOnly = true;
        ta.rows = Math.min(Math.max((g.vertices || []).length + 1, 4), 12);
        ta.value = (g.vertices || [])
          .map(p => (p[0] !== p[0] && p[1] !== p[1]) ? "nan, nan"
            : `${fmtUserNum(p[0] * 1e6)}, ${fmtUserNum(p[1] * 1e6)}`).join("\n");
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
      const sGeo3 = this._sec("Priority · mesh", true, 1);
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
        `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`, (g.name || `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`) + " name"));
      if (g.material) gHead.push(this._el("span", "tag", g.material));
      gHead.push(this._delBtn("geometry", i));
      body.appendChild(this._card(gHead, inner));
    });
  },

  _source(body) {
    /* source 卡 = SOURCE_SCHEMA + kit（2026-11-26）：地址 = <当前 fdtd 名>.<源名>。
     * 目前重建了 soft_source；其它类型等各自的 schema（先给明说，不留旧手搓）。 */
    const sv = ((this.cfg && this.cfg.solvers) || [])
      .find((x) => (x.kind || "fdtd") === "fdtd");
    const solver = (sv && sv.name) || "fdtd";
    const list = this.cfg.source || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("soft_source", "No sources yet",
        "Add from the FDTD ribbon: Sources ▸ Soft source"));
      return;
    }
    for (const [src, i] of this._items(list)) {
      if (src.type !== "soft_source") {
        body.appendChild(this._card(
          [this._el("span", "pane-id-name", "Settings")],
          this._el("div", "note",
            `${src.type}: card not rebuilt yet (source migration in progress)`)));
        continue;
      }
      const addr = solver + "." + src.name;
      const commit = (field, literal) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", " + field + "="
          + literal + ")", { echo: false }).catch(() => {});
      };
      const wrap = this._el("div", "pane-body");
      this.renderPage(SOURCE_SCHEMA(this.cfg || {}, commit, src), wrap);
      body.appendChild(this._card(
        [this._el("span", "pane-id-name", "Settings")], wrap));
    }
  },

  _monitor(body) {
    const list = this.cfg.monitor || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("mon_time", "No monitors yet",
        "Add them in the script (rewiring pending)"));
      return;
    }
    this._items(list).forEach(([m, i]) => {
      const inner = document.createDocumentFragment();
      /* 位置与尺寸（物理因果 RoundE：先定观测面/域，再定观测什么、怎么采样） */
      const sMon2 = this._sec("Position & size", true, 2);
      inner.appendChild(sMon2.head); inner.appendChild(sMon2.body);
      sMon2.body.appendChild(this._vec("center (µm)", `monitor.${i}.center`, m.center, 3, "len"));
      sMon2.body.appendChild(this._vec("size (µm)", `monitor.${i}.size`, m.size, 3, "len"));
      sMon2.body.appendChild(this._el("div", "note",
        "size slots: 0 = single cell · empty = stay null (whole axis) · "
        + "all three whole-domain = drop size and use the default."));
      /* 观测节（RoundE 统一）：场分量/频率采样/窗函数收进观测节，与物理因果一致 */
      const sMon1 = this._sec("Observe", false);
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
           (m.name || `monitor ${i + 1}`) + " name"),
         this._el("span", "tag type", m.type),
         this._delBtn("monitor", i)],
        inner));
    });
    /* ＋监视器 假添加行已删（L-35）：同「+ 源」，只写本地不生效。 */
  },


  _run(body) {
    // G18 单旋钮：时长唯一在 domain.total_time（domain 节编辑）——run 节
    // 只剩超时；卡数由系统定（G78），步数是 build 后派生量。
    const r = this.cfg.run || {};
    body.appendChild(this._row("timeout (s)",
      this._input("run.timeout_s", r.timeout_s ?? 3600, "num")));
    // 提交面板挂载点（app.js 常驻 DOM 移进来; 2026-08-31au 方案A：
    // 执行动作归 run 节，与生成代码的 genie.run(...) 同构）
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

/* 页面层（行/表/节/渲染器）住 views/kit.js：这里是卡片与数据投影。 */
Object.assign(FormPanel, KIT);
