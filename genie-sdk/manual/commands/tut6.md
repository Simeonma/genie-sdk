# 6 · Reading results

> The simulation ran; a results file is now in hand. This page explains what is
> in it, what each quantity means physically, and the three mistakes that
> most often produce wrong numbers from a healthy simulation.

The solver does not make judgment calls; it faithfully integrates
whatever configuration it was given and hands back the fields it
computed. The judgment happens now, in how the results are read. Most
wrong numbers in FDTD practice come not from solver bugs but from three
well-known reading errors (a truncated ring-down, a monitor in the
wrong place, and raw amplitudes mistaken for physical transmission),
and all three are detectable from the results file itself once it is known
where to look. This page builds that skill and ends with a checklist
that turns it into routine.

## What comes back

`f.run()` auto-downloads a result file (HDF5, `genie-results-h5/1`) into
`$GENIE_RESULT_DIR` (default `./genie_results/`). Its structure mirrors
the monitor list — one entry per monitor, one key per field or axis:

```
/tr/Ex, /tr/Ey, /tr/Ez, …        # dataset per requested field (h5 group /tr)
/tr/freqs                        # axis: freqs (freq monitor) | /tr/time (time monitor)
/ix/eps, /ix/mu                  # index monitor: built ε(r), μ(r)
```

On disk the file (`genie-results-h5/1`) is **grouped** — one h5 group per
monitor (name = monitor name, group attr `domain` = `t`/`f`/`i`), one
dataset per field/axis; root attrs carry `format` / `executed_steps` /
`dt` / `grid_shape` / `monitors`. The flat `t@名.time`-style keys are the
SDK's in-memory intermediate; the full protocol lives in
[09 · results](09-results.html).

The mirroring is deliberate: whatever was attached to the simulation
appears under the monitor's name, so results files stay
self-describing. A file whose key list does not match the monitor
list is a file from a different version of the configuration, and the
fastest diagnosis of "these numbers look old" is comparing the monitor
names against the script that claims to have produced them.

Three read paths, one file:

```python
fr   = f.get_freq('tr')   # typed object, cached: fr.freqs, fr.fields
data = f.results()        # whole-file view: data.tr.Ex, data.attrs, data.plot()
d    = f.digest()         # numeric summary computed on the server (no file)
```

The three paths serve three workflows. `get_freq` is the analysis
path: typed access to one monitor with the axes already attached.
`results()` is the inspection path: the whole file, including the
`attrs` metadata (executed steps, grid, timestamps) that answers
"what actually ran". `digest()` is the summary path, computed on the
server before any download and appropriate for harvesting
normalized quantities like transmission across many runs. Reaching for
the full file when the digest would do is the usual inefficiency;
reaching for the digest when the raw fields are needed is the usual
dead end.

## Physics of the three monitor types

**Time monitor** → `E(tₙ)` at one point/volume: use it to verify the pulse
passed, measure reflections by arrival time, and confirm ring-down
completeness (fields should be ~0 at the end; if not, the
`total_time` was too short). One sampling caveat: if the monitor uses
`.down_sample((t0, t1), stride)`, samples recorded outside that window
contribute nothing to the frequency-domain results (the solver emits a
warning) — the DFT sees only the declared window.

The time monitor is the raw sensor and the other two are processed
forms of it, which is why it remains the arbiter when processed results
look suspicious. Reflections are read off it by arrival time: with the
source at `z_s` and a reflector at `z_r`, the echo arrives near
`2(z_r − z_s)/c`, so a time series is a depth-resolved map of where
light turned around. A field still visibly oscillating at the end of
the record is a hard warning that every spectrum from this run is
window-contaminated.

**Freq monitor** → complex DFT amplitudes `Ẽ(fₖ)` (see
[Tutorial 5](tut5.html)). The magnitude squared is the
spectral intensity:

```math
I(f) ∝ |Ẽ(f)|²
```

The amplitudes are complex, and the phase carries information that
magnitude discards. Reconstructing fields at one frequency across a
monitor plane and taking Re{E e^{−iωt}} produces instantaneous
snapshots of the mode, from which mode shapes, standing-wave patterns,
and local defect fields can all be read. When a resonance is faint in
`|Ẽ|²`, its phase signature is often cleaner: the phase of the DFT
amplitude rotates rapidly through a resonance, a diagnostic that
survives even when the magnitude contrast is poor.

**Index monitor** → the *built* `ε(r)`, `μ(r)` after rasterization to the
Yee grid — the ground truth of what the solver actually sees. The
distinction between the geometry declared and the geometry that was
built is where a large fraction of geometry bugs live: two overlapping
shapes resolve by priority order, a cylinder rasterizes into a staircase
whose effective diameter depends on the grid, and a shape reaching
outside the domain disappears silently ([04 · f.geom](04-geom.html)). All
of this is invisible in the fields — a simulation of the wrong structure
produces perfectly plausible fields of the wrong structure — so one
index-monitor dump per new geometry, read as an image, is the cheapest
insurance in the workflow.

## The three classic mistakes

**1 · Truncated ring-down.** A resonance `E(t) = E₀e^{−t/τ}e^{−iω₀t}`
observed only for `t ≤ T` transforms into a Lorentzian convolved with
the window sinc:

![Truncated ring-down and its spectrum](figs/fig-ringdown.svg)

```math
|Ẽ(f)|² ∝ 1 / ( (ω−ω₀)² + (1/τ)² )  ⊛  sinc²(fT)
```

Consequences with numbers: the peak widens to `Δf ≈ 1/(πτ)`, and if the
amplitude has not decayed below `e^{−π}` (≈ 4%) by `T`, sidelobes at the
−13 dB level of the rectangle window ride on the spectrum. The
corruption is spectral: cutting a resonant signal off mid-ring multiplies
it by a rectangle, convolving the true lineshape with a sinc — the main
lobe broadens the measured linewidth (an extracted Q is always an
underestimate) and the sidelobes imprint ripples spaced `1/T` that can
masquerade as additional resonances. No post-processing of the spectrum
can undo this; the information was discarded when the run stopped.
Diagnose with a time monitor; fix with `total_time ≳ 3τ` (or `apodize` as
mitigation, at 2× resolution cost — see
[Tutorial 5](tut5.html)).

**2 · Monitor in the wrong place.** Inside the source window the monitor measures
incident + scattered mixed; inside PML nothing physical is measured.
Face monitors belong between structure and PML.

Both failure modes produce numbers, which is what makes this mistake
survive review. A monitor inside the TFSF
(total-field/scattered-field) window reports the coherent superposition
of the analytic incident wave and the scattered field, so its
"transmission" dips for reasons unrelated to the structure; a monitor
inside the PML reports fields being actively attenuated, a spectrum that
decays with distance into the layer rather than with any physical
mechanism. The safe band is the gap between the structure and the PML,
with one or two cells of margin on each side.

**3 · Reading raw amplitudes as transmission.** `|Ẽ|²` from a freq
monitor is proportional to power only after normalization by the
incident flux: `T(f) = P_trans/P_inc`. The trustworthy numbers are the
server-computed ones in `digest()` marked `normalized=true` (face
monitors only; point monitors do not carry `T`).

The raw spectrum inherits the source envelope, so its overall shape is
the Gaussian pulse and the structure's response is a modulation on top.
The classic symptom of reading it raw is a transmission plot that
rises and falls exactly where the source spectrum rises and falls: that
curve is mostly the pulse, not the physics. After normalization the
source cancels and what remains is the structure's transfer function,
flat where the structure is transparent and dipped where it is not.

## A concrete session

```python
fr = f.get_freq('tr')
import numpy as np
spec = np.abs(fr.fields['Ex'])**2      # relative spectral intensity
peak = fr.freqs[int(np.argmax(spec))]  # peak frequency (Hz)

data = f.results()
print(data.attrs['executed_steps'])    # steps actually run
data.plot('tr', 'Ex')                  # quick matplotlib look
```

This snippet is the first minute of analysis, not the last. It locates
the strongest spectral feature and confirms the run did the work it
claimed, but each line answers a different question than the ones that
matter most: whether the ring-down completed, whether the peak is
resolved, and whether the amplitudes are normalized. The checklist
below supplies those questions in order.

**Worked numbers.** Consider a resonance reported at `f₀ = 193 THz`
with `Q = 2000`. Its ring-down time is
`τ = Q/(πf₀) = 2000/(π·193×10¹²) ≈ 3.3 ps`. Requiring the amplitude to
fall by `e^{−3}` before cutoff means running for at least
`total_time ≈ 3τ ≈ 10 ps`. Under-run to `T = 2 ps` and the achievable
resolution is `Δf = 1/T = 500 GHz` against a true linewidth of
`f₀/Q = 96.5 GHz`: the recorded peak is five times too broad, and the
extracted Q would come out near 400 regardless of the real cavity.
The two numbers `Δf = 1/T` and `f₀/Q` should be compared for every
resonance intended to be quoted; the run is honest only when the first is
meaningfully smaller than the second.

## A post-processing checklist

Run the checks in this order; each one can invalidate everything after
it, so early failures are cheap to catch and expensive to skip.

1. **Decay check.** Open a time monitor and confirm the field has
   fallen to a small fraction of its peak (well under the 4% level of
   mistake 1) by the end of the record. If it has not, no spectrum from
   this run is trustworthy: extend `total_time` toward `3τ` and rerun.
   This single check prevents the majority of wrong-numbers incidents.

2. **Index-monitor check.** View the built `ε(r)` for one slice through
   the structure and confirm it matches the intended geometry (placement,
   overlap priority, stair-casing) — a wrong ε(r) invalidates every
   number computed from the fields.

3. **Resolution check.** For each spectral feature intended to be
   report, compare the achievable resolution `Δf = 1/total_time`
   against the feature width — `f₀/Q` for a resonance, the designed
   passband for a filter. The resolution must be comfortably finer
   than the feature; at parity, the reported width is the window's,
   not the physics'.

4. **Normalization check.** Confirm the numbers about to be quoted
   come from normalized quantities (`digest()` entries marked
   `normalized=true`, or an explicit two-run ratio explicitly performed), and
   that for lossy problems the conservation identity `R + T + A = 1`
   closes to within a few percent. Raw `|Ẽ|²` is a shape, not a
   transmission.

The checklist costs minutes per run and its value compounds: the first
time it catches a truncated ring-down or a misplaced monitor, it has
paid for itself many times over. Its deeper purpose is to move the
burden of trust from the final numbers, where it is anxious and
unverifiable, to the four cheap questions above, where it is a matter
of looking.

## Continue

- Full data surface: [09 · results](09-results.html)
- Server-side summaries without files: `f.digest()` in
  [07 · f — submit & lifecycle](07-run.html)
