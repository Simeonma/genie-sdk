import { $ } from "./util.js";
"use strict";

export function loadWorkspaceFiles() {
  const tb = $("files-tbody");
  if (tb) tb.innerHTML = "";
  const empty = $("files-empty");
  if (empty) {
    empty.classList.remove("hidden");
    empty.textContent = "文档用 File → Open 打开（genie.open）；"
      + "Studio 不做工作区文件管理，日常文件请用系统文件管理器。";
  }
  ["files-upload", "files-refresh"].forEach((id) => {
    const b = $(id);
    if (b) b.classList.add("hidden");
  });
  const pv = $("files-preview");
  if (pv) pv.classList.add("hidden");
}
