"""genie.host.dialogs — native Windows file dialogs for the Studio surface.

The browser can only offer an ``<input type=file>`` picker; Studio's window
and its Open/Save/Save As need the **real** OS dialog (path entry, recent
places, overwrite prompt). This module wraps ``comdlg32`` directly — no
tkinter, no main-thread restrictions (host requests are served on a worker
thread).

Tk-free on purpose: tkinter needs the main thread on Windows, and the host
serves HTTP on a thread pool; ctypes dialogs are thread-agnostic.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

__all__ = ["ask_open", "ask_save"]

_MB = 8 * 1024 * 1024
_MAX_PATH = 32767


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


_OFN_OVERWRITEPROMPT = 0x00000002
_OFN_NOCHANGEDIR = 0x00000008
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_EXPLORER = 0x00080000

_comdlg32 = ctypes.WinDLL("comdlg32")


def _filter(filters):
    """``[("Genie simulation", "*.gensim")]`` → the double-NUL filter string."""
    if not filters:
        filters = [("All files", "*.*")]
    out = []
    for label, pattern in filters:
        out.append(f"{label} ({pattern})\0{pattern}\0")
    out.append("\0")
    return "".join(out)


def _run(which: str, title: str, filters, initial, defext, flags) -> str | None:
    import os
    if initial:
        # Windows 分隔符归一：comdlg32 对 "C:/a/b" 这类混合分隔符的
        # lpstrFile 会直接失败且不弹窗（2026-11-22 实测）。
        initial = os.path.normpath(initial)
    buf = ctypes.create_unicode_buffer(_MAX_PATH)
    if initial:
        buf.value = initial
    ofn = _OpenFileNameW()
    ofn.lStructSize = ctypes.sizeof(_OpenFileNameW)
    ofn.lpstrFilter = ctypes.c_wchar_p(_filter(filters))
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = _MAX_PATH
    ofn.lpstrTitle = title or None
    if initial:
        import os
        d = os.path.dirname(initial)
        if d:
            ofn.lpstrInitialDir = d
    ofn.lpstrDefExt = defext or None
    ofn.Flags = _OFN_EXPLORER | _OFN_NOCHANGEDIR | _OFN_PATHMUSTEXIST | flags
    ok = _comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return None
    return buf.value or None


def ask_open(title="Open simulation", filters=None, initial=None) -> str | None:
    """Show the native Open dialog; ``None`` = cancelled."""
    return _run("open", title, filters, initial, defext="gensim",
                flags=_OFN_FILEMUSTEXIST)


def ask_save(title="Save simulation", filters=None, initial=None) -> str | None:
    """Show the native Save As dialog (overwrite confirmation included)."""
    return _run("save", title, filters, initial, defext="gensim",
                flags=_OFN_OVERWRITEPROMPT)
