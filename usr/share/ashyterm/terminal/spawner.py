# ashyterm/terminal/spawner.py

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

from ..utils.exceptions import (SSHConnectionError, SSHKeyError,
                                TerminalSpawnError)
from ..utils.logger import (get_logger, log_error_with_context,
                            log_terminal_event)
from ..utils.platform import (get_command_builder, get_environment_manager,
                              get_platform_info, get_shell_detector,
                              has_command, is_windows)
from ..utils.security import (HostnameValidator, InputSanitizer,
                              validate_ssh_hostname, validate_ssh_key_file)


class ProcessTracker:
    """Track spawned processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._ssh_timeouts: Dict[str, int] = {}  # Track SSH connection timeouts
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a spawned process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}
            self.logger.debug(
                f"Process registered: PID={pid}, type={process_info.get('type', 'unknown')}"
            )

    def unregister_process(self, pid: int) -> bool:
        """Unregister a process."""
        with self._lock:
            if pid in self._processes:
                process_info = self._processes.pop(pid)
                self.logger.debug(
                    f"Process unregistered: PID={pid}, type={process_info.get('type', 'unknown')}"
                )
                return True
            return False

    def get_process_info(self, pid: int) -> Optional[Dict[str, Any]]:
        """Get process information."""
        with self._lock:
            return self._processes.get(pid, {}).copy()

    def get_all_processes(self) -> Dict[int, Dict[str, Any]]:
        """Get all tracked processes."""
        with self._lock:
            return self._processes.copy()

    def terminate_all(self) -> None:
        """Terminate all tracked processes robustly and cross-platform."""
        with self._lock:
            pids_to_terminate = list(self._processes.keys())
            if not pids_to_terminate:
                return

            self.logger.info(f"Terminating {len(pids_to_terminate)} tracked processes.")

            if is_windows():
                # On Windows, use taskkill for a more reliable shutdown.
                for pid in pids_to_terminate:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            check=False,
                            capture_output=True,
                        )
                        self.logger.debug(f"Sent taskkill to process {pid}")
                    except (OSError, FileNotFoundError):
                        self.logger.error("taskkill command not found.")
                        break  # Stop trying if the command doesn't exist
                    finally:
                        self.unregister_process(pid)
            else:
                # On Unix-like systems, try SIGTERM first, then SIGKILL.
                # Step 1: Send SIGTERM to all
                for pid in pids_to_terminate:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        self.logger.debug(f"Sent SIGTERM to process {pid}")
                    except (OSError, ProcessLookupError):
                        # The process may have already terminated
                        self.unregister_process(pid)

                # Give some time for processes to terminate gracefully
                time.sleep(0.2)

                # Step 2: Send SIGKILL to those that remain
                remaining_pids = list(self._processes.keys())
                for pid in remaining_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                        self.logger.warning(
                            f"Process {pid} did not respond to SIGTERM, sent SIGKILL."
                        )
                    except (OSError, ProcessLookupError):
                        pass  # The process terminated in the meantime
                    finally:
                        self.unregister_process(pid)

    def register_ssh_timeout(self, session_name: str, timeout_id: int) -> None:
        """Register an SSH connection timeout."""
        with self._lock:
            self._ssh_timeouts[session_name] = timeout_id

    def cancel_ssh_timeout(self, session_name: str) -> None:
        """Cancel an SSH connection timeout."""
        with self._lock:
            timeout_id = self._ssh_timeouts.pop(session_name, None)
            if timeout_id:
                GLib.source_remove(timeout_id)

    def cleanup_ssh_timeouts(self) -> None:
        """Cancel all SSH connection timeouts."""
        with self._lock:
            for timeout_id in self._ssh_timeouts.values():
                GLib.source_remove(timeout_id)
            self._ssh_timeouts.clear()


class ProcessSpawner:
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner")
        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.shell_detector = get_shell_detector()

        # Process tracking
        self.process_tracker = ProcessTracker()

        # Thread safety
        self._spawn_lock = threading.Lock()

        # Statistics
        self._stats = {
            "local_spawns": 0,
            "ssh_spawns": 0,
            "spawn_failures": 0,
            "processes_terminated": 0,
        }

        self.logger.info(
            f"Process spawner initialized on {self.platform_info.platform_type.value}"
        )

    def spawn_local_terminal(
        self,
        terminal: Vte.Terminal,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
    ) -> bool:
        """
        Spawn a local terminal session with enhanced platform support.

        Args:
            terminal: Vte.Terminal widget
            callback: Optional callback function for spawn completion
            user_data: Optional user data for callback
            working_directory: Optional working directory for the terminal

        Returns:
            True if spawn initiated successfully
        """
        with self._spawn_lock:
            try:
                self.logger.debug("Starting local terminal spawn")

                # Get platform-appropriate shell
                shell_path, shell_type = self.shell_detector.get_user_shell()
                shell_args = self.shell_detector.get_shell_command_args(shell_type)

                # Build command
                cmd = [shell_path] + shell_args

                # Resolve working directory with comprehensive validation
                working_dir = self._resolve_and_validate_working_directory(
                    working_directory
                )

                if working_directory and working_dir:
                    self.logger.info(f"Using working directory: {working_dir}")
                elif working_directory and not working_dir:
                    self.logger.warning(
                        f"Invalid working directory '{working_directory}', using home directory: {self.platform_info.home_dir}"
                    )
                else:
                    self.logger.debug(f"Using default working directory: {working_dir}")

                # Get environment
                env = self.environment_manager.get_terminal_environment()

                # Add VTE_VERSION to enable shell integration (OSC7)
                vte_version = (
                    Vte.get_major_version() * 10000
                    + Vte.get_minor_version() * 100
                    + Vte.get_micro_version()
                )
                env["VTE_VERSION"] = str(vte_version)
                self.logger.debug(f"Setting VTE_VERSION={env['VTE_VERSION']}")

                env_list = [f"{k}={v}" for k, v in env.items()]

                self.logger.debug(f"Spawning local terminal: {cmd}")

                # Validate command exists
                if not Path(shell_path).exists():
                    raise TerminalSpawnError(
                        shell_path, f"Shell not found: {shell_path}"
                    )

                # Spawn the process
                terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    working_dir,
                    cmd,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    None,  # Child setup function
                    None,  # Child setup data
                    -1,  # Timeout (-1 for no timeout)
                    None,  # Cancellable
                    callback if callback else self._default_spawn_callback,
                    (user_data if user_data else "Local Terminal",),
                )

                self._stats["local_spawns"] += 1
                self.logger.info("Local terminal spawn initiated successfully")
                log_terminal_event(
                    "spawn_initiated", str(user_data), f"local shell: {shell_path}"
                )

                return True

            except Exception as e:
                self._stats["spawn_failures"] += 1
                self.logger.error(f"Local terminal spawn failed: {e}")
                log_error_with_context(e, "local terminal spawn", "ashyterm.spawner")

                if isinstance(e, TerminalSpawnError):
                    raise
                else:
                    raise TerminalSpawnError("local shell", str(e))

    def spawn_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> bool:
        """Spawns an SSH terminal session."""
        # This method now calls the generic spawner with the 'ssh' command
        return self._spawn_remote_session("ssh", terminal, session, callback, user_data)

    def spawn_sftp_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> bool:
        """Spawns an SFTP terminal session."""
        # This method calls the generic spawner with the 'sftp' command
        return self._spawn_remote_session(
            "sftp", terminal, session, callback, user_data
        )

    def _spawn_remote_session(
        self,
        command_type: str,  # 'ssh' or 'sftp'
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> bool:
        """Generic logic for spawning remote sessions (SSH or SFTP)."""
        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalSpawnError(
                    command_type, "Session is not configured for SSH"
                )

            try:
                self.logger.debug(
                    f"Starting {command_type.upper()} spawn for session: {session.name}"
                )
                self._validate_ssh_session(session)

                # Build the command (ssh or sftp)
                remote_cmd = self._build_remote_command_secure(command_type, session)
                if not remote_cmd:
                    raise TerminalSpawnError(
                        command_type, f"Failed to build {command_type.upper()} command"
                    )

                working_dir = str(self.platform_info.home_dir)
                env = self.environment_manager.get_terminal_environment()
                env_list = [f"{k}={v}" for k, v in env.items()]

                self.logger.debug(
                    f"{command_type.upper()} command: {' '.join(remote_cmd)}"
                )

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

                # Monitor SSH output for errors
                self._monitor_ssh_errors(terminal, session)

                self._stats["ssh_spawns"] += 1
                self.logger.info(
                    f"{command_type.upper()} session spawn initiated for: {session.name}"
                )
                log_terminal_event(
                    "spawn_initiated",
                    session.name,
                    f"{command_type.upper()} to {session.get_connection_string()}",
                )
                return True

            except Exception as e:
                self._stats["spawn_failures"] += 1
                self.logger.error(
                    f"{command_type.upper()} session spawn failed for {session.name}: {e}"
                )
                log_error_with_context(
                    e,
                    f"{command_type.upper()} spawn for {session.name}",
                    "ashyterm.spawner",
                )
                self._show_ssh_error_on_terminal(terminal, session, str(e))
                raise TerminalSpawnError(command_type, str(e))

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        """
        Validate SSH session configuration with security checks.

        Args:
            session: SessionItem to validate

        Raises:
            Various SSH-related exceptions for different validation failures
        """
        # Validate hostname
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}")

        # Validate authentication configuration
        if session.uses_key_auth():
            if not session.auth_value:
                raise SSHKeyError("", "SSH key path is empty")

            try:
                validate_ssh_key_file(session.auth_value)
            except Exception as e:
                raise SSHKeyError(session.auth_value, str(e))

        elif session.uses_password_auth():
            if not session.auth_value:
                self.logger.warning(
                    f"Password authentication configured but no password provided for {session.name}"
                )

        # Validate username
        if session.user:
            sanitized_user = InputSanitizer.sanitize_username(session.user)
            if sanitized_user != session.user:
                self.logger.warning(
                    f"Username sanitized for session {session.name}: '{session.user}' -> '{sanitized_user}'"
                )

        # Check if hostname resolves (non-blocking)
        try:
            ip = HostnameValidator.resolve_hostname(session.host, timeout=5.0)
            if ip:
                self.logger.debug(f"Hostname {session.host} resolves to {ip}")
                if HostnameValidator.is_private_ip(ip):
                    self.logger.info(f"Connecting to private IP: {ip}")
            else:
                self.logger.warning(f"Hostname {session.host} could not be resolved")
        except Exception as e:
            self.logger.debug(
                f"Hostname resolution check failed for {session.host}: {e}"
            )

    def _build_remote_command_secure(
        self, command_type: str, session: "SessionItem"
    ) -> Optional[List[str]]:
        """Builds a remote command (SSH or SFTP) securely."""
        try:
            if not has_command(command_type):
                raise SSHConnectionError(
                    session.host, f"{command_type.upper()} command not found on system"
                )

            # Common options for SSH and SFTP
            ssh_options = {
                "ConnectTimeout": "30",
                "ServerAliveInterval": "30",
                "ServerAliveCountMax": "3",
                "StrictHostKeyChecking": "ask",
            }

            # The CommandBuilder now needs a generic method
            cmd = self.command_builder.build_remote_command(
                command_type,
                hostname=session.host,
                port=session.port if session.port != 22 else None,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=ssh_options,
            )

            # For SSH, inject a remote command to set up PROMPT_COMMAND for OSC7.
            # This version uses corrected quoting to work reliably.
            if command_type == "ssh":
                try:
                    # This string is carefully crafted to be passed as a single argument to the remote shell.
                    # 1. `bash -c '...'`: Executes the entire string within the single quotes on the remote host.
                    # 2. `export PROMPT_COMMAND=...`: Sets the prompt command.
                    # 3. `printf ...`: The command to be executed at each prompt.
                    # 4. The single quotes around the printf command ensure it's treated as a single value for PROMPT_COMMAND.
                    # 5. `$(hostname)` and `$PWD` are NOT escaped, so they are evaluated by the remote shell dynamically.
                    osc7_setup_command = 'export PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\007" "$(hostname)" "$PWD"\'; exec $SHELL -l'

                    if "-t" not in cmd:
                        cmd.insert(
                            1, "-t"
                        )  # Force pseudo-terminal allocation, often needed for interactive shells

                    # The entire command is passed as a single argument
                    cmd.append(osc7_setup_command)
                    self.logger.debug("Enhanced SSH command with dynamic OSC7 support.")
                except Exception as e:
                    self.logger.warning(f"Could not enhance SSH with OSC7: {e}")

            # sshpass logic remains the same
            if session.uses_password_auth() and session.auth_value:
                if has_command("sshpass"):
                    cmd = ["sshpass", "-p", session.auth_value] + cmd
                else:
                    self.logger.warning(
                        "sshpass not available for password authentication"
                    )

            return cmd

        except Exception as e:
            self.logger.error(f"{command_type.upper()} command building failed: {e}")
            raise SSHConnectionError(session.host, f"Command building failed: {e}")

    def _feed_terminal_safely(self, terminal: Vte.Terminal, message: str) -> None:
        """
        Safely feed text to terminal with error handling.

        Args:
            terminal: Terminal widget
            message: Message to display
        """
        try:
            if terminal.get_realized():
                terminal.feed(message.encode("utf-8"))
        except Exception as e:
            self.logger.debug(f"Failed to feed message to terminal: {e}")

    def _show_ssh_error_on_terminal(
        self, terminal: Vte.Terminal, session: "SessionItem", error_message: str
    ) -> None:
        """
        Display SSH error message on terminal with enhanced formatting.

        Args:
            terminal: Terminal widget
            session: SessionItem that failed
            error_message: Error message to display
        """
        try:
            error_text = f"\r\n{'=' * 60}\r\n"
            error_text += f"SSH Connection Error for '{session.name}'\r\n"
            error_text += f"Host: {session.get_connection_string()}\r\n"
            error_text += f"Error: {error_message}\r\n"
            error_text += f"{'=' * 60}\r\n"
            error_text += "Please check your connection settings and try again.\r\n\r\n"

            self._feed_terminal_safely(terminal, error_text)

        except Exception as e:
            self.logger.error(f"Failed to show SSH error on terminal: {e}")

    def _default_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """
        Enhanced default callback for process spawn completion.

        Args:
            terminal: Terminal widget
            pid: Process ID (or -1 if failed)
            error: Error object if spawn failed
            user_data: User data passed to spawn (as tuple)
        """
        try:
            # Unpack user_data if it's a tuple
            if isinstance(user_data, tuple) and len(user_data) > 0:
                terminal_name = str(user_data[0])
            else:
                terminal_name = str(user_data) if user_data else "Terminal"

            if error:
                self.logger.error(
                    f"Process spawn failed for {terminal_name}: {error.message}"
                )
                log_terminal_event(
                    "spawn_failed", terminal_name, f"error: {error.message}"
                )

                # Show user-friendly error message
                error_msg = f"\r\nFailed to start {terminal_name}:\r\n"
                error_msg += f"Error: {error.message}\r\n"
                error_msg += "Please check your system configuration.\r\n\r\n"
                self._feed_terminal_safely(terminal, error_msg)

            else:
                self.logger.info(
                    f"Process spawned successfully for {terminal_name} with PID {pid}"
                )
                log_terminal_event("spawned", terminal_name, f"PID {pid}")

                if pid > 0:
                    # Register process for tracking
                    process_info = {
                        "name": terminal_name,
                        "type": "local",
                        "terminal": terminal,
                    }
                    self.process_tracker.register_process(pid, process_info)

        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")

    def _ssh_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """
        Enhanced callback for SSH process spawn completion.

        Args:
            terminal: Terminal widget
            pid: Process ID (or -1 if failed)
            error: Error object if spawn failed
            user_data: SessionItem or user data (as tuple)
        """
        try:
            # Unpack user_data if it's a tuple
            if isinstance(user_data, tuple) and len(user_data) > 0:
                actual_data = user_data[0]
            else:
                actual_data = user_data

            session_name = "SSH Session"
            session_host = "unknown"

            if hasattr(actual_data, "name"):  # SessionItem
                session_name = actual_data.name
                session_host = actual_data.get_connection_string()
            elif actual_data:
                session_name = str(actual_data)

            if error:
                # Cancel timeout since spawn failed
                self.process_tracker.cancel_ssh_timeout(session_name)

                self.logger.error(
                    f"SSH spawn failed for {session_name}: {error.message}"
                )
                log_terminal_event(
                    "ssh_spawn_failed", session_name, f"error: {error.message}"
                )

                # Determine error type and provide specific guidance
                error_guidance = self._get_ssh_error_guidance(error.message)

                error_msg = "\r\nSSH Connection Failed:\r\n"
                error_msg += f"Session: {session_name}\r\n"
                error_msg += f"Host: {session_host}\r\n"
                error_msg += f"Error: {error.message}\r\n"
                if error_guidance:
                    error_msg += f"Suggestion: {error_guidance}\r\n"
                error_msg += "\r\n"

                self._feed_terminal_safely(terminal, error_msg)

            else:
                # Cancel timeout since spawn succeeded
                self.process_tracker.cancel_ssh_timeout(session_name)

                self.logger.info(
                    f"SSH process spawned successfully for {session_name} with PID {pid}"
                )
                log_terminal_event(
                    "ssh_spawned", session_name, f"PID {pid} to {session_host}"
                )

                if pid > 0:
                    # Register process for tracking
                    process_info = {
                        "name": session_name,
                        "type": "ssh",
                        "terminal": terminal,
                        "session": actual_data
                        if hasattr(actual_data, "name")
                        else None,
                    }
                    self.process_tracker.register_process(pid, process_info)

        except Exception as e:
            self.logger.error(f"SSH spawn callback handling failed: {e}")

    def _monitor_ssh_errors(self, terminal, session) -> None:
        """Monitor SSH terminal output for error messages and show dialogs."""
        try:
            session_name = session.name if hasattr(session, "name") else str(session)
            session_host = (
                session.get_connection_string()
                if hasattr(session, "get_connection_string")
                else "unknown"
            )

            # Track if we've already shown an error dialog for this session
            error_shown_key = f"ssh_error_shown_{session_name}_{int(time.time())}"

            def on_terminal_output(terminal, *args):
                try:
                    # Check if we've already shown an error for this session
                    if hasattr(self, error_shown_key):
                        return True

                    # Get all terminal text
                    start_row, start_col = 0, 0
                    end_row, end_col = (
                        terminal.get_row_count(),
                        terminal.get_column_count(),
                    )

                    try:
                        # Try to get text using VTE method
                        text_data = terminal.get_text_range(
                            start_row, start_col, end_row, end_col, lambda *args: True
                        )
                        if text_data and len(text_data) > 0:
                            text = (
                                text_data[0]
                                if isinstance(text_data, tuple)
                                else str(text_data)
                            )
                        else:
                            text = ""
                    except Exception as e:
                        self.logger.debug(f"Error getting terminal text: {e}")
                        text = ""

                    if not text or len(text.strip()) < 10:
                        return True

                    text_lower = text.lower()

                    # Common SSH error patterns - check for these in terminal output
                    error_patterns = [
                        ("connection timed out", "timeout", "SSH connection timed out"),
                        ("operation timed out", "timeout", "SSH operation timed out"),
                        (
                            "connection refused",
                            "refused",
                            "Connection was refused by the server",
                        ),
                        (
                            "host key verification failed",
                            "hostkey",
                            "Host key verification failed",
                        ),
                        ("permission denied", "permission", "Authentication failed"),
                        (
                            "could not resolve hostname",
                            "dns",
                            "Hostname could not be resolved",
                        ),
                        ("no route to host", "network", "No network route to host"),
                        ("connection reset by peer", "reset", "Connection was reset"),
                        (
                            "network is unreachable",
                            "unreachable",
                            "Network is unreachable",
                        ),
                        (
                            "ssh: connect to host",
                            "connection",
                            "Failed to connect to SSH host",
                        ),
                    ]

                    # Check for SSH error patterns
                    for pattern, error_type, friendly_msg in error_patterns:
                        if pattern in text_lower:
                            self.logger.info(
                                f"SSH error detected: {pattern} for {session_host}"
                            )

                            # Mark error as shown to prevent multiple dialogs
                            setattr(self, error_shown_key, True)

                            # Show error dialog
                            GLib.timeout_add(
                                100,
                                lambda: self._show_ssh_error_dialog(
                                    session_host, friendly_msg, error_type
                                ),
                            )
                            return False  # Stop monitoring after error found

                    return True  # Continue monitoring

                except Exception as e:
                    self.logger.error(f"Error monitoring SSH output: {e}")
                    return True

            # Connect to terminal content changes and child exit
            terminal.connect("contents-changed", on_terminal_output)

            # Also monitor for process exit
            def on_child_exited(terminal, exit_status):
                try:
                    if exit_status != 0 and not hasattr(self, error_shown_key):
                        # Process exited with error, check terminal content
                        GLib.timeout_add(200, lambda: on_terminal_output(terminal))
                except Exception as e:
                    self.logger.error(f"Error in child exit handler: {e}")

            terminal.connect("child-exited", on_child_exited)

        except Exception as e:
            self.logger.error(f"Failed to setup SSH error monitoring: {e}")

    def _show_ssh_error_dialog(
        self, ssh_target: str, error_message: str, error_type: str
    ) -> None:
        """Show SSH error dialog with detailed information."""

        def show_dialog():
            try:
                import gi

                from ..ui.ssh_dialogs import SSHTimeoutDialog

                gi.require_version("Gtk", "4.0")
                from gi.repository import Gtk as GtkApp

                # Get the main application window
                app = GtkApp.Application.get_default()
                parent_window = None
                if app and hasattr(app, "get_active_window"):
                    parent_window = app.get_active_window()

                # Customize dialog based on error type
                if error_type == "timeout":
                    dialog = SSHTimeoutDialog(
                        parent_window=parent_window,
                        ssh_target=ssh_target,
                        timeout_seconds=30,
                    )
                else:
                    # Use a generic error dialog approach
                    dialog = self._create_ssh_error_dialog(
                        parent_window, ssh_target, error_message, error_type
                    )

                def on_response(dialog, response_id):
                    if response_id == "retry":
                        self.logger.info(
                            f"User requested retry for SSH connection to {ssh_target}"
                        )
                        # Could implement retry logic here
                    dialog.destroy()

                dialog.connect("response", on_response)
                dialog.present()

            except Exception as e:
                self.logger.error(f"Failed to show SSH error dialog: {e}")

        # Schedule dialog on main thread
        GLib.idle_add(show_dialog)

    def _create_ssh_error_dialog(
        self, parent_window, ssh_target: str, error_message: str, error_type: str
    ):
        """Create a generic SSH error dialog."""
        try:
            import gi

            gi.require_version("Adw", "1")
            from gi.repository import Adw

            dialog = Adw.MessageDialog(parent=parent_window)
            dialog.set_heading("SSH Connection Failed")

            # Create detailed error message
            details = [
                f"Failed to connect to: {ssh_target}",
                f"Error: {error_message}",
                "",
            ]

            # Add specific guidance based on error type
            if error_type == "refused":
                details.append("Possible causes:")
                details.append("• SSH service is not running on the target host")
                details.append("• Port is blocked by firewall")
                details.append("• Wrong port number specified")
            elif error_type == "permission":
                details.append("Possible causes:")
                details.append("• Invalid username or password")
                details.append("• SSH key authentication failed")
                details.append("• User account is disabled")
            elif error_type == "dns":
                details.append("Possible causes:")
                details.append("• Hostname does not exist")
                details.append("• DNS resolution failed")
                details.append("• Check hostname spelling")
            elif error_type == "network":
                details.append("Possible causes:")
                details.append("• Network connectivity issues")
                details.append("• Host is unreachable")
                details.append("• Check network configuration")
            else:
                details.append("Please check your connection settings and try again.")

            dialog.set_body("\n".join(details))

            # Add response buttons
            dialog.add_response("close", "Close")
            dialog.add_response("retry", "Retry Connection")
            dialog.set_default_response("close")
            dialog.set_close_response("close")

            return dialog

        except Exception as e:
            self.logger.error(f"Failed to create SSH error dialog: {e}")
            return None

    def _terminate_ssh_process(self, session_name: str) -> None:
        """
        Terminate SSH process by session name.

        Args:
            session_name: Name of the SSH session to terminate
        """
        try:
            processes = self.process_tracker.get_all_processes()
            for pid, process_info in processes.items():
                if (
                    process_info.get("type") == "ssh"
                    and process_info.get("name") == session_name
                ):
                    try:
                        if not is_windows():
                            os.kill(pid, signal.SIGTERM)
                            self.logger.info(
                                f"Terminated SSH process {pid} for {session_name}"
                            )
                        else:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(pid)],
                                check=False,
                                capture_output=True,
                            )
                            self.logger.info(
                                f"Terminated SSH process {pid} for {session_name}"
                            )

                        self.process_tracker.unregister_process(pid)
                        break
                    except (OSError, ProcessLookupError):
                        # Process already terminated
                        self.process_tracker.unregister_process(pid)
                        break
        except Exception as e:
            self.logger.error(
                f"Failed to terminate SSH process for {session_name}: {e}"
            )

    def _get_ssh_error_guidance(self, error_message: str) -> str:
        """
        Get user-friendly guidance based on SSH error message.

        Args:
            error_message: SSH error message

        Returns:
            User-friendly guidance string
        """
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
        elif "no such file or directory" in error_lower:
            return "SSH command not found or SSH key file missing"
        else:
            return "Check your SSH configuration and network connectivity"

    def _resolve_and_validate_working_directory(
        self, working_directory: Optional[str]
    ) -> str:
        """
        Resolve and validate working directory with comprehensive error handling.

        Args:
            working_directory: Working directory path to resolve

        Returns:
            Valid working directory path (falls back to home if invalid)
        """
        if not working_directory:
            return str(self.platform_info.home_dir)

        try:
            import os
            from pathlib import Path

            # Expand user home directory and environment variables
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))

            # Convert to absolute path
            resolved_path = os.path.abspath(expanded_path)

            # Create Path object for validation
            path_obj = Path(resolved_path)

            # Comprehensive validation
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

            self.logger.debug(
                f"Working directory validated successfully: {resolved_path}"
            )
            return resolved_path

        except Exception as e:
            self.logger.error(
                f"Error validating working directory '{working_directory}': {e}"
            )
            return str(self.platform_info.home_dir)

    def cleanup_process(self, pid: int) -> None:
        """
        Clean up tracking for a terminated process.

        Args:
            pid: Process ID to clean up
        """
        try:
            success = self.process_tracker.unregister_process(pid)
            if success:
                self._stats["processes_terminated"] += 1
                self.logger.debug(f"Process cleanup completed for PID {pid}")
        except Exception as e:
            self.logger.error(f"Process cleanup failed for PID {pid}: {e}")

    def get_active_processes(self) -> Dict[int, Dict[str, Any]]:
        """
        Get dictionary of currently active processes.

        Returns:
            Dictionary mapping PIDs to process information
        """
        return self.process_tracker.get_all_processes()

    def terminate_all_processes(self) -> None:
        """Terminate all tracked processes and clean up."""
        try:
            self.logger.info("Terminating all spawned processes")

            # Cancel all SSH timeouts first
            self.process_tracker.cleanup_ssh_timeouts()

            self.process_tracker.terminate_all()
            self.logger.info("All processes terminated")
        except Exception as e:
            self.logger.error(f"Process termination failed: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get spawner statistics.

        Returns:
            Dictionary with spawner statistics
        """
        try:
            stats = self._stats.copy()
            stats.update({
                "active_processes": len(self.get_active_processes()),
                "platform": self.platform_info.platform_type.value,
                "ssh_available": has_command("ssh"),
                "sshpass_available": has_command("sshpass"),
            })
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {"error": str(e)}

    def test_ssh_connection(self, session: "SessionItem") -> Tuple[bool, str]:
        """
        Test SSH connection without spawning a terminal, returning success and a message.

        Args:
            session: SessionItem to test

        Returns:
            Tuple of (success_boolean, message_string)
        """
        try:
            self.logger.debug(f"Testing SSH connection for session: {session.name}")

            # Validate session first
            self._validate_ssh_session(session)

            # Build a non-interactive test command
            ssh_options = {
                "BatchMode": "yes",
                "ConnectTimeout": "5",
                "StrictHostKeyChecking": "no",  # Avoid interactive prompts for testing
                "PasswordAuthentication": "no",  # Prefer key auth for non-interactive test
            }

            # Build the base command
            cmd = self.command_builder.build_remote_command(
                "ssh",
                hostname=session.host,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                port=session.port if session.port != 22 else None,
                options=ssh_options,
            )

            # Add a simple command to execute and exit
            cmd.append("exit")

            # Handle password authentication with sshpass if available
            if session.uses_password_auth() and session.auth_value:
                if has_command("sshpass"):
                    # Prepend sshpass command
                    cmd = ["sshpass", "-p", session.auth_value] + cmd
                    self.logger.debug("Using sshpass for connection test")
                else:
                    msg = "sshpass is not installed. Cannot test password-based connections automatically."
                    self.logger.warning(msg)
                    return False, msg

            self.logger.debug(f"SSH test command: {' '.join(cmd)}")

            # Execute the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,  # A safety timeout for the whole process
            )

            if result.returncode == 0:
                self.logger.info(f"SSH connection test successful for {session.name}")
                return True, "Connection successful!"
            else:
                error_message = result.stderr.strip()
                self.logger.warning(
                    f"SSH connection test failed for {session.name}: {error_message}"
                )
                return False, error_message

        except Exception as e:
            self.logger.error(
                f"SSH connection test failed with an exception for {session.name}: {e}"
            )
            return False, str(e)


# Global spawner instance
_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    """
    Get the global ProcessSpawner instance (thread-safe singleton).

    Returns:
        ProcessSpawner singleton instance
    """
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
                _spawner_instance.terminate_all_processes()
                _spawner_instance = None
