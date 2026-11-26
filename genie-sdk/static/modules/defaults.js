/* modules/defaults.js — GUI 侧的默认值（唯一来源，2026-11-26 站长拍）。
 *
 * kernel 只建空包络；"新建对象后应有的一套值" 属于面板规范，住在这里：
 * - 声明 fdtd / mode 后的初始域、时间、网格、边界；
 * - 边界 kind 切换时补哪些参数、清哪些旧参数。
 * 脚本里 genie.add_solver() 不带这些值（要自己在脚本里写）。
 */
"use strict";

export const DEFAULT_RANGE = [-1e-6, 1e-6];      /* ±1 µm */
export const DEFAULT_SPACING = 5e-8;             /* 0.05 µm */
export const DEFAULT_TOTAL_TIME = 1e-12;         /* 1000 fs */
export const DEFAULT_CFL = 0.99;
export const DEFAULT_AUTO_SHUTOFF = 1e-6;
export const DEFAULT_WAVELENGTH = 1.55e-6;
export const DEFAULT_NBG = 1.0;
export const DEFAULT_NMODES = 1;

/** kind → 它专属的字段（换 kind 时这些要清掉） */
export const BOUNDARY_KIND_FIELDS = {
  pml: ["layers", "kappa_max", "order", "reflection"],
  bloch: ["k", "from_source"],
};

/** kind → 选中它时补的默认参数 */
export const BOUNDARY_DEFAULTS = {
  pml: { layers: 12, kappa_max: 1.0, order: 3 },
  bloch: { k: [0, 0, 0], from_source: false },
};

export const MODE_TRANSVERSE = { x: ["y", "z"], y: ["x", "z"], z: ["x", "y"] };

export const modeEdges = (axis) => {
  const [a, b] = MODE_TRANSVERSE[axis] || MODE_TRANSVERSE.z;
  return [`${a}_min`, `${a}_max`, `${b}_min`, `${b}_max`];
};

/** 声明 fdtd 后要写下的那一套（字段名 → 值，SI） */
export function fdtdSeed() {
  const boundary = {};
  for (const f of ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"])
    boundary[f] = { kind: "pec" };
  return {
    x_range: DEFAULT_RANGE, y_range: DEFAULT_RANGE, z_range: DEFAULT_RANGE,
    total_time: DEFAULT_TOTAL_TIME, cfl: DEFAULT_CFL,
    auto_shutoff: DEFAULT_AUTO_SHUTOFF,
    dx: DEFAULT_SPACING, dy: DEFAULT_SPACING, dz: DEFAULT_SPACING,
    boundary,
  };
}

/** 声明 mode（或换传播轴）后要写下的那一套 */
export function modeSeed(axis = "z") {
  const [a, b] = MODE_TRANSVERSE[axis] || MODE_TRANSVERSE.z;
  const boundary = {};
  for (const f of modeEdges(axis)) boundary[f] = { kind: "pec" };
  return {
    axis, position: 0.0, wavelength: DEFAULT_WAVELENGTH,
    n_modes: DEFAULT_NMODES,
    [`${a}_range`]: DEFAULT_RANGE, [`${b}_range`]: DEFAULT_RANGE,
    [`d${a}`]: DEFAULT_SPACING, [`d${b}`]: DEFAULT_SPACING,
    [`${axis}_range`]: null, [`d${axis}`]: null,
    [`${axis}_spacing`]: null, [`${axis}_coords`]: null,
    boundary,
  };
}

/** 全局仿真设置（文档 simulation 段）：193 THz / 20 THz 带宽 */
export function simulationSeed() {
  return { freq_center: 193e12, freq_span: 20e12 };
}

/** 声明 soft source 后要写下的那一套（193 THz ≈ 1.55 µm） */
export function softSourceSeed() {
  return {
    freq: 193e12, bandwidth: 20e12, delay: 0.0, phase: 0.0,
    amplitude: 1.0, kind: "electric",
    center: [0.0, 0.0, 0.0],   /* 点源：不给 size（wire 默认 0 = 单格） */
    polarization: [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
  };
}

/** JS 值 → Python 字面量（exec 载荷用；None/True/False/列表） */
export function pyLit(v) {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(pyLit).join(", ") + "]";
  if (typeof v === "object")
    return "{" + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ": " + pyLit(x)).join(", ") + "}";
  return "None";
}

/** 一组字段 → "k=v, ..."（exec set_named 的参数段） */
export function fieldsArg(fields) {
  return Object.entries(fields).map(([k, v]) => `${k}=${pyLit(v)}`).join(", ");
}
