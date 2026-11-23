# studio/vendor — third-party components (NOTICE)

Every third-party file in this directory is distributed verbatim, with no
local modifications except where noted below. Versions follow the identifier
embedded in each file; update this list whenever a component is updated.

| File | Source | Version | License | Local changes |
|---|---|---|---|---|
| three.min.js | https://github.com/mrdoob/three.js | r128 (copyright line 2010-2021) | MIT (SPDX header in the file) | none |
| OrbitControls.js | three.js r128 `examples/js/controls/OrbitControls.js` | r128 | MIT (**no license header in the file**; defer to the three.js repository LICENSE) | none |

> 2026-10 stage 2: `pyodide/` (75 MB, MPL-2.0) and `genie_sdk/` (SDK
> distribution mirror) were removed per framework section 4 ("delete
> Pyodide") — previews now go through the local Python endpoint
> `/api/preview`, and the browser no longer executes Python.
