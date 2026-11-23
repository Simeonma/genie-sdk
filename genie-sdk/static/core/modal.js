/* core/modal.js — 通用模态与拟合小图（2026-11-22 自主 app.js 抽出）
 * 被面共用；零业务知识。 */
"use strict";

  /* ── 材料库：两段式（选材料 → 拟合预览窗口）────────────────────
   * 第一段只挑材料；第二段给参数（波长窗口 / 极点上限 / 强制无源）
   * 并把**原始数据点**与**拟合曲线**画出来。拟合跑在 worker 里
   * （genie._fit_preview → out(kind="matfit")，只读、不碰模型）；入模
   * 走 add_material(type='library') + fit_material，不新增落模路径。 */
  const GM_CSS = `
  .gm-bd{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;
    display:flex;align-items:center;justify-content:center;font:13px/1.5 system-ui,sans-serif}
  .gm-card{background:#16191d;color:#d8dee6;border:1px solid #2c333a;border-radius:10px;
    box-shadow:0 18px 60px rgba(0,0,0,.6);padding:16px 18px;min-width:360px;max-width:92vw}
  .gm-card h3{margin:0 0 10px;font-size:14px;font-weight:600;color:#eaf0f7}
  .gm-row{display:flex;gap:8px;align-items:center;margin:8px 0}
  .gm-row label{color:#9fb0c2;min-width:96px;font-size:12px}
  .gm-card select,.gm-card input[type=number],.gm-card input[type=text]{
    background:#0e1116;color:#d8dee6;border:1px solid #2c333a;border-radius:6px;
    padding:5px 7px;font:12px system-ui,sans-serif;outline:none}
  .gm-card input[type=number]{width:82px}
  .gm-card select{min-width:200px}
  .gm-btns{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
  .gm-btn{background:#222932;color:#cfd8e3;border:1px solid #33404d;border-radius:6px;
    padding:6px 14px;font-size:12px;cursor:pointer}
  .gm-btn:hover{background:#2b3541}
  .gm-btn.pri{background:#2f6feb;border-color:#2f6feb;color:#fff}
  .gm-btn.pri:hover{background:#3b7bf5}
  .gm-btn:disabled{opacity:.45;cursor:default}
  .gm-fit{display:flex;gap:16px;align-items:flex-start}
  .gm-plot{background:#0e1116;border:1px solid #222a33;border-radius:8px;padding:6px}
  .gm-meta{margin-top:8px;color:#8fa2b6;font-size:12px;max-width:560px}
  .gm-warn{color:#e0a33e}
  .gm-err{color:#ff6b6b}
  `;
  function _gmCss() {
    if (document.getElementById("gm-css")) return;
    const st = document.createElement("style");
    st.id = "gm-css"; st.textContent = GM_CSS;
    document.head.appendChild(st);
  }
  function _gmModal(title) {
    _gmCss();
    const bd = document.createElement("div");
    bd.className = "gm-bd";
    const card = document.createElement("div");
    card.className = "gm-card";
    const h = document.createElement("h3");
    h.textContent = title;
    const body = document.createElement("div");
    const btns = document.createElement("div");
    btns.className = "gm-btns";
    card.appendChild(h); card.appendChild(body); card.appendChild(btns);
    bd.appendChild(card);
    document.body.appendChild(bd);
    const close = () => { bd.remove(); };
    bd.addEventListener("mousedown", (e) => { if (e.target === bd) close(); });
    const onKey = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } };
    document.addEventListener("keydown", onKey);
    bd._close = close;
    return { root: bd, body, btns, close };
  }
  function _gmBtn(label, pri, onclick) {
    const b = document.createElement("button");
    b.className = "gm-btn" + (pri ? " pri" : "");
    b.textContent = label;
    b.addEventListener("click", onclick);
    return b;
  }

  /* n、k 两格：原始散点 + 拟合实线，共享波长轴（nm）。 */
  function _gmPlot(d) {
    const W = 460, H = 150, PADL = 46, PADR = 10, PADT = 12, PADB = 22;
    const wl0 = d.band[0] * 1e9, wl1 = d.band[1] * 1e9;
    const xs = (nm) => PADL + (nm - wl0) / Math.max(wl1 - wl0, 1e-9) * (W - PADL - PADR);
    function panel(ytitle, ys, yf, yRaw) {
      let lo = Infinity, hi = -Infinity;
      ys.forEach((v) => { if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      yf.forEach((v) => { if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
      if (hi - lo < 1e-9) { hi = lo + 1; }
      const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      const yy = (v) => PADT + (hi - v) / (hi - lo) * (H - PADT - PADB);
      const pts = d.wl_fit.map((w, i) => xs(w * 1e9) + "," + yy(yf[i])).join(" ");
      const dots = d.wl.map((w, i) => "<circle cx=\"" + xs(w * 1e9) + "\" cy=\"" + yy(ys[i]) +
        "\" r=\"2.6\" fill=\"#ffb74d\"/>").join("");
      return "<g><rect x=\"" + PADL + "\" y=\"" + PADT + "\" width=\"" + (W - PADL - PADR) +
        "\" height=\"" + (H - PADT - PADB) + "\" fill=\"none\" stroke=\"#222a33\"/>" +
        "<text x=\"6\" y=\"" + (PADT + 10) + "\" fill=\"#8fa2b6\" font-size=\"11\">" + ytitle + "</text>" +
        "<text x=\"6\" y=\"" + (PADT + 22) + "\" fill=\"#5f7183\" font-size=\"9\">" + hi.toFixed(2) + "</text>" +
        "<text x=\"6\" y=\"" + (H - PADB) + "\" fill=\"#5f7183\" font-size=\"9\">" + lo.toFixed(2) + "</text>" +
        "<polyline points=\"" + pts + "\" fill=\"none\" stroke=\"#4ea1ff\" stroke-width=\"1.6\"/>" +
        dots + "</g>";
    }
    const s1 = panel("n", d.n, d.n_fit);
    const s2 = panel("k", d.k, d.k_fit);
    return "<svg width=\"" + W + "\" height=\"" + (H * 2 + 18) + "\" viewBox=\"0 0 " + W + " " + (H * 2 + 18) + "\">" +
      s1 + "<g transform=\"translate(0," + (H + 14) + ")\">" + s2 + "</g>" +
      "<text x=\"" + ((W + PADL) / 2) + "\" y=\"" + (H * 2 + 14) + "\" fill=\"#8fa2b6\" font-size=\"10\">" +
      wl0.toFixed(0) + "–" + wl1.toFixed(0) + " nm</text></svg>";
  }


export { _gmCss, _gmModal, _gmBtn, _gmPlot };
