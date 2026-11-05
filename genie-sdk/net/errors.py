"""genie.net.errors — the SDK's outward error contract (the GenieError family).

Hierarchy::

    GenieError                  base class (carries HTTP status / the server's raw detail)
    ├─ GenieConfigError         local config pre-check failure (submit boundary)
    ├─ GenieHTTPError           request-layer failure (4xx/5xx: validation errors, invalid key, ...)
    └─ GenieJobError            job terminal-state failure (failed/killed: carries a log tail)

Design note: the server's raw information (pydantic ValidationError / the
resource guard's GuardError) is **preserved verbatim** in ``.detail``; the
message body is only assembled for readability — a user debugging a config
problem can see the per-field error paths without opening a browser.

The host's ``/gw`` normalizes upstream errors into codes (auth / upstream /
resource / rate / server, fixed English wording); this module is the
SDK-side exception **vocabulary**, not a second wording surface.
"""
from __future__ import annotations

from typing import Any

__all__ = ["GenieError", "GenieConfigError", "GenieHTTPError",
           "GenieJobError", "GW_CODES", "ENVELOPE_KEYS"]

#: /gw error envelope keys (produced by host/gw, consumed by client)
ENVELOPE_KEYS = ("code", "message", "detail")

#: Five-code table: code → fixed English message (**the single copy**).
#: host/gw references it to produce envelopes and client references it on
#: transport failure — neither hand-copies; consumers match on code only
#: and display message, with no second mapping.
GW_CODES = {
    "auth": "Authentication required",
    "upstream": "Upstream unreachable",
    "resource": "Request rejected",
    "rate": "Rate limited",
    "server": "Server error",
}


class GenieError(Exception):
    """SDK base exception. ``status`` = the HTTP status code (None for
    non-HTTP origins); ``detail`` = the raw error body returned by the server
    (str / list / dict)."""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class GenieConfigError(GenieError):
    """Local config pre-check failure at the submit boundary.

    Validation goes through ``genie_protocol`` (resolve + validate_for_ui) —
    the same hands as the gateway's submit gate, with identical error paths
    and wording; the only difference is being moved earlier to a local
    fail-fast."""


class GenieHTTPError(GenieError):
    """Request failure (4xx/5xx) or transport failure (``status=None``).
    ``code`` = one of the /gw five codes (transport failure is fixed to
    ``"upstream"``, message taken from ``GW_CODES``)."""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None, code: str | None = None):
        super().__init__(message, status=status, detail=detail)
        self.code = code


class GenieJobError(GenieError):
    """The job ended in the failed/killed terminal state. ``error`` = the
    server-side error summary; ``log_tail`` = the driver log tail (the first
    place to look when troubleshooting)."""

    def __init__(self, job_id: str, state: str, error: str | None = None,
                 log_tail: str | None = None):
        msg = f"job {job_id} terminal state = {state}"
        if error:
            msg += f": {error}"
        super().__init__(msg, detail=error)
        self.job_id = job_id
        self.state = state
        self.error = error
        self.log_tail = log_tail
