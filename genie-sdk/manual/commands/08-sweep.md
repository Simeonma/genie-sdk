# 08 · sweep (f.sweep & SweepHandle)

> `f.sweep(...)` expands the sweep **locally** and returns a `SweepHandle`
> immediately — no submission, no waiting, at call time. A sweep is a pure
> client-side composition of ordinary jobs: there is no parent job and no
> server-side sweep track. **Each point is a full `Simulation` instance
> driven through the ordinary `run()` primitive**, and each point saves its
> own result document into the sweep folder.

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
| `gpus_per_sim` | int | no | 1 | GPU cap per point — passed to each point's `run(gpu=…)` (a real knob); same cap as `run(gpu=…)` itself (1..8, single protocol constant), out-of-range raises `SweepExpandError` at the call point |
| `max_parallel` | int | no | 1 | Concurrent points in flight (1 = strictly sequential; capped by your quota) |
| `on_failure` | str | no | `"continue"` | `"continue"` (bad points mark `failed`, the rest run) / `"abort"` (first bad point **of any bad kind** — `failed`/`killed`/`archived` — stops scheduling and cancels in-flight points) |
| `mode` | str | no | `"product"` | `"product"` (Cartesian) / `"zip"` (axes paired element-wise, equal lengths required) |
| `name` | str | no | first axis name | Sweep folder name: `<workspace>/[owner/]sweeps/<name>-<timestamp>/` |

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
h.visualize("tr", "T")        # same verb as a single run
```

### Submission semantics (run() primitive, one instance per point)

Each point is an independent `Simulation` driven through `run()` — the
single submission primitive. What each point gets for free:

- local pre-submit validation (expressions resolved; errors before network);
- **auto-save**: the point's result document `<name>-0001.gensim` lands in
  the sweep folder — the same `.gensim` container a single run writes;
- ordinary-job tracking (job id, digest, cancellation) through the
  session gateway.

Failed and canceled points also get a document (`state.json` with the
error, no result member) — the folder is auditable. The `sweep.json` index
is written before the start (all points `pending`) and updated at every
point terminal and at the sweep terminal.

The concurrency window executes the points: `max_parallel=1` runs them
strictly in order; `>1` uses a thread pool with one `Simulation` per
thread (instances share nothing). The window is a politeness measure —
your quota remains authoritative.

## SweepHandle

Handle-side state machine: `pending → running → done | canceled | failed`
— `failed` only when **every** point ended bad (failed/killed/canceled);
any success → `done`.

| Attribute | Type | Description |
|---|---|---|
| `h.id` | str | A local sweep id |
| `h.dir` | Path | The sweep folder (`sweep.json` + one `<name>-NNNN.gensim` per point) |
| `h.name` | str | Folder slug / point-name prefix |
| `h.state` | str | Handle state: `pending` / `running` / `done` / `canceled` / `failed` |
| `h.sims` | list[Simulation] | The per-point instances — full `Simulation` surface per point |
| `h.points` | list[dict] | Per-point **resolved configurations** (what each child job was submitted with) |
| `h.axis_names` | list[str] | Axis parameter names, in spec order |
| `h.params` | dict[str, ndarray] | `{axis: per-point values}` — the coordinates of the extra dimensions |
| `h.job_ids` | list[str \| None] | Per-point child job ids; `None` until that point's `run()` submitted |
| `h.states` | list[str] | Per-point states (`pending` / `running` / `succeeded` / `failed` / `killed` / `archived` / `canceled`) |

| Method | Description |
|---|---|
| `wait(timeout=None, progress=None)` | Execute all points to a terminal state and return the final status dict. `done` → returned; `failed`/`canceled` → raises `GenieJobError` carrying the rollup. `timeout` expiry → `GenieHTTPError` (408); execution is not killed — re-enter `wait()` with no arguments to resume (finished points are skipped). In sequential mode (the default) the timeout is checked between points, so the raise can land later than `timeout` if a point runs long. `progress=True` prints a one-line rollup per step; a callable receives `progress(status)` |
| `results()` | The sweep's result — **the same `SimData` a single run returns**, with one extra leading axis per swept parameter (see below). Reads the point documents; zero HTTP |
| `visualize(mon, field=None)` | Push the sweep's result to the Studio Plot panel — the same verb as `Simulation.visualize`. The panel's axis pickers gain the parameter axes (pick one for a 2-D map, leave the rest as slices). Needs a GUI session; there is no local plotting fallback |
| `status()` | Pure local assembly `{sweep_id, state, dir, rollup, points: [{idx, state, job_id, doc}]}` — **no network call** |
| `rollup()` | Local count dict over per-point states (`{"succeeded": 3, "running": 1, …}`) |
| `cancel()` | Idempotent: in-flight child jobs are cancelled one by one (via each point's live job id), remaining points marked `canceled` |

`on_failure="abort"` semantics (rewritten 2026-10-27): the **first bad
terminal of any kind** (`failed` / `killed` / `archived`) stops
scheduling new points and cancels the in-flight ones; unstarted points
are marked `canceled`. The handle ends `canceled` (unless every point
bad → `failed`).

## Results: one ordinary result with k extra dimensions

A sweep does not invent a second result surface. `h.results()` returns the
same object as `f.results()`; the only difference is that every field array
grew **one leading axis per swept parameter** (k axes for k parameters —
one, two, three, any number up to the 32-axis expansion limit).

```python
r = h.results()

r.tr.T                 # single point  → (n_freq,)
r.tr.T                 # 1 parameter  → (n_pitch, n_freq)
r.tr.T                 # 2 parameters → (n_pitch, n_width, n_freq)
r.tr.T[2]              # one point's original array, exactly as a single run
r.tr.T[1, 0]           # a specific parameter combination (2 params)
r.tr.freqs             # (n_freq,)  — coordinates are NOT replicated
r.pt.Ex.shape          # the point's own trailing dims are unchanged
```

- **Axis order** = the order the axes were declared (Cartesian product, the
  first axis is the outermost loop) — the same order as `h.axis_names` and
  `h.params`.
- **Coordinates** (`freqs` / `time` / `x` / `y` / `z`) stay 1-D: an axis is
  one vector, not N copies. The parameter values are in `h.params` and are
  also recorded in the result's root attribute `sweep`
  (`{"axes": [{"name": …, "values": […]}, …], "grid": […]}`), so the data
  describes its own extra dimensions.
- **Failed points** are NaN slices; the shape never collapses, so index `i`
  always means your `i`-th parameter value.
- **Shape changes**: if the sweep changes the mesh, point arrays no longer
  stack — `results()` raises with the point index and both shapes instead
  of guessing.
- **Scalars**: a digest-like summary is a NumPy reduction on the cube, e.g.
  `np.abs(r.tr.T).max(axis=1)` — there is no separate column table.

`h.visualize("tr", "T")` pushes exactly this result to the Plot panel. The
panel already assigns roles to the available axes: with one parameter the
default view is `freqs` × `pitch` (a 2-D map) and the remaining axes are
slices; pick the parameter as `y` for a map, or slice it to see a single
curve.

## Reading a sweep folder later

`genie.open_sweep(dir)` reads a sweep folder written to disk — on another
machine or in a new process — **without opening any per-point simulation**:

```python
sw = genie.open_sweep("D:/proj/sweeps/pitch-20261030-0217")
sw.axes          # [{"name": "pitch", "linspace": [...]}, …]
sw.params        # {"pitch": array([…])}
sw.rollup()      # {"succeeded": 6}
sw.status()      # local assembly, no network
r = sw.results() # the same cube; sw.visualize("tr", "T") works the same way
```

**Common pitfalls**

- Calling old-surface methods — `manifest()`, `child_id(idx)`,
  `per-point pNNNN/ downloads`, `download(dir)`, `aggregate()`,
  `digests()` and `h.results(i)` do not exist on the current handle. Use
  `h.status()`, `h.results()` and NumPy indexing.
- Taking the `f` after `f.run()`, tweaking parameters, and sweeping —
  the sweep base pulls `to_config()`, i.e. the **current**
  configuration, which is locked after a run; don't run before
  sweeping.
- Assuming one failed point means total failure — under
  `on_failure="continue"` (default) the remaining points run anyway;
  check the `rollup` breakdown.
