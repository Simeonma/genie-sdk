"""工作区根设置（**机器级持久**，2026-10-30 站长定案）。

一个用户一台机器一个"当前工作区"（像 IDE 的"打开文件夹"）：所有会话、
`genie console`、结果、脚本、自动存档的 `.gensim` 都跟着它走。用户想换，
就显式换（`genie workspace PATH` 或 Studio 的 Workspace… 按钮）。

解析优先级（唯一口径，kernel_session.workspace_root 也走这里）：

1. ``GENIE_SESSION_CWD`` / ``GENIE_SCRIPTS_DIR``（显式 env，CI/特殊场景；
   测试隔离也靠它）；
2. ``~/.genie/workspace.json`` 的 ``root``（本模块写的持久设置）；
3. 缺省 ``~/data/genie/scripts``（存量用户原地不动）。

设置文件只存"用户的选择"，不是第二份状态：目录本身仍是普通文件夹。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..core import paths as _paths

MAX_RECENT = 8


def settings_path() -> Path:
    """``~/.genie/workspace.json``（与凭据仓同目录，永不进公开仓）。"""
    return _paths.settings_path()


def default_root() -> Path:
    """缺省工作区根（**调用时**解析 home——模块导入时钉死会在测试/换
    home 后指向错误目录）。"""
    return _paths.default_root()


def _read_json() -> dict:
    try:
        raw = settings_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(data: dict) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".workspace-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def env_root() -> Path | None:
    """显式 env 覆盖（最高优先级；空串不算）。"""
    return _paths.env_root()


def current_root() -> Path:
    """当前工作区根（解析口径 = core.paths，单一来源）。"""
    return _paths.workspace_root()


def source() -> str:
    """``env`` / ``setting`` / ``default``——界面与 CLI 如实显示来源。"""
    return _paths.source()


def recents() -> list[str]:
    out = _read_json().get("recent")
    return [str(x) for x in out if isinstance(x, str)][:MAX_RECENT] if isinstance(
        out, list) else []


def set_root(path) -> Path:
    """设置当前工作区（建目录、校验可写、记 recent、原子写设置）。

    注意：env 覆盖优先——设了 ``GENIE_SESSION_CWD``/``GENIE_SCRIPTS_DIR``
    的机器上，本设置不生效（CLI 会如实提示）。
    """
    p = Path(path).expanduser()
    if p.exists() and not p.is_dir():
        raise ValueError(f"工作区必须是目录：{p}")
    p.mkdir(parents=True, exist_ok=True)
    probe = p / ".genie-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"工作区不可写：{p}（{e}）") from e
    data = _read_json()
    old = str(data.get("root") or "")
    recent = [r for r in data.get("recent") or [] if isinstance(r, str)]
    entry = str(p.resolve())
    recent = [entry] + [r for r in recent if r != entry]
    data["root"] = entry
    data["recent"] = recent[:MAX_RECENT]
    _atomic_write(data)
    return Path(data["root"])


def clear() -> None:
    """清掉持久设置（回到缺省根；env 覆盖不受影响）。"""
    data = _read_json()
    data.pop("root", None)
    _atomic_write(data)


def pick_directory(title: str = "选择工作区") -> str | None:
    """服务端**原生**目录对话框（本地 Studio 专用）。

    Windows 走 Win32 ``SHBrowseForFolderW``（ctypes，零新依赖，任何线程
    可调）；其它平台 / 失败 → None（调用方指路 ``genie workspace PATH``）。
    取消 → None。
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", ctypes.c_uint),
            ("lpfn", ctypes.c_void_p),
            ("lParam", ctypes.c_void_p),
            ("iImage", ctypes.c_int),
        ]

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BROWSEINFOW)]
    shell32.SHGetPathFromIDListW.restype = ctypes.c_int
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p,
                                             wintypes.LPWSTR]
    ole32.CoInitialize(None)
    try:
        shown = ctypes.create_unicode_buffer(1024)
        bi = _BROWSEINFOW()
        bi.hwndOwner = None
        bi.pidlRoot = None
        bi.pszDisplayName = ctypes.cast(shown, wintypes.LPWSTR)
        bi.lpszTitle = title
        # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
        bi.ulFlags = 0x0001 | 0x0040 | 0x0010
        bi.lpfn = None
        bi.lParam = None
        bi.iImage = 0
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None                      # 用户取消
        try:
            out = ctypes.create_unicode_buffer(1024)
            ok = shell32.SHGetPathFromIDListW(pidl, out)
            return out.value if ok else None
        finally:
            ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    finally:
        ole32.CoUninitialize()
