# GENIE SDK

Build photonic FDTD simulations in Python: scriptable modeling → one-click
submission to cloud GPUs → results saved into a portable `.gensim`
document → custom `post_analyze` post-processing travels with it.

## Install

Requires Python ≥ 3.10:

```
pip install genie-sdk
```

During the prerelease phase you can also install from source:

```
pip install git+https://github.com/Simeonma/genie-sdk.git
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

Minimal script (paste into the editor and run):

```python
f.domain.x_range = (-1e-6, 1e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 60e-15
f.mesh.dx = 40e-9
f.mat.add_lorentz(f.mat_from_nk("Au"))        # material library in one step
f.geom.add_box("wg")
f.geom["wg"].material = "Au"
f.geom["wg"].x_range = (-5e-7, 5e-7)
f.geom["wg"].y_range = (-5e-7, 5e-7)
f.geom["wg"].z_range = (-5e-7, 5e-7)
f.src.add_plane_wave("src")
f.src["src"].freq = 300e12
f.src["src"].bandwidth = 60e12
f.mon.add_freq("tr")
f.mon["tr"].field = "Ex"
f.mon["tr"].freq = {"center": 300e12, "bw": 120e12, "n": 200}

f.submit()          # submit to the cloud; the result is saved back into the local .gensim
r = f.results()     # read the results (SimData)
```

## Parameter sweeps

Define a named sweep, run it as one batch, read the results as a cube
(the sweep axes lead the field dimensions; failed points are NaN):

```python
f.add_param_sweep("grid", {"x": [2e-6, 3e-6, 4e-6]})
sw = f.run_param_sweep("grid")     # blocks until the whole batch finishes
sw.get_freq("tr")                  # fields shaped (3, ...) — one leading axis
sw.run_post_analyze("Q")           # re-run a post_analyze recipe over all points
```

Each point is a plain `.gensim` document under `parameter_sweep_grid/`,
with an `index.json` account beside them.

## Command reference

| Command | Purpose |
|---|---|
| `genie login <token>` | Log in to the cloud (stores the token locally) |
| `genie studio` | Launch the local Studio (bare `genie` is equivalent) |
| `genie connect` | Print the current Studio address and token (for agents) |
| `genie logout` | Revoke cloud credentials |

Full documentation is on its way (1.0.0 alpha). For issues, contact your
service administrator.
