"""genie — v2 SDK 包根。

公开面只有 ``f``（Q1.4 / Q6.1）：worker 每次 exec 预注入，根包**不导出模型入口**。
结构地图：``genie-sdk/MODMAP.md``；裁决：``docs/DECISIONS.md``。
"""

#: 与根 pyproject.toml 双处对齐（发版纪律）；1.0.0a1 = v2 首个公开线
#: 版本（站长拍 2026-11-05：v2 换线、v1 线下线）。
__version__ = "1.0.0a1"
