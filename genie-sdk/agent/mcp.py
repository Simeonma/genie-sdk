"""genie.agent.mcp — the stdio MCP server (Q6.6): three tools over Studio.

Entry point is the CLI verb ``genie mcp`` (Q6.9), never a second script
name. The server speaks MCP on stdio; the tools are the three verbs from
``genie.agent`` (thin wrappers over the same host endpoints the browser
uses — no second execution face, Q3.1).

An agent runtime is configured with something like::

    {"mcpServers": {"genie": {"command": "genie", "args": ["mcp"]}}}

``GENIE_STUDIO_URL`` (+ ``GENIE_STUDIO_TOKEN``) overrides the studio plate;
otherwise the adapter follows ``~/.genie/studio.json`` (Q3.2).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import ENV_TOKEN, ENV_URL
from . import connect as _connect
from . import edit_script as _edit_script
from . import run_code as _run_code
from . import run_script as _run_script

__all__ = ["build", "main", "INSTRUCTIONS"]

#: Handed to the agent at handshake: how to connect, when to use which verb,
#: where the knowledge lives. Deliberately no API list / field names (Q6.5:
#: the manual is the single source; this text only routes).
INSTRUCTIONS = """\
GENIE Studio is a local photonic-FDTD workbench. You drive the same script
and the same terminal the human sees in the browser — not a copy.

Connection: the human runs `genie studio` first; it writes the studio plate
(`~/.genie/studio.json`). If a tool reports "no Studio session", ask the
human to start Studio; do not invent a port.

Tools and when to use them:
- `edit_script(text)` — replace the workspace script (`script.py`) wholesale.
  Use it to author or rewrite a model. The browser editor shows your text.
- `run_script()` — run the current workspace script with Run semantics: a
  fresh namespace, the model rebuilt from the script. Use it after editing.
- `run_code(code)` — run one snippet in the terminal's namespace. Use it to
  poke at the live model or read a value without touching the script.

Every call blocks until the run reaches its terminal state and returns the
tail of the terminal output. While a run is in progress any further write is
rejected as busy (the call returns `busy: true`); reads are not.

Read the API: the manual is the single source of truth for the `f` surface.
Until `manual/` ships, use the Studio UI and the terminal: `dir(f)` and
`help(f.<name>)` inside `run_code` answer most questions.
"""


def build(server_factory=None):
    """Wire the three verbs onto an MCP server (``server_factory`` is a test seam)."""
    if server_factory is None:
        from mcp.server.mcpserver import MCPServer       # lazy: verbs work without mcp
        server_factory = MCPServer
    server = server_factory("genie", instructions=INSTRUCTIONS)

    @server.tool(name="edit_script")
    def edit_script(text: str) -> str:
        """Replace the workspace script with `text` (the editor shows it)."""
        return _as_text(_edit_script(text))

    @server.tool(name="run_script")
    def run_script() -> str:
        """Run the current workspace script (Run = fresh namespace, model rebuilt)."""
        return _as_text(_run_script())

    @server.tool(name="run_code")
    def run_code(code: str) -> str:
        """Run one snippet in the terminal's namespace (the model survives)."""
        return _as_text(_run_code(code))

    return server


def _as_text(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="genie mcp",
        description="stdio MCP server exposing Studio's three verbs")
    ap.add_argument("--url", help="Studio base URL (overrides the studio plate)")
    ap.add_argument("--token", help="local session token (with --url)")
    args = ap.parse_args(argv)
    if args.url:
        os.environ[ENV_URL] = args.url
    if args.token:
        os.environ[ENV_TOKEN] = args.token

    status = _connect()
    if not status.get("session"):
        # stdout belongs to MCP once the server starts; a missing Studio is
        # reported to the human on stderr and the server still starts (the
        # human may start Studio later; every tool then re-reads the plate).
        print(f"genie mcp: {status.get('error')}", file=sys.stderr)
    else:
        print(f"genie mcp: Studio {status.get('base_url')} "
              f"(workspace {status.get('workspace')})", file=sys.stderr)
    build().run("stdio")
    return 0


if __name__ == "__main__":          # pragma: no cover — the entry is `genie mcp`
    sys.exit(main())
