# 08 · sweep (f.sweep & SweepHandle)

> `f.sweep(...)` expands the sweep **locally** and returns a `SweepHandle`
> immediately — no submission, no waiting, at call time. A sweep is a pure
> client-side composition of ordinary jobs: there is no parent job and no
> server-side sweep track; each point is submitted as a regular job under
> the signed-in user. The handle keeps all state local.

## `f.sweep()`

Expands the SweepSpec against the base configuration at the call site:
`SweepSpec` validation plus `validate_sweep_config` (axis-name check,
expansion, per-point re-resolution). An invalid spec raises
`SweepExpandError` **locally** at the call point. A valid call returns a
`SweepHandle` with the per-point **resolved** configurations ready to
submit — nothing has been sent yet.

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `config` | dict | no | `to_config()` | Base SimConfig JSON; defaults to the current instance's collected configuration |
| `sweep` | dict | no | — | Fully-controlled SweepSpec dict; mutually exclusive with `parameters` (both given → `ValueError`) |
| `parameters` | list | no | — | Convenient axis form: `[{"name": "dx", "linspace": [10e-9, 40e-9, 4]}, …]`; each axis is `{"name": …}` plus exactly one of `values` / `linspace` / `logspace` (mutually exclusive, violating → `ValueError`); `name` must be a key of the base `parameters` table |
| `mode` | str | no | `"product"` | `"product"` (Cartesian, axis order = outermost loop first) / `"zip"` (axes paired element-wise, equal lengths required) |
| `gpus_per_sim` | int | no | 1 | GPUs per point (1–8) |
| `max_parallel` | int | no | 1 | Local cap on points in flight (1–16) |
| `on_failure` | str | no | `"continue"` | `"continue"` (failed points become NaN rows, the rest run) / `"abort"` (first failure cancels the remainder) |

**Local expansion constraints** (`SweepExpandError` at the `f.sweep()`
call point): at most 32 axes; at most 512 expanded points by default
(a server-side cap; over-limit rejected with the count and the cap);
`zip` axes of equal
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

### Submission semantics (sliding window)

Points are submitted as ordinary jobs, one per point, through a sliding
window: at most `max_parallel` jobs are kept in flight, and each finished
point frees a slot for the next pending one. The window is a politeness
measure only — server-side admission remains authoritative.

## SweepHandle

Handle-side state machine (state names retained for downstream
compatibility):
`pending → running → done | canceled | failed` — `failed` only when
**every** point ended bad (failed/killed/canceled); any success →
`done`.

| Attribute | Type | Description |
|---|---|---|
| `h.id` | str | A local sweep id
| `h.state` | str | Handle state: `pending` / `running` / `done` / `canceled` / `failed` |
| `h.points` | list[dict] | Per-point **resolved configurations** (what each child job was or will be submitted with) |
| `h.axis_names` | list[str] | Axis parameter names, in spec order |
| `h.job_ids` | list[str \| None] | Per-point child job ids; `None` until submitted |
| `h.states` | list[str] | Per-point states (`pending` / `queued` / `running` / `succeeded` / `failed` / `killed` / `canceled`) — snapshot as of the last refresh |

| Method | Description |
|---|---|
| `wait(timeout=None, poll=2.0, progress=None)` | Drive the orchestration (one `pump()` per `poll` seconds) to a terminal state and return the final status dict. `done` → returned; `failed`/`canceled` → raises `GenieJobError` carrying the rollup. `timeout` expiry → `GenieHTTPError` (408); nothing is killed — re-enter `wait()` with no arguments to resume the same handle. `progress=True` prints a one-line rollup per step; a callable receives `progress(status)` |
| `pump()` | Advance one step locally: fill the submission window, refresh in-flight states, rule on abort/cancel, judge terminal state |
| `status()` | Pure local assembly `{sweep_id, state, rollup, points: [{idx, state, job_id}]}` — **no network call**; in-flight states are the snapshot from the last refresh (call `wait`/`pump` first for fresh data) |
| `rollup()` | Local count dict over per-point states (`{"succeeded": 3, "running": 1, …}`) |
| `cancel()` | Idempotent (no 409 path): in-flight child jobs are cancelled one by one, remaining points marked `canceled` |
| `digests()` | Per-point `GET /api/jobs/{id}/digest`; failed / canceled / not-yet-run points → `None` (NaN rows downstream) |
| `aggregate()` | **Local** aggregation as a plain column dict (see below) — not npz bytes, not an HTTP fetch; never 404/410 |
| `download(dir)` | Lands exactly two files into `dir`: `manifest.json` (verbatim `status()` output) and `aggregate.npz`. No `pNNNN/` per-point directories — each point is an ordinary job; per-point full results are fetched through the ordinary results surface after `genie.connect()` |

`on_failure="abort"` semantics: only a point that ends `failed` triggers
automatic cancellation of the remaining points; a `killed` point does
not.

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

- Calling old-surface methods — `manifest()`, `.result(idx)`,
  `child_id(idx)` and per-point `pNNNN/` downloads do not exist on the
  current handle; per-point results come from the child jobs (ordinary
  jobs) through the ordinary results surface.
- Taking the `f` after `f.run()`, tweaking parameters, and sweeping —
  the `config` default pulls `to_config()`, i.e. the **current**
  configuration, which is locked after a run; don't run before
  sweeping.
- Assuming one failed point means total failure — under
  `on_failure="continue"` (default) the remaining points run anyway;
  check the `rollup` breakdown.
