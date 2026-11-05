"""genie.host.cli — the ``genie`` command line (the five verbs).

Everything is "parse + call the host + print", **zero domain logic**; the
three bans (locked by tests): no model access (no importing core / worker),
no direct gateway dialing (no importing net — dialing belongs to host/gw,
cli needs not even the vocabulary), no credential-file writes (writing the
store goes only through ``host/cred`` functions). Bare ``genie`` is the
same as ``genie studio``.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import threading

from . import cred
from .app import DEFAULT_GATEWAY, DEFAULT_PLATE, App, read_plate

__all__ = ["main", "VERBS"]

VERBS = ("login", "logout", "connect", "studio", "mcp")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv else "studio"          # bare genie is the same as studio
    rest = argv[1:]
    if verb == "login":
        return _login(rest)
    if verb == "logout":
        return _logout()
    if verb == "connect":
        return _connect()
    if verb == "studio":
        return _studio(rest)
    if verb == "mcp":
        return _mcp()
    print(f"unknown verb {verb!r} (available: {' / '.join(VERBS)})")
    return 2


def _login(rest) -> int:
    token = rest[0] if rest else getpass.getpass("cloud token: ")
    if not token:
        print("empty token; not logged in")
        return 1
    cred.save_token(token)                        # store writes go only through cred (single write function)
    print(f"logged in (credentials written to {cred.DEFAULT_PATH}, 0600)")
    return 0


def _logout() -> int:
    cred.clear()
    print("logged out (cloud credentials cleared)")
    return 0


def _connect() -> int:
    """Report connection info (report whenever the studio plate is present; no dial-out verification — the caller tests real connectivity)."""
    data = read_plate(DEFAULT_PLATE)
    if not data:
        print("session: false (unreachable — start `genie studio` first)")
        return 1
    print(json.dumps({"base_url": f"http://127.0.0.1:{data['port']}",
                      "token": data["token"]}))
    return 0


def _studio(rest) -> int:
    port = 0                                      # no default port: system-assigned
    if rest and rest[0] == "--port":
        if len(rest) < 2 or not rest[1].isdigit():
            print("usage: genie studio [--port N]")
            return 2
        port = int(rest[1])
    gw = os.environ.get("GENIE_BACKEND") or DEFAULT_GATEWAY
    studio = App(os.getcwd(), port=port, gateway_url=gw)
    studio.start()
    print(f"Genie Studio: http://127.0.0.1:{studio.port}"
          f" (plate: {studio.plate_path}; Ctrl+C to stop)")
    try:
        threading.Event().wait()                  # block to keep serving
    except KeyboardInterrupt:
        pass
    finally:
        studio.stop()
    return 0


def _mcp() -> int:
    """Agent adapter entry point — will be staged along with the ``agent/`` package; a placeholder for now, nothing pre-staged."""
    print("genie mcp: the agent adapter is not implemented yet (arrives with the agent/ package)")
    return 2
