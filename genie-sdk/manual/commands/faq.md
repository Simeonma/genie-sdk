# FAQ

> Quick answers to the most common questions, each linking to the authoritative
> page. Everything here is drawn from the reference pages and tutorials — no
> behavior beyond them.

## Setup & basics

### How do I connect to the service?

One form: zero-argument `genie.connect()` (2026-10-29). It rides a live local Studio if one is found, otherwise dials the gateway directly using env / `genie login` credentials. Passing connection parameters raises `GenieConfigError` pointing at `genie login`; on an unconfigured machine the bare call raises the same actionable error — see [00 · Connections](00-connect.html).

```python
import genie
f = genie.connect()   # the only form
```

### Why did tweaking `f` and rerunning resend the old configuration?

A `Simulation` can only be submitted **once** — after `f.run()` the configuration is locked, and parameters passed to `run()` then raise RuntimeError. Create a fresh `genie.connect()` and rebuild via `f.to_script()` (export the script, run it on the new instance) for the next run — see [07 · Submit & lifecycle](07-run.html).

### Why are my lengths wrong by orders of magnitude?

The SDK is SI throughout (meters, Hz, seconds), so `f.mesh.dx = 25` is meters, not nm — write `25e-9`; mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

### How do I check my geometry before running?

Add an index monitor (`f.mon.add_index().name('ix').field('both')`) — it returns the *built* `ε(r), μ(r)` after rasterization, where stair-casing, priority mistakes, and misplaced shapes show up. Also `f.summary()` self-checks completeness pre-submit. See [06 · Monitors](06-mon.html) and [07 · Submit](07-run.html).

## Domain, mesh & boundaries

### What does `cfl` change?

`f.domain.cfl` (default 0.9) scales the time step relative to the 3D stability limit: `Δt = cfl × Δt_max`. It is normally left untouched; lowering it buys stability margin at the cost of more steps for the same `total_time` — see [01 · Domain](01-domain.html) and [Tutorial 1](tut1.html).

### How fine should my grid be?

At least ~10 cells per wavelength in the highest-index material (`dx ≈ λ/(10·n)`), finer near resonances. Set a nominal `f.mesh.dx` and use `f.mesh.refine(..., factor=k)` boxes (integers ≥ 2) around the fine structure — see [02 · Mesh](02-mesh.html).

### Why was my refine region rejected or useless?

The SDK chain API silently truncates a non-integer factor (2.5→2); always pass int — `1.5` becomes 1, i.e. no refinement at all, so pass integers ≥ 2. A region flush against the domain edge wastes cells refining inside the PML; leave 2–3 transition layers — see [02 · Mesh](02-mesh.html).

### Why is my oblique periodic simulation giving wrong physics?

`periodic` is valid only for **normal incidence**; oblique incidence on a repeating structure needs `bloch` boundaries — mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

```python
f.domain.boundary_condition('x_min', 'bloch', from_source=True)
```

### How long should `total_time` be?

Long enough that the observable has happened: a resonance of quality factor Q at f₀ needs `total_time ≳ Q/(π·f₀)` to ring down, and spectral resolution is `Δf ≈ 1/total_time`. A time monitor should confirm that fields have decayed at the end. See [Tutorial 3](tut3.html) and [Tutorial 6](tut6.html).

## Sources

### How do I choose the source bandwidth?

The Gaussian pulse's `bandwidth` (Hz) sets which spectrum is excited: it resolves features coarser than roughly `bw`, and by time–bandwidth duality the pulse lasts `~1/bw` — wide bandwidth = short pulse = short simulation. With the σ = 0.5/bw convention a 120 THz bandwidth pulse has a ≈9.8 fs time-amplitude FWHM. See [05 · Sources](05-src.html) and [Tutorial 5](tut5.html).

```python
f.src.add_plane_wave().freq(300e12).bandwidth(60e12).from_face('z_min')
```

### How should I set the plane-wave incidence angle?

Prefer `.from_face('x_min' …)` — normal incidence, simplest and most reliable. For oblique incidence set `theta`/`phi` (`from_face` is not combined with them, being sugar for normal incidence), and use `bloch` boundaries with a matching `size` window. See [05 · Sources](05-src.html).

### For a Gaussian beam, what does `radius` mean?

It is the **aperture radius** on the source plane, not the beam width: the 1/e² spot is `w ≈ 0.47·radius`, so set `radius ≈ 2.12·w` for a desired `w`. See [05 · Sources](05-src.html).

### Why do my monitors near the source show garbage?

Monitors a few cells from the source plane record incident + scattered fields coherently mixed, and inside the PML fields have no physical meaning — mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

## Monitors & results

### How do I request frequency points on a freq monitor?

Only the three-argument form `.freq(center, bw, n)` works — points `linspace` over `[center−bw/2, center+bw/2]`; a single value or list is rejected. See [06 · Monitors](06-mon.html).

```python
f.mon.add_freq().name('tr').center(0, 0, 500e-9).size(1e-6, 1e-6, 0) \
    .field('all').freq(300e12, 120e12, 200)
```

### Why is my transmission not between 0 and 1?

Raw `|Ẽ|²` is not transmission — only server-computed `f.digest()` numbers marked `normalized=true` on **plane** monitors are physical T; mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

### My spectrum has ringing/sidelobes around a sharp peak. Why?

A finite record convolves the spectrum with a sinc — a truncated ring-down; run longer or apodize — mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

### How do I read the results?

Three paths into the same auto-downloaded h5: `f.get_freq('tr')` (cached raw arrays), `f.results()` (whole-file view), `f.digest()` (server-side numeric summary, no file). The job must be `done`; a wrong name raises `KeyError` with a hint. See [09 · Results](09-results.html) and [Tutorial 6](tut6.html).

```python
fr = f.get_freq('tr');  spec = abs(fr.fields['Ex'])**2
```

## Materials

### How do I fit gold (or any metal)?

Register a library fit: `f.mat.add_from_library('Au', fit_range=(500e-9, 900e-9))` loads bundled n-k data and fits Drude-Lorentz poles client-side (37 materials, works offline; needs scipy). Note `fit_range` is in **meters**. See [03 · Materials](03-mat.html) and [10 · genie.material](10-fit-nk.html).

```python
f.mat.add_from_library('Au', fit_range=(500e-9, 900e-9), n_lorentz=3)
```

### Why can't I just set a negative constant ε for a metal?

A negative constant ε is rejected at submission — it is nondispersive and numerically divergent. The kernel is pole-only: metals must be Drude/Lorentz poles (`add_lorentz` + `add_eps_drude_pole`). See [03 · Materials](03-mat.html) and [Tutorial 4](tut4.html).

### How do I express gain in a material?

Not with negative damping — `gamma < 0` is rejected. The gain channel is a **negative oscillator strength** (Δε < 0 on a Lorentz pole, equivalently ωp² < 0), which the kernel accepts as a bounded pump model. See [Tutorial 4](tut4.html).

### How do geometries decide which material wins where?

Shapes overlap by `priority` — larger wins; equal priority: later in list order wins. Unassigned space is vacuum. "Punch a hole" with a higher-priority background-material patch, not deletion. See [04 · Geometry](04-geom.html) and [Tutorial 2](tut2.html).

## Running & sweeps

### How do I scan a parameter without blocking?

Put the quantity behind a named parameter (`f.parameters({'pitch': 180e-9})`, referenced as `'pitch'` in numeric slots), then `f.sweep(parameters=[{"name": "pitch", "linspace": [...]}])` returns a `SweepHandle` immediately. See [08 · Sweep](08-sweep.html).

```python
h = f.sweep(parameters=[{"name": "pitch", "linspace": [140e-9, 240e-9, 6]}],
            max_parallel=2)
h.wait(progress=True)
```

### How do I scan two parameters together (zip)?

Two ways: `mode="zip"` pairs axis points element-wise, or pass a fully-controlled `sweep` SweepSpec dict — `sweep` and `parameters` are mutually exclusive (giving both raises `ValueError`; pick one). Default `mode="product"` scans the Cartesian product. See [08 · Sweep](08-sweep.html).

### Why was my sweep point skipped / marked failed?

Under the default `on_failure="continue"` failed points become NaN rows and the rest run anyway — mechanisms and diagnostics in [Troubleshooting](troubleshooting.html).

### Why can't I sweep after `f.run()`?

The sweep's base config defaults to `to_config()` — the **current** configuration, which is locked after a run. Build the parameterized configuration and sweep it without running first. Also note sweep axes can only vary **parameter names**, not literals like a `dx` value. See [08 · Sweep](08-sweep.html) and [07 · Submit](07-run.html).

### How do I rerun with a changed parameter?

A submitted instance cannot be mutated. Either export `f.to_script()`, tweak the script, and run it on a fresh `genie.connect()`, or make the quantity a named parameter and use `f.sweep()` with a single-point axis. See [07 · Submit](07-run.html) and [08 · Sweep](08-sweep.html).

## Units & expressions

### How do I convert between wavelength and frequency units?

`f = C0/λ`, and a spectral span converts as `Δf ≈ C0·Δλ/λ²` (e.g. 100 nm at
1550 nm ≈ 12.5 THz; the same Δλ is worth more THz at short wavelengths).
See the quick conversions table in [Quickstart · Units](quickstart.html).

### Can I write `"d/4"` instead of a number?

Yes — every numeric slot accepts an expression string evaluated **server-side**, referencing the global parameter table set by `f.parameters()` (repeated calls merge). Use bare parameter names (`'d/4'`, not `'{d}/4'` — braces are a Python set literal and get rejected); unit suffixes like `'50[nm]'` also work. Referencing an undefined parameter is rejected at the submission boundary, not locally. See [07 · Submit](07-run.html), [00 · Connections](00-connect.html), and [12 · Parameters](12-parameters.html).

```python
f.parameters({'d': 1e-6, 'gap': 'd / 10'})
f.mesh.dx = 'd / 40'
```

### How do I build common structures quickly?

`genie.lib.grating(f, …)` and `genie.lib.ring(f, …)` generate whole geometry node sets in one parameter-driven call (register materials first; `duty` ∈ (0,1) open interval, `n ≥ 1` — violations raise ValueError immediately). See [11 · genie.lib](11-lib.html).
