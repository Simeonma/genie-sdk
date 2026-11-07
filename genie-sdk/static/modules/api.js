/* modules/api.js — 身份/凭据 + 统一的失败措辞（M2/O2；2026-11-01 删 session_id）
 *
 * 三件事在这里，且只在这里：
 *  1. **凭据与"模型属于谁"分开存**：token（能不能提交）/ 用户名（换账号 =
 *     丢模型）。**模型只有一份**——没有 session_id 可存、可传（一个进程
 *     一份模型；新模型 = 调一次 `/api/model/new`）。
 *  2. **一个失败出口**：`apiFailText()` 把 401/503/502 翻成人话，
 *     `apiNotice()` 做去重提示。旧代码有 6 处各自处理"执行进程不可用"，
 *     用户能看到 4 种不同表现（静默/一行红字/503/兜底）。
 *  3. **请求头统一注入**：X-API-Key 由 boot.js 的 fetch 包装统一加，
 *     调用点不再手写（手写就会漏）。
 */
"use strict";

const SESSION_USER_KEY = "genie_v2_session_user";  /* 当前模型属于谁 */
const SESSION_TOKEN_KEY = "genie_v2_api_key";      /* 能不能提交 */

function genieToken() {
  try { return localStorage.getItem(SESSION_TOKEN_KEY) || ""; } catch { return ""; }
}

/**
 * 登录成功后记录"模型属于谁"，返回**是否换了账号**。
 *
 * 规则：同账号再登录（改密码/token 过期重登）→ 保留模型；换账号 → 丢掉
 * 浏览器的"模型属于谁"记录（整份换模型走 worker 的 `f._open`，不再有
 * 服务端 `model/new` 动作）。
 */
function genieClaimUser(username) {
  let owner = "";
  try { owner = localStorage.getItem(SESSION_USER_KEY) || ""; } catch {}
  const changed = !!(owner && username && owner !== username);
  try { if (username) localStorage.setItem(SESSION_USER_KEY, username); } catch {}
  return changed;
}

/** 退出登录：忘掉归属（模型由调用方先调 `genieNewModel()` 丢弃）。 */
function genieForgetUser() {
  try { localStorage.removeItem(SESSION_USER_KEY); } catch {}
}

/** token 失效（401/吊销/过期）：只清凭据，**保留模型**——
 *  重新登录同一个账号应当回到同一个模型。 */
function genieDropToken() {
  try {
    localStorage.removeItem(SESSION_TOKEN_KEY);
    localStorage.removeItem("genie_v2_username");
  } catch {}
}

/** 失败原因 → 一句人话（唯一实现）。 */
function apiFailText(r, j) {
  const d = (j && (j.detail || j.message)) || "";
  if (r && r.status === 401) {
    return "登录已失效或用完了——请重新登录（右上角账号钮）";
  }
  if (r && r.status === 503) {
    return "后台执行进程不可用（本机内核没起来或已退出）——重启 Studio 后重试";
  }
  if (r && r.status === 502) {
    return d || "上游服务不可达";
  }
  if (d) return d;
  return r ? ("HTTP " + r.status) : "网络错误";
}

/* 同一句话 6 秒内只提示一次（轮询/连点不刷屏） */
let _apiNoticeAt = 0;
let _apiNoticeMsg = "";

function apiNotice(msg, kind) {
  const now = Date.now();
  if (msg === _apiNoticeMsg && now - _apiNoticeAt < 6000) return;
  _apiNoticeMsg = msg;
  _apiNoticeAt = now;
  try { toast(msg, kind || "err"); } catch { /* toast 未就绪不拦 */ }
}

/** 一次失败的统一处理：翻译 + 去重提示。返回那句人话（调用方可直接写终端）。 */
function apiFailed(where, r, j) {
  const text = apiFailText(r, j);
  if (r && r.status === 401) {
    /* 凭据死了：清 token（保留 session），按钮回「Connect」 */
    genieDropToken();
    const b = document.getElementById("login-toggle");
    if (b) { b.textContent = "Connect"; b.classList.remove("on"); b.classList.add("err"); }
  }
  apiNotice((where ? where + "：" : "") + text, "err");
  return text;
}
