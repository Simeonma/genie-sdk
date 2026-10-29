# 00 · Connections & entry

> One module-level entry: `genie.connect()` opens a session. All units are SI;
> numeric slots also accept expression strings (`"20[nm]"`, `"d/4"` via
> `f.parameters`), evaluated server-side.

## `genie.connect()`

Connect to the GENIE service and return a `Simulation` instance (conventionally
named `f`) — a live configuration object that is serialized and sent to the
cloud only when `f.run()` submits. Returns: a `Simulation` with six
sub-objects: `f.domain` ([Domain](#f-domain-physical-domain-boundaries)), `f.mesh` ([Grid](#f-mesh-grid-local-refinement)), `f.mat` ([Materials](#f-mat-materials-registry)), `f.geom` ([Geometry](#f-geom-geometry)), `f.src` ([Sources](#f-src-sources)), `f.mon` ([Monitors](#f-mon-monitors)).

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `timeout` | float | No | 30.0 | HTTP timeout (s, keyword-only) |

**Zero-argument is the only form** (2026-10-29): connection parameters
are gone. Credentials are injected once per machine by a human (login) or by
an operator (environment variables) — scripts never carry addresses or keys.

**Two rails (picked automatically, same call):** a live local Studio is
found → ride it through the session's `/gw` proxy (the script holds no
credential); otherwise → dial the gateway directly, address and token
resolved from env / the machine's credential store.

Login once per machine (either surface writes the same token store):

```bash
genie login                     # terminal: username + password, once
genie studio                    # or log in once in the Studio login gate
genie connect https://genie.example.com   # set the machine's gateway, once
```

Tokens are idle-expiring (7 days unused; actively used tokens never expire) — on expiry, log in again in the same surface as before.

```python
import genie

f = genie.connect()             # the only form — credentials come from
                                # the machine (genie login), not the script
```

**Common pitfalls**

- Passing `base_url` / `api_key` / `username` / `password` raises
  `GenieConfigError` pointing at `genie login` — the error is the
  instruction. Operator-provisioned machines use the `GENIE_URL` /
  `GENIE_API_KEY` / `GENIE_USERNAME` / `GENIE_PASSWORD` environment
  variables instead.
- A `Simulation` can only be submitted once — re-running an already-submitted instance raises `RuntimeError`; create a fresh `connect()` for a new run.
- On a fresh machine with nothing configured, bare `connect()` raises `GenieConfigError` pointing at `genie login` (after `genie connect <address>` if the gateway isn't set) — the error is the instruction.
- Inside a Studio session, `genie.connect` is taken over by the session — same zero-argument call, session credentials.

**See also** [Simulation state machine & run()](#f-submit-lifecycle)

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
