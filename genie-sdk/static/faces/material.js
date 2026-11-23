import { abandonWait, awaitEvent, exec } from "../core/bus.js";
import { _gmBtn, _gmModal, _gmPlot } from "../core/modal.js";
import { termExec } from "../modules/term.js";
/* faces/material.js — 材料面（2026-11-22 批 2a 自主 app.js 抽出；行为零变化）
 *
 * 库材料两段式：① 选材料（`genie._material_library()` → out(kind="matlib")）
 * ② 拟合窗口（`genie._fit_preview(...)` → out(kind="matfit")，只读不碰模型）；
 * 入模 = `add_material(type='library')` + `set_named(band…)` + `fit_material`。
 * 与其它面只经 exec/事件/toast 交互（L-16 的多轴预览与入模改进在本面内改）。
 */
"use strict";

  /* ── 库材料：按钮 = worker 里一句 genie 命令 ────────────────────
   * 材料库两段式（选材料 → 拟合参数），拟合跑在 worker 里
   * （genie._fit_preview → out(kind="matfit")，只读、不碰模型）；
   * 入模走 add_material(type='library') + fit_material（懒拟合随 run）。*/
  const FIT_LS = { win: "v2-fit-window", poles: "v2-fit-poles",
                   tol: "v2-fit-tolerance" };
  function fitPref(key, fallback) {
    try {
      const v = window.localStorage.getItem(FIT_LS[key]);
      return v === null || v === "" ? fallback : v;
    } catch (e) { return fallback; }          /* private mode / disabled storage */
  }
  function fitRemember(key, value) {
    try { window.localStorage.setItem(FIT_LS[key], String(value)); } catch (e) { /* ignore */ }
  }

  async function fitPreview(name, win, poles, passive, tol) {
    const wait = awaitEvent("matfit", (d) => !d || d.name === name, 120000);
    let code = "genie._fit_preview(" + JSON.stringify(name);
    const m = /^\s*(\d+(?:\.\d+)?)\s*[-~,，]\s*(\d+(?:\.\d+)?)\s*$/.exec(win || "");
    if (m) code += ", band=(" + (parseFloat(m[1]) * 1e-9) + ", " + (parseFloat(m[2]) * 1e-9) + ")";
    if (/^\s*\d+\s*$/.test(poles || "")) code += ", max_poles=" + parseInt(poles, 10);
    if (/^\s*\d*\.?\d+(?:[eE][-+]?\d+)?\s*$/.test(tol || "")) code += ", tolerance=" + parseFloat(tol);
    if (passive) code += ", make_passive=True";
    code += ")";
    try {
      await exec(code, { fresh: false, echo: false });
    } catch (e) {
      abandonWait();
      throw e;
    }
    return wait;
  }

  /* 第二段：拟合窗口（参数 + 曲线 + 加入材料） */
  function openFitWindow(name, win0, poles0, tol0) {
    const w = _gmModal("拟合 — " + name);
    const row = document.createElement("div");
    row.className = "gm-row";
    row.innerHTML = "<label>波长窗口 nm</label>";
    const win = document.createElement("input");
    win.type = "text"; win.value = win0 || "400-800"; win.style.width = "110px";
    const lb = document.createElement("label");
    lb.textContent = "极点上限"; lb.style.minWidth = "66px";
    const poles = document.createElement("input");
    poles.type = "number"; poles.min = "0"; poles.max = "30"; poles.value = poles0 || "10";
    const pLab = document.createElement("label");
    pLab.textContent = "强制无源";
    const passive = document.createElement("input");
    passive.type = "checkbox";
    row.appendChild(win); row.appendChild(lb); row.appendChild(poles);
    const tLab = document.createElement("label"); tLab.textContent = "tolerance"; tLab.style.minWidth = "66px"; const tol = document.createElement("input"); tol.type = "number"; tol.step = "0.001"; tol.min = "0"; tol.value = (tol0 || "0.1"); tol.style.width = "82px";
    row.appendChild(pLab); row.appendChild(passive); row.appendChild(tLab); row.appendChild(tol);
    const plotWrap = document.createElement("div");
    plotWrap.className = "gm-plot";
    plotWrap.textContent = "点击「拟合」…";
    const meta = document.createElement("div");
    meta.className = "gm-meta";
    const left = document.createElement("div");
    left.appendChild(row); left.appendChild(meta);
    const flex = document.createElement("div");
    flex.className = "gm-fit";
    flex.appendChild(left); flex.appendChild(plotWrap);
    w.body.appendChild(flex);

    const doFit = _gmBtn("拟合", true, async () => {
      doFit.disabled = true;
      plotWrap.textContent = "拟合中…";
      meta.textContent = "";
      try {
        const d = await fitPreview(name, win.value.trim(), poles.value.trim(),
                                   passive.checked, tol.value.trim());
        /* L-16：每唯一轴表一块图（iso 一块=三轴同值，单轴晶体两块，
         * 双轴三块）；摘要/warnings 按同样粒度逐块给。 */
        plotWrap.innerHTML = d.fits.map(_gmPlot).join("");
        meta.innerHTML = "";
        d.fits.forEach((f) => {
          const line = document.createElement("div");
          line.textContent = "[" + f.axes.join("/") + "] " + f.summary;
          meta.appendChild(line);
          if (f.warnings && f.warnings.length) {
            const wl2 = document.createElement("div");
            wl2.className = "gm-warn";
            wl2.textContent = "⚠ " + f.warnings.join(" · ");
            meta.appendChild(wl2);
          }
        });
        fitRemember("win", win.value.trim());
        fitRemember("poles", poles.value.trim());
        fitRemember("tol", tol.value.trim());
        apply.disabled = false;
      } catch (e) {
        plotWrap.textContent = "";
        meta.className = "gm-meta gm-err";
        meta.textContent = "✗ " + e.message;
      } finally {
        doFit.disabled = false;
      }
    });
    const apply = _gmBtn("加入材料", true, () => {
      let parts = [];
      const m = /^\s*(\d+(?:\.\d+)?)\s*[-~,，]\s*(\d+(?:\.\d+)?)\s*$/.exec(win.value || "");
      if (m) parts.push("band=(" + (parseFloat(m[1]) * 1e-9) + ", " + (parseFloat(m[2]) * 1e-9) + ")");
      if (/^\s*\d+\s*$/.test(poles.value || "")) parts.push("max_poles=" + parseInt(poles.value, 10));
      if (/^\s*\d*\.?\d+(?:[eE][-+]?\d+)?\s*$/.test(tol.value || "")) parts.push("tolerance=" + parseFloat(tol.value));
      if (passive.checked) parts.push("make_passive=True");
      termExec("genie.add_material(name=" + JSON.stringify(name) + ", type='library', library=" + JSON.stringify(name) + ")\n" +
            "genie.set_named('material.' + " + JSON.stringify(name) + ", " + parts.join(", ") + ")\n" +
            "genie.fit_material(" + JSON.stringify(name) + ")");
      w.close();
    });
    apply.disabled = true;
    w.btns.appendChild(_gmBtn("取消", false, w.close));
    w.btns.appendChild(doFit);
    w.btns.appendChild(apply);
    doFit.click();
  }

  /* 第一段：选材料 */
  function openLibraryDialog() {
    const w = _gmModal("新建材料（来自材料库）");
    const row = document.createElement("div");
    row.className = "gm-row";
    const lb = document.createElement("label");
    lb.textContent = "材料";
    const sel = document.createElement("select");
    sel.innerHTML = "<option value=''>载入中…</option>";
    row.appendChild(lb); row.appendChild(sel);
    w.body.appendChild(row);
    const go = _gmBtn("拟合", true, () => {
      const name = sel.value;
      if (!name) return;
      w.close();
      openFitWindow(name, fitPref("win", "400-800"), fitPref("poles", "10"), fitPref("tol", "0.1"));
    });
    go.disabled = true;
    w.btns.appendChild(_gmBtn("取消", false, w.close));
    w.btns.appendChild(go);
    const wait = awaitEvent("matlib", null, 20000);
    exec("genie._material_library()", { fresh: false, echo: false })
      .then(() => wait)
      .then((d) => {
        const names = (d && d.materials) || [];
        sel.innerHTML = "";
        names.forEach((n) => {
          const o = document.createElement("option");
          o.value = n; o.textContent = n;
          sel.appendChild(o);
        });
        go.disabled = names.length === 0;
        if (!names.length) sel.innerHTML = "<option value=''>（库为空）</option>";
      })
      .catch((e) => {
        sel.innerHTML = "<option value=''>库清单读取失败</option>";
        console.error(e);
      });
  }

export { fitPreview, openFitWindow, openLibraryDialog };
