# studio/vendor — 第三方组件清单（NOTICE）

本目录下所有第三方文件均原样分发、未做本地改动（除下表注明）。版本以
文件内嵌标识为准；更新任一组件时同步更新本清单。

| 文件 | 来源 | 版本 | License | 本地改动 |
|---|---|---|---|---|
| three.min.js | https://github.com/mrdoob/three.js | r128（版权行 2010-2021） | MIT（文件头部含 SPDX） | 无 |
| OrbitControls.js | three.js r128 `examples/js/controls/OrbitControls.js` | r128 | MIT（**文件内未含 license 头**，以 three.js 仓 LICENSE 为准） | 无 |

> 2026-10 阶段 ②：`pyodide/`（75MB，MPL-2.0）与 `genie_sdk/`（SDK 分发
> 镜像）已按 framework §四「Pyodide 删掉」定案移除——preview 改走本地
> Python 端点 `/api/preview`，浏览器不再执行 Python。
