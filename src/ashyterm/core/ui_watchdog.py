"""GTK main-loop stall diagnostics."""

import faulthandler
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..utils.logger import get_logger

DEFAULT_STALL_TIMEOUT_SECONDS = 5.0
DEFAULT_HEARTBEAT_INTERVAL_MS = 500
DEFAULT_CHECK_INTERVAL_SECONDS = 0.5


class UiWatchdog:
    """Record Python stacks when the GTK main loop stops processing events."""

    def __init__(
        self,
        log_dir: Path,
        stall_timeout_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS,
        heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS,
        check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self.log_dir = log_dir
        self.stall_timeout_seconds = max(1.0, float(stall_timeout_seconds))
        self.heartbeat_interval_ms = max(100, int(heartbeat_interval_ms))
        self.check_interval_seconds = max(0.1, float(check_interval_seconds))
        self.logger = get_logger("ashyterm.core.ui_watchdog")
        self.report_path: Optional[Path] = None
        self._active = False
        self._stall_reported = False
        self._last_heartbeat = time.monotonic()
        self._heartbeat_source_id: Optional[int] = None
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Start main-loop heartbeats and the daemon observer."""
        with self._state_lock:
            if self._active:
                return True
            self._active = True
            self._stall_reported = False
            self._last_heartbeat = time.monotonic()
            self._stop_event.clear()

        try:
            source_id = GLib.timeout_add(
                self.heartbeat_interval_ms, self._record_heartbeat
            )
            worker = threading.Thread(
                target=self._watch_loop,
                name="ashy-ui-watchdog",
                daemon=True,
            )
            with self._state_lock:
                self._heartbeat_source_id = source_id
                self._thread = worker
            worker.start()
            return True
        except Exception as exc:
            self.logger.warning(f"Could not start UI watchdog: {exc}")
            self.stop()
            return False

    def stop(self) -> None:
        """Stop monitoring without blocking application shutdown."""
        with self._state_lock:
            self._active = False
            source_id = self._heartbeat_source_id
            self._heartbeat_source_id = None
            worker = self._thread
            self._thread = None
        self._stop_event.set()

        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        if worker and worker is not threading.current_thread():
            try:
                worker.join(timeout=1.0)
            except RuntimeError:
                pass

    def _record_heartbeat(self) -> bool:
        with self._state_lock:
            if not self._active:
                return GLib.SOURCE_REMOVE
            self._last_heartbeat = time.monotonic()
            self._stall_reported = False
        return GLib.SOURCE_CONTINUE

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self.check_interval_seconds):
            self._check_for_stall(time.monotonic())

    def _check_for_stall(self, now: float) -> None:
        with self._state_lock:
            stalled_for = now - self._last_heartbeat
            if (
                not self._active
                or self._stall_reported
                or stalled_for < self.stall_timeout_seconds
            ):
                return
            self._stall_reported = True
        self._dump_stall_report(stalled_for)

    def _dump_stall_report(self, stalled_for: float) -> None:
        try:
            report_path = self._get_report_path()
            with report_path.open("a", encoding="utf-8") as report:
                self._write_report_header(report, stalled_for)
                faulthandler.dump_traceback(file=report, all_threads=True)
                report.write("\n")
                report.flush()
            self.logger.error(f"UI stall report written to {report_path}")
        except Exception as exc:
            self.logger.error(f"Could not write UI stall report: {exc}")

    def _get_report_path(self) -> Path:
        if self.report_path is None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.report_path = self.log_dir / (
                f"ui-watchdog-{timestamp}-{os.getpid()}.log"
            )
        return self.report_path

    def _write_report_header(self, report: TextIO, stalled_for: float) -> None:
        report.write("=== AshyTerm UI stall detected ===\n")
        report.write(f"timestamp={datetime.now().astimezone().isoformat()}\n")
        report.write(f"pid={os.getpid()}\n")
        report.write(f"stalled_for_seconds={stalled_for:.3f}\n")
        report.write(self._format_native_wait_states())
        report.write("\nPython thread stacks:\n")
        report.flush()

    @staticmethod
    def _format_native_wait_states() -> str:
        task_root = Path("/proc/self/task")
        if not task_root.is_dir():
            return "native_wait_states=unavailable\n"

        lines = ["Native thread wait states:"]
        try:
            task_dirs = sorted(task_root.iterdir(), key=lambda path: int(path.name))
        except (OSError, ValueError):
            return "native_wait_states=unavailable\n"

        for task_dir in task_dirs:
            try:
                name = (task_dir / "comm").read_text(encoding="utf-8").strip()
                wait_state = (task_dir / "wchan").read_text(encoding="utf-8").strip()
                lines.append(f"  tid={task_dir.name} name={name} wchan={wait_state}")
            except OSError:
                continue
        return "\n".join(lines) + "\n"
