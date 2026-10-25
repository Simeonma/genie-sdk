/* live.js — SDK → Studio 实时桥（v2 外部会话通道的消费端）。
 *
 * 数据链：SDK 进程 POST /api/drafts {label, script} → 本桥轮询
 * GET /__drafts?since=seq → 采纳最新脚本文本 → runScript()（GUI 经会话
 * /api/exec 重新执行派生模型——通道里没有 cfg，脚本是唯一本体）。
 *
 * 采纳纪律（v1 dsh_bridge 三条教训全带上）：
 *  · 陈旧防劫持：只采纳【页面加载之后】新到的草稿（LOADED_AT-5s 容忍
 *    时钟差）；旧草稿只推进 since 不采纳（否则登录瞬间被历史脚本覆盖）。
 *  · 焦点保护：用户正在输入控件（INPUT/SELECT/TEXTAREA）里打字时不
 *    冲刷，跳过本条（下一条新草稿再来）。
 *  · 端点 401/404（旧服务/未部署）→ 首探失败永久休眠，不打扰。
 *
 * 轮询纪律（2026-10 轮询风暴修复）：
 *  · 退避：连续空轮询把间隔从 1s 逐级拉到 8s 上限；一旦有更新立刻
 *    回落 1s（实时性只在真有变化时被需要）。
 *  · 页面隐藏时停摆（document.hidden 直接跳过本轮，不占网络）。
 *  · 单标签页守卫：多标签同时开着时只有一个真轮询（BroadcastChannel
 *    抢主租约，5s 心跳续约；抢不到就静默待命，页面照样能用）。
 */
(function () {
  let since = 0;
  let timer = null;
  let idleRounds = 0;
  const LOADED_AT = Date.now() / 1000 - 5;
  const BASE_MS = 1000, MAX_MS = 8000;

  /* ── 单标签页租约（BroadcastChannel；不支持则退化为人人轮询）── */
  const TAB_ID = Math.random().toString(36).slice(2);
  const bc = (typeof BroadcastChannel !== "undefined")
    ? new BroadcastChannel("genie-v2-live") : null;
  let leader = null, leaderAt = 0;
  if (bc) {
    bc.onmessage = (ev) => {
      const m = ev.data || {};
      if (m.type === "claim" && m.id !== TAB_ID) {
        /* 已有更新的主：让位（更新的时间戳赢） */
        if ((m.at || 0) > leaderAt) { leader = m.id; leaderAt = m.at; }
      }
      if (m.type === "yield" && m.id === leader) { leader = null; leaderAt = 0; }
    };
  }
  function iAmLeader() {
    if (!bc) return true;                     // 无 BroadcastChannel：退化
    const now = Date.now();
    if (leader === TAB_ID) { leaderAt = now; return true; }
    if (leader && now - leaderAt < 5000) return false;   // 他主健在
    leader = TAB_ID; leaderAt = now;          // 抢主
    bc.postMessage({ type: "claim", id: TAB_ID, at: now });
    return true;
  }
  window.addEventListener("pagehide", () => {
    if (bc && leader === TAB_ID) bc.postMessage({ type: "yield", id: TAB_ID });
  });

  function userIsTyping() {
    const el = document.activeElement;
    return !!el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
  }

  function adopt(item) {
    if ((item.ts || 0) < LOADED_AT) return;      /* 陈旧：只推进不采纳 */
    if (userIsTyping()) return;                  /* 输入中：不冲刷 */
    const ed = document.getElementById("code-editor");
    if (!ed || typeof runScript !== "function") return;
    ed.value = item.script;
    const label = item.label || "sdk";
    if (typeof setStatus === "function") {
      setStatus("␢ SDK 脚本已同步 (" + label + ")——预览中…", "ok");
    }
    runScript();
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(pull, ms);
  }

  function pull() {
    if (document.hidden) { schedule(MAX_MS); return; }   /* 隐藏页：躺平 */
    if (!iAmLeader()) { schedule(BASE_MS * 2); return; } /* 非主标签：待命 */
    fetch("/__drafts?since=" + since)
      .then(r => {
        if (!r.ok) { stop(); return null; }      /* 404/401：无通道，休眠 */
        return r.json();
      })
      .then(items => {
        if (items === null) return;
        const arr = Array.isArray(items) ? items : [];
        for (const it of arr) {
          since = Math.max(since, it.seq || 0);
          if (it.script && typeof it.script === "string") adopt(it);
        }
        /* 退避：有更新回落 1s；空轮询逐级翻倍到 8s 上限 */
        idleRounds = arr.length ? 0 : idleRounds + 1;
        schedule(Math.min(BASE_MS * Math.pow(2, idleRounds), MAX_MS));
      })
      .catch(() => schedule(MAX_MS));            /* 网络抖动：退避重试 */
  }

  function stop() { if (timer) { clearTimeout(timer); timer = null; } }

  /* 未登录：草稿通道是执行面的邻居（服务端同闸），问也是 401 —— 不问。 */
  if (!(typeof genieToken === "function" ? genieToken()
        : localStorage.getItem("genie_v2_api_key"))) return;

  fetch("/__drafts?since=0")
    .then(r => { if (r.ok) { pull(); } })
    .catch(() => {});
})();
