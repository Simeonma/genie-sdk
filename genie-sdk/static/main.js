/* static/main.js — 唯一入口：按依赖图拉起全部 GUI 模块（2026-11-22）
 * 经典 script 顺序 → 显式 import；vendor（three/OrbitControls）仍是
 * 全局 classic script，模块里直接用全局 THREE。 */
import "./icons.js";
import "./views/previews.js";
import "./views/scene_palette.js";
import "./views/scene3d.js";
import "./views/scene2d.js";
import "./views/form.js";
import "./modules/util.js";
import "./modules/app.js";
import "./modules/api.js";
import "./modules/state.js";
import "./modules/tree.js";
import "./modules/inspector.js";
import "./modules/sweep.js";
import "./modules/scene.js";
import "./modules/toolbar.js";
import "./modules/script.js";
import "./modules/term.js";
import "./modules/result.js";
import "./modules/dock.js";
import "./modules/boot.js";
