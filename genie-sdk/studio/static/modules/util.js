/* modules/util.js — 基础工具（$——全前端唯一公共依赖）
 * G5 拆分（2026-10-14）：自 app.js 机械切出，零行为变化；
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
/* ── 基础 ──────────────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);

/* v1 同款树元数据（GEO_TYPE_LABEL 在 previews.js） */
