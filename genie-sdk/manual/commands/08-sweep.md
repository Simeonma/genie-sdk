# 08 · sweep (f.sweep & SweepHandle)

> `f.sweep(...)` expands the sweep **locally** and returns a `SweepHandle`
> immediately — no submission, no waiting, at call time. A sweep is a pure
> client-side composition of ordinary jobs: there is no parent job and no
> server-side sweep track. **Each point is a full `Simulation` instance
> driven through the ordinary `run()` primitive** — local validation,
> auto-save (per-point h5 into the session results directory) and the
> whole results surface come with it.

## `f.sweep()`

Expands the axes against the base configuration at the call site:
`SweepSpec` validation plus `validate_sweep_config` (axis-name check,
expansion, per-point re-resolution). All spec-shape, axis-name and
per-point errors raise `SweepExpandError` **locally** at the call point
(one error type — G92). A valid call returns a `SweepHandle` holding one
independent `Simulation` per point — nothing has been submitted yet.

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `parameters` | list | yes | — | Axis form: `[{"name": "pitch", "linspace": [10e-9, 40e-9, 4]}, …]`; each axis is `{"name": …}` plus exactly one of `values` / `linspace` / `logspace` (violating → `SweepExpandError`); `name` must be a key of the base `parameters` table |
| `gpus_per_sim` | int | no | 1 | GPU cap per point — passed to each point's `run(gpu=…)` (a real knob); same cap as `run(gpu=…)` itself (1..4, single protocol constant), out-of-range raises `SweepExpandError` at the call point |
| `max_parallel` | int | no | 1 | Concurrent points in flight (1 = strictly sequential; capped by your quota) |
| `on_failure` | str | no | `"continue"` | `"continue"` (bad points mark `failed`, the rest run) / `"abort"` (first bad point **of any bad kind** — `failed`/`killed`/`archived` — stops scheduling and cancels in-flight points) |
| `mode` | str | no | `"product"` | `"product"` (Cartesian) / `"zip"` (axes paired element-wise, equal lengths required) |

**Local expansion constraints** (`SweepExpandError` at the `f.sweep()`
call point): at most 32 axes; at most 512 expanded points by default
(over-limit rejected with the count and the cap); `zip` axes of equal
length; every axis `name` must exist in the base `parameters` key set
(else the error lists the available names). `linspace`/`logspace` take
`[start, stop, n]` with `n ≥ 2` and `start != stop`; `logspace`
interprets the bounds as exponents of 10.

```python
f.parameters({'pitch': 180e-9})
# …chain configuration; dx etc. use bare-name pitch expressions…
h = f.sweep(parameters=[
    {"name": "pitch", "linspace": [140e-9, 240e-9, 6]},
], max_parallel=2)
h.wait(progress=True)
```

### Submission semantics (run() primitive, one instance per point)

Each point is an independent `Simulation` driven through `run()` — the
single submission primitive. What each point gets for free:

- local pre-submit validation (expressions resolved; errors before network);
- **auto-save**: the point's result h5 lands in the session results
  directory, exactly like a standalone run — plot it, `visualize` it,
  fetch it later;
- ordinary-job tracking (job id, digest, cancellation) through the
  session gateway.

The concurrency window executes the points: `max_parallel=1` runs them
strictly in order; `>1` uses a thread pool with one `Simulation` per
thread (instances share nothing). The window is a politeness measure —
your quota remains authoritative.

## SweepHandle

Handle-side state machine (state names retained for downstream
compatibility):
`pending → running → done | canceled | failed` — `failed` only when
**every** point ended bad (failed/killed/canceled); any success →
`done`.

| Attribute | Type | Description |
|---|---|---|
| `h.id` | str | A local sweep id |
| `h.state` | str | Handle state: `pending` / `running` / `done` / `canceled` / `failed` |
| `h.sims` | list[Simulation] | The per-point instances — full `Simulation` surface per point |
| `h.points` | list[dict] | Per-point **resolved configurations** (what each child job was submitted with) |
| `h.axis_names` | list[str] | Axis parameter names, in spec order |
| `h.job_ids` | list[str \| None] | Per-point child job ids; `None` until that point's `run()` submitted |
| `h.states` | list[str] | Per-point states (`pending` / `running` / `succeeded` / `failed` / `killed` / `archived` / `canceled`) — snapshot as of the last refresh |

| Method | Description |
|---|---|
| `wait(timeout=None, progress=None)` | Execute all points to a terminal state and return the final status dict. `done` → returned; `failed`/`canceled` → raises `GenieJobError` carrying the rollup. `timeout` expiry → `GenieHTTPError` (408); execution is not killed — re-enter `wait()` with no arguments to resume (finished points are skipped). In sequential mode (the default) the timeout is checked between points, so the raise can land later than `timeout` if a point runs long. `progress=True` prints a one-line rollup per step; a callable receives `progress(status)` |
| `results(i)` | The i-th point's `SimData` — reads the point's auto-saved h5 through the ordinary results surface |
| `status()` | Pure local assembly `{sweep_id, state, rollup, points: [{idx, state, job_id}]}` — **no network call** |
| `rollup()` | Local count dict over per-point states (`{"succeeded": 3, "running": 1, …}`) |
| `cancel()` | Idempotent: in-flight child jobs are cancelled one by one (via each point's live job id), remaining points marked `canceled` |
| `digests()` | Per-point `GET /api/jobs/{id}/digest`; failed / canceled / not-yet-run points → `None` (NaN rows downstream) |
| `aggregate()` | **Local** aggregation as a plain column dict (see below) — not npz bytes, not an HTTP fetch; never 404/410 |
| `download(dir)` | Lands exactly two files into `dir`: `manifest.json` (verbatim `status()` output plus an `aggregate` summary) and `aggregate.npz`. This format is canonical — there is no legacy server-side sweep artifact to stay compatible with |

`on_failure="abort"` semantics (rewritten 2026-10-27): the **first bad
terminal of any kind** (`failed` / `killed` / `archived`) stops
scheduling new points and cancels the in-flight ones; unstarted points
are marked `canceled`. The handle ends `canceled` (unless every point
bad → `failed`).

### Aggregate column contract

`aggregate()` / `download(dir)` build the table locally from the
per-point digests; the column names are a consumed contract
(downstream plotting depends on them verbatim):

- Axis columns `param_<name>` — one per axis, taken from each point's
  resolved `parameters`; name collisions resolved deterministically by
  appending `_2`, `_3`, ….
- Result columns `pt_<flattened digest key>` — the union of numeric
  leaves across all points' digests; nested dicts join with `.` and
  lists by index, e.g. `pt_monitors.0.comps.Ex.abs_max`. Boolean leaves
  are skipped.
- Missing keys and failed/canceled points fill with `NaN`.
- Metadata columns: `_axes` (a JSON list of `{column, name}` entries)
  and `_skipped_keys` (JSON list of non-numeric skipped keys).

**Common pitfalls**

- Calling old-surface methods — `manifest()`, `child_id(idx)` and
  per-point `pNNNN/` downloads do not exist on the current handle;
  per-point results are auto-saved by each point's `run()` and reachable
  via `h.results(i)` or the ordinary results surface.
- Taking the `f` after `f.run()`, tweaking parameters, and sweeping —
  the sweep base pulls `to_config()`, i.e. the **current**
  configuration, which is locked after a run; don't run before
  sweeping.
- Assuming one failed point means total failure — under
  `on_failure="continue"` (default) the remaining points run anyway;
  check the `rollup` breakdown.
