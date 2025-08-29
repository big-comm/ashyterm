# ashyterm/terminal/manager.py

import os
import pathlib
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
from gi.repository import GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager
from ..ui.menus import create_terminal_menu
from ..ui.ssh_dialogs import create_generic_ssh_error_dialog
from ..utils.exceptions import (
    TerminalCreationError,
    VTENotAvailableError,
)
from ..utils.logger import get_logger, log_terminal_event
from ..utils.osc7_tracker import OSC7Info, get_osc7_tracker
from ..utils.platform import get_environment_manager, get_platform_info
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
        with self._lock:
            return terminal_id in self._closing_terminals

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
            return terminal_id

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
        self.tab_manager = None
        self.on_terminal_focus_changed: Optional[Callable] = None
        self.on_terminal_directory_changed: Optional[Callable] = None
        self.terminal_exit_handler: Optional[Callable] = None
        self._stats = {
            "terminals_created": 0,
            "terminals_failed": 0,
            "terminals_closed": 0,
        }
        self._process_check_timer_id = GLib.timeout_add_seconds(
            2, self._periodic_process_check
        )
        self.logger.info("Terminal manager initialized")

    def apply_settings_to_all_terminals(self):
        """Applies current settings to all active terminals."""
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
        try:
            for terminal_id in self.registry.get_all_terminal_ids():
                self.manual_ssh_tracker.check_process_tree(terminal_id)
        except Exception as e:
            self.logger.error(f"Periodic process check failed: {e}")
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

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, param_spec):
        try:
            uri = terminal.get_current_directory_uri()
            if not uri:
                return
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
            self._update_title(terminal, osc7_info)
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    def _update_title(
        self, terminal: Vte.Terminal, osc7_info: Optional[OSC7Info] = None
    ):
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        terminal_info = self.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            return
        new_title = "Terminal"
        if terminal_info.get("type") == "ssh":
            session = terminal_info.get("identifier")
            if isinstance(session, SessionItem):
                new_title = (
                    f"{session.name}:{osc7_info.display_path}"
                    if osc7_info
                    else session.name
                )
        elif terminal_info.get("type") == "local":
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)
            if ssh_target:
                new_title = (
                    f"{ssh_target}:{osc7_info.display_path}"
                    if osc7_info
                    else ssh_target
                )
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
    ):
        terminal = self._create_base_terminal()
        if not terminal:
            raise TerminalCreationError("base terminal creation failed", "local")

        terminal_id = self.registry.register_terminal(terminal, "local", title)
        self._setup_terminal_events(terminal, title, terminal_id)

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

            self.spawner.spawn_local_terminal(
                terminal,
                callback=self._on_spawn_callback,
                user_data=user_data_for_spawn,
                working_directory=resolved_working_dir,
            )

            self.logger.info(
                f"Local terminal created successfully: '{title}' (ID: {terminal_id})"
            )
            log_terminal_event("created", title, "local terminal")
            self._stats["terminals_created"] += 1
            return terminal
        except TerminalCreationError as e:
            self.registry.unregister_terminal(terminal_id)
            self._stats["terminals_failed"] += 1
            raise

    def create_ssh_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        with self._creation_lock:
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
            user_data_for_spawn = (terminal_id, session)

            try:
                self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                )
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
            except TerminalCreationError as e:
                self.registry.unregister_terminal(terminal_id)
                self._stats["terminals_failed"] += 1
                raise

    def _create_base_terminal(self) -> Optional[Vte.Terminal]:
        try:
            terminal = Vte.Terminal()
            terminal.set_vexpand(True)
            terminal.set_hexpand(True)
            terminal.set_mouse_autohide(True)
            terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            terminal.set_scroll_on_output(True)
            terminal.set_scroll_on_keystroke(True)
            terminal.set_scroll_unit_is_pixels(True)
            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)
            self._setup_context_menu(terminal)
            return terminal
        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None

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
            terminal.connect(
                "notify::current-directory-uri", self._on_directory_uri_changed
            )
            self.manual_ssh_tracker.track(terminal_id, terminal)
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect(
                "enter", self._on_terminal_focus_in, terminal, terminal_id
            )
            terminal.add_controller(focus_controller)
            terminal.terminal_id = terminal_id
        except Exception as e:
            self.logger.error(
                f"Failed to configure terminal events for ID {terminal_id}: {e}"
            )

    def _setup_context_menu(self, terminal: Vte.Terminal) -> None:
        try:
            menu_model = create_terminal_menu(terminal)
            terminal.set_context_menu_model(menu_model)
        except Exception as e:
            self.logger.error(f"Context menu setup failed: {e}")

    def _on_terminal_focus_in(self, controller, terminal, terminal_id):
        try:
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
        except Exception as e:
            self.logger.error(f"Terminal focus in handling failed: {e}")

    def _on_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            return
        try:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return
            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )
            if terminal_id in self._pending_kill_timers:
                GLib.source_remove(self._pending_kill_timers.pop(terminal_id))
            closed_by_user = getattr(terminal, "_closed_by_user", False)
            if (
                terminal_info.get("type") in ["ssh", "sftp"]
                and child_status != 0
                and not closed_by_user
            ):
                self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.SPAWN_FAILED
                )
                self.logger.warning(
                    f"SSH/SFTP connection for '{terminal_name}' failed with status: {child_status}"
                )
                GLib.idle_add(
                    self._show_ssh_connection_error_dialog,
                    terminal_name,
                    identifier,
                    terminal,
                    terminal_id,
                    child_status,
                )
            else:
                if not self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.EXITED
                ):
                    self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                    return
                self.logger.info(
                    f"Terminal '{terminal_name}' process exited with status: {child_status}"
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
        try:
            connection_string = (
                identifier.get_connection_string()
                if isinstance(identifier, SessionItem)
                else ""
            )
            dialog = create_generic_ssh_error_dialog(
                self.parent_window, session_name, connection_string
            )

            def on_dialog_response(dlg, response_id):
                self._cleanup_terminal_ui(
                    terminal, terminal_id, child_status, identifier
                )
                dlg.destroy()

            dialog.connect("response", on_dialog_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show SSH error dialog: {e}")
            self._cleanup_terminal_ui(terminal, terminal_id, child_status, identifier)
        return False

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

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
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
            self.osc7_tracker.untrack_terminal(terminal)
            self.manual_ssh_tracker.untrack(terminal_id)
            if hasattr(terminal, "_closed_by_user"):
                try:
                    delattr(terminal, "_closed_by_user")
                except Exception:
                    pass
            if self.registry.unregister_terminal(terminal_id):
                self._stats["terminals_closed"] += 1
                log_terminal_event(
                    "removed", terminal_name, "terminal resources cleaned"
                )
            if terminal_id in self._pending_kill_timers:
                GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data_tuple: tuple,
    ) -> None:
        try:
            terminal_id, user_data = user_data_tuple
            if error:
                self.logger.error(
                    f"Terminal spawn failed for ID {terminal_id}: {error.message}"
                )
                self.registry.update_terminal_status(terminal_id, "spawn_failed")
            else:
                self.registry.update_terminal_process(terminal_id, pid)
                if isinstance(user_data, dict):
                    execute_command = user_data.get("execute_command")
                    if execute_command and pid > 0:

                        def execute_once():
                            self._execute_command_in_terminal(
                                terminal,
                                execute_command,
                                user_data.get("close_after_execute", False),
                            )
                            return False

                        GLib.timeout_add(100, execute_once)
        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")

    def _execute_command_in_terminal(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool = False
    ) -> bool:
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
        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False
            info = self.registry.get_terminal_info(terminal_id)
            if not info or info.get("status") in [
                TerminalState.EXITED.value,
                TerminalState.SPAWN_FAILED.value,
            ]:
                return False
            pid = info.get("process_id")
            if not pid or pid == -1:
                GLib.idle_add(self._cleanup_terminal, terminal, terminal_id)
                return False

            terminal_name = (
                info["identifier"].name
                if isinstance(info["identifier"], SessionItem)
                else str(info["identifier"])
            )
            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception:
                pass

            try:
                target_id = os.getpgid(pid) if force_kill_group else pid
                os.kill(target_id, signal.SIGHUP)
            except (ProcessLookupError, PermissionError) as e:
                self.logger.warning(
                    f"Could not send signal to PID {pid}, likely already exited: {e}"
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

    def copy_selection(self, terminal: Vte.Terminal):
        if terminal.get_has_selection():
            terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste_clipboard(self, terminal: Vte.Terminal):
        terminal.paste_clipboard()

    def select_all(self, terminal: Vte.Terminal):
        terminal.select_all()

    def cleanup_all_terminals(self):
        if self._process_check_timer_id:
            GLib.source_remove(self._process_check_timer_id)
            self._process_check_timer_id = None
        get_spawner().process_tracker.terminate_all()