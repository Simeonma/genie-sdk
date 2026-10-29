# A first simulation, step by step

> This page builds one simulation — a 10-period dielectric grating with a
> plane-wave source and a frequency-domain transmission monitor — one piece
> at a time. By the end, every part of a GENIE configuration, the reason
> for each choice, and the way to read the numbers that come back have been
> covered. The quickstart shows the same script in one glance; this page
> explains it.

A GENIE simulation is a single configuration object, conventionally named `f`.
It is assembled in memory — domain, grid, materials, geometry, source,
monitors — and nothing touches the network until `f.run()` submits the whole
thing at once. The walkthrough follows the same order the config itself uses, and for the
same reason: later pieces refer to earlier ones (geometry references material
names, monitors reference the domain they live in), so the foundation has to
exist first.

## Step 1 · Connect

Everything starts with `genie.connect()`, which returns a `Simulation`
instance — a live configuration object serialized and sent to the cloud only
at submit time. The address is the GENIE service address; the API key travels as an
`X-API-Key` header. Both can come from environment variables (`GENIE_URL`,
`GENIE_API_KEY`), but passing them explicitly makes the script self-contained
and easier to share. One caution worth learning on day one: a `Simulation`
can only be submitted once — re-running an already-submitted instance
raises `RuntimeError`, so call
`genie.connect()` again for a fresh simulation.

```python
import genie

f = genie.connect()   # credentials come from `genie login` (once per machine)
```

## Step 2 · Domain and boundary conditions

The domain is the volume of space the solver actually discretizes. It must be
large enough to contain the structure plus room for the fields to travel —
the grating teeth span roughly 1.8 µm along x, so ±2 µm leaves margin on both
sides — but not larger, because every cell in the box costs GPU memory and
time. The y and z extents (±1 µm) fully contain the teeth, which are 1 µm
wide and 200 nm tall. `total_time` decides how long the pulse evolves; the
server converts it to steps internally. The value must let the wave traverse the structure and the
frequency monitor accumulate a clean DFT; 20 × 10⁻¹⁴ s (200 fs) comfortably
does both for this geometry.

Boundary conditions are set per face, independently. Light enters through
`x_min` and transmits toward `x_max`, so both x faces need absorbing `pml`
boundaries — the entrance face as much as the exit, or reflections from the
source-side wall would contaminate the measurement. Unset faces fall back to
the server default.

![Domain anatomy](figs/fig-domain.svg)

```python
f.domain.x_range = (-2e-6, 2e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 20e-14
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')
```

## Step 3 · Grid

The FDTD grid breaks the domain into cells of size dx × dy × dz, and the grid
spacing sets both accuracy and cost: halving a spacing roughly doubles the
cell count on that axis and tightens the stability-limited time step. So
resolution goes where the fields vary fast, and is saved elsewhere.

The grating pitch is 180 nm with 90 nm teeth — the geometry changes on a
~90 nm scale along x, so 15 nm resolves each tooth edge with several cells.
The field variation along y and z is much gentler, and 30 nm suffices there.
Setting `dy`/`dz` falls back to `dx` if left unset; here all
three are set explicitly to make the anisotropic choice visible. Note that everything
is SI: `15e-9` means 15 nanometers. Passing `dx = 25` would make the domain
orders of magnitude smaller than the grid and be rejected by the `min_cells`
guard.

If a structure demanded extra resolution only in one region, `f.mesh.refine()`
stacks a box-shaped refinement on top; it is not needed here, but it is the
tool to reach for when a single global spacing would be wasteful.

![Mesh refinement](figs/fig-mesh.svg)

```python
f.mesh.dx = 15e-9
f.mesh.dy = 30e-9
f.mesh.dz = 30e-9
```

## Step 4 · Materials

Materials live in a registry on `f.mat`, and geometry refers to them **by
name** — so registration must precede any `add_box().material(...)`. This
indirection is deliberate: one registered material can fill many geometry
objects, and changing the material means editing one line, not every shape.
For a dielectric grating, a constant (non-dispersive) material is the right
model: silicon dioxide's permittivity barely varies over the source bandwidth,
so a single ε carries the physics. The grating works by index contrast — a
higher-ε tooth against a lower-ε background (the default vacuum background)
produces partial reflection at each interface; the periodic stack makes the
reflections interfere, and the pitch selects which wavelengths interfere
constructively. That contrast is the entire device, which is why ε = 2.1 is
all the material physics this simulation needs. Only when dispersion matters
(metals, resonances) do `add_lorentz` or `add_from_library` apply.

```python
f.mat.add_const().name('sio2').eps(2.1)
```

## Step 5 · Geometry

The grating is ten identical teeth, each a box 90 nm long (along x), 1 µm
wide (y), and 200 nm tall (z), starting at x = −1.6 µm and repeating with a
180 nm pitch. A plain `for` loop stamps them out: in GENIE, one loop pass is
one execution is one geometry node, so ten iterations produce ten boxes —
parameterized repetition is part of the scripting model, not a separate
mechanism. `add_box` is positioned by min/max ranges on all three axes (all
six values required, unlike the other shapes which use `.center()`). Keeping
the teeth inside the y/z bounds chosen in step 2 means the structure never
touches the domain edge. Overlapping shapes are resolved by `priority`
(larger wins); our teeth do not overlap, so the default priority is fine.

![Geometry factories](figs/fig-shapes.svg)

```python
N_TEETH, PITCH = 10, 180e-9
for i in range(N_TEETH):
    x = -1.6e-6 + i * PITCH
    f.geom.add_box().material('sio2') \
        .x_range(x, x + 90e-9) \
        .y_range(-5e-7, 5e-7) \
        .z_range(-1e-7, 1e-7)
```

## Step 6 · Source

A simulation needs at least one source (the config enforces this at submit).
For measuring how a structure transmits a plane wave, `add_plane_wave` is the
match: it injects with TFSF (total-field/scattered-field) logic, so the wave
is one-way by construction and scatterers placed inside the total-field
region behave correctly. The two required waveform parameters are the Gaussian
envelope's center frequency and bandwidth: 330 THz (a wavelength near 900 nm
in vacuum) with a 60 THz bandwidth gives the monitor something to integrate
over a range of wavelengths. `.from_face('x_min')` places the wave on the
inner side of that face at normal incidence and is the recommended way to aim
a plane wave — simpler and more reliable than hand-tuning `theta`/`phi`. The
optional `.name('pw')` costs nothing and keeps the run traceable. One
placement rule to carry forward: keep monitors away from the TFSF source
plane, where readings mix incident and scattered fields.

![Source types](figs/fig-sources.svg)

```python
f.src.add_plane_wave().freq(3.3e14).bandwidth(6e13) \
    .from_face('x_min').name('pw')
```

## Step 7 · Monitors

Monitors record fields without disturbing the simulation. The transmission
spectrum is measured by `add_freq`, the frequency-domain recorder: it takes
the DFT of the time signal at each of its frequency points and yields the
complex amplitude — the workhorse for transmission and reflection spectra.
Placement follows the physics. The monitor sits just past the last tooth
(x = 1.7 µm) so it sees only transmitted light; it is a thin volume monitor
(200 nm along x) spanning the full tooth aperture in y and z, so it integrates the
whole beam, not a sample of it. Because it is a volume monitor, the
digest's physical T — reported only for plane monitors — does not apply
here; compare field amplitudes rather than expecting a normalized T.
`.field('all')` records every component,
and `.freq(3.3e14, 1.2e14, 300)` asks for 300 points spread by `linspace`
over [270, 390] THz — that triple is the required form; a single frequency
or list is rejected. Always name monitors: the name is the addressing
key when fetching results. Two placement rules: never place a monitor inside
the PML, where fields have no physical meaning, and remember that a physical
transmittance T requires a plane monitor plus server-side normalization.

![Monitor placement](figs/fig-monitors.svg)

```python
f.mon.add_freq().name('tr') \
    .center(1.7e-6, 0, 0).size(2e-7, 1.2e-6, 1.2e-6) \
    .field('all').freq(3.3e14, 1.2e14, 300)
```

## Step 8 · Run and read results

`f.run()` is the synchronous entry point: it serializes the config,
prechecks it locally, submits, and polls until a terminal state — then
automatically downloads the results to `<job_id>_results.h5` under
`$GENIE_RESULT_DIR` (default `./genie_results/`). A failure raises
`GenieJobError` rather than returning quietly.

Three retrieval paths cover the data: `f.get_freq('tr')` returns the raw
frequency and complex-field arrays, `f.results()` opens the whole h5 file as
one object, and `f.digest()` prints a server-computed numeric summary with
zero GPU reads — the cheapest first check of a tuning round. Full semantics
for all three are documented in [Results](09-results.html).

```python
f.run()

fr = f.get_freq('tr')          # raw arrays: fr.freqs (Hz), fr.fields
T  = abs(fr.fields['Ex'])**2   # caller-owned normalization/plotting

data = f.results()             # full h5 view: data.tr.Ex, data.attrs
print(f.digest())              # numeric summary (server-computed, zero-GPU)
```


## The complete script

Stripping comments from the blocks above reproduces the Quickstart script verbatim — see the [Quickstart](quickstart.html).

From here, parameterize the pitch and scan it with `f.sweep()`, or add metals
via `add_lorentz` / `add_from_library` — see the page table in the
[quickstart](quickstart.md) for where each topic lives.
