/* modules/matlib.js — 库材料拟合对话框（G20 材料分类学重构 2026-10-16）
 *
 * 第一性原则：内核只吃 pole，cfg 里唯一的合法材料实体 = wire 材料节点。
 * nk 数据（内置 37 库 / 用户自备三列）不是材料，是拟合的原料——pole
 * 永远是设计期按波长现算的暂态产物（同一份 Au，可见光与紫外拟出不同
 * 的 pole）。本对话框 = "选数据 → 定波段 → fit → pole 入 cfg" 的唯一
 * GUI 通道，替代已退役的 from_library 死入口（类别错误：把数据引用
 * 塞进 cfg）。
 *
 * 端点：/api/tools/nk_library、/api/tools/material_fit——2026-10-27 G19b
 * 起为 Studio 本地实现（studio/server.py，genie.core.material + 本地
 * scipy；网关 tools 面随协议层手术撤除）；X-API-Key = localStorage 优先。
 * 经典 script 按序加载，跨模块引用为运行期全局查找。 */
const _C0 = 299792458.0;
let _nkLibCache = null;

function _apiKey() {
  return localStorage.getItem("genie_v2_api_key") || "";
}

async function nkLibraryList() {
  if (_nkLibCache) return _nkLibCache;
  const r = await fetch("/api/tools/nk_library",
                        { headers: { "X-API-Key": _apiKey() } });
  if (!r.ok) throw new Error("nk_library " + r.status);
  const out = (await r.json()).models || [];
  _nkLibCache = out;
  return out;
}

/* 源频带 → 波长预填（米）：λ = c/f，取 f0±bw/2 对应区间；无源回退
 * 可见光 400–800nm。fit_range 语义 = 数据覆盖的拟合窗口。 */
function _prefitRangeFromSources() {
  const s = (cfg && cfg.source && cfg.source[0]) || null;
  const f = s && s.freq;
  if (f && f.center) {
    const c = f.center, bw = f.bw || 0;
    const lo = _C0 / (c + bw / 2), hi = bw > 0 ? _C0 / Math.max(c - bw / 2, 1) : lo;
    return [Math.min(lo, hi), Math.max(lo, hi)];
  }
  return [4e-7, 8e-7];
}

/* 三列文本 → (wl_m, n, k)；波长 >1e-4 视为 nm 自动换米（宽容启发式） */
function parseNkText(text) {
  const wl = [], n = [], k = [];
  for (const line of String(text).split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const parts = t.split(/[\s,;]+/).map(Number).filter((x) => !Number.isNaN(x));
    if (parts.length < 3) continue;
    let w = parts[0];
    if (w > 1e-4) w /= 1e9;                 /* nm → m */
    wl.push(w); n.push(parts[1]); k.push(parts[2]);
  }
  if (wl.length < 3) throw new Error("至少需要 3 行有效数据（波长 n k）");
  return [wl, n, k];
}

/* fit + 入 cfg（对话框与 e2e 共用的函数面）：
 * src = {library:"Au"} 或 {nkText:"…"}；range=[lo,hi] 米。
 * nk 载荷形状 = {wavelength_m, n, k}（端点契约，不是三元组数组）。 */
async function fitAndInsert(src, range, nDrude, nLorentz, maxfev) {
  const payload = {
    fit_range: range, n_drude: nDrude, n_lorentz: nLorentz,
    maxfev: maxfev || 5000,
  };
  if (src.library) payload.library = src.library;
  else {
    const [wl, n, k] = parseNkText(src.nkText);
    payload.nk = { wavelength_m: wl, n, k };
  }
  const r = await fetch("/api/tools/material_fit", { method: "POST",
    headers: { "Content-Type": "application/json",
               "X-API-Key": _apiKey() },
    body: JSON.stringify(payload) });
  const out = await r.json();
  if (!r.ok) throw new Error(out.detail || ("HTTP " + r.status));
  if (!out.material_node) {
    throw new Error("拟合未收敛（eps_inf≤0 或异常）——"
      + (out.warnings || []).join("；"));
  }
  if (!cfg) cfg = { domain: {}, mesh: {}, materials: [], geometry: [],
                    source: [], monitor: [], run: {} };
  if (!cfg.materials) cfg.materials = [];
  cfg.materials.push(out.material_node);
  onFormMutate();
  if (typeof selectObj === "function")
    selectObj("materials", cfg.materials.length - 1);
  return out;
}

/* ── 对话框（覆盖层，connDialog 同款形态）────────────────────── */
function openNkFitDialog() {
  const old = document.getElementById("v2-matlib");
  if (old) { old.remove(); return; }
  const wrap = document.createElement("div");
  wrap.id = "v2-matlib";
  wrap.style.cssText = "position:fixed;inset:0;z-index:10000;background:rgba(15,23,42,.45)"
    + ";display:flex;align-items:center;justify-content:center";
  const pre = _prefitRangeFromSources().map((x) => (x * 1e9).toFixed(0));
  wrap.innerHTML = `
    <div style="background:#fff;border-radius:10px;padding:20px 24px;width:460px;
                box-shadow:0 8px 30px rgba(0,0,0,.25);font-size:13px;color:#1f2328">
      <div style="font-weight:600;font-size:15px;margin-bottom:4px">库材料（nk → pole 拟合）</div>
      <div style="color:#57606a;margin-bottom:10px">pole 按当前波段现算入 cfg——数据在库，拟合随仿真波长走</div>
      <label style="display:block;margin:8px 0 4px">数据来源</label>
      <div style="display:flex;gap:12px;margin-bottom:4px">
        <label><input type="radio" name="ml-src" value="lib" checked> 内置库</label>
        <label><input type="radio" name="ml-src" value="raw"> 自备数据（三列文本）</label>
      </div>
      <select id="ml-lib" style="width:100%;box-sizing:border-box;padding:6px 8px;
              border:1px solid #d0d7de;border-radius:6px"></select>
      <textarea id="ml-raw" rows="5" placeholder="每行：波长 n k　（波长 nm 或 m 自动识别，# 开头为注释）"
        style="display:none;width:100%;box-sizing:border-box;padding:6px 8px;
               border:1px solid #d0d7de;border-radius:6px;font-family:monospace"></textarea>
      <div style="display:flex;gap:8px;margin-top:10px">
        <div style="flex:1"><label>fit 波段下限 (nm)</label>
          <input id="ml-lo" type="number" value="${pre[0]}"
            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d0d7de;border-radius:6px"></div>
        <div style="flex:1"><label>上限 (nm)</label>
          <input id="ml-hi" type="number" value="${pre[1]}"
            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d0d7de;border-radius:6px"></div>
        <div style="flex:1"><label>Drude 数</label>
          <input id="ml-nd" type="number" value="0" min="0"
            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d0d7de;border-radius:6px"></div>
        <div style="flex:1"><label>Lorentz 数</label>
          <input id="ml-nl" type="number" value="2" min="0"
            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d0d7de;border-radius:6px"></div>
      </div>
      <div id="ml-msg" style="margin-top:8px;color:#57606a;min-height:16px"></div>
      <div style="margin-top:10px;text-align:right;display:flex;gap:8px;justify-content:flex-end">
        <button id="ml-cancel" style="padding:6px 14px;border:1px solid #d0d7de;background:#fff;border-radius:6px;cursor:pointer">取消</button>
        <button id="ml-fit" style="padding:6px 14px;border:0;background:#1f6feb;color:#fff;border-radius:6px;cursor:pointer">拟合并加入材料</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const $ = (id) => wrap.querySelector("#" + id);
  const msg = (t, err) => { $("ml-msg").textContent = t;
                            $("ml-msg").style.color = err ? "#cf222e" : "#57606a"; };
  nkLibraryList().then((list) => {
    $("ml-lib").innerHTML = list.map((m) => `<option>${m}</option>`).join("");
  }).catch((e) => { $("ml-lib").innerHTML = "<option>（清单加载失败）</option>";
                    msg(String(e), true); });
  wrap.querySelectorAll('input[name="ml-src"]').forEach((rad) => {
    rad.addEventListener("change", () => {
      const raw = wrap.querySelector('input[name="ml-src"]:checked').value === "raw";
      $("ml-lib").style.display = raw ? "none" : "";
      $("ml-raw").style.display = raw ? "" : "none";
    });
  });
  $("ml-cancel").addEventListener("click", () => wrap.remove());
  wrap.addEventListener("click", (e) => { if (e.target === wrap) wrap.remove(); });
  $("ml-fit").addEventListener("click", async () => {
    const raw = wrap.querySelector('input[name="ml-src"]:checked').value === "raw";
    const lo = Number($("ml-lo").value) * 1e-9, hi = Number($("ml-hi").value) * 1e-9;
    if (!(lo > 0 && hi > lo)) { msg("波段非法（须 0 < 下限 < 上限，nm）", true); return; }
    /* 空串 → 默认；显式数值（含 0）原样透传（`Number(v)||默认` 会吞 0）。 */
    const num = (id, dft) => {
      const t = String($(id).value ?? "").trim();
      if (t === "") return dft;
      const v = Number(t);
      return Number.isFinite(v) ? v : dft;
    };
    $("ml-fit").disabled = true;
    msg("拟合中…");
    try {
      const src = raw ? { nkText: $("ml-raw").value }
                      : { library: $("ml-lib").value };
      const out = await fitAndInsert(src, [lo, hi],
                                     num("ml-nd", 0),
                                     num("ml-nl", 2),
                                     5000);
      const q = out.quality || {};
      msg(`✓ 已入 cfg：${out.n_poles} poles，rmse_n=${q.rmse_n ?? "—"}，`
        + `rmse_k=${q.rmse_k ?? "—"}`);
      setTimeout(() => wrap.remove(), 1200);
    } catch (e) {
      msg(String(e.message || e), true);
      $("ml-fit").disabled = false;
    }
  });
}

window.__matlib = { fitAndInsert, parseNkText, openNkFitDialog, nkLibraryList };
