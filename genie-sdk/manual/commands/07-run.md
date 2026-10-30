# 07 · f — submit & lifecycle

> Five everyday commands: `parameters` / `run` / `cancel` / `summary` /
> `digest`, plus the serialization pair (to_config / to_script).

![Lifecycle](figs/fig-lifecycle.svg)

## `f.parameters()`

Sets/merges the **global parameter table** (`{name: value | expression
string}`); repeated calls accumulate, and sweep axes can only vary
**parameter names** (see page 08).

```python
f.parameters({'d': 1e-6, 'gap': 'd / 10'})
f.mesh.dx = 'd / 40'          # expression slot: evaluated server-side
f.geom.add_box().name('core').x_range(0, 'd')
```

Expression syntax and evaluation rules are defined authoritatively in
[12 · parameters](12-parameters.html).

## `f.run()`

The synchronous entry point: implies submission (serialize + local precheck
+ POST), polls until a terminal state, then **automatically downloads the
results** to `<job_id>_results.h5` under `$GENIE_RESULT_DIR` (default
`./genie_results/`; on failure it only warns — `results()` re-fetches on
demand). Returns the `Simulation` itself (`state == "done"`), ready for
`f.get_freq('tr')`.

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `gpu` | int | no | — | **Optional upper bound** on GPUs (1..8). The server picks the actual count `N` from the model's estimated demand and the machine capacity, then caps it by your `max_gpu_per_run` quota and by this value. Omit it unless you want to forbid multi-GPU. Which cards, NV-link pairing and axis splitting are the provider's business |
| `timeout_s` | int | no | 3600 | Server-side job timeout (s) |
| `poll_interval` | float | no | 2.0 | Polling interval (s) |
| `progress` | callable | no | — | Per-poll callback `progress(status_dict)` |

Simulation length is defined solely by `domain.total_time` (SI seconds, > 0
required) — `run()` takes **no step argument**; the job advances until the
grid's time capacity is reached, and the step count is a read-only derived
value (`dt`/`n_steps` in `summary`/`results`).

## `f.save(path)` / `genie.open(path)`

One `.gensim` file = one simulation. `f.save('lab.gensim')` writes the
current configuration plus the instance's already-downloaded result (when
the instance has run) into a single zip container: `simconfig.json`,
optional `results/<job>_results.h5` + `state.json` (job id, steps, dt,
grid shape), and optional `workspace/` files. The write is atomic (a `.part`
file is replaced into place), so a crash never leaves a half-written
document.

`genie.open('lab.gensim')` reads the container back: **inside a Studio
session it replaces the current session model** (same semantics as the
GUI's “Open simulation”); outside a session it returns a new instance with
the configuration loaded (ready to `run()` once credentials are set).

```python
f.run()
f.save('lab.gensim')       # config + result in one file
f2 = genie.open('lab.gensim')
```

The container format (member names, size caps, guards) lives in
`genie.core.simfile` — the same implementation serves the SDK and the
Studio endpoints; do not hand-build the zip.

### GPU scheduling semantics

You do not choose cards or how many. Three parties split the decision:

1. **This SDK** sends the configuration and shows the result.
2. **The service** decides **how many** GPUs (`N`): it asks the kernel for the
   model's demand curve, takes the smallest `N` whose per-rank need fits one
   card on that machine, and caps it with your quota and `run.gpu`.
3. **The provider** (the kernel's lease pool today, a cloud scheduler later)
   decides **which** cards and exposes exactly `N` of them to the job.

`N` is always the fewest cards that fit: any rank topology gives bit-identical
results, so extra cards only cost time and money.

Two failure shapes, deliberately different:

- **Model too big for the machine** (even with your cap) → rejected at submit
  with HTTP 422 and the numbers (`need_mib` vs the machine's card size).
- **No free card right now** → the job waits in the queue and is dispatched
  when a card frees up (no silent degradation, no failed job).

Want the numbers before submitting? Ask the kernel (read-only, no job created):

```python
c = genie.connect()
info = c.preflight(f.to_config())      # see 00-connect / HTTP_API.md
if info["available"]:
    print(info["demand"]["1"], "MiB for one GPU", info["grid"]["n_tot"], "cells")
```

```python
f.run()                                       # most common
f.run(gpu=2, progress=print)                  # dual GPU + progress callback
```

**Common pitfalls**

- Passing `gpu/timeout_s` after submission → RuntimeError (the configuration is already sent); each instance submits at most once — to change the configuration, create a new `genie.connect()`. At submission the configuration is frozen
verbatim; there is no parameter-update-and-resume interface.
- Omitting `domain.total_time` → rejected before submission (it is the only definition of simulation length).
- Job failure/kill → raises `GenieJobError` (`.state` lands on `failed`/`killed`). Besides `done`/`failed`/`killed`, historical jobs may report the terminal state `archived` (server-side archival of past jobs; the client's `TERMINAL_BAD` set includes it). Results exist only for successful terminal states: querying the results endpoint of a `failed`/`killed` job returns HTTP 409.

## `f.cancel()`

Cancels the submitted background job (`-> dict | None`); a no-op returning
None if this instance was never submitted.

## `f.summary()`

Configuration summary (domain/mesh/per-list counts/monitor names and
types/run section/state/job_id, plus the server-side job summary if
submitted). Prints and returns the dict (`-> dict`); use it to self-check
completeness before submission.

The returned dict carries the keys `{domain, mesh, n_materials,
n_geometry, n_source, monitors, run, state, job_id}`. When the instance
has been submitted, a `server_summary` block is attached containing
(among others) `grid_shape` — the grid **including absorbing layers** —
plus `dt` and `n_steps`. The reported `dt` can be cross-checked against
the Courant bound, `dt ≈ cfl · dx / (C0 · √3)`; the kernel actually
computes `dt = cfl / √(Σᵢ 1/dᵢ²)` in c = 1 units — the two forms agree
for uniform `dx`.

## `f.digest()`

Numeric result summary, computed server-side with zero GPU reads (`job_id:
str | None = None -> dict`, defaulting to this instance's most recent
submission): peak frequency/amplitude per monitor; plane monitors
additionally carry transmittance min/max/center-frequency values (the
physical T only when `normalized=true`). The dict always contains a
`source_energies` key — an empty object when the configuration declares
no named sources. For comparisons and tuning rounds,
read this instead of downloading result files. Unsubmitted instance and no
`job_id` → RuntimeError.

## Serialization pair

| Command | Returns | Description |
|---|---|---|
| `to_config()` | dict | Current configuration → SimConfig JSON (dense: defaults spelled out explicitly) |
| `to_script()` | str | Current configuration → genie script text (byte-identical to the Studio code generator); for provenance/sharing/papers |

```python
cfg = f.to_config()
open("share.py", "w").write(f.to_script())
```

To rebuild or copy a simulation, export the script with `to_script()` and
execute it on a **fresh `genie.connect()`** instance — that is the only
public rebuild path. To vary many parameters, build them through the
chained APIs (`f.domain` / `f.mat` / `f.geom` / `f.src` / `f.mon`) plus
`f.parameters()`.

**Common pitfalls**

- Editing a SimConfig JSON and expecting to load it back into an instance —
  `f.load_config()` is an **internal load-bearing channel, not a committed
  API** (session rollback / form channel only); its name and behavior are
  not guaranteed. Always rebuild via `to_script()` + a fresh `genie.connect()`.
