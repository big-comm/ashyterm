# ashyterm/terminal/manager.py

import os
import signal
import threading
import time
import weakref
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gdk, Gio, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..settings.config import VTE_AVAILABLE
from ..settings.manager import SettingsManager
from ..ui.menus import create_terminal_menu
from ..ui.ssh_dialogs import create_generic_ssh_error_dialog
from ..utils.exceptions import (
    TerminalCreationError,
    TerminalSpawnError,
    VTENotAvailableError,
)

# Import new utility systems
from ..utils.logger import get_logger, log_terminal_event
from ..utils.osc7_tracker import OSC7Info, get_osc7_tracker
from ..utils.platform import get_environment_manager, get_platform_info, is_windows
from ..utils.security import validate_session_data
from .spawner import get_spawner


class TerminalState(Enum):
    """Terminal lifecycle states for proper management."""

    INITIALIZING = "initializing"
    RUNNING = "running"
    FOCUSED = "focused"
    UNFOCUSED = "unfocused"
    EXITING = "exiting"
    EXITED = "exited"
    SPAWN_FAILED = "spawn_failed"


class TerminalLifecycleManager:
    """Manages terminal lifecycle with state tracking and proper cleanup."""

    def __init__(self, registry, logger):
        self.registry = registry
        self.logger = logger
        self._closing_terminals = set()
        self._lock = threading.RLock()

    def is_terminal_closing(self, terminal_id: int) -> bool:
        """Check if a terminal is in the closing process."""
        with self._lock:
            return terminal_id in self._closing_terminals

    def mark_terminal_closing(self, terminal_id: int) -> bool:
        """Mark a terminal as closing. Returns False if already closing."""
        with self._lock:
            if terminal_id in self._closing_terminals:
                return False
            self._closing_terminals.add(terminal_id)
            return True

    def unmark_terminal_closing(self, terminal_id: int) -> None:
        """Remove terminal from closing set."""
        with self._lock:
            self._closing_terminals.discard(terminal_id)

    def transition_state(self, terminal_id: int, new_state: TerminalState) -> bool:
        """Transition terminal to new state with validation."""
        with self._lock:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                return False

            current_state = terminal_info.get("status", "")

            # Validate state transitions
            if new_state == TerminalState.EXITED and current_state.startswith("exited"):
                # Already exited, don't allow re-transition
                return False

            self.registry.update_terminal_status(terminal_id, new_state.value)
            return True


class ManualSSHTracker:
    """Tracks manually initiated SSH sessions and their targets."""

    def __init__(self, registry, on_state_changed_callback):
        self.logger = get_logger("ashyterm.terminal.ssh_tracker")
        self.registry = registry
        self.on_state_changed = on_state_changed_callback
        self._tracked_terminals = {}
        self._lock = threading.Lock()

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

    def get_ssh_target(self, terminal_id: int) -> Optional[str]:
        """Get the SSH target for a terminal, or None if not in SSH session."""
        with self._lock:
            state = self._tracked_terminals.get(terminal_id)
            if state and state.get("in_ssh"):
                return state.get("ssh_target")
            return None

    def check_process_tree(self, terminal_id: int):
        if not PSUTIL_AVAILABLE:
            return

        with self._lock:
            if terminal_id not in self._tracked_terminals:
                return

            state = self._tracked_terminals[terminal_id]
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info or terminal_info.get("type") != "local":
                return

            pid = terminal_info.get("process_id")
            if not pid:
                return

            try:
                parent_proc = psutil.Process(pid)
                children = parent_proc.children(recursive=True)
                ssh_proc = next(
                    (p for p in children if p.name().lower() == "ssh"), None
                )
                currently_in_ssh = ssh_proc is not None

                if currently_in_ssh != state["in_ssh"]:
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
                        self.logger.info(
                            f"Manual SSH session ended in terminal {terminal_id}"
                        )
                        state["in_ssh"] = False
                        state["ssh_target"] = None

                    # Notify that the state has changed
                    terminal = state["terminal_ref"]()
                    if terminal and self.on_state_changed:
                        GLib.idle_add(self.on_state_changed, terminal)

            except psutil.NoSuchProcess:
                if state["in_ssh"]:
                    state["in_ssh"] = False
                    state["ssh_target"] = None
                    terminal = state["terminal_ref"]()
                    if terminal and self.on_state_changed:
                        GLib.idle_add(self.on_state_changed, terminal)
            except Exception as e:
                self.logger.debug(
                    f"Error checking process tree for terminal {terminal_id}: {e}"
                )


class TerminalRegistry:
    """Registry for tracking terminal instances and their metadata."""

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

            self.logger.debug(
                f"Terminal registered: ID={terminal_id}, type={terminal_type}"
            )
            return terminal_id

    def get_active_terminal_count(self) -> int:
        """Counts terminals that are considered active (not exited or failed)."""
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
                self.logger.debug(
                    f"Terminal {terminal_id} process updated: PID={process_id}"
                )

    def update_terminal_status(self, terminal_id: int, status: str) -> None:
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]["status"] = status
                self.logger.debug(f"Terminal {terminal_id} status updated: {status}")

    def get_terminal(self, terminal_id: int) -> Optional[Vte.Terminal]:
        with self._lock:
            ref = self._terminal_refs.get(terminal_id)
            if ref:
                return ref()
            return None

    def get_terminal_info(self, terminal_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._terminals.get(terminal_id, {}).copy()

    def unregister_terminal(self, terminal_id: int) -> bool:
        with self._lock:
            if terminal_id in self._terminals:
                del self._terminals[terminal_id]
                if terminal_id in self._terminal_refs:
                    del self._terminal_refs[terminal_id]
                self.logger.debug(f"Terminal unregistered: ID={terminal_id}")
                return True
            return False

    def _cleanup_terminal_ref(self, terminal_id: int) -> None:
        with self._lock:
            if terminal_id in self._terminal_refs:
                del self._terminal_refs[terminal_id]
                self.logger.debug(f"Terminal reference cleaned up: ID={terminal_id}")

    def get_all_terminal_ids(self) -> List[int]:
        with self._lock:
            return list(self._terminals.keys())

    def get_terminal_count(self) -> int:
        with self._lock:
            return len(self._terminals)


class TerminalManager:
    """Enhanced terminal manager with comprehensive functionality."""

    def __init__(self, parent_window, settings_manager: SettingsManager):
        self.logger = get_logger("ashyterm.terminal.manager")
        self.parent_window = parent_window
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()
        self.environment_manager = get_environment_manager()

        self.registry = TerminalRegistry()
        self.spawner = get_spawner()
        self.lifecycle_manager = TerminalLifecycleManager(self.registry, self.logger)

        self.osc7_tracker = get_osc7_tracker(settings_manager)
        self.manual_ssh_tracker = ManualSSHTracker(
            self.registry, self._on_manual_ssh_state_changed
        )

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pending_kill_timers: Dict[int, int] = {}

        self.security_auditor = None
        self.tab_manager = None  # Will be set by CommTerminalWindow

        self.on_terminal_focus_changed: Optional[Callable] = None
        self.on_terminal_directory_changed: Optional[Callable] = None
        self.terminal_exit_handler: Optional[Callable] = (
            None  # Handler for terminal exits
        )

        self._stats = {
            "terminals_created": 0,
            "terminals_failed": 0,
            "terminals_closed": 0,
        }

        self._process_check_timer_id = GLib.timeout_add_seconds(
            2, self._periodic_process_check
        )

        self.logger.info("Terminal manager initialized")

    def set_tab_manager(self, tab_manager):
        """Sets the TabManager instance to resolve circular dependency."""
        self.tab_manager = tab_manager

    def set_terminal_exit_handler(self, handler: Callable):
        """Set the handler for terminal exit events."""
        self.terminal_exit_handler = handler
        self.logger.debug("Terminal exit handler set")

    def _periodic_process_check(self) -> bool:
        """Periodically check the process tree for all local terminals."""
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            for terminal_id in terminal_ids:
                self.manual_ssh_tracker.check_process_tree(terminal_id)
        except Exception as e:
            self.logger.error(f"Periodic process check failed: {e}")
        return True

    def _on_manual_ssh_state_changed(self, terminal: Vte.Terminal):
        """Callback from tracker when SSH state changes. Forces a title update."""
        self.logger.debug(
            f"Manual SSH state changed for terminal {getattr(terminal, 'terminal_id', 'N/A')}, forcing title update."
        )
        self._update_title(terminal)
        return False

    def _resolve_working_directory(
        self, working_directory: Optional[str]
    ) -> Optional[str]:
        """
        Resolve and validate working directory.

        Args:
            working_directory: Working directory path to resolve

        Returns:
            Resolved path or None if invalid
        """
        if not working_directory:
            return None

        try:
            import os
            from pathlib import Path

            # Expand user home and environment variables
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))

            # Convert to absolute path
            resolved_path = os.path.abspath(expanded_path)

            # Validate directory exists and is accessible
            path_obj = Path(resolved_path)
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

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, param_spec):
        """Handles the VTE notify::current-directory-uri signal, which is much more efficient."""
        try:
            # This check is important to avoid unnecessary processing
            if not self.settings_manager.get("osc7_enabled", True):
                return

            uri = terminal.get_current_directory_uri()
            if not uri:
                return

            # The rest of the parsing and title update logic
            from urllib.parse import unquote, urlparse

            parsed_uri = urlparse(uri)
            if parsed_uri.scheme != "file":
                return

            path = unquote(parsed_uri.path)
            hostname = parsed_uri.hostname or "localhost"

            display_path = self.osc7_tracker.parser._create_display_path(path)
            osc7_info = OSC7Info(
                hostname=hostname, path=path, display_path=display_path
            )

            # Calls the unified title update logic
            self._update_title(terminal, osc7_info)

        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    def _update_title(
        self, terminal: Vte.Terminal, osc7_info: Optional[OSC7Info] = None
    ):
        """Unified logic to determine and set the correct tab title."""
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return

        terminal_info = self.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            return

        new_title = "Terminal"

        if terminal_info.get("type") == "ssh":
            session = terminal_info.get("identifier")
            if isinstance(session, SessionItem) and osc7_info:
                new_title = f"{session.name}:{osc7_info.display_path}"
            elif isinstance(session, SessionItem):
                new_title = session.name

        elif terminal_info.get("type") == "local":
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)

            if ssh_target:
                if osc7_info:
                    new_title = f"{ssh_target}:{osc7_info.display_path}"
                else:
                    new_title = ssh_target
            elif osc7_info:
                new_title = osc7_info.display_path
            else:
                new_title = terminal_info.get("identifier", "Local")

        if self.on_terminal_directory_changed:
            self.on_terminal_directory_changed(terminal, new_title, osc7_info)

    def create_local_terminal(
        self,
        title: str = "Local Terminal",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Optional[Vte.Terminal]:
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for local terminal creation")
                raise VTENotAvailableError()

            try:
                if working_directory:
                    self.logger.debug(
                        f"Creating local terminal: '{title}' with working directory: '{working_directory}'"
                    )
                else:
                    self.logger.debug(
                        f"Creating local terminal: '{title}' with default working directory"
                    )

                if execute_command:
                    self.logger.debug(
                        f"Creating local terminal: '{title}' with execute command: '{execute_command}'"
                    )

                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError(
                        "base terminal creation failed", "local"
                    )

                terminal_id = self.registry.register_terminal(terminal, "local", title)

                self._setup_terminal_events(terminal, title, terminal_id)

                # Validate and resolve working directory before passing to spawner
                resolved_working_dir = self._resolve_working_directory(
                    working_directory
                )
                if working_directory and not resolved_working_dir:
                    self.logger.warning(
                        f"Invalid working directory '{working_directory}' for terminal '{title}', using default"
                    )

                success = self.spawner.spawn_local_terminal(
                    terminal,
                    callback=lambda t, pid, error, data: self._on_spawn_callback(
                        t,
                        pid,
                        error,
                        data,
                        terminal_id,
                        execute_command,
                        close_after_execute,
                    ),
                    user_data=title,
                    working_directory=resolved_working_dir,
                )

                if success:
                    self.logger.info(
                        f"Local terminal created successfully: '{title}' (ID: {terminal_id})"
                    )
                    log_terminal_event("created", title, "local terminal")
                    self._stats["terminals_created"] += 1
                    return terminal
                else:
                    self.registry.unregister_terminal(terminal_id)
                    self._stats["terminals_failed"] += 1
                    raise TerminalSpawnError("shell", "Local process spawn failed")

            except Exception as e:
                self._stats["terminals_failed"] += 1
                self.logger.error(f"Failed to create local terminal '{title}': {e}")

                if isinstance(
                    e, (TerminalCreationError, TerminalSpawnError, VTENotAvailableError)
                ):
                    raise
                else:
                    raise TerminalCreationError(str(e), "local")

    def create_ssh_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for SSH terminal creation")
                raise VTENotAvailableError()

            try:
                self.logger.debug(
                    f"Creating SSH terminal for session: '{session.name}'"
                )

                session_data = session.to_dict()
                is_valid, errors = validate_session_data(session_data)
                if not is_valid:
                    error_msg = f"Session validation failed: {', '.join(errors)}"
                    raise TerminalCreationError(error_msg, "ssh")

                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError("base terminal creation failed", "ssh")

                terminal_id = self.registry.register_terminal(terminal, "ssh", session)

                self._setup_terminal_events(terminal, session, terminal_id)

                success = self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    lambda t, pid, error, data: self._on_spawn_callback(
                        t, pid, error, data, terminal_id
                    ),
                    session,
                )

                if success:
                    self.logger.info(
                        f"SSH terminal created successfully: '{session.name}' (ID: {terminal_id})"
                    )
                    log_terminal_event(
                        "created",
                        session.name,
                        f"SSH to {session.get_connection_string()}",
                    )
                    self._stats["terminals_created"] += 1
                    return terminal
                else:
                    self.registry.unregister_terminal(terminal_id)
                    self._stats["terminals_failed"] += 1
                    raise TerminalSpawnError(
                        f"ssh://{session.get_connection_string()}",
                        "SSH process spawn failed",
                    )

            except Exception as e:
                self._stats["terminals_failed"] += 1
                self.logger.error(
                    f"Failed to create SSH terminal for '{session.name}': {e}"
                )

                if isinstance(
                    e, (TerminalCreationError, TerminalSpawnError, VTENotAvailableError)
                ):
                    raise
                else:
                    raise TerminalCreationError(str(e), "ssh")

    def create_sftp_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        """Creates a new terminal and starts an SFTP session in it."""
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for SFTP terminal creation")
                raise VTENotAvailableError()

            try:
                self.logger.debug(
                    f"Creating SFTP terminal for session: '{session.name}'"
                )

                # Validate the session
                session_data = session.to_dict()
                is_valid, errors = validate_session_data(session_data)
                if not is_valid:
                    raise TerminalCreationError(
                        f"Session validation failed: {', '.join(errors)}", "sftp"
                    )

                # Create the base terminal widget
                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError("base terminal creation failed", "sftp")

                # Register the terminal
                terminal_id = self.registry.register_terminal(terminal, "sftp", session)
                self._setup_terminal_events(terminal, session, terminal_id)

                # Set up the specific Drag-and-Drop for SFTP
                self._setup_sftp_drag_and_drop(terminal)

                # Spawn the SFTP process
                success = self.spawner.spawn_sftp_session(
                    terminal,
                    session,
                    lambda t, pid, error, data: self._on_spawn_callback(
                        t, pid, error, data, terminal_id
                    ),
                    session,
                )

                if success:
                    self.logger.info(
                        f"SFTP terminal created successfully: '{session.name}' (ID: {terminal_id})"
                    )
                    self._stats["terminals_created"] += 1
                    return terminal
                else:
                    self.registry.unregister_terminal(terminal_id)
                    self._stats["terminals_failed"] += 1
                    raise TerminalSpawnError(
                        f"sftp://{session.get_connection_string()}",
                        "SFTP process spawn failed",
                    )

            except Exception as e:
                self._stats["terminals_failed"] += 1
                self.logger.error(
                    f"Failed to create SFTP terminal for '{session.name}': {e}"
                )
                if isinstance(
                    e, (TerminalCreationError, TerminalSpawnError, VTENotAvailableError)
                ):
                    raise
                else:
                    raise TerminalCreationError(str(e), "sftp")

    def _create_base_terminal(self) -> Optional[Vte.Terminal]:
        try:
            self.logger.debug("Creating base VTE terminal widget")
            terminal = Vte.Terminal()

            terminal.set_vexpand(True)
            terminal.set_hexpand(True)
            terminal.set_mouse_autohide(True)
            terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            terminal.set_scroll_on_output(True)
            terminal.set_scroll_on_keystroke(True)
            terminal.set_scroll_unit_is_pixels(True)

            if self.platform_info.is_windows():
                terminal.set_encoding("utf-8")

            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)

            self._setup_context_menu(terminal)

            self.logger.debug("Base terminal created and configured")
            return terminal

        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None

    def _setup_sftp_drag_and_drop(self, terminal: Vte.Terminal) -> None:
        """Sets up the drag-and-drop target for an SFTP terminal."""
        try:
            self.logger.debug(
                f"Setting up SFTP drag-and-drop for terminal {getattr(terminal, 'terminal_id', 'N/A')}"
            )

            # Create a DropTarget that accepts files (Gio.File).
            drop_target = Gtk.DropTarget.new(type=Gio.File, actions=Gdk.DragAction.COPY)

            # Connect the "drop" signal to our callback.
            drop_target.connect("drop", self._on_file_drop)

            # Add the drop controller to the terminal widget.
            terminal.add_controller(drop_target)

            self.logger.debug("SFTP drag-and-drop configured.")
        except Exception as e:
            self.logger.error(f"Failed to setup SFTP drag-and-drop: {e}")

    def _on_file_drop(self, drop_target, value, x, y) -> bool:
        """Callback called when a file or folder is dropped on an SFTP terminal."""
        try:
            terminal = drop_target.get_widget()
            file = value  # The value is a Gio.File

            local_path = file.get_path()
            if not local_path:
                return False

            # Check if dropped item is a directory
            try:
                # Query the file type to differentiate between files and directories.
                file_info = file.query_info(
                    "standard::type", Gio.FileQueryInfoFlags.NONE, None
                )
                file_type = file_info.get_file_type()
            except GLib.Error as e:
                self.logger.error(f"Could not query file info for dropped item: {e}")
                # Fallback to assuming it's a file if we can't get info
                file_type = Gio.FileType.REGULAR

            # Use 'put -r' for directories and 'put' for regular files.
            if file_type == Gio.FileType.DIRECTORY:
                self.logger.info(f"Directory dropped on SFTP terminal: {local_path}")
                # The '-r' flag is for recursive upload.
                command_to_send = f'put -r "{local_path}"\n'
            else:
                self.logger.info(f"File dropped on SFTP terminal: {local_path}")
                command_to_send = f'put "{local_path}"\n'

            self.logger.debug(
                f"Sending command to SFTP child: {command_to_send.strip()}"
            )

            # Send the command to the sftp process running in the terminal.
            terminal.feed_child(command_to_send.encode("utf-8"))

            return True  # Indicate that the drop was handled successfully.
        except Exception as e:
            self.logger.error(f"Error handling file drop: {e}")
            return False

    def _setup_terminal_events(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        try:
            terminal.connect(
                "child-exited", self._on_child_exited, identifier, terminal_id
            )
            terminal.connect("eof", self._on_eof, identifier, terminal_id)

            # OSC7 directory tracking (Your existing correct code)
            terminal.connect(
                "notify::current-directory-uri", self._on_directory_uri_changed
            )

            self.manual_ssh_tracker.track(terminal_id, terminal)

            # Setup VTE native hyperlink support
            self._setup_native_hyperlinks(terminal, terminal_id)

            # Focus controllers
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect(
                "enter", self._on_terminal_focus_in, terminal, terminal_id
            )
            focus_controller.connect(
                "leave", self._on_terminal_focus_out, terminal, terminal_id
            )
            terminal.add_controller(focus_controller)

            # Click controller for focus and hyperlink handling
            click_controller = Gtk.GestureClick()
            click_controller.set_button(1)  # Left button only
            click_controller.connect(
                "pressed", self._on_terminal_clicked, terminal, terminal_id
            )
            terminal.add_controller(click_controller)

            terminal.terminal_id = terminal_id

            self.logger.debug(f"Terminal events configured for ID: {terminal_id}")

        except Exception as e:
            self.logger.error(
                f"Failed to configure terminal events for ID {terminal_id}: {e}"
            )

    def _setup_context_menu(self, terminal: Vte.Terminal) -> None:
        try:
            menu_model = create_terminal_menu(terminal)
            if hasattr(terminal, "set_context_menu_model"):
                terminal.set_context_menu_model(menu_model)
            else:
                right_click = Gtk.GestureClick()
                right_click.set_button(Gdk.BUTTON_SECONDARY)
                right_click.connect(
                    "pressed", self._on_terminal_right_click_pressed, terminal
                )
                terminal.add_controller(right_click)
        except Exception as e:
            self.logger.error(f"Context menu setup failed: {e}")

    def _on_terminal_right_click_pressed(self, gesture, n_press, x, y, terminal):
        try:
            self.logger.debug(f"Right click at ({x}, {y})")

            # Create menu with click coordinates for URL detection
            menu_model = create_terminal_menu(terminal, int(x), int(y))
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(terminal)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()
        except Exception as e:
            self.logger.error(f"Terminal right click handling failed: {e}")

    def _on_terminal_clicked(self, gesture, n_press, x, y, terminal, terminal_id):
        """Handle terminal clicks for focus and hyperlink opening."""
        try:
            # Check for hovered hyperlink first
            if hasattr(terminal, "_hovered_hyperlink") and terminal._hovered_hyperlink:
                hyperlink_uri = terminal._hovered_hyperlink
                success = self._open_hyperlink(hyperlink_uri)
                if success:
                    self.logger.info(
                        f"Hyperlink opened from terminal {terminal_id}: {hyperlink_uri}"
                    )
                    return Gdk.EVENT_STOP

            # Try VTE's match detection at click position using coordinates
            if hasattr(terminal, "match_check"):
                try:
                    # Convert click coordinates to character column/row
                    char_width = terminal.get_char_width()
                    char_height = terminal.get_char_height()

                    if char_width > 0 and char_height > 0:
                        col = int(x / char_width)
                        row = int(y / char_height)

                        # Try match_check with coordinates
                        match_result = terminal.match_check(col, row)

                        if match_result and len(match_result) >= 2:
                            matched_text = match_result[0]
                            tag = match_result[1]

                            self.logger.debug(
                                f"Match found at ({col}, {row}): '{matched_text}' (tag: {tag})"
                            )

                            if matched_text and self._is_valid_url(matched_text):
                                success = self._open_hyperlink(matched_text)
                                if success:
                                    self.logger.info(
                                        f"Matched URL opened: {matched_text}"
                                    )
                                    return Gdk.EVENT_STOP

                except Exception as e:
                    self.logger.debug(f"Match check failed: {e}")

            # Normal focus handling
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

    def _on_terminal_focus_in(self, controller, terminal, terminal_id):
        try:
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
        except Exception as e:
            self.logger.error(f"Terminal focus in handling failed: {e}")

    def _on_terminal_focus_out(self, controller, terminal, terminal_id):
        try:
            self.registry.update_terminal_status(terminal_id, "unfocused")
        except Exception as e:
            self.logger.error(f"Terminal focus out handling failed: {e}")

    def _on_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        """Handles both child-exited and eof signals with proper lifecycle management."""
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            self.logger.debug(
                f"Terminal {terminal_id} already being processed for exit"
            )
            return

        try:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )

            # Cancel any pending SIGKILL timers
            if terminal_id in self._pending_kill_timers:
                timeout_id = self._pending_kill_timers.pop(terminal_id)
                GLib.source_remove(timeout_id)
                self.logger.debug(f"SIGKILL timer cancelled for terminal {terminal_id}")

            # If this terminal was closed by user action, don't show connection error dialogs
            closed_by_user = getattr(terminal, "_closed_by_user", False)

            if (
                terminal_info.get("type") in ["ssh", "sftp"]
                and child_status != 0
                and not closed_by_user
            ):
                # Handle SSH connection failures by showing a dialog first.
                # The UI cleanup will be triggered *after* the dialog is closed.
                self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.SPAWN_FAILED
                )
                self.logger.warning(
                    f"SSH/SFTP connection for '{terminal_name}' (ID: {terminal_id}) failed with status: {child_status}"
                )
                # Schedule the dialog to run in the next idle cycle to ensure the terminal has rendered the error.
                GLib.idle_add(
                    self._show_ssh_connection_error_dialog,
                    terminal_name,
                    identifier,
                    terminal,
                    terminal_id,
                    child_status,
                )
            else:
                # Handle normal exit: schedule UI cleanup immediately.
                if not self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.EXITED
                ):
                    self.logger.debug(f"Terminal {terminal_id} already in exited state")
                    self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                    return

                self.logger.info(
                    f"Terminal '{terminal_name}' (ID: {terminal_id}) process exited with status: {child_status}"
                )
                log_terminal_event("exited", terminal_name, f"status {child_status}")
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    child_status,
                    identifier,
                )

        except Exception as e:
            self.logger.error(f"Terminal child exit handling failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _show_ssh_connection_error_dialog(
        self, session_name, identifier, terminal, terminal_id, child_status
    ):
        """
        Helper to show the SSH error dialog and connect its closing to the UI cleanup.
        """
        try:
            connection_string = ""
            if isinstance(identifier, SessionItem):
                connection_string = identifier.get_connection_string()

            dialog = create_generic_ssh_error_dialog(
                self.parent_window, session_name, connection_string
            )

            # Define the callback for when the dialog is closed
            def on_dialog_response(dlg, response_id):
                self.logger.debug(
                    f"SSH error dialog closed for terminal {terminal_id}. Triggering UI cleanup."
                )
                # Now, schedule the UI cleanup
                self._cleanup_terminal_ui(
                    terminal, terminal_id, child_status, identifier
                )
                dlg.destroy()

            dialog.connect("response", on_dialog_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show SSH error dialog: {e}")
            # Fallback: if the dialog fails, still try to clean up the UI
            self._cleanup_terminal_ui(terminal, terminal_id, child_status, identifier)

        return False  # Do not repeat

    def _on_eof(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        """Handle EOF signal - treat as clean exit (status 0)."""
        self._on_child_exited(terminal, 0, identifier, terminal_id)

    def _cleanup_terminal_ui(
        self, terminal: Vte.Terminal, terminal_id: int, child_status: int, identifier
    ) -> bool:
        """Clean up terminal UI - called on main thread."""
        try:
            # Call the terminal exit handler if set
            if self.terminal_exit_handler:
                self.logger.debug(
                    f"Calling terminal exit handler for terminal {terminal_id}"
                )
                self.terminal_exit_handler(terminal, child_status, identifier)

            # Always notify tab manager of process exit for proper UI handling
            if self.tab_manager:
                self.logger.debug(
                    f"Notifying tab manager of terminal {terminal_id} exit"
                )
                self.tab_manager._on_terminal_process_exited(
                    terminal, child_status, identifier
                )
            else:
                self.logger.debug(
                    f"No tab manager available, cleaning up directly for terminal {terminal_id}"
                )
                # Fallback cleanup if no tab manager
                self._cleanup_terminal(terminal, terminal_id)

        except Exception as e:
            self.logger.error(f"Terminal UI cleanup failed: {e}")
        finally:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

        return False  # Don't repeat

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
        """Clean up terminal resources without affecting UI."""
        with self._cleanup_lock:
            if not self.registry.get_terminal_info(terminal_id):
                return

            terminal_info = self.registry.get_terminal_info(terminal_id)
            identifier = terminal_info.get("identifier", "Unknown")
            terminal_name = (
                identifier
                if isinstance(identifier, str)
                else getattr(identifier, "name", "Unknown")
            )

            self.logger.info(
                f"Cleaning up resources for terminal '{terminal_name}' (ID: {terminal_id})"
            )

            # Clean up tracking systems
            self.osc7_tracker.untrack_terminal(terminal)
            self.manual_ssh_tracker.untrack(terminal_id)

            # Clean up hyperlink state
            if hasattr(terminal, "_hovered_hyperlink"):
                delattr(terminal, "_hovered_hyperlink")

            # Remove user-initiated close flag if present
            if hasattr(terminal, "_closed_by_user"):
                try:
                    delattr(terminal, "_closed_by_user")
                except Exception:
                    pass

            # Unregister from registry
            success = self.registry.unregister_terminal(terminal_id)
            if success:
                self._stats["terminals_closed"] += 1
                log_terminal_event(
                    "removed", terminal_name, "terminal resources cleaned"
                )

            # Cancel any pending SIGKILL timers
            if terminal_id in self._pending_kill_timers:
                timeout_id = self._pending_kill_timers.pop(terminal_id)
                GLib.source_remove(timeout_id)

    def close_terminal(self, terminal: Vte.Terminal) -> bool:
        """Public method to close a terminal properly."""
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return False

        return self.remove_terminal(terminal, force_kill_group=False)

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any,
        terminal_id: int,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> None:
        try:
            if error:
                self.logger.error(
                    f"Terminal spawn failed for ID {terminal_id}: {error.message}"
                )
                self.registry.update_terminal_status(terminal_id, "spawn_failed")
            else:
                self.logger.debug(
                    f"Terminal spawn successful for ID {terminal_id}, PID: {pid}"
                )
                self.registry.update_terminal_process(terminal_id, pid)

                # Execute command if provided
                if execute_command and pid > 0:
                    self.logger.info(
                        f"Executing command in terminal {terminal_id}: {execute_command}"
                    )
                    try:
                        # Add a small delay to ensure the shell is ready
                        def execute_once():
                            self._execute_command_in_terminal(
                                terminal, execute_command, close_after_execute
                            )
                            return False  # Prevent the timeout from repeating

                        GLib.timeout_add(100, execute_once)
                    except Exception as e:
                        self.logger.error(f"Failed to schedule command execution: {e}")

        except Exception as e:
            self.logger.error(
                f"Spawn callback handling failed for terminal ID {terminal_id}: {e}"
            )

    def _execute_command_in_terminal(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool = False
    ) -> bool:
        """
        Execute a command in a terminal.

        Args:
            terminal: Terminal widget
            command: Command to execute
            close_after_execute: Whether to close terminal after execution

        Returns:
            True if command was sent successfully
        """
        try:
            if not terminal or not command:
                return False

            # For close_after_execute, wrap command to exit after completion
            if close_after_execute:
                command_with_exit = f"({command}); exit"
                command_bytes = f"{command_with_exit}\n".encode("utf-8")
            else:
                # Send the command followed by Enter
                command_bytes = f"{command}\n".encode("utf-8")

            terminal.feed_child(command_bytes)

            self.logger.debug(f"Command executed in terminal: {command}")
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
            self.logger.debug(
                f"Process {pid} ('{terminal_name}') terminated before SIGKILL fallback."
            )
        except Exception as e:
            self.logger.error(f"Error during final check for PID {pid}: {e}")
        return False

    def remove_terminal(
        self, terminal: Vte.Terminal, force_kill_group: bool = False
    ) -> bool:
        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info or terminal_info.get("status") in [
                TerminalState.EXITED.value,
                TerminalState.SPAWN_FAILED.value,
            ]:
                self.logger.debug(
                    f"Skipping remove for already exited/failed terminal {terminal_id}"
                )
                return False

            pid = terminal_info.get("process_id")
            if not pid or pid == -1:
                GLib.idle_add(self._cleanup_terminal, terminal, terminal_id)
                return False

            terminal_name = (
                terminal_info["identifier"].name
                if isinstance(terminal_info["identifier"], SessionItem)
                else str(terminal_info["identifier"])
            )
            terminal_type = terminal_info.get("type", "local")

            self.logger.info(
                f"Initiating shutdown for terminal '{terminal_name}' (PID: {pid}, Type: {terminal_type})"
            )

            # Mark this terminal as being closed by user action so child-exit
            # handlers can avoid showing connection error dialogs for SSH/SFTP.
            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception:
                # Non-fatal: if we cannot set attribute, continue with shutdown
                self.logger.debug(
                    f"Could not set _closed_by_user on terminal {terminal_id}"
                )

            if not is_windows():
                try:
                    signal_to_send = (
                        signal.SIGHUP if terminal_type == "local" else signal.SIGTERM
                    )
                    signal_name = "SIGHUP" if terminal_type == "local" else "SIGTERM"

                    # FIX: Differentiate between closing an individual split vs an entire tab
                    if force_kill_group:
                        # Closing an entire tab - kill the whole process group
                        pgid = os.getpgid(pid)
                        os.killpg(pgid, signal_to_send)
                        self.logger.debug(
                            f"{signal_name} sent to process group {pgid} of PID {pid}."
                        )
                    else:
                        # Closing an individual split - kill only this process
                        os.kill(pid, signal_to_send)
                        self.logger.debug(
                            f"{signal_name} sent to individual process PID {pid}."
                        )

                except (ProcessLookupError, PermissionError) as e:
                    self.logger.debug(
                        f"Could not send signal to PID {pid}, likely already exited: {e}"
                    )
                    return True
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError) as e:
                    self.logger.debug(
                        f"Could not send SIGTERM to PID {pid} on Windows: {e}"
                    )
                    return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_terminated, pid, terminal_name, terminal_id
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            self.logger.debug(f"SIGKILL fallback scheduled for PID {pid} in 5 seconds.")

            return True

    def update_all_terminals(self) -> None:
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            for terminal_id in terminal_ids:
                terminal = self.registry.get_terminal(terminal_id)
                if terminal and terminal.get_realized():
                    self.settings_manager.apply_terminal_settings(
                        terminal, self.parent_window
                    )
        except Exception as e:
            self.logger.error(f"Failed to update terminals: {e}")

    def get_terminal_count(self) -> int:
        return self.registry.get_terminal_count()

    def get_terminals(self) -> List[Vte.Terminal]:
        terminals = []
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                terminals.append(terminal)
        return terminals

    def cleanup_all_terminals(self) -> None:
        self.logger.debug("Cleaning up TerminalManager resources.")
        if hasattr(self, "_process_check_timer_id") and self._process_check_timer_id:
            GLib.source_remove(self._process_check_timer_id)
            self._process_check_timer_id = None
            self.logger.debug("Periodic process check timer removed.")

        # Clears the registries to prevent memory leaks
        self.registry._terminals.clear()
        self.registry._terminal_refs.clear()

    def copy_selection(self, terminal: Vte.Terminal) -> bool:
        if terminal.get_has_selection():
            terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        return False

    def paste_clipboard(self, terminal: Vte.Terminal) -> bool:
        terminal.paste_clipboard()
        return True

    def select_all(self, terminal: Vte.Terminal) -> None:
        terminal.select_all()

    def zoom_in(self, terminal: Vte.Terminal, step: float = 0.1) -> bool:
        current_scale = terminal.get_font_scale()
        new_scale = min(current_scale + step, 3.0)
        terminal.set_font_scale(new_scale)
        self.settings_manager.set("font_scale", new_scale, save_immediately=False)
        return True

    def zoom_out(self, terminal: Vte.Terminal, step: float = 0.1) -> bool:
        current_scale = terminal.get_font_scale()
        new_scale = max(current_scale - step, 0.3)
        terminal.set_font_scale(new_scale)
        self.settings_manager.set("font_scale", new_scale, save_immediately=False)
        return True

    def zoom_reset(self, terminal: Vte.Terminal) -> bool:
        terminal.set_font_scale(1.0)
        self.settings_manager.set("font_scale", 1.0, save_immediately=False)
        return True

    def get_statistics(self) -> Dict[str, Any]:
        stats = self._stats.copy()
        stats.update({
            "active_terminals": self.get_terminal_count(),
            "platform": self.platform_info.platform_type.value,
            "vte_available": VTE_AVAILABLE,
        })
        return stats

    def get_terminal_info(self, terminal: Vte.Terminal) -> Optional[Dict[str, Any]]:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is not None:
            return self.registry.get_terminal_info(terminal_id)
        return None

    def has_active_ssh_sessions(self) -> bool:
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if terminal_info and terminal_info.get("type") == "ssh":
                process_id = terminal_info.get("process_id")
                status = terminal_info.get("status", "")
                if (
                    process_id
                    and process_id > 0
                    and status not in ["exited", "spawn_failed"]
                ):
                    return True
        return False

    def get_active_ssh_session_names(self) -> list:
        ssh_sessions = []
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if terminal_info and terminal_info.get("type") == "ssh":
                process_id = terminal_info.get("process_id")
                status = terminal_info.get("status", "")
                inactive_statuses = [
                    "exited",
                    "spawn_failed",
                    "eof",
                    "exited_0",
                    "exited_1",
                    "exited_2",
                ]
                is_active = (
                    process_id
                    and process_id > 0
                    and not any(
                        status.startswith(inactive) for inactive in inactive_statuses
                    )
                )
                if is_active:
                    identifier = terminal_info.get("identifier")
                    if hasattr(identifier, "name") and hasattr(
                        identifier, "get_connection_string"
                    ):
                        ssh_sessions.append(
                            f"{identifier.name} ({identifier.get_connection_string()})"
                        )
                    elif hasattr(identifier, "name"):
                        ssh_sessions.append(identifier.name)
                    else:
                        ssh_sessions.append(str(identifier))
        return ssh_sessions

    def get_active_ssh_count(self) -> int:
        count = 0
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if terminal_info and terminal_info.get("type") == "ssh":
                process_id = terminal_info.get("process_id")
                status = terminal_info.get("status", "")
                if (
                    process_id
                    and process_id > 0
                    and status not in ["exited", "spawn_failed"]
                ):
                    count += 1
        return count

    def _setup_native_hyperlinks(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Setup VTE's native hyperlink support with URL regex patterns."""
        try:
            # Enable VTE's built-in hyperlink detection
            terminal.set_allow_hyperlink(True)

            # Configure URL detection patterns
            self._configure_url_patterns(terminal)

            # Connect to VTE's native hyperlink hover signal
            terminal.connect(
                "hyperlink-hover-uri-changed", self._on_hyperlink_hover, terminal_id
            )

            self.logger.debug(
                f"Native hyperlink support with patterns enabled for terminal {terminal_id}"
            )

        except Exception as e:
            self.logger.error(
                f"Failed to setup native hyperlinks for terminal {terminal_id}: {e}"
            )

    def _on_hyperlink_hover(
        self, terminal: Vte.Terminal, uri: str, bbox, terminal_id: int
    ):
        """Handle VTE's native hyperlink hover signal."""
        try:
            if uri:
                # Store the currently hovered URI for click handling
                terminal._hovered_hyperlink = uri
                self.logger.debug(f"Hyperlink hovered in terminal {terminal_id}: {uri}")
            else:
                # Clear hovered URI when mouse leaves hyperlink
                if hasattr(terminal, "_hovered_hyperlink"):
                    delattr(terminal, "_hovered_hyperlink")
                    self.logger.debug(
                        f"Hyperlink hover cleared in terminal {terminal_id}"
                    )

        except Exception as e:
            self.logger.error(
                f"Hyperlink hover handling failed for terminal {terminal_id}: {e}"
            )

    def _open_hyperlink(self, uri: str) -> bool:
        """Open hyperlink using system default handler."""
        try:
            import subprocess
            from urllib.parse import urlparse

            if not uri or not uri.strip():
                self.logger.warning("Empty or invalid URI provided")
                return False

            uri = uri.strip()

            # Basic URI validation
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

    def _configure_url_patterns(self, terminal: Vte.Terminal) -> None:
        """Configure URL detection patterns for the terminal."""
        try:
            # URL regex patterns
            url_patterns = [
                # HTTP/HTTPS URLs
                r'https?://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;:!?]',
                # FTP URLs
                r'ftp://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;:!?]',
                # Email addresses
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            ]

            # Try VTE.Regex first (newer VTE versions)
            if hasattr(terminal, "match_add_regex") and hasattr(Vte, "Regex"):
                for pattern in url_patterns:
                    try:
                        # **CORREÇÃO: Usar o flag Vte.RegexFlags.MULTILINE**
                        regex_flags = 0
                        if hasattr(Vte.RegexFlags, "MULTILINE"):
                            regex_flags = Vte.RegexFlags.MULTILINE

                        regex = Vte.Regex.new_for_match(
                            pattern, len(pattern.encode()), regex_flags
                        )
                        if regex:
                            tag = terminal.match_add_regex(regex, 0)

                            if hasattr(terminal, "match_set_cursor_name"):
                                terminal.match_set_cursor_name(tag, "pointer")

                            self.logger.debug(
                                f"URL pattern added (Vte.Regex): {pattern} (tag: {tag})"
                            )

                    except Exception as e:
                        self.logger.debug(
                            f"Failed to add VTE regex pattern '{pattern}': {e}"
                        )

            # Fallback to GLib.Regex for older versions
            elif hasattr(terminal, "match_add_gregex"):
                from gi.repository import GLib

                for pattern in url_patterns:
                    try:
                        regex = GLib.Regex.new(
                            pattern, GLib.RegexCompileFlags.OPTIMIZE, 0
                        )
                        tag = terminal.match_add_gregex(regex, 0)
                        terminal.match_set_cursor_type(tag, 2)  # Hand cursor
                        self.logger.debug(
                            f"URL pattern added (GLib.Regex): {pattern} (tag: {tag})"
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to add GLib regex pattern '{pattern}': {e}"
                        )

            else:
                self.logger.warning("No URL pattern matching method available in VTE")

        except Exception as e:
            self.logger.error(f"Failed to configure URL patterns: {e}")

    def _is_valid_url(self, text: str) -> bool:
        """Check if text is a valid URL."""
        try:
            from urllib.parse import urlparse

            result = urlparse(text.strip())
            return bool(result.scheme and result.netloc)
        except Exception:
            return False
