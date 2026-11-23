import { post } from "../core/bus.js";
import { cfg, setDocDirty, toast } from "./state.js";
/* modules/edit.js — GUI 编辑通道（2026-11-23 终态：单一真相 = worker 里的模型）
 *
 * 第一性：GUI 只是**投影 + 命令发送器**——不持可编辑副本、不做状态 diff、
 * 不反推动作。每个动作/字段编辑 → 一条明确的意图 → host 生成一行 Python →
 * worker 改模型 → 快照回来重画。
 *
 * 被删掉的旧机器（它们只为"两份模型同步"服务，且是 bug 温床）：
 *   diffCfg / pushCfg / scheduleCfgPush / _serverCfg 基线 /
 *   intentsFromOps 整表反推 / replaceModel / _pendingExec。
 * 保留的映射只有一张：**字段路径 → 意图**（GUI 知道路径，host 知道命令）。
 */
"use strict";

/* cfg 列表键 → /form 的 collection 词 */
export const COLL_OF = {
  materials: "mat", geometry: "geom", source: "src", monitor: "mon",
};

/* 一条意图 → host（/form）。失败即报（不静默）。 */
export async function sendIntent(intent) {
  try {
    await post("/form", intent);
    setDocDirty(true);                     /* 模型即将变；快照落地后由 File 面清 */
    return true;
  } catch (e) {
    toast("提交失败：" + (e && e.message ? e.message : e), "err");
    return false;
  }
}

export async function sendIntents(list) {
  let ok = true;
  for (const it of list || []) ok = (await sendIntent(it)) && ok;
  return ok;
}

/* 字段路径 → 一条 set 意图。身份（对象 name/type）只**读** cfg，不写。 */
export function fieldIntent(path, value) {
  const parts = String(path || "").split(".");
  const head = parts[0];
  /* 数组下标路径（domain.x_range.0 / geometry.3.x_range.1）：wire 字段是
   * 整数组 → 从只读快照取当前数组、替换该位、按整字段发。 */
  const last = parts[parts.length - 1];
  const isIdx = /^\d+$/.test(last);
  const arrField = (base, fieldPath, idx) => {
    const cur = (base && Array.isArray(base[fieldPath])) ? base[fieldPath].slice() : [];
    while (cur.length < Math.max(idx + 1, 2)) cur.push(0);
    cur[idx] = value;
    return cur;
  };
  if (head === "parameters") {
    const name = parts[1];
    if (!name) return null;
    if (value === null) return { op: "param", name, remove: true };
    return { op: "param", name, value,
             add: !(cfg && cfg.parameters && name in cfg.parameters) };
  }
  if (head === "domain") {
    if (isIdx) {
      const fieldPath = parts.slice(1, -1).join(".");
      return { collection: "domain", id: null, field: fieldPath,
               value: arrField(cfg && cfg.domain, fieldPath, Number(last)) };
    }
    return { collection: "domain", id: null,
             field: parts.slice(1).join("."), value };
  }
  const coll = COLL_OF[head];
  if (!coll || parts.length < 3) return null;   /* 身份/整表不在这里发 */
  const item = ((cfg && cfg[head]) || [])[Number(parts[1])];
  if (!item || !item.name) return null;
  if (isIdx) {
    const fieldPath = parts.slice(2, -1).join(".");
    return { collection: coll, id: null, name: item.name, kind: item.type,
             field: fieldPath, value: arrField(item, fieldPath, Number(last)) };
  }
  return { collection: coll, id: null, name: item.name, kind: item.type,
           field: parts.slice(2).join("."), value };
}

export function submitField(path, value) {
  const it = fieldIntent(path, value);
  if (!it) return Promise.resolve(false);
  return sendIntent(it);
}

/* 动作点（加对象）：身份 + 全部字段，按序发。 */
export function addIntents(kind, name, type, fields) {
  const coll = { mat: "mat", geo: "geom", src: "src", mon: "mon",
                 materials: "mat", geometry: "geom", source: "src",
                 monitor: "mon" }[kind];
  const out = [{ op: "add", collection: coll, kind: type, name }];
  for (const [k, v] of Object.entries(fields || {})) {
    if (k === "name" || k === "type" || k === "id") continue;
    out.push({ collection: coll, id: null, name, kind: type, field: k, value: v });
  }
  return out;
}

export function removeIntent(kind, name) {
  const coll = { mat: "mat", geo: "geom", src: "src", mon: "mon",
                 materials: "mat", geometry: "geom", source: "src",
                 monitor: "mon" }[kind];
  return { op: "remove", collection: coll, name };
}

/* 校验面：L-7 挂账（worker 内部校验尚未接线）——保留空实现，调用点不变，
 * 接线时只改这一处。 */
export function scheduleValidate() {}

/* busy 期提交的待执行命令（单槽排队；term.js 读写，权限在本模块） */
export let _pendingExec = null;
export function setPendingExec(v) { _pendingExec = v; }
