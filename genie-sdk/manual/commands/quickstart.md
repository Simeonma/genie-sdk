# Quickstart

> A complete no-wizard, copy-paste starter: one grating transmission simulation,
> from `connect()` to a plot, in about 30 lines. Every step links to the
> full reference.

## Install

```bash
# GitHub direct (if raw.githubusercontent.com is reachable)
pip install https://github.com/Simeonma/genie-sdk/raw/dist/dist/genie_sdk-2.0.0a8-py3-none-any.whl

# CDN mirror (recommended on CN networks): same wheel via jsDelivr
pip install https://cdn.jsdelivr.net/gh/Simeonma/genie-sdk@dist/dist/genie_sdk-2.0.0a8-py3-none-any.whl
```

The URLs above carry the current release filename; check the Releases page for
the latest filename before installing a newer version.

Distribution name `genie-sdk`; import name `genie` (`import genie`).

Version list: [Releases](https://github.com/Simeonma/genie-sdk/releases)
(wheels also on the `dist` branch). Python ≥ 3.10;
`requests / numpy / pydantic / h5py` install automatically.

One install has everything (2026-10-29): Studio GUI, material
fitting, uploads — all included. Real optional extras: `genie-sdk[agent]`
MCP tool surface · `genie-sdk[dev]` pytest.

Where do the URL and credentials come from? The service address and the
account come from whoever operates the GENIE deployment being used — there
is no public signup.

Post-install sanity check:

```bash
python -c "import genie; print(genie.__version__)"
```

Where do files go? One machine-level workspace holds the scripts, result
files, and the auto-saved `.gensim` documents:

```bash
genie workspace              # show the current workspace (and recent ones)
genie workspace D:\proj      # set it — everything afterwards lives there
genie studio                 # GUI: File → Workspace… picks the same setting
```

Unset, the workspace defaults to `~/data/genie/scripts`; result files land
under `<workspace>/<owner>/genie_results/`, and after every successful run
the current simulation is auto-saved to `<workspace>/<owner>/<session>.gensim`
(no manual save needed). Opening a `.gensim` in Studio (`File → Open…`)
also **switches the workspace to that file's folder** and makes the opened
file the auto-save target — so a run updates the document you opened.

## The whole script

This is a real, runnable simulation — a 10-period dielectric grating with a
plane-wave source and a frequency-domain transmission monitor:

```python
import genie

f = genie.connect()   # credentials come from `genie login` (once per machine)

# 1 · physical domain (SI units throughout)
f.domain.x_range = (-2e-6, 2e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 20e-14
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')

# 2 · grid
f.mesh.dx = 15e-9
f.mesh.dy = 30e-9
f.mesh.dz = 30e-9

# 3 · material
f.mat.add_const().name('sio2').eps(2.1)

# 4 · geometry: 10 grating teeth
N_TEETH, PITCH = 10, 180e-9
for i in range(N_TEETH):
    x = -1.6e-6 + i * PITCH
    f.geom.add_box().material('sio2') \
        .x_range(x, x + 90e-9) \
        .y_range(-5e-7, 5e-7) \
        .z_range(-1e-7, 1e-7)

# 5 · source
f.src.add_plane_wave().freq(3.3e14).bandwidth(6e13) \
    .from_face('x_min').name('pw')

# 6 · monitor
f.mon.add_freq().name('tr') \
    .center(1.7e-6, 0, 0).size(2e-7, 1.2e-6, 1.2e-6) \
    .field('all').freq(3.3e14, 1.2e14, 300)

# 7 · submit and wait
f.run()
```

## What each step does

| Step | Page | In one line |
|---|---|---|
| domain + PML | [01 · f.domain](01-domain.html) | Simulation box, total time, absorbing boundaries on x_min/x_max |
| mesh | [02 · f.mesh](02-mesh.html) | 15 nm fine axis along the grating, 30 nm elsewhere |
| material | [03 · f.mat](03-mat.html) | Register `sio2` (ε=2.1) — geometry references it by name |
| geometry | [04 · f.geom](04-geom.html) | A `for` loop stamps out N boxes — N executions, N nodes |
| source | [05 · f.src](05-src.html) | Plane wave from face `x_min`, centered 330 THz |
| monitor | [06 · f.mon](06-mon.html) | Frequency monitor `tr` behind the grating, 300 points |
| run | [07 · f — submit & lifecycle](07-run.html) | Submit + poll; results auto-download on success |

## Read the results

`f.run()` returns after the job reaches a terminal state and the result file
has been auto-downloaded. Three ways in:

```python
fr = f.get_freq('tr')        # raw arrays: fr.freqs (Hz), fr.fields
T  = abs(fr.fields['Ex'])**2 # normalization/plotting is up to the analysis code

data = f.results()           # full h5 view: data.tr.Ex, data.attrs

print(f.digest())            # numeric summary (server-computed, zero-GPU)
```

See [09 · results](09-results.html) for the full data surface.

## Units and quick conversions

The SDK is SI throughout (meters, Hz, seconds). Wavelength ↔ frequency is
`f = C0/λ` with `C0 = 299792458 m/s`; vacuum impedance `ETA0 ≈ 376.73 Ω`
for reference. Quick lookup:

| λ | f = C0/λ |
|---|---|
| 500 nm | ≈ 600 THz |
| 800 nm | ≈ 375 THz |
| 1310 nm | ≈ 229 THz |
| 1550 nm | ≈ 193 THz |

Bandwidth conversion for a spectral span centered at λ:

```
Δf ≈ C0 · Δλ / λ²
```

Example: `Δλ = 100 nm` at `λ = 1550 nm` →
`Δf ≈ 3e8 × 100e-9 / (1550e-9)² ≈ 12.5 THz`. Note the `1/λ²` scaling: the
same `Δλ` at the short-wavelength end (say 500 nm) corresponds to a much
larger `Δf` (~120 THz for 100 nm) — check `bandwidth`/monitor spans in
frequency, not just in nanometers.

## Where to go next

Pages 00–12 are lookup reference, not sequential reading — follow the step
table above and jump to a page as needed.

- Parameterize the pitch and scan it: [08 · sweep](08-sweep.html)
- Metals and dispersion: [03 · f.mat](03-mat.html)
  (`add_lorentz`, `add_from_library`)
- Ready-made structures (gratings, rings): [11 · genie.lib](11-lib.html)
