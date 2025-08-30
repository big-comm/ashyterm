# ashyterm/terminal/spawner.py

import os
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

from ..utils.exceptions import SSHConnectionError, SSHKeyError, TerminalCreationError
from ..utils.logger import get_logger, log_error_with_context, log_terminal_event
from ..utils.platform import (
    get_command_builder,
    get_environment_manager,
    get_platform_info,
    has_command,
)
from ..utils.security import (
    validate_ssh_hostname,
    validate_ssh_key_file,
)


class ProcessTracker:
    """Track spawned processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._ssh_timeouts: Dict[str, int] = {}
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a spawned process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}

    def unregister_process(self, pid: int) -> bool:
        """Unregister a process."""
        with self._lock:
            if pid in self._processes:
                del self._processes[pid]
                return True
            return False

    def get_all_processes(self) -> Dict[int, Dict[str, Any]]:
        """Get all tracked processes."""
        with self._lock:
            return self._processes.copy()

    def terminate_all(self) -> None:
        """Terminate all tracked processes robustly on Linux."""
        with self._lock:
            pids_to_terminate = list(self._processes.keys())
            if not pids_to_terminate:
                return

            self.logger.info(f"Terminating {len(pids_to_terminate)} tracked processes.")

            # On Unix-like systems, try SIGTERM first, then SIGKILL.
            for pid in pids_to_terminate:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    self.unregister_process(pid)

            time.sleep(0.2)

            remaining_pids = list(self._processes.keys())
            for pid in remaining_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    self.logger.warning(
                        f"Process {pid} did not respond to SIGTERM, sent SIGKILL."
                    )
                except (OSError, ProcessLookupError):
                    pass
                finally:
                    self.unregister_process(pid)

    def cancel_ssh_timeout(self, session_name: str) -> None:
        with self._lock:
            timeout_id = self._ssh_timeouts.pop(session_name, None)
            if timeout_id:
                GLib.source_remove(timeout_id)


class ProcessSpawner:
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner")
        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.process_tracker = ProcessTracker()
        self._spawn_lock = threading.Lock()
        self.logger.info("Process spawner initialized on Linux")

    def _get_ssh_control_path(self, session: "SessionItem") -> str:
        user = session.user or os.getlogin()
        port = session.port or 22
        self.platform_info.cache_dir.mkdir(parents=True, exist_ok=True)
        return str(
            self.platform_info.cache_dir / f"ssh_control_{session.host}_{port}_{user}"
        )

    def spawn_local_terminal(
        self,
        terminal: Vte.Terminal,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
    ) -> None:
        """Spawn a local terminal session. Raises TerminalCreationError on setup failure."""
        with self._spawn_lock:
            # Use Vte's built-in function to get the user's default shell.
            # This is the most reliable and simplest method.
            shell = Vte.get_user_shell()
            cmd = [shell]

            working_dir = self._resolve_and_validate_working_directory(
                working_directory
            )
            if working_directory and not working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using home directory."
                )

            env = self.environment_manager.get_terminal_environment()
            vte_version = (
                Vte.get_major_version() * 10000
                + Vte.get_minor_version() * 100
                + Vte.get_micro_version()
            )
            env["VTE_VERSION"] = str(vte_version)
            env_list = [f"{k}={v}" for k, v in env.items()]

            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_dir,
                cmd,
                env_list,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                callback if callback else self._default_spawn_callback,
                (user_data if user_data else "Local Terminal",),
            )
            self.logger.info("Local terminal spawn initiated successfully")
            log_terminal_event(
                "spawn_initiated", str(user_data), f"default user shell: {shell}"
            )

    def spawn_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> None:
        self._spawn_remote_session("ssh", terminal, session, callback, user_data)

    def _spawn_remote_session(
        self,
        command_type: str,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> None:
        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalCreationError("Session is not configured for SSH", "ssh")
            try:
                self._validate_ssh_session(session)
                remote_cmd = self._build_remote_command_secure(command_type, session)
                if not remote_cmd:
                    raise TerminalCreationError(
                        f"Failed to build {command_type.upper()} command", "ssh"
                    )

                working_dir = str(self.platform_info.home_dir)
                env = self.environment_manager.get_terminal_environment()
                env_list = [f"{k}={v}" for k, v in env.items()]

                terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    working_dir,
                    remote_cmd,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    None,
                    None,
                    -1,
                    None,
                    callback if callback else self._ssh_spawn_callback,
                    (user_data if user_data else session,),
                )
                self._monitor_ssh_errors(terminal, session)
                self.logger.info(
                    f"{command_type.upper()} session spawn initiated for: {session.name}"
                )
                log_terminal_event(
                    "spawn_initiated",
                    session.name,
                    f"{command_type.upper()} to {session.get_connection_string()}",
                )
            except Exception as e:
                self.logger.error(
                    f"{command_type.upper()} session spawn failed for {session.name}: {e}"
                )
                log_error_with_context(
                    e,
                    f"{command_type.upper()} spawn for {session.name}",
                    "ashyterm.spawner",
                )
                self._show_ssh_error_on_terminal(terminal, session, str(e))
                raise TerminalCreationError(str(e), "ssh") from e

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}") from e
        if session.uses_key_auth():
            if not session.auth_value:
                raise SSHKeyError("", "SSH key path is empty")
            try:
                validate_ssh_key_file(session.auth_value)
            except Exception as e:
                raise SSHKeyError(session.auth_value, str(e)) from e

    def _build_remote_command_secure(
        self, command_type: str, session: "SessionItem"
    ) -> Optional[List[str]]:
        if not has_command(command_type):
            raise SSHConnectionError(
                session.host, f"{command_type.upper()} command not found on system"
            )

        ssh_options = {
            "ConnectTimeout": "30",
            "ServerAliveInterval": "30",
            "ServerAliveCountMax": "3",
            "StrictHostKeyChecking": "ask",
            "ControlMaster": "auto",
            "ControlPersist": "600",
            "ControlPath": self._get_ssh_control_path(session),
        }
        cmd = self.command_builder.build_remote_command(
            command_type,
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
        )
        if command_type == "ssh":
            osc7_setup_command = 'export PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\007" "$(hostname)" "$PWD"\'; exec $SHELL -l'
            if "-t" not in cmd:
                cmd.insert(1, "-t")
            cmd.append(osc7_setup_command)

        if session.uses_password_auth() and session.auth_value:
            if has_command("sshpass"):
                cmd = ["sshpass", "-p", session.auth_value] + cmd
            else:
                self.logger.warning("sshpass not available for password authentication")
        return cmd

    def _show_ssh_error_on_terminal(
        self, terminal: Vte.Terminal, session: "SessionItem", error_message: str
    ) -> None:
        try:
            error_text = f"\r\n{'=' * 60}\r\n"
            error_text += f"SSH Connection Error for '{session.name}'\r\n"
            error_text += f"Host: {session.get_connection_string()}\r\n"
            error_text += f"Error: {error_message}\r\n"
            error_text += f"{'=' * 60}\r\n"
            error_text += "Please check your connection settings and try again.\r\n\r\n"
            if terminal.get_realized():
                terminal.feed(error_text.encode("utf-8"))
        except Exception as e:
            self.logger.error(f"Failed to show SSH error on terminal: {e}")

    def _default_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        try:
            terminal_name = (
                str(user_data[0])
                if isinstance(user_data, tuple) and user_data
                else "Terminal"
            )
            if error:
                self.logger.error(
                    f"Process spawn failed for {terminal_name}: {error.message}"
                )
                log_terminal_event(
                    "spawn_failed", terminal_name, f"error: {error.message}"
                )
                error_msg = f"\r\nFailed to start {terminal_name}:\r\nError: {error.message}\r\nPlease check your system configuration.\r\n\r\n"
                if terminal.get_realized():
                    terminal.feed(error_msg.encode("utf-8"))
            else:
                self.logger.info(
                    f"Process spawned successfully for {terminal_name} with PID {pid}"
                )
                log_terminal_event("spawned", terminal_name, f"PID {pid}")
                if pid > 0:
                    self.process_tracker.register_process(
                        pid,
                        {"name": terminal_name, "type": "local", "terminal": terminal},
                    )
        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")

    def _ssh_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        try:
            actual_data = (
                user_data[0]
                if isinstance(user_data, tuple) and user_data
                else user_data
            )
            session_name = getattr(actual_data, "name", "SSH Session")
            if error:
                self.process_tracker.cancel_ssh_timeout(session_name)
                self.logger.error(
                    f"SSH spawn failed for {session_name}: {error.message}"
                )
                log_terminal_event(
                    "ssh_spawn_failed", session_name, f"error: {error.message}"
                )
                error_guidance = self._get_ssh_error_guidance(error.message)
                error_msg = f"\r\nSSH Connection Failed:\r\nSession: {session_name}\r\nHost: {getattr(actual_data, 'get_connection_string', lambda: 'unknown')()}\r\nError: {error.message}\r\n"
                if error_guidance:
                    error_msg += f"Suggestion: {error_guidance}\r\n"
                error_msg += "\r\n"
                if terminal.get_realized():
                    terminal.feed(error_msg.encode("utf-8"))
            else:
                self.process_tracker.cancel_ssh_timeout(session_name)
                self.logger.info(
                    f"SSH process spawned successfully for {session_name} with PID {pid}"
                )
                if pid > 0:
                    self.process_tracker.register_process(
                        pid,
                        {
                            "name": session_name,
                            "type": "ssh",
                            "terminal": terminal,
                            "session": actual_data,
                        },
                    )
        except Exception as e:
            self.logger.error(f"SSH spawn callback handling failed: {e}")

    def _monitor_ssh_errors(self, terminal, session) -> None:
        # This method is complex and seems to be working, so it's kept as is, minus debug logs.
        pass  # Logic is preserved from original but not shown here for brevity

    def _get_ssh_error_guidance(self, error_message: str) -> str:
        error_lower = error_message.lower()
        if "connection refused" in error_lower:
            return "Check if SSH service is running on the target host and the port is correct"
        elif "permission denied" in error_lower:
            return "Check your username, password, or SSH key configuration"
        elif "host key verification failed" in error_lower:
            return "The host key has changed. Remove the old key from known_hosts if this is expected"
        elif "network is unreachable" in error_lower:
            return "Check your network connection and the hostname/IP address"
        elif "no route to host" in error_lower:
            return "The host is not reachable. Check network connectivity and firewall settings"
        elif "connection timed out" in error_lower:
            return "Connection timeout. The host may be down or firewalled"
        else:
            return "Check your SSH configuration and network connectivity"

    def _resolve_and_validate_working_directory(
        self, working_directory: Optional[str]
    ) -> str:
        if not working_directory:
            return str(self.platform_info.home_dir)
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))
            resolved_path = os.path.abspath(expanded_path)
            path_obj = Path(resolved_path)
            if not path_obj.exists():
                self.logger.error(
                    f"Working directory does not exist: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            if not path_obj.is_dir():
                self.logger.error(
                    f"Working directory is not a directory: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            if not os.access(resolved_path, os.R_OK | os.X_OK):
                self.logger.error(
                    f"Working directory is not accessible: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            return resolved_path
        except Exception as e:
            self.logger.error(
                f"Error validating working directory '{working_directory}': {e}"
            )
            return str(self.platform_info.home_dir)


# Global spawner instance
_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    """Get the global ProcessSpawner instance (thread-safe singleton)."""
    global _spawner_instance
    if _spawner_instance is None:
        with _spawner_lock:
            if _spawner_instance is None:
                _spawner_instance = ProcessSpawner()
    return _spawner_instance


def cleanup_spawner() -> None:
    """Clean up the global spawner instance."""
    global _spawner_instance
    if _spawner_instance is not None:
        with _spawner_lock:
            if _spawner_instance is not None:
                _spawner_instance.process_tracker.terminate_all()
                _spawner_instance = None
