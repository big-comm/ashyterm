# ashyterm/terminal/process_tracker.py

import os
import shutil
import signal
import threading
import time
from typing import Any, Dict

from ..utils.logger import get_logger


class ProcessTracker:
    """Track launched processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a launched process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}

    def snapshot(self) -> Dict[int, Dict[str, Any]]:
        """Return a consistent snapshot of tracked processes.

        Callers use this instead of touching the private map/lock so we can
        change the internal representation without breaking them.
        """
        with self._lock:
            return dict(self._processes)

    def unregister_process(self, pid: int) -> bool:
        """Unregister a process."""
        with self._lock:
            if pid in self._processes:
                process_info = self._processes.pop(pid)
                temp_dir_path = process_info.get("temp_dir_path")
                if temp_dir_path:
                    try:
                        shutil.rmtree(temp_dir_path)
                        self.logger.debug(
                            f"Cleaned up temp zshrc directory: {temp_dir_path}"
                        )
                    except Exception as e:
                        self.logger.error(
                            f"Failed to clean up temp zshrc directory {temp_dir_path}: {e}"
                        )
                sshpass_file = process_info.get("sshpass_file")
                if sshpass_file:
                    try:
                        os.unlink(sshpass_file)
                    except OSError:
                        pass
                return True
            return False

    def terminate_process(self, pid: int) -> None:
        """Terminate specific PID safely. Used by window managers to clean up own children."""
        with self._lock:
            if pid in self._processes:
                self.logger.info(f"Terminating specific process {pid}")
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                finally:
                    self.unregister_process(pid)

    def terminate_all(self) -> None:
        """Terminate all tracked processes robustly on Linux."""
        with self._lock:
            pids_to_terminate = list(self._processes.keys())
            if not pids_to_terminate:
                return

            self.logger.info(f"Terminating {len(pids_to_terminate)} tracked processes.")

            for pid in pids_to_terminate:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    self.unregister_process(pid)

            time.sleep(0.2)

            remaining_pids = list(self._processes.keys())
            for pid in remaining_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    self.logger.warning(
                        f"Process {pid} did not respond to SIGTERM, sent SIGKILL."
                    )
                except OSError:
                    pass
                finally:
                    self.unregister_process(pid)
