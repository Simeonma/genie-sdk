/* modules/matlib.js — 库材料拟合对话框（G20 材料分类学重构 2026-10-16；
 * R38 Q1=A/Q3=A 重做：拟合预览-确认流 + n/k 对照图 + 显式单位）
 *
 * 第一性原则：内核只吃 pole，cfg 里唯一的合法材料实体 = wire 材料节点。
 * nk 数据（内置 37 库 / 用户自备三列）不是材料，是拟合的原料——pole
 * 永远是设计期按波长现算的暂态产物（同一份 Au，可见光与紫外拟出不同
 * 的 pole）。本对话框 = "选数据 → 定波段 → 拟合预览（对照图）→ 确认
 * 入 cfg" 的唯一 GUI 通道。
 *
 * R38 流程（Q1=A 裁决）：拟合成功不再自动关对话框——quality 的逐波长
 * 对照表（wavelength_m/n_data/n_fit/k_data/k_fit）就地画成两张
 * data 点 + fit 曲线对照图，用户看过拟合质量再点"确认加入"（charter
 * R9：判断极点数/波段够不够必须看到曲线）。单位面（Q3=A 裁决）：
 * 自备数据波长单位显式单选 nm/µm/m——修前 w>1e-4 当 nm 的启发式把
 * µm 表（refractiveindex.info 导出惯用格式）静默拟出 1000× 频率错
 * 材料；THz 后端明确支持（拟合搜索盒注释自证），GUI 不能只认 nm/m。
 *
 * 端点：/api/tools/nk_library、/api/tools/material_fit——G19b 起为
 * Studio 本地实现（studio/server.py，genie.core.material + 本地
 * scipy）；X-API-Key = localStorage 优先。经典 script 按序加载，
 * 跨模块引用为运行期全局查找。 */
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

/* 源频带 → 波长预填（米）：λ = c/f，取 f±bw/2 对应区间；无源回退
 * 可见光 400–800nm。fit_range 语义 = 数据覆盖的拟合窗口。
 * R38 修复：源线格式的 freq/bandwidth 是**标量**（_wire _TemporalMixin），
 * {center,bw} 是监视器形状——此前读 f.center 恒 undefined，任何真实源
 * 都静默回退 400–800nm，1550nm 用户开对话框就拟错波段。 */
function _prefitRangeFromSources() {
  const s = (cfg && cfg.source && cfg.source[0]) || null;
  const f = s && s.freq;
  if (typeof f === "number" && f > 0) {
    const bw = (typeof s.bandwidth === "number" && s.bandwidth > 0)
      ? s.bandwidth : 0;
    const lo = _C0 / (f + bw / 2), hi = bw > 0 ? _C0 / Math.max(f - bw / 2, 1) : lo;
    return [Math.min(lo, hi), Math.max(lo, hi)];
  }
  return [4e-7, 8e-7];
}

/* 三列文本 → (wl_m, n, k)。R38（Q3=A）：波长单位由调用方显式给定
 * （"nm"|"um"|"m"），不再做 w>1e-4 猜 nm 的启发式——µm 表会静默拟出
 * 1000× 频率错材料且 rmse 好看。坏行（非数 token/列数不足）计数并
 * 汇报，不再 filter 静默蒸发（列移位之源）。 */
function parseNkText(text, unit) {
  const scale = unit === "nm" ? 1e-9 : unit === "um" ? 1e-6 : 1;
  const wl = [], n = [], k = [];
  let badRows = 0;
  for (const line of String(text).split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const toks = t.split(/[\s,;]+/);
    if (toks.length < 3 || toks.slice(0, 3).some((x) => !Number.isFinite(Number(x)))) {
      badRows += 1;
      continue;
    }
    wl.push(Number(toks[0]) * scale);
    n.push(Number(toks[1]));
    k.push(Number(toks[2]));
  }
  if (wl.length < 3) {
    throw new Error("至少需要 3 行有效数据（波长 n k）——已解析 " + wl.length
      + " 行，跳过坏行 " + badRows + " 行（列数不足或含非数值）");
  }
  if (badRows > 0) {
    /* 坏行不拦拟合（窗口外/可修），但必须说清，不许静默蒸发 */
    console.warn("matlib: skipped " + badRows + " malformed data line(s)");
  }
  return [wl, n, k, badRows];
}

/* 拟合（不落 cfg）：src = {library:"Au"} 或 {nkText:"…", unit:"nm"}；
 * range=[lo,hi] 米。返回 {out, badRows}。 */
async function runFit(src, range, nDrude, nLorentz, maxfev) {
  const payload = {
    fit_range: range, n_drude: nDrude, n_lorentz: nLorentz,
    maxfev: maxfev || 5000,
  };
  let badRows = 0;
  if (src.library) {
    payload.library = src.library;
    /* Q2 裁决（R38）：库拟合产物默认名 = 库名（与 SDK 糖 add_from_library
     * 一致）——修前一律 "fitted"，Au/Ag 重名互覆（材料重名=后者覆盖
     * 先写），第一个金属求解时静默失效。自备文本仍用缺省名。 */
    payload.name = src.library;
  } else {
    const [wl, n, k, bad] = parseNkText(src.nkText, src.unit || "nm");
    badRows = bad;
    payload.nk = { wavelength_m: wl, n, k };
  }
  const r = await fetch("/api/tools/material_fit", { method: "POST",
    headers: { "Content-Type": "application/json",
               "X-API-Key": _apiKey() },
    body: JSON.stringify(payload) });
  /* R38 修复：先判 ok 再解析——服务端 4xx/5xx 的 body 可能是纯文本
   * （非 JSON），旧的 `await r.json()` 前置会先炸 SyntaxError
   * （"Unexpected token 'I'"），把端点给的可操作 detail 全吞掉。 */
  if (!r.ok) {
    let detail = "HTTP " + r.status;
    try {
      const j = await r.json();
      if (j && j.detail) detail = String(j.detail);
    } catch (e) { /* 纯文本 body：保留 HTTP 状态码 */ }
    throw new Error(detail);
  }
  const out = await r.json();
  if (!out.material_node) {
    throw new Error("拟合未收敛（eps_inf≤0 或异常）——"
      + (out.warnings || []).join("；"));
  }
  return { out, badRows };
}

/* 拟合结果落 cfg（确认后才调；对话框与 e2e 共用的函数面）。 */
function insertFit(out) {
  if (!cfg) cfg = { domain: {}, mesh: {}, materials: [], geometry: [],
                    source: [], monitor: [], run: {} };
  if (!cfg.materials) cfg.materials = [];
  cfg.materials.push(out.material_node);
  onFormMutate();
  if (typeof selectObj === "function")
    selectObj("materials", cfg.materials.length - 1);
  return out;
}

/* 兼容面（e2e/console）：一步拟合+入 cfg。 */
async function fitAndInsert(src, range, nDrude, nLorentz, maxfev) {
  const { out } = await runFit(src, range, nDrude, nLorentz, maxfev);
  return insertFit(out);
}

/* ── 拟合对照图（Q1=A）：data 点 + fit 曲线两张小图（n 上 k 下，
 * 共享波长横轴 nm）。轻量自绘，不耦合 result.js 的结果面板设施。── */
function _fitChart(canvas, wlNm, data, fit, yLabel) {
  const W = canvas.clientWidth || 400, H = canvas.clientHeight || 110;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const pad = { l: 34, r: 6, t: 6, b: 16 };
  const x0 = wlNm[0], x1 = wlNm[wlNm.length - 1];
  const ys = data.concat(fit);
  let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  if (y1 - y0 < 1e-12) { y0 -= 0.5; y1 += 0.5; }
  const px = (x) => pad.l + (x - x0) / (x1 - x0 || 1) * (W - pad.l - pad.r);
  const py = (y) => pad.t + (1 - (y - y0) / (y1 - y0)) * (H - pad.t - pad.b);
  g.strokeStyle = "#d0d7de"; g.lineWidth = 1;
  g.beginPath(); g.moveTo(pad.l, pad.t); g.lineTo(pad.l, H - pad.b);
  g.lineTo(W - pad.r, H - pad.b); g.stroke();
  g.fillStyle = "#57606a"; g.font = "10px sans-serif";
  g.fillText(y1.toPrecision(3), 2, pad.t + 8);
  g.fillText(y0.toPrecision(3), 2, H - pad.b);
  g.fillText(x0 + "nm", pad.l, H - 3);
  g.fillText(x1.toPrecision(3) + "nm", W - pad.r - 40, H - 3);
  g.fillText(yLabel, W - pad.r - 26, pad.t + 8);
  g.strokeStyle = "#cf222e"; g.lineWidth = 1.5;   /* fit 曲线 */
  g.beginPath();
  fit.forEach((v, i) => (i ? g.lineTo(px(wlNm[i]), py(v))
                           : g.moveTo(px(wlNm[i]), py(v))));
  g.stroke();
  g.fillStyle = "#1f6feb";                          /* data 点 */
  data.forEach((v, i) => {
    g.beginPath(); g.arc(px(wlNm[i]), py(v), 2, 0, 6.283); g.fill();
  });
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
      <div style="color:#57606a;margin-bottom:10px">pole 按当前波段现算——拟合预览对照图，确认后入 cfg</div>
      <label style="display:block;margin:8px 0 4px">数据来源</label>
      <div style="display:flex;gap:12px;margin-bottom:4px">
        <label><input type="radio" name="ml-src" value="lib" checked> 内置库</label>
        <label><input type="radio" name="ml-src" value="raw"> 自备数据（三列文本）</label>
      </div>
      <select id="ml-lib" style="width:100%;box-sizing:border-box;padding:6px 8px;
              border:1px solid #d0d7de;border-radius:6px"></select>
      <textarea id="ml-raw" rows="5" placeholder="每行：波长 n k（# 开头为注释）"
        style="display:none;width:100%;box-sizing:border-box;padding:6px 8px;
               border:1px solid #d0d7de;border-radius:6px;font-family:monospace"></textarea>
      <div id="ml-unit-row" style="display:none;margin-top:6px">
        <label style="display:block;margin:0 0 4px">波长单位（Q3：显式选择，不猜）</label>
        <div style="display:flex;gap:12px">
          <label><input type="radio" name="ml-unit" value="nm" checked> nm</label>
          <label><input type="radio" name="ml-unit" value="um"> µm</label>
          <label><input type="radio" name="ml-unit" value="m"> m</label>
        </div>
      </div>
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
      <div id="ml-quality" style="display:none;margin-top:10px">
        <canvas id="ml-chart-n" style="width:100%;height:110px;display:block"></canvas>
        <canvas id="ml-chart-k" style="width:100%;height:110px;display:block;margin-top:4px"></canvas>
        <div id="ml-qmsg" style="color:#57606a;margin-top:4px;min-height:16px"></div>
      </div>
      <div id="ml-msg" style="margin-top:8px;color:#57606a;min-height:16px"></div>
      <div style="margin-top:10px;text-align:right;display:flex;gap:8px;justify-content:flex-end">
        <button id="ml-cancel" style="padding:6px 14px;border:1px solid #d0d7de;background:#fff;border-radius:6px;cursor:pointer">取消</button>
        <button id="ml-fit" style="padding:6px 14px;border:0;background:#1f6feb;color:#fff;border-radius:6px;cursor:pointer">拟合预览</button>
        <button id="ml-insert" style="display:none;padding:6px 14px;border:0;background:#1a7f37;color:#fff;border-radius:6px;cursor:pointer">确认加入材料</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const $ = (id) => wrap.querySelector("#" + id);
  const msg = (t, err) => { $("ml-msg").textContent = t;
                            $("ml-msg").style.color = err ? "#cf222e" : "#57606a"; };
  let pending = null;   /* 最近一次成功拟合的结果（确认后才入 cfg） */
  nkLibraryList().then((list) => {
    $("ml-lib").innerHTML = list.map((m) => `<option>${m}</option>`).join("");
  }).catch((e) => { $("ml-lib").innerHTML = "<option>（清单加载失败）</option>";
                    msg(String(e), true); });
  wrap.querySelectorAll('input[name="ml-src"]').forEach((rad) => {
    rad.addEventListener("change", () => {
      const raw = wrap.querySelector('input[name="ml-src"]:checked').value === "raw";
      $("ml-lib").style.display = raw ? "none" : "";
      $("ml-raw").style.display = raw ? "" : "none";
      $("ml-unit-row").style.display = raw ? "" : "none";
    });
  });
  $("ml-cancel").addEventListener("click", () => wrap.remove());
  wrap.addEventListener("click", (e) => { if (e.target === wrap) wrap.remove(); });
  $("ml-fit").addEventListener("click", async () => {
    const raw = wrap.querySelector('input[name="ml-src"]:checked').value === "raw";
    const unit = wrap.querySelector('input[name="ml-unit"]:checked')
      ? wrap.querySelector('input[name="ml-unit"]:checked').value : "nm";
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
    $("ml-insert").style.display = "none";
    msg("拟合中…");
    try {
      const src = raw ? { nkText: $("ml-raw").value, unit }
                      : { library: $("ml-lib").value };
      const { out, badRows } = await runFit(src, [lo, hi],
                                            num("ml-nd", 0),
                                            num("ml-nl", 2),
                                            5000);
      pending = out;
      const q = out.quality || {};
      /* Q1=A：对照图 + rmse 行，用户过目后确认（不再 1.2s 自动关） */
      const wlNm = (q.wavelength_m || []).map((x) => x * 1e9);
      if (wlNm.length) {
        $("ml-quality").style.display = "";
        _fitChart($("ml-chart-n"), wlNm, q.n_data || [], q.n_fit || [], "n");
        _fitChart($("ml-chart-k"), wlNm, q.k_data || [], q.k_fit || [], "k");
        const extra = badRows > 0 ? `，跳过坏行 ${badRows} 行` : "";
        $("ml-qmsg").textContent =
          `${out.n_poles} poles，rmse_n=${q.rmse_n ?? "—"}，`
          + `rmse_k=${q.rmse_k ?? "—"}${extra}（蓝点=数据，红线=拟合）`;
      }
      msg("✓ 拟合完成——对照图确认后加入 cfg");
      $("ml-insert").style.display = "";
    } catch (e) {
      msg(String(e.message || e), true);
    } finally {
      $("ml-fit").disabled = false;
    }
  });
  $("ml-insert").addEventListener("click", () => {
    if (!pending) return;
    try {
      insertFit(pending);
      wrap.remove();
    } catch (e) {
      msg(String(e.message || e), true);
    }
  });
}

window.__matlib = { fitAndInsert, runFit, insertFit, parseNkText,
                    openNkFitDialog, nkLibraryList, _fitChart };
