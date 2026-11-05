"""genie.host.script — 脚本文本（工作区 ``script.py``）+ 编辑器回填状态。

Q5.4：脚本是工作区文件（打字即落盘——防抖在浏览器，服务端**收即写**；
Ctrl+S 保存 = 同一个动作的仪式版）；Q3.5：回填状态机——**最后写入者
生效**、编辑器有焦点时不覆盖（回 ``hold``）、失焦后下一拍拉最新。
显式动词：**打开 / 另存为** = ``open_as``（换编辑目标，路径跟随——同
模型 ``_save()`` 姿势）。无 dirty 状态、无 diff 合并；busy 不拦读。
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["ScriptState"]


class ScriptState:
    """编辑器后端的全部状态：目标文件 + 文本 + 版本号（写入单调递增，
    浏览器拿它判断「要不要应用这次回填」）。"""

    def __init__(self, workspace, filename: str = "script.py"):
        self.path = Path(workspace) / filename
        # 打开工作区自动载入（Q5.4）；不存在则从空文本开始
        self._text = (self.path.read_text(encoding="utf-8")
                      if self.path.exists() else "")
        self.version = 1 if self._text else 0

    def write(self, text: str) -> int:
        """打字即落盘 / 保存（立即写盘 + 版本 +1；最后写入者生效）。"""
        self._text = str(text)
        self.version += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._text, encoding="utf-8")
        return self.version

    def read(self, *, focused: bool) -> dict:
        """回填一拍：有焦点 → ``{"hold": True}``（编辑器保自己的文本）；
        无焦点 → 最新文本（失焦后下一拍拉最新）。"""
        if focused:
            return {"hold": True, "version": self.version}
        return {"text": self._text, "version": self.version}

    def open_as(self, path) -> int:
        """打开 / 另存为：换编辑目标文件，路径跟随。"""
        self.path = Path(path)
        self._text = (self.path.read_text(encoding="utf-8")
                      if self.path.exists() else "")
        self.version += 1
        return self.version
