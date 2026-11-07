/* modules/workspace.js — FILES 页签（v2 GUI 收口）
 *
 * v2 的文档住在 `.gensim` 里：打开 = File → Open（`f._open`），保存 =
 * `f._save()`。v1 的「工作区文件面板」（列/看/传/删/载入脚本）指向的是
 * 已退役的 `/api/workspace/*` 与 `/api/scripts/read`，v2 不做工作区文件
 * 管理——这里只留一句明说，并把入口控件藏起来，不再发任何请求。
 */
"use strict";

function loadWorkspaceFiles() {
  const tb = $("files-tbody");
  if (tb) tb.innerHTML = "";
  const empty = $("files-empty");
  if (empty) {
    empty.classList.remove("hidden");
    empty.textContent = "文档用 File → Open 打开（f._open）；"
      + "Studio 不做工作区文件管理，日常文件请用系统文件管理器。";
  }
  ["files-upload", "files-refresh"].forEach((id) => {
    const b = $(id);
    if (b) b.classList.add("hidden");
  });
  const pv = $("files-preview");
  if (pv) pv.classList.add("hidden");
}
