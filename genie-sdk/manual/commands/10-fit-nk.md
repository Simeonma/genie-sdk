# 10 · genie.material — n-k fitting & library

> Three module-level functions. For day-to-day work prefer
> [`f.mat.add_from_library`](#f-mat-materials-registry) (an automation wrapper over the same
> workflow); reach for these directly only with custom n-k data
> or when a fit needs fine-tuning.

## Model-selection ladder

When a material model is needed, the selection order is: a **constant ε**
first, then a **hand-written pole** set, and only then a **library fit**.
The rule is to use the simplest model that describes the physics
correctly: a constant is free in memory and runtime, each hand-written
pole buys one resonance at known cost, and a fit is reserved for when
tabulated n-k data must actually be reproduced.

## genie.material.fit_nk()

Fit n-k data into a Drude-Lorentz pole set (the only dispersive form the
kernel accepts); requires scipy (ImportError → `pip install scipy`).
Returns: dict `{converged, eps_inf, n_poles, poles, quality,
material_node, warnings}` — `material_node` is a `lorentz` node that goes
straight into a SimConfig (None = no usable node). Multi-component input
keeps the fit detail (`converged`/`eps_inf`/`n_poles`/`poles`/`quality`)
keyed by component; the `material_node` shape differs by input: a 7-tuple
(anisotropic xx/yy/zz) yields ONE assembled node — 3-element diagonal
`eps_inf` plus per-axis rank-1 poles — while a dict of custom component
names keeps `{component: node}`.
An unusable fit (e.g. too few data points inside `fit_range`, or
non-finite n/k values inside the window) raises `ValueError` with an
actionable message; a genuine iteration-cap hit reports via `warnings`
with the raise-`maxfev` prescription.

| Param | Type | Required | Default | Description |
|---|---|---|---|---|
| `nk` | 3-tuple / 7-tuple / dict | yes | — | `(wl, n, k)`: single-component iso fit (wl in m); `(wl, nx, kx, ny, ky, nz, kz)`: anisotropic xx/yy/zz; `{component name: (wl, n, k)}`: arbitrarily named components |
| `fit_range` | (lo, hi) m | no | (400e-9, 800e-9) | Fitting wavelength window; < 3 points inside → ValueError |
| `n_drude` / `n_lorentz` | int | no | 0 / 2 | Number of Drude / Lorentz poles |
| `maxfev` | int | no | 5000 | Iteration cap for curve_fit |
| `name` | str | no | None | Name of the resulting material_node |

**Example**

```python
import genie
wl, n, k = my_refractiveindex_data()          # SI: m / dimensionless
res = genie.material.fit_nk((wl, n, k), fit_range=(500e-9, 900e-9))
assert res["converged"] and res["material_node"]
# merge via chained API — pole args are positional (see 03 · f.mat);
# `**p` would also pass `kind`, which the pole chain does not accept
node = res["material_node"]
m = f.mat.add_lorentz().name(node["name"]).eps_inf(node["eps_inf"])
for p in node["poles"]:
    if p["kind"] == "drude":
        m.add_eps_drude_pole(p["omega_p"], p["gamma"])
    else:
        m.add_eps_lorentz_pole(p["omega_0"], p["gamma"], p["delta_eps"])
```

**Common pitfalls**

- Passing wavelengths in nm — both `fit_range` and the data must be in
  meters.
- Reversed `fit_range` (lo ≥ hi) — ValueError.

**Anisotropic fit (7-tuple) — one node, axis-mounted poles**

For crystalline/liquid-crystal data with distinct principal axes, pass
all three (n, k) column pairs; `material_node` comes back as a **single**
anisotropic lorentz node whose `eps_inf` is the 3-element diagonal
`[εxx, εyy, εzz]` and whose poles are **rank-1** (`alpha` = axis unit
vector; `beta` defaults to `alpha` per wire semantics, so a pole
contributes Δε along its axis only). The node validates as-is against
the wire and can be merged like any lorentz material:

```python
res = genie.material.fit_nk((wl, nx, kx, ny, ky, nz, kz))
node = res["material_node"]        # eps_inf=[εxx, εyy, εzz]; rank-1 poles
m = f.mat.add_lorentz().name(node["name"]).eps_inf(node["eps_inf"])
for p in node["poles"]:
    if p["kind"] == "drude":
        m.add_eps_drude_pole(p["omega_p"], p["gamma"], alpha=p["alpha"])
    else:
        m.add_eps_lorentz_pole(p["omega_0"], p["gamma"], p["delta_eps"],
                               alpha=p["alpha"])
```

`material_node` is None if any axis failed to converge (each axis's
warning is folded into `warnings`). A dict input with custom component
names is NOT assumed to be axes — it keeps `{component: node}`.

## The fit_range → eps_inf → cfl divergence chain

`fit_range` does not just select where the fit is judged; it moves
`eps_inf`, and `eps_inf` feeds the Courant stability margin. Narrow the
window and the fitter trades background offset for in-band accuracy, and
the resulting `eps_inf` can drop below 1 — an n < 1 effective background
that tightens the CFL bound in ways the default `cfl` no longer covers.
Mechanically, `eps_inf` < 1 raises the wave speed in the material above
`c/√ε_max`, so the Courant stability limit on the time step tightens
accordingly — a small `eps_inf` forces a much smaller `dt` or the run
diverges.

Measured chain (Au, default pole counts):

| fit_range | eps_inf | cfl = 0.9 | cfl = 0.5 / 0.3 |
|---|---|---|---|
| 400–800 nm (default) | ≈ 7.6 | stable | stable |
| 500–600 nm | ≈ 0.20 (n < 1) | NaN divergence within ~1000 steps | stable |

Mitigations, in order of cost: probe with a short `total_time` trial run
before committing to a campaign; lower `cfl`; widen `fit_range` so the
fitter recovers a physical background; and when a narrow-band fit is
unavoidable, trust only in-band results and treat the stability margin
as part of the fit's price.

## genie.material.load_library_nk() / list_library_materials()

`load_library_nk(name)` returns `(wl, n, k)` of a library material —
**purely local** since SDK 2.0.0a9 (the library ships inside the package;
an unknown name raises with a pointer to
`genie.material.list_library_materials()` — there is no HTTP
fallback anymore). Feed it to `fit_nk` or plot it yourself.
`list_library_materials()` lists all names (37 entries, e.g. `Au`,
`Ag`, `Si`…).

```python
wl, n, k = genie.material.load_library_nk('Au')
genie.material.list_library_materials()
```

See also　[add_from_library](#f-mat-materials-registry) = an automation wrapper over
load_library_nk + fit_nk + registration.
