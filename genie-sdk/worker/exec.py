"""genie.worker.exec — execution plane: namespaces / the genie composite / output capture.

- REPL: exec runs in place in the current namespace (a mid-way failure
  **keeps everything before it**);
- fresh (Run): rebuilds wholesale in a **shallow copy of the old
  namespace** against a **new empty model**; only on success is the
  namespace replaced wholesale + the model atomically swapped; on failure
  nothing moves, but commits and staging that already happened inside
  fresh are **not rolled back** (external side effects are not rolled
  back with the namespace);
- ``genie`` is re-injected on every exec (bound to run's action plane — this
  module only builds the binding; the action implementations live in
  run.py);
- stdout is the IPC channel: hijacked during exec (captured into out);
  ``echo=False`` turns off only the user's stdout — cfg is still
  emitted, and stderr still goes into the ring (red error lines);
- cfg emission: one version is always emitted after each
  exec completes (an unconditional snapshot regardless of success or
  failure — idempotent, naturally coalesced by the 300ms tick).
"""
from __future__ import annotations

import io
import sys

from genie import ipc
from genie.core.model import Model

from .run import bind


class ExecContext:
    """The worker's execution-plane state: the live model, the namespace,
    and the action environment (emit / document path / gateway inputs).
    run's actions lazily build a net Client via ``client()``."""

    def __init__(self, emit, model=None, gw_base_url=None,
                 worker_token=None, doc_path=None):
        self.emit = emit
        self.model = model if model is not None else Model()
        self.gw_base_url = gw_base_url
        self.worker_token = worker_token
        self.doc_path = doc_path
        self.inflight: set = set()          # in-flight jobs of this genie (used by _cancel(None))
        self.post_recipes: dict = {}        # named recipes {name: {code, script}}
        self.sweeps: dict = {}              # named sweep definitions {name: {parameters, mode, n_gpu, on_failure}}
        self.ns: dict = {}
        self._active_ns: "dict | None" = None   # the ns currently being exec'd (fresh = a local copy)
        self._reinject_genie()

    # -- genie / net ---------------------------------------------------

    def _reinject_genie(self) -> None:
        self.ns["genie"] = bind(self.model, self)

    def current_ns(self) -> dict:
        """The namespace currently being exec'd (fresh = a local copy;
        normally self.ns). The post execution plane takes its shallow
        copy here — recipes must resolve to the functions just defined in
        this exec segment."""
        return self._active_ns if self._active_ns is not None else self.ns

    def client(self):
        """net Client (zero-credentials discipline: base_url / token passed in explicitly)."""
        from genie.net import Client
        return Client(self.gw_base_url, worker_token=self.worker_token)

    # -- exec (the execution plane, the only write channel)------------------------------------

    def exec_op(self, op: dict) -> None:
        """One exec op: run → out line → cfg snapshot → done."""
        ok, err = self._run(op["code"], op.get("fresh", False),
                            op.get("echo", True))
        # unconditional cfg snapshot: emitted even on failure (the current live model)
        self.emit(ipc.cfg(self.model.to_config()))
        self.emit(ipc.done(op["id"], ok, err))

    def _run(self, code: str, fresh: bool, echo: bool):
        """One piece of code: REPL in place / fresh rebuild in a shallow copy;
        stdout hijack; result (ok, err)."""
        ns = dict(self.ns) if fresh else self.ns
        model = Model() if fresh else self.model
        ns["genie"] = bind(model, self)     # genie is re-injected on every exec

        saved_out, saved_err = sys.stdout, sys.stderr
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        self._active_ns = ns                 # the post plane takes the ns currently being exec'd
        try:
            exec(compile(code, "<genie>", "exec"), ns)
        except Exception as e:               # noqa: BLE001 — a user-code failure goes back as done
            # failure: neither namespace nor model moves (the fresh copy is simply discarded)
            return False, f"{type(e).__name__}: {e}"
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
            self._active_ns = None

        if fresh:                            # only on success: wholesale replace + atomic swap of the model
            self.ns = ns
            self.model = model
        self._reinject_genie()

        text = buf_out.getvalue()
        if echo and text:                    # echo=False: stdout captured, then discarded
            self.emit(ipc.out(text))
        errtext = buf_err.getvalue()
        if errtext:                          # stderr → red error lines (echo=False only turns off stdout)
            self.emit(ipc.out(errtext, kind=ipc.OUT_KIND_ERROR))
        return True, None
