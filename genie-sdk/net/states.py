"""genie.net.states — 作业终态词表（唯一一份）。

消费者 = worker/run 等待循环（Q2.2-1A / 终版 2026-11-04）：每秒自查
``job_status`` 得进度与状态 → ``TERMINAL_OK`` 收结果、``TERMINAL_BAD``
抛 ``GenieJobError``、都不在则继续等。``canceled`` 与 ``killed`` 皆终态
BAD（带外取消网关可能标任一，worker 措辞统一 canceled——Q2.2-3）；
值清单对网关（genie-service）的作业状态机核对；core / worker
不得另写第二份（``test_terminal_vocab_single_source`` 锁）。
"""
from __future__ import annotations

__all__ = ["TERMINAL_OK", "TERMINAL_BAD"]

TERMINAL_OK = frozenset({"done"})
TERMINAL_BAD = frozenset({"failed", "killed", "canceled"})
