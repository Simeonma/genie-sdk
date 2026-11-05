"""genie.core.orchestration — orchestration knowledge (pure: zero
network, zero process, zero environment).

The home of "N runs of one model": the account (the Sweep read face +
index) and per-driver pure pieces (the sweep expander today; the PSO
algorithm module later). Verbs and the driver live in worker (they
breathe the exec environment); this package holds only what is purely
testable. See DECISIONS Q10."""
from . import expand, sweep

__all__ = ["expand", "sweep"]
