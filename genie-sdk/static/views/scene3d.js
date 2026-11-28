import { selectObj } from "../modules/inspector.js";
import { fdtdConfig } from "../modules/util.js";
import { SCENE_PALETTE } from "./scene_palette.js";
"use strict";

/* 材料调色：优先取材料节点自带的 color（2026-10-21 用户定案：对象按材料
 * 上色；脚本/表单可定义，材料库材料自带预设色——G46），否则按材料名稳定
 * 散列取调色板一色（同名同色）。
 * 色值单源 = scene_palette.js（P2-6）；勿在本文件再写字面。 */
export const MAT_PALETTE = SCENE_PALETTE.MATERIALS;
export function matColor(name) {
  let h = 0;
  const s = String(name || "mat");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return MAT_PALETTE[h % MAT_PALETTE.length];
}
/* "#RRGGBB" / "RRGGBB" → 0xRRGGBB；非法返回 null */
export function parseHexColor(v) {
  if (typeof v !== "string") return null;
  const s = v.trim().replace(/^#/, "");
  return /^[0-9a-fA-F]{6}$/.test(s) ? parseInt(s, 16) : null;
}

export class Scene3D {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.scene = null; this.camera = null; this.renderer = null; this.controls = null;
    this.domainGroup = null; this.structureGroup = null; this.markerGroup = null;
    this.meshGroup = null;
    this.showPml = true; this.showGrid = false; this.showWireframe = true;
    this.wireMode = "edges";     /* solid | edges | wire —— 结构着色三态 */
    this.showRulers = true;      /* 边框坐标尺（刻度 + µm 读数） */
    this.showXray = false;       /* X 光：结构半透明，看穿嵌套体 */
    this.projection = "ortho";   /* ortho | persp —— 透视/正交切换 */
    this.showClip = false;       /* 剖面切片 */
    this.clipAxis = "z";         /* 剖切法轴 */
    this.clipT = 0.5;            /* 剖切位置（0..1 沿该轴） */
    this._clipPlane = null;
    this._sel = null;            /* 当前选中 {kind, index} → 3D 高亮 */
    this._srcGroups = {}; this._monGroups = {};   /* 源/监视器的对象组（高亮用） */
    /* 能力表（G39）：scene.js 依此决定浮条按钮可用性，2D 降级是它的补集 */
    this.capabilities = {
      rulers: true, xray: true, projection: true,
      section: true, highlight: true,
      wireModes: ["solid", "edges", "wire"],
    };
    this.structureMeshes = [];
    this._geoIdxMeshes = {};
    this.axesRenderer = null; this.axesScene = null; this.axesCamera = null;
    this._cfg = null;
    this._dirty = true;
    /* 简化渲染（裁决 D）：对象数超阈值 → InstancedMesh 合批（裁决 B），
     * 线框/标签/拾取高亮让位性能，徽标示警 */
    this._simThreshold = 600;
    this._simplified = false; this._simCount = 0; this._simBox = null;
    this._simBadge = null;
  }

  /* 画布 2D 纹理（label sprite）用：色板 hex 数值 → rgba 字符串，
   * alpha 由调用方给；避免本文件出现裸颜色字面（P2-6） */
  _cssRgba(hex, alpha) {
    return "rgba(" + ((hex >> 16) & 255) + "," + ((hex >> 8) & 255)
      + "," + (hex & 255) + "," + alpha + ")";
  }

  markDirty() { this._dirty = true; }

  init() {
    const w = this.container.clientWidth || 400;
    const h = this.container.clientHeight || 300;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(w, h);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // 透明画布: 露出 #scene-panel 的 CSS 渐变背景(2026-08-31 质感轮)
    this.renderer.setClearColor(SCENE_PALETTE.BLACK, 0);
    /* 色彩管线（2026-10-21）：sRGB 输出 + 线性（不做胶片曲线）。
     * ACES 会把中调抬亮并去饱和——材料色全被冲成粉白（实测 #C07070 渲染出
     * #E3A9A9）。这是技术预览器，不是电影渲染：要的是"填什么色就是什么色"，
     * 所以走 NoToneMapping，明暗对比交给灯光强度（总辐照 ≈1.0）。 */
    this.renderer.outputEncoding = THREE.sRGBEncoding;
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    /* 剖面切片（2026-10-21）：材质级裁剪需要打开局部裁剪开关 */
    this.renderer.localClippingEnabled = true;
    this.container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = null;   // 透明, CSS 渐变透出
    this.scene.fog = new THREE.Fog(SCENE_PALETTE.BG.FOG, 1e-4, 2e-3);
    /* 影棚环境贴图（2026-10-21）：无 envMap 的 MeshStandard/Physical 只有直射光
     * 贡献，实体读作「没上材质的灰块」。用一张 Canvas 渐变天空（上白下冷灰 +
     * 柔光斑）经 PMREM 卷积成环境贴图——零外部资源，和浅色工作台同一光位，
     * 实体立刻有柔和明暗过渡。 */
    try {
      const envCanvas = document.createElement("canvas");
      envCanvas.width = 256; envCanvas.height = 128;
      const ectx = envCanvas.getContext("2d");
      const egrd = ectx.createLinearGradient(0, 0, 0, 128);
      egrd.addColorStop(0.0, "#ffffff");
      egrd.addColorStop(0.42, "#eaf1f9");
      egrd.addColorStop(0.55, "#ccd9e8");
      egrd.addColorStop(1.0, "#93a6bb");
      ectx.fillStyle = egrd; ectx.fillRect(0, 0, 256, 128);
      const espot = ectx.createRadialGradient(70, 30, 2, 70, 30, 46);
      espot.addColorStop(0, "rgba(255,255,255,0.95)");
      espot.addColorStop(1, "rgba(255,255,255,0)");
      ectx.fillStyle = espot; ectx.fillRect(0, 0, 140, 90);
      const envTex = new THREE.CanvasTexture(envCanvas);
      envTex.encoding = THREE.sRGBEncoding;
      envTex.mapping = THREE.EquirectangularReflectionMapping;
      const pmrem = new THREE.PMREMGenerator(this.renderer);
      this.scene.environment = pmrem.fromEquirectangular(envTex).texture;
      pmrem.dispose(); envTex.dispose();
    } catch (err) { /* 环境贴图失败不阻塞渲染 */ }

    const aspect = w / h;
    const size0 = 8e-7;
    this.camera = new THREE.OrthographicCamera(
      -size0 * aspect, size0 * aspect, size0, -size0, 1e-9, 1e-2);
    this.camera.position.set(8e-7, 6e-7, 1.2e-6);
    this.camera.lookAt(0, 0, 0);

    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.rotateSpeed = 0.6;
    this.controls.zoomSpeed = 1.2;
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    /* 相机一动（含阻尼收敛期）→ 标脏 → 下一帧重画；静止即停画。
     * 坐标尺依附相机：换面后要换到最靠相机的棱上（12 次点积，极廉） */
    this.controls.addEventListener("change", () => {
      this._updateRulers();
      this.markDirty();
    });
    /* 拾取（2026-10-21 用户需求）：点一下 3D 里的对象＝选中它（树/表单同步）。
     * 只有"点击"才算，拖动视角（>4px）不触发；点空白处清掉选中。 */
    const cv = this.renderer.domElement;
    this._pickDown = null;
    cv.addEventListener("pointerdown", (ev) => {
      this._pickDown = { x: ev.clientX, y: ev.clientY };
    });
    cv.addEventListener("pointerup", (ev) => {
      const d = this._pickDown;
      this._pickDown = null;
      if (!d) return;
      if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 4) return;
      this.pickAt(ev.clientX, ev.clientY);
    });
    this._simBadge = document.createElement("div");
    this._simBadge.className = "sim-badge hidden";
    this.container.appendChild(this._simBadge);

    this.scene.add(new THREE.HemisphereLight(
      SCENE_PALETTE.LIGHTS.HEMI_SKY, SCENE_PALETTE.LIGHTS.HEMI_GROUND, 0.24));
    this.scene.add(new THREE.AmbientLight(SCENE_PALETTE.LIGHTS.AMBIENT, 0.06));
    const dl1 = new THREE.DirectionalLight(SCENE_PALETTE.LIGHTS.KEY, 0.82);
    dl1.position.set(6e-7, 9e-7, 7e-7); this.scene.add(dl1);
    const dl2 = new THREE.DirectionalLight(SCENE_PALETTE.LIGHTS.RIM, 0.16);
    dl2.position.set(-7e-7, -3e-7, -6e-7); this.scene.add(dl2);
    const dl3 = new THREE.DirectionalLight(SCENE_PALETTE.LIGHTS.FILL, 0.18);
    dl3.position.set(-6e-7, 4e-7, -8e-7); this.scene.add(dl3);

    this.domainGroup = new THREE.Group();
    this.structureGroup = new THREE.Group();
    this.markerGroup = new THREE.Group();
    this.meshGroup = new THREE.Group();
    // 直接 Three.js 坐标：用户 X→X、Y→Y、Z→Z（Y-up）
    this.scene.add(this.domainGroup);
    this.scene.add(this.meshGroup);
    this.scene.add(this.structureGroup);
    this.scene.add(this.markerGroup);

    // ── HUD 坐标轴（第二渲染器，跟随主视角方向）──
    this.axesRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    // 140px @ bottom/left 14px：与 .scene-tools 的 14px 边距体系对齐（P0-4）
    this.axesRenderer.setSize(140, 140);
    this.axesRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.axesRenderer.setClearColor(SCENE_PALETTE.BLACK, 0);
    this.axesRenderer.domElement.style.position = "absolute";
    this.axesRenderer.domElement.style.bottom = "14px";
    this.axesRenderer.domElement.style.left = "14px";
    this.axesRenderer.domElement.style.zIndex = "10";
    this.axesRenderer.domElement.style.pointerEvents = "none";
    this.container.appendChild(this.axesRenderer.domElement);

    this.axesScene = new THREE.Scene();
    const axLen = 24, axFov = 58;
    this.axesCamera = new THREE.OrthographicCamera(-axFov, axFov, axFov, -axFov, 0.1, 300);
    this.axesCamera.position.set(70, 90, -55);
    this.axesCamera.lookAt(0, 0, 0);
    const axColor = SCENE_PALETTE.MARKERS.AXIS;
    /* 轴向三箭头（2026-10-21 重刻）：ArrowHelper 的杆是 1px 硬线，浅底上会被
     * 采样成断续点——视觉焦点落在左下角最容易读成"渲染坏了"。改用细圆柱杆 +
     * 小圆锥头（msaa 抗锯齿），单色弱钢蓝，读作仪器角标。 */
    const axMat = new THREE.MeshBasicMaterial(
      { color: axColor, transparent: true, opacity: 0.42 });
    const AX_UP = new THREE.Vector3(0, 1, 0);
    for (const dir of [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) {
      const d = new THREE.Vector3(dir[0], dir[1], dir[2]);
      const q = new THREE.Quaternion().setFromUnitVectors(AX_UP, d);
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.72, 0.72, axLen - 7, 8), axMat);
      shaft.quaternion.copy(q);
      shaft.position.copy(d.clone().multiplyScalar((axLen - 7) / 2));
      this.axesScene.add(shaft);
      const head = new THREE.Mesh(new THREE.ConeGeometry(3.0, 7.5, 14), axMat);
      head.quaternion.copy(q);
      head.position.copy(d.clone().multiplyScalar(axLen - 3.7));
      this.axesScene.add(head);
    }
    this.axesScene.add(new THREE.Mesh(
      new THREE.SphereGeometry(2.1, 16, 16),
      new THREE.MeshBasicMaterial({ color: axColor, transparent: true, opacity: 0.5 })));
    const labelDist = axLen + 3;
    this._addAxisLabelHUD("X", new THREE.Vector3(labelDist, 0, 0), axColor);
    this._addAxisLabelHUD("Y", new THREE.Vector3(0, labelDist, 0), axColor);
    this._addAxisLabelHUD("Z", new THREE.Vector3(0, 0, labelDist), axColor);

    this._resizeTimer = null;
    window.addEventListener("resize", () => {
      if (this._resizeTimer) clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => this.onResize(), 100);
    });
  }

  _addAxisLabelHUD(text, pos, color) {
    const canvas = document.createElement("canvas");
    canvas.width = 72; canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    /* 2026-10-21：bold sans → 600 等宽（仪器角标）；去掉了粗白晕圈（浅底上
     * 白晕不可见，只会让字读成贴纸），改用一次极轻的暗影勾边。 */
    ctx.font = "600 32px 'Cascadia Mono', Consolas, ui-monospace, monospace";
    ctx.shadowColor = "rgba(255,255,255,0.9)";
    ctx.shadowBlur = 3;
    ctx.fillStyle = this._cssRgba(SCENE_PALETTE.MARKERS.AXIS, 0.92);
    ctx.fillText(text, 36, 34);
    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
    sprite.position.copy(pos);
    sprite.scale.set(24, 21, 1);
    this.axesScene.add(sprite);
  }


  /* 主入口：直接吃 service 配置 JSON（{domain, mesh, geometry, source, monitor}）。
   * updateCount：重渲调用计数（UI 同步批 1——e2e 断言 3D 投影刷新的场景标志）。 */
  /* ── 发光晕（径向渐变 sprite + 叠加混合）──
   * 2026-10-21：旧做法是套一层低透明球壳，读作"第二个灰球"而不是光。径向
   * 渐变贴图 + AdditiveBlending 在浅底上给出真正的外溢光，且不依赖后处理。 */
  _glowSprite(size, colorHex, opacity) {
    if (!this._glowTex) {
      const c = document.createElement("canvas");
      c.width = 128; c.height = 128;
      const g = c.getContext("2d");
      const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
      grd.addColorStop(0.0, "rgba(255,255,255,1)");
      grd.addColorStop(0.18, "rgba(255,255,255,0.85)");
      grd.addColorStop(0.45, "rgba(255,255,255,0.32)");
      grd.addColorStop(0.72, "rgba(255,255,255,0.08)");
      grd.addColorStop(1.0, "rgba(255,255,255,0)");
      g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
      this._glowTex = new THREE.CanvasTexture(c);
      this._glowTex.needsUpdate = true;
    }
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: this._glowTex,
      color: new THREE.Color(colorHex).convertSRGBToLinear(),
      transparent: true, opacity: opacity == null ? 0.55 : opacity,
      depthWrite: false, depthTest: false,
      blending: THREE.AdditiveBlending,
    }));
    sp.scale.set(size, size, 1);
    return sp;
  }

  /* 材料名 → 画布色：优先材料节点自带 color，其次名字散列 */
  _materialColor(name) {
    const list = (this._cfg && this._cfg.materials) || [];
    for (const m of list) {
      if (!m || m.name !== name) continue;
      const over = parseHexColor(m.color);
      if (over !== null) return over;
      break;
    }
    return matColor(name);
  }

  /* ── 细杆边：以 12 根细长方体画盒边 ──
   * 为什么不用 LineSegments/EdgesGeometry：WebGL 线宽恒为 1px 且不做抗锯齿，
   * 浅色画布上的斜向边会被采样成断续点线（"虚线框"），是图形窗廉价感的主因。 */
  _edgeBars(cx, cy, cz, sx, sy, sz, t, color, opacity) {
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial(
      { color, transparent: true, opacity, depthWrite: false });
    const hx = sx / 2, hy = sy / 2, hz = sz / 2;
    for (const a of [-1, 1]) for (const b of [-1, 1]) {
      const mx = new THREE.Mesh(new THREE.BoxGeometry(sx + t, t, t), mat);
      mx.position.set(0, a * hy, b * hz); g.add(mx);
      const my = new THREE.Mesh(new THREE.BoxGeometry(t, sy + t, t), mat);
      my.position.set(a * hx, 0, b * hz); g.add(my);
      const mz = new THREE.Mesh(new THREE.BoxGeometry(t, t, sz + t), mat);
      mz.position.set(a * hx, b * hy, 0); g.add(mz);
    }
    g.position.set(cx, cy, cz);
    return g;
  }

  /* ── 域体坐标尺（2026-10-21 用户核心需求："3D domain 的边框要有坐标"）──
   * 结构：3D 里只画**刻度杆**（细杆，与 _edgeBars 同族）；数字走 DOM 覆盖层
   * ——投影一次、CSS 定位，任何缩放下都清晰，且能做版面避让（合成到 canvas
   * 里的贴片要么糊、要么随相机缩放变形）。
   * 每条轴有 4 条平行棱：每帧选"投影离框心最远"的那条（贴轮廓），
   * 其余三条连刻度一起隐藏。 */
  _showDomainEmptyHint(on) {
    let el = this.container.querySelector(".scene-domain-hint");
    if (!on) {
      if (el) el.classList.add("hidden");
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.className = "scene-domain-hint";
      const t = document.createElement("div");
      t.className = "sdh-title";
      t.textContent = "未定义 domain";
      const h = document.createElement("div");
      h.className = "sdh-hint";
      h.textContent = "结构照常显示；填好 x / y / z 范围后出现域框、网格与标尺";
      el.appendChild(t);
      el.appendChild(h);
      this.container.appendChild(el);
    }
    el.classList.remove("hidden");
  }

  _buildAxisRulers(cx, cy, cz, sx, sy, sz) {
    const half = [sx / 2, sy / 2, sz / 2];
    const center = [cx, cy, cz];
    const maxDim = Math.max(sx, sy, sz);
    this._rulerCenter = new THREE.Vector3(cx, cy, cz);
    /* 覆盖层：随窗口尺寸自动拉伸，pointer-events 关掉（不挡拾取） */
    if (!this._rulerLayer) {
      this._rulerLayer = document.createElement("div");
      this._rulerLayer.className = "ruler-layer";
      this.container.appendChild(this._rulerLayer);
    }
    this._rulerLayer.textContent = "";
    this._rulerLabels = [];
    this._rulerPick = [-1, -1, -1];      /* 选棱迟滞记忆（新场景重来） */
    const tickLen = maxDim * 0.022, tickT = maxDim * 0.0038;
    const tickMat = new THREE.MeshBasicMaterial({
      color: SCENE_PALETTE.DOMAIN.EDGE, transparent: true, opacity: 0.72,
      depthWrite: false,
    });
    const sets = [];
    for (let a = 0; a < 3; a++) {
      const p1 = (a + 1) % 3, p2 = (a + 2) % 3;
      const variants = [];
      for (const s1 of [-1, 1]) for (const s2 of [-1, 1]) {
        const g = new THREE.Group();
        const mid = center.slice();
        mid[p1] += s1 * half[p1];
        mid[p2] += s2 * half[p2];
        const nDiv = 6;
        for (let k = 0; k <= nDiv; k++) {
          const p = mid.slice();
          p[a] = center[a] - half[a] + (2 * half[a]) * k / nDiv;
          p[p1] += s1 * tickLen / 2;      /* Vector3 不能按下标赋值，先算数组 */
          const dims = [tickT, tickT, tickT];
          dims[p1] = tickLen;
          const bar = new THREE.Mesh(
            new THREE.BoxGeometry(dims[0], dims[1], dims[2]), tickMat);
          bar.position.set(p[0], p[1], p[2]);
          g.add(bar);
        }
        /* 数值：两端各一个；只有最长那条轴再加一个中点读数（短轴上多一个牌
         * 就会互压成糊字，7 个读数已经是这幅框的信息上限）。 */
        const isLongest = half[a] * 2 >= maxDim * 0.999;
        const labelKs = [0, nDiv];   /* 两端读数即可：多点撞读数读作噪声 */
        const labels = [];
        for (const k of labelKs) {
          const w = center[a] - half[a] + (2 * half[a]) * k / nDiv;
          const um = w * 1e6;
          let txt = Math.abs(um) < 1e-9 ? "0"
            : (Math.abs(um) >= 100 ? um.toFixed(0) : um.toFixed(2));
          if (k === nDiv && isLongest) txt += " µm";
          const el = document.createElement("span");
          el.className = "ruler-lbl";
          /* 轴字母前缀（x/y/z，半透明）：两条轴在角上各出一个 "1.00" 时，
           * 没有字母就是两个一模一样的数——标上轴名才读得出哪个是哪个。 */
          const ax = document.createElement("i");
          ax.textContent = "xyz"[a];
          el.appendChild(ax);
          el.appendChild(document.createTextNode(txt));
          this._rulerLayer.appendChild(el);
          const q = mid.slice();
          q[a] = w;
          q[p1] += s1 * tickLen;          /* 锚点 = 刻度外端 */
          const item = { el, anchor: new THREE.Vector3(q[0], q[1], q[2]) };
          labels.push(item);
          this._rulerLabels.push(item);
        }
        this.domainGroup.add(g);
        variants.push({ group: g, labels,
          mid: new THREE.Vector3(mid[0], mid[1], mid[2]) });
      }
      sets.push(variants);
    }
    this._rulerSets = sets;
    this._updateRulers();
  }

  /* 相机一动（含窗口尺寸变化）：选棱 → 投影锚点 → 贴到覆盖层 → 版面避让。
   * 纯 2D 运算，没有纹理重建，可安全地每次 change 调用。 */
  _updateRulers() {
    const sets = this._rulerSets;
    if (!sets || !this.camera || !this._rulerLayer) return;
    const W = this.container.clientWidth || 400;
    const H = this.container.clientHeight || 300;
    const toPx = (v) => {
      const p = v.clone().project(this.camera);
      return [(p.x * 0.5 + 0.5) * W, (-p.y * 0.5 + 0.5) * H];
    };
    const cS = toPx(this._rulerCenter || new THREE.Vector3());
    const shown = [];
    if (!this._rulerPick) this._rulerPick = [-1, -1, -1];
    sets.forEach((variants, ai) => {
      /* 选棱：投影后离框心最远的那条（贴着轮廓走）。"离相机最近"在等轴
       * 视图下常常穿过框内部——数字会浮在实体上。
       * 迟滞（2026-10-21）：候选分数接近时保持上一次的选择，否则拖动视角
       * 的两条棱得分来回反超，读数就在两条棱之间跳——就是用户看到的"闪"。 */
      const scores = variants.map((v) => {
        const p = toPx(v.mid);
        return Math.hypot(p[0] - cS[0], p[1] - cS[1]);
      });
      let bestIdx = 0;
      for (let i = 1; i < scores.length; i++) {
        if (scores[i] > scores[bestIdx]) bestIdx = i;
      }
      const prev = this._rulerPick[ai];
      if (prev >= 0 && prev < scores.length && prev !== bestIdx
          && scores[prev] > scores[bestIdx] * 0.9) bestIdx = prev;
      this._rulerPick[ai] = bestIdx;
      variants.forEach((v, i) => {
        const on = (i === bestIdx);
        v.group.visible = on;
        for (const lb of v.labels) lb.el.style.display = on ? "" : "none";
      });
      shown.push(...variants[bestIdx].labels);
    });
    /* 锚点 → 像素，再沿"远离框心"的方向外推 16px（不压框线）。
     * 锚点跑到画布外的读数隐藏；阈值给 24px 余量（贴边时反复进出视口
     * 会闪，给个滞回带）。 */
    const placed = [];
    for (const lb of shown) {
      const p = toPx(lb.anchor);
      if (p[0] < -24 || p[0] > W + 24 || p[1] < -24 || p[1] > H + 24) {
        lb.el.style.display = "none";
        continue;
      }
      lb.el.style.display = "";
      let dx = p[0] - cS[0], dy = p[1] - cS[1];
      const len = Math.hypot(dx, dy) || 1;
      placed.push({ el: lb.el, x: p[0] + (dx / len) * 16,
                    y: p[1] + (dy / len) * 16 });
    }
    /* 版面避让：两点过近就把后者往下推 13px（最多推 3 次，够用且不抖） */
    for (let pass = 0; pass < 3; pass++) {
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const A = placed[i], B = placed[j];
          if (Math.abs(A.x - B.x) < 46 && Math.abs(A.y - B.y) < 13) {
            B.y += (B.y >= A.y ? 13 : -13);
          }
        }
      }
    }
    /* 贴边裁剪：读数不允许被画布边界吃掉（"y −1.0" 半截最扎眼） */
    for (const it of placed) {
      const w = it.el.offsetWidth || 44;
      it.x = Math.min(Math.max(it.x, 4), Math.max(4, W - w - 4));
      it.y = Math.min(Math.max(it.y, 12), H - 6);
    }
    for (const it of placed) {
      it.el.style.transform =
        "translate(" + Math.round(it.x) + "px," + Math.round(it.y) + "px)";
    }
  }

  updateScene(cfg) {
    this.updateCount = (this.updateCount || 0) + 1;
    this._cfg = cfg;
    this.clearGroup(this.domainGroup);
    this.clearGroup(this.structureGroup);
    this.clearGroup(this.markerGroup);
    this.clearGroup(this.meshGroup);
    this.structureMeshes = [];

    const conf = fdtdConfig(cfg);
    const dom = conf.domain || {};
    const rng = (arr, i) => {
      if (!Array.isArray(arr)) return NaN;
      const v = Number(arr[i]);
      return isFinite(v) ? v : NaN;
    };
    const d = {
      xmin: rng(dom.x_range, 0), xmax: rng(dom.x_range, 1),
      ymin: rng(dom.y_range, 0), ymax: rng(dom.y_range, 1),
      zmin: rng(dom.z_range, 0), zmax: rng(dom.z_range, 1),
    };
    let hasDomain = true;
    for (const ax of ["x", "y", "z"]) {
      if (!(d[ax + "max"] > d[ax + "min"])) { hasDomain = false; break; }
    }
    this._showDomainEmptyHint(!hasDomain);
    if (!hasDomain) {
      const vb = this._geometryBounds(cfg.geometry) || {
        xmin: -5e-7, xmax: 5e-7, ymin: -5e-7, ymax: 5e-7,
        zmin: -5e-7, zmax: 5e-7,
      };
      Object.assign(d, vb);
      this._domRanges = null;
      if (this._rulerLayer) this._rulerLayer.textContent = "";
    }
    const cx = (d.xmin + d.xmax) / 2, cy = (d.ymin + d.ymax) / 2, cz = (d.zmin + d.zmax) / 2;
    const sx = d.xmax - d.xmin, sy = d.ymax - d.ymin, sz = d.zmax - d.zmin;
    this._domRanges = d;            /* 剖面平面按域范围换算位置 */
    const half = { x: sx / 2, y: sy / 2, z: sz / 2 };

    // ── PML 扩展域（前端本地计算：各 pml 面 layers × 该轴名义格距）──
    const pmlBox = this._pmlBox(cfg, d);
    if (this.showPml && pmlBox) {
      const pcx = (pmlBox.xmin + pmlBox.xmax) / 2, pSx = pmlBox.xmax - pmlBox.xmin;
      const pcy = (pmlBox.ymin + pmlBox.ymax) / 2, pSy = pmlBox.ymax - pmlBox.ymin;
      const pcz = (pmlBox.zmin + pmlBox.zmax) / 2, pSz = pmlBox.zmax - pmlBox.zmin;
      /* PML 外边界：细杆边（msaa 抗锯齿） + 去掉整盒薄壳填充。
       * 2026-10-21：六面各 0.04 琥珀叠在浅底画布上，会把整个域染成米黄。 */
      /* 边杆粗细按"域尺度"取（不能取 camera.top：updateScene 里相机取景在
       * 本函数之后才跑，首帧拿到的是初始化值——细成 0.25px 的亚像素杆，
       * 在浅底上被采样成一根断续点线）。0.0045×最长边 ≈ 1.2px @600px 窗高。 */
      const tg = Math.max(sx, sy, sz) * 0.0034;
      this.domainGroup.add(this._edgeBars(pcx, pcy, pcz, pSx, pSy, pSz, tg,
        SCENE_PALETTE.BC.pml, 0.4));
      this.domainGroup.add(this._edgeBars(cx, cy, cz, sx, sy, sz, tg,
        SCENE_PALETTE.BC.pml, 0.26));
    }

    // ── 六面 BC 着色面：2026-10-21 用户定案移除 ──

    if (hasDomain) {
      const tg2 = Math.max(sx, sy, sz) * 0.0032;
      this.domainGroup.add(this._edgeBars(cx, cy, cz, sx, sy, sz, tg2,
        SCENE_PALETTE.DOMAIN.EDGE, 0.5));
      /* 边框坐标尺（刻度 + µm 读数）：随域存在，用户没定义域就不画。 */
      this._buildAxisRulers(cx, cy, cz, sx, sy, sz);
    }


    // ── 结构（geometry，按 priority 排序绘制）──
    // 记录原索引 → mesh 映射（3D 组感：structure 选中高亮其子项）
    this._geoIdxMeshes = {};
    const geoms = (cfg.geometry || []).map((g, idx) => ({ g, idx }))
      .sort((a, b) => (a.g.priority || 0) - (b.g.priority || 0));
    /* 简化渲染（裁决 B/D）：超阈值 → InstancedMesh 合批（同类几何一次
     * 绘制）；线框/标签/选中高亮让位性能，徽标示警。非 box/sphere 的
     * 稀有类型走原逐对象路径（大批量场景里占比可忽略）。 */
    this._simplified = geoms.length > this._simThreshold;
    this._simCount = geoms.length;
    if (this._simplified) {
      this._buildStructureInstanced(geoms);
    } else {
      for (const { g, idx } of geoms) {
      /* 3D 渲染前求值：cfg 槽里存表达式串（"-d" 等）是声明式发射的
       * 设计（服务端求值为真源），3D 只是预览——这里先把数值字段
       * 求值成数值副本，求值失败的对象整只跳过（resolveNum 已 warn）。 */
      const rg = this._resolveGeometry(g);
      if (!rg) continue;
        const mesh = this.buildStructureMesh(rg);
        if (mesh) {
          mesh.userData.geoIndex = idx;     /* 拾取：mesh → cfg.geometry 下标 */
          mesh.userData.prio = Number(rg.priority) || 0;   /* 剖面盖板分层用 */
          this.structureGroup.add(mesh);
          this.structureMeshes.push(mesh);
          this._geoIdxMeshes[idx] = mesh;
        }
      }
    }

    this.fitCamera();
    this._applyWireMode();   /* 重建后按当前三态上色/显隐（新对象默认全可见） */

    // ── 网格线（本地按名义格距画，大幅抽稀）──
    if (hasDomain && this.showGrid) this._updateGridLines(cfg, d);
    if (hasDomain && this.showGrid) this._updateRefineLines(cfg, d);

    // ── 源与监视器标记 ──
    const domainSz = Math.max(sx, sy, sz);
    const markerR = domainSz * 0.02;
    this._srcGroups = {};
    this._monGroups = {};
    (conf.source || []).forEach((s, i) => this._buildSourceMarker(s, i, markerR, d));
    (conf.monitor || []).forEach((m, i) => this._buildMonitorMarker(m, i, markerR, d));

    // ── 简化模式收尾：徽标 + 包围盒（r128 Box3.setFromObject 不展开
    // InstancedMesh 实例，_simBox 由合批器手算）──
    if (this._simBadge) {
      this._simBadge.classList.toggle("hidden", !this._simplified);
      if (this._simplified) {
        this._simBadge.textContent = "";
        this._simBadge.appendChild(document.createTextNode("简化渲染 · " + this._simCount + " 对象"));
      }
    }

    /* 重建后：显示模式（X 光）与选中描边都要重新落到新对象上——
     * 否则改一次参数，选中高亮就消失了 */
    if (this.showXray) this.setXray(true);
    if (this.showRulers === false) this.setRulers(false);
    /* 剖面/裁剪挂在材质上，重建后必须重新挂一遍 */
    this._applyClipPlane();
    if (this.showClip) this._applyClipping();
    this._applyHighlight();
    this.markDirty();   /* 结构重建必重画（相机不动也要） */
  }

  /* PML 扩展盒：每面 layers × 该轴名义格距（dy/dz 未设回退 dx，同 schema 语义；
   * Q30 起基础格距住 domain） */
  _pmlBox(cfg, d) {
    const dom = fdtdConfig(cfg).domain || {};
    const dx = dom.dx || 20e-9;
    const hs = { x_min: dx, x_max: dx, y_min: dom.dy || dx, y_max: dom.dy || dx,
                 z_min: dom.dz || dx, z_max: dom.dz || dx };
    const bnd = dom.boundary || {};
    let any = false;
    const out = { xmin: d.xmin, xmax: d.xmax, ymin: d.ymin, ymax: d.ymax,
                  zmin: d.zmin, zmax: d.zmax };
    for (const f of ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]) {
      const b = bnd[f];
      if (b && b.kind === "pml") {
        const t = (b.layers || 12) * hs[f];
        if (f.endsWith("_min")) out[f[0] + "min"] -= t;
        else out[f[0] + "max"] += t;
        any = true;
      }
    }
    return any ? out : null;
  }

  resolveNum(v, fieldName) {
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    if (typeof v === "string" && v.trim())
      console.warn("[scene3d] 字段", fieldName,
        "表达式槽未解析（resolved 副本缺失）:", v);
    return null;
  }

  /* 结构几何 → 数值副本：逐字段求值，任一关键字段失败 → null（跳过对象）。
   * 缺省字段（undefined）原样保留，由 buildStructureMesh/_geoTop 各自兜底。 */
  _resolveGeometry(g) {
    const out = Object.assign({}, g);
    const tuple = (k, name) => {
      if (out[k] == null) return true;
      if (!Array.isArray(out[k])) return false;
      const r = out[k].map((x, i) => this.resolveNum(x, name + "[" + i + "]"));
      if (r.some((x) => x === null)) return false;
      out[k] = r;
      return true;
    };
    const scalar = (k, name) => {
      if (out[k] == null) return true;
      const r = this.resolveNum(out[k], name);
      if (r === null) return false;
      out[k] = r;
      return true;
    };
    switch (out.type) {
      case "box":
        return (tuple("x_range", "x_range") && tuple("y_range", "y_range")
          && tuple("z_range", "z_range")) ? out : null;
      case "sphere":
        return (tuple("center", "center") && scalar("radius", "radius")) ? out : null;
      case "cylinder":
        return (tuple("center", "center") && scalar("radius", "radius")
          && scalar("height", "height")) ? out : null;
      case "ngon_z":
        return (tuple("center", "center") && scalar("circumradius", "circumradius")
          && scalar("n_sides", "n_sides") && scalar("height", "height")) ? out : null;
      case "ellipse_z":
        return (tuple("center", "center") && scalar("rx", "rx") && scalar("ry", "ry")
          && scalar("height", "height")) ? out : null;
      case "ring_z":
        return (tuple("center", "center") && scalar("inner_radius", "inner_radius")
          && scalar("outer_radius", "outer_radius") && scalar("height", "height"))
          ? out : null;
      case "polygon_z":
      case "polygon_swept":
        return this._resolvePolygon(out);
      default:
        return out;
    }
  }

  /* polygon_z / polygon_swept：z_range/tilt 元组 + vertices 逐点坐标。
   * [null,null] 哨兵（多子多边形分隔）保留；单个顶点坐标求值失败 →
   * 当哨兵丢弃（不整只删多边形，仅降级该点，resolveNum 已 warn）。 */
  _resolvePolygon(g) {
    if (Array.isArray(g.vertices)) {
      const verts = [];
      for (const v of g.vertices) {
        if (v == null) { verts.push([null, null]); continue; }
        const x = v[0] == null ? null : this.resolveNum(v[0], "vertices");
        const y = v[1] == null ? null : this.resolveNum(v[1], "vertices");
        if (x === null || y === null) { verts.push([null, null]); continue; }
        verts.push([x, y]);
      }
      g.vertices = verts;
    }
    const tuple = (k, name) => {
      if (g[k] == null) return true;
      if (!Array.isArray(g[k])) return false;
      const r = g[k].map((x, i) => this.resolveNum(x, name + "[" + i + "]"));
      if (r.some((x) => x === null)) return false;
      g[k] = r;
      return true;
    };
    return (tuple("z_range", "z_range") && tuple("tilt", "tilt")) ? g : null;
  }

  /* geometry 节点（数值数组，新 schema 形状）→ three.js mesh */
  buildStructureMesh(g) {
    const color = new THREE.Color(
      this._materialColor(g.material)).convertSRGBToLinear();
    let geo = null, pos = { x: 0, y: 0, z: 0 };
    switch (g.type) {
      case "box": {
        const xr = g.x_range || [-1e-7, 1e-7], yr = g.y_range || [-1e-7, 1e-7],
              zr = g.z_range || [-1e-7, 1e-7];
        pos = { x: (xr[0] + xr[1]) / 2, y: (yr[0] + yr[1]) / 2, z: (zr[0] + zr[1]) / 2 };
        geo = new THREE.BoxGeometry(
          Math.abs(xr[1] - xr[0]), Math.abs(yr[1] - yr[0]), Math.abs(zr[1] - zr[0]));
        break;
      }
      case "sphere": {
        const c = g.center || [0, 0, 0];
        pos = { x: c[0], y: c[1], z: c[2] };
        geo = new THREE.SphereGeometry(Math.abs(g.radius || 1e-7), 32, 24);
        break;
      }
      case "cylinder": {
        // 沿 z 拉伸的圆盘（THREE.CylinderGeometry 是 Y 轴向，用 extrude 保 z 对齐）
        const c = g.center || [0, 0, 0];
        const r = Math.abs(g.radius || 1e-7), hgt = Math.abs(g.height || 2e-7);
        const shape = new THREE.Shape();
        shape.absarc(0, 0, r, 0, Math.PI * 2, false);
        geo = new THREE.ExtrudeGeometry(
          shape, { depth: hgt, bevelEnabled: false, curveSegments: 48 });
        geo.translate(0, 0, -hgt / 2);
        pos = { x: c[0], y: c[1], z: c[2] };
        break;
      }
      case "ngon_z": {
        const c = g.center || [0, 0, 0];
        const n = Math.max(3, Math.floor(g.n_sides || 6));
        const r = Math.abs(g.circumradius || 1e-7);
        const hgt = Math.abs(g.height || 2e-7);
        const shape = new THREE.Shape();
        for (let i = 0; i < n; i++) {
          const a = (i / n) * Math.PI * 2;
          const x = Math.cos(a) * r, y = Math.sin(a) * r;
          if (i === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
        }
        shape.closePath();
        geo = new THREE.ExtrudeGeometry(
          shape, { depth: hgt, bevelEnabled: false, curveSegments: 1 });
        geo.translate(0, 0, -hgt / 2);
        pos = { x: c[0], y: c[1], z: c[2] };
        break;
      }
      case "ellipse_z": {
        const c = g.center || [0, 0, 0];
        const rx = Math.abs(g.rx || 1e-7), ry = Math.abs(g.ry || 1e-7);
        const hgt = Math.abs(g.height || 2e-7);
        const shape = new THREE.Shape();
        shape.absellipse(0, 0, rx, ry, 0, Math.PI * 2, false, 0);
        geo = new THREE.ExtrudeGeometry(
          shape, { depth: hgt, bevelEnabled: false, curveSegments: 48 });
        geo.translate(0, 0, -hgt / 2);
        pos = { x: c[0], y: c[1], z: c[2] };
        break;
      }
      case "ring_z": {
        const c = g.center || [0, 0, 0];
        const ri = Math.abs(g.inner_radius || 0);
        const ro = Math.abs(g.outer_radius || 1e-7);
        const hgt = Math.abs(g.height || 2e-7);
        const shape = new THREE.Shape();
        shape.absarc(0, 0, Math.max(ro, ri + 1e-30), 0, Math.PI * 2, false);
        const hole = new THREE.Path();
        hole.absarc(0, 0, Math.min(ri, ro), 0, Math.PI * 2, true);
        shape.holes.push(hole);
        geo = new THREE.ExtrudeGeometry(
          shape, { depth: hgt, bevelEnabled: false, curveSegments: 48 });
        geo.translate(0, 0, -hgt / 2);
        pos = { x: c[0], y: c[1], z: c[2] };
        break;
      }
      case "polygon_z":
      case "polygon_swept": {
        const { shapes, cx, cy } = this._polygonShape(g.vertices);
        const zr = g.z_range || [-1e-7, 1e-7];
        const hgt = Math.abs(zr[1] - zr[0]);
        // 多实体分别挤出后合并为单一 geometry（一次绘制）
        const parts = shapes.map((sh) => {
          const gg = new THREE.ExtrudeGeometry(
            sh, { depth: hgt, bevelEnabled: false, curveSegments: 1 });
          gg.translate(0, 0, -hgt / 2);
          return gg;
        });
        geo = parts.length === 1 ? parts[0] : this._mergeGeoms(parts);
        const zc = (zr[0] + zr[1]) / 2;
        if (g.type === "polygon_swept") {
          /* tilt = 截面随 (z − z_center) 平移斜率 → z 轴剪切；
           * 先绕 z=0 剪切，再补常量平移使截面在 z_center 处居中。 */
          const tx = Number(g.tilt && g.tilt[0]) || 0;
          const ty = Number(g.tilt && g.tilt[1]) || 0;
          if (tx || ty) {
            const m = new THREE.Matrix4();
            m.set(1, 0, tx, 0, 0, 1, ty, 0, 0, 0, 1, 0, 0, 0, 0, 1);
            geo.applyMatrix4(m);
            geo.translate(-tx * hgt / 2, -ty * hgt / 2, -hgt / 2);
          }
        }
        /* polygon_z 不补第二次平移：map 里已按 -hgt/2 居中（z ∈ [-h/2, +h/2]），
         * 再平移一次会让整块下沉 hgt/2，看着孔没穿透平板，而 z_range 是对的。 */
        pos = { x: cx, y: cy, z: zc };
        break;
      }
      default:
        return null;
    }
    /* 表达式槽（Q13）：字段值可能是 "L*2" 这类还没解开的字符串，算出来是
     * NaN。三维里画不出一个还没求值的尺寸——**不画**（标签仍在，检查器显示
     * 原文），别把 NaN 顶点喂给 three 让整个场景的包围盒跟着坏掉。 */
    geo.computeBoundingBox();
    const bb = geo.boundingBox;
    if (!bb || ![bb.min.x, bb.min.y, bb.min.z,
                 bb.max.x, bb.max.y, bb.max.z].every(Number.isFinite)) {
      return null;
    }
    /* 实体材质（2026-10-21）：MeshPhysical（无 envMap 时又贵又平）→ Standard +
     * 影棚环境贴图。DoubleSide 保留（几何的 face winding 不保证朝外，单面裁
     * 剪会丢掉顶/侧面、只剩一片平板）；不透明时双面不产生排序问题。 */
    const mat = new THREE.MeshStandardMaterial({
      color, metalness: 0.03, roughness: 0.42,
      envMapIntensity: 0.42,
      transparent: false, opacity: 1, side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(pos.x, pos.y, pos.z);
    const edges = this._edgesOf(geo);
    const wireOp = this.wireMode === "wire"
      ? SCENE_PALETTE.STRUCTURE.EDGE_OPACITY_WIRE
      : SCENE_PALETTE.STRUCTURE.EDGE_OPACITY;
    const wire = new THREE.LineSegments(edges, new THREE.LineBasicMaterial(
      { color: new THREE.Color(SCENE_PALETTE.STRUCTURE.EDGE).convertSRGBToLinear(),
        transparent: wireOp < 1, opacity: wireOp }));
    wire.visible = this.showWireframe;
    mesh.add(wire);
    /* 曲面体的轮廓线（2026-11-23 站长）：EdgesGeometry 只给"角度突变"的棱，
     * 圆柱/椭球/圆环的侧面是曲面（竖线不生成）、球体完全没有棱——纯线框下
     * 只剩上下圆或什么都不显示。这里补一组显式轮廓线，只在 wire 模式画。 */
    const outlineGeo = this._outlineGeometry(g);
    if (outlineGeo) {
      const outline = new THREE.LineSegments(outlineGeo,
        new THREE.LineBasicMaterial({
          color: new THREE.Color(SCENE_PALETTE.STRUCTURE.EDGE).convertSRGBToLinear(),
          transparent: wireOp < 1, opacity: wireOp }));
      outline.userData.outline = true;
      outline.visible = this.wireMode === "wire";
      mesh.add(outline);
    }
    if (this.wireMode === "wire") mat.visible = false;   /* 纯线框：面不画 */
    return mesh;
  }

  /* 曲面体的显式轮廓线（局部坐标，挂在 mesh 下随其平移）：
   * 圆柱/椭球 4 条纵向母线；圆环外内圈各 4 条；球体三条正交大圆。 */
  _outlineGeometry(g) {
    const v = [];
    const seg = (a, b) => v.push(a[0], a[1], a[2], b[0], b[1], b[2]);
    if (g.type === "cylinder" || g.type === "ellipse_z") {
      const rx = g.type === "cylinder" ? Math.abs(g.radius || 1e-7)
                                       : Math.abs(g.rx || 1e-7);
      const ry = g.type === "cylinder" ? rx : Math.abs(g.ry || 1e-7);
      const h = Math.abs(g.height || 2e-7);
      for (let i = 0; i < 4; i++) {
        const a = (Math.PI / 2) * i;
        const x = Math.cos(a) * rx, y = Math.sin(a) * ry;
        seg([x, y, -h / 2], [x, y, h / 2]);
      }
    } else if (g.type === "ring_z") {
      const ri = Math.abs(g.inner_radius || 0);
      const ro = Math.abs(g.outer_radius || 1e-7);
      const h = Math.abs(g.height || 2e-7);
      for (let i = 0; i < 4; i++) {
        const a = (Math.PI / 2) * i;
        const c = Math.cos(a), s = Math.sin(a);
        seg([c * ro, s * ro, -h / 2], [c * ro, s * ro, h / 2]);
        if (ri > 0) seg([c * ri, s * ri, -h / 2], [c * ri, s * ri, h / 2]);
      }
    } else if (g.type === "sphere") {
      const r = Math.abs(g.radius || 1e-7);
      const N = 48;
      const circles = [
        (t) => [Math.cos(t) * r, Math.sin(t) * r, 0],
        (t) => [Math.cos(t) * r, 0, Math.sin(t) * r],
        (t) => [0, Math.cos(t) * r, Math.sin(t) * r],
      ];
      for (const f of circles) {
        for (let i = 0; i < N; i++) {
          const t1 = (i / N) * Math.PI * 2, t2 = ((i + 1) / N) * Math.PI * 2;
          seg(f(t1), f(t2));
        }
      }
    } else {
      return null;                      /* 棱柱/棱锥/方块：EdgesGeometry 已够 */
    }
    if (!v.length) return null;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
    return geo;
  }

  _pointInPoly(pt, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
      if ((yi > pt[1]) !== (yj > pt[1])
          && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  _mergeGeoms(geos) {
    if (geos.length === 1) return geos[0];
    const flat = geos.map((g) => (g.index ? g.toNonIndexed() : g));
    const pos = [], norm = [], uv = [];
    for (const g of flat) {
      for (const v of g.attributes.position.array) pos.push(v);
      if (g.attributes.normal)
        for (const v of g.attributes.normal.array) norm.push(v);
      if (g.attributes.uv)
        for (const v of g.attributes.uv.array) uv.push(v);
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    if (norm.length) out.setAttribute("normal", new THREE.Float32BufferAttribute(norm, 3));
    if (uv.length) out.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    return out;
  }

  _polygonShape(vertices) {
    const groups = [[]];
    for (const v of (vertices || [])) {
      if (v == null || v[0] == null || v[1] == null
          || !isFinite(Number(v[0])) || !isFinite(Number(v[1]))) {
        groups.push([]);
        continue;
      }
      groups[groups.length - 1].push([Number(v[0]), Number(v[1])]);
    }
    const valid = groups.filter((g) => g.length >= 3);
    if (!valid.length) valid.push([[0, 0], [1e-7, 0], [0, 1e-7]]);
    // 包含深度：p 在哪些轮廓内部 → 最近包含者（面积最小）+ 深度
    const area = (g) => Math.abs(g.reduce((a, p, i) => {
      const q = g[(i + 1) % g.length]; return a + (p[0] * q[1] - q[0] * p[1]);
    }, 0)) / 2;
    const depth = new Array(valid.length).fill(0);
    const parent = new Array(valid.length).fill(-1);
    for (let i = 0; i < valid.length; i++) {
      let best = -1, bestArea = Infinity;
      for (let j = 0; j < valid.length; j++) {
        if (j === i) continue;
        if (this._pointInPoly(valid[i][0], valid[j])) {
          const a = area(valid[j]);
          if (a < bestArea) { bestArea = a; best = j; }
        }
      }
      if (best >= 0) { depth[i] = depth[best] + 1; parent[i] = best; }
    }
    let cx = 0, cy = 0;
    const first = valid[0];
    for (const p of first) { cx += p[0]; cy += p[1]; }
    cx /= first.length; cy /= first.length;
    // 实体（偶深度）+ 洞（奇深度挂到其父实体形状）
    const openShapes = new Map();   // valid 索引 → Shape（洞挂靠用）
    const shapes = [];
    for (let i = 0; i < valid.length; i++) {
      const mkPath = () => {
        const pa = new THREE.Path();
        pa.moveTo(valid[i][0][0] - cx, valid[i][0][1] - cy);
        for (let k = 1; k < valid[i].length; k++)
          pa.lineTo(valid[i][k][0] - cx, valid[i][k][1] - cy);
        pa.closePath();
        return pa;
      };
      if (depth[i] % 2 === 0) {
        const sh = new THREE.Shape();
        sh.moveTo(valid[i][0][0] - cx, valid[i][0][1] - cy);
        for (let k = 1; k < valid[i].length; k++)
          sh.lineTo(valid[i][k][0] - cx, valid[i][k][1] - cy);
        sh.closePath();
        openShapes.set(i, sh); shapes.push(sh);
      } else if (parent[i] >= 0) {
        const ps = openShapes.get(parent[i]);
        if (ps) ps.holes.push(mkPath());
      }
    }
    return { shapes, cx, cy };
  }

  /* 几何顶部点 + 特征尺寸（label 锚点/指示线终点；与 buildStructureMesh
   * 的几何语义一致：z 是物理 z，顶 = 最大 z） */
  /* 几何包围盒（2026-11-23）：无 domain 时给渲染一个**尺度参考**——
   * 只用来定标签/标记大小与虚拟范围，不画域体（用户没定义域）。 */
  _geometryBounds(geoms) {
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const g of geoms || []) {
      const r = this._resolveGeometry(g);
      if (!r) continue;
      let top;
      try { top = this._geoTop(r); } catch { continue; }
      const p = top.p, s = Math.abs(Number(top.size));
      if (!isFinite(s) || s <= 0 || !p) continue;
      for (let i = 0; i < 3; i++) {
        lo[i] = Math.min(lo[i], p[i] - s / 2);
        hi[i] = Math.max(hi[i], p[i] + s / 2);
      }
    }
    if (!isFinite(lo[0]) || !(hi[0] > lo[0])) return null;
    return { xmin: lo[0], xmax: hi[0], ymin: lo[1], ymax: hi[1],
             zmin: lo[2], zmax: hi[2] };
  }


  _geoTop(g) {
    if (g.type === "box") {
      const xr = g.x_range || [-1e-7, 1e-7], yr = g.y_range || [-1e-7, 1e-7],
            zr = g.z_range || [-1e-7, 1e-7];
      return { p: [(xr[0] + xr[1]) / 2, (yr[0] + yr[1]) / 2, zr[1]],
               size: Math.max(xr[1] - xr[0], yr[1] - yr[0], zr[1] - zr[0]) };
    }
    if (g.type === "sphere") {
      const c = g.center || [0, 0, 0], r = Math.abs(g.radius || 1e-7);
      return { p: [c[0], c[1], c[2] + r], size: 2 * r };
    }
    if (g.type === "polygon_z" || g.type === "polygon_swept") {
      /* 跳过 [null,null] 哨兵（多子多边形分隔）——否则质心/特征尺寸算成
       * NaN，label 锚点漂到无穷远（与 _polygonShape 的哨兵过滤同源）。 */
      const pts = (g.vertices || [[0, 0]]).filter((v) =>
        v != null && v[0] != null && v[1] != null
        && isFinite(Number(v[0])) && isFinite(Number(v[1])));
      const vs = pts.length ? pts : [[0, 0]];
      const cx = vs.reduce((a, v) => a + v[0], 0) / vs.length;
      const cy = vs.reduce((a, v) => a + v[1], 0) / vs.length;
      const zr = g.z_range || [-1e-7, 1e-7];
      const size = 2 * (Math.max(...vs.map((v) => Math.hypot(v[0] - cx, v[1] - cy))) || 1e-7);
      return { p: [cx, cy, zr[1]], size };
    }
    // cylinder / ngon_z / ellipse_z / ring_z：center + 特征半径 + height
    const c = g.center || [0, 0, 0];
    const r = Math.abs(g.radius || g.circumradius || g.outer_radius || g.rx || 1e-7);
    const h = g.height != null ? Math.abs(g.height)
      : (g.z_range ? Math.abs(g.z_range[1] - g.z_range[0]) : 2e-7);
    return { p: [c[0], c[1], c[2] + h / 2], size: Math.max(2 * r, h) };
  }



  /* ── 标签屏幕等大（2026-10-21 G42 缓解）──
   * 贴片精灵的尺寸原本锚在"域半径"，放大视角时纹理被拉伸 → 糊成灰块。
   * 这里按"可见世界高度 / 画布像素高"把每张卡固定成 ~96px 宽：缩放相机时
   * 卡不变形、纹理始终 1:1 附近采样，读得清；同时标签之间的大小关系稳定。 */


  /* 标签显示开关（🏷 按钮）——只切可见性，不重建 */

  /* 网格线：三个远侧面按名义格距画（每轴 ≤200 条，超出抽稀；
   * Q30 起基础格距住 domain） */
  _updateGridLines(cfg, d) {
    const dom = fdtdConfig(cfg).domain || {};
    const dx = dom.dx || 20e-9;
    const steps = { x: dom.dx || dx, y: dom.dy || dx, z: dom.dz || dx };
    const sp = {};
    for (const ax of ["x", "y", "z"]) {
      const lo = d[ax + "min"], hi = d[ax + "max"];
      const n = Math.floor((hi - lo) / steps[ax]);
      if (n < 1) { sp[ax] = [lo, hi]; continue; }
      let stride = Math.ceil(n / 200);
      const pts = [];
      for (let i = 0; i <= n; i += stride) pts.push(lo + i * steps[ax]);
      if (pts[pts.length - 1] < hi) pts.push(hi);
      sp[ax] = pts;
    }
    const mat = new THREE.LineBasicMaterial(
      { color: SCENE_PALETTE.GRID.MESH, transparent: true, opacity: 0.65, depthTest: true }  /* COMSOL 浅底网格 */);
    this._addGridPlane(sp.x, sp.y, d.zmin, null, mat);
    this._addGridPlane(sp.x, sp.z, d.ymin, { axis: 1 }, mat);
    this._addGridPlane(sp.y, sp.z, d.xmin, { axis: 0 }, mat);
  }

  /* mesh 加密区叠加（E-5/D-3 移植的本地近似；Q30 对象化重排）：基础
   * 格距住 domain；每个 mesh 对象按「绝对间距或 factor 相对基础」得
   * 名义格距，在加密盒三面画加密线（示意——growth_rate 渐变与服务器
   * 真实非均匀分布不可知，真值以 mesh_preview / 提交后为准）。下一轮
   * clearGroup 自然清除。 */
  _updateRefineLines(cfg, d) {
    const conf = fdtdConfig(cfg);
    const dom = conf.domain || {};
    const base = { x: dom.dx || 20e-9, y: dom.dy || dom.dx || 20e-9,
                   z: dom.dz || dom.dx || 20e-9 };
    const mat = new THREE.LineBasicMaterial(
      { color: SCENE_PALETTE.GRID.REFINE, transparent: true,
        opacity: 0.75, depthTest: true });
    for (const m of (conf.mesh || [])) {
      if (!m || typeof m !== "object") continue;
      const xr = m.x_range, yr = m.y_range, zr = m.z_range;
      if (!Array.isArray(xr) || !Array.isArray(yr) || !Array.isArray(zr)) continue;
      const step = {
        x: m.dx || base.x / Math.max(1, m.factor_x || 1),
        y: m.dy || base.y / Math.max(1, m.factor_y || 1),
        z: m.dz || base.z / Math.max(1, m.factor_z || 1),
      };
      const lines = (lo, hi, st) => {
        if (!(st > 0)) return [lo, hi];
        const n = Math.floor((hi - lo) / st);
        if (n < 1) return [lo, hi];
        const stride = Math.ceil(n / 200);
        const out = [];
        for (let i = 0; i <= n; i += stride) out.push(lo + i * st);
        if (out[out.length - 1] < hi) out.push(hi);
        return out;
      };
      const px = lines(xr[0], xr[1], step.x);
      const py = lines(yr[0], yr[1], step.y);
      const pz = lines(zr[0], zr[1], step.z);
      this._addGridPlane(px, py, zr[0], null, mat);
      this._addGridPlane(px, pz, yr[0], { axis: 1 }, mat);
      this._addGridPlane(py, pz, xr[0], { axis: 0 }, mat);
    }
  }

  _addGridPlane(positionsA, positionsB, fixedVal, axisInfo, mat) {
    const pts = [];
    const aMin = positionsA[0], aMax = positionsA[positionsA.length - 1];
    const bMin = positionsB[0], bMax = positionsB[positionsB.length - 1];
    for (const t of positionsB) {
      if (!axisInfo) pts.push(aMin, t, fixedVal, aMax, t, fixedVal);
      else if (axisInfo.axis === 1) pts.push(aMin, fixedVal, t, aMax, fixedVal, t);
      else pts.push(fixedVal, aMin, t, fixedVal, aMax, t);
    }
    for (const s of positionsA) {
      if (!axisInfo) pts.push(s, bMin, fixedVal, s, bMax, fixedVal);
      else if (axisInfo.axis === 1) pts.push(s, fixedVal, bMin, s, fixedVal, bMax);
      else pts.push(fixedVal, s, bMin, fixedVal, s, bMax);
    }
    if (!pts.length) return;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.meshGroup.add(new THREE.LineSegments(geo, mat));
  }


  clearGroup(group) {
    while (group.children.length > 0) {
      const child = group.children[0];
      group.remove(child);
      // r128 的 material.dispose() 不释放 map（CanvasTexture）——
      // label sprite 每次重建都累积显存，须显式 dispose（审查轮建议 3）
      const disposeObj = (o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          if (o.material.map && o.material.map.dispose) o.material.map.dispose();
          o.material.dispose();
        }
      };
      disposeObj(child);
      if (child.children) {
        for (let i = child.children.length - 1; i >= 0; i--) {
          const gc = child.children[i];
          child.remove(gc);
          disposeObj(gc);
        }
      }
    }
  }

  /* 取景 = 结构 ∪ 域盒 ∪ 标记（不含漂浮标签），使整个仿真体积——域盒、
   * PML 吸收壳、源注入面、monitor——一起入画（Round C 语义：域读作体积）。
   * 域为 0 时退回结构；全空时回退默认透视。 */
  /* 简化渲染合批器（裁决 B）：unit 几何 × 实例矩阵（位置/缩放）× 实例色。
   * box/sphere 占绝对多数走合批；其余类型逐对象走原管线。 */
  _buildStructureInstanced(geoms) {
    const items = [], others = [];
    for (const { g, idx } of geoms) {
      const rg = this._resolveGeometry(g);
      if (!rg) continue;
      if (rg.type === "box") {
        const xr = rg.x_range || [-1e-7, 1e-7], yr = rg.y_range || [-1e-7, 1e-7],
              zr = rg.z_range || [-1e-7, 1e-7];
        items.push({ kind: "box",
          pos: [(xr[0] + xr[1]) / 2, (yr[0] + yr[1]) / 2, (zr[0] + zr[1]) / 2],
          scale: [Math.max(Math.abs(xr[1] - xr[0]), 1e-12),
                  Math.max(Math.abs(yr[1] - yr[0]), 1e-12),
                  Math.max(Math.abs(zr[1] - zr[0]), 1e-12)],
          color: new THREE.Color(this._materialColor(rg.material)).convertSRGBToLinear() });
      } else if (rg.type === "sphere") {
        const c = rg.center || [0, 0, 0];
        const r = Math.max(Math.abs(rg.radius || 1e-7), 1e-12);
        items.push({ kind: "sphere", pos: [c[0], c[1], c[2]], scale: [r, r, r],
          color: new THREE.Color(this._materialColor(rg.material)).convertSRGBToLinear() });
      } else {
        others.push({ rg, idx });   /* 稀有类型：量少，逐对象保真 */
      }
    }
    const mkInstanced = (geo, list, opacity) => {
      if (!list.length) return;
      /* 与逐对象路径同一套材质语言（Standard + 环境光），只是合批绘制：
       * 无光照的 Basic 会让大批量对象读成一堆色块贴纸。 */
      const mat = new THREE.MeshStandardMaterial({
        transparent: opacity < 1, opacity,
        metalness: 0.0, roughness: 0.55, envMapIntensity: 0.12,
        side: THREE.DoubleSide,
      });
      const im = new THREE.InstancedMesh(geo, mat, list.length);
      /* InstancedMesh 的包围球按基础几何算，容易整批被误剔 */
      im.frustumCulled = false;
      const m4 = new THREE.Matrix4();
      list.forEach((it, i) => {
        m4.makeScale(it.scale[0], it.scale[1], it.scale[2]);
        m4.setPosition(it.pos[0], it.pos[1], it.pos[2]);
        im.setMatrixAt(i, m4);
        im.setColorAt(i, it.color);
      });
      im.instanceMatrix.needsUpdate = true;
      if (im.instanceColor) im.instanceColor.needsUpdate = true;
      im.userData.sim = true;        /* 合批实例：三态由 _applyWireMode 统一管 */
      this.structureGroup.add(im);
      this.structureMeshes.push(im);
    };
    /* 合批线框层：一次绘制；可见性交给 _applyWireMode（solid 隐藏） */
    const mkSimLines = (pts, opacity) => {
      if (!pts.length) return;
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      const line = new THREE.LineSegments(eg, new THREE.LineBasicMaterial(
        { color: new THREE.Color(SCENE_PALETTE.STRUCTURE.EDGE).convertSRGBToLinear(),
          transparent: opacity < 1, opacity }));
      line.userData.simLine = true;
      this.structureGroup.add(line);
    };
    const boxes = items.filter((i) => i.kind === "box");
    mkInstanced(new THREE.BoxGeometry(1, 1, 1), boxes, 1.0);
    /* 合并线框层：所有 box 的 12 条棱合成一条 LineSegments（一次绘制）——
     * 简化模式的"脆感"来源就是只画半透明填充块没有棱线 */
    {
      const pts = [];
      const H = [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                 [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]];
      const E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
      for (const it of boxes) {
        /* 1.01× 外扩：棱线与面共面时深度相等，会被面遮挡成"零线" */
        const [cx, cy, cz] = it.pos, [hx, hy, hz] = [it.scale[0]*0.53, it.scale[1]*0.53, it.scale[2]*0.53];
        const v = H.map(([a, b, c]) => [cx + a*hx, cy + b*hy, cz + c*hz]);
        for (const [a, b] of E) pts.push(...v[a], ...v[b]);
      }
      mkSimLines(pts, SCENE_PALETTE.STRUCTURE.EDGE_OPACITY);
    }
    const spheres = items.filter((i) => i.kind === "sphere");
    mkInstanced(new THREE.SphereGeometry(1, 16, 12), spheres, 1.0);
    /* 球合批的轮廓线：每球三条正交大圆（16 段）——否则简化模式纯线框下
     * 球体什么都不显示（与逐对象路径同病，2026-11-23）。 */
    {
      const pts = [];
      const N = 16;
      for (const it of spheres) {
        const [cx, cy, cz] = it.pos, r = it.scale[0] * 1.06;
        const rings = [
          (t) => [cx + Math.cos(t) * r, cy + Math.sin(t) * r, cz],
          (t) => [cx + Math.cos(t) * r, cy, cz + Math.sin(t) * r],
          (t) => [cx, cy + Math.cos(t) * r, cz + Math.sin(t) * r],
        ];
        for (const f of rings) {
          for (let i = 0; i < N; i++) {
            const a = f((i / N) * Math.PI * 2);
            const b = f(((i + 1) / N) * Math.PI * 2);
            pts.push(...a, ...b);
          }
        }
      }
      mkSimLines(pts, SCENE_PALETTE.STRUCTURE.EDGE_OPACITY);
    }
    for (const { rg, idx } of others) {
      const mesh = this.buildStructureMesh(rg);
      if (mesh) { this.structureGroup.add(mesh); this.structureMeshes.push(mesh); this._geoIdxMeshes[idx] = mesh; }
    }
    /* 手算包围盒（r128 Box3.setFromObject 不展开 InstancedMesh 实例） */
    const box = new THREE.Box3();
    for (const it of items) {
      box.expandByPoint(new THREE.Vector3(
        it.pos[0] - it.scale[0] / 2, it.pos[1] - it.scale[1] / 2, it.pos[2] - it.scale[2] / 2));
      box.expandByPoint(new THREE.Vector3(
        it.pos[0] + it.scale[0] / 2, it.pos[1] + it.scale[1] / 2, it.pos[2] + it.scale[2] / 2));
    }
    if (!box.isEmpty()) this._simBox = box;
  }

  fitCamera() {
    let box = new THREE.Box3();
    box.makeEmpty();
    /* 结构组逐个 child 取盒（2026-11-23）：Box3.setFromObject 对
     * InstancedMesh 用基础几何（BoxGeometry(1,1,1) = 世界 1 米）——
     * 合批后相机被拉到 1 米外，1e-7 的对象全成空。合批实例一律跳过，
     * 由 _simBox（合批器手算的真实包围盒）补上。 */
    for (const ch of this.structureGroup.children) {
      if (ch.userData && ch.userData.sim) continue;
      box.union(new THREE.Box3().setFromObject(ch));
    }
    box.union(new THREE.Box3().setFromObject(this.domainGroup));
    box.union(new THREE.Box3().setFromObject(this.markerGroup));
    if (this._simBox) box.union(this._simBox);   /* 合批实例的手算包围盒 */
    if (box.isEmpty()) box = new THREE.Box3().setFromObject(this.structureGroup);
    if (box.isEmpty()) box = new THREE.Box3().setFromObject(this.domainGroup);
    const aspect = (this.container.clientWidth || 400) / (this.container.clientHeight || 300);
    if (box.isEmpty()) {
      const size = 8e-7;
      this.camera.zoom = 1;                    /* 见下：复位必须清掉滚轮缩放 */
      this.camera.left = -size * aspect; this.camera.right = size * aspect;
      this.camera.top = size; this.camera.bottom = -size;
      this.camera.near = 1e-9; this.camera.far = 1e-4;
      this.camera.position.set(8e-7, 6e-7, 1.2e-6);
      this.camera.lookAt(0, 0, 0);
      this.camera.updateProjectionMatrix();
      return;
    }
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);
    /* 取景（2026-10-21 重写）：按**投影后**的包围盒取景，而不是"最大边 ×2"。
     * 旧算法对一个 3.8×0.9×1.0 µm 的长条域会把可见高度定在 2×3.8µm，实体只
     * 占窗内一小块（"没对准"）；现在把 8 个角投到相机的 right/up 基上取极值，
     * 再按 aspect 归一，任何长宽比都刚好撑满窗口的 ~88%。 */
    const viewDir = new THREE.Vector3(1.15, 1.35, 1.05).normalize();  // 相机方位
    const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0),
      viewDir).normalize();
    const up = new THREE.Vector3().crossVectors(viewDir, right).normalize();
    let halfW = 0, halfH = 0;
    for (const ix of [-1, 1]) for (const iy of [-1, 1]) for (const iz of [-1, 1]) {
      const c = new THREE.Vector3(
        center.x + ix * size.x / 2,
        center.y + iy * size.y / 2,
        center.z + iz * size.z / 2).sub(center);
      halfW = Math.max(halfW, Math.abs(c.dot(right)));
      halfH = Math.max(halfH, Math.abs(c.dot(up)));
    }
    const MARGIN = 1.2;                        /* 旋转余量 + 端牌不贴边被裁 */
    const halfHFit = Math.max(halfH, halfW / Math.max(aspect, 1e-6)) * MARGIN;
    const maxDim = Math.max(size.x, size.y, size.z, 1e-9);
    if (this.camera.isPerspectiveCamera) {
      /* 透视：可见高度 = 2·d·tan(fov/2) → 反解相机距离 */
      const fov = THREE.MathUtils.degToRad(this.camera.fov);
      const dist = (halfHFit / Math.tan(fov / 2)) * 1.02;
      this.camera.aspect = aspect;
      this.camera.near = dist * 0.05;
      this.camera.far = dist * 3 + maxDim * 2;
      this.camera.position.set(
        center.x + viewDir.x * dist,
        center.y + viewDir.y * dist,
        center.z + viewDir.z * dist);
      /* 雾化景深：按**相机距离**定（2026-11-23）——按场景尺寸定会把
       * 相机放在 far 之外，整场全白。 */
      this.scene.fog = new THREE.Fog(SCENE_PALETTE.BG.FOG,
                                     dist - maxDim * 1.1,
                                     dist + maxDim * 2.4);
    } else {
      const viewDist = maxDim * 3.2;
      /* 裁剪面按场景尺度收紧（2026-11-23）：near 曾经是 1e-9 → near/far
       * 比 1e4 量级，深度缓冲精度被耗光，合批棱线外扩 3% 也照样被面吃掉。 */
      this.camera.near = Math.max(viewDist - maxDim * 2, maxDim * 1e-3);
      this.camera.far = viewDist + maxDim * 2;
      this.camera.left = -halfHFit * aspect; this.camera.right = halfHFit * aspect;
      this.camera.top = halfHFit; this.camera.bottom = -halfHFit;
      this.camera.position.set(
        center.x + viewDir.x * viewDist,
        center.y + viewDir.y * viewDist,
        center.z + viewDir.z * viewDist);
      this.scene.fog = new THREE.Fog(SCENE_PALETTE.BG.FOG,
                                     viewDist - maxDim * 1.1,
                                     viewDist + maxDim * 2.4);
    }
    this.camera.lookAt(center);
    this.camera.zoom = 1;
    this.camera.updateProjectionMatrix();
    if (this.controls) {
      this.controls.target.copy(center);
      this.controls.update();
    }
    /* 取景变了 → 坐标尺的屏幕等大换算与"最近棱"选择都要重算 */
    this._updateRulers();
    this.markDirty();
  }

  /* ── 透视 / 正交切换（2026-10-21）──
   * 换的是相机实例，OrbitControls 直接换 object；位置/朝向保持，随后按新
   * 类型重算视锥（正交用 halfH，透视用 fov + 距离）。 */
  setProjection(kind) {
    const next = (kind === "persp") ? "persp" : "ortho";
    if (next === this.projection && this.camera) return;
    this.projection = next;
    const aspect = (this.container.clientWidth || 400)
      / (this.container.clientHeight || 300);
    const target = this.controls ? this.controls.target.clone() : new THREE.Vector3();
    const pos = this.camera.position.clone();
    let cam;
    if (next === "persp") {
      cam = new THREE.PerspectiveCamera(36, aspect, 1e-9, 1e-2);
    } else {
      cam = new THREE.OrthographicCamera(-1e-6 * aspect, 1e-6 * aspect,
        1e-6, -1e-6, 1e-9, 1e-2);
    }
    cam.position.copy(pos);
    cam.up.copy(this.camera.up);
    cam.lookAt(target);
    cam.updateProjectionMatrix();
    this.camera = cam;
    if (this.controls) {
      this.controls.object = cam;
      this.controls.target.copy(target);
    }
    this.fitCamera();
    this.markDirty();
  }

  /* ── 剖面切片（2026-10-21）──
   * three 局部裁剪面：开关时给场景所有材质挂/摘 clippingPlanes；轴与位置
   * 变了只改平面法向与常量（不重建几何）。 */
  setSection(on) {
    this.showClip = !!on;
    if (!this.showClip) this._clipPlane = null;
    this._applyClipPlane();
    this._applyClipping();
    this.markDirty();
  }
  setClipAxis(axis) {
    if (!"xyz".includes(axis)) return;
    this.clipAxis = axis;
    this._applyClipPlane();
    this._applyClipping();
    this.markDirty();
  }
  setClipPos(t) {
    this.clipT = Math.max(0, Math.min(1, Number(t)));
    this._applyClipPlane();
    this.markDirty();
  }
  _applyClipPlane() {
    if (!this.showClip) { this._clipPlane = null; return; }
    const d = this._domRanges;
    if (!d) return;
    const a = "xyz".indexOf(this.clipAxis);
    const lo = [d.xmin, d.ymin, d.zmin][a], hi = [d.xmax, d.ymax, d.zmax][a];
    const at = lo + (hi - lo) * this.clipT;
    /* 就地改同一个 Plane 对象：材质里的 clippingPlanes 持有的是对象引用，
     * 每帧 new 一个会让拖动滑杆"不生效"（只有重建材质时才吃到新值）——
     * 这就是"必须点一下 X/Y/Z 才更新"的根因。 */
    if (!this._clipPlane) this._clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
    this._clipPlane.normal.set(0, 0, 0);
    this._clipPlane.normal.setComponent(a, -1);   /* 保留 坐标 < at 的一侧 */
    this._clipPlane.constant = at;
    /* 截面盖板跟着平面走（盖板物体本身不动，只沿法轴平移 + 层间抬升） */
    if (this._capGroup) {
      for (const cap of this._capGroup.children) {
        if (!cap.userData || !cap.userData.isCap) continue;
        cap.position.setComponent(a, at + (cap.userData.lift || 0));
      }
    }
  }
  _applyClipping() {
    const planes = this.showClip && this._clipPlane ? [this._clipPlane] : null;
    this.scene.traverse((o) => {
      const mats = o.material
        ? (Array.isArray(o.material) ? o.material : [o.material]) : null;
      if (!mats) return;
      for (const m of mats) {
        if (o.userData && o.userData.capHelper) continue;   /* 盖板/模板件不裁剪 */
        m.clippingPlanes = planes;
        m.clipIntersection = false;
        m.needsUpdate = true;
      }
    });
    this._rebuildSectionCaps();
  }

  /* ── 剖面盖板（2026-10-21 用户："切面看不清，要能看到被截开的结构"）──
   * 裁剪只挖掉半个实体，切口是空壳——看着像"掏空"，读不出这是实心材料。
   * 用模板缓冲把"实体被切到的像素"填成实体颜色：每个颜色组画一对模板件
   * （背面 +1 / 正面 −1），再用一个贴在切面上的大平面按 stencil≠0 上色，
   * 于是切口读作一片实心截面。盖板不裁剪、跟随平面平移。 */
  _rebuildSectionCaps() {
    if (this._capGroup) {
      this.scene.remove(this._capGroup);
      /* 只释放自己建的平面几何：模板件用的是结构体**共享**的 geometry，
       * 一起 dispose 会把还在渲染的实体几何也清掉（GPU 缓冲被回收，下一帧
       * 重建时算出 NaN 包围球 → 控制台刷 computeBoundingSphere 警告）。 */
      if (this._capPlaneGeo) { this._capPlaneGeo.dispose(); this._capPlaneGeo = null; }
      this._capGroup = null;
    }
    if (!this.showClip || !this._clipPlane) return;
    const d = this._domRanges;
    if (!d) return;
    const a = "xyz".indexOf(this.clipAxis);
    const span = Math.max(d.xmax - d.xmin, d.ymax - d.ymin, d.zmax - d.zmin);
    const size = span * 1.6;
    const planeGeo = new THREE.PlaneGeometry(size, size);
    this._capPlaneGeo = planeGeo;
    const grp = new THREE.Group();
    /* 盖板单元（2026-10-21 G37）：**默认一个对象一块盖板**——同色的两块料
     * 在切面上才分得开。对象太多（>CAP_PER_OBJECT_MAX）时回退到按颜色分组，
     * 否则模板件数量（3 件/对象）会拖垮帧率。
     * 排序按 priority 升序：低优先级垫后、高优先级贴前；嵌套体（球在盒子里）
     * 共面时谁 priority 高谁盖在上面——与求解器语义一致。 */
    const CAP_PER_OBJECT_MAX = 40;
    const units = [];
    const meshes = this.structureMeshes.filter(
      (m) => m && m.material && m.material.color);
    if (meshes.length <= CAP_PER_OBJECT_MAX) {
      for (const m of meshes) {
        units.push({
          hex: m.material.color.getHexString(),
          prio: (m.userData && m.userData.prio) || 0,
          meshes: [m],
        });
      }
    } else {
      const byColor = new Map();
      for (const m of meshes) {
        const hex = m.material.color.getHexString();
        if (!byColor.has(hex)) byColor.set(hex, { meshes: [], prio: 0 });
        const u = byColor.get(hex);
        u.meshes.push(m);
        u.prio = Math.max(u.prio, (m.userData && m.userData.prio) || 0);
      }
      for (const [hex, u] of byColor) {
        units.push({ hex, prio: u.prio, meshes: u.meshes });
      }
    }
    units.sort((x, y) => x.prio - y.prio);
    const liftStep = span * 0.0022;      /* 每层盖板沿法向抬一点，避免共面打架 */
    let level = 0;
    for (const unit of units) {
      const hex = unit.hex;
      const meshes = unit.meshes;
      const lift = level * liftStep;
      level += 1;
      const mkStencil = (side, op) => new THREE.MeshBasicMaterial({
        colorWrite: false, depthWrite: false, depthTest: false,
        side, stencilWrite: true,
        stencilFunc: THREE.AlwaysStencilFunc,
        stencilFail: op, stencilZFail: op, stencilZPass: op,
        /* 模板件本身也要被同一个平面裁剪：否则闭合实体的正/背面都过一遍
         * 模板（+1 再 −1 抵消），盖板永远得不到非零模板 → 一片都不画。 */
        clippingPlanes: [this._clipPlane], clipIntersection: false,
      });
      const backMat = mkStencil(THREE.BackSide, THREE.IncrementWrapStencilOp);
      const frontMat = mkStencil(THREE.FrontSide, THREE.DecrementWrapStencilOp);
      for (const m of meshes) {
        m.updateWorldMatrix(true, false);
        for (const mat of [backMat, frontMat]) {
          const sm = new THREE.Mesh(m.geometry, mat);
          sm.userData.capHelper = true;
          sm.matrixAutoUpdate = false;
          sm.matrix.copy(m.matrixWorld);
          sm.renderOrder = 1;          /* 先建模板 */
          sm.frustumCulled = false;
          grp.add(sm);
        }
      }
      const capMat = new THREE.MeshStandardMaterial({
        color: new THREE.Color("#" + hex).convertSRGBToLinear(),
        roughness: 0.88, metalness: 0.0, side: THREE.DoubleSide,
        stencilWrite: true, stencilRef: 0,
        stencilFunc: THREE.NotEqualStencilFunc,
        stencilFail: THREE.ReplaceStencilOp,
        stencilZFail: THREE.ReplaceStencilOp,
        stencilZPass: THREE.ReplaceStencilOp,
      });
      const cap = new THREE.Mesh(planeGeo, capMat);
      cap.userData.capHelper = true;
      cap.userData.isCap = true;
      cap.userData.lift = lift;        /* 沿法向的层间抬升量 */
      cap.renderOrder = 2;             /* 模板建好后再上色 */
      if (a === 0) cap.rotation.y = Math.PI / 2;
      else if (a === 1) cap.rotation.x = Math.PI / 2;
      cap.position.set(
        a === 0 ? 0 : (d.xmin + d.xmax) / 2,
        a === 1 ? 0 : (d.ymin + d.ymax) / 2,
        a === 2 ? 0 : (d.zmin + d.zmax) / 2);
      grp.add(cap);
    }
    this.scene.add(grp);
    this._capGroup = grp;
    this._applyClipPlane();            /* 把盖板放到当前切面位置 */
  }

  _edgesOf(geo, threshold) {
    if (!geo || !geo.attributes || !geo.attributes.position) return new THREE.BufferGeometry();
    const bb = new THREE.Box3().setFromBufferAttribute(geo.attributes.position);
    const size = new THREE.Vector3();
    bb.getSize(size);
    const m = Math.max(size.x, size.y, size.z) || 1;
    const k = 1 / m;
    const g2 = geo.clone();
    g2.scale(k, k, k);
    const edges = new THREE.EdgesGeometry(g2, threshold == null ? 28 : threshold);
    g2.dispose();
    edges.scale(m, m, m);
    return edges;
  }

  /* 屏幕坐标 → 拾取：结构体 → 源 → 监视器（就近优先）；命中即调 selectObj
   * （树选中 + 表单 + 高亮都走同一条路），落空则清高亮。 */
  pickAt(clientX, clientY) {
    if (!this.renderer || !this.camera) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1);
    if (!this._ray) this._ray = new THREE.Raycaster();
    this._ray.setFromCamera(ndc, this.camera);
    const hits = this._ray.intersectObjects(this.structureGroup.children, true);
    for (const h of hits) {
      let o = h.object;
      while (o && o.parent && o.parent !== this.structureGroup) o = o.parent;
      if (o && o.userData && o.userData.geoIndex != null) {
        if (typeof selectObj === "function")
          selectObj("geometry", o.userData.geoIndex);
        return;
      }
    }
    for (const pair of [["source", this._srcGroups], ["monitor", this._monGroups]]) {
      const map = pair[1] || {};
      for (const idx of Object.keys(map)) {
        if (this._ray.intersectObject(map[idx], true).length) {
          if (typeof selectObj === "function") selectObj(pair[0], +idx);
          return;
        }
      }
    }
    if (this.clearHighlight) this.clearHighlight();
  }

  /* ── 选中高亮（2026-10-21 用户需求："鼠标选在 box 上，3D 里要有点高亮"）──
   * 只做"强调"，不做"其余变暗"：选中对象叠一圈强调色描边（depthTest 关，
   * 永远压在实体前面），材质加一点自发光；其他对象保持原样。
   * 支持 geometry / source / monitor / materials（选材料 = 高亮所有用它的体）。 */
  highlightSelection(kind, index) {
    this._sel = (index == null || index < 0) ? null : { kind, index: +index };
    this._applyHighlight();
  }
  clearHighlight() {
    this._sel = null;
    this._applyHighlight();
  }
  _applyHighlight() {
    if (this._hlGroup) {
      this.scene.remove(this._hlGroup);
      this._hlGroup.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
      });
      this._hlGroup = null;
    }
    /* 先复位材质自发光（上一轮可能被点亮过） */
    for (const m of this.structureMeshes) {
      if (m && m.material && m.material.emissive) {
        m.material.emissive.setHex(0x000000);
        m.material.emissiveIntensity = 0;
      }
    }
    const sel = this._sel;
    if (!sel) { this.markDirty(); return; }
    /* 纯线框模式：面不画，光晕外壳也就没意义 → 改为把该对象的棱线染成高亮色
     * （先复位上一轮染过的线） */
    if (this._hlWires) {
      for (const w of this._hlWires) {
        if (w.material) w.material.color.copy(
          new THREE.Color(SCENE_PALETTE.STRUCTURE.EDGE).convertSRGBToLinear());
      }
      this._hlWires = null;
    }
    const targets = [];
    if (sel.kind === "geometry") {
      const mesh = this._geoIdxMeshes && this._geoIdxMeshes[sel.index];
      if (mesh) targets.push(mesh);
    } else if (sel.kind === "materials") {
      const mt = ((this._cfg && this._cfg.materials) || [])[sel.index];
      if (mt) {
        ((this._cfg && this._cfg.geometry) || []).forEach((g, i) => {
          if (g.material === mt.name && this._geoIdxMeshes[i])
            targets.push(this._geoIdxMeshes[i]);
        });
      }
    } else if (sel.kind === "source" || sel.kind === "monitor") {
      const map = sel.kind === "source" ? this._srcGroups : this._monGroups;
      if (map && map[sel.index]) targets.push(map[sel.index]);
    }
    if (!targets.length) { this.markDirty(); return; }
    const col = SCENE_PALETTE.HIGHLIGHT.SELECT;
    const hl = new THREE.Group();
    /* 外壳描边（BackSide 微放大）：1px 线在实心体上太细，外面套一层极淡的
     * 同色壳，读作"选中光晕"——不污染实体本色（emissive 会把蓝体染成紫）。 */
    const shellMat = new THREE.MeshBasicMaterial({
      color: col, transparent: true, opacity: 0.5, depthWrite: false,
      side: THREE.BackSide });
    if (this.wireMode === "wire") {
      /* 纯线框：把选中对象的棱线染成高亮色（面本来就不画，外壳看不见） */
      this._hlWires = [];
      for (const t of targets) {
        t.traverse((o) => {
          if (o.isLineSegments && o.material) {
            o.material.color.setHex(col);
            this._hlWires.push(o);
          }
        });
      }
      this.markDirty();
      return;
    }
    for (const t of targets) {
      t.traverse((o) => {
        if (!o.isMesh || !o.geometry) return;
        o.updateWorldMatrix(true, false);
        /* 只画"外壳光晕"，不画 1px 描边线：线要么被实体挡住（看不出选中），
         * 要么开 depthTest:false 把背面棱线也画出来（读作一堆内部线框）。
         * 外壳读作一圈柔光，干净且和材质的颜色不打架。 */
        const shell = new THREE.Mesh(o.geometry, shellMat);
        shell.scale.multiplyScalar(1.07);
        shell.applyMatrix4(o.matrixWorld);
        shell.renderOrder = 998;
        hl.add(shell);
      });
    }
    this.scene.add(hl);
    this._hlGroup = hl;
    this.markDirty();
  }

  setRulers(v) {
    this.showRulers = !!v;
    if (this._rulerLayer) this._rulerLayer.style.display = this.showRulers ? "" : "none";
    if (this._rulerSets) {
      for (const variants of this._rulerSets) {
        for (const vv of variants) if (!this.showRulers) vv.group.visible = false;
      }
      if (this.showRulers) this._updateRulers();
    }
    this.markDirty();
  }

  /* X 光：结构半透明（能看穿嵌套体）。只改结构体材质，不动源/监视器/域框。 */
  setXray(v) {
    this.showXray = !!v;
    for (const mesh of this.structureMeshes) {
      if (!mesh || !mesh.material) continue;
      if (mesh.userData && mesh.userData.sim) continue;   /* 合批实例自带半透明 */
      mesh.material.transparent = this.showXray || false;
      mesh.material.opacity = this.showXray ? 0.34 : 1;
      mesh.material.depthWrite = !this.showXray;
      mesh.material.needsUpdate = true;
    }
    this.markDirty();
  }

  setPmlVisible(v) { this.showPml = v; if (this._cfg) this.updateScene(this._cfg); }
  setGridVisible(v) { this.showGrid = v; if (this._cfg) this.updateScene(this._cfg); }
  /* ── 着色模式三态（2026-10-21）──
   * solid = 纯实体；edges = 实体 + 深色棱线（默认）；wire = 纯线框（面隐藏）。
   * 线框模式下面仍是"可拾取"的：材质只是不画（material.visible=false），
   * 点击命中的还是实体本身。 */
  setWireframeMode(mode) {
    this.wireMode = (mode === "solid" || mode === "wire") ? mode : "edges";
    this.showWireframe = this.wireMode !== "solid";
    this._applyWireMode();
    this.markDirty();
  }
  /* 兼容旧调用（2D 降级/外部）：布尔 → edges / solid */
  setWireframe(v) { this.setWireframeMode(v ? "edges" : "solid"); }
  _applyWireMode() {
    const wire = this.wireMode === "wire";
    const showEdges = this.showWireframe;
    const op = wire ? SCENE_PALETTE.STRUCTURE.EDGE_OPACITY_WIRE
                    : SCENE_PALETTE.STRUCTURE.EDGE_OPACITY;
    for (const mesh of this.structureMeshes) {
      if (!mesh) continue;
      if (mesh.material) {
        mesh.material.visible = !wire;      /* 纯线框：只留子层棱线 */
        mesh.material.needsUpdate = true;
      }
      mesh.traverse(function (child) {
        if (child.isLineSegments) {
          /* 轮廓线只在纯线框出现；普通棱线在 edges/wire 两态出现 */
          child.visible = child.userData.outline === true ? wire : showEdges;
          if (child.material) {
            child.material.opacity = op;
            child.material.transparent = op < 1;
            child.material.needsUpdate = true;
          }
        }
      });
    }
    /* 合批线框层（简化渲染）：不在 structureMeshes 的子树里，单独管 */
    this.structureGroup.traverse(function (child) {
      if (!child.userData || !child.userData.simLine) return;
      child.visible = showEdges;
      if (child.material) {
        child.material.opacity = wire ? 0.95 : 0.55;
        child.material.transparent = true;
        child.material.needsUpdate = true;
      }
    });
  }
  resetCamera() { this.fitCamera(); }
  takeScreenshot() {
    this._dirty = true;
    this.render();
    const link = document.createElement("a");
    link.download = "fdtd_scene.png";
    link.href = this.renderer.domElement.toDataURL("image/png");
    link.click();
  }

  /* 源标记：发光球 + 偏振箭头（复数分量取实部）+ 标签 */
  /* 箭头工厂：自建圆柱杆+圆锥头（弃用 THREE.ArrowHelper——本 vendor
   * r128 改版里杆长渲染恒 1e-4 与传入 length 无关, 世界坐标实测
   * 定位, 疑似 min-clamp 魔改且 matrixWorld 不随 scale 刷新;
   * 2026-08-31av）。长度锚定域尺度（markerR=域2%）×系数, 任何配置
   * 下视觉恒定。userData.arrow 供测试识别。 */
  _arrow(dir, origin, L, col, headL, headW) {
    const g = new THREE.Group();
    const d = new THREE.Vector3(dir[0], dir[1], dir[2]).normalize();
    g.position.set(origin[0], origin[1], origin[2]);
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d);
    g.userData.arrow = true;
    const mat = new THREE.MeshBasicMaterial({ color: col });
    const shaftLen = Math.max(L - headL, L * 0.5);
    const shaft = new THREE.Mesh(
      new THREE.CylinderGeometry(L * 0.015, L * 0.015, shaftLen, 6), mat);
    shaft.position.y = shaftLen / 2;
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(headW, headL, 12), mat);
    cone.position.y = shaftLen + headL / 2;
    g.add(shaft);
    g.add(cone);
    return g;
  }

  /* 源标记数值字段求值：center/size/radius/theta/phi/psi。size 的显式
   * null（"该轴全域"哨兵）保留；center 缺省 null → 0；求值失败 → 0 占位
   * （保守：标记退到原点/点形，仍可见，不崩不静默消失——resolveNum 已
   * warn 带字段名）。 */
  _resolveSourceNums(src) {
    const out = Object.assign({}, src);
    const tup = (k, keepNull) => {
      if (!Array.isArray(out[k])) return;
      out[k] = out[k].map((x, i) => {
        if (x == null && keepNull) return null;
        const n = this.resolveNum(x, "source." + k + "[" + i + "]");
        return n === null ? 0 : n;
      });
    };
    tup("center", false);
    tup("size", true);
    for (const k of ["radius", "theta", "phi", "psi"]) {
      if (out[k] == null) continue;
      const n = this.resolveNum(out[k], "source." + k);
      out[k] = n === null ? 0 : n;
    }
    return out;
  }

  /* 监视器标记数值字段求值：center 缺省/失败 → 0；size 显式 null 保留
   * （该轴全域），求值失败 → 0（点/薄片降级）。 */
  _resolveMonitorNums(mon) {
    const out = Object.assign({}, mon);
    if (Array.isArray(out.center)) {
      out.center = out.center.map((x, i) => {
        const n = this.resolveNum(x, "monitor.center[" + i + "]");
        return n === null ? 0 : n;
      });
    }
    if (Array.isArray(out.size)) {
      out.size = out.size.map((x, i) => {
        if (x == null) return null;
        const n = this.resolveNum(x, "monitor.size[" + i + "]");
        return n === null ? 0 : n;
      });
    }
    return out;
  }

  /* 平面矩形框（4 根细杆，落在注入面所在平面上）——注入面/监视面共用。
   * 不用 _edgeBars：薄板的 12 根杆两层几乎重合，会读成"双线玻璃墙"。 */
  _planeFrame(pos, dims, axis, color, opacity) {
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial(
      { color, transparent: true, opacity, depthWrite: false });
    const perp = [0, 1, 2].filter((i) => i !== axis);
    const t = Math.max(dims[0], dims[1], dims[2]) * 0.006;
    for (const p of perp) {
      const other = perp[0] === p ? perp[1] : perp[0];
      const len = dims[p];
      const d = [t, t, t];
      d[p] = len;
      for (const s of [-1, 1]) {
        const bar = new THREE.Mesh(
          new THREE.BoxGeometry(d[0], d[1], d[2]), mat);
        const q = [pos[0], pos[1], pos[2]];
        q[other] += s * dims[other] / 2;
        bar.position.set(q[0], q[1], q[2]);
        g.add(bar);
      }
    }
    return g;
  }

  _buildSourceMarker(src, index, radius, d) {
    src = this._resolveSourceNums(src);
    const col = SCENE_PALETTE.MARKERS.SOURCE;
    const planar = src.type === "plane_wave" || src.type === "gaussian_beam";
    const size = src.size || [];
    /* 每个源装进自己的 Group：选中高亮要按对象取描边（2026-10-21） */
    const g = new THREE.Group();

    if (planar) {
      const { axis, k, pos, t } = this._injectionPlane(src, d);
      /* 注入面（2026-10-21 第二次定案，用户：「只要有箭头就行了」）：
       * 平面波与高斯束**同一套形状语言** —— 极淡的一片注入面 + 细边框 +
       * 一支方向箭头。去掉的东西：往前的光柱圆柱（读作"实体"而不是"光"，
       * 还挡住域内的结构）、高斯专用的束径圆环（与平面波长得不一样，
       * 反而要人去猜）。两者的差别回到语义上：名字、类型标签、孔径宽度。 */
      const dims = [0, 1, 2].map((i) => (i === axis ? radius * 0.16 : t[i]));
      const slab = new THREE.Mesh(
        new THREE.BoxGeometry(dims[0], dims[1], dims[2]),
        new THREE.MeshBasicMaterial({
          color: new THREE.Color(col).convertSRGBToLinear(),
          transparent: true, opacity: 0.06,
          side: THREE.DoubleSide, depthWrite: false,
        }));
      slab.position.set(pos[0], pos[1], pos[2]);
      g.add(slab);
      /* 注入面轮廓：单层矩形细框（_planeFrame），读作一片"孔径" */
      g.add(this._planeFrame(pos, dims, axis, col, 0.85));

      /* 传播方向：唯一的方向语言就是这支箭头（长度锚定域尺度，
       * 倾斜按实际 k 矢量，同方言默认） */
      const kl = Math.hypot(k[0], k[1], k[2]) || 1;
      const L = radius * 6.5;
      g.add(this._arrow(
        [k[0] / kl, k[1] / kl, k[2] / kl], pos, L, col, L * 0.26, L * 0.10));

      this.markerGroup.add(g);
      this._srcGroups[index] = g;
      return;
    }

    /* soft_source：无 size = 点偶极子 → 发光小球（原样）；有 size =
     * 电流贴片 → 半透明琥珀盒（正槽=实际跨度, 非正槽=薄层 0.3×,
     * 与 monitor 面标记同款瘦身约定, 08-31av3）。 */
    const c = src.center || [0, 0, 0];
    const hasSize = size.some((s) => s > 0);
    if (hasSize) {
      const dims = [0, 1, 2].map((i) => (size[i] > 0 ? size[i] : radius * 0.3));
      const patch = new THREE.Mesh(
        new THREE.BoxGeometry(dims[0], dims[1], dims[2]),
        new THREE.MeshPhysicalMaterial({
          color: col, emissive: col, emissiveIntensity: 0.2,
          transparent: true, opacity: 0.25 }));
      patch.position.set(c[0], c[1], c[2]);
      g.add(patch);
    } else {
      /* 点源：发光核 + 外溢光晕（2026-10-21 重刻）——
       * 旧的半透明壳球读作"第二个灰球"；现在核用 emissive 白心、晕用叠加
       * 渐变 sprite，浅底上才真的"在发光"。 */
      const core = new THREE.Mesh(
        new THREE.SphereGeometry(radius * 0.8, 16, 12),
        new THREE.MeshStandardMaterial({
          color: new THREE.Color(col).convertSRGBToLinear(),
          emissive: new THREE.Color(col).convertSRGBToLinear(),
          emissiveIntensity: 0.85,
          roughness: 0.35, metalness: 0.0,
        }));
      core.position.set(c[0], c[1], c[2]);
      g.add(core);
      const halo = this._glowSprite(radius * 7.0, col, 0.6);
      halo.position.set(c[0], c[1], c[2]);
      g.add(halo);
    }

    // polarization: [1,0,0] 或 [[re,im]×3] —— 取实部画箭头（仅软源：
    // 平面波无 polarization 字段, E 方向由 theta/phi/psi 隐含, 不画,
    // 防止被误读成传播方向）
    let p = src.polarization || null;
    if (p && Array.isArray(p[0])) p = p.map((pair) => (pair ? pair[0] : 0));
    if (p && p.length === 3) {
      const len = Math.hypot(p[0], p[1], p[2]);
      if (len > 1e-9) {
        const arrowLen = radius * 2.5;
        g.add(this._arrow(
          [p[0] / len, p[1] / len, p[2] / len], c, arrowLen, col,
          arrowLen * 0.3, arrowLen * 0.15));
      }
    }
    this.markerGroup.add(g);
    this._srcGroups[index] = g;
    this._srcLabel(src, index, c, radius, col, size);
  }

  /* 注入面几何（plane_wave / gaussian_beam 共用; 语义对齐手册 05-src 页）：
   * 传播轴 = propagation_axis > from_face > size 恰一非正槽（歧义 z）;
   * 位置 = center（from_face 覆盖为贴该面内侧、横轴中心=域心）;
   * k = theta/phi 实际方向（默认 ±轴）。横向跨度 t[i] = 正槽值或全域。 */
  _injectionPlane(src, d) {
    const spans = [d.xmax - d.xmin, d.ymax - d.ymin, d.zmax - d.zmin];
    const mid = [(d.xmin + d.xmax) / 2, (d.ymin + d.ymax) / 2, (d.zmin + d.zmax) / 2];
    const size = src.size || [null, null, null];
    let axis = { x: 0, y: 1, z: 2 }[src.propagation_axis] ?? null;
    let sign = src.direction === "-" ? -1 : 1;
    const pos = (src.center || mid).slice(0, 3);
    if (src.from_face) {
      const key = src.from_face[0];                 // "x_min" → "x"
      axis = "xyz".indexOf(key);
      sign = src.from_face.endsWith("_max") ? -1 : 1;
      const off = spans[axis] * 0.03;               // 贴面内侧（显示近似）
      pos[axis] = src.from_face.endsWith("_max")
        ? d[key + "max"] - off : d[key + "min"] + off;
      pos[(axis + 1) % 3] = mid[(axis + 1) % 3];
      pos[(axis + 2) % 3] = mid[(axis + 2) % 3];
    }
    if (axis == null) {
      const nonPos = [0, 1, 2].filter((i) => !(size[i] > 0));
      axis = nonPos.length === 1 ? nonPos[0] : 2;
    }
    let k = [0, 0, 0];
    k[axis] = sign;
    const th = (src.theta || 0) * Math.PI / 180, ph = (src.phi || 0) * Math.PI / 180;
    if (th || ph) {
      k = [Math.sin(th) * Math.cos(ph), Math.sin(th) * Math.sin(ph), Math.cos(th)];
      if (sign < 0) k = k.map((v) => -v);
    }
    const t = [0, 1, 2].map((i) =>
      i === axis ? 0 : (size[i] > 0 ? size[i] : spans[i]));
    /* Round C 语义修正：无显式注入面又无显式中心时，把注入面贴到传播
     * 方向对应的域面（默认平面波读作"从某面射入"，而非湮没域心）。
     * 面由 k 符号定：k>0 贴轴 min 面、k<0 贴轴 max 面；横轴居中=域心。 */
    if (!src.from_face && !src.center) {
      const key = "xyz"[axis];
      const off = spans[axis] * 0.03;
      pos[axis] = (k[axis] < 0) ? d[key + "max"] - off : d[key + "min"] + off;
      pos[(axis + 1) % 3] = mid[(axis + 1) % 3];
      pos[(axis + 2) % 3] = mid[(axis + 2) % 3];
    }
    return { axis, k, pos, t };
  }


  /* 监视器标记（2026-10-21 重刻）：监视器 = 「记录面」而不是发光体。
   * 旧值用 emissive 让它们自己发亮，观感与源混淆；现在统一为
   * 「极淡的半透明面 + 细杆描边框」：面读作采样面，框读作记录区边界，
   * 颜色仍走 MONITOR 青（与源蓝、几何材料色三族分开）。 */
  _buildMonitorMarker(mon, index, radius, d) {
    mon = this._resolveMonitorNums(mon);
    const col = SCENE_PALETTE.MARKERS.MONITOR;
    const c = mon.center || [0, 0, 0];
    const sz = mon.size || [0, 0, 0];
    const spans = [d.xmax - d.xmin, d.ymax - d.ymin, d.zmax - d.zmin];
    const domMid = [(d.xmin + d.xmax) / 2, (d.ymin + d.ymax) / 2, (d.zmin + d.zmax) / 2];
    let volume = false;
    const nSpan = sz.filter((s) => s > 0).length;      // 0=点 2=面 3=体
    const thin = radius * (nSpan === 0 ? 1.0 : 0.22);  // 点=方块边长; 面=法向厚
    const dim = [0, 0, 0], pos = [c[0], c[1], c[2]];
    for (let i = 0; i < 3; i++) {
      const s = sz[i];
      if (s == null) { dim[i] = spans[i]; pos[i] = domMid[i]; volume = true; }
      else if (s > 0) { dim[i] = s; volume = true; }
      else dim[i] = thin;
    }
    const lcol = new THREE.Color(col).convertSRGBToLinear();
    /* 每个监视器装进自己的 Group：选中高亮要按对象取描边（2026-10-21） */
    const g = new THREE.Group();
    if (nSpan === 0) {
      /* 点监视器：空心小方框（中心留空 = 一个探针点） */
      const tgp = Math.max(dim[0], dim[1], dim[2]) * 0.22;
      g.add(this._edgeBars(
        pos[0], pos[1], pos[2], dim[0], dim[1], dim[2], tgp, col, 0.95));
      const dot = this._glowSprite(dim[0] * 2.4, col, 0.5);
      dot.position.set(pos[0], pos[1], pos[2]);
      g.add(dot);
    } else {
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(dim[0], dim[1], dim[2]),
        new THREE.MeshBasicMaterial({
          color: lcol, transparent: true, depthWrite: false,
          opacity: volume ? 0.018 : 0.025, side: THREE.DoubleSide,
        }));
      mesh.position.set(pos[0], pos[1], pos[2]);
      g.add(mesh);
      const tgm = Math.max(dim[0], dim[1], dim[2]) * 0.0035;
      g.add(this._edgeBars(
        pos[0], pos[1], pos[2], dim[0], dim[1], dim[2], tgm, col, 0.5));
    }
    this.markerGroup.add(g);
    this._monGroups[index] = g;
  }


  /* 标签卡纵横比（画布 200×90，与 sprite scale.y = s×_labelAspect 同源） */

  render() {
    this.controls.update();          /* 阻尼平滑：动 → change → markDirty → 下一帧画 */
    if (!this._dirty) return;        /* 按需渲染：场景无变化，零开销返回 */
    this.renderer.render(this.scene, this.camera);
    // HUD 轴：反向跟随主视角方向，正交视锥固定（标签不随缩放变大小）
    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    this.axesCamera.position.copy(dir.clone().multiplyScalar(-140));
    this.axesCamera.lookAt(0, 0, 0);
    this.axesRenderer.render(this.axesScene, this.axesCamera);
    this._dirty = false;
  }

  onResize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    const aspect = w / h;
    if (this.camera.isPerspectiveCamera) {
      this.camera.aspect = aspect;
    } else {
      const topVal = this.camera.top;
      this.camera.left = -topVal * aspect;
      this.camera.right = topVal * aspect;
    }
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    /* 画布尺寸变了 → 坐标尺读数要重新投影（否则平移/拉宽后数字错位） */
    this._updateRulers();
    this.markDirty();
  }

  /* 世界单位/像素：正交按视锥高度，透视按到框心的距离 × 视场角 */
}
