
/* modules/util.js — 基础工具（$——全前端唯一公共依赖）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ── 基础 ──────────────────────────────────────────────────── */
export const $ = (id) => document.getElementById(id);

/* Python 字面量编码器（Q44：值插值统一走它，禁 JSON.stringify 裸拼）。
 * JSON 与 Python 字面量只差三处：null/true/false → None/True/False；
 * 字符串双引号两边通用（JSON 的 \\uXXXX 转义 Python 也认）。 */
/* 复数输入（2026-11-26 站长拍）：直接收 `1` / `2j` / `1-0.5j` / `1+2i`，
 * 返回 {re, im} 或 null。空串非法（格子必须给一个明确的值）。 */
export function parseComplex(text) {
  const s = String(text == null ? "" : text).replace(/\s+/g, "").replace(/i/g, "j");
  if (!s) return null;
  if (!s.includes("j")) {
    if (!/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s)) return null;
    return { re: Number(s), im: 0 };
  }
  const m = /^([-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)?([-+]?)(\d*\.?\d+(?:[eE][-+]?\d+)?)?j$/
    .exec(s);
  if (!m) return null;
  const hasSign = m[4] === "+" || m[4] === "-";
  let re = 0, im;
  if (m[5] != null && m[5] !== "") {          /* a±bj */
    re = Number(m[1] || 0);
    im = (m[4] === "-" ? -1 : 1) * Number(m[5]);
  } else if (hasSign) {                       /* a±j */
    re = Number(m[1] || 0);
    im = m[4] === "-" ? -1 : 1;
  } else if (m[1] != null && m[1] !== "" && m[1] !== "+" && m[1] !== "-") {
    im = Number(m[1]);                        /* 2j → 纯虚数 2 */
  } else {
    im = 1;                                   /* j / -j */
  }
  if (!isFinite(re) || !isFinite(im)) return null;
  return { re, im };
}

/* {re, im} → 输入框里的最简写法 */
export function formatComplex(re, im) {
  const r = Number(re) || 0, i = Number(im) || 0;
  if (i === 0) return String(r);
  const unit = (i === 1 ? "j" : i === -1 ? "-j" : i + "j");
  if (r === 0) return unit;
  return r + (i > 0 ? "+" : "") + unit;
}


export function py(v) {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") {
    if (!isFinite(v)) throw new Error("py(): non-finite number");
    return String(v);
  }
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(py).join(", ") + "]";
  if (typeof v === "object")
    return "{" + Object.keys(v).map((k) => py(k) + ": " + py(v[k])).join(", ") + "}";
  throw new Error("py(): unsupported " + typeof v);
}

/* 视图投影（2026-11-28 收口）：worker 的 cfg 快照 = 文档形状的
 * `{model:{materials,geometry,parameters}, solvers:[{id,name,kind,config}],
 * analysis}`（effective 值）。这里只把共享 model 键平铺到顶层，**不再**
 * 平铺 solver config——solver 配置只有 `solvers[].config` 一条路，
 * 表单/树/3D 经 fdtdConfig(cfg) 取 fdtd 包络。
 * 2026-11-23：从 window 挂载改为模块导出——ESM 迁移后 `typeof guiViewOf`
 * 在模块作用域永远是 undefined，投影被静默跳过（树/3D 吃不到模型）。 */
export function guiViewOf(c) {
  if (!c || typeof c !== "object") return c;
  if (!c.model) return c;
  return Object.assign({}, c, c.model);
}

/* fdtd 包络的唯一取法：solver 槽按 kind 找，不读任何平铺副本。 */
export function fdtdConfig(cfg) {
  const sv = ((cfg && cfg.solvers) || []).find(
    (s) => s && (s.kind || "fdtd") === "fdtd");
  return (sv && sv.config) || {};
}

