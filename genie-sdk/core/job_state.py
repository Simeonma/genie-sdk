"""genie.core.job_state — 作业终态词表（G24 裁决口径的唯一一份）。

消费方：``net/client.py``（轮询终态判定）与 ``core/sweep.py``（子作业
终态/rollup）——2026-10-27 G91 清账前各写一份（sweep 那份漏 archived，
整批归档时 sweep 误报 done），现收拢到此，两边 import 同一对常量。

语义：
- ``succeeded`` = 唯一好终态；
- ``failed`` / ``killed`` = 坏终态；
- ``archived`` = janitor 归档（**终态**，产物在 archive_path 下；对"等结果"
  的调用方它不是成功——结果已不在热存储，取数方要另走归档面）。
"""
from __future__ import annotations

TERMINAL_OK = "succeeded"
TERMINAL_BAD = ("failed", "killed", "archived")
