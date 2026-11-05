"""genie.host.script — script text (the workspace ``script.py``) + editor backfill state.

The script is a workspace file (save-on-keystroke — the debounce
lives in the browser, the server side **writes on receipt**; a Ctrl+S save
= the ceremonial version of the same action). The backfill state
machine — **last writer wins**, no overwrite while focused (reply
``hold``), and the tick after blur pulls the latest. Explicit verb:
**open / save as** = ``open_as`` (switch the edit target; the path follows).
No dirty state, no diff merging; busy does not block reads.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["ScriptState"]


class ScriptState:
    """All state of the editor backend: target file + text + version number
    (monotonically increasing on write; the browser uses it to decide
    "whether to apply this backfill")."""

    def __init__(self, workspace, filename: str = "script.py"):
        self.path = Path(workspace) / filename
        # Auto-load when the workspace opens; start from empty text if it does not exist
        self._text = (self.path.read_text(encoding="utf-8")
                      if self.path.exists() else "")
        self.version = 1 if self._text else 0

    def write(self, text: str) -> int:
        """Save-on-keystroke / save (write to disk at once + version +1; last writer wins)."""
        self._text = str(text)
        self.version += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._text, encoding="utf-8")
        return self.version

    def read(self, *, focused: bool) -> dict:
        """One backfill tick: focused → ``{"hold": True}`` (the editor keeps
        its own text); unfocused → the latest text (the tick after blur
        pulls the latest)."""
        if focused:
            return {"hold": True, "version": self.version}
        return {"text": self._text, "version": self.version}

    def open_as(self, path) -> int:
        """Open / save as: switch the target file being edited; the path follows."""
        self.path = Path(path)
        self._text = (self.path.read_text(encoding="utf-8")
                      if self.path.exists() else "")
        self.version += 1
        return self.version
