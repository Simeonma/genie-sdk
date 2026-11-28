import { exec } from "../core/bus.js";
import { py, parseComplex, formatComplex, fdtdConfig } from "../modules/util.js";
import { GEO_TYPE_LABEL, previewEl } from "./previews.js";
import { matColor } from "./scene3d.js";
import { KIT } from "./kit.js";
import { C0 } from "../modules/constants.js";
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
export const DOMAIN_FIELDS = ["center", "size", "dx", "dy",
  "dz", "x_spacing", "y_spacing", "z_spacing", "total_time", "cfl",
  "auto_shutoff", "boundary"];
export const BOUNDARY_FIELDS = ["kind", "layers", "kappa_max", "order",
  "k", "from_source"];
const DOMAIN_FACES = ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"];

function _fmtSeg(t) {
  return t.map((s) => `${(s.d * 1e9).toPrecision(3)}nm`).join(" | ");
}

/* mode 卡覆盖的字段（测试按 ModeDomainSpec / ModeConfigSpec 反射校验）。 */
export const MODE_FIELDS = ["axis", "position", "center", "size",
  "dx", "dy", "dz", "x_spacing", "y_spacing", "z_spacing",
  "boundary", "wavelength", "n_modes", "n_ref",
  "rotate_axis", "rotate_angle", "rotate_radius"];

/* 放置词汇 v2（2.0.6a1）：center+size 槽对 → (lo, hi) 互算（UI 表仍以
 * min/max 形展示，落盘写 center/size）。fdtd 全槽；mode 槽级 null。
 * allowZero（2.0.8a1，仅 domain）：size=0 的轴仍显示零宽行（min=max=
 * center）——该轴=一格，用户可直接改长度。 */
function csRange(center, size, i, allowZero) {
  const ci = Array.isArray(center) ? Number(center[i]) : NaN;
  const si = Array.isArray(size) ? Number(size[i]) : NaN;
  return isFinite(ci) && isFinite(si) && (allowZero ? si >= 0 : si > 0)
    ? [ci - si / 2, ci + si / 2] : null;
}

function csCommit(commit, cur, key, i, val) {
  const arr = (Array.isArray(cur) ? cur.slice() : [null, null, null]);
  arr[i] = val;
  commit(key, `[${arr.map((x) => x == null ? "None" : py(x)).join(", ")}]`);
}

/* range 行写回 = 一条 exec 写 center+size（一次往返；source 的
 * setPlacement 先例）。分两条 commit 会发两次 /exec、重画两次。 */
function csWrite(addr, center, size) {
  exec("genie.set_named(" + JSON.stringify(addr) + ", center=" + py(center)
    + ", size=" + py(size) + ")", { echo: false }).catch(() => {});
}

const MODE_FACES = { x: ["y_min", "y_max", "z_min", "z_max"],
                     y: ["x_min", "x_max", "z_min", "z_max"],
                     z: ["x_min", "x_max", "y_min", "y_max"] };

export function DOMAIN_SCHEMA(cfg, commit, gs, setGs, gmon, setGm) {
  const d = (cfg && cfg.domain) || {};
  const range = (a) => csRange(d.center, d.size, "xyz".indexOf(a));
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
  /* 换 kind = 只写 kind；pml/bloch 的专属字段由 core/model 的 resolver
   * 按 kind 补/清（L-62，GUI 不写默认值）。 */
  const setFaceKind = (f, kind) => commitFace(f, { kind });

  /* 1. General settings —— 域放置（center/size，表内 min/max 互算）+ 时间步进 */
  const rangeRows = ["x", "y", "z"].map((a, i) => ({
    label: a,
    value: range(a),
    onSet: (v) => {
      const c = [0, 1, 2].map((k) =>
        (Array.isArray(d.center) ? Number(d.center[k]) : 0) || 0);
      const z = [0, 1, 2].map((k) =>
        (Array.isArray(d.size) && d.size[k] != null) ? d.size[k] : null);
      c[i] = (v[0] + v[1]) / 2;
      z[i] = v[1] - v[0];
      csWrite("fdtd", c, z);        /* 一条 exec 写对（同 source 先例） */
    },
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
              hint: "field-energy shutoff ratio; 0 = off",
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
        /* 值来自 effective 视图（L-62）：pml 面的 layers/κ/order 由
         * core/model 的 resolver 按 kind 补，GUI 不播种。 */
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

  /* 全局 source 节（2026-11-28 站长拍）：不再单独树节点/卡片，
   * 内容（spectrumTiming）直接住 fdtd 卡末尾；写腿 = global_source 单例。 */
  if (typeof setGs === "function") {
    sections.push(GLOBAL_SCHEMA(cfg, setGs, gs).sections[0]);
  }
  /* 全局 monitor 节（2026-11-28 B 案）：测量频谱/点数/apodize；
   * 写腿 = global_monitor 单例。on（跟源）时不渲染频段表（真相住源侧）。 */
  if (typeof setGm === "function") {
    sections.push(GLOBAL_MONITOR_SCHEMA(cfg, setGm, gmon).sections[0]);
  }

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
  const axis = d.axis;
  const edges = MODE_FACES[axis] || [];
  const [t1, t2] = [edges[0][0], edges[2][0]];
  const bo = mc.boundary || {};
  const kindOf = (f) => (bo[f] || {}).kind || "";
  const range = (a) => csRange(d.center, d.size, "xyz".indexOf(a));
  const commitFace = (f, face) => {
    const cur = mc.boundary || {};
    const nb = {};
    for (const k of Object.keys(cur)) nb[k] = Object.assign({}, cur[k]);
    nb[f] = face;
    mc.boundary = nb;
    commit("boundary", py(nb));
  };
  const setFaceKind = (f, kind) => commitFace(f, { kind });
  const hasSpacing = [t1, t2].some((a) => Array.isArray(d[`${a}_spacing`]));
  const nRef = (v) => (typeof v === "string" ? v : (v == null ? "" : String(v)));

  const IDX = { x: 0, y: 1, z: 2 };
  /* 换轴 = 只写 axis；新轴的横轴窗口/网格/边界与失效旋转由
   * core/model 的 resolver 按 axis 产出（L-62，GUI 不写默认值）。 */
  const switchAxis = (v) => commit("axis", py(v));

  const sections = [{
    type: "section", key: "mode:general", title: "General settings", open: true,
    items: [
      { type: "row", label: "Propagation axis", controls: [{
          kind: "select", value: axis, options: ["x", "y", "z"], rerender: true,
          onPick: (v) => switchAxis(v) }] },
      { type: "range", caption: "Domain range", unit: "len",
        order: ["center", "size", "min", "max"],
        rows: ["x", "y", "z"].map((a, i) => (a === axis
          ? { label: a, centerOnly: true, position: d.position,
              onCenter: (v) => commit("position", py(v)) }
          : { label: a, value: range(a),
              onSet: (v) => {
                csCommit(commit, d.center, "center", i, (v[0] + v[1]) / 2);
                csCommit(commit, d.size, "size", i, v[1] - v[0]);
              } })) },
      { type: "table", caption: "mode solver control",
        cols: [{ label: "Wavelength", unit: "µm" }, { label: "Modes" },
               { label: "Search index" }],
        rows: [{ cells: [
          { kind: "str", path: "mode.wavelength",
            value: wavelengthText(mc.wavelength),
            hint: "µm; several: comma-separated, ascending",
            validate: wavelengthError,
            onSet: (s) => commit("wavelength", wavelengthLiteral(s)) },
          { kind: "num", path: "mode.n_modes", value: mc.n_modes,
            onSet: (v) => commit("n_modes", py(v)) },
          /* 空 = 内核取截面最大折射率（n_ref=None），不需要 auto 状态 */
          { kind: "num", path: "mode.n_ref", value: mc.n_ref,
            hint: "refractive index for the mode search; empty = cross-section max",
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
            hint: "degrees, right-hand rule; |angle| < 90",
            onSet: (v) => commit("rotate_angle", py(v)) },
          { kind: "num", path: "mode.rotate_radius",
            value: mc.rotate_axis ? mc.rotate_radius : undefined,
            disabled: !mc.rotate_axis, unit: "len",
            hint: "offset of the rotation axis from the plane centre; 0 = centre",
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
let SIM_UNIT = "frequency";
/* 本次会话里哪些 source 选了 Use global（不落文档；重开即普通值）。 */
const LOCKED = new Set();      /* 视图偏好：factor | absolute（不落模型） */

export function MESH_SCHEMA(cfg, commit, m) {
  const useAbs = MESH_METHOD
    ? MESH_METHOD === "absolute"
    : ["x", "y", "z"].some((a) => m[`d${a}`] != null);
  const method = useAbs ? "absolute" : "factor";
  const cell = (a) => (useAbs
    ? { kind: "num", path: `mesh.${a}.d`, value: m[`d${a}`], unit: "len",
        hint: "absolute step; must not exceed the base step on that axis",
        onSet: (v) => commit(`d${a}`, py(v)),
        onClear: () => commit(`d${a}`, "None") }
    : { kind: "num", path: `mesh.${a}.factor`, value: m[`factor_${a}`],
        hint: "ratio to the base step; 1 = unchanged",
        onSet: (v) => commit(`factor_${a}`, py(v)),
        onClear: () => commit(`factor_${a}`, "None") });
  return { sections: [{
    type: "section", key: "mesh:general", title: "General settings", open: true,
    items: [
      { type: "range", caption: "Range", unit: "len",
        order: ["center", "size", "min", "max"],
        rows: ["x", "y", "z"].map((a, i) => ({
          label: a,
          value: csRange(m.center, m.size, i),
          onSet: (v) => {
            csCommit(commit, m.center, "center", i, (v[0] + v[1]) / 2);
            csCommit(commit, m.size, "size", i, v[1] - v[0]);
          },
        })) },
      /* 方法与渐变率放在同一张表（2026-11-25 站长拍）：都是"怎么加密"的控制量 */
      { type: "table", caption: "Mesh control",
        cols: [{ label: "Refine method" }, { label: "Growth rate" }],
        rows: [{ cells: [
          { kind: "select", value: method, options: ["factor", "absolute"],
            rerender: true,
            onPick: (v) => {
              MESH_METHOD = v;
              /* 绝对步长留空，由用户填；默认值（factor=1）由 resolver 给。 */
            } },
          { kind: "num", path: "mesh.growth_rate", value: m.growth_rate,
            hint: "refinement transition gradient; 1 = hard step",
            onSet: (v) => commit("growth_rate", py(v)) },
        ] }] },
      { type: "table", caption: useAbs ? "Refine step" : "Refine factor",
        cols: ["x", "y", "z"].map((a) => ({
          label: a, unit: useAbs ? "µm" : "" })),
        rows: [{ cells: ["x", "y", "z"].map(cell) }] },
    ],
  }] };
}

/* ══════════════════ Spectrum + Temporal（2026-11-28 站长拍）══════════════
 * 全局 source 与所有 source 卡共用这一副长相：
 *   Spectrum = 1×5 范围表（首格 frequency/wavelength 切换 + center/span/
 *              min/max 四列互算）
 *   Temporal = delay / phase / amplitude 三列
 * 数据与写腿由调用方给；SIM_UNIT 是同一份视图状态：在哪张卡切频率/波长，
 * 所有卡一起跟。 */
function spectrumTiming({ band, temporal, path, onBand, onTemporal }) {
  const wl = SIM_UNIT === "wavelength";
  const pair = band ? (wl ? [C0 / band[1], C0 / band[0]] : band) : null;
  const cell = (field, value, unit, hint) => {
    const c = { kind: "num", path: `${path}.${field}`, value };
    if (unit) c.unit = unit;
    if (hint) c.hint = hint;
    c.onSet = (v) => onTemporal(field, v);
    return c;
  };
  const out = [{
    type: "range", caption: "Spectrum", unit: wl ? "len" : "freq",
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
        if (hi > lo) onBand([lo, hi]);
      } }],
  }];
  /* temporal == null = monitor 版（测量侧无时域参数），只出 Spectrum */
  if (temporal != null) {
    out.push({
      type: "table", caption: "Temporal",
      cols: [{ label: "delay", unit: "fs" }, { label: "phase", unit: "rad" },
             { label: "amplitude" }],
      rows: [{ cells: [
        cell("delay", temporal && temporal.delay, "time",
          "extra offset after the auto-placed pulse peak; 0 = none"),
        cell("phase", temporal && temporal.phase, null),
        cell("amplitude", temporal && temporal.amplitude, null),
      ] }],
    });
  }
  return out;
}

/* ══════════════════ FDTD ▸ soft source（2026-11-26 站长拍）══════════════
 * 与 fdtd/mode/mesh 同一副面板：General settings（Position / Type /
 * Polarization）→ Waveform & spectrum（Use global 节首 + 公共件
 * spectrumTiming；勾上全局时两张表不渲染）。 */
export function SOURCE_SCHEMA(cfg, commit, s, addr) {
  const vec = (key) => {
    const v = Array.isArray(s[key]) ? s[key] : [];
    return {
      cells: ["x", "y", "z"].map((_, k) => ({
        kind: "num", value: v[k], unit: "len",
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
    ? s.polarization : [];
  /* Use global（2026-11-28 站长拍）：节首一个二元开关。勾上 = 这个 source
   * 的 id 进文档级 `global_source.sources`（resolver 在 view/载荷里展开），
   * 频谱/时域两张表不渲染。关掉 = 从集合移除；source 自己的字段原样保留，
   * 所以来回切不丢值。 */
  const bound = s.use_global === true;
  const num = (x) => typeof x === "number" && isFinite(x);
  const band = (num(s.freq) && num(s.bandwidth))
    ? [s.freq - s.bandwidth / 2, s.freq + s.bandwidth / 2] : null;
  const toggleGlobal = (on) => {
    if (s.id == null) return;
    const known = new Set((fdtdConfig(cfg).source || []).map((x) => x.id));
    const cur = new Set((((cfg || {}).global_source || {}).sources) || []);
    if (on) cur.add(s.id); else cur.delete(s.id);
    const ids = Array.from(cur).filter((x) => known.has(x))
      .sort((a, b) => a - b);
    exec("genie.set_named('global_source', sources=" + py(ids) + ")",
      { echo: false }).catch(() => {});
  };
  /* Waveform & spectrum 节：Use global 在节首；勾上时只留开关 + 提示。 */
  const wave = [{
    type: "row", label: "Use global", controls: [{
      kind: "select", path: "source.use_global",
      value: bound ? "on" : "off", options: ["off", "on"],
      onSet: (v) => toggleGlobal(v === "on"),
    }] }];
  if (bound) {
    wave.push({ type: "note",
      text: "Spectrum and timing follow the Global source setting." });
  } else {
    wave.push(...spectrumTiming({
      band, path: "source",
      temporal: { delay: s.delay, phase: s.phase,
                  amplitude: s.amplitude },
      onBand: (v) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", freq="
          + py((v[0] + v[1]) / 2) + ", bandwidth=" + py(v[1] - v[0]) + ")",
          { echo: false }).catch(() => {});
      },
      onTemporal: (field, v) => commit(field, py(v)),
    }));
  }
  const isSoft = s.type === "soft_source";
  let general;
  if (isSoft) {
    general = [
      /* 位置第一（2026-11-26 站长拍）：wire 字段仍叫 center */
      { type: "table", caption: "Position",
        cols: ["x", "y", "z"].map((a) => ({ label: a, unit: "µm" })),
        rows: [vec("center")] },
      { type: "row", label: "Type", controls: [{
          kind: "select", value: s.kind,
          options: ["electric", "magnetic"], rerender: true,
          onPick: (v) => commit("kind", py(v)) }] },
      /* 复数直接输入（2026-11-26 站长拍）：xyz 三列，一格一个复数
       * （`1` / `2j` / `1-0.5j`），写回线格式的 [[re,im]×3]。 */
      { type: "table", caption: "Polarization",
        cols: ["x", "y", "z"].map((a) => ({ label: a })),
        rows: [{ cells: [0, 1, 2].map((k) => ({
          kind: "str", path: `source.polarization.${k}`,
          value: pol[k] ? formatComplex(pol[k][0], pol[k][1]) : "",
          hint: "complex per component: 1 / 2j / 1-0.5j",
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
    ];
  } else if (s.type === "mode") {
    /* mode source 暂下架（L-65，2026-11-28 站长拍）：direction 与横截面
     * 窗口的能力面待 kernel 改动后重定（wire v1：注入恒 +axis、mode
     * 定义在全截面）。wire 字段仍在、脚本可建——GUI 只给明说，不渲染
     * 借来的 plane_wave 字段。 */
    general = [{ type: "row", label: "Mode source",
      controls: [{ kind: "text",
        text: "temporarily unavailable — capability redesign in progress" }] }];

  } else {
    /* plane_wave（2026-11-28 站长拍）：位置/窗口 = Domain range 四列表
     * （mode 同款；传播轴行 centerOnly，横轴行 range → 整份写 center+size，
     * 未设分量补 0、未设槽写 None——wire 的 center 是整份 Vec3，无逐轴字段，
     * 表格换算必须物化整份，同 Spectrum center/span 的换算先例）。
     * 传播 = axis 下拉 + direction 下拉；θ/φ = Incidence 表（Rotation 同形）；
     * ψ = 单输入（偏振角，替代 soft source 的复数 polarization）。 */
    const IDX = { x: 0, y: 1, z: 2 };
    const axis = s.propagation_axis || null;
    const center = Array.isArray(s.center) ? s.center : null;
    const size = Array.isArray(s.size) ? s.size : null;
    const hasSize = size && size.some((x) => x != null);
    const rangeOf = (a) => {
      const k = IDX[a], c = center && center[k], w = size && size[k];
      return (typeof c === "number" && typeof w === "number")
        ? [c - w / 2, c + w / 2] : null;
    };
    const setPlacement = (mut) => {
      const c = [0, 1, 2].map((k) =>
        (center && typeof center[k] === "number") ? center[k] : 0.0);
      const z = [0, 1, 2].map((k) =>
        (size && size[k] != null) ? size[k] : null);
      if (axis) z[IDX[axis]] = null;     /* 传播槽恒 null（wire 契约） */
      mut(c, z);
      exec("genie.set_named(" + JSON.stringify(addr)
        + ", center=" + py(c) + ", size=" + py(z) + ")",
        { echo: false }).catch(() => {});
    };
    general = [
      /* Propagation 表（2026-11-28 站长拍）：axis / direction 两列。
       * from_face 已从 wire 删除（2.0.5a1）——定位词汇就是
       * center + axis（resolver 默认注入面在传播轴域 min 端）。 */
      { type: "table", caption: "Propagation",
        cols: [{ label: "axis" }, { label: "direction" }],
        rows: [{ cells: [
          { kind: "select", path: "source.propagation_axis", value: axis,
            options: ["x", "y", "z"], rerender: true,
            onPick: (v) => {
              const z = [0, 1, 2].map((k) =>
                (size && size[k] != null) ? size[k] : null);
              z[IDX[v]] = null;        /* 新传播槽让位（横槽保留） */
              exec("genie.set_named(" + JSON.stringify(addr)
                + ", propagation_axis=" + py(v)
                + (hasSize ? ", size=" + py(z) : "") + ")",
                { echo: false }).catch(() => {});
            } },
          { kind: "select", path: "source.direction",
            value: s.direction, options: ["+", "-"],
            onSet: (v) => commit("direction", py(v)) },
        ] }] },
      { type: "range", caption: "Domain range", unit: "len",
        order: ["center", "size", "min", "max"],
        rows: ["x", "y", "z"].map((a) => (a === axis
          ? { label: a, centerOnly: true,
              position: center && center[IDX[a]],
              onCenter: (v) => setPlacement((c) => { c[IDX[a]] = v; }) }
          : { label: a, value: rangeOf(a),
              onSet: (v) => setPlacement((c, z) => {
                c[IDX[a]] = (v[0] + v[1]) / 2;
                z[IDX[a]] = v[1] - v[0];
              }) })) },
      { type: "table", caption: "Angles",
        cols: [{ label: "polar θ (°)" }, { label: "azimuthal φ (°)" },
               { label: "polarization ψ (°)" }],
        rows: [{ cells: [
          { kind: "num", path: "source.theta", value: s.theta,
            onSet: (v) => commit("theta", py(v)) },
          { kind: "num", path: "source.phi", value: s.phi,
            onSet: (v) => commit("phi", py(v)) },
          { kind: "num", path: "source.psi", value: s.psi,
            onSet: (v) => commit("psi", py(v)) },
        ] }] },
    ];
    /* gaussian_beam = plane_wave 全套 + radius（wire 继承 PlaneWaveSpec）；
     * radius 是 wire 必填（gt=0、无默认）——GUI 显示空、hint 指路，
     * 不发明值（Domain range 与 Angles 之间）。 */
    if (s.type === "gaussian_beam") {
      general.splice(2, 0, { type: "row", label: "Radius",
        controls: [{ kind: "num", path: "source.radius", value: s.radius,
          unit: "len",
          hint: "transverse 1/e² aperture, w ≈ 0.47·radius; default = 2 µm",
          onSet: (v) => commit("radius", py(v)) }] });
    }
  }
  return { sections: [{
    type: "section", key: "src:general", title: "General settings", open: true,
    items: general,
  }, {
    type: "section", key: "src:wave", title: "Waveform & spectrum",
    open: true,
    items: wave,
  }] };
}

/* ══════════════════ 全局 source（2026-11-28）══════════════════
 * 文档级 model.global_source：source 用 id 成员继承；resolver 在 view/载荷
 * 展开成数值字段；wire 没有这个字段。 */
export function GLOBAL_SCHEMA(cfg, setGs, gs) {
  /* 全局 source 设置 = 文档级单例（model.global_source）；值为 effective。 */
  const g = gs || {};
  const f = g.freq, bw = g.bandwidth;
  const band = (typeof f === "number" && typeof bw === "number")
    ? [f - bw / 2, f + bw / 2] : null;
  return { sections: [{
    type: "section", key: "sim:source", title: "Global source", open: true,
    items: spectrumTiming({
      band, path: "global_source",
      temporal: { delay: g.delay, phase: g.phase, amplitude: g.amplitude },
      onBand: (v) => setGs({ freq: (v[0] + v[1]) / 2,
                             bandwidth: v[1] - v[0] }),
      onTemporal: (field, v) => setGs({ [field]: v }),
    }),
  }] };
}

/* ══════════════════ 全局 monitor（2026-11-28 B 案）══════════════════
 * 文档级 model.global_monitor：默认测量频谱独立于激励（global_source）。
 * freq 型 monitor 用 id 成员继承：频谱（center/bw）+ 点数 n + apodize；
 * 显式写频谱组或 apodize = 退出名单（resolver 展开、载荷剥 use_global）。
 * spectrum_from_source（拍 A，同日）：显式联动——频段跟 global_source
 * 走（真相住源侧）；n/apodize 仍归测量。 */
export function GLOBAL_MONITOR_SCHEMA(cfg, setGm, gm) {
  const g = gm || {};
  const follow = g.spectrum_from_source === true;
  const num = (v) => typeof v === "number" ? v : null;
  /* off 时才读自己的频段画表；on 时频段跟源（源侧显示归 global_source 卡） */
  const band = (!follow && num(g.freq) != null && num(g.bandwidth) != null)
    ? [num(g.freq) - num(g.bandwidth) / 2,
       num(g.freq) + num(g.bandwidth) / 2] : null;
  const ap = g.apodize || {};
  const setAp = (patch) => {
    if (patch.side === null || patch.side === "") {
      /* side 空 = 无窗（矩形窗）——删整键，不残留孤儿 width */
      setGm({ apodize: null });
      return;
    }
    const d = {};
    if (ap.side) d.side = ap.side;
    if (Array.isArray(ap.time_window)) d.time_window = ap.time_window;
    if (ap.width != null) d.width = ap.width;
    Object.assign(d, patch);
    if (!d.side && !d.time_window && d.width == null) {
      setGm({ apodize: null });
      return;
    }
    setGm({ apodize: d });
  };
  return { sections: [{
    type: "section", key: "sim:monitor", title: "Global monitor",
    open: true,
    items: [
      /* Use global source（拍 A，同日终拍默认 on）：on = 频段跟源走
       * （只读显示源值——真相住源侧）；off = 独立（本卡可编）。来回切
       * 不丢值：跟源时自己的频段字段原样保留在文档里。 */
      { type: "row", label: "Use global source", controls: [{
          kind: "select",
          path: "global_monitor.spectrum_from_source",
          value: follow ? "on" : "off",
          options: ["on", "off"],
          rerender: true,
          onSet: (v) => setGm({ spectrum_from_source: v === "on" }) }] },
      /* on = 频段跟源（真相住源侧，本卡不再显示频段）；off = 独立
       * （本卡可编）。来回切不丢值：跟源时自己的频段字段原样保留。 */
      ...(follow ? [] : spectrumTiming({
            band, path: "global_monitor", temporal: null,
            onBand: (v) => setGm({ freq: (v[0] + v[1]) / 2,
                                   bandwidth: v[1] - v[0] }),
            onTemporal: null,
          })),
      { type: "row", label: "Frequency points",
        controls: [{ kind: "num", path: "global_monitor.n", value: g.n,
          hint: "DFT sample count across the band; 0 = unset",
          onSet: (v) => setGm({ n: v }) }] },
      /* apodize：定量谱学的首程窗；side 空 = 无窗（矩形） */
      { type: "table", caption: "Apodization",
        cols: [{ label: "side" }, { label: "window (fs)" },
               { label: "width (fs)" }],
        rows: [{ cells: [
          { kind: "select", path: "global_monitor.apodize.side",
            value: ap.side || "", options: ["", "start", "stop", "both"],
            unsetLabel: "none",
            onSet: (v) => setAp({ side: v || null }) },
          { kind: "num", path: "global_monitor.apodize.time_window",
            value: Array.isArray(ap.time_window) ? ap.time_window[0] : undefined,
            unit: "time",
            hint: "window start; second slot follows width",
            onSet: (v) => setAp({ time_window: [v, (ap.width || 0) + v] }) },
          { kind: "num", path: "global_monitor.apodize.width",
            value: ap.width, unit: "time",
            hint: "cosine taper width; empty = no window",
            onSet: (v) => setAp({ width: v }) },
        ] }] },
    ],
  }] };
}

/* ══════════════════ FDTD ▸ monitor 卡（现代化，2026-11-28）════════════
 * 与 source 卡同构：Position（center+size，size 槽 null=整轴）→ 观测节。
 * freq 型：Use global 开关（id 进 global_monitor 名单；写频谱/apodize
 * 即退出——worker 同语义）+ Spectrum/点数/Apodization；down_sample 只
 * 属 time 型（wire 契约：freq/index 带 down_sample 422）；index 型
 * field = eps/mu/both。 */
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
      mode: "mode",
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
                   mesh: "Mesh", run: "Settings" }[this.current];
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
   * (geometry.0.box.center), 表单字段路径没有 (geometry.0.center.0);
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
        this._input(path + "." + base, mt[base], "num")));
    } else {
      const bv = mt[base];
      const wraps = AX.map((a) => {
        const key = base + "_" + a;
        const inpWrap = this._input(path + "." + key, mt[key], "num");
        const inp = inpWrap.querySelector("input");
        inp.placeholder = bv != null ? String(bv) : "";  /* 空槽 = 回落基值 */
        inp.dataset.emptyDeletes = "1"; /* 清空 = 删命名键 */
        inp.title = bv != null ? "empty = base value " + bv
                               : "empty = base value";
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
  _domain(body) {
    const commit = (field, literal) => {
      exec("genie.set_named('fdtd', " + field + "=" + literal + ")",
        { echo: false }).catch(() => {});
    };
    /* 全局单例写腿（住进 fdtd 卡，2026-11-28）：地址不同（'global_source'
     * / 'global_monitor'，不是 fdtd solver），一条命令一个字段。 */
    const mkSingleton = (addr) => (fields) => {
      const lines = [];
      for (const [k, v] of Object.entries(fields || {})) {
        lines.push("genie.set_named('" + addr + "', " + k + "=" + py(v) + ")");
      }
      if (lines.length) exec(lines.join("\n"), { echo: false }).catch(() => {});
    };
    const setGs = mkSingleton("global_source");
    const setGm = mkSingleton("global_monitor");
    const gs = (this.cfg && this.cfg.global_source) || {};
    const gmon = (this.cfg && this.cfg.global_monitor) || {};
    this.renderPage(DOMAIN_SCHEMA(fdtdConfig(this.cfg), commit, gs, setGs,
      gmon, setGm), body);
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
    for (const [m, i] of this._items(fdtdConfig(this.cfg).mesh || [])) {
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
              this._input(P + ".delta_eps", pl.delta_eps, pk)));
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
    /* 几何卡 = GEOMETRY_SCHEMA + kit（2026-11-28）：地址 = geometry.<name>
     * （几何是 model 级集合，不是 solver 子节点）；卡头保留对象族统一语言
     * （预览 / 名字 / 材料 chip / 删除）。 */
    const list = this.cfg.geometry || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("structure", "No geometry yet",
        "Add from the DESIGN ribbon: Structures ▸ Box / Sphere / …"));
      return;
    }
    const matNames = this._matNames();
    for (const [g, i] of this._items(list)) {
      const addr = "geometry." + g.name;
      const commit = (field, literal) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", " + field + "="
          + literal + ")", { echo: false }).catch(() => {});
      };
      const inner = this._el("div", "pane-body");
      this.renderPage(GEOMETRY_SCHEMA(this.cfg || {}, commit, g, matNames),
        inner);
      const gHead = [
        this._preview(g.type),
        this._headName(`geometry.${i}.name`, g.name,
          `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`,
          (g.name || `${GEO_TYPE_LABEL[g.type] || g.type} #${i + 1}`) + " name"),
      ];
      if (g.material) gHead.push(this._el("span", "tag", g.material));
      gHead.push(this._delBtn("geometry", i));
      body.appendChild(this._card(gHead, inner));
    }
  },

  _source(body) {
    /* source 卡 = SOURCE_SCHEMA + kit（2026-11-26）：地址 = <当前 fdtd 名>.<源名>。
     * 类型分派在 SOURCE_SCHEMA 里（soft_source / plane_wave），这里不再拦。 */
    const sv = ((this.cfg && this.cfg.solvers) || [])
      .find((x) => (x.kind || "fdtd") === "fdtd");
    const solver = (sv && sv.name) || "fdtd";
    const list = fdtdConfig(this.cfg).source || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("soft_source", "No sources yet",
        "Add from the FDTD ribbon: Sources ▸ Soft source / Plane wave"));
      return;
    }
    for (const [src, i] of this._items(list)) {
      const addr = solver + "." + src.name;
      const commit = (field, literal) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", " + field + "="
          + literal + ")", { echo: false }).catch(() => {});
      };
      const wrap = this._el("div", "pane-body");
      this.renderPage(SOURCE_SCHEMA(this.cfg || {}, commit, src, addr), wrap);
      body.appendChild(this._card(
        [this._el("span", "pane-id-name", "Settings")], wrap));
    }
  },

  _monitor(body) {
    /* monitor 卡 = MONITOR_SCHEMA + kit（2026-11-28 现代化）：地址 =
     * <当前 fdtd 名>.<监视器名>；类型分派（freq 的 Use global /
     * Spectrum、time 的 Sampling、index 的小卡）在 MONITOR_SCHEMA。 */
    const sv = ((this.cfg && this.cfg.solvers) || [])
      .find((x) => (x.kind || "fdtd") === "fdtd");
    const solver = (sv && sv.name) || "fdtd";
    const list = fdtdConfig(this.cfg).monitor || [];
    if (!list.length) {
      body.appendChild(this._paneEmpty("mon_time", "No monitors yet",
        "Add from the FDTD toolbar: Monitors ▸ Time / Freq / Index"));
      return;
    }
    for (const [m, i] of this._items(list)) {
      const addr = solver + "." + m.name;
      const commit = (field, literal) => {
        exec("genie.set_named(" + JSON.stringify(addr) + ", " + field + "="
          + literal + ")", { echo: false }).catch(() => {});
      };
      const wrap = this._el("div", "pane-body");
      this.renderPage(MONITOR_SCHEMA(this.cfg || {}, commit, m, addr), wrap);
      /* 卡头学 source 卡（2026-11-28 站长拍）：只有 "Settings" 行——
       * 不放图标 / 类型字样 / 名称编辑（删除走左树，改名走属性面板）。 */
      body.appendChild(this._card(
        [this._el("span", "pane-id-name", "Settings")], wrap));
    }
  },


  _run(body) {
    // G18 单旋钮：时长唯一在 domain.total_time（domain 节编辑）——run 节
    // 只剩超时；卡数由系统定（G78），步数是 build 后派生量。
    const r = this.cfg.run || {};
    body.appendChild(this._row("timeout (s)",
      this._input("run.timeout_s", r.timeout_s, "num")));
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

/* ═══ 几何对象卡（2026-11-28 站长拍：几何功能弄好）════════════════════
 * 与 source/monitor 同构：GEOMETRY_SCHEMA = 数据描述符，DOM 由 kit 生成；
 * 写腿 = commit（genie.set_named('geometry.<name>', …)）。字段面按
 * wire GeometrySpec 七型分派（box / sphere / cylinder / ngon_z /
 * polygon_z / ellipse_z / ring_z）；rotation / priority 各省一行。 */
function parseVertices(text) {
  /* 顶点表（µm，SI 写出）：一行 "x, y"；空行 = 子多边形分隔（[null,null]
   * 哨兵，与 wire PolygonZSpec 同一语义）。< 3 点 = 非法（wire min_length）。 */
  const out = [];
  for (const row of String(text || "").split(/\r?\n/)) {
    const t = row.trim();
    if (!t) {
      if (out.length) out.push([null, null]);
      continue;
    }
    const m = t.split(",").map((x) => Number(x.trim()));
    if (m.length !== 2 || !m.every((x) => isFinite(x))) return null;
    out.push([m[0] * 1e-6, m[1] * 1e-6]);
  }
  while (out.length && out[out.length - 1][0] == null) out.pop();
  return out.length >= 3 ? out : null;
}

export function GEOMETRY_SCHEMA(cfg, commit, g, matNames) {
  const AX = ["x", "y", "z"];
  const num = (x) => typeof x === "number" && isFinite(x);
  const center = Array.isArray(g.center) ? g.center : null;
  const size = Array.isArray(g.size) ? g.size : null;
  const centerOf = (i) => ((center && num(center[i])) ? center[i] : undefined);
  const centerArr = () => AX.map((a, i) =>
    (center && num(center[i])) ? center[i] : 0.0);
  const addr = "geometry." + g.name;

  /* 行内写腿：单个字段一条 exec */
  const setField = (field, literal) => commit(field, literal);
  const setCenterSlot = (i, v) => {
    const c = centerArr();
    c[i] = v;
    setField("center", py(c));
  };
  const setVecSlot = (field, i, v) => {
    const cur = Array.isArray(g[field]) ? g[field] : null;
    const a = [0, 1, 2].map((k) => (cur && num(cur[k])) ? cur[k] : 0.0);
    a[i] = v;
    setField(field, py(a));
  };

  const items = [{
    type: "row", label: "material", controls: [{
      kind: "select", path: addr + ".material",
      value: g.material || "", options: ["", ...(matNames || [])],
      unsetLabel: "(choose material)",
      onSet: (v) => { if (v) setField("material", py(v)); } }] }];

  const posItems = [];
  if (g.type === "box") {
    /* box 不是"单轴 range"：三轴 size 各自独立、wire 要求三轴正长——
     * 逐格写会把其余轴写成 null（422）。所以 center 单格写、size 整行
     * 写（三格全填才发；不发明默认值）。 */
    posItems.push({
      type: "table", caption: "Center", cols: AX.map((a) => ({ label: a, unit: "µm" })),
      rows: [{ cells: AX.map((a, i) => ({
        kind: "num", path: addr + ".center." + i, unit: "len",
        value: centerOf(i),
        onSet: (v) => setCenterSlot(i, v) })) }] });
    posItems.push({
      type: "table", caption: "Size", cols: AX.map((a) => ({ label: a, unit: "µm" })),
      rows: [{ unit: "len",
        cells: AX.map((a, i) => ({
          kind: "num", path: addr + ".size." + i, unit: "len",
          value: (size && num(size[i])) ? size[i] : undefined })),
        /* 三格全填才写（缺格 → 提示，不补默认） */
        validate: (vals) => (vals.every((v) => v > 0)
          ? null : { index: 0, msg: "size 三轴都必须 > 0" }),
        onRow: (vals) => setField("size", py(vals)) }] });
  } else if (g.type === "polygon_z") {
    const verts = (g.vertices || []).map((p) =>
      (!Array.isArray(p) || p[0] == null || p[1] == null)
        ? "" : `${fmtUserNum(p[0] * 1e6)}, ${fmtUserNum(p[1] * 1e6)}`);
    posItems.push({
      type: "row", label: "vertices (µm)", controls: [{
        kind: "area", path: addr + ".vertices", value: verts.join("\n"),
        rows: 6,
        validate: (t) => (parseVertices(t) == null
          ? "每行 x, y（µm）；空行 = 子多边形分隔；至少 3 点" : null),
        onSet: (t) => {
          const v = parseVertices(t);
          if (v) setField("vertices", py(v));
        } }] });
    posItems.push({
      type: "range", caption: "Extrusion", unit: "len",
      order: ["center", "size", "min", "max"],
      rows: [{ label: "z",
        value: Array.isArray(g.z_range) ? g.z_range : null,
        onSet: (v) => setField("z_range", py([v[0], v[1]])) }] });
  } else {
    posItems.push({
      type: "range", caption: "Position", unit: "len",
      order: ["center", "size", "min", "max"],
      rows: AX.map((a, i) => ({
        label: a, centerOnly: true, position: centerOf(i),
        onCenter: (v) => setCenterSlot(i, v) })) });
    const dims = {
      sphere: ["radius"], cylinder: ["radius", "height"],
      ngon_z: ["n_sides", "circumradius", "height"],
      ellipse_z: ["rx", "ry", "height"],
      ring_z: ["inner_radius", "outer_radius", "height"],
    }[g.type] || [];
    for (const f of dims) {
      posItems.push({ type: "row", label: f, controls: [{
        kind: "num", path: addr + "." + f, value: g[f],
        unit: f === "n_sides" ? null : "len",
        onSet: (v) => setField(f, py(v)) }] });
    }
  }

  const materialRow = items[0];
  const rotationTable = { type: "table", caption: "Rotation (°)",
    cols: AX.map((a) => ({ label: a })),
    rows: [{ cells: AX.map((a, i) => ({
      kind: "num", path: addr + ".rotation." + i,
      value: Array.isArray(g.rotation) ? g.rotation[i] : undefined,
      onSet: (v) => setVecSlot("rotation", i, v) })) }] };
  const priorityRow = { type: "row", label: "priority", controls: [{
    kind: "num", path: addr + ".priority", value: g.priority,
    onSet: (v) => setField("priority", py(v)) }] };

  /* 两节（2026-11-28 站长拍）：General settings = 位置 + 几何参数 +
   * rotation；Material settings = material + priority。 */
  return { sections: [
    { type: "section", key: "geo:general", title: "General settings",
      open: true, items: [...posItems, rotationTable] },
    { type: "section", key: "geo:material", title: "Material settings",
      open: true, items: [materialRow, priorityRow] },
  ] };
}


Object.assign(FormPanel, KIT);

export function MONITOR_SCHEMA(cfg, commit, m, addr) {
  const center = Array.isArray(m.center) ? m.center : null;
  const size = Array.isArray(m.size) ? m.size : null;
  const num = (x) => typeof x === "number" && isFinite(x);
  const AX = ["x", "y", "z"];
  /* 监视器维度（2026-11-28 站长拍）：size 槽有值（null = 整轴全跨，
   * > 0 = 长度）即该轴占维。0D = 点，1D = 线，2D = 面，3D = 体。
   * 维度不落 wire：切维度/方向只写 size（非活动轴 0 = 单格；新活动轴
   * 默认 2 µm、center 默认 0），重开按 size 反推——活动轴恒有值，派生
   * 无歧义（无会话态、无 pending）。 */
  const DEFAULT_MON_SIZE = 2e-6;
  const rawSize = (a) => {
    const v = Array.isArray(m.size) ? m.size[AX.indexOf(a)] : undefined;
    return v === null || num(v) ? v : undefined;
  };
  const hasDim = (a) => {
    const v = rawSize(a);
    return v === null || (num(v) && v > 0);
  };
  const ds = { dim: AX.filter(hasDim).length,
               dir: AX.filter(hasDim).join("") };
  const dirLabel = (act) => (act === "xz" ? "zx" : act);
  const setDims = (n, dirStr) => {
    let act;
    if (n === 0) act = [];
    else if (n === 3) act = AX.slice();
    else if (n === 1) act = [dirStr || (ds.dir.length === 1 ? ds.dir : "x")];
    else act = (dirStr || (ds.dir.length === 2 ? dirLabel(ds.dir) : "xy"))
      .split("");
    /* 活动轴长度按顺序搬（改方向不丢已填长度）；新活动轴没有来源 =
     * 默认 2 µm（站长 2026-11-28：默认 center=0 / size=2 µm，只有
     * 维度/方向固定的轴才为 0）。 */
    const oldLens = (ds.dir || "").split("").map((a) => rawSize(a));
    const sizeArr = AX.map((a) => {
      const k = act.indexOf(a);
      if (k < 0) return 0;
      if (k < oldLens.length) return oldLens[k];
      const v = rawSize(a);
      return (v === null || (num(v) && v > 0)) ? v : DEFAULT_MON_SIZE;
    });
    commit("size", py(sizeArr));
  };
  const bound = m.use_global === true;   /* freq 型：名单内 */
  const f = m.freq || {};
  const band = (num(f.center) && num(f.bw))
    ? [f.center - f.bw / 2, f.center + f.bw / 2] : null;
  const toggleGlobal = (on) => {
    if (m.id == null) return;
    const known = new Set((fdtdConfig(cfg).monitor || [])
      .filter((x) => x.type === "freq").map((x) => x.id));
    const cur = new Set((((cfg || {}).global_monitor || {}).monitors) || []);
    if (on) cur.add(m.id); else cur.delete(m.id);
    const ids = Array.from(cur).filter((x) => known.has(x))
      .sort((a, b) => a - b);
    exec("genie.set_named('global_monitor', monitors=" + py(ids) + ")",
      { echo: false }).catch(() => {});
  };

  /* Polarization 多选（L-66）：str（"all"/单分量）或六分量子集 list。归一
   * 只在 GUI 写腿——全六开写 "all" 省表示（wire 层不归一）。空集不可写
   * （wire 422）：关到空就地弹回，不发明值。all 列在最前：all=on 时
   * 六分量锁死（flat-locked：无下拉、只读文本色；要挑子集先关 all——
   * 关 all 只解锁不写值，选中态仍是当前快照的展开）；六分量重新全开
   * = 写回 "all" 并锁回。index 型不参与：仍是 eps/mu/both 下拉。 */
  const COMPONENTS = ["Ex", "Ey", "Ez", "Hx", "Hy", "Hz"];
  let fieldItems;
  if (m.type === "index") {
    fieldItems = [{ type: "row", label: "field", controls: [{
        kind: "select", value: m.field,
        options: ["eps", "mu", "both"],
        onSet: (v) => commit("field", py(v)) }] }];
  } else {
    const cur = m.field === "all" ? new Set(COMPONENTS)
      : new Set(Array.isArray(m.field) ? m.field
        : (m.field ? [m.field] : []));
    const allOn = cur.size === 6;
    const write = (next) => {
      if (next.size === 6) commit("field", py("all"));
      else commit("field", py(COMPONENTS.filter((c) => next.has(c))));
    };
    /* 表内联动：all ↔ 六分量的锁与显示（同一行 7 个 select 的 DOM 联动，
     * rerender 兜底最终一致） */
    const rowSelects = (sel) =>
      Array.from((sel.closest("table") || document)
        .querySelectorAll("select"));
    const lockComps = (locked, self) => {
      for (const s of rowSelects(self)) {
        if ((s.getAttribute("aria-label") || "") === "all") continue;
        s.disabled = locked;
        s.classList.toggle("flat-locked", locked);
      }
    };
    const syncAll = (on, self) => {
      for (const s of rowSelects(self)) {
        if ((s.getAttribute("aria-label") || "") === "all") s.value = on ? "on" : "off";
      }
    };
    const cell = (comp) => ({
      kind: "select", path: "mon.field." + comp,
      value: cur.has(comp) ? "on" : "off", options: ["off", "on"],
      aria: comp, disabled: allOn, flatLocked: allOn,
      onSet: (v, sel) => {
        const next = new Set(cur);
        if (v === "on") next.add(comp); else next.delete(comp);
        if (!next.size) { sel.value = "on"; return; }   /* 关到空 = 弹回 */
        write(next);
        if (next.size === 6) { syncAll(true, sel); lockComps(true, sel); }
      } });
    const allCell = {
      kind: "select", path: "mon.field.all",
      value: allOn ? "on" : "off", options: ["off", "on"],
      aria: "all",
      /* 不 rerender：关 all 是纯 UI 态（不写值），快照仍是 "all"，
       * 重建会把解锁立刻冲掉（2026-11-28 实测）。 */
      onSet: (v, sel) => {
        if (v === "on") {
          commit("field", py("all"));
          lockComps(true, sel);
          return;
        }
        /* 关 all = 解锁子集编辑，不写值（选中态 = 当前快照展开） */
        lockComps(false, sel);
      } };
    fieldItems = [{ type: "table", caption: "Polarization",
      cols: [{ label: "all" }].concat(COMPONENTS.map((c) => ({ label: c }))),
      rows: [{ cells: [allCell].concat(COMPONENTS.map(cell)) }] }];
  }

  /* 维度/方向行（2026-11-28 站长拍）+ 范围表 **恒定三行**：
   * 活动轴的 center/size/min/max 可编；非活动轴只有 center 可编
   * （size/min/max 显 —）；0D = 三行都只有 center。 */
  const centerOf = (i) => (center && typeof center[i] === "number")
    ? center[i] : undefined;
  const rangeRows = AX.map((a, k) => {
    if (!ds.dir.includes(a)) {
      return {
        label: a, centerOnly: true,
        position: centerOf(k),
        onCenter: (v) => {
          const c = [0, 1, 2].map((j) =>
            (center && typeof center[j] === "number") ? center[j] : 0.0);
          c[k] = v;
          commit("center", py(c));
        },
      };
    }
    return {
      label: a,
      value: (centerOf(k) != null && size && typeof size[k] === "number")
        ? [center[k] - size[k] / 2, center[k] + size[k] / 2]
        : null,
      onSet: (v) => {
        const c = [0, 1, 2].map((i) =>
          (center && typeof center[i] === "number") ? center[i] : 0.0);
        const z = [0, 1, 2].map((i) =>
          (size && size[i] != null) ? size[i] : null);
        c[k] = (v[0] + v[1]) / 2;
        z[k] = v[1] - v[0];
        csWrite(addr, c, z);        /* 一条 exec 写对（同 source 先例） */
      },
    };
  });
  const dimItems = [{ type: "row", label: "Dimensionality",
    controls: [{ kind: "select", path: "mon.dim",
      value: `${ds.dim}D`, options: ["0D", "1D", "2D", "3D"],
      onSet: (v) => setDims(parseInt(v, 10), null) }] }];
  if (ds.dim === 1 || ds.dim === 2) {
    dimItems.push({ type: "row", label: "Axes",
      controls: [{ kind: "select", path: "mon.dir",
        value: dirLabel(ds.dir),
        options: ds.dim === 1 ? ["x", "y", "z"] : ["xy", "yz", "zx"],
        onSet: (v) => setDims(ds.dim, v) }] });
  }

  const sections = [{
    type: "section", key: "mon:general", title: "General settings", open: true,
    items: [
      /* 域放置词汇 v2 同款：center+size ↔ min/max 四列互算；size 槽空
       * = 整轴（min/max 行空 → 写 null 槽）。写整份物化（同 plane wave
       * setPlacement 先例）。monitor 的 size 允许 0（allowZero：0 =
       * 单格，点/线/面监视器）。 */
      ...dimItems,
      { type: "range", caption: "Domain range", unit: "len", allowZero: true,
        order: ["center", "size", "min", "max"], rows: rangeRows },
      ...fieldItems,
    ],
  }];

  if (m.type === "freq") {
    const observe = [{
      type: "row", label: "Use global monitor setting", controls: [{
        kind: "select", path: "mon.use_global",
        value: bound ? "on" : "off", options: ["off", "on"],
        onSet: (v) => toggleGlobal(v === "on") }] }];
    if (bound) {
      observe.push({ type: "note",
        text: "Spectrum and apodization follow the Global monitor setting." });
    } else {
      observe.push(...spectrumTiming({
        band, path: "mon", temporal: null,
        onBand: (v) => {
          exec("genie.set_named(" + JSON.stringify(addr)
            + ", freq_center=" + py((v[0] + v[1]) / 2)
            + ", freq_bw=" + py(v[1] - v[0]) + ")",
            { echo: false }).catch(() => {});
        },
        onTemporal: null,
      }));
      observe.push({ type: "row", label: "Frequency points",
        controls: [{ kind: "num", path: "mon.freq.n", value: f.n,
          hint: "DFT sample count across the band",
          onSet: (v) => commit("freq_n", py(v)) }] });
      const ap = m.apodize || {};
      const setAp = (patch) => {
        if (patch.side === null || patch.side === "") {
          commit("apodize", "None");   /* 无窗 = 矩形 */
          return;
        }
        const d = {};
        if (ap.side) d.side = ap.side;
        if (Array.isArray(ap.time_window)) d.time_window = ap.time_window;
        if (ap.width != null) d.width = ap.width;
        Object.assign(d, patch);
        commit("apodize", py(d));
      };
      observe.push({ type: "table", caption: "Apodization",
        cols: [{ label: "side" }, { label: "window (fs)" },
               { label: "width (fs)" }],
        rows: [{ cells: [
          { kind: "select", path: "mon.apodize.side",
            value: ap.side || "", options: ["", "start", "stop", "both"],
            unsetLabel: "none",
            onSet: (v) => setAp({ side: v || null }) },
          { kind: "num", path: "mon.apodize.time_window",
            value: Array.isArray(ap.time_window) ? ap.time_window[0] : undefined,
            unit: "time",
            hint: "window start; second slot follows width",
            onSet: (v) => setAp({ time_window: [v, (ap.width || 0) + v] }) },
          { kind: "num", path: "mon.apodize.width",
            value: ap.width, unit: "time",
            hint: "cosine taper width; empty = no window",
            onSet: (v) => setAp({ width: v }) },
        ] }] });
    }
    /* Observe 节撤（站长 2026-11-28）：内容直接并进 General——
     * monitor 卡只剩一节，少一层折叠。 */
    sections[0].items.push(...observe);
  }

  if (m.type === "time") {
    const ds = m.down_sample || {};
    sections.push({ type: "section", key: "mon:sample",
      title: "Sampling", open: true, items: [
        { type: "table", caption: "Down sample",
          cols: [{ label: "window start (fs)" },
                 { label: "window end (fs)" }, { label: "stride" }],
          rows: [{ cells: [
            { kind: "num", path: "mon.down_sample.time_window",
              value: Array.isArray(ds.time_window) ? ds.time_window[0] : undefined,
              unit: "time",
              onSet: (v) => {
                const end = Array.isArray(ds.time_window)
                  ? ds.time_window[1] : v;
                commit("down_sample", py({ time_window: [v, end],
                  stride: ds.stride != null ? ds.stride : 1 }));
              } },
            { kind: "num", path: "mon.down_sample.time_window_1",
              value: Array.isArray(ds.time_window) ? ds.time_window[1] : undefined,
              unit: "time",
              onSet: (v) => {
                const start = Array.isArray(ds.time_window)
                  ? ds.time_window[0] : v;
                commit("down_sample", py({ time_window: [start, v],
                  stride: ds.stride != null ? ds.stride : 1 }));
              } },
            { kind: "num", path: "mon.down_sample.stride",
              value: ds.stride,
              hint: "record every Nth step; 1 = every step",
              onSet: (v) => {
                const d = { stride: v };
                if (Array.isArray(ds.time_window)) d.time_window = ds.time_window;
                commit("down_sample", py(d));
              } },
          ] }] },
      ] });
  }

  return { sections };
}
