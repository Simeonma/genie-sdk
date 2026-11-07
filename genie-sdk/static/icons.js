/* 树/检查器图标（2026-09 UI 打磨）：内联 SVG 矢量图标集合。
 *
 * 统一 16×16 viewBox、fill=none、stroke=currentColor——颜色由 CSS
 * 按类别 class 着色（.tree-ic.domain / .pane-icon.source 等），
 * 选中态/主题下图标自动跟随而不失真。
 *
 * 渲染纪律：零 innerHTML——iconEl() 用 DOMParser 安全解析（XML，
 * 无 HTML 注入），解析结果缓存、cloneNode 复用。
 *
 * 用法：document.querySelector(".tree-ic").appendChild(iconEl("geometry"))
 */
"use strict";

const ICON_PATHS = {
  /* 域：斜立方体线框 */
  domain: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 5.5 L8 3 L13.5 5.5 L13.5 11 L8 13.5 L2.5 11 Z"/><path d="M2.5 5.5 L8 8 L13.5 5.5"/><path d="M8 8 L8 13.5"/></svg>',
  /* 网格：外框 + 井字 */
  mesh: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.5 L13.5 6.5 M2.5 9.5 L13.5 9.5 M6.5 2.5 L6.5 13.5 M9.5 2.5 L9.5 13.5"/></svg>',
  /* 材料：棱镜 + 折射光线——光学材料最直观的表达（2026-09-02
   * 站长反馈三层堆叠看不懂；光进入材料被折射 = 材料的光学性质） */
  materials: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5 L13.5 8 L8 13.5 L2.5 8 Z"/><path d="M3.5 4.5 L6.8 8.3"/><path d="M9.2 7.7 L12.5 4.5"/></svg>',
  /* 几何：单个三角形——简洁的"几何体"抽象（2026-09-02 站长定案；
   * 与 structure 的一堆三角形形成「单个 vs 集合」语义） */
  geometry: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.8 L13.8 11.8 L2.2 11.8 Z"/></svg>',
  /* 结构：一堆三角形（品字形三个小三角）——"一组几何体"的集合感
   * （2026-09-02 站长定案；与 geometry 单个三角形呼应） */
  structure: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5 L11.2 7.2 L4.8 7.2 Z"/><path d="M3.8 10.2 L7 14.9 L0.6 14.9 Z"/><path d="M12.2 10.2 L15.4 14.9 L9 14.9 Z"/></svg>',
  /* 源：点源 + 同心涟漪——看得出"从中心向外辐射"（2026-09-02
   * 站长反馈原半弧波纹看不出形状，改完整圆环） */
  source: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8.5" r="1.5"/><circle cx="8" cy="8.5" r="3.4"/><circle cx="8" cy="8.5" r="5.3"/></svg>',
  /* 监视器：屏幕 + 支架 + 底座（2026-09-02 站长反馈要"屏幕"） */
  monitor: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2" width="11" height="8" rx="1.2"/><path d="M4.5 5 L11.5 5"/><path d="M8 10 L8 12.5"/><path d="M5.5 12.5 L10.5 12.5"/></svg>',
  /* 作业：列表卡片 */
  jobs: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M5.5 5.5 L10.5 5.5 M5.5 8 L10.5 8 M5.5 10.5 L8.5 10.5"/></svg>',
  /* 运行（检查器专用，树中无此节） */
  run: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 3.5 L11.5 8 L5.5 12.5 Z"/></svg>',
  /* 参数扫描（2026-09 sweep 树栏）：双滑杆 = 轴×取值 */
  sweep: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 5 H13.5 M2.5 11 H13.5"/><circle cx="10.5" cy="5" r="1.8" fill="currentColor" stroke="none"/><circle cx="5.5" cy="11" r="1.8" fill="currentColor" stroke="none"/></svg>',
  /* 参数系统批 2（2026-10）：全局参数节（COMSOL 式 name/value 表）
   * 2026-10-21：旧字形画的是字母 A + S 曲线（远看读成「As」）——改双滑杆
   * + 滑块：参数面板的通用语义，与「运行参数」滑杆图标同族。 */
  params: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.6 5.2 H13.4 M2.6 10.8 H13.4"/><circle cx="5.8" cy="5.2" r="1.9"/><circle cx="10.2" cy="10.8" r="1.9"/></svg>',

  /* ══ 工具栏一键添加的形状图标（2026-09-02g）══ */
  box: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5.5 L8 3 L13 5.5 L13 11 L8 13.5 L3 11 Z"/><path d="M3 5.5 L8 8 L13 5.5"/><path d="M8 8 L8 13.5"/></svg>',
  sphere: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.2"/><path d="M4.9 4.9 A3.8 3.8 0 0 1 8.6 3.4"/></svg>',
  cylinder: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5 A4 2 0 0 1 12 5.5 L12 10.5 A4 2 0 0 1 4 10.5 Z"/><path d="M4 5.5 A4 2 0 0 0 12 5.5"/></svg>',
  ngon_z: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5 L13 5.5 L13 10.5 L8 13.5 L3 10.5 L3 5.5 Z"/><path d="M8 2.5 L8 13.5"/></svg>',
  ellipse_z: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="8" cy="8" rx="6" ry="3.8"/><path d="M8 4.2 L8 11.8"/></svg>',
  ring_z: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5"/><circle cx="8" cy="8" r="2.5"/></svg>',
  polygon_z: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5 L13.2 6.2 L11 12.3 L5 12.3 L2.8 6.2 Z"/><path d="M8 3.8 L8 11.5"/></svg>',
  polygon_swept: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 4 L12.5 4 L10.5 12 L1.5 12 Z"/><path d="M3.5 4 L4.8 9.8"/><path d="M10.5 12 L11.8 6.2"/></svg>',

  /* 源类型（工具栏带文字按钮） */
  soft_source: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="9.5" r="1.5"/><circle cx="8" cy="9.5" r="4"/></svg>',
  plane_wave: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 4.5 L13.5 4.5 M2.5 8 L13.5 8 M2.5 11.5 L13.5 11.5"/></svg>',
  gaussian_beam: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 3.5 C8 5.5, 8 10.5, 3.5 12.5"/><path d="M12.5 3.5 C8 5.5, 8 10.5, 12.5 12.5"/></svg>',

  /* 监视器类型（工具栏带文字按钮） */
  mon_time: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 10 Q4.5 4.5 6.5 8 T10.5 8 T13.5 6"/></svg>',
  mon_freq: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 5 L3.5 11 M8 3 L8 13 M12.5 5 L12.5 11"/></svg>',
  mon_index: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="10" height="10" rx="1"/><path d="M3 10.5 L13 4"/></svg>',

  /* ══ 3D 场景工具条图标（2026-09-02 审查修复：emoji/unicode → 统一 SVG）══ */
  fitcam: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="2.6"/><path d="M8 1.5 V4 M8 12 V14.5 M1.5 8 H4 M12 8 H14.5"/></svg>',
  wire: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5.5 L8 3 L13 5.5 L13 11 L8 13.5 L3 11 Z"/><path d="M3 5.5 L8 8 L13 5.5 M8 8 L8 13.5" stroke-dasharray="2 1.6"/></svg>',
  pml: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><rect x="5.5" y="5.5" width="5" height="5" rx="0.8"/></svg>',
  grid: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.2 H13.5 M2.5 9.9 H13.5 M6.2 2.5 V13.5 M9.9 2.5 V13.5" stroke-width="1"/></svg>',
  labels: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8.5 L8.5 2.5 H13.5 V7.5 L7.5 13.5 Z"/><circle cx="10.8" cy="5.2" r="0.9"/></svg>',
  results: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 13.5 H13.5"/><path d="M4.7 13.5 V9.2 M8 13.5 V4.8 M11.3 13.5 V7.2"/></svg>',
  shot: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="12" height="8" rx="1.5"/><circle cx="8" cy="9" r="2.4"/><path d="M5.8 5 L6.8 3.2 H9.2 L10.2 5"/></svg>',

  /* ══ 材料分型 / 布局（2026-10-21 收编 previews.js：全站图标单一线稿族）══ */
  /* 恒定折射率：一块均匀厚板 + 三点等距标记（处处同一个 n） */
  mat_const: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.6" y="5.6" width="10.8" height="4.8" rx="0.9"/><path d="M5.2 8 H5.3 M7.9 8 H8 M10.7 8 H10.8" stroke-width="1.8"/></svg>',
  /* 色散材料：坐标轴内一条共振峰曲线（n 随 λ 变） */
  mat_lorentz: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.4 2.6 V13.4 H13.6" stroke-opacity="0.55"/><path d="M3.6 11.4 C5.4 11.2 6.1 9.9 6.7 6.4 C7.1 4 8.3 4.2 8.8 6.8 C9.5 10.3 10.6 11.4 12.9 11.7"/></svg>',
  /* 材料库：三片厚薄一致的材质小样并排 + 底导轨 */
  mat_library: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2.6" y="4.2" width="3.2" height="7.6" rx="0.7"/><rect x="6.4" y="2.8" width="3.2" height="9" rx="0.7"/><rect x="10.2" y="5.4" width="3.2" height="6.4" rx="0.7"/></svg>',
  /* 工作台布局：窗口外框 + 三格分栏 */
  layout: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.4" y="3.2" width="11.2" height="9.6" rx="1.1"/><path d="M2.4 6.2 H13.6"/><path d="M6.4 6.2 V12.8 M10.6 6.2 V12.8"/></svg>',

  /* 许愿星芒（2026-09 聊天欢迎横幅：替代 🪔 emoji——四芒星 = make a wish，
   * 同 16 viewBox 圆角细线系；颜色随 currentColor（横幅内 = --warning）） */
  sparkle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2 L9.8 6.2 L14 8 L9.8 9.8 L8 14 L6.2 9.8 L2 8 L6.2 6.2 Z"/><path d="M13.4 2.4 L13.4 4.6 M12.3 3.5 L14.5 3.5"/></svg>',

  /* ══ 动作/状态图标（2026-09 审计 P1-5：unicode 字形当图标 → 统一 SVG 家族）══ */
  /* close：✕ 两笔交叉——关闭/删除（树行删除钮、聊天收起、变量行删除） */
  close: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.3 3.3 L12.7 12.7 M12.7 3.3 L3.3 12.7"/></svg>',
  /* chev_down：▾ 单折箭头——"收起"型动作（结果抽屉收起钮；折叠箭头本体
   * 仍是 CSS content / e2e 锁定的文本字形，本键只服务按钮语义） */
  chev_down: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4.1 6.3 L8 10.2 L11.9 6.3"/></svg>',
  /* stop：■ 圆角方块——停止（运行停止钮；与 run 三角成对） */
  stop: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3.5" width="9" height="9" rx="1.3"/></svg>',
  /* copy：⧉ 前后两张圆角纸（后纸只留露出的上/左缘，避免前纸内穿越线）——
   * 复制实例（树行复制钮、右键菜单复制） */
  copy: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6.4 V4.2 A1.4 1.4 0 0 0 8.6 2.8 H4.2 A1.4 1.4 0 0 0 2.8 4.2 V8.6 A1.4 1.4 0 0 0 4.2 10 H6.4"/><rect x="6.4" y="6.4" width="6.8" height="6.8" rx="1.4"/></svg>',
  /* download：⤓ 箭头入托盘——取回/下载（.py 下载、results.npz、材料节点插入） */
  download: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.6 V9.2 M4.9 6.3 L8 9.4 L11.1 6.3"/><path d="M3.6 11.4 V12.7 H12.4 V11.4"/></svg>',
  /* upload：⤵ 托盘出箭头——装回/上载（作业配置载回表单） */
  upload: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 11.2 V4.4 M4.9 7.5 L8 4.4 L11.1 7.5"/><path d="M3.6 11.4 V12.7 H12.4 V11.4"/></svg>',
  /* refresh：↻ 顺时针回环箭头——重新拉取（结果抽屉"刷新"、作业 running 转圈态） */
  refresh: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.2 8 A5.9 5.9 0 0 1 8.1 2.2 A6 6 0 0 1 13.9 5.5 M13.9 2.3 V5.5 L10.7 5.5"/></svg>',
  /* reset：⟲ 逆时针回环箭头——开新/回到初始（聊天"开新对话"，与 refresh 方向相异以示区别） */
  reset: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.8 8 A5.9 5.9 0 0 0 7.9 2.2 A6 6 0 0 0 2.1 5.5 M2.1 2.3 V5.5 H5.3"/></svg>',
  /* gear：⚙ 八齿齿轮——配置（聊天"配置 LLM"入口） */
  gear: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="2.4"/><path d="M8 4.6 V3 M8 13 V11.4 M11.4 8 H13 M3 8 H4.6 M10.4 5.6 L11.5 4.5 M5.6 10.4 L4.5 11.5 M10.4 10.4 L11.5 11.5 M5.6 5.6 L4.5 4.5"/></svg>',
  /* expand：⤢ 四角外张——进入全屏（聊天全屏钮非全屏态） */
  expand: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.4 2.6 H3.8 Q2.6 2.6 2.6 3.8 V5.4 M10.6 2.6 H12.2 Q13.4 2.6 13.4 3.8 V5.4 M13.4 10.6 V12.2 Q13.4 13.4 12.2 13.4 H10.6 M2.6 10.6 V12.2 Q2.6 13.4 3.8 13.4 H5.4"/></svg>',
  /* collapse：四角内收——退出全屏（聊天全屏钮全屏态；与 expand 成对切换） */
  collapse: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.3 2.6 V3.9 Q5.3 5.3 3.9 5.3 H2.6 M10.7 2.6 V3.9 Q10.7 5.3 12.1 5.3 H13.4 M13.4 10.7 V9.3 Q13.4 7.9 12 7.9 H10.6 M2.6 10.7 V9.3 Q2.6 7.9 4 7.9 H5.4"/></svg>',
  /* check：✓ 对勾——成功/就绪态（已登录徽章、保存 LLM、作业 succeeded 状态） */
  check: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.2 8.5 L6.7 12 L12.8 4.7"/></svg>',
  /* edit：✎ 斜铅笔——编辑/重命名（右键菜单编辑、agent 编辑中徽标） */
  edit: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2.4 A1.4 1.4 0 0 1 13 4.4 L4.7 12.7 L2 13.3 L2.7 10.6 Z"/><path d="M8.3 13.4 H14"/></svg>',
  /* menu：☰ 三横——排队/待处理等非运行非成功态（作业标题状态图标，
   * 语义由相邻文本承载，图标只作占位图形） */
  menu: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.9 4.7 H13.1 M2.9 8 H13.1 M2.9 11.3 H13.1"/></svg>',
  /* think：💭 气泡+四芒星——思考过程（聊天推理块 summary 前缀） */
  think: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.6 10.6 H10.4 A2.4 2.4 0 0 0 12.8 8.2 A2.9 2.9 0 0 0 10.2 5.3 A3 3 0 0 0 4.7 5.9 A2.5 2.5 0 0 0 5.6 10.6 Z"/><path d="M13.2 1.6 L13.2 3.8 M12.1 2.7 L14.3 2.7"/></svg>',
  /* note：📋 夹板便签——查看运行日志（作业详情"日志"钮） */
  note: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3.1" y="3.3" width="9.8" height="10.6" rx="1.3"/><path d="M5.9 3.3 V2.4 A1.4 1.4 0 0 1 7.3 1 H8.7 A1.4 1.4 0 0 1 10.1 2.4 V3.3"/><path d="M5.5 6.9 H10.5 M5.5 9.1 H10.5"/></svg>',
  /* vial：⚗ 锥形瓶+液面——材料拟合预览/拟合工具（form.js 两个 ⚗ 按钮） */
  vial: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.6 2.9 V5.9 C6.6 7.1 6.2 7.8 5.3 8.8 A3.3 3.3 0 0 0 10.7 8.8 C9.8 7.8 9.4 7.1 9.4 5.9 V2.9"/><path d="M5.7 2.9 H10.3 M6 11 H10"/></svg>',
  save: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.0 3.0 H10.6 L13.0 5.4 V13.0 H3.0 Z" fill="currentColor" fill-opacity="0.12"/><path d="M3.0 3.0 H10.6 L13.0 5.4 V13.0 H3.0 Z"/><path d="M5.4 3.0 V6.2 H10.2 V3.0"/><rect x="5.4" y="9.0" width="5.2" height="4.0" fill="currentColor" fill-opacity="0.3" stroke-width="1.2"/></svg>',
  /* runSel：实心三角 + 虚线走带——运行选段 */
  runSel: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.4 4.6 L7.6 8.0 L3.4 11.4 Z" fill="currentColor" fill-opacity="0.85" stroke-width="1.2"/><path d="M10.0 4.6 V11.4 M12.4 5.4 V10.6" stroke-opacity="0.55"/></svg>',

  /* ══ File 菜单文档图标（2026-10 站长裁决：Windows 文件风格——
   * 白纸折角 + 类型色徽记。有意脱离 currentColor 线框家族：文件类型
   * 靠固定色相识别（py=品牌蓝 / cfg=琥珀 / log=绿），主题色随动会
   * 消灭这层「这是什么文件」的语义，故此处 fill/stroke 写死。 */
  /* file_py：蓝折角 .py 文档，‹/› 代码徽记（打开/下载脚本共用） */
  /* 打开/存储仿真（2026-10）：与 ribbon 同族的 slate 线稿——文档片 +
   * 动作箭头，stroke currentColor（File 组曾用橙色彩色线稿，全灰化后突兀） */
  file_open: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 4.5 L6 2.5 L13.5 2.5 V10.5 H10"/><path d="M2.5 4.5 V13.5 H6.5"/><path d="M6 2.5 V5 H8.5"/><path d="M13.5 6.5 L8.5 6.5"/><path d="M11 4.5 L13.5 6.5 L11 8.5"/></svg>',
  file_save: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 3.5 H10.5 L13.5 6.5 V13.5 H2.5 Z"/><path d="M4.5 3.5 V6.5 H9.5 V3.5"/><rect x="4.5" y="9" width="7" height="4.5" rx="0.5"/><path d="M13.5 9.5 L11 12 L8.5 9.5"/></svg>',
  file_py: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 1.5 H9.5 L12.5 4.5 V14.5 H3.5 Z" fill="#F8FAFC" stroke="#94A3B8" stroke-width="1.1"/><path d="M9.5 1.5 V4.5 H12.5 Z" fill="#1F62B0" stroke="#1F62B0" stroke-width="1.1"/><path d="M5.7 7.4 L4.1 9.0 L5.7 10.6 M7.3 7.4 L8.9 9.0 L7.3 10.6" stroke="#1F62B0" stroke-width="1.2"/></svg>',
  /* file_cfg：琥珀折角 .json 配置文档，双滑杆徽记（导入/导出状态共用） */
  file_cfg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 1.5 H9.5 L12.5 4.5 V14.5 H3.5 Z" fill="#F8FAFC" stroke="#94A3B8" stroke-width="1.1"/><path d="M9.5 1.5 V4.5 H12.5 Z" fill="#B45309" stroke="#B45309" stroke-width="1.1"/><path d="M5.0 7.3 H11.0 M5.0 10.7 H11.0" stroke="#B45309" stroke-width="1.2"/><circle cx="9.4" cy="7.3" r="1.2" fill="#F8FAFC" stroke="#B45309" stroke-width="1.2"/><circle cx="6.6" cy="10.7" r="1.2" fill="#F8FAFC" stroke="#B45309" stroke-width="1.2"/></svg>',
  /* file_log：绿折角 .txt 记录文档，文本行徽记（导出终端记录） */
  file_log: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 1.5 H9.5 L12.5 4.5 V14.5 H3.5 Z" fill="#F8FAFC" stroke="#94A3B8" stroke-width="1.1"/><path d="M9.5 1.5 V4.5 H12.5 Z" fill="#15803D" stroke="#15803D" stroke-width="1.1"/><path d="M5.3 7.2 H10.7 M5.3 9.6 H10.7 M5.3 12.0 H8.3" stroke="#15803D" stroke-width="1.2"/></svg>',
};

const _iconCache = {};

/* 取一个图标元素（克隆自缓存；未知 key 回退 geometry）。
 * 返回的 <svg> 一律 aria-hidden（装饰性图标：语义由所在按钮的
 * aria-label / 相邻文本承担），随 clone 每次设置。 */
function iconEl(key) {
  const svg = ICON_PATHS[key] || ICON_PATHS.geometry;
  if (!_iconCache[key]) {
    _iconCache[key] = new DOMParser()
      .parseFromString(svg, "image/svg+xml").documentElement;
  }
  const ic = _iconCache[key].cloneNode(true);
  ic.setAttribute("aria-hidden", "true");
  return ic;
}
