/* modules/script.js — 脚本面板（2026-10-14 站长裁决精简：Run / Load /
 * Download 三钮制——无保存/改名/复制/选中运行；脚本由 worker 在每次
 * 执行时自动存档到服务端系统空间（用户不可见，同模型快照待遇）。
 * G5 拆分自 app.js；经典 script 按序加载。
 *
 * 2026-10-21 追加：Python 语法高亮（只读镜像层）。
 * 为什么不改编辑器本身：editable 面必须仍是 <textarea>（IME / 撤销栈 /
 * 原生选中 / 无障碍），所以高亮走「镜像 pre」——textarea 文字透明只留
 * 光标与选区，颜色由底下同字号同 padding 的 <pre> 提供，滚动由 boot.js
 * 的 gutter 钩子同步。零 innerHTML：token 逐个 append 文本/span 节点。 */
async function runScript() {
  const ta = $("code-editor");
  const script = ta ? ta.value : "";
  if (!script.trim()) { toast("Empty script — write something first", "warn"); return; }
  try { localStorage.setItem("genie_v2_script", script); } catch { /* ignore */ }
  /* mode=build：脚本运行 = 构造一个新模型，成功才替换当前模型（失败
   * 时当前模型一个字节都不动）。旧名 reset 已由服务端兼容一版。 */
  await termExec(script, { mode: "build" });
}

/* ══ Python 词法着色（轻量、无依赖）════════════════════════════════
 * 捕获组顺序 = 着色优先级：注释 → 字符串 → 关键字 → 数字 → 装饰器/函数名。
 * 只在"行内"匹配，跨行三引号另用 [\s\S] 兜住（pre 保留换行，无需分行）。 */
const PY_HL_RE = new RegExp([
  "(#[^\\n]*)",                                            // 1 comment
  "('''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"|'(?:\\\\.|[^'\\\\\\n])*'|\"(?:\\\\.|[^\"\\\\\\n])*\")", // 2 string
  "\\b(import|from|as|def|return|if|elif|else|for|while|in|not|and|or|is|lambda|class|with|try|except|finally|raise|yield|pass|break|continue|global|assert|del|True|False|None)\\b", // 3 keyword
  "\\b(\\d+\\.?\\d*(?:[eE][-+]?\\d+)?|\\.\\d+(?:[eE][-+]?\\d+)?)\\b", // 4 number
  "(@[A-Za-z_][\\w.]*)",                                   // 5 decorator
].join("|"), "g");

/* 把源码渲染进 <pre> 镜像层（DOM 节点拼装，零 innerHTML） */
function renderPythonHighlight(pre, code) {
  if (!pre) return;
  const frag = document.createDocumentFragment();
  let last = 0;
  PY_HL_RE.lastIndex = 0;
  let m;
  while ((m = PY_HL_RE.exec(code)) !== null) {
    if (m.index > last) frag.appendChild(document.createTextNode(code.slice(last, m.index)));
    const span = document.createElement("span");
    span.className = m[1] ? "py-cmt" : m[2] ? "py-str" : m[3] ? "py-kw"
      : m[4] ? "py-num" : "py-dec";
    span.textContent = m[0];
    frag.appendChild(span);
    last = m.index + m[0].length;
    if (m[0].length === 0) PY_HL_RE.lastIndex++;   // 防零宽死循环
  }
  frag.appendChild(document.createTextNode(code.slice(last) + "\n"));
  pre.replaceChildren(frag);
}
