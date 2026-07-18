# ashyterm/filemanager/operations.py
import ctypes
import os
import re
import signal
import shlex
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
from .accelerated_download import (
    AcceleratedDownloadCancelled,
    AcceleratedDownloadConfig,
    AcceleratedDownloadUnavailable,
    AsyncSSHSegmentedDownloader,
)

# Pre-compiled pattern for rsync progress parsing
_PROGRESS_PERCENT_PATTERN = re.compile(r"(\d+)%")
_RSYNC_COMPRESSION_MODES = {"auto", "always", "never"}
_ACCELERATED_DOWNLOAD_DEFAULT_REQUESTS = 6
_ACCELERATED_DOWNLOAD_MIN_REQUESTS = 2
_ACCELERATED_DOWNLOAD_MAX_REQUESTS = 10
_ACCELERATED_DOWNLOAD_DEFAULT_MIN_SIZE_MB = 64
_ACCELERATED_DOWNLOAD_CHUNK_SIZE_BYTES = 4 * 1024 * 1024
_INCOMPRESSIBLE_EXTENSIONS = frozenset(
    {
        ".7z",
        ".apk",
        ".avi",
        ".bz2",
        ".deb",
        ".flac",
        ".gif",
        ".gz",
        ".iso",
        ".jpeg",
        ".jpg",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".pdf",
        ".png",
        ".rar",
        ".webm",
        ".webp",
        ".xz",
        ".zip",
        ".zst",
    }
)

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


def set_pdeathsig_kill() -> None:
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


def _format_exception_for_log(exc: Exception) -> str:
    message = str(exc)
    exc_type = type(exc).__name__
    return f"{exc_type}: {message}" if message else exc_type


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
        # OpenSSH ControlMaster cannot safely bootstrap concurrent commands
        # against the same socket. Serialize remote probes/listings while
        # keeping them off the GTK main thread.
        self._remote_command_lock = threading.Lock()

    def shutdown(self) -> None:
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
                with self._remote_command_lock:
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

        if "input/output error" in output_lower:
            detail_lines = [
                line.strip()
                for line in output.splitlines()
                if "input/output error" in line.lower()
            ][:5]
            detail = "\n".join(detail_lines)
            message = _(
                "Remote input/output error: the server could not read one or more source files."
            )
            return f"{message}\n{detail}" if detail else message

        # Fallback to a generic message if output is empty but an error occurred
        if not output.strip():
            return _("An unknown transfer error occurred.")

        return output.strip()

    def _schedule_transfer_completion(
        self, completion_callback, transfer_id: str, success: bool, message: str
    ) -> None:
        if completion_callback:
            GLib.idle_add(completion_callback, transfer_id, success, message)

    def _build_rsync_ssh_command(self, spawner, session: SessionItem) -> str:
        """Return the ssh command used by rsync's ``-e`` option."""
        ssh_options = spawner._get_base_ssh_options(session)
        ssh_options["BatchMode"] = "yes"
        cmd = spawner.command_builder.build_remote_command(
            "ssh",
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
        )
        return shlex.join(cmd[:-1])

    def _get_rsync_remote_target(self, session: SessionItem) -> str:
        """Return the rsync remote target, with user only when configured."""
        return f"{session.user}@{session.host}" if session.user else session.host

    def _build_sftp_command(self, spawner, session: SessionItem) -> list[str]:
        """Return the base sftp command for transfer fallback."""
        sftp_options = spawner._get_base_ssh_options(session)
        sftp_options["BatchMode"] = "yes"
        return spawner.command_builder.build_remote_command(
            "sftp",
            hostname=session.host,
            username=session.user if session.user else None,
            port=session.port if session.port != 22 else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=sftp_options,
        )

    def _get_rsync_compression_mode(self) -> str:
        try:
            from ..settings.manager import get_settings_manager

            mode = get_settings_manager().get("file_transfer_rsync_compression", "auto")
        except Exception as exc:
            self.logger.debug(f"Could not read rsync compression setting: {exc}")
            mode = "auto"
        return mode if mode in _RSYNC_COMPRESSION_MODES else "auto"

    def _path_has_incompressible_extension(self, path: str) -> bool:
        lower_path = path.lower()
        return any(lower_path.endswith(ext) for ext in _INCOMPRESSIBLE_EXTENSIONS)

    def _get_rsync_archive_flags(
        self,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        compression_mode: Optional[str] = None,
    ) -> str:
        mode = compression_mode or self._get_rsync_compression_mode()
        if mode == "always":
            return "-avz"
        if mode == "never":
            return "-av"
        if is_directory:
            return "-avz"
        if self._path_has_incompressible_extension(
            source_path
        ) or self._path_has_incompressible_extension(dest_path):
            return "-av"
        return "-avz"

    def _get_setting_value(self, key: str, default: Any) -> Any:
        try:
            from ..settings.manager import get_settings_manager

            return get_settings_manager().get(key, default)
        except Exception as exc:
            self.logger.debug(f"Could not read setting {key}: {exc}")
            return default

    def _get_clamped_int_setting(
        self, key: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = self._get_setting_value(key, default)
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    def _get_accelerated_download_settings(self) -> tuple[bool, int, int, int, str]:
        enabled = bool(
            self._get_setting_value("file_transfer_accelerated_downloads", True)
        )
        parallel_requests = self._get_clamped_int_setting(
            "file_transfer_parallel_requests",
            _ACCELERATED_DOWNLOAD_DEFAULT_REQUESTS,
            _ACCELERATED_DOWNLOAD_MIN_REQUESTS,
            _ACCELERATED_DOWNLOAD_MAX_REQUESTS,
        )
        min_size_mb = self._get_clamped_int_setting(
            "file_transfer_accelerated_min_size_mb",
            _ACCELERATED_DOWNLOAD_DEFAULT_MIN_SIZE_MB,
            0,
            4096,
        )
        connect_timeout = self._get_clamped_int_setting(
            "ssh_connect_timeout",
            30,
            1,
            300,
        )
        strict_host_key = str(
            self._get_setting_value("ssh_strict_host_key_checking", "accept-new")
        )
        if strict_host_key not in {"ask", "accept-new", "yes", "no"}:
            strict_host_key = "accept-new"
        return (
            enabled,
            parallel_requests,
            min_size_mb * 1024 * 1024,
            connect_timeout,
            strict_host_key,
        )

    def _should_use_accelerated_download(
        self, session: SessionItem, is_directory: bool, file_size: int
    ) -> bool:
        enabled, parallel_requests, min_size_bytes, _timeout, _strict_host_key = (
            self._get_accelerated_download_settings()
        )
        if not enabled or parallel_requests < _ACCELERATED_DOWNLOAD_MIN_REQUESTS:
            return False
        if not session or not session.is_ssh():
            return False
        if file_size < min_size_bytes:
            return False
        if session.proxy_jump:
            return False
        if session.uses_password_auth() and not session.auth_value:
            return False
        return True

    def _start_accelerated_download_with_fallback(
        self,
        transfer_id: str,
        session: SessionItem,
        remote_path: str,
        local_path: Path,
        is_directory: bool,
        file_size: int,
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ) -> None:
        """Start an AsyncSSH segmented download and fallback to rsync/SFTP on error."""

        def accelerated_thread():
            (
                _enabled,
                parallel_requests,
                _min_size_bytes,
                connect_timeout,
                strict_host_key,
            ) = self._get_accelerated_download_settings()
            config = AcceleratedDownloadConfig(
                parallel_requests=parallel_requests,
                chunk_size_bytes=_ACCELERATED_DOWNLOAD_CHUNK_SIZE_BYTES,
                connect_timeout=connect_timeout,
                strict_host_key_checking=strict_host_key,
            )

            def emit_progress(progress: float) -> None:
                if progress_callback:
                    GLib.idle_add(progress_callback, transfer_id, progress)

            try:
                downloader = AsyncSSHSegmentedDownloader(
                    session,
                    config,
                    progress_callback=emit_progress,
                    cancellation_event=cancellation_event,
                )
                if is_directory:
                    downloader.download_directory(
                        remote_path,
                        local_path,
                        min_segment_size=_min_size_bytes,
                    )
                else:
                    downloader.download(
                        remote_path, local_path, expected_size=file_size
                    )
                self._schedule_transfer_completion(
                    completion_callback,
                    transfer_id,
                    True,
                    _("Download completed successfully."),
                )
            except AcceleratedDownloadCancelled:
                self.logger.warning(f"Download cancelled for {remote_path}")
                self._schedule_transfer_completion(
                    completion_callback, transfer_id, False, "Cancelled"
                )
            except AcceleratedDownloadUnavailable as exc:
                self.logger.info(
                    f"Accelerated download unavailable for {remote_path}; falling back: {exc}"
                )
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
            except Exception as exc:
                error_detail = _format_exception_for_log(exc)
                self.logger.warning(
                    f"Accelerated download failed for {remote_path}; falling back: {error_detail}"
                )
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

        threading.Thread(target=accelerated_thread, daemon=True).start()

    def _remove_sftp_batch_file(self, batch_file_path: Optional[str]) -> None:
        if not batch_file_path:
            return
        try:
            Path(batch_file_path).unlink(missing_ok=True)
        except OSError as exc:
            self.logger.warning(
                f"Failed to remove SFTP batch file {batch_file_path}: {exc}"
            )

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
    ) -> None:
        """Unified rsync flow for up/download; ``direction`` picks source/dest roles."""
        threading.Thread(
            target=self._run_transfer_with_progress,
            kwargs={
                "transfer_id": transfer_id,
                "session": session,
                "source_path": source_path,
                "dest_path": dest_path,
                "is_directory": is_directory,
                "direction": direction,
                "progress_callback": progress_callback,
                "completion_callback": completion_callback,
                "cancellation_event": cancellation_event,
            },
            daemon=True,
        ).start()

    def _run_transfer_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        direction: str,
        progress_callback,
        completion_callback,
        cancellation_event: Optional[threading.Event],
    ) -> None:
        is_download = direction == "download"
        op_label = _("Download") if is_download else _("Upload")
        try:
            spawner = self._get_transfer_spawner()
            if self._is_command_available(session, "rsync"):
                self._run_rsync_transfer(
                    transfer_id,
                    session,
                    source_path,
                    dest_path,
                    is_directory,
                    is_download,
                    op_label,
                    spawner,
                    progress_callback,
                    completion_callback,
                    cancellation_event,
                )
            else:
                self._run_sftp_transfer(
                    transfer_id,
                    session,
                    source_path,
                    dest_path,
                    is_directory,
                    is_download,
                    op_label,
                    spawner,
                    completion_callback,
                )
        except OperationCancelledError:
            self.logger.warning(f"{op_label} cancelled for {source_path}")
            self._schedule_transfer_completion(
                completion_callback, transfer_id, False, "Cancelled"
            )
        except Exception as exc:
            self.logger.error(f"Exception during {direction}: {exc}")
            self._schedule_transfer_completion(
                completion_callback, transfer_id, False, str(exc)
            )
        finally:
            with self._lock:
                self._active_processes.pop(transfer_id, None)

    def _get_transfer_spawner(self):
        if self._spawner is None:
            from ..terminal.spawner import get_spawner

            self._spawner = get_spawner()
        return self._spawner

    def _run_rsync_transfer(
        self,
        transfer_id: str,
        session: SessionItem,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        is_download: bool,
        op_label: str,
        spawner,
        progress_callback,
        completion_callback,
        cancellation_event: Optional[threading.Event],
    ) -> None:
        command = self._build_rsync_transfer_command(
            session,
            source_path,
            dest_path,
            is_directory,
            is_download,
            spawner,
        )
        process = self._start_process(transfer_id, command)
        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=_drain_stderr_to_list,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()
        stdout = self._read_rsync_output(
            process,
            transfer_id,
            op_label,
            progress_callback,
            cancellation_event,
        )
        if stderr_thread.is_alive():
            stderr_thread.join(timeout=2.0)
        process.wait()
        self._complete_process_transfer(
            process.returncode,
            stdout + "".join(stderr_lines),
            completion_callback,
            transfer_id,
            op_label,
        )

    def _build_rsync_transfer_command(
        self,
        session: SessionItem,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        is_download: bool,
        spawner,
    ) -> list[str]:
        source_rsync = source_path.rstrip("/") + "/" if is_directory else source_path
        remote_target = self._get_rsync_remote_target(session)
        if is_download:
            rsync_source = f"{remote_target}:{source_rsync}"
            rsync_dest = dest_path
        else:
            rsync_source = source_rsync
            rsync_dest = f"{remote_target}:{dest_path}"
        return [
            "rsync",
            self._get_rsync_archive_flags(source_path, dest_path, is_directory),
            "--progress",
            "-e",
            self._build_rsync_ssh_command(spawner, session),
            rsync_source,
            rsync_dest,
        ]

    @staticmethod
    def _read_rsync_output(
        process,
        transfer_id: str,
        op_label: str,
        progress_callback,
        cancellation_event: Optional[threading.Event],
    ) -> str:
        output: list[str] = []
        for line in iter(process.stdout.readline, ""):
            if cancellation_event and cancellation_event.is_set():
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                raise OperationCancelledError(f"{op_label} cancelled by user.")
            output.append(line)
            match = _PROGRESS_PERCENT_PATTERN.search(line)
            if match and progress_callback:
                GLib.idle_add(progress_callback, transfer_id, float(match.group(1)))
        return "".join(output)

    def _run_sftp_transfer(
        self,
        transfer_id: str,
        session: SessionItem,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        is_download: bool,
        op_label: str,
        spawner,
        completion_callback,
    ) -> None:
        batch_file_path: Optional[str] = None
        try:
            batch_text = self._build_sftp_batch(
                source_path, dest_path, is_directory, is_download
            )
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False, suffix=".sftp"
            ) as batch_file:
                batch_file.write(batch_text)
                batch_file_path = batch_file.name
            command = self._build_sftp_command(spawner, session) + [
                "-b",
                batch_file_path,
            ]
            process = self._start_process(transfer_id, command)
            stdout, stderr = process.communicate()
            self._complete_process_transfer(
                process.returncode,
                stdout + stderr,
                completion_callback,
                transfer_id,
                op_label,
            )
        finally:
            self._remove_sftp_batch_file(batch_file_path)

    def _build_sftp_batch(
        self,
        source_path: str,
        dest_path: str,
        is_directory: bool,
        is_download: bool,
    ) -> str:
        destination = str(Path(dest_path).parent) if is_directory else dest_path
        operation = "get" if is_download else "put"
        return (
            f"{operation} -r {self._quote_sftp_batch_path(source_path)} "
            f"{self._quote_sftp_batch_path(destination)}\nquit\n"
        )

    @staticmethod
    def _quote_sftp_batch_path(path: str) -> str:
        if any(character in path for character in "\r\n\0"):
            raise ValueError(_("Invalid path format"))
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _complete_process_transfer(
        self,
        exit_code: int,
        output: str,
        completion_callback,
        transfer_id: str,
        op_label: str,
    ) -> None:
        success = exit_code == 0
        message = (
            f"{op_label} completed successfully."
            if success
            else self._parse_transfer_error(output)
        )
        self._schedule_transfer_completion(
            completion_callback, transfer_id, success, message
        )

    def start_download_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        remote_path: str,
        local_path: Path,
        is_directory: bool,
        file_size: int = 0,
        progress_callback: Any = None,
        completion_callback: Any = None,
        cancellation_event: Optional[threading.Event] = None,
    ) -> None:
        """Start a download operation with progress tracking."""
        if self._should_use_accelerated_download(session, is_directory, file_size):
            self._start_accelerated_download_with_fallback(
                transfer_id=transfer_id,
                session=session,
                remote_path=remote_path,
                local_path=local_path,
                is_directory=is_directory,
                file_size=file_size,
                progress_callback=progress_callback,
                completion_callback=completion_callback,
                cancellation_event=cancellation_event,
            )
            return
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
        progress_callback: Any = None,
        completion_callback: Any = None,
        cancellation_event: Optional[threading.Event] = None,
    ) -> None:
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
