
/* modules/util.js — 基础工具（$——全前端唯一公共依赖）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ── 基础 ──────────────────────────────────────────────────── */
export const $ = (id) => document.getElementById(id);

/* Q17 嵌套户型 → GUI 平铺视图的一次性投影（视图层零改动）：
 * worker 的 cfg 快照是 {model:{materials,geometry,parameters},
 * config:{domain,mesh,source,monitor}, solvers, analysis}；表单/树/3D/
 * 检查器仍按平铺键读写本地 cfg。投影保留 model/config 原键（serverItems
 * 的 diff 基准两态都能读），顶层平铺键从两段浅抄。
 * 2026-11-23：从 window 挂载改为模块导出——ESM 迁移后 `typeof guiViewOf`
 * 在模块作用域永远是 undefined，投影被静默跳过（树/3D 吃不到模型）。 */
export function guiViewOf(c) {
  if (!c || typeof c !== "object") return c;
  if (!c.model && !c.config) return c;
  const m = c.model || {}, k = c.config || {};
  return Object.assign({}, c, m, k);
}

