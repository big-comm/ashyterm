# ashyterm/terminal/local_spawn_mixin.py

import fcntl
import os
import shutil
import subprocess
import tempfile
import termios
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem
    from .highlighter import HighlightedTerminalProxy

from ..utils.flatpak_host import (
    get_host_shell, host_shell_command, is_flatpak, prepare_host_bash, shell_temp_root,
)
from ..utils.logger import log_terminal_event
from ..utils.osc7 import OSC7_HOST_DETECTION_SNIPPET


class LocalSpawnMixin:
    """Mixin providing local terminal spawning capabilities."""

    # Attributes provided by ProcessSpawner:
    # - logger
    # - settings_manager
    # - platform_info
    # - command_builder
    # - environment_manager
    # - process_tracker
    # - _spawn_lock

    def _get_expected_terminal_size(self, terminal: Vte.Terminal) -> Tuple[int, int]:
        """Get expected terminal size (rows, cols) from saved window dimensions.
        Avoid initial resize SIGWINCH by matching PTY size to restored window.
        """
        try:
            window_width = self.settings_manager.get("window_width", 1200)
            window_height = self.settings_manager.get("window_height", 700)

            # headerbar ~47px, tabbar ~36px, padding ~20px
            ui_overhead_height = 103
            ui_overhead_width = 20

            available_width = max(400, window_width - ui_overhead_width)
            available_height = max(200, window_height - ui_overhead_height)

            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()

            if char_width > 0 and char_height > 0:
                cols = max(40, available_width // char_width)
                rows = max(10, available_height // char_height)
                self.logger.debug(
                    f"Calculated expected terminal size: {rows}x{cols} "
                    f"(window: {window_width}x{window_height}, "
                    f"char: {char_width}x{char_height})"
                )
                return (rows, cols)
        except Exception as e:
            self.logger.debug(f"Could not calculate expected terminal size: {e}")

        rows = terminal.get_row_count() or 24
        cols = terminal.get_column_count() or 80
        return (rows, cols)

    def _prepare_shell_environment(
        self,
    ) -> Tuple[List[str], Dict[str, str], Optional[str], str]:
        """Prepare shell env for local terminal spawn.
        Handles: user shell detection, VTE version env, OSC7 integration, login shell config.

        Returns: (command_list, environment_dict, temp_dir_path, shell_name)
        """
        shell = get_host_shell() if is_flatpak() else Vte.get_user_shell()
        shell_basename = os.path.basename(shell)
        temp_dir_path: Optional[str] = None

        env = self.environment_manager.get_terminal_environment()
        osc7_command = (
            f"__ashyterm_osc7() {{ {OSC7_HOST_DETECTION_SNIPPET} "
            'printf "\\033]7;file://%s%s\\007" "$ASHYTERM_OSC7_HOST" "$PWD"; }; __ashyterm_osc7'
        )

        if shell_basename == "zsh":
            try:
                temp_dir_path = tempfile.mkdtemp(
                    prefix="ashyterm_zsh_",
                    dir=shell_temp_root() if is_flatpak() else None,
                )
                zshrc_path = os.path.join(temp_dir_path, ".zshrc")

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

                env["ZDOTDIR"] = temp_dir_path
                self.logger.info(
                    f"Using temporary ZDOTDIR for zsh OSC7 integration: {temp_dir_path}"
                )

            except Exception as e:
                self.logger.error(f"Failed to set up zsh OSC7 integration: {e}")
                if temp_dir_path:
                    shutil.rmtree(temp_dir_path, ignore_errors=True)
                temp_dir_path = None
        else:
            self.logger.info("Bash detected - using native shell behavior for OSC7.")

        if self.settings_manager.get("use_login_shell", False):
            cmd = [shell, "-l"]
            self.logger.info(f"Spawning '{shell} -l' as a login shell.")
        else:
            cmd = [shell]
            if is_flatpak() and shell_basename == "bash":
                temp_dir_path, rcfile = prepare_host_bash(osc7_command)
                cmd.extend(["--rcfile", rcfile])

        if is_flatpak():
            if temp_dir_path is None:
                temp_dir_path = tempfile.mkdtemp(prefix="ashyterm_host_", dir=shell_temp_root())
            env["_ASHYTERM_HOST_STATE"] = os.path.join(temp_dir_path, "host-process")
        return cmd, env, temp_dir_path, shell_basename

    def _create_pty_preexec_fn(
        self, slave_fd: int, master_fd: int, claim_ctty: bool = True
    ) -> Callable[[], None]:
        """Create preexec_fn for PTY setup in child process."""

        def preexec_fn() -> None:
            os.setsid()
            if claim_ctty:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(master_fd)

        return preexec_fn

    def _cleanup_pty_fds(
        self,
        slave_fd: Optional[int],
        master_fd: Optional[int],
        slave_fd_closed: bool,
    ) -> None:
        """Clean up PTY file descriptors on error."""
        if slave_fd is not None and not slave_fd_closed:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass

    def _invoke_spawn_error_callback(
        self,
        callback: Optional[Callable],
        terminal: Vte.Terminal,
        error: Exception,
        user_data: Any,
        temp_dir_path: Optional[str],
    ) -> None:
        """Invoke callback with spawn error information."""
        if callback:
            glib_error = GLib.Error.new_literal(
                GLib.quark_from_string("spawn-error"),
                str(error),
                0,
            )
            final_user_data = {
                "original_user_data": user_data,
                "temp_dir_path": temp_dir_path,
            }
            GLib.idle_add(callback, terminal, -1, glib_error, (final_user_data,))

    def _run_highlighted_spawn(
        self,
        proxy: "HighlightedTerminalProxy",
        terminal: Vte.Terminal,
        cmd: List[str],
        working_dir: str,
        env: Dict[str, str],
        process_name: str,
        spawn_type: str,
        callback: Optional[Callable],
        user_data: Any,
        temp_dir_path: Optional[str],
        session: Optional["SessionItem"] = None,
    ) -> Optional["HighlightedTerminalProxy"]:
        """Execute common highlighted spawn logic.
        Handles PTY creation, process spawning, cleanup — shared between local and SSH.
        """
        master_fd: Optional[int] = None
        slave_fd: Optional[int] = None
        slave_fd_closed = False

        try:
            master_fd, slave_fd = proxy.create_pty()
            rows, cols = self._get_expected_terminal_size(terminal)
            proxy.set_window_size(rows, cols)

            # Flatpak's host helper must acquire the controlling terminal.
            preexec_fn = self._create_pty_preexec_fn(
                slave_fd, master_fd,
                claim_ctty=not (spawn_type == "local" and is_flatpak()),
            )

            if spawn_type == "local":
                cmd = host_shell_command(cmd, env, working_dir, terminal)
            proc = subprocess.Popen(
                cmd,
                cwd=working_dir,
                env=env,
                preexec_fn=preexec_fn,
                close_fds=False,
            )
            pid = proc.pid

            os.close(slave_fd)
            slave_fd_closed = True

            if not proxy.start(pid):
                self.logger.error(f"Failed to start highlight proxy for {spawn_type}")
                os.close(master_fd)
                return None

            process_info: Dict[str, Any] = {
                "name": process_name,
                "type": spawn_type,
                "terminal": terminal,
                "highlight_proxy": proxy,
            }
            if temp_dir_path:
                process_info["temp_dir_path"] = temp_dir_path
            if self._last_sshpass_file:
                process_info["sshpass_file"] = self._last_sshpass_file
                self._last_sshpass_file = None
            if session:
                process_info["session"] = session

            self.process_tracker.register_process(pid, process_info)

            if callback:
                final_user_data = {
                    "original_user_data": user_data,
                    "temp_dir_path": temp_dir_path,
                }
                GLib.idle_add(callback, terminal, pid, None, (final_user_data,))

            self.logger.info(
                f"Highlighted {spawn_type} terminal launched with PID {pid}"
            )

            return proxy

        except Exception as e:
            self._cleanup_pty_fds(slave_fd, master_fd, slave_fd_closed)
            raise e

    def spawn_local_terminal(
        self,
        terminal: Vte.Terminal,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
        precreated_env: Optional[tuple] = None,
    ) -> None:
        """Spawn a local terminal session. Raises TerminalCreationError on setup failure."""
        with self._spawn_lock:
            working_dir = self._resolve_and_validate_working_directory(
                working_directory
            )
            if working_directory and not working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using home directory."
                )

            if precreated_env and not working_directory:
                cmd, env, temp_dir_path, _shell_name = precreated_env
                self.logger.debug("Using pre-prepared shell environment")
            else:
                cmd, env, temp_dir_path, _shell_name = self._prepare_shell_environment()
            cmd = host_shell_command(cmd, env, working_dir, terminal)
            env_list = [f"{k}={v}" for k, v in env.items()]

            final_user_data = {
                "original_user_data": user_data,
                "temp_dir_path": temp_dir_path,
            }

            terminal.spawn_async(
                Vte.PtyFlags.NO_CTTY if is_flatpak() else Vte.PtyFlags.DEFAULT,
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
            self.logger.info("Local terminal launch initiated successfully")
            log_terminal_event(
                "launch_initiated", str(user_data), f"shell command: {' '.join(cmd)}"
            )

    def spawn_highlighted_local_terminal(
        self,
        terminal: Vte.Terminal,
        session: Optional["SessionItem"] = None,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
        terminal_id: Optional[int] = None,
    ) -> Optional["HighlightedTerminalProxy"]:
        """Spawn a local terminal with output highlighting support."""
        from .highlighter import HighlightedTerminalProxy

        with self._spawn_lock:
            working_dir = self._resolve_and_validate_working_directory(
                working_directory
            )
            if working_directory and not working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using home directory."
                )
            if not working_dir:
                working_dir = str(self.platform_info.home_dir)

            cmd, env, temp_dir_path, shell_name = self._prepare_shell_environment()

            proxy = HighlightedTerminalProxy(
                terminal,
                "local",
                proxy_id=terminal_id,
                shell_name=shell_name,
            )

            process_name = str(user_data) if user_data else "Terminal"

            try:
                result = self._run_highlighted_spawn(
                    proxy=proxy,
                    terminal=terminal,
                    cmd=cmd,
                    working_dir=working_dir,
                    env=env,
                    process_name=process_name,
                    spawn_type="local",
                    callback=callback,
                    user_data=user_data,
                    temp_dir_path=temp_dir_path,
                )

                if result:
                    log_terminal_event(
                        "launch_initiated",
                        process_name,
                        f"highlighted shell: {' '.join(cmd)}",
                    )

                return result

            except Exception as e:
                self.logger.error(f"Highlighted spawn failed: {e}")
                proxy.stop()
                self._invoke_spawn_error_callback(
                    callback, terminal, e, user_data, temp_dir_path
                )
                return None

    def _resolve_and_validate_working_directory(
        self, working_directory: Optional[str]
    ) -> str:
        """Resolve and validate working directory. Return home dir on any failure."""
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
