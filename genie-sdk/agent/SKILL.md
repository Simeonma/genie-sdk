# GENIE Studio — agent skill

You drive a **local photonic-FDTD workbench**. Studio (the browser app the
human has open) is the one execution surface: your edits land in the same
`script.py`, your runs land in the same terminal. There is no second model.

## Connecting

The human starts Studio:

```
genie studio
```

It writes a *studio plate* — `~/.genie/studio.json` (pid + port + local
session token). The adapter reads that plate; there is no default port and
you must not scan ports.

- Plate missing / stale → the tools answer `no Studio session`. Ask the human
  to run `genie studio` (or to restart it). Do not invent a URL.
- Explicit override: `GENIE_STUDIO_URL` (+ `GENIE_STUDIO_TOKEN`), or
  `genie mcp --url http://127.0.0.1:<port> --token <token>`.
- Sanity check from a shell: `genie connect` prints
  `{"base_url": ..., "token": ...}` when the session is up.

Wire the MCP server into the agent runtime (stdio):

```json
{"mcpServers": {"genie": {"command": "genie", "args": ["mcp"]}}}
```

## The three tools — and when to use which

| tool | what it does | use it when |
|---|---|---|
| `edit_script(text)` | replaces the workspace script wholesale | authoring or rewriting a model; the human sees your text in the editor |
| `run_script()` | runs that script with **Run** semantics: fresh namespace, model rebuilt from the script | after `edit_script`, to make the model real |
| `run_code(code)` | runs one snippet in the terminal's namespace; the model survives | probing the live model, reading a value, small edits |

Rules that bite:

- Every call **blocks to the terminal state** and returns the tail of the
  terminal output. A failing run comes back `ok: false` with the error text.
- One write at a time: while a run is in progress, another write answers
  `busy: true` and is not queued. Reads never block. Wait, then retry once.
- The script is not the model. Editing the script changes nothing until
  `run_script`; model changes (via `run_code`) do not rewrite the script.
  Drift between the two is expected and accepted.

## Where the knowledge lives

The manual (`manual/`, shipped with the SDK) is the single source of truth
for the `f` API — its collections, verbs, and field semantics. Do not
memorise API from this file; this file only routes.

Until the manual ships in your install, ask the live process:

- `run_code("dir(f)")` — the public surface, names only.
- `run_code("help(f.submit)")` — one verb's signature and docstring.

A typical first session: `run_code("dir(f)")` → `edit_script(...)` →
`run_script()` → read the terminal output.
