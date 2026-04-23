# ashyterm/terminal/ssh_spawn_mixin.py

import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem
    from .highlighter import HighlightedTerminalProxy

from ..utils.exceptions import SSHConnectionError, SSHKeyError, TerminalCreationError
from ..utils.logger import log_error_with_context, log_terminal_event
from ..utils.osc7 import OSC7_HOST_DETECTION_SNIPPET
from ..utils.platform import has_command
from ..utils.security import (
    validate_ssh_hostname,
    validate_ssh_key_file,
)
from ..utils.translation_utils import _

# Logger name constant
LOGGER_NAME_SPAWNER = "ashyterm.spawner"


class SSHSpawnMixin:
    """Mixin providing SSH/SFTP session spawning capabilities."""

    # Attributes provided by ProcessSpawner:
    # - logger
    # - settings_manager
    # - platform_info
    # - command_builder
    # - environment_manager
    # - process_tracker
    # - _spawn_lock
    # - _last_sshpass_file

    def _get_ssh_control_path(self, session: "SessionItem") -> str:
        """Generate SSH ControlMaster socket path."""
        user = session.user or os.getlogin()
        port = session.port or 22
        self.platform_info.cache_dir.mkdir(parents=True, exist_ok=True)
        return str(
            self.platform_info.cache_dir / f"ssh_control_{session.host}_{port}_{user}"
        )

    def _resolve_sftp_working_dir(
        self, command_type: str, sftp_local_dir: Optional[str]
    ) -> str:
        """Resolve the working directory for SFTP sessions."""
        working_dir = str(self.platform_info.home_dir)
        if command_type != "sftp" or not sftp_local_dir:
            return working_dir
        try:
            local_path = Path(sftp_local_dir).expanduser()  # noqa: F821
            if local_path.exists() and local_path.is_dir():
                return str(local_path)
            self.logger.warning(
                f"SFTP local directory '{sftp_local_dir}' is invalid; "
                "falling back to home directory."
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to use SFTP local directory '{sftp_local_dir}': {e}"
            )
        return working_dir

    def _prepare_spawn_environment(
        self, sshpass_env: Optional[dict]
    ) -> list:
        """Prepare environment variables for terminal spawn."""
        env = self.environment_manager.get_terminal_environment()
        if sshpass_env:
            env.update(sshpass_env)
        return [f"{k}={v}" for k, v in env.items()]

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
                result = self._build_remote_command_secure(
                    command_type,
                    session,
                    initial_command,
                    sftp_remote_path,
                )
                if not result:
                    raise TerminalCreationError(
                        f"Failed to build {command_type.upper()} command", command_type
                    )

                remote_cmd, sshpass_env = result
                working_dir = self._resolve_sftp_working_dir(
                    command_type, sftp_local_dir
                )
                env_list = self._prepare_spawn_environment(sshpass_env)

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
                    "launch_initiated",
                    session.name,
                    f"{command_type.upper()} to {session.get_connection_string()}",
                )
            except Exception as e:
                self._cleanup_pending_sshpass_file()
                self.logger.error(
                    f"{command_type.upper()} session launch failed for {session.name}: {e}"
                )
                log_error_with_context(
                    e,
                    f"{command_type.upper()} launch for {session.name}",
                    LOGGER_NAME_SPAWNER,
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

    def spawn_highlighted_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
        terminal_id: Optional[int] = None,
    ) -> Optional["HighlightedTerminalProxy"]:
        """Spawn an SSH session with output highlighting support."""
        from .highlighter import HighlightedTerminalProxy

        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalCreationError("Session is not configured for SSH", "ssh")

            try:
                self._validate_ssh_session(session)
                result = self._build_remote_command_secure(
                    "ssh",
                    session,
                    initial_command,
                    None,
                )
                if not result:
                    raise TerminalCreationError("Failed to build SSH command", "ssh")

                remote_cmd, sshpass_env = result

                working_dir = str(self.platform_info.home_dir)
                env = self.environment_manager.get_terminal_environment()

                if sshpass_env:
                    env.update(sshpass_env)

                proxy = HighlightedTerminalProxy(
                    terminal,
                    "ssh",
                    proxy_id=terminal_id,
                )

                spawn_result = self._run_highlighted_spawn(
                    proxy=proxy,
                    terminal=terminal,
                    cmd=remote_cmd,
                    working_dir=working_dir,
                    env=env,
                    process_name=session.name,
                    spawn_type="ssh",
                    callback=callback,
                    user_data=user_data,
                    temp_dir_path=None,
                    session=session,
                )

                if spawn_result:
                    log_terminal_event(
                        "launch_initiated",
                        session.name,
                        f"highlighted SSH to {session.get_connection_string()}",
                    )

                return spawn_result

            except Exception as e:
                self._cleanup_pending_sshpass_file()
                self.logger.error(f"Highlighted SSH spawn failed: {e}")
                if "proxy" in locals():
                    proxy.stop()
                self._invoke_spawn_error_callback(
                    callback, terminal, e, user_data, None
                )
                return None

    def execute_remote_command_sync(
        self, session: "SessionItem", command: List[str], timeout: int = 10
    ) -> Tuple[bool, str]:
        """Execute a non-interactive command on a remote session synchronously.
        Uses aggressive timeout to prevent UI freezing on lost connections.
        """
        if not session.is_ssh():
            return False, _("Not an SSH session.")

        try:
            self._validate_ssh_session(session)
            connect_timeout = min(timeout - 2, 8) if timeout > 4 else timeout
            result = self._build_non_interactive_ssh_command(
                session, command, connect_timeout=connect_timeout
            )
            if not result:
                raise TerminalCreationError(
                    "Failed to build non-interactive SSH command", "ssh"
                )

            full_cmd, sshpass_env = result

            self.logger.debug(
                f"Executing remote command (timeout={timeout}s): {' '.join(full_cmd)}"
            )

            pass_file = None
            run_env = None
            if sshpass_env and "_ASHYTERM_SSHPASS_FILE" in sshpass_env:
                pass_file = sshpass_env["_ASHYTERM_SSHPASS_FILE"]

            try:
                proc_result = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=run_env,
                )
            finally:
                if pass_file:
                    try:
                        Path(pass_file).unlink(missing_ok=True)
                    except OSError as unlink_exc:
                        self.logger.debug(
                            f"Could not remove sshpass temp file {pass_file}: {unlink_exc}"
                        )

            if proc_result.returncode == 0:
                return True, proc_result.stdout
            else:
                error_output = (
                    proc_result.stdout.strip() + "\n" + proc_result.stderr.strip()
                ).strip()
                if any(
                    err in error_output.lower()
                    for err in [
                        "connection",
                        "timed out",
                        "unreachable",
                        "refused",
                        "reset",
                    ]
                ):
                    self.logger.warning(
                        f"Connection issue for {session.name}: {error_output}"
                    )
                    return False, _("Connection lost or unreachable.")

                self.logger.warning(
                    f"Remote command failed for {session.name} with code {proc_result.returncode}: {error_output}"
                )
                return False, error_output
        except subprocess.TimeoutExpired:
            self.logger.error(
                f"Remote command timed out after {timeout}s for session {session.name}"
            )
            return False, _("Command timed out. Connection may be lost.")
        except Exception as e:
            self.logger.error(
                f"Failed to execute remote command for {session.name}: {e}"
            )
            log_error_with_context(
                e, f"Remote command execution for {session.name}", LOGGER_NAME_SPAWNER
            )
            return False, str(e)

    def test_ssh_connection(self, session: "SessionItem") -> Tuple[bool, str]:
        """Test SSH connection without spawning a full terminal. Returns (success, message)."""
        if not session.is_ssh():
            return False, "Not an SSH session."

        try:
            self._validate_ssh_session(session)
            cmd, run_env = self._build_ssh_test_command(session)
            if cmd is None:
                return False, str(run_env)

            self.logger.info(f"Testing SSH connection with command: {' '.join(cmd)}")
            return self._execute_ssh_test(
                cmd, run_env if isinstance(run_env, dict) else None, session.name
            )

        except Exception as e:
            self.logger.error(
                f"Exception during SSH connection test for {session.name}: {e}"
            )
            return False, str(e)

    def _build_ssh_test_command(
        self, session: "SessionItem"
    ) -> Tuple[Optional[List[str]], Optional[dict] | str]:
        """Build SSH test command and environment. Returns (command, env) or (None, error).

        Reads password via the session.auth_value property (which pulls from
        the keyring) instead of the raw _auth_value attribute, which is
        intentionally blank for password-auth sessions.
        """
        raw_password = (
            session.auth_value if session.uses_password_auth() else ""
        ) or ""
        use_password = session.uses_password_auth() and bool(raw_password)

        ssh_options = self._build_ssh_test_options(session, use_password)
        cmd = self._build_ssh_base_cmd(session, ssh_options)

        if not use_password:
            return cmd, None

        return self._wrap_sshpass(cmd, raw_password)

    _ALLOWED_STRICT_HOSTKEY = ("ask", "accept-new", "yes", "no")

    def _get_strict_host_key_checking(self) -> str:
        """Return the configured StrictHostKeyChecking policy, clamped to valid values."""
        value = self.settings_manager.get(
            "ssh_strict_host_key_checking", "accept-new"
        )
        if value in self._ALLOWED_STRICT_HOSTKEY:
            return value
        self.logger.warning(
            f"Invalid ssh_strict_host_key_checking={value!r}, falling back to accept-new"
        )
        return "accept-new"

    def _build_ssh_test_options(self, session, use_password: bool) -> dict:
        """Build SSH option dict for test connections."""
        opts = {
            "BatchMode": "no" if use_password else "yes",
            "ConnectTimeout": "10",
            "StrictHostKeyChecking": self._get_strict_host_key_checking(),
            "PasswordAuthentication": "yes" if use_password else "no",
        }
        if getattr(session, "x11_forwarding", False):
            opts["ForwardX11"] = "yes"
            opts["ForwardX11Trusted"] = "yes"
        return opts

    def _build_ssh_base_cmd(self, session, ssh_options: dict) -> list:
        """Build base SSH command list for testing."""
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
        return cmd

    def _wrap_sshpass(self, cmd: list, password: str):
        """Wrap command with sshpass for password authentication.

        Always uses -f <file> (mode 0600); never -e env var.
        The caller must delete the returned password file after use
        (see _execute_ssh_test's try/finally). Returns (None, error_str)
        if sshpass is unavailable or the temp file cannot be created.
        """
        if not has_command("sshpass"):
            return (
                None,
                "sshpass is not installed, cannot test password authentication.",
            )
        pass_file = self._create_sshpass_file(password)
        if not pass_file:
            return (
                None,
                "Could not create secure temporary file for SSH password.",
            )
        run_env = os.environ.copy()
        run_env["LC_ALL"] = "C"
        cmd = ["sshpass", "-f", pass_file] + cmd
        # Piggy-back path on env dict so the caller can unlink it.
        run_env["_ASHYTERM_SSHPASS_FILE"] = pass_file
        return cmd, run_env

    def _execute_ssh_test(
        self, cmd: List[str], run_env: Optional[dict], session_name: str
    ) -> Tuple[bool, str]:
        """Execute SSH test command and return result.

        Removes the ephemeral sshpass password file (if any) before returning,
        regardless of success, timeout, or exception.
        """
        pass_file = None
        env_to_pass = run_env
        if run_env is not None and "_ASHYTERM_SSHPASS_FILE" in run_env:
            # Don't leak the marker into the child's environment.
            env_to_pass = {
                k: v for k, v in run_env.items() if k != "_ASHYTERM_SSHPASS_FILE"
            }
            pass_file = run_env["_ASHYTERM_SSHPASS_FILE"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, env=env_to_pass
            )
        finally:
            if pass_file:
                try:
                    Path(pass_file).unlink(missing_ok=True)
                except OSError as exc:
                    self.logger.debug(
                        f"Could not remove sshpass temp file {pass_file}: {exc}"
                    )

        if result.returncode == 0:
            self.logger.info(f"SSH connection test successful for {session_name}")
            return True, "Connection successful."

        error_message = result.stderr.strip()
        self.logger.warning(
            f"SSH connection test failed for {session_name}: {error_message}"
        )
        return False, error_message

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        """Validate SSH session configuration."""
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}") from e
        if session.uses_key_auth():
            if session.auth_value:
                try:
                    validate_ssh_key_file(session.auth_value)
                except Exception as e:
                    raise SSHKeyError(session.auth_value, str(e)) from e

    def _get_base_ssh_options(self, session: "SessionItem") -> Dict[str, str]:
        """Build base SSH options dictionary."""
        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        connect_timeout = self.settings_manager.get("ssh_connect_timeout", 30)

        ssh_options = {
            "ConnectTimeout": str(connect_timeout),
            "ServerAliveInterval": "30",
            "ServerAliveCountMax": "3",
            "StrictHostKeyChecking": self._get_strict_host_key_checking(),
            "UpdateHostKeys": "yes",
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
        }

        if persist_duration > 0:
            ssh_options["ControlPersist"] = str(persist_duration)

        return ssh_options

    def _apply_x11_and_tunnel_options(
        self,
        ssh_options: Dict[str, str],
        session: "SessionItem",
        command_type: str,
    ) -> None:
        """Apply X11 forwarding and port forwarding options in-place."""
        has_x11 = command_type == "ssh" and getattr(session, "x11_forwarding", False)
        has_tunnels = command_type == "ssh" and getattr(
            session, "port_forwardings", None
        )

        if has_x11 or has_tunnels:
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)

        if has_tunnels:
            ssh_options["ExitOnForwardFailure"] = "yes"

        if has_x11:
            ssh_options["ForwardX11"] = "yes"
            ssh_options["ForwardX11Trusted"] = "yes"

    def _add_x11_flag_to_command(
        self, cmd: List[str], session: "SessionItem", command_type: str
    ) -> None:
        """Add -Y flag for X11 forwarding if needed."""
        if command_type != "ssh":
            return
        if not getattr(session, "x11_forwarding", False):
            return
        if "-Y" not in cmd:
            insertion_index = 1 if len(cmd) > 1 else len(cmd)
            cmd.insert(insertion_index, "-Y")

    def _add_port_forwarding_args(
        self, cmd: List[str], session: "SessionItem", command_type: str
    ) -> None:
        """Add port forwarding arguments to command."""
        if command_type != "ssh":
            return
        if not getattr(session, "port_forwardings", None):
            return

        for tunnel in session.port_forwardings:
            try:
                local_host = tunnel.get("local_host", "localhost") or "localhost"
                local_port = int(tunnel.get("local_port", 0))
                remote_host = tunnel.get("remote_host") or session.host
                remote_port = int(tunnel.get("remote_port", 0))
            except (TypeError, ValueError):
                continue

            if (
                not remote_host
                or not (1 <= local_port <= 65535)
                or not (1 <= remote_port <= 65535)
            ):
                continue

            forward_spec = f"{local_host}:{local_port}:{remote_host}:{remote_port}"
            insertion_index = max(len(cmd) - 1, 1)
            cmd[insertion_index:insertion_index] = ["-L", forward_spec]

    def _add_remote_shell_command(
        self, cmd: List[str], initial_command: Optional[str]
    ) -> None:
        """Add remote shell command suffix for SSH sessions."""
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

        if "-t" not in cmd:
            cmd.insert(1, "-t")
        cmd.append(full_remote_command)

    def _wrap_with_sshpass(
        self, cmd: List[str], session: "SessionItem"
    ) -> Tuple[List[str], Optional[Dict[str, str]]]:
        """Wrap command with sshpass for password auth. Uses -f file (mode 0600).

        Never falls back to SSHPASS env var because env is exposed via
        /proc/PID/environ to any process of the same user.
        """
        if not (session.uses_password_auth() and session.auth_value):
            return (cmd, None)

        if not has_command("sshpass"):
            raise SSHConnectionError(
                session.host,
                "sshpass is required for password authentication but is not installed",
            )

        pass_file = self._create_sshpass_file(session.auth_value)
        if not pass_file:
            raise SSHConnectionError(
                session.host,
                "Could not create secure temporary file for SSH password",
            )

        self._last_sshpass_file = pass_file
        return (["sshpass", "-f", pass_file] + cmd, None)

    def _create_sshpass_file(self, password: str) -> Optional[str]:
        """Create secure temp file containing SSH password (mode 0600)."""
        import tempfile

        try:
            self.platform_info.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(
                prefix="ashyterm_sshpass_",
                dir=str(self.platform_info.cache_dir),
            )
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, password.encode("utf-8"))
            finally:
                os.close(fd)
            return path
        except Exception as e:
            self.logger.error(f"Failed to create sshpass temp file: {e}")
            return None

    def _cleanup_pending_sshpass_file(self) -> None:
        """Unlink and clear the last created sshpass password file, if any.

        Called when a spawn fails before the file gets transferred to the
        ProcessTracker. Otherwise the password would linger in cache_dir.
        """
        path = self._last_sshpass_file
        if not path:
            return
        self._last_sshpass_file = None
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            self.logger.debug(f"Could not remove sshpass temp file {path}: {exc}")

    def _build_remote_command_secure(
        self,
        command_type: str,
        session: "SessionItem",
        initial_command: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
    ) -> Optional[Tuple[List[str], Optional[Dict[str, str]]]]:
        """Build SSH/SFTP command for INTERACTIVE session. Returns (cmd, sshpass_env)."""
        if not has_command(command_type):
            raise SSHConnectionError(
                session.host, f"{command_type.upper()} command not found on system"
            )

        ssh_options = self._get_base_ssh_options(session)
        self._apply_x11_and_tunnel_options(ssh_options, session, command_type)

        cmd = self.command_builder.build_remote_command(
            command_type,
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
            remote_path=sftp_remote_path if command_type == "sftp" else None,
        )

        self._add_x11_flag_to_command(cmd, session, command_type)
        self._add_port_forwarding_args(cmd, session, command_type)

        if command_type == "ssh":
            self._add_remote_shell_command(cmd, initial_command)

        return self._wrap_with_sshpass(cmd, session)

    def _build_non_interactive_ssh_command(
        self, session: "SessionItem", command: List[str], connect_timeout: int = 10
    ) -> Optional[Tuple[List[str], Optional[Dict[str, str]]]]:
        """Build SSH command for NON-INTERACTIVE session. Returns (cmd, sshpass_env)."""
        if not has_command("ssh"):
            raise SSHConnectionError(session.host, "SSH command not found on system")

        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        ssh_options = {
            "ConnectTimeout": str(connect_timeout),
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
            "BatchMode": "yes",
            "ServerAliveInterval": "5",
            "ServerAliveCountMax": "2",
            "StrictHostKeyChecking": self._get_strict_host_key_checking(),
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

        # "sshpass_env" here is repurposed to tell execute_remote_command_sync
        # the path of the password file it must unlink when done. We never
        # expose the password via SSHPASS env var.
        sshpass_env: Optional[Dict[str, str]] = None
        if session.uses_password_auth() and session.auth_value:
            if not has_command("sshpass"):
                raise SSHConnectionError(
                    session.host,
                    "sshpass is required for password authentication but is not installed",
                )
            pass_file = self._create_sshpass_file(session.auth_value)
            if not pass_file:
                raise SSHConnectionError(
                    session.host,
                    "Could not create secure temporary file for SSH password",
                )
            cmd = ["sshpass", "-f", pass_file] + cmd
            sshpass_env = {"_ASHYTERM_SSHPASS_FILE": pass_file}
        return (cmd, sshpass_env)

    def _extract_spawn_callback_data(
        self, user_data: Any, spawn_type: str
    ) -> Tuple[Any, Optional[str], str, Any]:
        """Extract and parse spawn callback data.
        Returns: (final_user_data, temp_dir_path, name, actual_data).
        """
        final_user_data = user_data[0] if isinstance(user_data, tuple) else user_data
        original_user_data = final_user_data.get("original_user_data")
        temp_dir_path = final_user_data.get("temp_dir_path")

        if spawn_type == "ssh":
            actual_data = (
                original_user_data[0]
                if isinstance(original_user_data, tuple) and original_user_data
                else original_user_data
            )
            name = getattr(actual_data, "name", "SSH Session")
        else:
            actual_data = None
            name = (
                str(original_user_data[0])
                if isinstance(original_user_data, tuple) and original_user_data
                else "Terminal"
            )

        return final_user_data, temp_dir_path, name, actual_data

    def _handle_spawn_error(
        self,
        terminal: Vte.Terminal,
        error: GLib.Error,
        name: str,
        spawn_type: str,
        actual_data: Any,
    ) -> None:
        """Handle spawn error — log and show error in terminal."""
        # The VTE spawn rejected the child (no PID was created); if a
        # password file was staged for this spawn it must be removed now.
        self._cleanup_pending_sshpass_file()

        event_type = (
            f"{spawn_type}_launch_failed" if spawn_type == "ssh" else "launch_failed"
        )
        self.logger.error(f"Process launch failed for {name}: {error.message}")
        log_terminal_event(event_type, name, f"error: {error.message}")

        if spawn_type == "ssh" and actual_data:
            error_guidance = self._get_ssh_error_guidance(error.message)
            connection_str = getattr(
                actual_data, "get_connection_string", lambda: "unknown"
            )()
            error_msg = (
                f"\nSSH Connection Failed:\nSession: {name}\n"
                f"Host: {connection_str}\nError: {error.message}\n"
            )
            if error_guidance:
                error_msg += f"Suggestion: {error_guidance}\n"
            error_msg += "\n"
        else:
            error_msg = (
                f"\nFailed to start {name}:\nError: {error.message}\n"
                "Please check your system configuration.\n\n"
            )

        if terminal.get_realized():
            terminal.feed(error_msg.encode("utf-8"))

    def _handle_spawn_success(
        self,
        pid: int,
        name: str,
        spawn_type: str,
        terminal: Vte.Terminal,
        temp_dir_path: Optional[str],
        actual_data: Any,
    ) -> None:
        """Handle successful spawn — register process."""
        self.logger.info(f"Process launched successfully for {name} with PID {pid}")
        log_terminal_event("launched", name, f"PID {pid}")

        if pid > 0:
            process_info = {
                "name": name,
                "type": spawn_type,
                "terminal": terminal,
            }
            if temp_dir_path:
                process_info["temp_dir_path"] = temp_dir_path
            if self._last_sshpass_file:
                process_info["sshpass_file"] = self._last_sshpass_file
                self._last_sshpass_file = None
            if spawn_type == "ssh" and actual_data:
                process_info["session"] = actual_data
            self.process_tracker.register_process(pid, process_info)

    def _generic_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
        spawn_type: str = "local",
    ) -> None:
        """Generic spawn callback for both local and SSH terminals."""
        try:
            _, temp_dir_path, name, actual_data = self._extract_spawn_callback_data(
                user_data, spawn_type
            )

            if error:
                self._handle_spawn_error(terminal, error, name, spawn_type, actual_data)
            else:
                self._handle_spawn_success(
                    pid, name, spawn_type, terminal, temp_dir_path, actual_data
                )

        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")

    def _default_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """Spawn callback for local terminals."""
        self._generic_spawn_callback(
            terminal, pid, error, user_data, spawn_type="local"
        )

    def _ssh_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """Spawn callback for SSH terminals."""
        self._generic_spawn_callback(terminal, pid, error, user_data, spawn_type="ssh")

    def _get_ssh_error_guidance(self, error_message: str) -> str:
        """Return user-friendly SSH error guidance."""
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
