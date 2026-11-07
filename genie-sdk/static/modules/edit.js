/* modules/edit.js — 表单编辑通道（M3b：改一个字段发一条增量，不发整份文档）
 * G5 拆分（2026-10-14）：自 app.js 机械切出；M3b 2026-10-22 换成 patch。
 *
 * 为什么要 diff 而不是直接全量推：模型真源在 worker，浏览器手里的 cfg 只是
 * **编辑缓冲**。以前每次敲字段都把整份文档推上去做整份替换，于是
 *   · 并发写（终端/另一标签）互相覆盖；
 *   · 自己的写入回声无法识别 → 整面板重渲染（丢焦点、吞键）；
 *   · 一次字段编辑要重建整棵对象树。
 * 现在所有改动先落在本地 cfg（几百个写入点一个都不用改），在**这一个收口点**
 * 与服务端已知状态求差，只发改动的路径。 */
"use strict";

/* 服务端已知状态（最近一次快照）。patch 的 diff 基准 = 它，不是上一次本地状态
 * ——否则连续编辑会diff 出错误增量。 */
let _serverCfg = null;

function onFormMutate() {
  /* 表单值即显示值——但只把**改过的路径**抄进 displayCfg，保住其余
   * resolved 槽（表达式/单位串的显示副本）。修前整份 displayCfg = cfg：
   * 一次没有实际变化的 blur 也会把 resolved 副本抹成 raw，"0.5[um]" 这类
   * 值在 3D 与参数 SI 列里就失效了（2026-10-30 真机实证）。 */
  if (!displayCfg || displayCfg === cfg) {
    displayCfg = cfg;
  } else {
    try {
      for (const op of diffCfg(_serverCfg || {}, cfg)) {
        setPath(displayCfg, op.path, op.value);
      }
    } catch (e) { displayCfg = cfg; }   /* 兜底：结构突变时退回整份 */
  }
  updateScene();
  renderTree();
  scheduleValidate();
  scheduleCfgPush();   /* 表单字段级修改 → worker 里的 f（terminal 战役：唯一可编辑） */
}

/** 两份模型求路径级差异 → [{path, value}]（value=null 表示删除）。
 *
 * 只有两种 op 形状：**标量路径赋值** 与 **整表替换**。结构操作（加/删/
 * 重排对象）天然就是"整个列表变了"，按表发既简单又不会因为下标移位而
 * 打到错的对象。
 */
function diffCfg(base, next) {
  const ops = [];
  (function walk(b, n, path) {
    if (n === null || typeof n !== "object") {
      if (JSON.stringify(b) !== JSON.stringify(n)) {
        if (path) ops.push({ path, value: n });
      }
      return;
    }
    if (Array.isArray(n)) {
      if (JSON.stringify(b) !== JSON.stringify(n)) {
        if (path) ops.push({ path, value: n });
      }
      return;
    }
    const bo = (b && typeof b === "object" && !Array.isArray(b)) ? b : {};
    for (const k of Object.keys(n)) {
      walk(bo[k], n[k], path ? path + "." + k : k);
    }
    for (const k of Object.keys(bo)) {
      if (!(k in n)) ops.push({ path: path ? path + "." + k : k, value: null });
    }
  })(base || {}, next || {}, "");
  return ops;
}

/* 表单 → worker 里的 f 推送（防抖 400ms；服务端权威，客户端只发增量） */
let _cfgPushTimer = null;
let _pendingExec = null;   /* busy 期提交的待执行命令（单槽排队）*/

async function pushCfg() {
  if (!cfg) return;
  const ops = diffCfg(_serverCfg || {}, cfg);
  if (!ops.length) return;                 /* 没改动 = 不发 */
  try {
    /* v2 连线层：路径 diff → 薄意图（host 生成 Python delta） */
    await GenieApp.formIntents(ops);
  } catch (e) {
    apiNotice("保存修改：" + (e && e.message ? e.message : e), "err");
  }
}
function scheduleCfgPush() {
  if (_cfgPushTimer) clearTimeout(_cfgPushTimer);
  _cfgPushTimer = setTimeout(async () => {
    _cfgPushTimer = null;
    await pushCfg();
  }, 400);
}
/* Run/脚本执行前 flush 未决的表单编辑（意图级：提交我刚刚的修改）。 */
async function flushPendingCfgPush() {
  if (!_cfgPushTimer) return;
  clearTimeout(_cfgPushTimer);
  _cfgPushTimer = null;
  await pushCfg();
}

/** 整份模型替换（导入 .gensim / 搬运）：服务端 replace + 本地视图刷新。
 *
 * 先把 `_serverCfg` 对齐成新模型，于是随后 onFormMutate 的 diff 为空
 * （不会再多发一坨 patch），只有这一次 replace。 */
async function replaceModel(model, source) {
  if (!model || typeof model !== "object") return false;
  cfg = model;
  /* 深拷贝：cfg 会被表单原地改，_serverCfg 必须是"服务端那份"的独立副本 */
  _serverCfg = (typeof structuredClone === "function")
    ? structuredClone(model) : JSON.parse(JSON.stringify(model));
  displayCfg = model;
  FormPanel.setCfg(cfg);
  updateScene();
  renderTree();
  renderInspector();
  scheduleValidate();
  /* v2：整份换模型 = worker 里 exec(fresh) 重建（不走本地替换端点）；
   * 这里的本地替换只服务显示层——需要真换模型时请在编辑器跑脚本。 */
  return true;
}

/* 模型级校验错误的去重记录（2026-10-21）：这类错误没有字段路径（loc 为空），
 * 挂不到输入框上，只能在面板层提示——按消息去重，避免每次防抖校验都弹一遍。 */
let _lastRootErr = "";

function scheduleValidate() {
  /* v2：校验只在 worker 里（提交时 `validate_for_ui`，错误走终端红行）。
   * v1 的 `/api/validate` 已退役；字段级即时校验（失焦 / 保存前）需要
   * 公开读动词 `f.validate()`，记在后端线，GUI 批先不接、也不再发请求。 */
}
