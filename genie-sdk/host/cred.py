"""genie.host.cred — the cloud credential store (``~/.genie/credentials.json``, 0600).

The cloud token lives only in the host: into this store, out as ``/gw``
upstream headers, **never into the worker, never into spawn env**. Exactly
one function writes the file (``_write_store`` — both entry points
login/logout go through it; locked by tests);
upstream headers are not assembled here (the header name appears only in
``host/gw``).
"""
from __future__ import annotations

import json
import os

__all__ = ["save_token", "load_token", "clear", "DEFAULT_PATH"]

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".genie",
                            "credentials.json")


def _write_store(path: str, data: dict) -> None:
    """The single write function: persist with 0600 (create the directory if missing; permissions best-effort — fully effective on POSIX)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def save_token(token: str, *, path: str | None = None) -> None:
    """login entry point: store the cloud token."""
    _write_store(path or DEFAULT_PATH, {"token": token})


def load_token(*, path: str | None = None) -> str | None:
    p = path or DEFAULT_PATH
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh).get("token")
    except (OSError, ValueError):
        return None


def clear(*, path: str | None = None) -> None:
    """logout entry point: withdraw the store (delete the file; the token value never lands in a second place)."""
    p = path or DEFAULT_PATH
    if os.path.exists(p):
        os.remove(p)
