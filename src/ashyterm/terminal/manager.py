# ashyterm/terminal/manager.py

import os
import pathlib
import re
import signal
import subprocess
import threading
import time
import weakref
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlparse

# Lazy import psutil - only when actually needed for process info
PSUTIL_AVAILABLE: Optional[bool] = None
psutil = None


def _get_psutil():
    """Lazy import psutil module."""
    global psutil, PSUTIL_AVAILABLE
    if PSUTIL_AVAILABLE is None:
        try:
            import psutil as _psutil

            psutil = _psutil
            PSUTIL_AVAILABLE = True
        except ImportError:
            PSUTIL_AVAILABLE = False
    return psutil


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, GObject, Gtk, Vte

from ..helpers import is_valid_url
from ..sessions.models import SessionItem
from ..settings.config import PROMPT_TERMINATOR_PATTERN
from ..settings.manager import SettingsManager
from ..utils.exceptions import (
    TerminalCreationError,
)
from ..utils.logger import get_logger, log_terminal_event
from ..utils.osc7 import OSC7Info, parse_directory_uri
from ..utils.osc7_tracker import get_osc7_tracker
from ..utils.platform import get_environment_manager, get_platform_info
from ..utils.security import validate_session_data
from ..utils.translation_utils import _

# Pre-compiled pattern for ANSI escape sequences used in command detection
# Matches: Standard CSI sequences and OSC sequences
_ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b\].*?\x07|\[+\??(?:\d*[;]?)*[ABCDEFGHJKPSTfmnsuhl]"
)

# Lazy import for spawner - loaded on first use
_spawner = None


def _get_spawner():
    """Lazy import spawner module."""
    global _spawner
    if _spawner is None:
        from .spawner import get_spawner

        _spawner = get_spawner
    return _spawner()


# Lazy imports for heavy modules - loaded on first use
_highlight_manager = None
_output_highlighter = None
_terminal_menu_creator = None


def _get_highlight_manager():
    """Lazy import highlight manager."""
    global _highlight_manager
    if _highlight_manager is None:
        from ..settings.highlights import get_highlight_manager

        _highlight_manager = get_highlight_manager
    return _highlight_manager()


def _get_output_highlighter():
    """Lazy import output highlighter."""
    global _output_highlighter
    if _output_highlighter is None:
        from .highlighter import get_output_highlighter

        _output_highlighter = get_output_highlighter
    return _output_highlighter()


def _create_terminal_menu(*args, **kwargs):
    """Lazy import terminal menu creator."""
    global _terminal_menu_creator
    if _terminal_menu_creator is None:
        from ..ui.menus import create_terminal_menu

        _terminal_menu_creator = create_terminal_menu
    return _terminal_menu_creator(*args, **kwargs)


class TerminalState(Enum):
    FOCUSED = "focused"
    UNFOCUSED = "unfocused"
    EXITED = "exited"
    SPAWN_FAILED = "spawn_failed"


class TerminalLifecycleManager:
    def __init__(self, registry, logger):
        self.registry = registry
        self.logger = logger
        self._closing_terminals = set()
        self._lock = threading.RLock()

    def mark_terminal_closing(self, terminal_id: int) -> bool:
        with self._lock:
            if terminal_id in self._closing_terminals:
                return False
            self._closing_terminals.add(terminal_id)
            return True

    def unmark_terminal_closing(self, terminal_id: int) -> None:
        with self._lock:
            self._closing_terminals.discard(terminal_id)

    def transition_state(self, terminal_id: int, new_state: TerminalState) -> bool:
        with self._lock:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                return False
            current_state = terminal_info.get("status", "")
            if new_state == TerminalState.EXITED and current_state.startswith("exited"):
                return False
            self.registry.update_terminal_status(terminal_id, new_state.value)
            return True


class ManualSSHTracker:
    def __init__(self, registry, on_state_changed_callback):
        self.logger = get_logger("ashyterm.terminal.ssh_tracker")
        self.registry = registry
        self.on_state_changed = on_state_changed_callback
        self._tracked_terminals = {}
        self._lock = threading.Lock()
        self._last_child_count = {}

    def track(self, terminal_id: int, terminal: Vte.Terminal):
        with self._lock:
            if terminal_id not in self._tracked_terminals:
                self._tracked_terminals[terminal_id] = {
                    "terminal_ref": weakref.ref(terminal),
                    "in_ssh": False,
                    "ssh_target": None,
                }

    def untrack(self, terminal_id: int):
        with self._lock:
            self._tracked_terminals.pop(terminal_id, None)
            self._last_child_count.pop(terminal_id, None)

    def get_ssh_target(self, terminal_id: int) -> Optional[str]:
        with self._lock:
            state = self._tracked_terminals.get(terminal_id)
            if state and state.get("in_ssh"):
                return state.get("ssh_target")
            return None

    def check_process_tree(self, terminal_id: int):
        psutil_mod = _get_psutil()
        if not psutil_mod:
            return

        with self._lock:
            if terminal_id not in self._tracked_terminals:
                return

            state = self._tracked_terminals[terminal_id]
            pid = self._get_terminal_pid(terminal_id)
            if not pid:
                return

            try:
                self._check_ssh_state(terminal_id, state, pid, psutil_mod)
            except psutil_mod.NoSuchProcess:
                self._handle_process_gone(terminal_id, state)
            except Exception as e:
                self.logger.debug(
                    f"Error checking process tree for terminal {terminal_id}: {e}"
                )

    def _get_terminal_pid(self, terminal_id: int) -> Optional[int]:
        """Get the process ID for a local terminal.

        Args:
            terminal_id: The terminal identifier.

        Returns:
            Process ID or None if not a local terminal.
        """
        terminal_info = self.registry.get_terminal_info(terminal_id)
        if not terminal_info or terminal_info.get("type") != "local":
            return None
        return terminal_info.get("process_id")

    def _check_ssh_state(
        self, terminal_id: int, state: dict, pid: int, psutil_mod
    ) -> None:
        """Check if SSH state has changed for a terminal.

        Args:
            terminal_id: The terminal identifier.
            state: Current tracking state dict.
            pid: Parent process ID.
            psutil_mod: The psutil module.
        """
        parent_proc = psutil_mod.Process(pid)
        current_children_count = len(parent_proc.children())

        # Optimization: skip if child count unchanged and not in SSH
        if self._last_child_count.get(terminal_id) == current_children_count:
            if not state["in_ssh"]:
                return

        self._last_child_count[terminal_id] = current_children_count

        children = parent_proc.children(recursive=True)
        ssh_proc = next((p for p in children if p.name().lower() == "ssh"), None)
        currently_in_ssh = ssh_proc is not None

        if currently_in_ssh != state["in_ssh"]:
            self._update_ssh_state(terminal_id, state, ssh_proc, currently_in_ssh)

    def _update_ssh_state(
        self, terminal_id: int, state: dict, ssh_proc, currently_in_ssh: bool
    ) -> None:
        """Update SSH state and notify callback.

        Args:
            terminal_id: The terminal identifier.
            state: Current tracking state dict.
            ssh_proc: The SSH process if found.
            currently_in_ssh: Whether currently in SSH session.
        """
        if currently_in_ssh:
            state["in_ssh"] = True
            cmdline = ssh_proc.cmdline()
            state["ssh_target"] = next(
                (arg for arg in cmdline if "@" in arg), ssh_proc.name()
            )
            self.logger.info(
                f"Detected manual SSH session in terminal {terminal_id}: {state['ssh_target']}"
            )
        else:
            self.logger.info(f"Manual SSH session ended in terminal {terminal_id}")
            state["in_ssh"] = False
            state["ssh_target"] = None

        self._notify_state_changed(state)

    def _handle_process_gone(self, _terminal_id: int, state: dict) -> None:
        """Handle when the process no longer exists.

        Args:
            _terminal_id: The terminal identifier (reserved for future use, prefixed with _ to indicate unused).
            state: Current tracking state dict.
        """
        if state["in_ssh"]:
            state["in_ssh"] = False
            state["ssh_target"] = None
            self._notify_state_changed(state)

    def _notify_state_changed(self, state: dict) -> None:
        """Notify callback that SSH state changed.

        Args:
            state: Tracking state dict with terminal reference.
        """
        terminal = state["terminal_ref"]()
        if terminal and self.on_state_changed:
            GLib.idle_add(self.on_state_changed, terminal)


class TerminalRegistry:
    def __init__(self):
        self.logger = get_logger("ashyterm.terminal.registry")
        self._terminals: Dict[int, Dict[str, Any]] = {}
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._lock = threading.RLock()
        self._next_id = 1

    def register_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_type: str,
        identifier: Union[str, SessionItem],
    ) -> int:
        with self._lock:
            terminal_id = self._next_id
            self._next_id += 1
            self._terminals[terminal_id] = {
                "type": terminal_type,
                "identifier": identifier,
                "created_at": time.time(),
                "process_id": None,
                "status": "initializing",
            }

            def cleanup_callback(ref):
                self._cleanup_terminal_ref(terminal_id)

            self._terminal_refs[terminal_id] = weakref.ref(terminal, cleanup_callback)
            return terminal_id

    def reregister_terminal(
        self, terminal: Vte.Terminal, terminal_id: int, terminal_info: Dict[str, Any]
    ):
        """Re-registers a terminal that was moved from another window."""
        with self._lock:
            self._terminals[terminal_id] = terminal_info
            self._terminal_refs[terminal_id] = weakref.ref(
                terminal, lambda ref: self._cleanup_terminal_ref(terminal_id)
            )
            self._next_id = max(self._next_id, terminal_id + 1)
            self.logger.info(f"Re-registered terminal {terminal_id} in new window.")

    def deregister_terminal_for_move(
        self, terminal_id: int
    ) -> Optional[Dict[str, Any]]:
        """Removes a terminal from the registry for moving, without cleanup."""
        with self._lock:
            if terminal_id in self._terminals:
                self.logger.info(f"De-registering terminal {terminal_id} for move.")
                self._terminal_refs.pop(terminal_id, None)
                return self._terminals.pop(terminal_id, None)
            return None

    def get_active_terminal_count(self) -> int:
        with self._lock:
            return sum(
                1
                for info in self._terminals.values()
                if info.get("status") not in ["exited", "spawn_failed"]
            )

    def update_terminal_process(self, terminal_id: int, process_id: int) -> None:
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]["process_id"] = process_id
                self._terminals[terminal_id]["status"] = "running"

    def update_terminal_status(self, terminal_id: int, status: str) -> None:
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]["status"] = status

    def get_terminal(self, terminal_id: int) -> Optional[Vte.Terminal]:
        with self._lock:
            ref = self._terminal_refs.get(terminal_id)
            return ref() if ref else None

    def get_terminal_info(self, terminal_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._terminals.get(terminal_id, {}).copy()

    def unregister_terminal(self, terminal_id: int) -> bool:
        with self._lock:
            if terminal_id in self._terminals:
                del self._terminals[terminal_id]
                if terminal_id in self._terminal_refs:
                    del self._terminal_refs[terminal_id]
                return True
            return False

    def _cleanup_terminal_ref(self, terminal_id: int) -> None:
        with self._lock:
            if terminal_id in self._terminal_refs:
                del self._terminal_refs[terminal_id]

    def get_all_terminal_ids(self) -> List[int]:
        with self._lock:
            return list(self._terminals.keys())

    def get_terminals_for_session(self, session_name: str) -> List[int]:
        """
        Get all terminal IDs for a given session name.

        Args:
            session_name: Name of the session to find terminals for.

        Returns:
            List of terminal IDs associated with the session.
        """
        with self._lock:
            result = []
            for tid, info in self._terminals.items():
                identifier = info.get("identifier")
                if (
                    isinstance(identifier, SessionItem)
                    and identifier.name == session_name
                ):
                    result.append(tid)
            return result

    def get_active_ssh_sessions(self) -> Dict[str, List[int]]:
        """
        Get all active SSH/SFTP sessions grouped by session name.

        Returns:
            Dictionary mapping session names to lists of terminal IDs.
        """
        with self._lock:
            sessions: Dict[str, List[int]] = {}
            for tid, info in self._terminals.items():
                if info.get("type") in ["ssh", "sftp"]:
                    identifier = info.get("identifier")
                    if isinstance(identifier, SessionItem):
                        name = identifier.name
                        if name not in sessions:
                            sessions[name] = []
                        sessions[name].append(tid)
            return sessions

    def get_terminals_by_status(self, status: str) -> List[int]:
        """
        Get all terminal IDs with a specific status.

        Args:
            status: The status to filter by (e.g., "running", "disconnected").

        Returns:
            List of terminal IDs with the given status.
        """
        with self._lock:
            return [
                tid
                for tid, info in self._terminals.items()
                if info.get("status") == status
            ]

    def get_terminals_by_type(self, terminal_type: str) -> List[int]:
        """
        Get all terminal IDs of a specific type.

        Args:
            terminal_type: The type to filter by (e.g., "ssh", "sftp", "local").

        Returns:
            List of terminal IDs of the given type.
        """
        with self._lock:
            return [
                tid
                for tid, info in self._terminals.items()
                if info.get("type") == terminal_type
            ]

    def get_session_terminal_count(self, session_name: str) -> int:
        """
        Get the count of terminals for a session.

        Args:
            session_name: Name of the session.

        Returns:
            Number of terminals associated with the session.
        """
        return len(self.get_terminals_for_session(session_name))

    def update_terminal_connection_status(
        self, terminal_id: int, connected: bool, error_message: Optional[str] = None
    ) -> None:
        """
        Update the connection status of an SSH/SFTP terminal.

        Args:
            terminal_id: The terminal to update.
            connected: Whether the terminal is connected.
            error_message: Optional error message if disconnected.
        """
        with self._lock:
            if terminal_id in self._terminals:
                info = self._terminals[terminal_id]
                if connected:
                    info["status"] = "connected"
                    info["connected_at"] = time.time()
                    info.pop("last_error", None)
                    info["reconnect_attempts"] = 0
                else:
                    info["status"] = "disconnected"
                    info["disconnected_at"] = time.time()
                    if error_message:
                        info["last_error"] = error_message

    def increment_reconnect_attempts(self, terminal_id: int) -> int:
        """
        Increment and return the reconnect attempt count for a terminal.

        Args:
            terminal_id: The terminal to update.

        Returns:
            The new reconnect attempt count.
        """
        with self._lock:
            if terminal_id in self._terminals:
                info = self._terminals[terminal_id]
                attempts = info.get("reconnect_attempts", 0) + 1
                info["reconnect_attempts"] = attempts
                return attempts
            return 0


class TerminalManager:
    def __init__(self, parent_window, settings_manager: SettingsManager):
        self.logger = get_logger("ashyterm.terminal.manager")
        self.parent_window = parent_window
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()
        self.environment_manager = get_environment_manager()
        self.registry = TerminalRegistry()
        self.spawner = _get_spawner()
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
                # Pass from_destroy=False by default as we don't know if it's from destroy here.
                # But usually this is called from cleanup logic, so widget might be dying.
                # Safe to just call stop(), it handles internal state.
                proxy.stop()
                self.logger.debug(f"Stopped highlight proxy for terminal {terminal_id}")
            except Exception as e:
                self.logger.error(f"Error stopping highlight proxy: {e}")

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

        This runs every 2 seconds and checks for manual SSH sessions.
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
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

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
        terminal_id: str,
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
        terminal_id: str,
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
            terminal.set_scroll_unit_is_pixels(True)
            if hasattr(terminal, "set_search_highlight_enabled"):
                terminal.set_search_highlight_enabled(True)
            if apply_settings:
                self.settings_manager.apply_terminal_settings(
                    terminal, self.parent_window
                )
            self._setup_context_menu(terminal)
            self._setup_url_patterns(terminal)

            # Enable kinetic scrolling for touchpad if setting is enabled
            if self.settings_manager.get("kinetic_scrolling", True):
                scroll_controller = Gtk.EventControllerScroll.new(
                    Gtk.EventControllerScrollFlags.BOTH_AXES
                    | Gtk.EventControllerScrollFlags.KINETIC
                )
                terminal.add_controller(scroll_controller)

            return terminal
        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None

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

    def _cancel_pending_kill_timer(self, terminal_id: int) -> None:
        """Cancels any pending kill timer for the terminal."""
        if terminal_id in self._pending_kill_timers:
            GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _analyze_exit_status(
        self, terminal: Vte.Terminal, terminal_info: dict, child_status: int
    ) -> dict:
        """Analyzes the exit status and returns exit information."""
        import os as os_module

        if os_module.WIFEXITED(child_status):
            decoded_exit_code = os_module.WEXITSTATUS(child_status)
        elif os_module.WIFSIGNALED(child_status):
            decoded_exit_code = 128 + os_module.WTERMSIG(child_status)
        else:
            decoded_exit_code = child_status

        user_terminated_codes = {130, 137, 143}  # SIGINT, SIGKILL, SIGTERM
        is_user_terminated = decoded_exit_code in user_terminated_codes
        closed_by_user = getattr(terminal, "_closed_by_user", False)
        is_ssh = terminal_info.get("type") in ["ssh", "sftp"]

        ssh_failed = (
            is_ssh
            and child_status != 0
            and not closed_by_user
            and not is_user_terminated
        )

        return {
            "decoded_exit_code": decoded_exit_code,
            "is_user_terminated": is_user_terminated,
            "closed_by_user": closed_by_user,
            "is_ssh": is_ssh,
            "ssh_failed": ssh_failed,
        }

    def _handle_ssh_failure(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        terminal_name: str,
        identifier: Union[str, SessionItem],
        child_status: int,
    ) -> None:
        """Handles SSH connection failure."""
        self.lifecycle_manager.transition_state(terminal_id, TerminalState.SPAWN_FAILED)
        self.logger.warning(
            f"SSH failed for '{terminal_name}' (status: {child_status})"
        )

        auto_reconnect_active = getattr(terminal, "_auto_reconnect_active", False)
        is_auth_error = self._check_ssh_auth_error(terminal, child_status)

        if is_auth_error and auto_reconnect_active:
            self.cancel_auto_reconnect(terminal)
            terminal.feed(
                b"\r\n\x1b[31m[Auth error - auto-reconnect stopped]\x1b[0m\r\n"
            )

        if auto_reconnect_active and not is_auth_error:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        else:
            GLib.idle_add(
                self._show_ssh_connection_error_dialog,
                terminal_name,
                identifier,
                terminal,
                terminal_id,
                child_status,
            )

    def _handle_normal_exit(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        terminal_name: str,
        identifier: Union[str, SessionItem],
        child_status: int,
        exit_info: dict,
    ) -> None:
        """Handles normal or user-initiated terminal exit."""
        if exit_info["is_ssh"] and self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        if exit_info["is_user_terminated"]:
            self.logger.info(
                f"Terminal '{terminal_name}' terminated by user signal "
                f"(exit code: {exit_info['decoded_exit_code']})"
            )

        if not self.lifecycle_manager.transition_state(
            terminal_id, TerminalState.EXITED
        ):
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        self.logger.info(f"Terminal '{terminal_name}' exited (status: {child_status})")
        log_terminal_event("exited", terminal_name, f"status {child_status}")
        GLib.idle_add(
            self._cleanup_terminal_ui,
            terminal,
            terminal_id,
            child_status,
            identifier,
        )

    def _on_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        """Handle terminal child process exit."""
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            return

        try:
            self._cleanup_connection_monitor(terminal)
            terminal._retry_in_progress = False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )
            self._cancel_pending_kill_timer(terminal_id)

            exit_info = self._analyze_exit_status(terminal, terminal_info, child_status)

            if exit_info["ssh_failed"]:
                self._handle_ssh_failure(
                    terminal, terminal_id, terminal_name, identifier, child_status
                )
            else:
                self._handle_normal_exit(
                    terminal,
                    terminal_id,
                    terminal_name,
                    identifier,
                    child_status,
                    exit_info,
                )

        except Exception as e:
            self.logger.error(f"Child exit handling failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _check_ssh_auth_error(self, terminal: Vte.Terminal, child_status: int) -> bool:
        """Check if SSH failure is due to authentication error."""
        import os as os_module

        # Decode exit code
        if os_module.WIFEXITED(child_status):
            exit_code = os_module.WEXITSTATUS(child_status)
        else:
            exit_code = child_status

        # Exit codes 5, 6 are common SSH auth failure codes
        if exit_code in (5, 6):
            return True

        # Check terminal text for auth patterns
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 20)
            result = terminal.get_text_range_format(
                0,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and len(result) > 0 and result[0]:
                text_lower = result[0].lower()
                auth_patterns = [
                    "permission denied",
                    "authentication failed",
                    "incorrect password",
                    "invalid password",
                    "too many authentication failures",
                ]
                for pattern in auth_patterns:
                    if pattern in text_lower:
                        return True
        except Exception:
            pass

        return False

    def _is_terminal_valid_for_error_dialog(
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int
    ) -> bool:
        """Checks if terminal is valid to show error dialog."""
        try:
            if terminal is None or not terminal.get_realized():
                self.logger.debug(
                    f"Skipping error dialog - terminal not realized for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return False
            if terminal.get_parent() is None:
                self.logger.debug(
                    f"Skipping error dialog - terminal orphaned for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return False
        except Exception as e:
            self.logger.debug(f"Terminal widget check failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return False
        return True

    def _should_skip_error_banner(
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int
    ) -> bool:
        """Determines if error banner should be skipped."""
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.debug(
                f"Skipping error banner - retry in progress for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return True

        if self.tab_manager and self.tab_manager.has_error_banner(terminal):
            self.logger.debug(
                f"Skipping error banner - banner already showing for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return True

        return False

    def _decode_exit_code(self, child_status: int) -> int:
        """Decodes the wait status to get actual exit code."""
        import os as os_module

        if os_module.WIFEXITED(child_status):
            return os_module.WEXITSTATUS(child_status)
        elif os_module.WIFSIGNALED(child_status):
            return 128 + os_module.WTERMSIG(child_status)
        return child_status

    def _extract_terminal_text(self, terminal: Vte.Terminal) -> Optional[str]:
        """Extracts recent text from terminal for error analysis."""
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 50)

            from gi.repository import Vte as VteLib

            result = terminal.get_text_range_format(
                VteLib.Format.TEXT,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and len(result) > 0 and result[0]:
                return result[0]
        except Exception as text_err:
            self.logger.debug(f"Could not extract terminal text: {text_err}")
        return None

    def _analyze_ssh_error(self, exit_code: int, terminal_text: Optional[str]) -> dict:
        """Analyzes SSH error and returns error info dict."""
        from ..ui.ssh_dialogs import get_error_info

        error_type, _, error_description = get_error_info(exit_code, terminal_text)

        auth_error_types = (
            "auth_failed",
            "auth_multi_failed",
            "key_rejected",
            "key_format_error",
            "key_permissions",
        )
        host_key_error_types = ("host_key_failed", "host_key_changed")

        return {
            "error_type": error_type,
            "error_description": error_description,
            "is_auth_error": error_type in auth_error_types,
            "is_host_key_error": error_type in host_key_error_types,
        }

    def _show_error_banner(
        self,
        terminal: Vte.Terminal,
        session_name: str,
        error_info: dict,
        session: Optional[SessionItem],
        terminal_id: int,
    ) -> None:
        """Shows the error banner in the tab manager."""
        if self.tab_manager:
            banner_shown = self.tab_manager.show_error_banner_for_terminal(
                terminal=terminal,
                session_name=session_name,
                error_message=error_info["error_description"],
                session=session,
                is_auth_error=error_info["is_auth_error"],
                is_host_key_error=error_info["is_host_key_error"],
            )

            if banner_shown:
                self.logger.info(
                    f"Showed inline error banner for '{session_name}' "
                    f"(auth_error={error_info['is_auth_error']})"
                )
            else:
                self.logger.warning(
                    f"Could not show inline banner for '{session_name}'"
                )

        self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _show_ssh_connection_error_dialog(
        self, session_name, identifier, terminal, terminal_id, child_status
    ):
        """
        Show SSH connection error using non-blocking inline banner.

        Uses an inline banner above the terminal instead of a modal dialog,
        allowing users to continue using other tabs while deciding how to handle
        the connection failure.

        Returns False for GLib.idle_add callback compatibility.
        """
        if not self._is_terminal_valid_for_error_dialog(
            terminal, session_name, terminal_id
        ):
            return False

        if self._should_skip_error_banner(terminal, session_name, terminal_id):
            return False

        try:
            exit_code = self._decode_exit_code(child_status)
            self.logger.debug(
                f"SSH error: raw status={child_status}, decoded exit_code={exit_code}"
            )

            terminal_text = self._extract_terminal_text(terminal)
            error_info = self._analyze_ssh_error(exit_code, terminal_text)
            session = identifier if isinstance(identifier, SessionItem) else None

            self._show_error_banner(
                terminal, session_name, error_info, session, terminal_id
            )

        except Exception as e:
            self.logger.error(f"Failed to show SSH error: {e}")
            import traceback

            self.logger.debug(traceback.format_exc())
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

        return False

    def _retry_ssh_connection_with_timeout(
        self, session: SessionItem, timeout: int
    ) -> bool:
        """
        Retry SSH connection with extended timeout.
        Creates a new tab (single retry mode).
        """
        try:
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            if self.tab_manager:
                self.tab_manager.create_ssh_tab(session)

            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retried SSH connection to '{session.name}' with {timeout}s timeout"
            )
        except Exception as e:
            self.logger.error(f"Failed to retry SSH connection: {e}")

        return False

    def start_auto_reconnect(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        duration_mins: int,
        interval_secs: int,
        timeout_secs: int,
    ) -> None:
        """
        Start automatic reconnection attempts for a failed SSH terminal.

        This keeps the same terminal tab and re-spawns SSH sessions in it.
        Progress is displayed inline in the terminal itself.
        """
        import time
        from datetime import datetime

        # Store auto-reconnect state on the terminal
        terminal._auto_reconnect_active = True
        terminal._auto_reconnect_cancelled = False
        terminal._auto_reconnect_timer_id = None

        end_time = time.time() + (duration_mins * 60)
        max_attempts = (duration_mins * 60) // interval_secs

        state = {
            "attempt": 0,
        }

        def get_timestamp() -> str:
            """Get current timestamp string."""
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def display_status(message: str, is_error: bool = False) -> None:
            """Display status message in the terminal with timestamp."""
            if not terminal.get_realized():
                return
            color = "\x1b[33m" if not is_error else "\x1b[31m"  # Yellow or Red
            reset = "\x1b[0m"
            dim = "\x1b[2m"
            timestamp = get_timestamp()
            terminal.feed(
                f"\r\n{dim}[{timestamp}]{reset} {color}[Auto-Reconnect] {message}{reset}\r\n".encode(
                    "utf-8"
                )
            )

        def show_connection_options() -> None:
            """Show connection error dialog with options when auto-reconnect exhausted."""
            terminal._auto_reconnect_active = False
            terminal._auto_reconnect_timer_id = None

            # Show the connection error dialog to give user options
            GLib.idle_add(
                self._show_ssh_connection_error_dialog,
                session.name,
                session,
                terminal,
                terminal_id,
                1,  # Non-zero status to indicate failure
            )

        def attempt_reconnect() -> bool:
            """Attempt a single reconnection.

            Returns False to stop GLib.timeout_add repetition (required by GTK).
            """
            # Clear timer reference since we're executing
            terminal._auto_reconnect_timer_id = None

            if getattr(terminal, "_auto_reconnect_cancelled", False):
                display_status(_("Cancelled by user."))
                terminal._auto_reconnect_active = False
                return False

            now = time.time()
            if now >= end_time:
                display_status(_("Time limit reached. Giving up."), is_error=True)
                display_status(_("Showing connection options..."))
                show_connection_options()
                return False

            state["attempt"] += 1
            remaining = int(end_time - now)
            remaining_mins = remaining // 60
            remaining_secs = remaining % 60

            display_status(
                _("Attempt {n}/{max} - Time remaining: {mins}m {secs}s").format(
                    n=state["attempt"],
                    max=max_attempts,
                    mins=remaining_mins,
                    secs=remaining_secs,
                )
            )

            # Re-spawn SSH in the same terminal
            try:
                original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
                self.settings_manager.set(
                    "ssh_connect_timeout", timeout_secs, save_immediately=False
                )

                # Re-spawn the SSH session in the existing terminal
                self._respawn_ssh_in_terminal(terminal, terminal_id, session)

                # Restore timeout
                GLib.timeout_add(
                    1000,
                    lambda: (
                        self.settings_manager.set(
                            "ssh_connect_timeout",
                            original_timeout,
                            save_immediately=False,
                        )
                        or False
                    ),
                )

            except Exception as e:
                self.logger.error(f"Auto-reconnect spawn error: {e}")
                display_status(
                    _("Spawn error: {error}").format(error=str(e)), is_error=True
                )

            # Schedule next attempt (the child-exited handler will check auto_reconnect state)
            if now + interval_secs < end_time:
                timer_id = GLib.timeout_add_seconds(interval_secs, attempt_reconnect)
                terminal._auto_reconnect_timer_id = timer_id
            else:
                display_status(_("Maximum attempts reached."), is_error=True)
                display_status(_("Showing connection options..."))
                show_connection_options()

            return False  # Don't repeat this call

        # Display initial message
        display_status(
            _(
                "Starting auto-reconnect: {attempts} attempts over {mins} minute(s), every {secs}s"
            ).format(
                attempts=max_attempts,
                mins=duration_mins,
                secs=interval_secs,
            )
        )
        display_status(_("Close this tab to cancel."))

        # Start first attempt after a short delay
        timer_id = GLib.timeout_add(500, attempt_reconnect)
        terminal._auto_reconnect_timer_id = timer_id

    def _respawn_ssh_in_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
    ) -> None:
        """
        Re-spawn an SSH session in an existing terminal.
        This is used for auto-reconnect to avoid creating new tabs.
        """
        try:
            # Update registry to show we're spawning
            self.registry.update_terminal_status(terminal_id, "spawning")

            # Check if we should use highlighted SSH
            highlight_manager = self._get_highlight_manager()
            output_highlighting_enabled = highlight_manager.enabled_for_ssh
            if session.output_highlighting is not None:
                output_highlighting_enabled = session.output_highlighting

            should_spawn_highlighted = output_highlighting_enabled

            user_data_for_spawn = (terminal_id, session)

            if should_spawn_highlighted:
                proxy = self.spawner.spawn_highlighted_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    terminal_id=terminal_id,
                )
                if proxy:
                    self._highlight_proxies[terminal_id] = proxy
                else:
                    # Fallback to standard
                    self.spawner.spawn_ssh_session(
                        terminal,
                        session,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                    )
            else:
                self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                )

            self.logger.info(f"Re-spawned SSH session in terminal {terminal_id}")

        except Exception as e:
            self.logger.error(f"Failed to re-spawn SSH: {e}")
            raise

    def _retry_ssh_in_same_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        timeout: int = 30,
    ) -> bool:
        """
        Retry SSH connection in the same terminal (single retry mode).

        Unlike auto-reconnect, this does a single retry with extended timeout
        and shows the connection attempt in the same terminal.

        Args:
            terminal: The terminal to retry in.
            terminal_id: Terminal ID.
            session: Session to connect.
            timeout: Connection timeout in seconds.

        Returns:
            True if retry was initiated, False otherwise.
        """
        # Prevent multiple simultaneous retries
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.warning(f"Retry already in progress for terminal {terminal_id}")
            return False

        try:
            # Mark retry in progress - will be cleared by _on_child_exited or _on_connection_success
            terminal._retry_in_progress = True

            # Display retry message
            terminal.feed(
                f"\r\n\x1b[33m[Retry] Attempting reconnection with {timeout}s timeout...\x1b[0m\r\n".encode(
                    "utf-8"
                )
            )

            # Set temporary timeout
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            # Re-spawn in the same terminal
            self._respawn_ssh_in_terminal(terminal, terminal_id, session)

            # Restore original timeout after a delay
            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retrying SSH connection to '{session.name}' with {timeout}s timeout in same terminal"
            )
            return True

        except Exception as e:
            terminal._retry_in_progress = False
            self.logger.error(f"Failed to retry SSH in same terminal: {e}")
            terminal.feed(f"\r\n\x1b[31m[Retry] Failed: {e}\x1b[0m\r\n".encode("utf-8"))
            return False

    def cancel_auto_reconnect(self, terminal: Vte.Terminal) -> None:
        """Cancel auto-reconnect for a terminal, including any pending timers."""
        terminal._auto_reconnect_cancelled = True
        terminal._auto_reconnect_active = False

        # Cancel pending timer if exists
        timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
        if timer_id is not None:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            terminal._auto_reconnect_timer_id = None

        self.logger.info(
            f"Auto-reconnect cancelled for terminal {getattr(terminal, 'terminal_id', 'N/A')}"
        )

    def is_auto_reconnect_active(self, terminal: Vte.Terminal) -> bool:
        """Check if auto-reconnect is active for a terminal."""
        return getattr(terminal, "_auto_reconnect_active", False)

    def _on_eof(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        self._on_child_exited(terminal, 0, identifier, terminal_id)

    def _cleanup_terminal_ui(
        self, terminal: Vte.Terminal, terminal_id: int, child_status: int, identifier
    ) -> bool:
        """Cleanup terminal UI after process exit.

        Returns False for GLib.idle_add callback compatibility.
        """
        # Safety check: Don't cleanup if auto-reconnect is active
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP_UI] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return False

        try:
            if self.terminal_exit_handler:
                self.terminal_exit_handler(terminal, child_status, identifier)
            if self.tab_manager:
                self.tab_manager._on_terminal_process_exited(
                    terminal, child_status, identifier
                )
            else:
                self._cleanup_terminal(terminal, terminal_id)
        except Exception as e:
            self.logger.error(f"Terminal UI cleanup failed: {e}")
        finally:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        return False

    def _cleanup_process_tracking(self, terminal_info: dict) -> None:
        """Cleans up process tracking for the terminal."""
        pid = terminal_info.get("process_id")
        if pid:
            self.spawner.process_tracker.unregister_process(pid)

    def _get_terminal_name_for_cleanup(self, terminal_info: dict) -> str:
        """Gets the terminal name for logging during cleanup."""
        identifier = terminal_info.get("identifier", "Unknown")
        if isinstance(identifier, str):
            return identifier
        return getattr(identifier, "name", "Unknown")

    def _cleanup_terminal_tracking(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Cleans up OSC7 and SSH tracking for the terminal."""
        self.osc7_tracker.untrack_terminal(terminal)
        self.manual_ssh_tracker.untrack(terminal_id)

    def _cleanup_terminal_handlers(self, terminal: Vte.Terminal) -> None:
        """Disconnects signal handlers and removes controllers."""
        if hasattr(terminal, "ashy_handler_ids"):
            for handler_id in terminal.ashy_handler_ids:
                if GObject.signal_handler_is_connected(terminal, handler_id):
                    terminal.disconnect(handler_id)
            terminal.ashy_handler_ids.clear()

        if hasattr(terminal, "ashy_controllers"):
            for controller in terminal.ashy_controllers:
                terminal.remove_controller(controller)
            terminal.ashy_controllers.clear()

    def _cleanup_terminal_attributes(self, terminal: Vte.Terminal) -> None:
        """Cleans up custom attributes from the terminal."""
        attrs_to_delete = ["_osc8_hovered_uri", "_closed_by_user"]
        for attr in attrs_to_delete:
            if hasattr(terminal, attr):
                try:
                    delattr(terminal, attr)
                except Exception as e:
                    self.logger.debug(f"Could not delete {attr} attr: {e}")

    def _finalize_terminal_cleanup(self, terminal_id: int, terminal_name: str) -> None:
        """Finalizes terminal cleanup by unregistering and updating stats."""
        if self.registry.unregister_terminal(terminal_id):
            self._stats["terminals_closed"] += 1
            log_terminal_event("removed", terminal_name, "terminal resources cleaned")

        if terminal_id in self._pending_kill_timers:
            GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return

        with self._cleanup_lock:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                return

            self._cleanup_highlight_proxy(terminal_id)
            self._cleanup_process_tracking(terminal_info)
            terminal_name = self._get_terminal_name_for_cleanup(terminal_info)

            self.logger.info(
                f"Cleaning up resources for terminal '{terminal_name}' (ID: {terminal_id})"
            )

            self._cleanup_terminal_tracking(terminal, terminal_id)
            self._cleanup_terminal_handlers(terminal)
            self._cleanup_terminal_attributes(terminal)
            self._finalize_terminal_cleanup(terminal_id, terminal_name)

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any,
    ) -> None:
        """
        Called when terminal spawn completes.

        For SSH: spawn success just means process started.
        We monitor process status to detect actual connection success.
        """
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            user_data_tuple = final_user_data.get("original_user_data")
            terminal_id, user_data = user_data_tuple

            if error:
                self.logger.error(
                    f"Spawn failed for terminal {terminal_id}: {error.message}"
                )
                self.registry.update_terminal_status(terminal_id, "spawn_failed")
                return

            self.registry.update_terminal_process(terminal_id, pid)

            # For retry/auto-reconnect: wait for process exit to determine success/failure
            # If process exits quickly (< 3s), it failed. If still running, it's connected.
            has_banner = self.tab_manager and self.tab_manager.has_error_banner(
                terminal
            )
            is_auto_reconnect = getattr(terminal, "_auto_reconnect_active", False)
            is_retry = getattr(terminal, "_retry_in_progress", False)

            if has_banner or is_auto_reconnect or is_retry:
                self._monitor_connection_status(terminal, terminal_id, pid)

            # Handle execute command - use a flag to ensure single execution
            if (
                isinstance(user_data, dict)
                and user_data.get("execute_command")
                and pid > 0
                and not getattr(terminal, "_startup_command_executed", False)
            ):
                terminal._startup_command_executed = True
                command_to_exec = user_data["execute_command"]
                close_after = user_data.get("close_after_execute", False)

                def exec_startup_command(
                    term=terminal, cmd=command_to_exec, close=close_after
                ):
                    self._execute_command_in_terminal(term, cmd, close)
                    return False  # Remove from timeout queue

                GLib.timeout_add(100, exec_startup_command)

        except Exception as e:
            self.logger.error(f"Spawn callback failed: {e}")

    def _is_process_alive(self, pid: int) -> bool:
        """Checks if a process is still running."""
        import os as os_module

        try:
            os_module.kill(pid, 0)
            return True
        except OSError:
            return False

    def _check_terminal_connection_status(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> Optional[bool]:
        """
        Checks terminal text for connection status.
        Returns True to continue checking, False to stop, None for timeout fallback.
        """
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 5)

            result = terminal.get_text_range_format(
                0,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and result[0]:
                recent_text = result[0].lower().strip()

                if "[auto-reconnect]" in recent_text:
                    if terminal._connect_check_count < 10:
                        return True

                has_recent_error = self._has_connection_error(recent_text)
                has_prompt = self._has_shell_prompt(recent_text)

                if has_prompt and not has_recent_error:
                    self.logger.info(f"SSH connected for terminal {terminal_id}")
                    self._on_connection_success(terminal)
                    return False

        except Exception:
            pass

        return None

    def _has_connection_error(self, text: str) -> bool:
        """Checks if text contains SSH connection error patterns."""
        error_patterns = [
            "no route to host",
            "connection refused",
            "connection timed out",
            "permission denied",
            "authentication failed",
            "host key verification failed",
            "broken pipe",
        ]
        return any(p in text for p in error_patterns)

    def _has_shell_prompt(self, text: str) -> bool:
        """Checks if text contains shell prompt indicators."""
        success_patterns = [
            "$",
            "#",
            "❯",
            "➜",
            "›",
            "last login:",
            "welcome to",
        ]
        return any(p in text for p in success_patterns)

    def _monitor_connection_status(
        self, terminal: Vte.Terminal, terminal_id: int, pid: int
    ) -> None:
        """
        Monitor SSH connection status after spawn.

        SSH connection is considered successful when:
        1. Process is still running after initial connect phase
        2. Terminal shows shell prompt in recent lines (not error messages)
        """
        terminal._monitoring_pid = pid
        terminal._connect_check_count = 0
        terminal._last_line_count = 0

        def check_connection():
            """Periodically check if SSH is truly connected."""
            if getattr(terminal, "_monitoring_pid", None) != pid:
                return False

            terminal._connect_check_count = (
                getattr(terminal, "_connect_check_count", 0) + 1
            )

            if not self._is_process_alive(pid):
                self._cleanup_connection_monitor(terminal)
                return False

            connection_result = self._check_terminal_connection_status(
                terminal, terminal_id
            )
            if connection_result is not None:
                return connection_result

            if terminal._connect_check_count < 10:
                return True

            self.logger.info(
                f"SSH appears connected for terminal {terminal_id} (timeout)"
            )
            self._on_connection_success(terminal)
            return False

        GLib.timeout_add(1000, check_connection)

    def _cleanup_connection_monitor(self, terminal: Vte.Terminal) -> None:
        """Clean up connection monitoring state."""
        for attr in ["_monitoring_pid", "_connect_check_count", "_last_line_count"]:
            if hasattr(terminal, attr):
                delattr(terminal, attr)

    def _on_connection_success(self, terminal: Vte.Terminal) -> None:
        """Handle successful SSH connection."""
        self._cleanup_connection_monitor(terminal)

        # Hide error banner
        if self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        # Stop auto-reconnect
        if getattr(terminal, "_auto_reconnect_active", False):
            terminal._auto_reconnect_active = False
            timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
            if timer_id:
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
                terminal._auto_reconnect_timer_id = None

        # Clear retry flag
        terminal._retry_in_progress = False

    def _execute_command_in_terminal(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool = False
    ) -> bool:
        """Execute a command in the terminal."""
        try:
            if not terminal or not command:
                return False
            command_to_run = f"({command}); exit" if close_after_execute else command
            terminal.feed_child(f"{command_to_run}\n".encode("utf-8"))
            return True
        except Exception as e:
            self.logger.error(f"Failed to execute command '{command}': {e}")
            return False

    def _ensure_process_terminated(
        self, pid: int, terminal_name: str, terminal_id: int
    ) -> bool:
        try:
            self._pending_kill_timers.pop(terminal_id, None)
            os.kill(pid, 0)
            self.logger.warning(
                f"Process {pid} ('{terminal_name}') did not exit gracefully. Sending SIGKILL."
            )
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            self.logger.error(f"Error during final check for PID {pid}: {e}")
        return False

    def remove_terminal(
        self, terminal: Vte.Terminal, force_kill_group: bool = False
    ) -> bool:
        # Cancel auto-reconnect FIRST before any other cleanup
        # This ensures we stop reconnection attempts immediately when closing
        if self.is_auto_reconnect_active(terminal):
            self.cancel_auto_reconnect(terminal)

        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False
            info = self.registry.get_terminal_info(terminal_id)
            if not info:
                return False

            identifier = info.get("identifier", "Unknown")

            # Mark terminal as closed by user
            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception:
                pass

            # If terminal already exited or spawn failed, just do UI cleanup
            if info.get("status") in [
                TerminalState.EXITED.value,
                TerminalState.SPAWN_FAILED.value,
            ]:
                # Need full UI cleanup to close the tab
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            pid = info.get("process_id")
            if not pid or pid == -1:
                # No process to kill, just do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            terminal_name = (
                identifier.name
                if isinstance(identifier, SessionItem)
                else str(identifier)
            )

            try:
                target_id = os.getpgid(pid) if force_kill_group else pid
                os.kill(target_id, signal.SIGHUP)
            except (ProcessLookupError, PermissionError) as e:
                self.logger.warning(
                    f"Could not send signal to PID {pid}, likely already exited: {e}"
                )
                # Process already exited, do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_terminated, pid, terminal_name, terminal_id
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            return True

    def has_active_ssh_sessions(self) -> bool:
        for info in self.registry._terminals.values():
            if info.get("type") == "ssh" and info.get("status") == "running":
                return True
        return False

    def reconnect_all_for_session(self, session_name: str) -> int:
        """
        Reconnect all disconnected terminals for a given session.

        Args:
            session_name: Name of the session to reconnect terminals for.

        Returns:
            Number of terminals where reconnection was initiated.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        reconnected = 0

        for terminal_id in terminal_ids:
            if self._try_reconnect_terminal(terminal_id, session_name):
                reconnected += 1

        return reconnected

    def _try_reconnect_terminal(self, terminal_id: int, session_name: str) -> bool:
        """Try to reconnect a single disconnected terminal.

        Returns True if reconnection was initiated.
        """
        info = self.registry.get_terminal_info(terminal_id)
        if not info or info.get("status") != "disconnected":
            return False

        session = info.get("identifier")
        if not isinstance(session, SessionItem):
            return False

        terminal = self.registry.get_terminal(terminal_id)
        if not terminal:
            return False

        try:
            self._respawn_ssh_in_terminal(terminal, terminal_id, session)
            self.logger.info(
                f"Initiated reconnection for terminal {terminal_id} "
                f"(session: {session_name})"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to reconnect terminal {terminal_id}: {e}")
            return False

    def disconnect_all_for_session(self, session_name: str) -> int:
        """
        Gracefully disconnect all terminals for a session.

        This cancels any auto-reconnect and sends exit command to SSH.

        Args:
            session_name: Name of the session to disconnect.

        Returns:
            Number of terminals disconnected.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        disconnected = 0

        for terminal_id in terminal_ids:
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                # Cancel any active auto-reconnect
                self.cancel_auto_reconnect(terminal)

                # Send exit command to terminate SSH session gracefully
                try:
                    terminal.feed_child(b"exit\n")
                    disconnected += 1
                    self.logger.info(
                        f"Sent disconnect to terminal {terminal_id} "
                        f"(session: {session_name})"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to disconnect terminal {terminal_id}: {e}"
                    )

        return disconnected

    def get_session_connection_status(self, session_name: str) -> Dict[str, Any]:
        """
        Get aggregated connection status for all terminals of a session.

        Args:
            session_name: Name of the session.

        Returns:
            Dictionary with connection status summary.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)

        status_counts = {
            "connected": 0,
            "disconnected": 0,
            "connecting": 0,
            "reconnecting": 0,
            "other": 0,
        }

        for terminal_id in terminal_ids:
            info = self.registry.get_terminal_info(terminal_id)
            if info:
                status = info.get("status", "unknown")
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts["other"] += 1

        total = len(terminal_ids)

        # Determine overall status
        if total == 0:
            overall = "no_terminals"
        elif status_counts["connected"] == total:
            overall = "all_connected"
        elif status_counts["disconnected"] == total:
            overall = "all_disconnected"
        elif status_counts["connected"] > 0:
            overall = "partial"
        elif status_counts["connecting"] > 0 or status_counts["reconnecting"] > 0:
            overall = "connecting"
        else:
            overall = "unknown"

        return {
            "total_terminals": total,
            "status_counts": status_counts,
            "overall_status": overall,
        }

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

    def _connect_hyperlink_handler(self, terminal: Vte.Terminal) -> None:
        """Connects the hyperlink hover handler to the terminal."""
        if not hasattr(terminal, "connect"):
            return

        handler_id = terminal.connect(
            "hyperlink-hover-uri-changed", self._on_hyperlink_hover_changed
        )
        if not hasattr(terminal, "ashy_handler_ids"):
            terminal.ashy_handler_ids = []
        terminal.ashy_handler_ids.append(handler_id)

    def _add_url_regex_patterns(self, terminal: Vte.Terminal) -> int:
        """Adds URL regex patterns to terminal. Returns count of patterns added."""
        if not hasattr(terminal, "match_add_regex") or not hasattr(Vte, "Regex"):
            return 0

        self.logger.debug("Using Vte.Regex for URL pattern matching")

        url_patterns = [
            r"https?://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
            r"ftp://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        ]

        vte_flags = 1024
        patterns_added = 0

        for pattern in url_patterns:
            if self._add_single_url_pattern(terminal, pattern, vte_flags):
                patterns_added += 1

        return patterns_added

    def _add_single_url_pattern(
        self, terminal: Vte.Terminal, pattern: str, vte_flags: int
    ) -> bool:
        """Adds a single URL pattern to terminal. Returns True on success."""
        try:
            regex = Vte.Regex.new_for_match(pattern, -1, vte_flags)
            if regex:
                tag = terminal.match_add_regex(regex, 0)
                if hasattr(terminal, "match_set_cursor_name"):
                    terminal.match_set_cursor_name(tag, "pointer")
                return True
        except Exception as e:
            self.logger.warning(f"Vte.Regex pattern '{pattern}' failed: {e}")
        return False

    def _setup_url_patterns(self, terminal: Vte.Terminal) -> None:
        try:
            terminal.set_allow_hyperlink(True)
            self._connect_hyperlink_handler(terminal)
            patterns_added = self._add_url_regex_patterns(terminal)

            if patterns_added > 0:
                self.logger.info(
                    f"URL pattern detection configured ({patterns_added} patterns)"
                )
            else:
                self.logger.error(
                    "Failed to configure URL patterns - URL clicking disabled"
                )

        except Exception as e:
            self.logger.error(f"Failed to setup URL patterns: {e}")

    def _on_hyperlink_hover_changed(self, terminal, uri, _bbox):
        try:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is not None:
                if uri:
                    terminal._osc8_hovered_uri = uri
                    self.logger.debug(
                        f"OSC8 hyperlink hovered in terminal {terminal_id}: {uri}"
                    )
                else:
                    if hasattr(terminal, "_osc8_hovered_uri"):
                        delattr(terminal, "_osc8_hovered_uri")
                    self.logger.debug(
                        f"OSC8 hyperlink hover cleared in terminal {terminal_id}"
                    )
        except Exception as e:
            self.logger.error(f"OSC8 hyperlink hover handling failed: {e}")

    def _on_terminal_clicked(self, gesture, _n_press, x, y, terminal, terminal_id):
        try:
            modifiers = gesture.get_current_event_state()
            ctrl_pressed = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)

            if ctrl_pressed:
                url_to_open = self._get_url_at_position(terminal, x, y)
                if url_to_open:
                    success = self._open_hyperlink(url_to_open)
                    if success:
                        self.logger.info(f"URL opened from Ctrl+click: {url_to_open}")
                        return Gdk.EVENT_STOP

            terminal.grab_focus()
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)

            return Gdk.EVENT_PROPAGATE

        except Exception as e:
            self.logger.error(
                f"Terminal click handling failed for terminal {terminal_id}: {e}"
            )
            return Gdk.EVENT_PROPAGATE

    def _on_terminal_key_pressed_for_detection(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> bool:
        """
        Handle key press events to detect command execution via screen scraping.

        This method intercepts the Enter key press (before VTE processes it),
        reads the current line from the terminal using VTE's text extraction,
        and analyzes it to detect the command being executed.

        This approach works reliably for SSH sessions and Docker containers
        where process sniffing is impossible.

        Args:
            controller: The key event controller.
            keyval: The key value (GDK key constant).
            keycode: The hardware keycode.
            state: Modifier state (Shift, Ctrl, etc.).
            terminal: The VTE terminal widget.
            terminal_id: The terminal's registry ID.

        Returns:
            Gdk.EVENT_PROPAGATE to allow VTE to process the key normally.
        """
        try:
            # Only trigger on Enter or KP_Enter, ignore if modifiers are pressed
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return Gdk.EVENT_PROPAGATE

            # Ignore if Shift, Ctrl, or Alt is pressed (might be a different action)
            if state & (
                Gdk.ModifierType.SHIFT_MASK
                | Gdk.ModifierType.CONTROL_MASK
                | Gdk.ModifierType.ALT_MASK
            ):
                return Gdk.EVENT_PROPAGATE

            # Get cursor position to read the current line
            _, row = terminal.get_cursor_position()

            # Extract the text of the current line using get_text_range_format
            # This is the modern VTE API that doesn't use deprecated callbacks
            # Signature: get_text_range_format(format, start_row, start_col, end_row, end_col)
            # Returns: tuple[Optional[str], int] - (text, length)
            try:
                # Get column count for the full line width
                col_count = terminal.get_column_count()

                # Use Vte.Format.TEXT for plain text extraction
                text_result = terminal.get_text_range_format(
                    Vte.Format.TEXT, row, 0, row, col_count
                )

                # get_text_range_format returns (text, length)
                if isinstance(text_result, tuple) and len(text_result) >= 1:
                    line_text = text_result[0] if text_result[0] else ""
                else:
                    line_text = ""

            except Exception as e:
                self.logger.debug(
                    f"get_text_range_format failed for terminal {terminal_id}: {e}"
                )
                return Gdk.EVENT_PROPAGATE

            # Strip whitespace and analyze the command
            line_text = line_text.strip() if line_text else ""
            if line_text:
                self._analyze_command_from_line(line_text, terminal, terminal_id)

        except Exception as e:
            self.logger.error(
                f"Key press detection failed for terminal {terminal_id}: {e}"
            )

        # CRUCIAL: Always propagate the event so VTE processes the newline
        return Gdk.EVENT_PROPAGATE

    # Shell keywords that should be skipped (not actual commands)
    _SHELL_KEYWORDS = frozenset(
        {
            "if",
            "then",
            "else",
            "elif",
            "fi",
            "for",
            "do",
            "done",
            "while",
            "until",
            "case",
            "esac",
            "select",
            "in",
            "function",
            "{",
            "}",
            "[[",
            "]]",
            "(",
            ")",
        }
    )

    # Prefix commands that should be skipped to find the real command
    _PREFIX_COMMANDS = frozenset(
        {
            "sudo",
            "time",
            "env",
            "nice",
            "nohup",
            "strace",
            "ltrace",
            "doas",
            "pkexec",
            "command",
            "builtin",
            "exec",
        }
    )

    # Glued keywords that can appear merged with commands
    _GLUED_KEYWORDS = frozenset(
        {
            "then",
            "else",
            "elif",
            "fi",
            "do",
            "done",
            "esac",
            "in",
        }
    )

    def _analyze_command_from_line(
        self, line: str, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """
        Analyze a terminal line to extract and set the command context.

        Uses a priority-based detection system:
        1. Ignored commands (tools with native coloring): set context and disable highlighting
        2. Known triggers (from HighlightManager): set context for command-specific rules
        3. Fallback: use first valid non-flag token
        """
        try:
            if not line:
                return

            clean_line = _ANSI_ESCAPE_PATTERN.sub("", line)
            command_part = self._extract_command_from_line(clean_line)

            if not command_part:
                return

            command_part = self._strip_glued_keywords(command_part)
            program_name = self._detect_program_name(command_part)

            if not program_name:
                return

            self._set_terminal_context(
                terminal_id, program_name, command_part, clean_line
            )

        except Exception as e:
            self.logger.error(
                f"Command analysis failed for terminal {terminal_id}: {e}"
            )

    def _extract_command_from_line(self, clean_line: str) -> str:
        """Extract command portion after the shell prompt."""
        matches = list(PROMPT_TERMINATOR_PATTERN.finditer(clean_line))
        if matches:
            last_match = matches[-1]
            return clean_line[last_match.end() :].strip()
        return clean_line.strip()

    def _strip_glued_keywords(self, command_part: str) -> str:
        """Remove glued shell keywords from command start."""
        command_lower = command_part.lower()
        for kw in self._GLUED_KEYWORDS:
            if command_lower.startswith(kw) and len(command_lower) > len(kw):
                if command_lower[len(kw)].isalpha():
                    return command_part[len(kw) :]
        return command_part

    def _detect_program_name(self, command_part: str) -> Optional[str]:
        """Detect the program name from command part."""
        last_command_part = self._get_last_pipeline_segment(command_part)
        tokens = last_command_part.split() if last_command_part else []

        from ..settings.manager import get_settings_manager

        settings_manager = get_settings_manager()
        highlight_manager = _get_highlight_manager()

        ignored_commands = set(settings_manager.get("ignored_highlight_commands", []))
        known_triggers = highlight_manager.get_all_triggers()

        return self._find_program_in_tokens(tokens, ignored_commands, known_triggers)

    def _get_last_pipeline_segment(self, command_part: str) -> str:
        """Get the last segment of a pipeline command."""
        parts = []
        current = []
        for char in command_part:
            if char in "|;&":
                if current:
                    parts.append("".join(current).strip())
                    current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current).strip())

        for part in reversed(parts):
            if part:
                return part
        return command_part

    def _find_program_in_tokens(
        self,
        tokens: list[str],
        ignored_commands: set[str],
        known_triggers: set[str],
    ) -> Optional[str]:
        """Find the program name from command tokens."""
        fallback = None

        for token in tokens:
            clean_token = self._clean_command_token(token)
            if not clean_token:
                continue

            clean_lower = clean_token.lower()

            if clean_lower in self._PREFIX_COMMANDS:
                continue
            if clean_lower in self._SHELL_KEYWORDS:
                continue

            # Handle concatenated keywords
            clean_token = self._extract_from_glued_keyword(clean_token, clean_lower)
            clean_lower = clean_token.lower()

            if clean_lower in self._SHELL_KEYWORDS:
                continue

            if clean_lower in ignored_commands or clean_lower in known_triggers:
                return clean_token

            if fallback is None:
                fallback = clean_token

        return fallback

    def _clean_command_token(self, token: str) -> str:
        """Clean a token by removing flags, paths, and dots."""
        if token.startswith("-"):
            return ""
        if "=" in token and "/" not in token:
            return ""

        clean = token
        if "/" in clean:
            clean = clean.split("/")[-1]
        return clean.lstrip(".")

    def _extract_from_glued_keyword(self, token: str, token_lower: str) -> str:
        """Extract command from keyword-glued token."""
        for kw in self._SHELL_KEYWORDS:
            if token_lower.startswith(kw) and len(token_lower) > len(kw):
                remainder = token[len(kw) :]
                if remainder and remainder[0].isalpha():
                    return remainder
        return token

    def _set_terminal_context(
        self,
        terminal_id: int,
        program_name: str,
        command_part: str,
        clean_line: str,
    ) -> None:
        """Set the syntax highlighting context for the terminal."""
        tokens = command_part.lower().split()
        is_help = self._is_help_command(tokens)

        highlighter = _get_output_highlighter()
        if is_help:
            highlighter.set_context("help", terminal_id, full_command=command_part)
            self.logger.debug(
                f"Terminal {terminal_id}: help context for: {clean_line[:50]}..."
            )
        else:
            highlighter.set_context(
                program_name, terminal_id, full_command=command_part
            )
            self.logger.debug(
                f"Terminal {terminal_id}: detected '{program_name}' from: {clean_line[:50]}..."
            )

    def _is_help_command(self, tokens: list[str]) -> bool:
        """Check if command is a help command."""
        if "--help" in tokens or "-h" in tokens:
            return True
        return bool(tokens and tokens[0] in ("help", "man"))

    def _get_osc8_hovered_uri(self, terminal: Vte.Terminal) -> Optional[str]:
        """Gets OSC8 hovered URI from terminal if available."""
        if hasattr(terminal, "_osc8_hovered_uri") and terminal._osc8_hovered_uri:
            return terminal._osc8_hovered_uri
        return None

    def _get_hyperlink_hover_uri(self, terminal: Vte.Terminal) -> Optional[str]:
        """Gets VTE hyperlink hover URI from terminal if available."""
        if not hasattr(terminal, "get_hyperlink_hover_uri"):
            return None
        try:
            hover_uri = terminal.get_hyperlink_hover_uri()
            if hover_uri:
                return hover_uri
        except Exception as e:
            self.logger.debug(f"VTE hyperlink detection failed: {e}")
        return None

    def _get_url_from_regex_match(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> Optional[str]:
        """Gets URL at position using regex match check."""
        if not hasattr(terminal, "match_check"):
            return None

        try:
            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()

            if char_width <= 0 or char_height <= 0:
                return None

            col = int(x / char_width)
            row = int(y / char_height)
            match_result = terminal.match_check(col, row)

            if match_result and len(match_result) >= 2:
                matched_text = match_result[0]
                if matched_text and is_valid_url(matched_text):
                    return matched_text
        except Exception as e:
            self.logger.debug(f"Regex match check failed: {e}")

        return None

    def _get_url_at_position(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> Optional[str]:
        try:
            url = self._get_osc8_hovered_uri(terminal)
            if url:
                return url

            url = self._get_hyperlink_hover_uri(terminal)
            if url:
                return url

            return self._get_url_from_regex_match(terminal, x, y)

        except Exception as e:
            self.logger.error(f"URL detection at position failed: {e}")
            return None

    def _open_hyperlink(self, uri: str) -> bool:
        try:
            if not uri or not uri.strip():
                self.logger.warning("Empty or invalid URI provided")
                return False

            uri = uri.strip()

            # Check if it looks like an email without mailto: prefix
            if (
                "@" in uri
                and not uri.startswith(("http://", "https://", "ftp://", "mailto:"))
                and "." in uri.split("@")[-1]
            ):
                uri = f"mailto:{uri}"

            try:
                parsed = urlparse(uri)
                if not parsed.scheme:
                    self.logger.warning(f"URI missing scheme: {uri}")
                    return False
            except Exception as e:
                self.logger.warning(f"Invalid URI format: {uri} - {e}")
                return False

            self.logger.info(f"Opening hyperlink: {uri}")

            subprocess.run(["xdg-open", uri], check=True, timeout=10)
            return True

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout opening hyperlink: {uri}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to open hyperlink '{uri}': {e}")
            return False
