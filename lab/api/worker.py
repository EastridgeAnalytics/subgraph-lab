"""The worker: queued runs, one at a time, in a background thread.

`POST /api/runs`, `POST /api/slice/run` and `POST /api/runs/{id}/resume` create or find a run, `submit` its id and
return at once. The thread takes the ids in order and calls `lab.pipeline.resume(run)`. The pipeline records a
failure on the manifest and raises; the worker logs it and carries on with the next run. It never pushes: the push is
`ops.push`, behind a confirm.

A run has one writer. Anything else that writes to a run's stores or its manifest while the worker holds it (a push,
a disposition, a search estimate, an MCP tool) goes through `exclusive(run_id)`, which refuses with `RunBusy` instead
of letting two writers meet. Reads open their own read-only snapshot and need no claim.
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from lab.api.view import keep_saving, read_manifest
from lab.common.log import get_logger
from lab.common.store import list_runs, run_paths
from lab.contracts.manifest import StepStatus

log = get_logger("api.worker")

STOP_WAIT = 20.0  # seconds `stop` waits for a step that is still running; the thread is a daemon either way


class RunBusy(RuntimeError):
    """The run is queued, running, or in use by another request."""


class Worker:
    def __init__(self, runner: Callable[[object], object] | None = None, *, on_idle: Callable[[], None] | None = None):
        """`runner(run)` is `lab.pipeline.resume` unless a test passes another. `on_idle()` is called after every run,
        whatever happened to it (the app uses it to make the Relate server look at the newest run)."""
        self._runner = runner
        self._on_idle = on_idle
        self._cv = threading.Condition()
        self._queue: deque[str] = deque()
        self._until: dict[str, str | None] = {}  # decision 38: the step a queued run stops after, or None for all
        self._current: str | None = None
        self._held: set[str] = set()
        self._stopping = False
        self._thread: threading.Thread | None = None

    # ---- the thread -----------------------------------------------------------------------------------------
    def start(self) -> None:
        with self._cv:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._loop, name="lab-worker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = STOP_WAIT) -> bool:
        """Stop taking runs. A run that is mid-step cannot be interrupted, so this waits up to `timeout` for it.
        Queued runs that never started stay `pending` on disk. True when the thread is gone."""
        with self._cv:
            self._stopping = True
            self._queue.clear()
            self._until.clear()
            self._cv.notify_all()
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        alive = thread.is_alive()
        if alive:
            log.warning("the worker is still running %s; the next start marks its step failed so it can resume", self._current)
        return not alive

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                run_id = self._queue.popleft()
                until = self._until.pop(run_id, None)
                self._current = run_id
            try:
                self._run(run_id, until)
            finally:
                with self._cv:
                    self._current = None
                if self._on_idle is not None:
                    try:
                        self._on_idle()
                    except Exception:  # noqa: BLE001 (a supervisor problem must not stop the worker)
                        log.exception("the worker's idle hook failed")

    def _run(self, run_id: str, until: str | None = None) -> None:
        from lab.runs import Run

        try:
            run = keep_saving(Run(run_id))  # a poll of the manifest must not make the pipeline's save fail
        except Exception:  # noqa: BLE001
            log.exception("run %s could not be opened; skipped", run_id)
            return
        try:
            runner = self._runner
            if runner is None:
                import lab.pipeline

                runner = lab.pipeline.resume  # looked up now, so a test can patch it
            runner(run) if until is None else runner(run, until=until)
        except Exception as e:  # noqa: BLE001 (the pipeline has put the failure on the manifest; the worker goes on)
            log.warning("run %s stopped: %s: %s", run_id, type(e).__name__, str(e)[:300])
        finally:
            run.close()  # the lab's writers off the stores; Relate's live server reads on

    # ---- queueing -------------------------------------------------------------------------------------------
    def submit(self, run_id: str, until: str | None = None) -> bool:
        """Queue a run, to stop after the step `until` or to run to its review. False when it is already queued or
        running (nothing is added twice). `RunBusy` when a request holds it."""
        with self._cv:
            if run_id in self._held:
                raise RunBusy(f"run {run_id} is in use by another request; try again in a moment")
            if run_id == self._current or run_id in self._queue:
                return False
            self._queue.append(run_id)
            self._until[run_id] = until
            self._cv.notify()
            return True

    @contextmanager
    def exclusive(self, run_id: str) -> Iterator[None]:
        """Claim a run for work done inline in a request. `RunBusy` when it is queued, running, or already claimed."""
        with self._cv:
            if run_id == self._current:
                raise RunBusy(f"run {run_id} is running; wait for it to reach its review, or resume it later")
            if run_id in self._queue:
                raise RunBusy(f"run {run_id} is queued; wait for it to run")
            if run_id in self._held:
                raise RunBusy(f"run {run_id} is in use by another request; try again in a moment")
            self._held.add(run_id)
        try:
            yield
        finally:
            with self._cv:
                self._held.discard(run_id)

    def busy(self) -> bool:
        """True while a run is running or waiting."""
        with self._cv:
            return self._current is not None or bool(self._queue)

    def snapshot(self) -> dict:
        with self._cv:
            return {"running": self._current, "queued": list(self._queue), "held": sorted(self._held)}

    # ---- after a crash --------------------------------------------------------------------------------------
    def recover_orphans(self) -> list[str]:
        """Call once at startup, before `start`. A step still marked running belongs to a lab that stopped (a crash, or
        a shutdown that could not wait): mark it failed with that reason, so the Run screen offers Resume."""
        fixed: list[str] = []
        for run_id in list_runs():
            path = run_paths(run_id).manifest
            try:
                manifest = read_manifest(path)
            except Exception:  # noqa: BLE001
                continue
            running = [s.name for s in manifest.steps if s.status is StepStatus.RUNNING]
            for name in running:
                manifest.fail(name, "the lab stopped while this step was running; resume the run to carry on")
            if running:
                manifest.save(path)
                fixed.append(run_id)
                log.warning("run %s: step %s was running when the lab stopped; marked failed", run_id, ", ".join(running))
        return fixed
