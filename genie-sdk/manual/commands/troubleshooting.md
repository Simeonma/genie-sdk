# Troubleshooting

> Organized by observed symptom, not by API. Each section is one symptom:
> what is actually seen → likely causes (most likely first) → a diagnostic
> step → the fix → a link to the authoritative page. Everything here traces
> to the reference pages and tutorials.

## Submission fails with a config error

A call to `f.run()` (or `f.sweep()`) raises a `GenieConfigError` before
anything reaches the server, or a 422 listing fields right after.

**Cause: an expression references an undefined parameter.** Numeric slots
accept expression strings like `'d/4'` (bare parameter names — braces such as
`'{d}/4'` are a Python set literal and get rejected), evaluated server-side
against the table set by `f.parameters()` — nothing is evaluated locally, so
a typo'd or missing name is only caught at the submission boundary, as
`GenieConfigError` (with `kind`/`msg`).
Diagnostic: check that every expression slot has a matching key in the
`f.parameters()` calls (repeated calls merge rather than replace).
Fix: define the parameter before submitting — see
[f-submit-lifecycle.html](07-run.html).

**Cause: building a configuration by editing a raw SimConfig JSON dict.**
The chained APIs (`f.domain/mat/geom/src/mon` + `f.parameters()`) never
produce unknown keys; a raw SimConfig JSON is an **internal format**, not a
hand-authoring surface.
Diagnostic: how did the JSON come to be — hand-written or copy-pasted?
Fix: rebuild that part with the chained APIs; to inspect the current
configuration read `f.to_config()` (read-only) — see
[f-submit-lifecycle.html](07-run.html).

**Cause: units written in the wrong scale.** `f.mesh.dx = 25` where 25 nm was
intended makes the domain orders of magnitude smaller than the grid — `min_cells`
rejects it (a 422 mentioning "min_cells") or the cell count explodes.
Likewise `fit_range` in nm errors the fit.
Diagnostic: print the value and ask "is this meters?"
Fix: write SI everywhere (`25e-9`) — see
[f-mesh-grid-local-refinement.html](02-mesh.html) and
[f-mat-materials-registry.html](03-mat.html).

**Cause: structurally invalid values.** Reversed domain ranges `(hi, lo)` are
rejected at submit; int slots (`max_refinement`, `min_cells`) reject floats
with `TypeError`; a negative constant ε for a metal is rejected outright.
Diagnostic: match the error text to the parameter table on the relevant page.
Fix: poles for metals, `lo < hi` tuples, ints as ints — see
[f-domain-physical-domain-boundaries.html](01-domain.html)
and [f-mat-materials-registry.html](03-mat.html).

## The spectrum looks wrong

The transmission/reflection curve has ringing or sidelobes around a sharp
peak, the peak is far too broad, or extra "resonances" appear that were never
designed.

**Cause: truncated ring-down.** A finite record of length T convolves the
spectrum with a sinc: −13.2 dB first sidelobes, linewidth broadened to
`Δf ≈ 1/(πτ)`, and ripples spaced `1/T` that masquerade as extra resonances.
Diagnostic: open a time monitor — if the field is still visibly oscillating
at the end of the record, every spectrum from this run is window-contaminated.
Fix: `total_time ≳ 3τ` (for Q at f₀: `τ = Q/(π·f₀)`); `apodize('both', …)`
(Hann window, −31.5 dB sidelobes) is mitigation at 2× resolution cost. No
post-processing of the spectrum can undo it — see
[reading-results.html](tut6.html) and
[sources-and-monitors.html](tut5.html).

**Cause: insufficient spectral resolution.** Achievable resolution is
`Δf = 1/total_time`; if that is not comfortably finer than the feature width
(`f₀/Q` for a resonance), the reported width is the window's, not the
physics'. Worked example: `T = 2 ps` gives 500 GHz against a true 96.5 GHz
linewidth — the extracted Q comes out ~5× too low regardless of the cavity.
Diagnostic: compare `Δf = 1/T` with `f₀/Q` for every resonance to be quoted.
Fix: run longer — see [reading-results.html](tut6.html) and
[boundaries-and-domain-size.html](tut3.html).

**Cause: wrong geometry was built.** A simulation of the wrong structure
produces perfectly plausible fields of the wrong structure; priority
mistakes, stair-casing, and shapes silently outside the domain are invisible
in the fields.
Diagnostic: one index monitor (`f.mon.add_index().name('ix').field('both')`)
read as an image — it shows the built `ε(r)`, the solver's ground truth.
Fix: correct geometry/priority before rerunning — see
[f-mon-monitors.html](06-mon.html) and
[f-geom-geometry.html](04-geom.html).

**Cause: source bandwidth misses the feature.** The Gaussian pulse's
`bandwidth` sets which spectrum is excited; features outside or near its edge
are not measured meaningfully.
Diagnostic: overlay the band of interest with `[center − bw/2, center + bw/2]`.
Fix: widen or recenter the pulse — see
[f-src-sources.html](05-src.html).

## Transmission is not between 0 and 1

The "transmission" plot exceeds 1, dips below 0, or rises and falls exactly
where the source spectrum does.

**Cause: reading raw `|Ẽ|²` as transmission.** Raw DFT magnitudes are
proportional to power only after normalization by the incident flux; raw, the
curve is mostly the Gaussian pulse envelope with the structure's response as
a modulation on top.
Diagnostic: does the plot trace the source spectrum? Then it is the pulse,
not the physics.
Fix: use server-computed numbers in `f.digest()` marked `normalized=true`
(physical T), or perform an explicit two-run ratio — see
[sources-and-monitors.html](tut5.html) and
[reading-results.html](tut6.html).

**Cause: point monitor where a plane is needed.** Point monitors have no area
integral and carry no T — `digest()` only reports physical transmittance for
plane monitors, and only when `normalized=true`.
Diagnostic: check the monitor's `size` — a default time/freq monitor is a
`(0,0,0)` point monitor.
Fix: give it extent (e.g. `.size(1e-6, 1e-6, 0)`) — see
[f-mon-monitors.html](06-mon.html).

**Cause: R + T + A > 1 for a lossy problem.** The conservation identity
should close to within a few percent; violation points at a too-small box or
a truncated resonance.
Diagnostic: run the normalization check (identity closure) from the
post-processing checklist.
Fix: enlarge the domain, extend `total_time` — see
[reading-results.html](tut6.html) and
[boundaries-and-domain-size.html](tut3.html).

## Plane-wave transmission spectrum is abnormally low or truncated

The plane-wave transmission curve collapses (e.g. measured T ≈ 0.14 where
the structure should pass almost everything) or cuts off across the band.

**Cause: source injected at a face flush against the PML inner edge.** With
`from_face` injection the pulse starts at the PML inner edge and reaches the
far-side monitor only after an extra transit `~d/c` (longer at oblique
incidence, `d/cosθ`). If the apodization stop time was set for the domain
center, the apodization window closes before the pulse has fully crossed the
monitor and the DFT flux is chopped.
Diagnostic: compare the `apodize` stop time against the time the pulse needs
to reach the *farthest* monitor.
Fix: set `stop ≥ t0 + d_max/cosθ + 3σ + width/2`, where `σ = 0.5/bandwidth`
— see [f-src-sources.html](05-src.html) and
[f-mon-monitors.html](06-mon.html).

**Cause: material fit window too narrow, producing `eps_inf < 1`.** A fitted
dispersive model can come out with `eps_inf < 1` (observed with Au fitted
over 500–600 nm); the ill-conditioned dispersion then amplifies fields and
the run goes NaN within ~1000 steps even though `cfl` is legal.
Diagnostic: check the fitted `eps_inf` for the `fit_range`; NaN at small
step counts with legal `cfl` is the signature.
Fix: widen `fit_range`, lower `cfl`, and do a short-`total_time` trial run —
see [10 · genie.material](10-fit-nk.html).

## Fields look unphysical near the edges

Monitor readings decay for no physical reason, dip where the structure should
be transparent, or the fields near the domain boundary behave strangely.

**Cause: monitor inside the PML.** Fields in an absorbing layer are being
actively attenuated; their spectrum decays with distance into the layer, not
with any physical mechanism. They have no physical meaning.
Diagnostic: check monitor coordinates against the domain ranges and PML
thickness.
Fix: face monitors belong between structure and PML, never inside it — see
[f-mon-monitors.html](06-mon.html) and
[reading-results.html](tut6.html).

**Cause: monitor inside the source window.** Inside the TFSF window (or at
the source plane) the record contains incident + scattered coherently mixed; at
frequencies where they interfere destructively the recorded "transmission"
dips for reasons that have nothing to do with the structure.
Diagnostic: move the monitor a few cells away from the source window and
compare.
Fix: keep monitors a few cells clear of the source plane — see
[f-src-sources.html](05-src.html) and
[sources-and-monitors.html](tut5.html).

**Cause: a non-absorbing face where light exits.** If light can reach a face
without an absorbing boundary there, reflections contaminate the readings.
Diagnostic: trace the propagation path; both entrance and exit faces need
PML (e.g. plane wave `from_face('x_min')` → PML on both x faces).
Fix: `f.domain.boundary_condition(face, 'pml')` on each — see
[f-domain-physical-domain-boundaries.html](01-domain.html).

**Cause: `periodic` at oblique incidence.** `periodic` is valid only for
normal incidence; oblique incidence on a repeating structure silently gives
wrong interference — the most common boundary mistake in metasurface work.
Diagnostic: is the source oblique (`theta`/`phi`) with `periodic` sides?
Fix: `bloch` with `from_source=True` (or explicit `k`) — see
[boundaries-and-domain-size.html](tut3.html) and
[f-domain-physical-domain-boundaries.html](01-domain.html).

## The sweep is slow or points fail

The sweep returns a `SweepHandle` fine, but takes forever, some points are
marked failed, or results for some indices come back as 409.

**Cause: `max_parallel` left at 1.** The default runs one point at a time
(sequentially, through each point's own `run()`). Diagnostic: check the
`max_parallel` passed (allowed 1–16) and `gpus_per_sim` (1–4, the same
cap as `run(gpu=…)` — a real knob: it is passed to every point's
`run(gpu=…)`).
Fix: raise `max_parallel` to match idle GPUs — see
[sweep-f-sweep-sweephandle.html](08-sweep.html).

**Cause: one failed point read as total failure.** Under the default
`on_failure="continue"` the remaining points run anyway; failed points become
NaN rows rather than an exception up front.
Diagnostic: `h.rollup` for the `{succeeded, failed, canceled}` breakdown and
`h.status()["points"]` for per-point states (`h.points` is a property holding
the resolved per-point configurations); if one bad configuration fails
everywhere, consider `on_failure="abort"`.
Fix: inspect the failing point's config, or switch the failure mode; per-point
results are the child jobs (ordinary jobs) fetched through the ordinary
results surface — see [sweep-f-sweep-sweephandle.html](08-sweep.html).

**Cause: more points than intended — `product` vs `zip`.** Multiple axes
default to `mode="product"` (Cartesian product): two 6-point axes are 36
simulations, not 6.
Diagnostic: compare the point count in `h.status()` with the expected count.
Fix: `mode="zip"` to pair axis points element-wise, or a fully-controlled
`sweep` SweepSpec dict — `sweep` and `parameters` are mutually exclusive
(giving both raises `ValueError`; pick one) — see
[sweep-f-sweep-sweephandle.html](08-sweep.html).

**Cause: sweeping a config locked by `f.run()`.** The sweep's base config
defaults to `to_config()` — the *current* configuration, which is locked
after a run; axes can only vary parameter names, not literals.
Diagnostic: was `f.run()` called on this instance before `f.sweep()`?
Fix: build the parameterized configuration and sweep it without running
first — see [sweep-f-sweep-sweephandle.html](08-sweep.html)
and [f-submit-lifecycle.html](07-run.html).

## Results are empty or stale

`f.get_freq('tr')` raises `KeyError`, `f.digest()` raises `RuntimeError`, or
the file held does not match the intended run.

**Cause: job not actually done.** `f.run()` returns only at a terminal state
(`state == "done"`); job failure or kill raises `GenieJobError` with `.state`
landing on `failed`/`killed`. Reaching into results of an unsubmitted
instance, or with no `job_id`, raises `RuntimeError`.
Diagnostic: `f.summary()` — it reports state and job_id.
Fix: rerun or wait; use the returned instance, not a fresh `genie.connect()`,
for `get_freq`/`digest` — see
[f-submit-lifecycle.html](07-run.html) and
[results-get-simdata.html](09-results.html).

**Cause: wrong monitor name.** Results are grouped by monitor `name`; a wrong
name raises `KeyError` with a hint.
Diagnostic: compare the name passed with the `f.mon` list — or with the
group list in the results file.
Fix: use the exact monitor name — see
[f-mon-monitors.html](06-mon.html) and
[results-get-simdata.html](09-results.html).

**Cause: file from a different version of the configuration.** The results
file's group list mirrors the monitor list; a mismatch means the file
predates the current script.
Diagnostic: compare group names in the h5 against the monitor list in the
script that claims to have produced it; `f.results().attrs` (executed steps,
grid, timestamps) answers "what actually ran".
Fix: rerun; the auto-download writes `<job_id>_results.h5` under
`$GENIE_RESULT_DIR` (default `./genie_results/`), and `results()` re-fetches
on demand if the download only warned — see
[reading-results.html](tut6.html) and
[results-get-simdata.html](09-results.html).

**Cause: reusing a submitted instance for a changed run.** A `Simulation`
submits at most once; after `f.run()` the configuration is locked, and
passing parameters again raises `RuntimeError` — so a rerun quietly never
happened and the old job is what is being read.
Diagnostic: did the rerun raise beforehand?
Fix: export `f.to_script()`, tweak the script, and run it on a fresh
`genie.connect()` instance — see [f-submit-lifecycle.html](07-run.html).
