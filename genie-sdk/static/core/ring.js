/* core/ring.js — 环投影（2026-11-23 从 app.js 下沉）
 *
 * 只做两件事：按 since 拉 /events；把一批事件归一化成
 * {lines, cfg, cfg_seq, job_progress, busy, epochChanged}。
 * **零显示层依赖**——拿到批次后交给注册的回调（boot 里注册），
 * 由回调喂终端/表单/树/3D。没有 origin/回声抑制（纯投影后不需要）。
 */
import { get } from "./bus.js";

const S = { epoch: null, since: 0, busy: false };
let _onBatch = null;

export function onBatch(fn) { _onBatch = fn; }

const nl = (t) => {
  const s = String(t == null ? "" : t);
  return /[\r\n]$/.test(s) ? s : s + "\n";
};

/* 一批事件 → 显示层要的形状。 */
export function snapshot(batch) {
  const evs = batch.events || [];
  const base = (batch.since || 0) - evs.length;
  const lines = [];
  let cfg = null, cfgSeq = (batch.since || 0), lastProgress = null;
  for (let i = 0; i < evs.length; i++) {
    const ev = evs[i], seq = base + i;
    if (ev.ev === "out") {
      const kind = ev.kind || null;
      /* L-35 拔线：meta/series/matfit/matlib/plot 都是已拔读桥的回程
       * 事件——静默丢弃（重接对应模块时恢复分发）。 */
      if (kind === "meta" || kind === "series" || kind === "matfit" ||
          kind === "matlib" || kind === "plot") {
        continue;
      }
      if (kind === "progress") {
        lastProgress = ev.data || {};
        lines.push({ seq, kind: "job-progress", text: ev.text || "",
                     data: lastProgress });
        continue;
      }
      if (kind === "plot") continue;
      if (kind === "error") {
        lines.push({ seq, kind: "stderr", text: nl(ev.text) });
        continue;
      }
      lines.push({ seq, kind: "stdout", text: nl(ev.text) });
    } else if (ev.ev === "done") {
      if (!ev.ok && ev.err) lines.push({ seq, kind: "stderr", text: nl(ev.err) });
    } else if (ev.ev === "cfg") {
      cfg = ev.cfg;
      cfgSeq = seq;
    }
  }
  return {
    lines,
    busy: !!batch.busy,
    cfg,
    cfg_seq: cfg ? cfgSeq : (batch.since || 0),
    job_progress: lastProgress,
  };
}

async function _pollOnce() {
  const batch = await get("/events?since=" + S.since);
  if (!batch) return S.busy;
  let epochChanged = false;
  if (S.epoch !== null && batch.epoch !== S.epoch) {
    S.epoch = batch.epoch;
    S.since = 0;
    epochChanged = true;
  }
  S.epoch = batch.epoch;
  S.since = batch.since;
  S.busy = !!batch.busy;
  const snap = snapshot(batch);
  if (_onBatch) _onBatch(snap, epochChanged);
  return S.busy;
}

/* 串行化：同一 since 被并发拉取会把同一批渲染两遍。 */
let _inFlight = null;
export function pollOnce() {
  if (_inFlight) return _inFlight;
  _inFlight = _pollOnce().finally(() => { _inFlight = null; });
  return _inFlight;
}

/* 写完成 → 立即读一拍（2026-11-28，站长拍）：表单写腿（bus.exec）只把
 * 命令交给 worker，结果在环里；这一拍把"写的结果"当场投影回来，回显
 * 延迟从 2s 空闲节拍降到一次往返。_inFlight 去重，连写只拍一次。
 * 2s 节拍保留——它只负责传播"不是我改的"改动（终端/另一窗口/agent）。 */
if (typeof window !== "undefined") {
  window.addEventListener("genie-exec-settled", () => {
    pollOnce().catch(() => {});
  });
}
