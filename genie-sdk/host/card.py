"""genie.host.card — job cards (the skin lives in the browser; data travels the ring, the stop control shares the same entry point).

Card data = the ``out(kind=progress)``
rollup that the worker's wait loop refreshes into the ring (the host only
ferries it, never asks the gateway — this package dials nothing); **one
stop control per batch**, sharing the same entry point as ``f.cancel`` (the
session interception entry → dials the gateway directly via host/gw). One
card per batch (a single run = a batch of length 1 is also one card); skin
details — "appears after submit, disappears once the whole batch completes
and the user closes it", plus the per-batch detail expansion — are staged
in code along with the GUI shell.

Lexical note: ``"out"`` / ``"progress"`` are the established words of the
ring payload (the frontend — this package and the GUI JS included — must
not coin its own field names; host does not import ipc, the literals are
aligned with the GUI JS against the same source).
"""
from __future__ import annotations

__all__ = ["stop", "snapshot"]


def stop(session) -> list:
    """One stop control per batch: the same entry point as ``f.cancel`` (the
    session interception entry — ``cancel_inflight`` → the host callback
    dials directly via ``/gw``; effective whether busy or idle)."""
    return session.cancel_inflight()


def snapshot(events) -> dict | None:
    """Ring projection → the latest progress rollup (card data; None if there is none)."""
    data = None
    for e in events:
        if e.get("ev") == "out" and e.get("kind") == "progress":
            data = e.get("data")
    return data
