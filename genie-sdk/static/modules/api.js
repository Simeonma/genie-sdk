import { toast } from "./state.js";
"use strict";

export const SESSION_USER_KEY = "genie_v2_session_user";  /* 当前模型属于谁 */

/** 退出登录：忘掉归属（模型由调用方先调 `genieNewModel()` 丢弃）。 */
export function genieForgetUser() {
  try { localStorage.removeItem(SESSION_USER_KEY); } catch {}
}

/** 失败原因 → 一句人话（唯一实现）。 */

/* 同一句话 6 秒内只提示一次（轮询/连点不刷屏） */
export let _apiNoticeAt = 0;
export let _apiNoticeMsg = "";

export function apiNotice(msg, kind) {
  const now = Date.now();
  if (msg === _apiNoticeMsg && now - _apiNoticeAt < 6000) return;
  _apiNoticeMsg = msg;
  _apiNoticeAt = now;
  try { toast(msg, kind || "err"); } catch { /* toast 未就绪不拦 */ }
}

/** 一次失败的统一处理：翻译 + 去重提示。返回那句人话（调用方可直接写终端）。 */
