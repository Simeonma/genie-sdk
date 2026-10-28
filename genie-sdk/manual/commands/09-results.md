# 09 · results (get_* & SimData)

> Three ways to retrieve results: `get_time/get_freq/get_index` (raw data,
> cached), `results()` (full h5 view + plotting), and `digest()` (numeric
> summary, see [Submit & lifecycle](#f-submit-lifecycle)). Prerequisite: the job state must be
> `done`. Since G170 the digest is carried inside the result h5 itself
> (root attrs), so one downloaded file = waveforms + axes + summary metrics;
> `digest()` reads the local file first (zero extra HTTP) and falls back to
> the gateway endpoint for older result files.

## f.get_time() / get_freq() / get_index()

Fetch the raw result object by monitor name; name omitted = first monitor
of that type. Cached — repeated calls cost no extra downloads.

| Command | Returns |
|---|---|
| `f.get_time(name=None)` | `TimeResult` from a time monitor |
| `f.get_freq(name=None)` | `FreqResult` from a frequency monitor |
| `f.get_index(name=None)` | `IndexResult` from an index monitor |

```python
fr = f.get_freq('tr')          # frequency monitor tr
fr.freqs                       # frequency array (Hz)
fr.fields                      # {field name: ndarray}
```

**Common pitfalls**

- Job not `done` → RuntimeError with the job log tail; wrong monitor name →
  `KeyError` with a name hint.

## The three Result objects

Shared surface: `.comps` (component list), `.keys()` (all data keys),
`.dict()` → `{key: np.ndarray}` for custom post-processing.

| Class | From | Extra attributes |
|---|---|---|
| `TimeResult` | time monitor | `.time` (time axis, s) |
| `FreqResult` | freq monitor | `.freqs` (frequency axis, Hz); fields are complex ndarrays |
| `IndexResult` | index monitor | eps/mu distribution (real) |

## f.results()

Returns: `SimData`, a wrapper over the h5 file. The grouped view it
exposes (`data.tr.Ex` — see below) is a convenience surface; the
underlying file layout follows the flat-key protocol described in
[The h5 key protocol](#the-h5-key-protocol) below. Prefers the file run() saved locally, otherwise
fetches on the spot; `job_id` omitted = this instance's latest job (any
historical job can be revisited).

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `job_id` | str | no | latest job | Job whose results to pull |

| Access | Gives |
|---|---|
| `data.monitors` | list of monitor names, e.g. `['tr', 'pt', …]` |
| `data.attrs` | root attributes: executed_steps / dt / grid_shape / format |
| `data.digest` | numeric summary dict carried in the h5 root attrs (`None` on older result files — fall back to `digest()`) |
| `data.tr.Ex` | attribute access → lazy h5py Dataset (slice it, or use `[()]` for the ndarray) |
| `data["tr"]["Ex"]` | subscripting → ndarray directly |
| `data.tr.keys()` | all dataset names for that monitor |

For figures, take the ndarray (`data["tr"]["Ex"]`) and use any Python
plotting library you like — or push the monitor to the Studio **Plot
panel** with `f.visualize` (below). The SDK ships no plotting code of
its own: interactive plotting lives in the Studio GUI by design (zero
matplotlib on the SDK side; the old `data.plot` was removed — R36).

## f.visualize()

Push a monitor's result to the Studio **Plot panel** — the interactive
result browser (1D curves with any axis as x, 2D cross-sections with any
two axes as x/y, slice pickers for the remaining axes, hover readouts,
zoom). Two trigger paths, one experience:

- **Inside a Studio session** (script editor / terminal): the session wires
  `f.visualize` for you.
- **Outside** (your own terminal, bare `genie.connect()`): the result h5 is
  uploaded to your session and the Plot panel opens automatically.

```python
f.run()
f.visualize("tr")            # default field
f.visualize("tr", "Ex")      # explicit field component
```

Requires a GUI session (open `genie studio` first); there is no local
plotting fallback by design — for custom figures, read the ndarrays from
`results()` and plot them yourself.

**Common pitfalls**

- Slice lazy h5py reads wherever possible (`data.tr.Ex[:, 0]`) instead of
  pulling the full array first.
- Comparing result files across parameter sweeps — use `digest()` (see
  page 07).

See also　Monitor definitions: [Monitors](#f-mon-monitors); auto-save paths: [f.run](#f-submit-lifecycle).

## The h5 key protocol

On disk the result file
(`genie-results-h5/1`, written by the compute kernel's driver) uses
**groups** — one group per monitor, one dataset per field/axis:

- Group name = monitor name (matches `cfg.monitor` entries);
- Group attribute `domain` ∈ `f` / `t` / `i` (frequency / time / index);
- Datasets inside the group = field components and axes (`Ex`, `time`,
  `freqs`, `x/y/z`, …);
- Root attributes: `format`, `executed_steps`, `dt`, `grid_shape`, and
  `monitors` (a JSON manifest with per-monitor `shape` metadata).

The **flat keys** `t@名.time` / `f@名.freqs` … you may see in the SDK's
in-memory plumbing (`get_time`/`get_freq` flatten the groups into a
`<domain>@<monitor>.<field>` dict before building the result objects) are
an **in-memory intermediate**, not the on-disk layout. Reading the file
directly with h5py means iterating groups and their datasets.

**Index-monitor data shape.** `i@名.eps` / `i@名.mu` is a **flat
per-cell list** (1-D, one value per grid cell) accompanied by
equal-length per-cell coordinate arrays `x`, `y`, `z` — not axis
ticks. To reconstruct a 3-D array, take `np.unique` on each
coordinate array and reshape: the unique counts give the grid
dimensions. Sanity anchors from a real run: vacuum n = 1.0000, SiO₂
n = 1.449 = √2.10.

**Axis-order pitfall (a common mistake).** The packed eps cube is
laid out in `(z, y, x)` order while the coordinate arrays are listed
as `x, y, z`. A reshape that assumes the coordinate order silently
transposes the structure — a correct build then *reads back as
wrong*, which is worse than a crash. Avoid by querying cells by
nearest-neighbor coordinate lookup, or by pinning the axis order
explicitly before any reshape.

**Time vs freq consistency.** On a healthy run the two routes agree to
within ~1%; a larger discrepancy points at monitor configuration
(sampling window, position mismatch) first, not the solver.
