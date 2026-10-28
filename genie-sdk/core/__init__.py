"""genie.core — 纯本地层（零网络，framework §四）。

builder（六命名空间+链式工厂）/ simulate（Simulation 主体）/ script
（对象→脚本）/ results / sweep / errors / material + nkdata。
本包不碰网络——传输在 ``genie.net``，接入层在 ``genie.integrations``。
（2026-10-28 G174：preview 脚本→对象引擎已整体退役，合并函数迁
``genie.studio.model_merge``。）
"""
