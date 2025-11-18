# ashyterm/terminal/spawner.py

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

from ..settings.manager import get_settings_manager
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
from ..utils.translation_utils import _

OSC7_HOST_DETECTION_SNIPPET = (
    'if [ -z "$ASHYTERM_OSC7_HOST" ]; then '
    "if command -v hostname >/dev/null 2>&1; then "
    'ASHYTERM_OSC7_HOST="$(hostname)"; '
    'elif [ -n "$HOSTNAME" ]; then '
    'ASHYTERM_OSC7_HOST="$HOSTNAME"; '
    "elif command -v uname >/dev/null 2>&1; then "
    'ASHYTERM_OSC7_HOST="$(uname -n)"; '
    "else "
    'ASHYTERM_OSC7_HOST="unknown"; '
    "fi; "
    "fi;"
)


class ProcessTracker:
    """Track spawned processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a spawned process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}

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
                return True
            return False

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


class ProcessSpawner:
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner")
        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.process_tracker = ProcessTracker()
        self.settings_manager = get_settings_manager()
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
            shell = Vte.get_user_shell()
            shell_basename = os.path.basename(shell)
            temp_dir_path_for_zsh = None

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

            # OSC7 integration for CWD tracking
            osc7_command = (
                f"{OSC7_HOST_DETECTION_SNIPPET} "
                'printf "\\033]7;file://%s%s\\007" "$ASHYTERM_OSC7_HOST" "$PWD"'
            )

            if shell_basename == "zsh":
                try:
                    # Create a temporary directory that we will manage for cleanup
                    temp_dir_path_for_zsh = tempfile.mkdtemp(prefix="ashyterm_zsh_")
                    zshrc_path = os.path.join(temp_dir_path_for_zsh, ".zshrc")

                    # This zshrc adds our hook, then sources the user's real .zshrc
                    zshrc_content = (
                        f"_ashyterm_update_cwd() {{ {osc7_command}; }}\n"
                        'if [[ -z "$precmd_functions" ]]; then\n'
                        "  typeset -a precmd_functions\n"
                        "fi\n"
                        "precmd_functions+=(_ashyterm_update_cwd)\n"
                        'if [ -f "$HOME/.zshrc" ]; then . "$HOME/.zshrc"; fi\n'
                    )

                    with open(zshrc_path, "w", encoding="utf-8") as f:
                        f.write(zshrc_content)

                    env["ZDOTDIR"] = temp_dir_path_for_zsh
                    self.logger.info(
                        f"Using temporary ZDOTDIR for zsh OSC7 integration: {temp_dir_path_for_zsh}"
                    )

                except Exception as e:
                    self.logger.error(f"Failed to set up zsh OSC7 integration: {e}")
                    if temp_dir_path_for_zsh:
                        shutil.rmtree(temp_dir_path_for_zsh, ignore_errors=True)
                    temp_dir_path_for_zsh = None  # Ensure it's not registered
            else:  # Bash and other compatible shells
                existing_prompt_command = env.get("PROMPT_COMMAND", "")
                if existing_prompt_command:
                    # Prepend our command to ensure it runs, then the user's
                    env["PROMPT_COMMAND"] = f"{osc7_command};{existing_prompt_command}"
                else:
                    env["PROMPT_COMMAND"] = osc7_command
                self.logger.info(
                    "Injected PROMPT_COMMAND for bash/compatible shell OSC7 integration."
                )

            if self.settings_manager.get("use_login_shell", False):
                cmd = [shell, "-l"]
                self.logger.info(f"Spawning '{shell} -l' as a login shell.")
            else:
                cmd = [shell]

            env_list = [f"{k}={v}" for k, v in env.items()]

            # Wrap user_data to include the temp dir path for zsh cleanup
            final_user_data = {
                "original_user_data": user_data,
                "temp_dir_path": temp_dir_path_for_zsh,
            }

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
                (final_user_data,),
            )
            self.logger.info("Local terminal spawn initiated successfully")
            log_terminal_event(
                "spawn_initiated", str(user_data), f"shell command: {' '.join(cmd)}"
            )

    def _spawn_remote_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        command_type: str,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
        sftp_local_dir: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
    ) -> None:
        """Generic method to spawn a remote (SSH/SFTP) session."""
        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalCreationError(
                    f"Session is not configured for {command_type.upper()}",
                    command_type,
                )
            try:
                self._validate_ssh_session(session)
                remote_cmd = self._build_remote_command_secure(
                    command_type,
                    session,
                    initial_command,
                    sftp_remote_path,
                )
                if not remote_cmd:
                    raise TerminalCreationError(
                        f"Failed to build {command_type.upper()} command", command_type
                    )

                working_dir = str(self.platform_info.home_dir)
                if command_type == "sftp" and sftp_local_dir:
                    try:
                        local_path = Path(sftp_local_dir).expanduser()
                        if local_path.exists() and local_path.is_dir():
                            working_dir = str(local_path)
                        else:
                            self.logger.warning(
                                f"SFTP local directory '{sftp_local_dir}' is invalid; falling back to home directory."
                            )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to use SFTP local directory '{sftp_local_dir}': {e}"
                        )
                env = self.environment_manager.get_terminal_environment()
                env_list = [f"{k}={v}" for k, v in env.items()]

                final_user_data = {
                    "original_user_data": user_data,
                    "temp_dir_path": None,
                }

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
                    (final_user_data,),
                )
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
                raise TerminalCreationError(str(e), command_type) from e

    def spawn_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
    ) -> None:
        """Spawns an SSH session in the given terminal."""
        self._spawn_remote_session(
            terminal,
            session,
            "ssh",
            callback,
            user_data,
            initial_command=initial_command,
        )

    def spawn_sftp_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        local_directory: Optional[str] = None,
        remote_path: Optional[str] = None,
    ) -> None:
        """Spawns an SFTP session in the given terminal."""
        self._spawn_remote_session(
            terminal,
            session,
            "sftp",
            callback,
            user_data,
            sftp_local_dir=local_directory,
            sftp_remote_path=remote_path,
        )

    def execute_remote_command_sync(
        self, session: "SessionItem", command: List[str], timeout: int = 15
    ) -> Tuple[bool, str]:
        """
        Executes a non-interactive command on a remote session synchronously.
        Returns a tuple of (success, output).
        """
        if not session.is_ssh():
            return False, _("Not an SSH session.")

        try:
            self._validate_ssh_session(session)
            full_cmd = self._build_non_interactive_ssh_command(session, command)
            if not full_cmd:
                raise TerminalCreationError(
                    "Failed to build non-interactive SSH command", "ssh"
                )

            self.logger.debug(f"Executing remote command: {' '.join(full_cmd)}")

            result = subprocess.run(
                full_cmd, capture_output=True, text=True, timeout=timeout
            )

            if result.returncode == 0:
                return True, result.stdout
            else:
                error_output = (
                    result.stdout.strip() + "\n" + result.stderr.strip()
                ).strip()
                self.logger.warning(
                    f"Remote command failed for {session.name} with code {result.returncode}: {error_output}"
                )
                return False, error_output
        except subprocess.TimeoutExpired:
            self.logger.error(f"Remote command timed out for session {session.name}")
            return False, _("Command timed out.")
        except Exception as e:
            self.logger.error(
                f"Failed to execute remote command for {session.name}: {e}"
            )
            log_error_with_context(
                e, f"Remote command execution for {session.name}", "ashyterm.spawner"
            )
            return False, str(e)

    def test_ssh_connection(self, session: "SessionItem") -> Tuple[bool, str]:
        """
        Tests an SSH connection without spawning a full terminal.
        Returns a tuple of (success, message).
        """
        if not session.is_ssh():
            return False, "Not an SSH session."

        try:
            self._validate_ssh_session(session)

            ssh_options = {
                "BatchMode": "yes",
                "ConnectTimeout": "10",
                "StrictHostKeyChecking": "no",
                "PasswordAuthentication": "no" if session.uses_key_auth() else "yes",
            }
            if getattr(session, "x11_forwarding", False):
                ssh_options["ForwardX11"] = "yes"
                ssh_options["ForwardX11Trusted"] = "yes"

            cmd = self.command_builder.build_remote_command(
                "ssh",
                hostname=session.host,
                port=session.port if session.port != 22 else None,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=ssh_options,
            )
            if getattr(session, "x11_forwarding", False) and "-Y" not in cmd:
                cmd.insert(1, "-Y")
            cmd.append("exit")

            if session.uses_password_auth() and session.auth_value:
                if has_command("sshpass"):
                    cmd = ["sshpass", "-p", session.auth_value] + cmd
                else:
                    return (
                        False,
                        "sshpass is not installed, cannot test password authentication.",
                    )

            self.logger.info(f"Testing SSH connection with command: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if result.returncode == 0:
                self.logger.info(f"SSH connection test successful for {session.name}")
                return True, "Connection successful."
            else:
                error_message = result.stderr.strip()
                self.logger.warning(
                    f"SSH connection test failed for {session.name}: {error_message}"
                )
                return False, error_message

        except Exception as e:
            self.logger.error(
                f"Exception during SSH connection test for {session.name}: {e}"
            )
            return False, str(e)

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}") from e
        if session.uses_key_auth():
            # CORRECTED LOGIC: Only validate the key file if a path is provided.
            if session.auth_value:
                try:
                    validate_ssh_key_file(session.auth_value)
                except Exception as e:
                    raise SSHKeyError(session.auth_value, str(e)) from e

    def _build_remote_command_secure(
        self,
        command_type: str,
        session: "SessionItem",
        initial_command: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
    ) -> Optional[List[str]]:
        """Builds an SSH/SFTP command for an INTERACTIVE session."""
        if not has_command(command_type):
            raise SSHConnectionError(
                session.host, f"{command_type.upper()} command not found on system"
            )

        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        ssh_options = {
            "ConnectTimeout": "30",
            "ServerAliveInterval": "30",
            "ServerAliveCountMax": "3",
            "StrictHostKeyChecking": "accept-new",
            "UpdateHostKeys": "yes",
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
        }
        if persist_duration > 0:
            ssh_options["ControlPersist"] = str(persist_duration)
        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)
        if command_type == "ssh" and getattr(session, "port_forwardings", None):
            # Port forwarding sessions should tear down immediately when the terminal exits.
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)
            ssh_options["ExitOnForwardFailure"] = "yes"
        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            ssh_options["ForwardX11"] = "yes"
            ssh_options["ForwardX11Trusted"] = "yes"

        cmd = self.command_builder.build_remote_command(
            command_type,
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
            remote_path=sftp_remote_path if command_type == "sftp" else None,
        )

        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            if "-Y" not in cmd:
                insertion_index = 1 if len(cmd) > 1 else len(cmd)
                cmd.insert(insertion_index, "-Y")

        if command_type == "ssh" and getattr(session, "port_forwardings", None):
            for tunnel in session.port_forwardings:
                try:
                    local_host = tunnel.get("local_host", "localhost") or "localhost"
                    local_port = int(tunnel.get("local_port", 0))
                    remote_host = tunnel.get("remote_host") or session.host
                    remote_port = int(tunnel.get("remote_port", 0))
                except (TypeError, ValueError):
                    continue

                if not remote_host or not (1 <= local_port <= 65535) or not (
                    1 <= remote_port <= 65535
                ):
                    continue

                forward_spec = f"{local_host}:{local_port}:{remote_host}:{remote_port}"
                insertion_index = max(len(cmd) - 1, 1)
                cmd[insertion_index:insertion_index] = ["-L", forward_spec]

        if command_type == "ssh":
            # This command sets up OSC7 tracking for bash/zsh and then executes the user's default shell.
            # It's a pragmatic approach that works for the most common shells.
            osc7_setup = (
                f"{OSC7_HOST_DETECTION_SNIPPET} "
                'export PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\007" "$ASHYTERM_OSC7_HOST" "$PWD"\''
            )
            shell_exec = 'exec "$SHELL" -l'

            remote_parts = []
            if initial_command:
                remote_parts.append(initial_command)
            remote_parts.append(osc7_setup)
            remote_parts.append(shell_exec)

            full_remote_command = "; ".join(remote_parts)

            # Force TTY allocation for interactive sessions
            if "-t" not in cmd:
                cmd.insert(1, "-t")
            cmd.append(full_remote_command)

        # For SFTP, no extra remote command is needed for an interactive session.

        if session.uses_password_auth() and session.auth_value:
            if has_command("sshpass"):
                cmd = ["sshpass", "-p", session.auth_value] + cmd
            else:
                self.logger.warning("sshpass not available for password authentication")
        return cmd

    def _build_non_interactive_ssh_command(
        self, session: "SessionItem", command: List[str]
    ) -> Optional[List[str]]:
        """Builds an SSH command for a NON-INTERACTIVE session, without TTY allocation."""
        if not has_command("ssh"):
            raise SSHConnectionError(session.host, "SSH command not found on system")

        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        ssh_options = {
            "ConnectTimeout": "15",
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
            "BatchMode": "yes",  # Ensure it doesn't prompt for passwords
        }
        if persist_duration > 0:
            ssh_options["ControlPersist"] = str(persist_duration)
        if getattr(session, "x11_forwarding", False):
            ssh_options["ForwardX11"] = "yes"
            ssh_options["ForwardX11Trusted"] = "yes"
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)

        cmd = self.command_builder.build_remote_command(
            "ssh",
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
        )

        if getattr(session, "x11_forwarding", False) and "-Y" not in cmd:
            insertion_index = 1 if len(cmd) > 1 else len(cmd)
            cmd.insert(insertion_index, "-Y")

        remote_command_str = " ".join(shlex.quote(part) for part in command)
        cmd.append(remote_command_str)

        if session.uses_password_auth() and session.auth_value:
            if has_command("sshpass"):
                cmd = ["sshpass", "-p", session.auth_value] + cmd
            else:
                self.logger.warning("sshpass not available for password authentication")
        return cmd
    def _default_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            original_user_data = final_user_data.get("original_user_data")
            temp_dir_path = final_user_data.get("temp_dir_path")

            terminal_name = (
                str(original_user_data[0])
                if isinstance(original_user_data, tuple) and original_user_data
                else "Terminal"
            )
            if error:
                self.logger.error(
                    f"Process spawn failed for {terminal_name}: {error.message}"
                )
                log_terminal_event(
                    "spawn_failed", terminal_name, f"error: {error.message}"
                )
                # Use Unix-style newlines (\n) for terminal output
                error_msg = f"\nFailed to start {terminal_name}:\nError: {error.message}\nPlease check your system configuration.\n\n"
                if terminal.get_realized():
                    terminal.feed(error_msg.encode("utf-8"))
            else:
                self.logger.info(
                    f"Process spawned successfully for {terminal_name} with PID {pid}"
                )
                log_terminal_event("spawned", terminal_name, f"PID {pid}")
                if pid > 0:
                    process_info = {
                        "name": terminal_name,
                        "type": "local",
                        "terminal": terminal,
                    }
                    if temp_dir_path:
                        process_info["temp_dir_path"] = temp_dir_path
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
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            original_user_data = final_user_data.get("original_user_data")
            actual_data = (
                original_user_data[0]
                if isinstance(original_user_data, tuple) and original_user_data
                else original_user_data
            )
            session_name = getattr(actual_data, "name", "SSH Session")
            if error:
                self.logger.error(
                    f"SSH spawn failed for {session_name}: {error.message}"
                )
                log_terminal_event(
                    "ssh_spawn_failed", session_name, f"error: {error.message}"
                )
                error_guidance = self._get_ssh_error_guidance(error.message)
                # Use Unix-style newlines (\n) for terminal output
                error_msg = f"\nSSH Connection Failed:\nSession: {session_name}\nHost: {getattr(actual_data, 'get_connection_string', lambda: 'unknown')()}\nError: {error.message}\n"
                if error_guidance:
                    error_msg += f"Suggestion: {error_guidance}\n"
                error_msg += "\n"
                if terminal.get_realized():
                    terminal.feed(error_msg.encode("utf-8"))
            else:
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


_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    global _spawner_instance
    if _spawner_instance is None:
        with _spawner_lock:
            if _spawner_instance is None:
                _spawner_instance = ProcessSpawner()
    return _spawner_instance


def cleanup_spawner() -> None:
    global _spawner_instance
    if _spawner_instance is not None:
        with _spawner_lock:
            if _spawner_instance is not None:
                _spawner_instance.process_tracker.terminate_all()
                _spawner_instance = None
