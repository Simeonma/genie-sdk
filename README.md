# GENIE SDK

Build photonic FDTD simulations in Python: scriptable modeling → one-click
submission to cloud GPUs → results saved into a portable `.gensim`
document → custom `post_analyze` post-processing travels with it.

## Install

Requires Python ≥ 3.10.

One download, one install. Each release ships a single zip that contains one
wheel (the wire-format protocol `genie_protocol` is inside it — there is no
second package):

- Windows: unzip, double-click `install.bat`
- macOS / Linux: unzip, `bash install.sh`

Both scripts run `pip install genie_sdk-<ver>-py3-none-any.whl` against the
Tsinghua PyPI mirror. Prefer plain pip:

```
pip install genie_sdk-<ver>-py3-none-any.whl
```

Upgrading from an older release that also installed `genie-protocol`? Remove
both first — the SDK now carries the protocol and refuses to import when two
copies are present:

```
pip uninstall -y genie-sdk genie-protocol
```

From a source checkout (the tree ships `protocol/` next to the SDK sources;
`pyproject.toml` maps both roots into the same wheel):

```
pip install .
```

## Connect to the cloud

GENIE simulations run on a cloud GPU cluster; the service endpoint is
`https://genie-optics.com`. You need a service token (request one from
your administrator). Log in once — credentials are written only to the
local `~/.genie/credentials.json` (permission 0600) and are never
uploaded anywhere:

```
genie login <your-token>
```

- Switch gateway: set the environment variable
  `GENIE_BACKEND=https://…` before launching (the default is the
  endpoint above);
- Revoke credentials: `genie logout`.

## Launch Studio

```
genie studio
```

Open the printed address in your browser (something like
`http://127.0.0.1:<port>`) — the script editor at the top left drives the
model directly with Python; exit with `Ctrl+C`. To pick a fixed port use
`genie studio --port 8777` (by default the system assigns one).

Minimal script (paste into the editor and run). Solvers are declared on
demand — there is no default (`genie.fdtd` exists only after
`add_solver`):

```python
genie.add_solver(name="fdtd", type="fdtd")
genie.set_named("fdtd", x_range=(-1e-6, 1e-6), y_range=(-1e-6, 1e-6),
                z_range=(-1e-6, 1e-6), total_time=60e-15, dx=40e-9)

genie.add_material(name="Au", type="library", library="Au")
genie.set_named("material.Au", band=(400e-9, 1000e-9), tolerance=0.01)
genie.fit_material("Au")                    # explicit fit (lazy on run too)

genie.add_geometry(name="wg", type="box")
genie.set_named("geometry.wg", material="Au",
                x_range=(-5e-7, 5e-7), y_range=(-5e-7, 5e-7),
                z_range=(-5e-7, 5e-7))

genie.fdtd.add_source(name="src", type="plane_wave")
genie.set_named("fdtd.src", freq_center=300e12, freq_bw=60e12)
genie.fdtd.add_monitor(name="tr", type="freq")
genie.set_named("fdtd.tr", field="Ex",
                freq_center=300e12, freq_bw=120e12, freq_n=200)

genie.run_fdtd()          # run; the result is saved back into the local .gensim
r = genie.get_result("tr", "Ex")     # one monitor field array
d = genie.results()                  # advanced: the whole SimData view
```

## Parameter sweeps

```python
genie.add_sweep(name="grid")
genie.set_named("sweep.grid", parameters={"L": [2e-6, 3e-6, 4e-6]})
sw = genie.run_sweep("grid")     # blocks until the whole batch finishes
sw.get_result("tr")              # fields shaped (3, ...) — one leading axis
sw.run_analysis("Q")             # re-run an analysis recipe over all points
```

Each point is a plain `.gensim` document under `parameter_sweep_grid/`,
with an `index.json` account beside them.

## Analyses (post-processing)

```python
genie.add_analysis(name="Q")
genie.set_named("analysis.Q", script="recipes/Q.py", call="calc(L=2e-6)")
genie.run_analysis("Q")          # result dict → post/Q.npz in the document
```

## Command reference

| Command | Purpose |
|---|---|
| `genie login <token>` | Log in to the cloud (stores the token locally) |
| `genie studio` | Launch the local Studio (bare `genie` is equivalent) |
| `genie connect` | Print the current Studio address and token (for agents) |
| `genie logout` | Revoke cloud credentials |

Full documentation is on its way (1.0.0 alpha). For issues, contact your
service administrator.
