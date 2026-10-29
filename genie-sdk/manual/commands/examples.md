# Example library

> Every example below is a complete runnable script. Each one states the physics it
> demonstrates and what to check in the output. All units are SI; the command surface
> is exactly the one documented on pages 00–09 and 11 (plus `genie.lib`).

## Grating coupler (baseline) — extras: none

A uniform dielectric grating under a normally incident plane wave is the
"hello world" of FDTD: periodic high-index teeth form a 1D photonic band
structure, and the transmission spectrum shows band-edge ripples and
suppression where the Bragg condition `2·(n_line·d + n_gap·(Λ − d)) = m·λ`
is met inside the stack. It shows the full connect → domain → mesh → mat →
geom → src → mon → run → get_freq loop, and how a period-`Λ` structure
shapes a spectrum. The teeth are stamped by the `genie.lib.grating` helper
(documented in [11 · genie.lib](11-lib.html))
instead of a hand-written loop.

```python
import genie
from genie.lib import grating

f = genie.connect()   # credentials come from `genie login` (once per machine)

# 1 · domain: light travels along x; PML on both entrance/exit faces
f.domain.x_range = (-2.5e-6, 2.5e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 4e-13
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')

# 2 · grid: fine along the propagation axis
f.mesh.dx = 15e-9
f.mesh.dy = 30e-9
f.mesh.dz = 30e-9

# 3 · materials: Si-like teeth (n≈3.48 → eps≈12.1) in air
f.mat.add_const().name('si').eps(12.1)

# 4 · geometry: 20 uniform periods, Λ = 220 nm, duty 0.5
grating(f, material='si',
        x_range=(-2.2e-6, 2.2e-6),
        y_range=(-5e-7, 5e-7), z_range=(-1.1e-7, 1.1e-7),
        n=20, pitch=220e-9, duty=0.5)

# 5 · source: plane wave from x_min, centered 200 THz (λ₀ = 1.5 µm)
f.src.add_plane_wave().freq(200e12).bandwidth(60e12) \
    .from_face('x_min').polarization(0, 0, 1).name('pw')

# 6 · transmission monitor behind the grating
f.mon.add_freq().name('tr') \
    .center(2.3e-6, 0, 0).size(2e-7, 1.2e-6, 1.2e-6) \
    .field('all').freq(200e12, 80e12, 300)

# 7 · submit and read
f.run()
print(f.digest())
fr = f.get_freq('tr')
T = abs(fr.fields['Ez'])**2
```

**What to look for**

- Band-edge ripples: the first stopband-like dip sits near the Bragg wavelength
  `λ_B = 2·(n_eff·d + n_gap·(Λ − d))` — with n_eff ≈ √12.1 ≈ 3.48, d = Λ/2, expect it
  deep in the IR, so inside the scan window only Fabry–Pérot-style
  fringes with period `Δf = c/(2·n_eff·L)`.
- An index monitor (`f.mon.add_index().name('ix').field('eps')`) confirms all 20 teeth
  landed at the intended pitch before trusting the spectrum.
- `f.digest()` reports transmittance min/max only when the monitor is a plane monitor
  with `normalized=true`; use it for quick tuning-round comparisons.

## Ring resonator add-drop filter — extras: none

A microring side-coupled to a straight bus waveguide is the canonical Lorentzian
resonator: light circulating in the ring interferes constructively only when the
round-trip phase closes, i.e. at resonances `2πR·n_eff = m·λ`. On resonance the
through port shows a dip (power is diverted) and the drop port shows a peak; the
resonance spacing is the free spectral range `FSR = c/(2πR·n_g)`. The example
shows how to combine two `genie.lib.ring` calls (bus slab + ring), use two frequency monitors,
and read two-port spectra against a closed-form formula.

Expected magnitude warning: plane-wave excitation of a 220 nm bus couples
weakly — expect small through/dip contrast. The example demonstrates
structure, not efficient excitation (a mode source or eigenmode injection
would be the efficient route).

![Ring resonance](figs/ex-ring.svg)

```python
import genie
from genie.lib import ring

f = genie.connect()

# 1 · domain: ring plane = x–y, PML on x and y faces, periodic in z
f.domain.x_range = (-3e-6, 3e-6)
f.domain.y_range = (-1.5e-6, 1.5e-6)
f.domain.z_range = (-6e-7, 6e-7)
f.domain.total_time = 1e-12          # long: high-Q ring rings for many round trips
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')
f.domain.boundary_condition('y_min', 'pml')
f.domain.boundary_condition('y_max', 'pml')
f.domain.boundary_condition('z_min', 'periodic')
f.domain.boundary_condition('z_max', 'periodic')

# 2 · grid
f.mesh.dx = 20e-9
f.mesh.dy = 20e-9
f.mesh.dz = 20e-9

# 3 · materials: silicon ring, silica-like surroundings (n≈1.45 → eps 2.1)
f.mat.add_const().name('si').eps(12.1)
f.mat.add_const().name('sio2').eps(2.1)

# 4 · geometry: straight bus waveguide (solid-disk-free box) + ring
f.geom.add_box().material('si') \
    .x_range(-2.8e-6, 2.8e-6).y_range(-0.95e-6, -0.55e-6).z_range(-0.11e-6, 0.11e-6)
ring(f, material='si', outer_radius=1.5e-6, inner_radius=1.24e-6,
     height=0.22e-6, center=(0.0, 0.3e-6, 0.0))     # R = 1.37 µm mid-radius

# 5 · source: bus-fed plane wave along +x
f.src.add_plane_wave().freq(193.5e12).bandwidth(20e12) \
    .from_face('x_min').name('pw')

# 6 · two-port monitors: through (bus exit) and drop (ring port)
f.mon.add_freq().name('through') \
    .center(2.6e-6, -0.75e-6, 0).size(2e-7, 5e-7, 3e-7) \
    .field('all').freq(193.5e12, 16e12, 400)
f.mon.add_freq().name('drop') \
    .center(0.0, 1.3e-6, 0).size(5e-7, 2e-7, 3e-7) \
    .field('all').freq(193.5e12, 16e12, 400)

# 7 · submit and read
f.run()
th, dr = f.get_freq('through'), f.get_freq('drop')
T_through = abs(th.fields['Ex'])**2
T_drop = abs(dr.fields['Ez'])**2
```

**What to look for**

- Lorentzian dips in the through spectrum and complementary peaks in the drop
  spectrum; the resonance spacing should match `FSR = c/(2πR·n_g)` — with
  R = 1.37 µm and n_g ≈ 3.5 that is ≈ 10 THz, so the 16 THz scan window shows
  one to two resonances.
- Resonance wavelengths satisfy `2πR·n_eff = m·λ` (m integer); shifting R by ΔR moves
  each resonance by `Δλ ≈ λ·ΔR/R`.
- Dips that fail to reach zero indicate weak coupling (bus–ring gap too large for
  critical coupling at this grid resolution) or insufficient total time to resolve a
  narrow ring-down.

## Distributed Bragg reflector — extras: none

A DBR is a stack of alternating quarter-wave ε layers: partial reflections from each
interface add in phase when the optical round trip per pair is half a wave,
`n1·d1 + n2·d2 = λ/2`, giving a stopband centered at `λ = 2·(n1·d1 + n2·d2)/m`.
Reflectivity grows with pair count N roughly as `R ∝ [(n1/n2)^(2N)]²` band-center.
It shows the small `for` loop of `add_box` (one pass = one geometry node) and how a
frequency monitor on the reflection side exposes the stopband directly.

![Bragg stopband](figs/ex-bragg.svg)

```python
import genie

f = genie.connect()

# 1 · domain: stack along x, PML both ends
f.domain.x_range = (-4e-6, 4e-6)
f.domain.y_range = (-6e-7, 6e-7)
f.domain.z_range = (-6e-7, 6e-7)
f.domain.total_time = 6e-13
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')

# 2 · grid
f.mesh.dx = 15e-9
f.mesh.dy = 30e-9
f.mesh.dz = 30e-9

# 3 · two const materials (n1 ≈ 2.10 → eps 4.4; n2 ≈ 1.45 → eps 2.1)
f.mat.add_const().name('h').eps(4.4)
f.mat.add_const().name('l').eps(2.1)

# 4 · geometry: 12 quarter-wave pairs centered at λ₀ = 1.55 µm
#     d1 = λ₀/(4·n1) ≈ 185 nm, d2 = λ₀/(4·n2) ≈ 267 nm
D1, D2 = 185e-9, 267e-9
x = -2.5e-6
for i in range(12):
    f.geom.add_box().material('h') \
        .x_range(x, x + D1).y_range(-5e-7, 5e-7).z_range(-5e-7, 5e-7)
    x += D1
    f.geom.add_box().material('l') \
        .x_range(x, x + D2).y_range(-5e-7, 5e-7).z_range(-5e-7, 5e-7)
    x += D2

# 5 · source: plane wave from x_min hitting the stack
f.src.add_plane_wave().freq(193.5e12).bandwidth(60e12) \
    .from_face('x_min').name('pw')

# 6 · reflection-side monitor: between source and stack (a few cells clear of TFSF)
f.mon.add_freq().name('refl') \
    .center(-2.8e-6, 0, 0).size(2e-7, 8e-7, 8e-7) \
    .field('all').freq(193.5e12, 70e12, 400)

# 7 · submit and read
f.run()
print(f.digest())
fr = f.get_freq('refl')
R = abs(fr.fields['Ex'])**2
```

**What to look for**

- A deep stopband centered near `λ = 2·(n1·d1 + n2·d2)` = 2·(2.10·185 + 1.45·267) nm
  ≈ 1.55 µm (m = 1), with the band edges where the phase mismatch opens up.
- Band-center reflectivity rising steeply with pair count — rerun with 6 vs 12 pairs
  and compare `digest()` transmittance/min values; R scales like `[(n1/n2)^(2N)]²`.
- Ripple period outside the band follows the total stack thickness
  `Δf ≈ c/(2·n_avg·L)`; monitor placement a few cells away from the source plane keeps
  incident/scattered mixing out of the reading.

## Dielectric metasurface unit cell (oblique Bloch) — extras: none

One disk per unit cell with Bloch-periodic transverse boundaries is the standard
metasurface building block: the disk acts as a truncated waveguide mode with
Mie-like electric/magnetic dipole resonances. Under oblique incidence the
transverse phase advance is set by the Bloch wave vector, requiring
`bloch` boundaries (never `periodic`) with `k = (2π/λ)·n·sin θ` on the in-plane
faces, with the plane-wave `theta` set to match. It introduces the `add_cylinder`
shape, `bloch` + `from_source` bookkeeping, and how transmission shifts with
incidence angle.

![Metasurface angular T](figs/ex-meta.svg)

```python
import genie

THETA_DEG = 20.0
LAMBDA0 = 1.55e-6
FREQ0 = 3e8 / LAMBDA0

f = genie.connect()

# 1 · domain: propagation along z; bloch in x (in-plane sweep), periodic in y
f.domain.x_range = (-4e-7, 4e-7)          # one unit cell, pitch = 800 nm
f.domain.y_range = (-4e-7, 4e-7)
f.domain.z_range = (-1.2e-6, 1.2e-6)
f.domain.total_time = 4e-13
f.domain.boundary_condition('x_min', 'bloch', from_source=True)
f.domain.boundary_condition('x_max', 'bloch', from_source=True)
f.domain.boundary_condition('y_min', 'periodic')
f.domain.boundary_condition('y_max', 'periodic')
f.domain.boundary_condition('z_min', 'pml')
f.domain.boundary_condition('z_max', 'pml')

# 2 · grid
f.mesh.dx = 12e-9
f.mesh.dy = 12e-9
f.mesh.dz = 12e-9

# 3 · materials: amorphous-Si-like disk (n≈3.48) in air
f.mat.add_const().name('a_si').eps(12.1)

# 4 · geometry: single disk unit cell
f.geom.add_cylinder().material('a_si') \
    .center(0, 0, 0).radius(180e-9).height(400e-9)

# 5 · source: oblique plane wave from z_min; theta drives the bloch k
f.src.add_plane_wave().freq(FREQ0).bandwidth(40e12) \
    .from_face('z_min').theta(THETA_DEG).name('pw')

# 6 · transmission monitor behind the disk
f.mon.add_freq().name('tr') \
    .center(0, 0, 0.9e-6).size(7e-7, 7e-7, 0) \
    .field('all').freq(FREQ0, 50e12, 300)

# 7 · submit and read
f.run()
print(f.digest())
fr = f.get_freq('tr')
T = abs(fr.fields['Ex'])**2 + abs(fr.fields['Ey'])**2
```

**What to look for**

- A dipole resonance dip whose wavelength shifts with angle: rerun with
  `THETA_DEG = 0, 10, 20` and compare `digest()` — the shift follows the in-plane
  momentum `k = (2π/λ)·n·sin θ` delivered to the bloch faces via `from_source=True`.
- The resonance stays Lorentzian but weakens with tilt (dipole overlap drops); the
  transmission peak between dips is the leaky-mode background.
- If explicit k is preferred instead of `from_source`, set
  `f.domain.boundary_condition('x_min', 'bloch', k=(2π/1.55e-6·sin θ, 0, 0))` and keep
  the source `theta` identical — a mismatch shows up as a wrong-periodicity spectral
  artifact.

## Gold nanoparticle scattering — extras: `genie-sdk[fit]`

A sub-wavelength gold sphere in water supports a localized surface plasmon: the
Drude-like ε(ω) of Au (fitted from the bundled n-k library) makes the polarizability
`α = 4πR³(ε_m − ε_d)/(ε_m + 2ε_d)` resonate where `Re ε_m ≈ −2ε_d`, giving a strong
dipole-like extinction peak near 530 nm in water (ε_d ≈ 1.77). The example uses
`add_from_library('Au')` — the library fit runs client-side and registers a pole
material — plus the TFSF plane wave and how `digest()`/`get_freq` serve as a
cross-section proxy.

```python
import genie

f = genie.connect()

# 1 · domain: compact box, PML everywhere the sphere can radiate into
f.domain.x_range = (-6e-7, 6e-7)
f.domain.y_range = (-6e-7, 6e-7)
f.domain.z_range = (-6e-7, 6e-7)
f.domain.total_time = 3e-13
for face in ('x_min', 'x_max', 'y_min', 'y_max', 'z_min', 'z_max'):
    f.domain.boundary_condition(face, 'pml')

# 2 · grid: fine near the 40 nm sphere and its plasmon near-field
f.mesh.dx = 4e-9
f.mesh.dy = 4e-9
f.mesh.dz = 4e-9
f.mesh.refine(x_range=(-1e-7, 1e-7), y_range=(-1e-7, 1e-7),
              z_range=(-1e-7, 1e-7), factor=2)

# 3 · materials: Au from the bundled n-k library, water background
f.mat.add_from_library('Au', fit_range=(450e-9, 800e-9), n_lorentz=2)
f.mat.add_const().name('water').eps(1.77)

# 4 · geometry: R = 40 nm Au sphere in water
f.geom.add_sphere().material('Au').center(0, 0, 0).radius(40e-9)
f.geom.add_box().material('water') \
    .x_range(-6e-7, 6e-7).y_range(-6e-7, 6e-7).z_range(-6e-7, 6e-7).priority(-1)

# 5 · source: plane wave from z_min, λ₀ ≈ 532 nm
f.src.add_plane_wave().freq(5.64e14).bandwidth(2e14) \
    .from_face('z_min').name('pw')

# 6 · forward-scattering monitor behind the sphere
f.mon.add_freq().name('fwd') \
    .center(0, 0, 4.5e-7).size(4e-7, 4e-7, 0) \
    .field('all').freq(5.64e14, 1.5e14, 300)

# 7 · submit and read
f.run()
print(f.digest())                     # peak frequency ≈ plasmon line
fr = f.get_freq('fwd')                # cross-section proxy:
sigma_proxy = abs(fr.fields['Ex'])**2 + abs(fr.fields['Ey'])**2
```

**What to look for**

- A forward-intensity maximum near 530 nm — the dipole plasmon where
  `Re ε_Au(ω) ≈ −2·ε_d`; shrinking R slightly blueshifts it, growing R redshifts and
  broadens (retardation quadrupole onset).
- The extinction-proxy peak from `get_freq` should line up with the
  `digest()` peak-frequency entry for monitor `fwd`; comparing both is a cheap
  sanity check before any finer post-processing.
- The fit quality is visible in the registered poles: `f.summary()` after the library
  fit shows the lorentz node Au expanded into; if the peak looks displaced, widen
  `fit_range` or raise `n_lorentz`.
