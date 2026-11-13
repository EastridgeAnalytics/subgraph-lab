"""`relate serve --read-only=live` for the current run's store (decision 21, page 07).

The lab image carries the `relate` binary and supervises one server. It serves the run store of the current run, the
newest run whose pull is done, so the app and an agent read the subgraph through Relate's own Bolt, HTTP and MCP
surfaces while the lab writes. `--read-only=live` holds no writer lease and follows the lab's commits. When a newer
run's pull finishes the server is restarted on that store.

Ports: inside the container (`LAB_RELATE_HOST` is not a loopback address) they are the container ports compose
publishes (7687, 7474, 7475); outside it they are the `LAB_RELATE_*_PORT` settings. `--auth none`: the ports are
published on 127.0.0.1 only (page 07).

If the binary is missing the API still runs; `status()` says so, and so does `/api/health`. What is being served is
recorded in `<LAB_DATA_DIR>/relate-serve.json`; the server's own output goes to `relate-serve.log` beside it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from lab.api.view import read_manifest
from lab.common.log import get_logger
from lab.common.relate_cli import relate_bin, relate_cli, relate_env
from lab.common.store import list_runs, run_paths
from lab.contracts.manifest import StepStatus, now_iso
from lab.settings import Settings, get_settings

log = get_logger("api.relate_server")

LOOPBACK = ("127.0.0.1", "localhost", "::1")
CONTAINER_PORTS = {"lab": 8000, "bolt": 7687, "http": 7474, "mcp": 7475}  # what compose.yaml publishes
RECORD = "relate-serve.json"
LOG_FILE = "relate-serve.log"
TERMINATE_WAIT = 10.0

_version: str | None = None


def in_container(settings: Settings | None = None) -> bool:
    """True when `LAB_RELATE_HOST` is not a loopback address: compose sets it to 0.0.0.0 inside the lab container."""
    return (settings or get_settings()).relate_host not in LOOPBACK


def serve_ports(settings: Settings | None = None) -> dict[str, int]:
    """The ports to listen on: the container's own inside the container, the settings' (host ports) outside it."""
    s = settings or get_settings()
    if in_container(s):
        return dict(CONTAINER_PORTS)
    return {"lab": s.port, "bolt": s.relate_bolt_port, "http": s.relate_http_port, "mcp": s.relate_mcp_port}


def relate_version() -> tuple[bool, str]:
    """(found, text): Relate's version from `relate --version` ("1.0.1"), or why it cannot be had. Cached once it works."""
    global _version
    if _version:
        return True, _version
    try:
        result = relate_cli(["--version"], check=False, timeout=15)
    except FileNotFoundError as e:
        return False, f"not found: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:200]
    first = (result.stdout.strip().splitlines() or [""])[0].strip()
    if result.returncode != 0 or not first:
        return False, f"relate --version failed ({result.returncode}): {result.error_line()}"[:200]
    _version = first.removeprefix("Relate").strip().removeprefix("v")
    return True, _version


def current_run_id(data_dir: Path | None = None) -> str | None:
    """The newest run whose pull is done, or None."""
    for run_id in list_runs(data_dir):  # newest first
        try:
            manifest = read_manifest(run_paths(run_id, data_dir).manifest)
        except Exception:  # noqa: BLE001 (a manifest being rewritten; the next look finds it)
            continue
        if manifest.step("pull").status is StepStatus.DONE and run_paths(run_id, data_dir).store.exists():
            return run_id
    return None


class RelateServer:
    def __init__(self, *, popen: Callable[..., subprocess.Popen] = subprocess.Popen, poll_seconds: float = 2.0,
                 retry_seconds: float = 15.0):
        self._popen = popen
        self._poll = poll_seconds
        self._retry = retry_seconds
        self._lock = threading.RLock()
        self._proc = None
        self._serving: dict | None = None
        self._error: str | None = None
        self._attempt: tuple[str | None, float] | None = None  # (run id, when): no retry of a failure inside `retry`
        self._log = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._job = None

    # ---- the supervising thread ------------------------------------------------------------------------------
    def start(self) -> None:
        """Serve the current run now, and keep looking for a newer one."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._watch, name="lab-relate-server", daemon=True)
            self._thread.start()

    def _watch(self) -> None:
        while True:
            try:
                self.ensure()
            except Exception:  # noqa: BLE001
                log.exception("the Relate server supervisor failed")
            if self._stop.wait(self._poll):
                return

    def stop(self) -> None:
        """Stop looking, and stop the server. Safe to call twice."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._poll + TERMINATE_WAIT + 5)
        with self._lock:
            self._terminate()
            self._serving = None

    # ---- one look ---------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Look now (the worker calls this when a run ends) instead of waiting for the next poll."""
        if not self._stop.is_set():
            self.ensure()

    def ensure(self) -> None:
        """Serve the current run's store: start, restart on a newer run, or restart a server that died."""
        with self._lock:
            settings = get_settings()
            run_id = current_run_id(settings.data_dir)
            alive = self._proc is not None and self._proc.poll() is None
            if alive and self._serving and self._serving["run_id"] == run_id:
                return
            if alive:
                self._terminate()  # a newer run: the store being served is no longer the current one
                self._serving = None
            elif self._proc is not None:
                self._note_exit()
            if run_id is None:
                return
            if self._attempt and self._attempt[0] == run_id and time.monotonic() - self._attempt[1] < self._retry:
                return  # it failed a moment ago; the same attempt would fail the same way
            self._launch(run_id, settings)

    # ---- the process ------------------------------------------------------------------------------------------
    def command(self, store: Path, settings: Settings | None = None) -> list[str]:
        s = settings or get_settings()
        p = serve_ports(s)
        return [relate_bin(s), "serve", "--database", str(store), "--read-only=live", "--host", s.relate_host,
                "--bolt", str(p["bolt"]), "--http", str(p["http"]), "--mcp", str(p["mcp"]), "--auth", "none"]

    def _launch(self, run_id: str, settings: Settings) -> None:
        self._attempt = (run_id, time.monotonic())
        store = run_paths(run_id, settings.data_dir).store.resolve()
        try:
            cmd = self.command(store, settings)
        except FileNotFoundError as e:
            self._error = str(e)
            log.warning("not serving %s: %s", run_id, e)
            return
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log = open(data_dir / LOG_FILE, "wb")  # noqa: SIM115 (kept open for the life of the child)
        try:
            self._proc = self._popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, env=relate_env())
        except OSError as e:
            self._error = f"could not start relate serve: {e}"
            self._proc = None
            self._close_log()
            log.warning(self._error)
            return
        self._bind_to_this_process(self._proc)
        p = serve_ports(settings)
        self._error = None
        self._serving = {"run_id": run_id, "store": str(store), "pid": getattr(self._proc, "pid", None),
                         "host": settings.relate_host, "bolt": p["bolt"], "http": p["http"], "mcp": p["mcp"],
                         "started_at": now_iso()}
        (data_dir / RECORD).write_text(json.dumps(self._serving, indent=2), encoding="utf-8")
        log.info("relate serve --read-only=live on %s (bolt %s, http %s, mcp %s), pid %s", run_id, p["bolt"], p["http"],
                 p["mcp"], self._serving["pid"])

    def _note_exit(self) -> None:
        """The server is gone and nobody asked it to be: keep the last lines of its output as the reason."""
        code = self._proc.poll() if self._proc is not None else None
        tail = self._log_tail()
        run_id = self._serving["run_id"] if self._serving else None
        self._error = f"relate serve exited with code {code}" + (f": {tail}" if tail else "")
        log.warning("%s (serving %s)", self._error, run_id)
        self._proc = None
        self._serving = None
        self._close_log()
        self._attempt = (run_id, time.monotonic()) if run_id else self._attempt

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(TERMINATE_WAIT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(TERMINATE_WAIT)
        self._close_log()
        if self._serving is not None:
            try:
                (Path(get_settings().data_dir) / RECORD).unlink(missing_ok=True)
            except OSError:
                pass

    def _close_log(self) -> None:
        handle, self._log = self._log, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _log_tail(self, lines: int = 3) -> str:
        try:
            text = (Path(get_settings().data_dir) / LOG_FILE).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return " | ".join(text.strip().splitlines()[-lines:])[:400]

    def _bind_to_this_process(self, proc) -> None:
        """On Windows, tie the child to this process so that it dies with it, even when the lab is killed by PID. A job
        object with kill-on-close does that. Elsewhere (the container) the child ends with its parent's namespace."""
        if sys.platform != "win32" or getattr(proc, "_handle", None) is None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Counters(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class Extended(ctypes.Structure):
                _fields_ = [("Basic", Basic), ("Io", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel = ctypes.windll.kernel32
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            if self._job is None:
                job = kernel.CreateJobObjectW(None, None)
                info = Extended()
                info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                kernel.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
                self._job = job
            kernel.AssignProcessToJobObject(wintypes.HANDLE(self._job), wintypes.HANDLE(int(proc._handle)))
        except Exception:  # noqa: BLE001 (a convenience: without it, stopping the lab by PID leaves the child)
            log.debug("could not bind the Relate server to this process", exc_info=True)

    # ---- what the API says ------------------------------------------------------------------------------------
    def status(self) -> dict:
        """What is being served and what went wrong, for `/api/health` and the logs. Never holds a credential."""
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            found, version = relate_version()
            return {"binary": found, "version": version if found else None, "error": self._error if found else version,
                    "serving": dict(self._serving) if alive and self._serving else None}
