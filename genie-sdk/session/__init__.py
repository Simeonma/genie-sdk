"""genie.session — host-side module that spawns and holds the worker: spawn / pipe / ops / ring.

- **spawn**: ``python -m genie.worker`` (cwd = workspace), wait for
  ``ready``; the three spawn env vars (worker/main's read side keeps the
  same-named constants, test-locked equal).
- **pipe**: one JSON object per line (UTF-8), both directions; the reader
  thread takes ev lines into the ring; a malformed line is recorded as one
  ``out(kind=error)`` line without breaking the pipe, and readline continues.
- **ops**: exec is the one and only downstream channel + busy-reject (the
  only pass-through exception = the ``f.cancel`` intercept, which works
  whether busy or idle and never enters exec); ids are issued by this
  package and echoed back unchanged by the worker.
- **ring**: ev ring + ``since`` cursor + epoch — host-process memory, never
  on disk; bounded, and past the cap drop oldest lines.

Dialing the gateway always calls back into the host (``on_cancel``; this
package touches no network); ``net`` imports only the ``net/states``
vocabulary (terminal-state decisions for the in-flight list).
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from genie import ipc
from genie.net.states import TERMINAL_BAD, TERMINAL_OK

__all__ = ["Session",
           "ENV_GW_BASE_URL", "ENV_WORKER_TOKEN", "ENV_DOC_PATH",
           "CANCEL_COMMAND", "ROW_BUSY",
           "ROW_CANCELED_ONE", "ROW_CANCELED_MANY",
           "ROW_RESTART_ONE", "ROW_RESTART_MANY"]

#: spawn env var names (write side; worker/main's read side keeps the
#: same-named constants, test-locked equal; env vars are not pipe words,
#: so they never enter ipc)
ENV_GW_BASE_URL = "GENIE_GW_BASE_URL"      # value includes the /gw prefix (Client concatenation contract)
ENV_WORKER_TOKEN = "GENIE_WORKER_TOKEN"
ENV_DOC_PATH = "GENIE_DOC_PATH"            # initial document path (decided by the host)

#: The ``f.cancel`` intercept command (no args = the whole in-flight
#: batch of this document; never enters exec, adds no pipe word)
CANCEL_COMMAND = "f.cancel"

# One-line reply literals (English, the same language as the worker's
# terminal lines; locked by test goldens)
ROW_BUSY = "busy: run in progress"                 # busy-reject one-line reply
ROW_CANCELED_ONE = "canceled job {id}"             # f.cancel one-line reply
ROW_CANCELED_MANY = "canceled {n} jobs"
ROW_RESTART_ONE = "worker restarted; canceled job {id}"    # kill/EOF one-line reply
ROW_RESTART_MANY = "worker restarted; canceled {n} jobs"

_RING_MAX = 1000            # ring cap; past it, drop oldest lines (never on disk)
_READY_DEADLINE_S = 10.0    # deadline for the spawn wait-for-ready (the spawn criterion, not a job timeout)


class Session:
    """A worker session inside the host process. Usage::

        s = Session(workspace, gw_base_url="http://127.0.0.1:PORT/gw",
                    worker_token=..., doc_path=..., on_cancel=host_dial)
        s.start()                      # spawn + wait for ready
        s.submit_exec("f.mesh.dx = 20e-9")
        epoch, since, evs = s.events()  # consumption entry on the 300ms single tick
        s.close()

    ``on_cancel(job_ids)`` = the host-side callback that dials ``/gw`` (the
    f.cancel and kill/EOF fallbacks both go through it; this package does no
    networking). ``worker_cmd`` is a test seam (fake worker; the production
    form is exactly one: ``python -m genie.worker``).
    """

    def __init__(self, workspace, *, gw_base_url, worker_token,
                 doc_path=None, on_cancel=None, worker_cmd=None):
        self.workspace = str(workspace)
        self.gw_base_url = gw_base_url
        self.worker_token = worker_token
        # The initial document path is decided by the host (when a path
        # is given, use that original path); the default = genie.gensim
        # inside the workspace. Owning and writing the file live in the
        # worker (the change channel = follow f._save).
        self.doc_path = (str(doc_path) if doc_path is not None
                         else os.path.join(self.workspace, "genie.gensim"))
        self.on_cancel = on_cancel
        self.worker_cmd = (list(worker_cmd) if worker_cmd is not None
                           else [sys.executable, "-m", "genie.worker"])
        self.epoch = uuid.uuid4().hex       # rotated on every host start (worker respawns never touch it)

        self._ring = collections.deque(maxlen=_RING_MAX)
        self._seq = 0                       # global sequence number (the since cursor)
        self._inflight = {}                 # job_id -> latest state (non-terminal = in flight)
        self._busy = False                  # from exec dispatch until done
        self._next_exec_id = 1              # issues exec ids
        self._proc = None
        self._threads = []
        self._generation = 0                # +1 on every successful spawn (restart waits on it)
        self._closed = False
        self._lock = threading.Lock()

    # ── ring (ev ring + since cursor + epoch; host-process memory, never on disk)────

    def _ring_append(self, ev: dict) -> None:
        with self._lock:
            self._ring.append((self._seq, ev))
            self._seq += 1

    def events(self, since: int = 0):
        """Ring projection (a 300ms single tick; after a disconnect,
        resume from the cursor). ``since`` = the number of entries already
        consumed (the cursor); returns ``(epoch, next_since, the list of
        unconsumed evs)``; when ``since`` predates the oldest surviving
        entry (old lines already dropped), the whole window is returned and
        the consumer does a full refetch as needed; epoch rollover is
        detected by the consumer through comparison."""
        with self._lock:
            items = [(s, e) for s, e in self._ring if s >= since]
            nxt = items[-1][0] + 1 if items else since
            return self.epoch, nxt, [e for _, e in items]

    # ── ops (exec downstream + busy-reject + f.cancel intercept)────────────────────

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def inflight(self) -> tuple:
        """The in-flight list (accumulated from rollup `data.jobs`; any id not in a terminal state is in flight)."""
        with self._lock:
            return tuple(sorted(self._inflight))

    def submit_exec(self, code, *, fresh=False, echo=True):
        """The one and only downstream exec channel (REPL line / fresh full
        run; form delta = ``echo=False``). Returns the exec id; when busy, a
        new write immediately gets a one-line reply (``ROW_BUSY``), is not
        queued, and None is returned. The only pass-through exception = the
        ``f.cancel`` intercept (works whether busy or idle; never enters
        exec). Reads do not go through here (hence never rejected)."""
        if str(code).strip() == CANCEL_COMMAND:
            self.cancel_inflight()
            return None
        with self._lock:
            if self._busy:
                reject = True
            else:
                reject = False
                self._busy = True
                exec_id = self._next_exec_id
                self._next_exec_id += 1
        if reject:
            self._ring_append(ipc.out(ROW_BUSY))    # the one-line reply is the final answer
            return None
        self._write(ipc.exec(exec_id, code, fresh=fresh, echo=echo))
        return exec_id

    def cancel_inflight(self):
        """Entry point of the ``f.cancel`` intercept (the card stop button =
        the same door): cancel the whole in-flight batch of this document →
        one one-line reply. Through ``on_cancel`` the host is called back to
        dial ``/gw`` directly; just-canceled ids leave the list immediately
        (no duplicate dials / inflated counts). Returns the list of canceled
        job ids; on callback failure an error line is recorded and the list
        is left untouched."""
        with self._lock:
            ids = sorted(self._inflight)
        if not self._dial(ids):
            return []
        with self._lock:
            for jid in ids:
                self._inflight.pop(jid, None)
        self._ring_append(ipc.out(self._cancel_row(ids)))
        return ids

    def _write(self, op: dict) -> None:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise RuntimeError("worker not started")
            self._proc.stdin.write(json.dumps(op, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()             # no concurrent writes: serialized under the same lock

    # ── pipe (reader thread: evs into the ring / a malformed line does not break the pipe / EOF fallback)──────────

    def _reader_loop(self, proc, ready: threading.Event) -> None:
        established = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if not isinstance(ev, dict):
                        raise ValueError("not an object")
                except ValueError:
                    # A malformed line does not break the pipe: record an
                    # error line and keep readline-ing
                    self._ring_append(ipc.out(f"bad pipe line: {line!r}",
                                              kind=ipc.OUT_KIND_ERROR))
                    continue
                self._ring_append(ev)            # take the line into the ring without interpreting its content
                if ev.get("ev") == ipc.EV_READY:
                    ready.set()
                    established = True
                elif ev.get("ev") == ipc.EV_DONE:
                    with self._lock:
                        self._busy = False        # busy released
                elif (ev.get("ev") == ipc.EV_OUT
                        and ev.get("kind") == ipc.OUT_KIND_PROGRESS):
                    self._update_inflight(ev.get("data") or {})
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if established and not self._closed:
            self._worker_died()                  # unified EOF fallback (kill and self-exit take the same path)

    def _update_inflight(self, data: dict) -> None:
        """The in-flight list is fed by the rollup ``data.jobs``: record the
        latest state per id; a terminal state (the net/states vocabulary)
        leaves the list."""
        jobs = data.get("jobs") or {}
        with self._lock:
            for jid, info in jobs.items():
                state = (info or {}).get("state")
                if state in TERMINAL_OK or state in TERMINAL_BAD:
                    self._inflight.pop(jid, None)
                else:
                    self._inflight[jid] = state

    def _dial(self, ids) -> bool:
        """Cancel callback (the host dials /gw); on failure, record an error line and return False."""
        if not ids:
            return True
        if self.on_cancel is None:
            return True
        try:
            self.on_cancel(list(ids))
            return True
        except Exception as e:                   # noqa: BLE001 — the reply line goes into the ring; never blocks the fallback
            self._ring_append(ipc.out(f"cancel failed: {e}",
                                      kind=ipc.OUT_KIND_ERROR))
            return False

    @staticmethod
    def _cancel_row(ids, *, restart=False) -> str:
        one, many = ((ROW_RESTART_ONE, ROW_RESTART_MANY) if restart
                     else (ROW_CANCELED_ONE, ROW_CANCELED_MANY))
        return one.format(id=ids[0]) if len(ids) == 1 else many.format(n=len(ids))

    def _worker_died(self) -> None:
        """The unified EOF fallback, five steps in a row: cancel the
        in-flight batch (same callback) → clear the list (a respawned
        worker does not recognize old entries) → reset busy → one one-line
        reply → respawn (startup recovery lives in the worker)."""
        with self._lock:
            ids = sorted(self._inflight)
            self._inflight.clear()
            self._busy = False
        self._dial(ids)                          # the fallback dials anyway; a failure only records an error line (the clear is unconditional)
        self._ring_append(ipc.out(self._cancel_row(ids, restart=True)))
        try:
            self._spawn_worker()
        except Exception as e:                   # noqa: BLE001 — a failed respawn reports one line; no respawn loop
            self._ring_append(ipc.out(f"worker restart failed: {e}",
                                      kind=ipc.OUT_KIND_ERROR))

    # ── spawn (start the worker + wait for ready; respawn takes the same path)──────────────────────

    def start(self) -> None:
        """Spawn the worker and wait for ``ready`` (the spawn completion criterion)."""
        with self._lock:
            if self._proc is not None:
                raise RuntimeError("session already started")
        self._spawn_worker()

    def restart_worker(self) -> None:
        """Session-surface 'restart worker' entry point (GUI button): kill →
        the unified EOF fallback (cancel in-flight + clear + busy reset +
        one-line reply + respawn), then wait for the respawn to finish."""
        with self._lock:
            gen = self._generation
            proc = self._proc
        if proc is None:
            raise RuntimeError("worker not started")
        proc.terminate()
        deadline = time.monotonic() + _READY_DEADLINE_S
        while time.monotonic() < deadline:
            with self._lock:
                if self._closed:
                    raise RuntimeError("session closed")
                if self._generation > gen:
                    return
            time.sleep(0.01)
        raise RuntimeError("worker restart timed out")

    def _spawn_worker(self) -> None:
        env = dict(os.environ)
        env.update({
            ENV_GW_BASE_URL: self.gw_base_url,
            ENV_WORKER_TOKEN: self.worker_token,
            ENV_DOC_PATH: self.doc_path,
            "PYTHONUTF8": "1",                   # pipe contract = UTF-8
        })
        proc = subprocess.Popen(
            self.worker_cmd, cwd=self.workspace,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=env)
        ready = threading.Event()
        t = threading.Thread(target=self._reader_loop, args=(proc, ready),
                             daemon=True)
        t.start()
        deadline = time.monotonic() + _READY_DEADLINE_S
        while not ready.is_set():
            if proc.poll() is not None:          # died before ready: the reader thread runs no fallback (not established)
                t.join(timeout=2)
                raise RuntimeError(
                    f"worker died before ready (exit {proc.returncode})")
            if time.monotonic() > deadline:      # deadline hit: kill the silent process, leave no orphans
                proc.terminate()
                t.join(timeout=2)
                raise RuntimeError("worker did not signal ready")
            time.sleep(0.01)
        with self._lock:
            self._proc = proc
            self._threads.append(t)
            self._generation += 1

    def close(self) -> None:
        """Close the session (host exit): kill the process, join the threads;
        no fallback is triggered (orphan jobs are neither hunted down nor
        announced)."""
        self._closed = True
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=3)
