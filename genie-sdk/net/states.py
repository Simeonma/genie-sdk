"""genie.net.states — the job terminal-state vocabulary (the single copy).

Consumer = the worker/run wait loop: poll ``job_status`` once per second for
progress and state → ``TERMINAL_OK`` collects the result, ``TERMINAL_BAD``
raises ``GenieJobError``, neither present means keep waiting. Both
``canceled`` and ``killed`` are terminal BAD (an out-of-band cancel may be
labeled either by the gateway; the worker's wording is unified to canceled);
the value list is locked by tests against the gateway's job state machine;
core / worker must not write a second copy.
"""
from __future__ import annotations

__all__ = ["TERMINAL_OK", "TERMINAL_BAD"]

TERMINAL_OK = frozenset({"done"})
TERMINAL_BAD = frozenset({"failed", "killed", "canceled"})
