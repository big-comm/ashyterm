# terminal/manager.py

from typing import List, Optional, Union, Callable, Any, Dict
import threading
import time
import weakref
import socket
import os
import signal
import subprocess

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Gdk, GLib
from gi.repository import Vte

from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager
from ..settings.config import VTE_AVAILABLE
from ..ui.menus import create_terminal_menu
from .spawner import get_spawner

# Import new utility systems
from ..utils.logger import get_logger, log_terminal_event
from ..utils.exceptions import (
    TerminalCreationError,
    TerminalSpawnError,
    VTENotAvailableError,
)
from ..utils.security import validate_session_data
from ..utils.platform import get_platform_info, get_environment_manager, is_windows
from ..utils.osc7_tracker import get_osc7_tracker, OSC7Info


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

    def get_ssh_target(self, terminal_id: int) -> Optional[str]:
        with self._lock:
            return self._tracked_terminals.get(terminal_id, {}).get("ssh_target")


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

        self.osc7_tracker = get_osc7_tracker(settings_manager)
        # --- INÍCIO DA MODIFICAÇÃO ---
        self.manual_ssh_tracker = ManualSSHTracker(
            self.registry, self._on_manual_ssh_state_changed
        )
        # --- FIM DA MODIFICAÇÃO ---

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pending_kill_timers: Dict[int, int] = {}

        self.security_auditor = None

        self.on_terminal_child_exited: Optional[Callable] = None
        self.on_terminal_eof: Optional[Callable] = None
        self.on_terminal_focus_changed: Optional[Callable] = None
        self.on_terminal_should_close: Optional[Callable] = None
        self.on_terminal_directory_changed: Optional[Callable] = None

        self._stats = {
            "terminals_created": 0,
            "terminals_failed": 0,
            "terminals_closed": 0,
        }

        # --- INÍCIO DA MODIFICAÇÃO ---
        self._process_check_timer_id = GLib.timeout_add_seconds(
            2, self._periodic_process_check
        )
        # --- FIM DA MODIFICAÇÃO ---

        self.logger.info("Terminal manager initialized")

    # --- INÍCIO DE NOVOS MÉTODOS ---
    def _periodic_process_check(self) -> bool:
        """Periodically check the process tree for all local terminals."""
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            for terminal_id in terminal_ids:
                self.manual_ssh_tracker.check_process_tree(terminal_id)
        except Exception as e:
            self.logger.error(f"Periodic process check failed: {e}")
        return True  # Keep the timer running

    def _on_manual_ssh_state_changed(self, terminal: Vte.Terminal):
        """Callback from tracker when SSH state changes. Forces a title update."""
        self.logger.debug(
            f"Manual SSH state changed for terminal {getattr(terminal, 'terminal_id', 'N/A')}, forcing title update."
        )
        self._update_title(terminal)
        return False  # for GLib.idle_add

    # --- FIM DE NOVOS MÉTODOS ---

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, param_spec):
        """Handles the notify::current-directory-uri signal from VTE."""
        try:
            uri = terminal.get_current_directory_uri()
            if not uri:
                return

            from urllib.parse import urlparse, unquote

            parsed_uri = urlparse(uri)
            if parsed_uri.scheme != "file":
                return

            path = unquote(parsed_uri.path)
            hostname = parsed_uri.hostname or "localhost"

            display_path = self.osc7_tracker.parser._create_display_path(path)
            osc7_info = OSC7Info(
                hostname=hostname, path=path, display_path=display_path
            )

            # --- INÍCIO DA MODIFICAÇÃO ---
            self._update_title(terminal, osc7_info)
            # --- FIM DA MODIFICAÇÃO ---

        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    # --- INÍCIO DE NOVO MÉTODO UNIFICADO ---
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

        new_title = "Terminal"  # Default

        # Case 1: Managed SSH Session (from sidebar)
        if terminal_info.get("type") == "ssh":
            session = terminal_info.get("identifier")
            if isinstance(session, SessionItem) and osc7_info:
                new_title = f"{session.name}:{osc7_info.display_path}"
            elif isinstance(session, SessionItem):
                new_title = session.name

        # Case 2: Local Terminal
        elif terminal_info.get("type") == "local":
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)

            # Sub-case 2a: Manual SSH is active
            if ssh_target:
                if osc7_info:
                    new_title = f"{ssh_target}:{osc7_info.display_path}"
                else:
                    # We're in SSH but haven't received a remote path yet
                    new_title = ssh_target
            # Sub-case 2b: Truly local
            elif osc7_info:
                new_title = osc7_info.display_path
            # Fallback to base title if no info
            else:
                new_title = terminal_info.get("identifier", "Local")

        if self.on_terminal_directory_changed:
            self.on_terminal_directory_changed(terminal, new_title, osc7_info)

    # --- FIM DE NOVO MÉTODO UNIFICADO ---

    def create_local_terminal(
        self, title: str = "Local Terminal"
    ) -> Optional[Vte.Terminal]:
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for local terminal creation")
                raise VTENotAvailableError()

            try:
                self.logger.debug(f"Creating local terminal: '{title}'")

                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError(
                        "base terminal creation failed", "local"
                    )

                terminal_id = self.registry.register_terminal(terminal, "local", title)

                self._setup_terminal_events(terminal, title, terminal_id)

                success = self.spawner.spawn_local_terminal(
                    terminal,
                    lambda t, pid, error, data: self._on_spawn_callback(
                        t, pid, error, data, terminal_id
                    ),
                    title,
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

            if self.platform_info.is_windows():
                terminal.set_encoding("utf-8")

            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)

            self._setup_context_menu(terminal)

            self.logger.debug("Base terminal created and configured")
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
                "child-exited", self._on_terminal_child_exited, identifier, terminal_id
            )
            terminal.connect("eof", self._on_terminal_eof, identifier, terminal_id)
            terminal.connect(
                "notify::current-directory-uri", self._on_directory_uri_changed
            )

            # --- INÍCIO DA MODIFICAÇÃO ---
            terminal_name = (
                identifier if isinstance(identifier, str) else identifier.name
            )
            self.manual_ssh_tracker.track(terminal_id, terminal)
            # --- FIM DA MODIFICAÇÃO ---

            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect(
                "enter", self._on_terminal_focus_in, terminal, terminal_id
            )
            focus_controller.connect(
                "leave", self._on_terminal_focus_out, terminal, terminal_id
            )
            terminal.add_controller(focus_controller)

            click_controller = Gtk.GestureClick()
            click_controller.set_button(0)
            click_controller.connect(
                "pressed", self._on_terminal_clicked, terminal, terminal_id
            )
            terminal.add_controller(click_controller)

            terminal.terminal_id = terminal_id

            self.logger.debug(f"Terminal events configured for ID: {terminal_id}")

        except Exception as e:
            self.logger.error(
                f"Failed to setup terminal events for ID {terminal_id}: {e}"
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
            menu_model = create_terminal_menu(terminal)
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(terminal)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()
        except Exception as e:
            self.logger.error(f"Terminal right click handling failed: {e}")

    def _on_terminal_clicked(self, gesture, n_press, x, y, terminal, terminal_id):
        try:
            terminal.grab_focus()
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
            return Gdk.EVENT_PROPAGATE
        except Exception as e:
            self.logger.error(f"Terminal click handling failed: {e}")
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

    def _on_terminal_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        try:
            terminal_name = (
                identifier if isinstance(identifier, str) else identifier.name
            )
            self.logger.info(
                f"Processo filho do terminal '{terminal_name}' (ID: {terminal_id}) saiu com status: {child_status}"
            )

            if terminal_id in self._pending_kill_timers:
                timeout_id = self._pending_kill_timers.pop(terminal_id)
                GLib.source_remove(timeout_id)
                self.logger.debug(
                    f"Timer de SIGKILL cancelado para o terminal {terminal_id} pois o processo encerrou graciosamente."
                )

            self.registry.update_terminal_status(terminal_id, f"exited_{child_status}")
            log_terminal_event("exited", terminal_name, f"status {child_status}")

            if child_status != 0:
                try:
                    if terminal.get_realized() and not (
                        hasattr(terminal, "is_closed") and terminal.is_closed()
                    ):
                        message = f"\r\n[Processo em '{terminal_name}' encerrou inesperadamente (status: {child_status})]\r\n"
                        terminal.feed(message.encode("utf-8"))
                except GLib.Error as e:
                    self.logger.debug(
                        f"Não foi possível enviar mensagem de saída para '{terminal_name}': {e}"
                    )

            if self.on_terminal_should_close:
                GLib.idle_add(
                    self.on_terminal_should_close, terminal, child_status, identifier
                )

            self._cleanup_terminal_resources(terminal, terminal_id)

            if self.on_terminal_child_exited:
                self.on_terminal_child_exited(terminal, child_status, identifier)

        except Exception as e:
            self.logger.error(
                f"Falha no tratamento da saída do processo filho para ID {terminal_id}: {e}"
            )

    def _on_terminal_eof(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        try:
            terminal_name = (
                identifier if isinstance(identifier, str) else identifier.name
            )
            self.logger.info(
                f"Terminal '{terminal_name}' (ID: {terminal_id}) received EOF signal"
            )
            self.registry.update_terminal_status(terminal_id, "eof")
            log_terminal_event("eof", terminal_name, "EOF signal received")
            if self.on_terminal_eof:
                self.on_terminal_eof(terminal, identifier)
        except Exception as e:
            self.logger.error(f"Terminal EOF handling failed for ID {terminal_id}: {e}")

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any,
        terminal_id: int,
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
        except Exception as e:
            self.logger.error(
                f"Spawn callback handling failed for terminal ID {terminal_id}: {e}"
            )

    def _ensure_process_terminated(
        self, pid: int, terminal_name: str, terminal_id: int
    ) -> bool:
        try:
            self._pending_kill_timers.pop(terminal_id, None)
            os.kill(pid, 0)
            self.logger.warning(
                f"Processo {pid} ('{terminal_name}') não saiu graciosamente. Enviando SIGKILL."
            )
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            self.logger.debug(
                f"Processo {pid} ('{terminal_name}') terminou antes do fallback de SIGKILL."
            )
        except Exception as e:
            self.logger.error(f"Erro durante a verificação final para o PID {pid}: {e}")
        return False

    def _cleanup_terminal_resources(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
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
                f"Limpando recursos para o terminal '{terminal_name}' (ID: {terminal_id})"
            )

            self.osc7_tracker.untrack_terminal(terminal)
            # --- INÍCIO DA MODIFICAÇÃO ---
            self.manual_ssh_tracker.untrack(terminal_id)
            # --- FIM DA MODIFICAÇÃO ---

            success = self.registry.unregister_terminal(terminal_id)
            if success:
                self._stats["terminals_closed"] += 1
                log_terminal_event(
                    "removed", terminal_name, "recursos do terminal limpos"
                )

            if terminal_id in self._pending_kill_timers:
                timeout_id = self._pending_kill_timers.pop(terminal_id)
                GLib.source_remove(timeout_id)

    def _get_ssh_control_path(self, session: "SessionItem") -> str:
        user = session.user or os.getlogin()
        port = session.port or 22
        self.platform_info.cache_dir.mkdir(parents=True, exist_ok=True)
        return str(
            self.platform_info.cache_dir / f"ssh_control_{session.host}_{port}_{user}"
        )

    def remove_terminal(self, terminal: Vte.Terminal) -> bool:
        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info or terminal_info.get("status", "").startswith(
                "exited"
            ):
                return False

            pid = terminal_info.get("process_id")
            if not pid or pid == -1:
                GLib.idle_add(self._cleanup_terminal_resources, terminal, terminal_id)
                return False

            terminal_name = (
                terminal_info["identifier"].name
                if isinstance(terminal_info["identifier"], SessionItem)
                else str(terminal_info["identifier"])
            )
            terminal_type = terminal_info.get("type", "local")

            self.logger.info(
                f"Iniciando encerramento para o terminal '{terminal_name}' (PID: {pid}, Tipo: {terminal_type})"
            )

            if terminal_type == "ssh":
                session = terminal_info.get("identifier")
                if isinstance(session, SessionItem):
                    self._shutdown_ssh_master_async(session)

            if not is_windows():
                try:
                    signal_to_send = (
                        signal.SIGHUP if terminal_type == "local" else signal.SIGTERM
                    )
                    signal_name = "SIGHUP" if terminal_type == "local" else "SIGTERM"
                    os.killpg(os.getpgid(pid), signal_to_send)
                    self.logger.debug(
                        f"{signal_name} enviado para o grupo de processos do PID {pid}."
                    )
                except (ProcessLookupError, PermissionError) as e:
                    self.logger.debug(
                        f"Não foi possível enviar sinal para o PID {pid}, provavelmente já encerrou: {e}"
                    )
                    return True
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError) as e:
                    self.logger.debug(
                        f"Não foi possível enviar SIGTERM para o PID {pid} no Windows: {e}"
                    )
                    return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_terminated, pid, terminal_name, terminal_id
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            self.logger.debug(
                f"Fallback de SIGKILL agendado para o PID {pid} em 5 segundos."
            )

            return True

    def _shutdown_ssh_master_async(self, session: "SessionItem"):
        def shutdown_task():
            try:
                control_path = self._get_ssh_control_path(session)
                command = ["ssh", "-O", "exit", "-S", control_path, session.host]
                self.logger.debug(
                    f"Executando comando de encerramento do mestre SSH: {' '.join(command)}"
                )
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    self.logger.info(
                        f"Comando de encerramento do mestre SSH para {session.host} enviado com sucesso."
                    )
                else:
                    self.logger.debug(
                        f"Comando de encerramento do mestre SSH falhou (pode ser normal): {result.stderr.strip()}"
                    )
            except Exception as e:
                self.logger.error(
                    f"Erro ao tentar encerrar o mestre SSH para {session.host}: {e}"
                )

        thread = threading.Thread(target=shutdown_task, daemon=True)
        thread.start()

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
        if hasattr(self, "_process_check_timer_id") and self._process_check_timer_id:
            GLib.source_remove(self._process_check_timer_id)
            self._process_check_timer_id = None
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

    def apply_zoom_to_all_terminals(self, scale: float) -> int:
        updated_count = 0
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal = self.registry.get_terminal(terminal_id)
            if terminal and terminal.get_realized():
                terminal.set_font_scale(scale)
                updated_count += 1
        self.settings_manager.set("font_scale", scale, save_immediately=False)
        return updated_count

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