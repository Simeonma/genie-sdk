"""genie.ipc — the host↔worker pipe vocabulary (a zero-IO leaf).

The one and only home of words and constructors is ``vocab`` — both ends
(session / worker) take them from there and must not write message
literals.
"""
from .vocab import *  # noqa: F401,F403 — public surface = all vocab names
from .vocab import __all__ as _vocab_all

__all__ = list(_vocab_all)
