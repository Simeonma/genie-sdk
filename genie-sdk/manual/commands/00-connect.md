# 00 · Entry & where models run

> **Execution happens in Studio, nowhere else** (2026-11-02). A `Simulation`
> (conventionally `f`) is a live configuration object; it is serialized and
> sent to the cloud only when `f.run()` submits — and that can only happen
> inside Studio's script editor / terminal.

## Where you can do what

| You want to… | Where | How |
|---|---|---|
| Build a model, run it, see results | **Studio** | script editor (whole recipe, atomic) or terminal (increments) |
| Build a model only — no run | anywhere | `genie.Simulation()` / `genie.open(path)` |
| Drive it from an agent | Studio + MCP | three verbs: edit script / run script / run a snippet |

## `genie.connect()` — Studio only

Inside Studio the worker takes this symbol over: it creates a **fresh
instance** and folds it into the current model, so recipes may still write
`genie.connect()`. Returns a `Simulation` with six sub-objects: `f.domain`
([Domain](#f-domain-physical-domain-boundaries)), `f.mesh`
([Grid](#f-mesh-grid-local-refinement)), `f.mat`
([Materials](#f-mat-materials-registry)), `f.geom`
([Geometry](#f-geom-geometry)), `f.src` ([Sources](#f-src-sources)),
`f.mon` ([Monitors](#f-mon-monitors)).

Outside Studio it raises `GenieConfigError` — there is no "run in your own
Python process". Build-only work uses:

```python
import genie
f = genie.Simulation()          # no gateway client: build/save, cannot run
f.domain.x_range = (-3e-6, 3e-6)
f.save("my.gensim")
```

## Login (once per machine)

Credentials live in the machine's token store; the Studio login gate writes
it. Scripts never carry addresses or keys.

```bash
genie login                     # terminal: username + password, once
genie studio                    # or log in once in the Studio login gate
```

Tokens are idle-expiring (7 days unused; actively used tokens never expire) —
on expiry, log in again in the same surface as before.

## Common pitfalls

- **`f.run()` needs a gateway client.** Instances built outside Studio have
  none (`f._client is None`) — running there is refused by design. Do the run
  inside Studio.
- A `Simulation` can only be submitted once — re-running an already-submitted
  instance raises `RuntimeError`; inside Studio the toolbar Run / `run_model()`
  submit a **temporary copy**, so your editable model never locks.
- Plots are GUI-only: `f.visualize(...)` inside Studio emits a `plot` event to
  the Plot panel; there is no external push channel (removed 2026-11-02).

**See also** [Simulation state machine & run()](#f-submit-lifecycle)

## Lifecycle of f (overview)

```
defined → built → running → done
                 ↘ failed / killed (run() raises GenieJobError; state lands on the actual terminal state)
```

| Command | What it does | Page |
|---|---|---|
| `f.parameters(table)` | Global parameter table (expressions and sweep references) | 07 |
| `f.run()` | Submit + poll to terminal state (synchronous) — Studio only | 07 |
| `f.cancel()` | Cancel a submitted job | 07 |
| `f.sweep(...)` | Submit a parameter sweep, returns SweepHandle — Studio only | 08 |
| `f.results()` / `f.get_time/get_freq/get_index` | Fetch results | 09 |
| `f.summary()` / `f.digest()` | Configuration/result summaries | 07 |
| `f.to_config()` / `f.to_script()` | The serialization pair (rebuild = to_script + a fresh `genie.connect()` inside Studio) | 07 |
