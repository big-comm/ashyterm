# ashyterm/filemanager/operations.py
import ctypes
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

from gi.repository import GLib

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.translation_utils import _

# Pre-compiled pattern for rsync progress parsing
_PROGRESS_PERCENT_PATTERN = re.compile(r"(\d+)%")

# --- NEW: Kernel-level process lifecycle management ---
# Use ctypes to access the prctl system call for robust cleanup.
# PR_SET_PDEATHSIG: Asks the kernel to send a signal to this process
# when its parent dies. This is the most reliable way to ensure
# child processes (like rsync/ssh) do not get orphaned.
try:
    libc: ctypes.CDLL | None = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG: int | None = 1
except (OSError, AttributeError):
    libc = None
    PR_SET_PDEATHSIG = None
    # This will fallback to os.killpg if prctl is not available.


def set_pdeathsig_kill():
    """
    Function to be run in the child process before exec.
    It tells the kernel to send SIGKILL to this process when the parent exits.
    """
    if libc and PR_SET_PDEATHSIG is not None:
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def _drain_stderr_to_list(stderr_stream, output_list: list):
    """Drain ``stderr_stream`` in a thread to avoid pipe-buffer deadlock.

    Without this, a subprocess that reads stdout in a loop while writing
    to stderr can block once the stderr pipe buffer fills.
    """
    _drain_logger = get_logger("ashyterm.filemanager.operations")
    try:
        for line in iter(stderr_stream.readline, ""):
            output_list.append(line)
    except Exception as exc:
        _drain_logger.debug(f"stderr drain ended with error: {exc}")
    finally:
        try:
            stderr_stream.close()
        except Exception as exc:
            _drain_logger.debug(f"Could not close stderr stream: {exc}")


# --- End of process management setup ---


class OperationCancelledError(Exception):
    """Custom exception to indicate that an operation was cancelled by the user."""


class FileOperations:
    def __init__(self, session_item: SessionItem, spawner=None):
        self.session_item = session_item
        self._spawner = spawner
        self.logger = get_logger("ashyterm.filemanager.operations")
        self._command_cache: dict[
            str, dict[str, tuple[bool, float]]
        ] = {}  # {session_key: {command: (available, timestamp)}}
        self._cache_ttl = 300  # 5 minutes
        self._max_cache_sessions = 50  # Max number of sessions in cache
        self._active_processes: dict[str, Any] = {}
        self._lock = threading.Lock()

    def shutdown(self):
        """Terminate all active subprocess groups managed by this instance."""
        self.logger.info(
            f"Shutting down operations. Terminating {len(self._active_processes)} active process groups."
        )
        with self._lock:
            for transfer_id, process in list(self._active_processes.items()):
                try:
                    # Best effort to terminate gracefully first.
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    process.wait(timeout=2)
                except ProcessLookupError:
                    self.logger.warning(
                        f"Process for transfer {transfer_id} (PID: {process.pid}) not found. Already terminated?"
                    )
                except subprocess.TimeoutExpired:
                    self.logger.warning(
                        f"Process group for transfer {transfer_id} did not terminate in time, killing."
                    )
                    # Force kill if graceful shutdown fails
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception as e:
                    self.logger.error(
                        f"Error terminating process group for transfer {transfer_id}: {e}"
                    )
            self._active_processes.clear()

    def _get_session_key(self, session: SessionItem) -> str:
        return f"{session.user or ''}@{session.host}:{session.port or 22}"

    def _prune_expired_cache(self, now: float) -> None:
        """Remove cache entries past their TTL (per-session + session-level)."""
        for session_key in list(self._command_cache):
            entries = self._command_cache[session_key]
            for cmd in list(entries):
                _available, cached_at = entries[cmd]
                if now - cached_at >= self._cache_ttl:
                    del entries[cmd]
            if not entries:
                del self._command_cache[session_key]

    def _is_command_available(
        self, session: SessionItem, command: str, use_cache: bool = True
    ) -> bool:
        session_key = self._get_session_key(session)
        now = time.monotonic()
        if use_cache:
            self._prune_expired_cache(now)
            cached = self._command_cache.get(session_key, {}).get(command)
            if cached is not None:
                available, cached_at = cached
                if now - cached_at < self._cache_ttl:
                    return available

        check_command = ["command", "-v", command]
        success, _ = self.execute_command_on_session(
            check_command, session_override=session
        )

        if session_key not in self._command_cache:
            # Evict oldest sessions if cache exceeds size limit. Uses the
            # most-recently-updated timestamp per session so we evict truly
            # idle sessions first.
            if len(self._command_cache) >= self._max_cache_sessions:
                oldest_key = min(
                    self._command_cache,
                    key=lambda k: (
                        max(ts for _, ts in self._command_cache[k].values())
                        if self._command_cache[k]
                        else 0
                    ),
                )
                del self._command_cache[oldest_key]
            self._command_cache[session_key] = {}
        self._command_cache[session_key][command] = (success, now)
        return success

    def check_command_available(
        self,
        command: str,
        use_cache: bool = True,
        session_override: Optional[SessionItem] = None,
    ) -> bool:
        """
        Public helper to check the availability of a command in the current or
        overridden session. Optionally bypasses the local cache when a fresh
        verification is required.
        """
        session = session_override if session_override else self.session_item
        if not session:
            return False
        return self._is_command_available(session, command, use_cache=use_cache)

    def execute_command_on_session(
        self,
        command: List[str],
        session_override: Optional[SessionItem] = None,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Run ``command`` locally or over SSH. Returns ``(success, output)``."""
        session_to_use = session_override if session_override else self.session_item
        if not session_to_use:
            return False, _("No session context for file operation.")

        try:
            if session_to_use.is_local():
                result = subprocess.run(
                    command,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return (
                    (True, result.stdout)
                    if result.returncode == 0
                    else (False, result.stderr)
                )
            elif session_to_use.is_ssh():
                if self._spawner is None:
                    from ..terminal.spawner import get_spawner

                    self._spawner = get_spawner()
                # Use shorter timeout for file manager operations to avoid UI freeze
                return self._spawner.execute_remote_command_sync(
                    session_to_use, command, timeout=timeout
                )
        except subprocess.TimeoutExpired:
            self.logger.error(
                f"Command timed out after {timeout}s: {' '.join(command)}"
            )
            return False, _("Command timed out. Connection may be lost.")
        except Exception as e:
            self.logger.error(f"Command execution failed: {e}")
            return False, str(e)

        # This case should not be reached if session is always local or ssh
        return False, _("Unsupported session type for command execution.")

    def get_remote_file_timestamp(self, remote_path: str) -> Optional[int]:
        """Gets the modification timestamp of a remote file."""
        # The command 'stat -c %Y' is standard on GNU systems for getting the epoch timestamp.
        command = ["stat", "-c", "%Y", remote_path]
        success, output = self.execute_command_on_session(command)
        if success and output.strip().isdigit():
            return int(output.strip())
        self.logger.warning(
            f"Failed to get timestamp for {remote_path}. Output: {output}"
        )
        return None

    def get_directory_size(
        self,
        path: str,
        is_remote: bool = False,
        session_override: Optional[SessionItem] = None,
    ) -> int:
        """Total bytes at ``path`` via ``du -sb``; 0 on failure."""
        try:
            command = ["du", "-sb", path]

            if is_remote:
                success, output = self.execute_command_on_session(
                    command, session_override, timeout=30
                )
            else:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                success = result.returncode == 0
                output = result.stdout if success else result.stderr

            if success and output.strip():
                # Output format: "SIZE\tPATH"
                parts = output.strip().split()
                if parts and parts[0].isdigit():
                    return int(parts[0])
        except Exception as e:
            self.logger.warning(f"Failed to get directory size for {path}: {e}")

        return 0

    def get_free_space(
        self,
        path: str,
        is_remote: bool = False,
        session_override: Optional[SessionItem] = None,
    ) -> int:
        """Free bytes at ``path`` via ``df -B1 --output=avail``; -1 on failure."""
        try:
            command = ["df", "-B1", "--output=avail", path]

            if is_remote:
                success, output = self.execute_command_on_session(
                    command, session_override, timeout=10
                )
            else:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                success = result.returncode == 0
                output = result.stdout if success else result.stderr

            if success and output.strip():
                # Output format: "Avail\n12345678" (header + value)
                lines = output.strip().split("\n")
                if len(lines) >= 2:
                    avail_str = lines[1].strip()
                    if avail_str.isdigit():
                        return int(avail_str)
        except Exception as e:
            self.logger.warning(f"Failed to get free space for {path}: {e}")

        return -1

    def _start_process(self, transfer_id, command):
        """Spawn the rsync/ssh child with ``preexec_fn`` for SIGKILL on parent death."""
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # Capture stderr separately
            text=True,
            bufsize=1,
            universal_newlines=True,
            start_new_session=True,  # Create a new process group
            preexec_fn=set_pdeathsig_kill,  # Ensure kernel cleans it up if we die
        )
        with self._lock:
            self._active_processes[transfer_id] = process
        return process

    def _parse_transfer_error(self, output: str) -> str:
        """Parses command output to find specific, user-friendly error messages."""
        output_lower = output.lower()
        permission_errors = {
            "permission denied",
            "permissão negada",
            "operation not permitted",
        }
        if any(err in output_lower for err in permission_errors):
            return _("Permission Denied: Check write permissions on the destination.")

        # Fallback to a generic message if output is empty but an error occurred
        if not output.strip():
            return _("An unknown transfer error occurred.")

        return output.strip()

    def _transfer_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        direction: str,  # "download" or "upload"
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        """Unified rsync flow for up/download; ``direction`` picks source/dest roles."""
        is_download = direction == "download"
        op_label = _("Download") if is_download else _("Upload")

        def transfer_thread():
            process = None
            stderr_thread = None
            stderr_lines: list = []
            try:
                if self._spawner is None:
                    from ..terminal.spawner import get_spawner

                    self._spawner = get_spawner()
                spawner = self._spawner

                if self._is_command_available(session, "rsync"):
                    ssh_cmd = (
                        f"ssh -o ControlPath={spawner._get_ssh_control_path(session)}"
                    )

                    # Add trailing slash to source for rsync directory copy
                    source_rsync = source_path
                    if is_directory:
                        source_rsync = source_path.rstrip("/") + "/"

                    # Build rsync command based on direction
                    if is_download:
                        rsync_source = f"{session.user}@{session.host}:{source_rsync}"
                        rsync_dest = dest_path
                    else:
                        rsync_source = source_rsync
                        rsync_dest = f"{session.user}@{session.host}:{dest_path}"

                    transfer_cmd = [
                        "rsync",
                        "-avz",
                        "--progress",
                        "-e",
                        ssh_cmd,
                        rsync_source,
                        rsync_dest,
                    ]
                    process = self._start_process(transfer_id, transfer_cmd)

                    # Start stderr draining thread to prevent deadlock
                    stderr_thread = threading.Thread(
                        target=_drain_stderr_to_list,
                        args=(process.stderr, stderr_lines),
                        daemon=True,
                    )
                    stderr_thread.start()

                    full_output = ""
                    for line in iter(process.stdout.readline, ""):
                        if cancellation_event and cancellation_event.is_set():
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            raise OperationCancelledError(
                                f"{op_label} cancelled by user."
                            )

                        full_output += line
                        match = _PROGRESS_PERCENT_PATTERN.search(line)
                        if match and progress_callback:
                            progress = float(match.group(1))
                            GLib.idle_add(progress_callback, transfer_id, progress)

                    # Wait for stderr thread to complete
                    if stderr_thread and stderr_thread.is_alive():
                        stderr_thread.join(timeout=2.0)

                    stderr_output = "".join(stderr_lines)
                    process.wait()
                    exit_code = process.returncode

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            f"{op_label} completed successfully.",
                        )
                    else:
                        error_message = self._parse_transfer_error(
                            full_output + stderr_output
                        )
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            False,
                            error_message,
                        )
                else:  # SFTP fallback
                    sftp_cmd_base = spawner.command_builder.build_remote_command(
                        "sftp", session
                    )

                    def _sftp_quote(raw: str) -> str:
                        """Quote a path for inclusion in an sftp batch line.

                        sftp's batch parser uses double quotes with backslash
                        escapes. Backslash and double-quote are the only
                        metacharacters we need to escape.
                        """
                        return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'

                    # For SFTP directory copy, destination must be the parent directory
                    if is_download:
                        sftp_dest = (
                            str(Path(dest_path).parent) if is_directory else dest_path
                        )
                        sftp_batch = (
                            f"get -r {_sftp_quote(source_path)} "
                            f"{_sftp_quote(sftp_dest)}\nquit\n"
                        )
                    else:
                        sftp_dest = (
                            str(Path(dest_path).parent) if is_directory else dest_path
                        )
                        sftp_batch = (
                            f"put -r {_sftp_quote(source_path)} "
                            f"{_sftp_quote(sftp_dest)}\nquit\n"
                        )

                    with tempfile.NamedTemporaryFile(
                        mode="w", delete=False, suffix=".sftp"
                    ) as batch_file:
                        batch_file.write(sftp_batch)
                        batch_file_path = batch_file.name
                    transfer_cmd = sftp_cmd_base + ["-b", batch_file_path]

                    process = self._start_process(transfer_id, transfer_cmd)

                    stdout, stderr = process.communicate()
                    exit_code = process.returncode
                    if "batch_file_path" in locals() and Path(batch_file_path).exists():
                        Path(batch_file_path).unlink()

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            f"{op_label} completed successfully.",
                        )
                    else:
                        error_msg = self._parse_transfer_error(stdout + stderr)
                        GLib.idle_add(
                            completion_callback, transfer_id, False, error_msg
                        )

            except OperationCancelledError:
                self.logger.warning(f"{op_label} cancelled for {source_path}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, "Cancelled")
            except Exception as e:
                self.logger.error(f"Exception during {direction}: {e}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, str(e))
            finally:
                with self._lock:
                    if transfer_id in self._active_processes:
                        del self._active_processes[transfer_id]

        threading.Thread(target=transfer_thread, daemon=True).start()

    def start_download_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        remote_path: str,
        local_path: Path,
        is_directory: bool,
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        """Start a download operation with progress tracking."""
        self._transfer_with_progress(
            transfer_id=transfer_id,
            session=session,
            source_path=remote_path,
            dest_path=str(local_path),
            is_directory=is_directory,
            direction="download",
            progress_callback=progress_callback,
            completion_callback=completion_callback,
            cancellation_event=cancellation_event,
        )

    def start_upload_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        local_path: Path,
        remote_path: str,
        is_directory: bool,
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        """Start an upload operation with progress tracking."""
        self._transfer_with_progress(
            transfer_id=transfer_id,
            session=session,
            source_path=str(local_path),
            dest_path=remote_path,
            is_directory=is_directory,
            direction="upload",
            progress_callback=progress_callback,
            completion_callback=completion_callback,
            cancellation_event=cancellation_event,
        )
