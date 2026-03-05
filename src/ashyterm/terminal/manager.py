# ashyterm/terminal/manager.py

import os
import pathlib
import threading
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager
from ..utils.exceptions import TerminalCreationError
from ..utils.logger import get_logger, log_terminal_event
from ..utils.osc7 import OSC7Info, parse_directory_uri
from ..utils.osc7_tracker import get_osc7_tracker
from ..utils.platform import get_environment_manager, get_platform_info
from ..utils.security import validate_session_data
from ..utils.translation_utils import _
from .registry import ManualSSHTracker, TerminalLifecycleManager, TerminalRegistry
from .ssh_lifecycle import SSHLifecycleMixin
from .url_handler import URLHandlerMixin

# Lazy imports for heavy modules - loaded on first use

@lru_cache(maxsize=1)
def _get_spawner():
    """Lazy import spawner module."""
    from .spawner import get_spawner
    return get_spawner()


@lru_cache(maxsize=1)
def _get_highlight_manager():
    """Lazy import highlight manager."""
    from ..settings.highlights import get_highlight_manager
    return get_highlight_manager()


@lru_cache(maxsize=1)
def _get_output_highlighter():
    """Lazy import output highlighter."""
    from .highlighter import get_output_highlighter
    return get_output_highlighter()


@lru_cache(maxsize=1)
def _get_terminal_menu_creator():
    """Lazy import terminal menu creator."""
    from ..ui.menus import create_terminal_menu
    return create_terminal_menu


def _create_terminal_menu(*args, **kwargs):
    """Create terminal menu via lazy-loaded creator."""
    return _get_terminal_menu_creator()(*args, **kwargs)


class TerminalManager(SSHLifecycleMixin, URLHandlerMixin):
    def __init__(self, parent_window, settings_manager: SettingsManager, spawner=None):
        self.logger = get_logger("ashyterm.terminal.manager")
        self.parent_window = parent_window
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()
        self.environment_manager = get_environment_manager()
        self.registry = TerminalRegistry()
        self.spawner = spawner or _get_spawner()
        self.lifecycle_manager = TerminalLifecycleManager(self.registry, self.logger)
        self.osc7_tracker = get_osc7_tracker(settings_manager)
        self.manual_ssh_tracker = ManualSSHTracker(
            self.registry, self._on_manual_ssh_state_changed
        )
        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pending_kill_timers: Dict[int, int] = {}
        self.tab_manager = None
        self.on_terminal_focus_changed: Optional[Callable] = None
        self.terminal_exit_handler: Optional[Callable] = None
        self._stats = {
            "terminals_created": 0,
            "terminals_failed": 0,
            "terminals_closed": 0,
        }
        self._highlight_proxies: Dict[
            int, Any
        ] = {}  # Dict[int, HighlightedTerminalProxy]
        self._highlight_manager = None
        # Track when commands start for long-running command notifications
        self._command_start_times: Dict[int, float] = {}
        # Process check timer runs every 3 seconds to reduce main thread load
        # (psutil calls can be blocking and cause UI freezes on some systems)
        self._process_check_timer_id = GLib.timeout_add_seconds(
            3, self._periodic_process_check
        )
        self.logger.info("Terminal manager initialized")

    def prepare_initial_terminal(self) -> None:
        """
        Pre-create the base terminal widget and prepare shell environment in background.
        This allows the terminal to be ready faster when the first tab is created.
        Call this early during window initialization for best results.
        """
        self._precreated_terminal = None
        self._precreated_env_ready = threading.Event()
        self._precreated_env_data = None
        self._highlights_ready = threading.Event()

        # Create base terminal widget immediately (must be on main thread)
        # Note: Don't apply settings yet since window UI may not be fully ready
        try:
            self._precreated_terminal = self._create_base_terminal(apply_settings=False)
            if self._precreated_terminal:
                self.logger.info("Pre-created base terminal widget for faster startup")
        except Exception as e:
            self.logger.warning(f"Failed to pre-create terminal: {e}")
            self._precreated_terminal = None

        # Prepare shell environment and highlights in background thread
        def prepare_background():
            try:
                # Prepare shell environment
                cmd, env, temp_dir_path = self.spawner._prepare_shell_environment()
                self._precreated_env_data = (cmd, env, temp_dir_path)
                self.logger.debug("Pre-prepared shell environment in background")
            except Exception as e:
                self.logger.warning(f"Failed to pre-prepare shell environment: {e}")
                self._precreated_env_data = None
            finally:
                self._precreated_env_ready.set()

            # Pre-load the HighlightManager (loads 50+ JSON files)
            try:
                from ..settings.highlights import get_highlight_manager

                self._highlight_manager = get_highlight_manager()
                self.logger.debug(
                    "Pre-loaded HighlightManager (JSON rules) in background"
                )
            except Exception as e:
                self.logger.warning(f"Failed to pre-load HighlightManager: {e}")

            # Pre-load highlight modules (output, shell_input)
            # NOTE: HighlightedTerminalProxy import removed - importing GTK from background
            # threads can cause race conditions on Wayland
            try:
                from .highlighter.output import get_output_highlighter
                from .highlighter.shell_input import get_shell_input_highlighter

                get_output_highlighter()
                get_shell_input_highlighter()

                self.logger.debug("Pre-loaded highlighting modules in background")
            except Exception as e:
                self.logger.warning(f"Failed to pre-load highlights: {e}")
            finally:
                self._highlights_ready.set()

        bg_thread = threading.Thread(target=prepare_background, daemon=True)
        bg_thread.start()

    def get_precreated_terminal(self) -> "Optional[Vte.Terminal]":
        """
        Get the pre-created terminal if available.
        Returns None if no terminal was pre-created or it was already consumed.
        """
        terminal = getattr(self, "_precreated_terminal", None)
        self._precreated_terminal = None
        return terminal

    def get_precreated_env_data(self, timeout: float = 0.1) -> "Optional[tuple]":
        """
        Get the pre-prepared shell environment data if ready.
        Args:
            timeout: Max time to wait for env preparation (default 100ms)
        Returns:
            Tuple of (cmd, env, temp_dir_path) or None if not ready/failed
        """
        ready_event = getattr(self, "_precreated_env_ready", None)
        if ready_event and ready_event.wait(timeout):
            data = getattr(self, "_precreated_env_data", None)
            self._precreated_env_data = None
            return data
        return None

    def _get_highlight_manager(self):
        if self._highlight_manager is None:
            self._highlight_manager = _get_highlight_manager()
        return self._highlight_manager

    def _cleanup_highlight_proxy(self, terminal_id: int):
        proxy = self._highlight_proxies.pop(terminal_id, None)
        if proxy:
            try:
                proxy.stop()
                self.logger.debug(f"Stopped highlight proxy for terminal {terminal_id}")
            except Exception as e:
                self.logger.error(f"Error stopping highlight proxy: {e}")

    def pause_highlight_proxy(self, terminal_id: int) -> None:
        """Pause highlighting for an inactive terminal."""
        proxy = self._highlight_proxies.get(terminal_id)
        if proxy:
            proxy.pause_highlighting()

    def resume_highlight_proxy(self, terminal_id: int) -> None:
        """Resume highlighting when terminal becomes visible."""
        proxy = self._highlight_proxies.get(terminal_id)
        if proxy:
            proxy.resume_highlighting()

    def apply_settings_to_all_terminals(self):
        self.logger.info("Applying settings to all active terminals.")
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                try:
                    self.settings_manager.apply_terminal_settings(
                        terminal, self.parent_window
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to apply settings to terminal {terminal_id}: {e}"
                    )

    def set_tab_manager(self, tab_manager):
        self.tab_manager = tab_manager

    def set_terminal_exit_handler(self, handler: Callable):
        self.terminal_exit_handler = handler

    def _periodic_process_check(self) -> bool:
        """
        Periodic check to detect manual SSH sessions in local terminals.

        This runs every 3 seconds and checks for manual SSH sessions.
        Note: Context-aware highlighting is now handled by CommandDetector
        which parses the terminal output stream in real-time.
        """
        try:
            if self.parent_window and hasattr(self.parent_window, "tab_manager"):
                active_terminal = self.parent_window.tab_manager.get_selected_terminal()

                if active_terminal:
                    terminal_id = getattr(active_terminal, "terminal_id", None)
                    if terminal_id is not None:
                        # Check for manual SSH sessions
                        self.manual_ssh_tracker.check_process_tree(terminal_id)
        except Exception as e:
            self.logger.debug(f"Periodic check error: {e}")
        return True

    def _on_manual_ssh_state_changed(self, terminal: Vte.Terminal):
        self._update_title(terminal)
        return False

    def _resolve_working_directory(
        self, working_directory: Optional[str]
    ) -> Optional[str]:
        if not working_directory:
            return None
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))
            resolved_path = os.path.abspath(expanded_path)
            path_obj = pathlib.Path(resolved_path)
            if (
                path_obj.exists()
                and path_obj.is_dir()
                and os.access(resolved_path, os.R_OK | os.X_OK)
            ):
                return resolved_path
            else:
                self.logger.warning(
                    f"Working directory not accessible: {working_directory}"
                )
                return None
        except Exception as e:
            self.logger.error(
                f"Error resolving working directory '{working_directory}': {e}"
            )
            return None

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, _param_spec):
        try:
            uri = terminal.get_current_directory_uri()
            osc7_info = parse_directory_uri(uri, self.osc7_tracker.parser)
            if osc7_info:
                self._update_title(terminal, osc7_info)
            self._check_long_command_notification(terminal)
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    def _on_terminal_commit(
        self, terminal: Vte.Terminal, text: str, size: int, terminal_id: int
    ):
        """Track when user sends input containing Enter (command execution)."""
        import time

        if "\r" in text or "\n" in text:
            self._command_start_times[terminal_id] = time.monotonic()

    def _check_long_command_notification(self, terminal: Vte.Terminal):
        """Send desktop notification if a long-running command finished in a background tab."""
        import time

        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return

        start_time = self._command_start_times.pop(terminal_id, None)
        if start_time is None:
            return

        threshold = self.settings_manager.get("long_command_threshold", 30)
        if threshold <= 0:
            return

        elapsed = time.monotonic() - start_time
        if elapsed < threshold:
            return

        # Only notify if this terminal is NOT in the foreground
        if self._is_terminal_in_foreground(terminal):
            return

        app = self.parent_window.get_application()
        if not app:
            return

        terminal_info = self.registry.get_terminal_info(terminal_id)
        tab_label = "Terminal"
        if terminal_info:
            tab_label = terminal_info.get("name", "Terminal")

        elapsed_str = self._format_duration(elapsed)
        notification = Gio.Notification.new(_("Command Finished"))
        notification.set_body(
            _("{tab} — completed after {time}").format(tab=tab_label, time=elapsed_str)
        )
        notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
        app.send_notification(f"ashy-long-cmd-{terminal_id}", notification)

    def _is_terminal_in_foreground(self, terminal: Vte.Terminal) -> bool:
        """Check if a terminal is the currently visible/active one."""
        if not self.tab_manager:
            return True
        active_tab = self.tab_manager.active_tab
        if not active_tab:
            return False
        page = self.tab_manager.pages.get(active_tab)
        if not page:
            return False
        # Check if this terminal is visible in the active tab's page
        return terminal.is_visible() and terminal.get_mapped()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m"

    def _compute_terminal_title(
        self,
        terminal_info: dict,
        terminal_id: int,
        terminal: Vte.Terminal,
        osc7_info: Optional[OSC7Info],
    ) -> str:
        """Computes the display title for a terminal based on its type."""
        terminal_type = terminal_info.get("type")

        if terminal_type == "ssh":
            return self._get_ssh_title(terminal_info, osc7_info)

        if terminal_type == "local":
            return self._get_local_title(terminal_info, terminal_id, osc7_info)

        if terminal_type == "sftp":
            return self._get_sftp_title(terminal_info, terminal)

        return "Terminal"

    def _get_ssh_title(self, terminal_info: dict, osc7_info: Optional[OSC7Info]) -> str:
        """Gets the title for an SSH terminal."""
        session = terminal_info.get("identifier")
        if isinstance(session, SessionItem):
            if osc7_info:
                return f"{session.name}:{osc7_info.display_path}"
            return session.name
        return "Terminal"

    def _get_local_title(
        self, terminal_info: dict, terminal_id: int, osc7_info: Optional[OSC7Info]
    ) -> str:
        """Gets the title for a local terminal."""
        ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)
        if ssh_target:
            if osc7_info:
                return f"{ssh_target}:{osc7_info.display_path}"
            return ssh_target

        if osc7_info:
            return osc7_info.display_path

        identifier = terminal_info.get("identifier")
        if isinstance(identifier, SessionItem):
            return identifier.name
        return str(identifier)

    def _get_sftp_title(self, terminal_info: dict, terminal: Vte.Terminal) -> str:
        """Gets the title for an SFTP terminal."""
        session = terminal_info.get("identifier")
        if isinstance(session, SessionItem):
            return self._get_sftp_display_title(session, terminal)
        return "Terminal"

    def _update_title(
        self, terminal: Vte.Terminal, osc7_info: Optional[OSC7Info] = None
    ):
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        terminal_info = self.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            return

        if osc7_info is None:
            uri = terminal.get_current_directory_uri()
            if uri:
                osc7_info = parse_directory_uri(uri, self.osc7_tracker.parser)

        new_title = self._compute_terminal_title(
            terminal_info, terminal_id, terminal, osc7_info
        )

        if self.tab_manager:
            self.tab_manager.update_titles_for_terminal(terminal, new_title, osc7_info)

    def _get_sftp_display_title(
        self, session: SessionItem, terminal: Vte.Terminal
    ) -> str:
        if self.tab_manager:
            page = self.tab_manager.get_page_for_terminal(terminal)
            if page:
                for tab in self.tab_manager.tabs:
                    if self.tab_manager.pages.get(tab) == page:
                        return getattr(tab, "_base_title", f"SFTP-{session.name}")
        return f"SFTP-{session.name}"

    def create_local_terminal(
        self,
        session: Optional[SessionItem] = None,
        title: str = "Local Terminal",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ):
        terminal = self._get_or_create_terminal()
        if not terminal:
            raise TerminalCreationError("base terminal creation failed", "local")

        identifier = session if session else title
        terminal_id = self.registry.register_terminal(terminal, "local", identifier)
        self._setup_terminal_events(terminal, identifier, terminal_id)

        try:
            resolved_working_dir = self._resolve_working_directory(working_directory)
            if working_directory and not resolved_working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using default"
                )

            user_data_for_spawn = (
                terminal_id,
                {
                    "execute_command": execute_command,
                    "close_after_execute": close_after_execute,
                },
            )

            should_highlight, _ = self._compute_highlighting_config(
                session, is_local=True
            )

            if should_highlight:
                self._spawn_highlighted_local(
                    terminal,
                    session,
                    user_data_for_spawn,
                    resolved_working_dir,
                    terminal_id,
                )
            else:
                precreated_env = self._get_precreated_env(working_directory)
                self.spawner.spawn_local_terminal(
                    terminal,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    working_directory=resolved_working_dir,
                    precreated_env=precreated_env,
                )

            self._log_terminal_creation(session, title, terminal_id, "local")
            return terminal
        except TerminalCreationError:
            self.registry.unregister_terminal(terminal_id)
            self._cleanup_highlight_proxy(terminal_id)
            self._stats["terminals_failed"] += 1
            raise

    def _get_or_create_terminal(self) -> Optional[Vte.Terminal]:
        """Get a pre-created terminal or create a new one."""
        terminal = self.get_precreated_terminal()
        if terminal:
            self.logger.debug("Using pre-created terminal for faster startup")
            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)
            self.logger.debug("Applied terminal settings to pre-created terminal")
        else:
            terminal = self._create_base_terminal()
        return terminal

    def _get_precreated_env(self, working_directory: Optional[str]):
        """Get pre-prepared environment if no custom working directory."""
        if not working_directory:
            return self.get_precreated_env_data(timeout=0.05)
        return None

    def _compute_highlighting_config(
        self, session: Optional[SessionItem], is_local: bool
    ) -> tuple[bool, dict]:
        """Compute whether highlighting should be enabled and return config.

        Returns:
            Tuple of (should_spawn_highlighted, config_dict)
        """
        highlight_manager = self._get_highlight_manager()

        # Get base setting for local or SSH
        if is_local:
            output_enabled = highlight_manager.enabled_for_local
        else:
            output_enabled = highlight_manager.enabled_for_ssh

        # Per-session override
        if session and session.output_highlighting is not None:
            output_enabled = session.output_highlighting

        # Cat and shell input depend on output highlighting
        cat_enabled = output_enabled and self.settings_manager.get(
            "cat_colorization_enabled", True
        )
        shell_input_enabled = output_enabled and self.settings_manager.get(
            "shell_input_highlighting_enabled", False
        )

        # Per-session overrides
        if session:
            if session.cat_colorization is not None:
                cat_enabled = output_enabled and session.cat_colorization
            if session.shell_input_highlighting is not None:
                shell_input_enabled = (
                    output_enabled and session.shell_input_highlighting
                )

        should_highlight = output_enabled or cat_enabled or shell_input_enabled

        config = {
            "output_highlighting": output_enabled,
            "cat_colorization": cat_enabled,
            "shell_input_highlighting": shell_input_enabled,
        }
        return should_highlight, config

    def _spawn_highlighted_local(
        self,
        terminal,
        session: Optional[SessionItem],
        user_data_for_spawn,
        resolved_working_dir: Optional[str],
        terminal_id: int,
    ) -> None:
        """Spawn a highlighted local terminal."""
        # Wait briefly for highlight modules if preparing
        highlights_ready = getattr(self, "_highlights_ready", None)
        if highlights_ready is not None:
            highlights_ready.wait(timeout=0.05)

        proxy = self.spawner.spawn_highlighted_local_terminal(
            terminal,
            session=session,
            callback=self._on_spawn_callback,
            user_data=user_data_for_spawn,
            working_directory=resolved_working_dir,
            terminal_id=terminal_id,
        )
        if proxy:
            self._highlight_proxies[terminal_id] = proxy
            self.logger.info(f"Highlighted local terminal spawned (ID: {terminal_id})")
        else:
            self.logger.warning(
                "Highlighted spawn failed, falling back to standard spawning"
            )
            self.spawner.spawn_local_terminal(
                terminal,
                callback=self._on_spawn_callback,
                user_data=user_data_for_spawn,
                working_directory=resolved_working_dir,
            )

    def _log_terminal_creation(
        self,
        session: Optional[SessionItem],
        title: str,
        terminal_id: int,
        term_type: str,
    ) -> None:
        """Log terminal creation event."""
        log_title = session.name if session else title
        self.logger.info(
            f"{term_type.capitalize()} terminal created successfully: '{log_title}' (ID: {terminal_id})"
        )
        log_terminal_event("created", log_title, f"{term_type} terminal")
        self._stats["terminals_created"] += 1

    def _validate_session(self, session: SessionItem, terminal_type: str) -> None:
        """Validates session data before terminal creation."""
        session_data = session.to_dict()
        is_valid, errors = validate_session_data(session_data)
        if not is_valid:
            error_msg = f"Session validation failed for {terminal_type.upper()}: {', '.join(errors)}"
            raise TerminalCreationError(error_msg, terminal_type)

    def _setup_remote_terminal(
        self, session: SessionItem, terminal_type: str
    ) -> tuple[Vte.Terminal, int]:
        """Creates and sets up a remote terminal, returning terminal and ID."""
        terminal = self._create_base_terminal()
        if not terminal:
            raise TerminalCreationError(
                f"base terminal creation failed for {terminal_type.upper()}",
                terminal_type,
            )

        terminal_id = self.registry.register_terminal(terminal, terminal_type, session)
        self._setup_terminal_events(terminal, session, terminal_id)
        return terminal, terminal_id

    def _spawn_ssh_terminal(
        self,
        terminal: Vte.Terminal,
        session: SessionItem,
        user_data: tuple,
        initial_command: Optional[str],
        terminal_id: int,
    ) -> None:
        """Spawns an SSH terminal with optional highlighting."""
        highlight_config = self._get_ssh_highlight_config(session)

        if highlight_config["should_highlight"]:
            self._spawn_highlighted_ssh(
                terminal, session, user_data, initial_command, terminal_id
            )
        else:
            self.spawner.spawn_ssh_session(
                terminal,
                session,
                callback=self._on_spawn_callback,
                user_data=user_data,
                initial_command=initial_command,
            )

        self._setup_ssh_drag_and_drop(terminal, terminal_id)

    def _get_ssh_highlight_config(self, session: SessionItem) -> dict:
        """Determines SSH highlighting configuration based on settings and session."""
        highlight_manager = self._get_highlight_manager()
        output_enabled = highlight_manager.enabled_for_ssh

        if session.output_highlighting is not None:
            output_enabled = session.output_highlighting

        cat_enabled = output_enabled and self.settings_manager.get(
            "cat_colorization_enabled", True
        )
        shell_input_enabled = output_enabled and self.settings_manager.get(
            "shell_input_highlighting_enabled", False
        )

        if session.cat_colorization is not None:
            cat_enabled = output_enabled and session.cat_colorization
        if session.shell_input_highlighting is not None:
            shell_input_enabled = output_enabled and session.shell_input_highlighting

        return {
            "output_enabled": output_enabled,
            "cat_enabled": cat_enabled,
            "shell_input_enabled": shell_input_enabled,
            "should_highlight": output_enabled or cat_enabled or shell_input_enabled,
        }

    def _spawn_highlighted_ssh(
        self,
        terminal: Vte.Terminal,
        session: SessionItem,
        user_data: tuple,
        initial_command: Optional[str],
        terminal_id: int,
    ) -> None:
        """Spawns a highlighted SSH session with fallback."""
        proxy = self.spawner.spawn_highlighted_ssh_session(
            terminal,
            session,
            callback=self._on_spawn_callback,
            user_data=user_data,
            initial_command=initial_command,
            terminal_id=terminal_id,
        )
        if proxy:
            self._highlight_proxies[terminal_id] = proxy
            self.logger.info(f"Highlighted SSH terminal spawned (ID: {terminal_id})")
        else:
            self.logger.warning(
                "Highlighted SSH spawn failed, falling back to standard spawning"
            )
            self.spawner.spawn_ssh_session(
                terminal,
                session,
                callback=self._on_spawn_callback,
                user_data=user_data,
                initial_command=initial_command,
            )

    def _spawn_sftp_terminal(
        self,
        terminal: Vte.Terminal,
        session: SessionItem,
        user_data: tuple,
        local_directory: Optional[str],
        remote_path: Optional[str],
    ) -> None:
        """Spawns an SFTP terminal."""
        self._setup_sftp_drag_and_drop(terminal)
        self.spawner.spawn_sftp_session(
            terminal,
            session,
            callback=self._on_spawn_callback,
            user_data=user_data,
            local_directory=local_directory,
            remote_path=remote_path,
        )

    def _create_remote_terminal(
        self,
        session: SessionItem,
        terminal_type: str,
        initial_command: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
        sftp_local_directory: Optional[str] = None,
    ) -> Optional[Vte.Terminal]:
        with self._creation_lock:
            self._validate_session(session, terminal_type)
            terminal, terminal_id = self._setup_remote_terminal(session, terminal_type)
            user_data_for_spawn = (terminal_id, session)

            try:
                if terminal_type == "ssh":
                    self._spawn_ssh_terminal(
                        terminal,
                        session,
                        user_data_for_spawn,
                        initial_command,
                        terminal_id,
                    )
                elif terminal_type == "sftp":
                    self._spawn_sftp_terminal(
                        terminal,
                        session,
                        user_data_for_spawn,
                        sftp_local_directory,
                        sftp_remote_path,
                    )
                else:
                    raise ValueError(
                        f"Unsupported remote terminal type: {terminal_type}"
                    )

                self._log_terminal_creation(
                    session, session.name, terminal_id, terminal_type
                )
                return terminal
            except TerminalCreationError:
                self.registry.unregister_terminal(terminal_id)
                self._cleanup_highlight_proxy(terminal_id)
                self._stats["terminals_failed"] += 1
                raise

    def create_ssh_terminal(
        self, session: SessionItem, initial_command: Optional[str] = None
    ) -> Optional[Vte.Terminal]:
        commands: List[str] = []
        if initial_command:
            commands.append(initial_command)
        if session.post_login_command_enabled and session.post_login_command:
            commands.append(session.post_login_command)
        combined_command = "; ".join(commands) if commands else None
        return self._create_remote_terminal(session, "ssh", combined_command)

    def create_sftp_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        remote_path = None
        local_directory = None
        if session.sftp_session_enabled:
            remote_path = session.sftp_remote_directory or None
            local_directory = session.sftp_local_directory or None
        return self._create_remote_terminal(
            session,
            "sftp",
            sftp_remote_path=remote_path,
            sftp_local_directory=local_directory,
        )

    def _create_base_terminal(
        self, apply_settings: bool = True
    ) -> Optional[Vte.Terminal]:
        try:
            terminal = Vte.Terminal()
            terminal.set_vexpand(True)
            terminal.set_hexpand(True)
            terminal.set_mouse_autohide(True)
            terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            terminal.set_scroll_on_output(False)
            terminal.set_scroll_on_keystroke(True)
            if hasattr(terminal, "set_scroll_unit_is_pixels"):
                terminal.set_scroll_unit_is_pixels(True)
            if hasattr(terminal, "set_search_highlight_enabled"):
                terminal.set_search_highlight_enabled(True)
            if apply_settings:
                self.settings_manager.apply_terminal_settings(
                    terminal, self.parent_window
                )
            self._setup_context_menu(terminal)
            self._setup_url_patterns(terminal)

            return terminal
        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None

    def _apply_font_size_change(self, family, new_size):
        self.settings_manager.set("font", f"{family} {new_size}")
        if hasattr(self, "parent_window"):
            self.parent_window._update_font_sizer_widget()
        return False

    def _setup_sftp_drag_and_drop(self, terminal: Vte.Terminal):
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_file_drop, terminal)
        terminal.add_controller(drop_target)

    def _setup_ssh_drag_and_drop(self, terminal: Vte.Terminal, terminal_id: int):
        """Setup drag-and-drop for SSH terminals to upload files."""
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_ssh_file_drop, terminal, terminal_id)
        terminal.add_controller(drop_target)

    def _on_file_drop(self, drop_target, value, x, y, terminal: Vte.Terminal) -> bool:
        try:
            files = value.get_files()
            for file in files:
                local_path = file.get_path()
                if local_path:
                    command_to_send = f'put -r "{local_path}"\n'
                    self.logger.info(
                        f"File dropped on SFTP terminal. Sending command: {command_to_send.strip()}"
                    )
                    terminal.feed_child(command_to_send.encode("utf-8"))
            return True
        except Exception as e:
            self.logger.error(f"Error handling file drop for SFTP: {e}")
            return False

    def _on_ssh_file_drop(
        self, drop_target, value, x, y, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """Handle file drop on SSH terminal to initiate upload via file manager."""
        try:
            files = value.get_files()
            if not files:
                return False

            # Get session info for this terminal
            info = self.registry.get_terminal_info(terminal_id)
            if not info:
                self.logger.warning(f"No terminal info for ID {terminal_id}")
                return False

            session = info.get("identifier")

            # Check if terminal is in SSH session (either session-based or manual SSH)
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)
            if not ssh_target and not (
                isinstance(session, SessionItem) and session.is_ssh()
            ):
                self.logger.info("Drop target is not an SSH session, ignoring")
                return False

            # Get file paths
            local_paths = []
            for file in files:
                path = file.get_path()
                if path:
                    local_paths.append(path)

            if not local_paths:
                return False

            # Signal to show upload confirmation dialog
            # This will be handled by the window to show the file manager dialog
            self.logger.info(
                f"Files dropped on SSH terminal. Requesting upload dialog for {len(local_paths)} files."
            )

            # Emit signal to notify the window about the file drop
            GLib.idle_add(
                self._emit_ssh_file_drop_signal,
                terminal_id,
                local_paths,
                session,
                ssh_target,
            )
            return True

        except Exception as e:
            self.logger.error(f"Error handling file drop for SSH: {e}")
            return False

    def _emit_ssh_file_drop_signal(
        self, terminal_id: int, local_paths: list, session, ssh_target: str
    ):
        """Emit signal to notify about SSH file drop (runs on main thread)."""
        # Store the dropped files info for the window to pick up
        self._pending_ssh_upload = {
            "terminal_id": terminal_id,
            "local_paths": local_paths,
            "session": session,
            "ssh_target": ssh_target,
        }
        # Notify via the terminal-focus-changed signal mechanism
        # The window can check for pending uploads when handling focus
        if hasattr(self, "_ssh_file_drop_callback") and self._ssh_file_drop_callback:
            self._ssh_file_drop_callback(terminal_id, local_paths, session, ssh_target)
        return False

    def set_ssh_file_drop_callback(self, callback):
        """Set callback for SSH file drop events."""
        self._ssh_file_drop_callback = callback

    def _setup_terminal_events(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        try:
            terminal.ashy_handler_ids = []
            terminal.ashy_controllers = []

            handler_id = terminal.connect(
                "child-exited", self._on_child_exited, identifier, terminal_id
            )
            terminal.ashy_handler_ids.append(handler_id)

            handler_id = terminal.connect("eof", self._on_eof, identifier, terminal_id)
            terminal.ashy_handler_ids.append(handler_id)

            handler_id = terminal.connect(
                "notify::current-directory-uri", self._on_directory_uri_changed
            )
            terminal.ashy_handler_ids.append(handler_id)

            handler_id = terminal.connect(
                "commit", self._on_terminal_commit, terminal_id
            )
            terminal.ashy_handler_ids.append(handler_id)

            self.manual_ssh_tracker.track(terminal_id, terminal)

            click_controller = Gtk.GestureClick()
            click_controller.set_button(1)
            click_controller.connect(
                "pressed", self._on_terminal_clicked, terminal, terminal_id
            )
            terminal.add_controller(click_controller)
            terminal.ashy_controllers.append(click_controller)

            # Context menu is now handled natively by VTE via setup-context-menu signal

            # Key event controller for command detection via screen scraping
            # Captures Enter key press to read the current command line from VTE
            key_controller = Gtk.EventControllerKey()
            key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            key_controller.connect(
                "key-pressed",
                self._on_terminal_key_pressed_for_detection,
                terminal,
                terminal_id,
            )
            terminal.add_controller(key_controller)
            terminal.ashy_controllers.append(key_controller)

            terminal.terminal_id = terminal_id
        except Exception as e:
            self.logger.error(
                f"Failed to configure terminal events for ID {terminal_id}: {e}"
            )

    def _setup_context_menu(self, terminal: Vte.Terminal) -> None:
        """Configure VTE's native context menu support."""
        terminal.connect("setup-context-menu", self._on_setup_context_menu)

    def _on_setup_context_menu(self, terminal: Vte.Terminal, context) -> None:
        """Build context menu when VTE requests it (native handler)."""
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return

        menu_model = _create_terminal_menu(
            terminal,
            terminal_id,
            settings_manager=self.settings_manager,
        )
        terminal.set_context_menu_model(menu_model)

    def _update_context_menu_with_url(
        self,
        terminal: Vte.Terminal,
        x: float,
        y: float,  # noqa: ARG002
    ) -> None:
        # URL context menu updates are handled elsewhere
        pass

    def _on_terminal_focus_in(self, _controller, terminal, terminal_id):
        try:
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
        except Exception as e:
            self.logger.error(f"Terminal focus in handling failed: {e}")

    def copy_selection(self, terminal: Vte.Terminal):
        if terminal.get_has_selection():
            terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste_clipboard(self, terminal: Vte.Terminal):
        terminal.paste_clipboard()

    def select_all(self, terminal: Vte.Terminal):
        terminal.select_all()

    def clear_terminal(self, terminal: Vte.Terminal):
        try:
            terminal.reset(True, True)

            def _send_newline():
                try:
                    if hasattr(terminal, "feed_child_binary"):
                        terminal.feed_child_binary(b"\n")
                    else:
                        terminal.feed_child("\n", -1)
                except Exception as exc:
                    self.logger.debug(f"Failed to send newline after clear: {exc}")
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(120, _send_newline)
            terminal_id = getattr(terminal, "terminal_id", None)
            terminal_name = "terminal"
            if terminal_id is not None:
                info = self.registry.get_terminal_info(terminal_id) or {}
                terminal_name = (
                    info.get("title")
                    or info.get("session_name")
                    or info.get("type")
                    or f"terminal-{terminal_id}"
                )
                log_terminal_event(
                    "cleared", terminal_name, "screen and scrollback cleared"
                )
            self.logger.info(f"Cleared terminal output for {terminal_name}")
        except Exception as e:
            self.logger.error(f"Failed to clear terminal output: {e}")

    def cleanup_all_terminals(self):
        """
        Force closes all terminals managed by this window instance.
        Corrected to only kill processes owned by this window, avoiding global app shutdown.
        """
        if self._process_check_timer_id:
            GLib.source_remove(self._process_check_timer_id)
            self._process_check_timer_id = None

        # Clean up all highlight proxies
        for terminal_id in self._highlight_proxies.copy():
            self._cleanup_highlight_proxy(terminal_id)

        # KILL ONLY LOCAL PROCESSES BELONGING TO THIS WINDOW
        spawner = _get_spawner()
        all_ids = self.registry.get_all_terminal_ids()

        count_killed = 0
        for t_id in all_ids:
            info = self.registry.get_terminal_info(t_id)
            if info and info.get("process_id"):
                pid = info["process_id"]
                # Use the new targeted kill method
                spawner.process_tracker.terminate_process(pid)
                count_killed += 1

        self.logger.info(
            f"cleanup_all_terminals: Terminated {count_killed} processes for this window."
        )
