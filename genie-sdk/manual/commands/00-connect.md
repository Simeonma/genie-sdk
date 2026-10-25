# 00 · Connections & entry

> Two module-level entries: `genie.connect()` opens a session; `genie.preview_script()`
> executes a script with zero network. All units are SI; numeric slots also accept
> expression strings (`"20[nm]"`, `"d/4"` via `f.parameters`), evaluated server-side.

## `genie.connect()`

Connect to the GENIE service and return a `Simulation` instance (conventionally
named `f`) — a live configuration object that is serialized and sent to the
cloud only when `f.run()` submits. Returns: a `Simulation` with six
sub-objects: `f.domain` ([Domain](#f-domain-physical-domain-boundaries)), `f.mesh` ([Grid](#f-mesh-grid-local-refinement)), `f.mat` ([Materials](#f-mat-materials-registry)), `f.geom` ([Geometry](#f-geom-geometry)), `f.src` ([Sources](#f-src-sources)), `f.mon` ([Monitors](#f-mon-monitors)).

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `base_url` | str | No | see priority below | Service address (`https://genie.example.com`) for remote SDK, `http://127.0.0.1:8777` for Studio debugging |
| `api_key` | str | No | see priority below | Auth key (sent as `X-API-Key` header) |
| `username` | str | No | see priority below | Username (metering attribution) |
| `timeout` | float | No | 30.0 | HTTP timeout (s, keyword-only) |

**Two connection modes (picked by how it is called):**

```text
bare genie.connect()          → reuses the local session's credentials
                                (Studio's, or an in-process headless one).
                                The script never holds a credential — the
                                session does.
explicit args / GENIE_URL env → goes direct: self-contained scripts, tests,
                                machines provisioned with GENIE_USERNAME /
                                GENIE_PASSWORD.
```

Login once per machine (either surface writes the same session-held token):

```bash
genie login                     # terminal: username + password, once
genie studio                    # or log in once in the Studio login gate
genie connect https://genie.example.com   # set the machine's gateway, once
```

Tokens are idle-expiring (7 days unused; actively used tokens never expire) — on expiry, log in again in the same surface as before.

```python
import genie

f = genie.connect()                    # reuses the local session's credentials
f = genie.connect("https://genie.example.com", api_key="…")
```

**Common pitfalls**

- A `Simulation` can only be submitted once — re-running an already-submitted instance raises `RuntimeError`; create a fresh `connect()` for a new run.
- On a fresh machine with nothing configured, bare `connect()` raises `GenieConfigError` pointing at `genie login` (after `genie connect <address>` if the gateway isn't set) — the error is the instruction.
- Inside a Studio session, `genie.connect` is taken over by the session (zero-network preview) and connection parameters are ignored — expected behavior.

**See also** [Simulation state machine & run()](#f-submit-lifecycle)

## `genie.preview_script()`

Execute a script text with zero network and return the model it builds — the
entry point shared by Studio preview and agent tools, entirely independent of
real submissions. Returns: a dict with `ok` (bool), `cfg` (captured SimConfig
JSON; on failure, everything built up to the crash), `stdout` (captured
prints), `stderr` (captured warnings and stderr output), and `error` (`None`,
or `{kind, message, line, traceback}` with the script line number).

| Param | Type | Required | Description |
|---|---|---|---|
| `source` | str | Yes | Full script text (may contain `import genie` / `genie.connect(...)`) |

`genie.connect` is replaced by a zero-network factory: `f.run()` only records
the run section, `get_*()`/`sweep()` return permissive placeholders — the
script doesn't crash but gets no real data. A `for` loop = N executions = N
cfg nodes; numpy expressions are evaluated into literals at execution time.

**Example**

```python
import genie
r = genie.preview_script(open("my_sim.py").read())
if r["ok"]:
    print(r["cfg"]["domain"])
else:
    print(r["error"]["line"], r["error"]["message"])
```

**Common pitfalls**

- Expecting simulation data from a preview — previews run no jobs; post-processing scripts only get placeholder objects.

**See also** `genie.check_script`; for script-driven automation see the agent documentation shipped in `genie/docs/agent.md`

## Lifecycle of f (overview)

```
defined → built → running → done
                 ↘ failed / killed (run() raises GenieJobError; state lands on the actual terminal state)
```

| Command | What it does | Page |
|---|---|---|
| `f.parameters(table)` | Global parameter table (expressions and sweep references) | 07 |
| `f.run()` | Submit + poll to terminal state (synchronous) | 07 |
| `f.cancel()` | Cancel a submitted job | 07 |
| `f.sweep(...)` | Submit a parameter sweep, returns SweepHandle | 08 |
| `f.results()` / `f.get_time/get_freq/get_index` | Fetch results | 09 |
| `f.summary()` / `f.digest()` | Configuration/result summaries | 07 |
| `f.to_config()` / `f.to_script()` | The serialization pair (rebuild = to_script + a fresh `genie.connect()`) | 07 |
