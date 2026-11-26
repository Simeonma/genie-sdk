
/* 对象 / 文件 / 控制 / 视图 图标（2026-11-24 定型 34 枚）。
 *
 * 两路来源，同一个入口 previewEl():
 *   1) PNG 素材：static/assets/icons/<key>.png（3D 渲染那套 = 对象族；
 *      2D 浅色那套 = 文件/控制/视图）——绝大多数 key 走这条；
 *   2) 内联 SVG 回退：还没有成品的 8 个 key（layout / params / domain /
 *      视图 proj·wire·ruler·grid·shot）继续用旧线条图，功能不丢。
 *
 * 全部素材统一用界面 accent #1F62B0；控制与视图是"裸记号、无卡片底"
 * （遵 style.css「按钮不带盒」）。语义由按钮 aria-label / 相邻文本承担，
 * 图标本身一律 aria-hidden。用法：btn.appendChild(previewEl("box"))
 */
"use strict";

export const GEO_TYPE_LABEL = {
  box: "Box", sphere: "Sphere", cylinder: "Cylinder",
  ngon_z: "N-gon", ellipse_z: "Ellipse", ring_z: "Ring",
  polygon_z: "Polygon", polygon_swept: "Swept",
};

/* key → 素材相对路径（相对 index.html 所在目录） */
export const ICON_SRC = {
  "materials": "/static/assets/icons/materials.png",
  "mat_const": "/static/assets/icons/mat_const.png",
  "mat_lorentz": "/static/assets/icons/mat_lorentz.png",
  "mon_time": "/static/assets/icons/mon_time.png",
  "mon_freq": "/static/assets/icons/mon_freq.png",
  "mon_index": "/static/assets/icons/mon_index.png",
  "mon_mode": "/static/assets/icons/mon_mode.png",
  "soft_source": "/static/assets/icons/soft_source.png",
  "plane_wave": "/static/assets/icons/plane_wave.png",
  "gaussian_beam": "/static/assets/icons/gaussian_beam.png",
  "mode": "/static/assets/icons/mode.png",
  "box": "/static/assets/icons/box.png",
  "sphere": "/static/assets/icons/sphere.png",
  "cylinder": "/static/assets/icons/cylinder.png",
  "ring_z": "/static/assets/icons/ring_z.png",
  "ngon_z": "/static/assets/icons/ngon_z.png",
  "polygon_z": "/static/assets/icons/polygon_z.png",
  "polygon_swept": "/static/assets/icons/polygon_swept.png",
  "structure": "/static/assets/icons/structure.png",
  "file_new": "/static/assets/icons/file_new.png",
  "file_open": "/static/assets/icons/file_open.png",
  "file_save": "/static/assets/icons/file_save.png",
  "file_save_as": "/static/assets/icons/file_save_as.png",
  "file_workspace": "/static/assets/icons/file_workspace.png",
  "play": "/static/assets/icons/play.png",
  "build": "/static/assets/icons/build.png",
  "edit_mode": "/static/assets/icons/edit_mode.png",
  "mesh": "/static/assets/icons/mesh.png",
  "solver_fdtd": "/static/assets/icons/solver_fdtd.png",
  "solver_mode": "/static/assets/icons/solver_mode.png",
  "view_fit": "/static/assets/icons/view_fit.png",
  "view_section": "/static/assets/icons/view_section.png",
  "view_xray": "/static/assets/icons/view_xray.png",
  "ellipse_z": "/static/assets/icons/cylinder.png",
};

/* 还没有成品素材、继续用内联 SVG 的 key */
export const PREVIEW_PATHS = {
  layout: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M21.60 6.40 L26.40 6.40 L26.40 25.60 L21.60 25.60 Z" fill="#1f62b0"/><path d="M5.60 7.20 L26.40 7.20 L26.40 24.80 L5.60 24.80 Z" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linejoin="round"/><path d="M12.00 7.20 L12.00 24.80" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M21.60 7.20 L21.60 24.80" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  params: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M5.60 10.40 L26.40 10.40" fill="none" stroke="#8e9fb4" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M5.60 21.60 L26.40 21.60" fill="none" stroke="#8e9fb4" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12.0" cy="10.4" r="3.2" fill="#1f62b0"/><circle cx="21.6" cy="21.6" r="3.2" fill="#4e5f75"/></svg>',
  domain: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M16.00 5.40 L26.00 10.60 L16.00 15.80 L6.00 10.60 Z" fill="#1f62b0" fill-opacity="0.08"/><path d="M16.00 5.40 L16.00 14.80 L6.00 20.00 M16.00 14.80 L26.00 20.00" fill="none" stroke="#36465a" stroke-opacity="0.24" stroke-width="1.3" stroke-linejoin="round"/><path d="M16.00 5.40 L26.00 10.60 L26.00 20.00 L16.00 25.20 L6.00 20.00 L6.00 10.60 Z" fill="none" stroke="#36465a" stroke-opacity="0.68" stroke-width="1.6" stroke-linejoin="round"/><path d="M6.00 10.60 L16.00 15.80" fill="none" stroke="#36465a" stroke-opacity="0.52" stroke-width="1.3" stroke-linejoin="round"/><path d="M16.00 15.80 L26.00 10.60" fill="none" stroke="#36465a" stroke-opacity="0.52" stroke-width="1.3" stroke-linejoin="round"/><path d="M16.00 15.80 L16.00 25.20" fill="none" stroke="#36465a" stroke-opacity="0.52" stroke-width="1.3" stroke-linejoin="round"/><path d="M8.31 9.40 L6.00 10.60 L6.00 13.20 M23.69 9.40 L26.00 10.60 L26.00 13.20 M6.00 17.40 L6.00 20.00 L8.31 21.20 M26.00 17.40 L26.00 20.00 L23.69 21.20" fill="none" stroke="#1f62b0" stroke-opacity="0.95" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  view_proj: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M9.00 12.00 L9.00 20.00" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M17.00 11.00 L27.00 11.00 L27.00 21.00 L17.00 21.00 Z" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M9.00 12.00 L15.51 11.19" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M9.00 20.00 L15.51 20.81" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  view_wire: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M16.00 8.40 L23.20 12.40" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M23.20 12.40 L23.20 19.60" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M23.20 19.60 L16.00 23.60" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M16.00 23.60 L8.80 19.60" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.80 19.60 L8.80 12.40" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.80 12.40 L16.00 8.40" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.80 12.40 L16.00 16.40" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M16.00 16.40 L23.20 12.40" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/><path d="M16.00 16.40 L16.00 23.60" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  view_ruler: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M24.48 11.76 L20.24 7.52 L7.52 20.24 L11.76 24.48 Z" fill="#ffffff" stroke="#4e5f75" stroke-width="2.0" stroke-linejoin="round"/><path d="M17.70 10.06 L19.54 11.90" fill="none" stroke="#8e9fb4" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M14.90 12.86 L16.74 14.70" fill="none" stroke="#8e9fb4" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M12.10 15.66 L13.94 17.50" fill="none" stroke="#8e9fb4" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M9.30 18.46 L11.14 20.30" fill="none" stroke="#8e9fb4" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M24.48 11.76 L20.24 7.52" fill="none" stroke="#1f62b0" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  view_grid: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><path d="M12.87 6.60 L12.87 25.40" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.60 12.87 L25.40 12.87" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M19.13 6.60 L19.13 25.40" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.60 19.13 L25.40 19.13" fill="none" stroke="#1f62b0" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.00 6.00 L26.00 6.00 L26.00 26.00 L6.00 26.00 Z" fill="none" stroke="#4e5f75" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  view_shot: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="28" height="28"><defs></defs><rect x="13.6" y="8.75" width="2.8" height="2.0" rx="0.5" fill="#1f62b0"/><rect x="7.5" y="10.75" width="17.0" height="10.5" rx="1.8" fill="#1f62b0"/><circle cx="16" cy="16" r="4.4" fill="#ffffff"/><circle cx="16" cy="16" r="2.8" fill="#1f62b0"/><rect x="9.0" y="12.0" width="2.2" height="2.2" rx="0.4" fill="#ffffff"/></svg>',
};

export const _previewCache = {};

/* PNG 素材 → <img>（缓存后 clone，避免重复解码） */
function _imgEl(key) {
  let el = _previewCache["img:" + key];
  if (!el) {
    el = document.createElement("img");
    el.className = "pv-img";
    el.src = ICON_SRC[key];
    el.alt = "";
    el.draggable = false;
    el.setAttribute("aria-hidden", "true");
    _previewCache["img:" + key] = el;
  }
  return el.cloneNode(false);
}

/* 内联 SVG 回退 → 解析一次后 clone */
function _svgEl(key) {
  if (!_previewCache[key]) {
    _previewCache[key] = new DOMParser()
      .parseFromString(PREVIEW_PATHS[key], "image/svg+xml").documentElement;
  }
  const pv = _previewCache[key].cloneNode(true);
  pv.setAttribute("aria-hidden", "true");
  return pv;
}

/* 取一个图标元素：优先 PNG 素材，其次内联 SVG，彻底未知才退到 box 的素材。
 * 两条路径都 aria-hidden（语义由按钮 aria-label / 相邻文本承担）。 */
export function previewEl(key) {
  if (ICON_SRC[key]) return _imgEl(key);
  if (PREVIEW_PATHS[key]) return _svgEl(key);
  return _imgEl("box");
}

(function installPreviewDefs() {
  if (!document.body) return;
  if (document.getElementById("preview-defs-sprite")) return;
  const NS = "http://www.w3.org/2000/svg";
  const sprite = document.createElementNS(NS, "svg");
  sprite.id = "preview-defs-sprite";
  sprite.setAttribute("aria-hidden", "true");
  sprite.setAttribute("focusable", "false");
  sprite.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
  for (const src of Object.values(PREVIEW_PATHS)) {
    const doc = new DOMParser().parseFromString(src, "image/svg+xml");
    for (const defs of doc.querySelectorAll("defs"))
      sprite.appendChild(doc.adoptNode(defs.cloneNode(true)));
  }
  document.body.prepend(sprite);
})();
