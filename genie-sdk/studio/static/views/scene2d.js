/* Genie FDTD service — 3D 预览的 Canvas 2D 等距投影降级（Phase B2，2026-08-30）
 *
 * 当浏览器 WebGL 不可用（硬件加速关闭/显卡黑名单/远程桌面）时，
 * app.js 用本类顶替 Scene3D：同一套鸭子类型接口（init/updateScene/
 * setPmlVisible/setGridVisible/setWireframe/resetCamera/render/
 * takeScreenshot + showPml/showGrid/showWireframe 字段）。
 *
 * 能力与局限：等距线框（可拖拽绕 Y 旋转、滚轮缩放）——域框/BC 面边色/
 * PML 外框/结构线框/源与监视器标记/网格线抽稀；无光照无透明填充，
 * 视觉上就是旧示波器风格的技术线框图。Canvas 2D 处处可用。
 */
"use strict";

class Scene2D {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.showPml = true; this.showGrid = false; this.showWireframe = true;
    this.wireMode = "edges";
    /* 能力表（G39）：2D 降级只有等距投影 + 线框叠加，其余显示模式没有实现；
     * scene.js 依此把对应浮条按钮置灰，避免"能点但不生效"的假按钮。 */
    this.capabilities = {
      rulers: false, xray: false, projection: false,
      section: false, highlight: false,
      wireModes: ["solid", "edges"],
    };
    this._cfg = null;
    this._yaw = Math.PI / 6;      // 绕 Y 旋转角（拖拽改）
    this._scale = 1; this._cx = 0; this._cy = 0;
    this._dirty = true;
  }

  init() {
    this.canvas = document.createElement("canvas");
    this.canvas.style.width = "100%";
    this.canvas.style.height = "100%";
    this.canvas.style.display = "block";
    this.canvas.style.cursor = "grab";
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    if (!this.ctx) throw new Error("Canvas 2D 不可用");

    let drag = null;
    this.canvas.addEventListener("mousedown", (e) => {
      drag = { x: e.clientX, yaw: this._yaw };
      this.canvas.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      this._yaw = drag.yaw + (e.clientX - drag.x) * 0.008;
      this._dirty = true;
    });
    window.addEventListener("mouseup", () => {
      drag = null;
      this.canvas.style.cursor = "grab";
    });
    this.canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      this._scale *= e.deltaY < 0 ? 1.12 : 1 / 1.12;
      this._dirty = true;
    }, { passive: false });

    this._resizeTimer = null;
    window.addEventListener("resize", () => {
      if (this._resizeTimer) clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => this.onResize(), 100);
    });
    this.onResize();
  }

  onResize() {
    const w = this.container.clientWidth || 400;
    const h = this.container.clientHeight || 300;
    this.canvas.width = w * Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.height = h * Math.min(window.devicePixelRatio || 1, 2);
    this._dirty = true;
  }

  /* 3D → 2D：绕 Y 旋转 yaw 后等距投影 */
  _proj(p) {
    const c = Math.cos(this._yaw), s = Math.sin(this._yaw);
    const xr = p[0] * c - p[2] * s;
    const zr = p[0] * s + p[2] * c;
    return [this._cx + (xr - zr) * this._scale,
            this._cy + (xr + zr) * 0.5 * this._scale - p[1] * this._scale];
  }

  /* 色板 hex 数值 → CSS 字符串（画布色单源 scene_palette.js，P2-6；
   * 本文件不再写字面色值） */
  _cssHex(hex) { return "#" + hex.toString(16).padStart(6, "0"); }
  _cssRgba(hex, a) {
    return "rgba(" + ((hex >> 16) & 255) + "," + ((hex >> 8) & 255)
      + "," + (hex & 255) + "," + a + ")";
  }

  _bcColor(kind) {
    /* 与 Scene3D._bcColor 同一数值表（SCENE_PALETTE.BC canon）——同语义
     * 两径同值（P2-6：2D 曾 dodgerblue 系，现统一派生，杜绝再分叉） */
    const hex = SCENE_PALETTE.BC[kind] || SCENE_PALETTE.BC.fallback;
    /* fallback = --accent 亮档 #4E8FD9（同 logo 渐变二阶，勿回外族亮蓝） */
    return this._cssHex(hex);
  }

  /* 与 Scene3D._pmlBox 同款：每 pml 面 layers × 该轴名义格距 */
  _pmlBox(cfg, d) {
    const dom = cfg.domain || {};
    const mesh = cfg.mesh || {};
    const dx = mesh.dx || 20e-9;
    const hs = { x_min: dx, x_max: dx, y_min: mesh.dy || dx, y_max: mesh.dy || dx,
                 z_min: mesh.dz || dx, z_max: mesh.dz || dx };
    const out = { xmin: d.xmin, xmax: d.xmax, ymin: d.ymin, ymax: d.ymax,
                  zmin: d.zmin, zmax: d.zmax };
    let any = false;
    for (const f of ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]) {
      const b = (dom.boundary || {})[f];
      if (b && b.kind === "pml") {
        const t = (b.layers || 12) * hs[f];
        if (f.endsWith("_min")) out[f[0] + "min"] -= t;
        else out[f[0] + "max"] += t;
        any = true;
      }
    }
    return any ? out : null;
  }

  _boxCorners(x0, x1, y0, y1, z0, z1) {
    return [
      [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
      [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ];
  }

  _edgesOf(corners) {
    return [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4],
            [0, 4], [1, 5], [2, 6], [3, 7]];
  }

  _strokeBox(x0, x1, y0, y1, z0, z1, color, width, dash) {
    const cs = this._boxCorners(x0, x1, y0, y1, z0, z1).map((p) => this._proj(p));
    this.ctx.strokeStyle = color;
    this.ctx.lineWidth = width || 1;
    this.ctx.setLineDash(dash || []);
    this.ctx.beginPath();
    for (const [a, b] of this._edgesOf()) {
      this.ctx.moveTo(cs[a][0], cs[a][1]);
      this.ctx.lineTo(cs[b][0], cs[b][1]);
    }
    this.ctx.stroke();
    this.ctx.setLineDash([]);
  }

  updateScene(cfg) {
    this.updateCount = (this.updateCount || 0) + 1;   // 重渲计数（UI 同步批 1 e2e 场景标志）
    this._cfg = cfg;
    const dom = cfg.domain || {};
    const d = {
      xmin: dom.x_range ? dom.x_range[0] : -1e-6, xmax: dom.x_range ? dom.x_range[1] : 1e-6,
      ymin: dom.y_range ? dom.y_range[0] : -1e-6, ymax: dom.y_range ? dom.y_range[1] : 1e-6,
      zmin: dom.z_range ? dom.z_range[0] : -1e-6, zmax: dom.z_range ? dom.z_range[1] : 1e-6,
    };
    this._d = d;
    this._fitView();
    this._dirty = true;
  }

  _fitView() {
    const d = this._d;
    const w = this.container.clientWidth || 400;
    const h = this.container.clientHeight || 300;
    // 包络：等距投影下 bbox 宽 ≈ (sx+sz)，高 ≈ (sx+sz)/2 + sy
    const ew = (d.xmax - d.xmin) + (d.zmax - d.zmin);
    const eh = ew / 2 + (d.ymax - d.ymin);
    this._scale = Math.min(w / (ew * 1.25), h / (eh * 1.35)) || 1;
    const c3 = [(d.xmin + d.xmax) / 2, (d.ymin + d.ymax) / 2, (d.zmin + d.zmax) / 2];
    const pc = this._projRaw(c3);
    this._cx = w / 2 - pc[0] * this._scale;
    this._cy = h / 2 - pc[1] * this._scale;
  }

  _projRaw(p) {
    const c = Math.cos(this._yaw), s = Math.sin(this._yaw);
    const xr = p[0] * c - p[2] * s;
    const zr = p[0] * s + p[2] * c;
    return [xr - zr, (xr + zr) * 0.5 - p[1]];
  }

  render() {
    if (!this._dirty || !this._cfg || !this._d) return;
    this._dirty = false;
    const d = this._d;
    const cfg = this._cfg;
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = this._cssHex(SCENE_PALETTE.BG.CANVAS_2D);
    ctx.fillRect(0, 0, W, H);

    // PML 外框（虚线琥珀）
    if (this.showPml) {
      const pb = this._pmlBox(cfg, d);
      if (pb) this._strokeBox(pb.xmin, pb.xmax, pb.ymin, pb.ymax,
                              pb.zmin, pb.zmax, this._cssRgba(SCENE_PALETTE.BC.pml, 0.35), 1, [4, 4]);
    }

    // 域框（六面 BC 色分着 12 条边：顶/底环 + 竖棱按所属面着色）
    const bnd = cfg.domain ? (cfg.domain.boundary || {}) : {};
    const bc = (f) => (bnd[f] && bnd[f].kind) || "pec";
    // 简化：整个域框按主色（z_min 面）+ 四条竖棱按 x/y 面色
    this._strokeBox(d.xmin, d.xmax, d.ymin, d.ymax, d.zmin, d.zmax,
                    this._bcColor(bc("z_min")), 1.4);
    const cs = this._boxCorners(d.xmin, d.xmax, d.ymin, d.ymax, d.zmin, d.zmax)
      .map((p) => this._proj(p));
    // 四条竖棱按各自贴合的 x/y 边界面着色
    ctx.lineWidth = 1.6;
    for (const [i, face] of [[0, "x_min"], [1, "x_max"], [2, "y_max"], [3, "y_min"]]) {
      ctx.strokeStyle = this._bcColor(bc(face));
      ctx.beginPath();
      ctx.moveTo(cs[i][0], cs[i][1]);
      ctx.lineTo(cs[i + 4][0], cs[i + 4][1]);
      ctx.stroke();
    }

    // 网格线（三个远侧面，抽稀到 ≤48/轴）
    if (this.showGrid) {
      const mesh = cfg.mesh || {};
      const dx = mesh.dx || 20e-9;
      ctx.strokeStyle = this._cssRgba(SCENE_PALETTE.GRID.CANVAS_2D, 0.35);
      ctx.lineWidth = 0.7;
      for (const [lo, hi, step, fixedAxis] of [
        [d.xmin, d.xmax, mesh.dx || dx, "z"], [d.zmin, d.zmax, mesh.dz || dx, "x"],
      ]) {
        const n = Math.floor((hi - lo) / step);
        if (n < 1) continue;
        const stride = Math.ceil(n / 48);
        ctx.beginPath();
        for (let i = 0; i <= n; i += stride) {
          const t = lo + i * step;
          let p1, p2;
          if (fixedAxis === "z") {
            p1 = this._proj([t, d.ymin, d.zmin]); p2 = this._proj([t, d.ymax, d.zmin]);
          } else {
            p1 = this._proj([d.xmin, d.ymin, t]); p2 = this._proj([d.xmin, d.ymax, t]);
          }
          ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]);
        }
        ctx.stroke();
      }
    }

    // 结构线框
    for (const g of (cfg.geometry || [])) {
      const color = this._cssHex(matColor(g.material));  // 材料色与 3D 同源（SCENE_PALETTE.MATERIALS）
      if (g.type === "box") {
        const xr = g.x_range || [-1e-7, 1e-7], yr = g.y_range || [-1e-7, 1e-7],
              zr = g.z_range || [-1e-7, 1e-7];
        this._strokeBox(xr[0], xr[1], yr[0], yr[1], zr[0], zr[1], color, 1.3);
      } else if (g.type === "sphere" || g.type === "cylinder") {
        const c = g.center || [0, 0, 0];
        const r = Math.abs(g.radius || 1e-7);
        const pc = this._proj(c);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.3;
        if (g.type === "sphere") {
          ctx.beginPath();
          ctx.ellipse(pc[0], pc[1], r * this._scale * 1.25, r * this._scale * 0.8, 0, 0, Math.PI * 2);
          ctx.stroke();
        } else {
          const h = Math.abs(g.height || 2e-7);
          const rx = r * this._scale * 1.25, ry = r * this._scale * 0.55;
          const top = this._proj([c[0], c[1] + h / 2, c[2]]);
          const bot = this._proj([c[0], c[1] - h / 2, c[2]]);
          for (const [cc, dir] of [[top, 1], [bot, -1]]) {
            ctx.beginPath();
            ctx.ellipse(cc[0], cc[1], rx, ry, 0, 0, Math.PI * 2);
            ctx.stroke();
          }
          ctx.beginPath();
          ctx.moveTo(top[0] - rx, top[1]); ctx.lineTo(bot[0] - rx, bot[1]);
          ctx.moveTo(top[0] + rx, top[1]); ctx.lineTo(bot[0] + rx, bot[1]);
          ctx.stroke();
        }
      }
      // 材料名标签
      if (g.material) {
        const pc2 = this._proj(g.type === "box"
          ? [(g.x_range[0] + g.x_range[1]) / 2, (g.y_range ? (g.y_range[0] + g.y_range[1]) / 2 : 0),
             (g.z_range ? (g.z_range[0] + g.z_range[1]) / 2 : 0)]
          : (g.center || [0, 0, 0]));
        ctx.fillStyle = color;
        ctx.font = "11px ui-monospace, monospace";
        ctx.fillText(String(g.material).slice(0, 12), pc2[0] + 4, pc2[1] - 4);
      }
    }

    // 源 / 监视器标记
    for (const s of (cfg.source || [])) {
      const p = this._proj(s.center || [0, 0, 0]);
      ctx.fillStyle = this._cssHex(SCENE_PALETTE.MARKERS.SOURCE);
      ctx.beginPath();
      ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
      ctx.fill();
      if (s.name) {
        ctx.font = "10px ui-monospace, monospace";
        ctx.fillText(String(s.name).slice(0, 10), p[0] + 6, p[1] + 3);
      }
    }
    for (const m of (cfg.monitor || [])) {
      const p = this._proj(m.center || [0, 0, 0]);
      ctx.strokeStyle = this._cssHex(SCENE_PALETTE.MARKERS.MONITOR);
      ctx.lineWidth = 1.4;
      ctx.strokeRect(p[0] - 4, p[1] - 4, 8, 8);
      if (m.name) {
        ctx.fillStyle = this._cssHex(SCENE_PALETTE.MARKERS.MONITOR);
        ctx.font = "10px ui-monospace, monospace";
        ctx.fillText(String(m.name).slice(0, 10), p[0] + 7, p[1] + 3);
      }
    }

    // 角落轴向指示（随 yaw 旋转）
    const ox = 46, oy = H - 40, L = 26;
    ctx.lineWidth = 1.6;
    const axisCol = this._cssHex(SCENE_PALETTE.MARKERS.AXIS);
    for (const [v, lab] of [
      [[1, 0, 0], "X"], [[0, 1, 0], "Y"], [[0, 0, 1], "Z"],
    ]) {
      const pv = this._projRaw(v);
      const len = Math.hypot(pv[0], pv[1]) || 1;
      ctx.strokeStyle = axisCol;
      ctx.beginPath();
      ctx.moveTo(ox, oy);
      ctx.lineTo(ox + pv[0] / len * L, oy + pv[1] / len * L);
      ctx.stroke();
      ctx.fillStyle = axisCol;
      ctx.font = "bold 10px sans-serif";
      ctx.fillText(lab, ox + pv[0] / len * (L + 5) - 3, oy + pv[1] / len * (L + 5) + 3);
    }
  }

  setPmlVisible(v) { this.showPml = v; this._dirty = true; }
  setGridVisible(v) { this.showGrid = v; this._dirty = true; }
  setWireframe(v) { this.showWireframe = v; this._dirty = true; }
  setWireframeMode(m) {
    this.wireMode = m;
    this.showWireframe = m !== "solid";
    this._dirty = true;
  }
  /* 与 Scene3D 同名的显示开关（2D 降级下不实现，只为按钮接线不报错） */
  setRulers(v) { this.showRulers = v; this._dirty = true; }
  setXray(v) { this.showXray = v; this._dirty = true; }
  setProjection(v) { this.projection = v; this._dirty = true; }
  setSection(v) { this.showClip = v; this._dirty = true; }
  setClipAxis(v) { this.clipAxis = v; this._dirty = true; }
  setClipPos(v) { this.clipT = v; this._dirty = true; }
  highlightSelection() { /* 2D 视图不做选中描边 */ }
  clearHighlight() { /* 同上 */ }
  resetCamera() { this._yaw = Math.PI / 6; if (this._d) this._fitView(); this._dirty = true; }
  takeScreenshot() {
    const link = document.createElement("a");
    link.download = "fdtd_scene_2d.png";
    link.href = this.canvas.toDataURL("image/png");
    link.click();
  }
}
