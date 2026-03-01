# ashyterm/filemanager/manager.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import os
import shlex
import tempfile
import threading
import weakref
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Vte

from ..core.tasks import AsyncTaskManager
from ..helpers import create_themed_popover_menu
from ..sessions.models import SessionItem
from ..terminal.manager import TerminalManager as TerminalManagerType
from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.security import InputSanitizer, ensure_secure_directory_permissions
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _
from ..utils.accessibility import set_label as a11y_label
from .models import FileItem
from .operations import FileOperations
from .search import FileSearchMixin
from .transfer_manager import TransferManager
from .transfers import FileTransferMixin

# CSS for file manager styles is now loaded from:
# data/styles/components.css (loaded by window_ui.py at startup)
# Classes: .transfer-progress-bar, .search-entry-no-icon

MAX_RECURSIVE_RESULTS = 1000


class FileManager(FileSearchMixin, FileTransferMixin, GObject.Object):
    FILE_MANAGER_MIN_HEIGHT = 200

    __gsignals__ = {
        "temp-files-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(
        self,
        parent_window: Gtk.Window,
        terminal_manager: TerminalManagerType,
        settings_manager,
    ):
        """
        Initializes the FileManager.
        Dependencies like TerminalManager are injected for better decoupling.

        Args:
            parent_window: The parent window, used for dialogs.
            terminal_manager: The central manager for terminal instances.
            settings_manager: The application's settings manager.
        """
        super().__init__()
        self.logger = get_logger("ashyterm.filemanager.manager")
        # Task 1: Store as weakrefs to prevent circular reference memory leaks
        self._parent_window_ref = weakref.ref(parent_window)
        self._terminal_manager_ref = weakref.ref(terminal_manager)
        self.settings_manager = settings_manager
        # Use global AsyncTaskManager instead of local executor
        self.transfer_history_window = None
        self.tooltip_helper = get_tooltip_helper()
        self._is_destroyed = False  # Flag to prevent callbacks after destroy

        # CSS styles are now loaded globally from components.css by window_ui.py

        self.session_item: Optional[SessionItem] = None
        self.operations: Optional[FileOperations] = None

        from ..utils.platform import get_config_directory

        self.config_dir = get_config_directory()
        self.transfer_manager = TransferManager(str(self.config_dir), self.operations)

        if self.settings_manager.get("use_system_tmp_for_edit", False):
            self.remote_edit_dir = Path(tempfile.gettempdir()) / "ashyterm_remote_edit"
            self.logger.info(
                f"Using system temporary directory for remote edits: {self.remote_edit_dir}"
            )
        else:
            self.remote_edit_dir = self.config_dir / "remote_edit_tmp"
            self.logger.info(
                f"Using config directory for remote edits: {self.remote_edit_dir}"
            )

        self.remote_edit_dir.mkdir(parents=True, exist_ok=True)
        ensure_secure_directory_permissions(str(self.remote_edit_dir))

        self.current_path = ""
        self._last_successful_path = (
            ""  # Track last successfully listed path for fallback
        )
        self.file_monitors: dict[str, Any] = {}
        self.edited_file_metadata: dict[tuple[str, ...], Any] = {}
        self._is_rebinding = False  # Flag to prevent race conditions during rebind
        self._rsync_status: Dict[str, bool] = {}
        self._rsync_notified_sessions: Set[str] = set()
        self._rsync_checks_in_progress: Set[str] = set()

        # Pre-declare attributes set in _setup_ui
        self.store: Optional[Gio.ListStore] = None
        self.scrolled_window: Optional[Gtk.ScrolledWindow] = None

        # State for verified command execution
        self._pending_command = None
        self._command_timeout_id = 0
        self._clipboard_items: List[Dict[str, Any]] = []
        self._clipboard_operation: Optional[str] = None
        self._clipboard_session_key: Optional[str] = None

        # Recursive search state
        self.recursive_search_enabled = False
        self._showing_recursive_results = False
        self._recursive_search_generation = 0
        self._recursive_search_in_progress = False

        self._build_ui()

        self.bound_terminal = None
        self.directory_change_handler_id = 0

        self.revealer.connect("destroy", self.shutdown)

        self.logger.info("FileManager instance created, awaiting terminal binding.")

    @property
    def parent_window(self):
        """Dereference weakref to get parent window."""
        return self._parent_window_ref()

    @property
    def terminal_manager(self):
        """Dereference weakref to get terminal manager."""
        return self._terminal_manager_ref()

    def reparent(self, new_parent_window, new_terminal_manager):
        """Updates internal references when moved to a new window."""
        self.logger.info("Reparenting FileManager to a new window.")
        self._parent_window_ref = weakref.ref(new_parent_window)
        self._terminal_manager_ref = weakref.ref(new_terminal_manager)

    def rebind_terminal(self, new_terminal: Vte.Terminal):
        """
        Binds the file manager to a new terminal instance, dynamically adjusting
        its context (local vs. remote) based on the terminal's current state.
        """
        self._is_rebinding = True  # Set flag to prevent race conditions
        if (
            self.bound_terminal
            and self.directory_change_handler_id > 0
            and GObject.signal_handler_is_connected(
                self.bound_terminal, self.directory_change_handler_id
            )
        ):
            try:
                self.bound_terminal.disconnect(self.directory_change_handler_id)
            except TypeError:
                self.logger.warning(
                    f"Could not disconnect handler {self.directory_change_handler_id} from old terminal."
                )

        self.bound_terminal = new_terminal
        self.logger.info(
            f"Rebinding file manager to terminal ID: {getattr(new_terminal, 'terminal_id', 'unknown')}"
        )

        terminal_id = getattr(new_terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        if not info:
            self.logger.error(
                f"Cannot rebind to terminal {terminal_id}: no info found."
            )
            self._is_rebinding = False
            return

        ssh_target = self.terminal_manager.manual_ssh_tracker.get_ssh_target(
            terminal_id
        )
        if ssh_target:
            self.logger.info(
                f"Terminal is in a manual SSH session to {ssh_target}. Creating dynamic context."
            )
            parts = ssh_target.split("@", 1)
            user, host = (parts[0], parts[1]) if len(parts) > 1 else (None, parts[0])
            self.session_item = SessionItem(
                name=f"SSH: {ssh_target}",
                session_type="ssh",
                host=host,
                user=user or "",
            )
        elif isinstance(info.get("identifier"), SessionItem):
            self.session_item = info.get("identifier")
        else:
            self.session_item = SessionItem("Local Terminal", session_type="local")

        self.operations = FileOperations(self.session_item)
        self.transfer_manager.file_operations = self.operations
        self._check_remote_rsync_requirement()

        self.directory_change_handler_id = self.bound_terminal.connect(
            "notify::current-directory-uri", self._on_terminal_directory_changed
        )
        self._fm_initiated_cd = False

        self._update_action_bar_for_session_type()
        terminal_dir = self._get_terminal_current_directory()

        # If OSC7 directory is not available, use a sensible default
        if not terminal_dir:
            terminal_dir = self._get_default_directory_for_session()

        terminal_dir_path = Path(terminal_dir).resolve()
        current_path_path = (
            Path(self.current_path).resolve() if self.current_path else None
        )
        if current_path_path is None or terminal_dir_path != current_path_path:
            self.logger.info(
                f"Terminal directory changed from {self.current_path} to {terminal_dir}, refreshing."
            )
            self.refresh(terminal_dir, source="terminal")

        GLib.timeout_add(100, self._finish_rebinding)

    def _get_session_identifier(self, session: SessionItem) -> str:
        """Builds a stable identifier string for the current session."""
        user = (session.user or "").strip()
        host = (session.host or "").strip()
        port = getattr(session, "port", 22) or 22
        user_part = f"{user}@" if user else ""
        host_part = host if host else ""
        return f"{user_part}{host_part}:{port}"

    def _get_current_session_key(self) -> str:
        if not self.session_item:
            return "unknown"
        if self.session_item.is_local():
            return "local"
        return self._get_session_identifier(self.session_item)

    def _show_toast(self, message: str):
        if hasattr(self.parent_window, "toast_overlay"):
            self.parent_window.toast_overlay.add_toast(Adw.Toast(title=message))
        else:
            self.logger.info(message)

    def _clear_clipboard(self) -> None:
        self._clipboard_items = []
        self._clipboard_operation = None
        self._clipboard_session_key = None

    def _can_paste(self) -> bool:
        if not self._clipboard_items or not self._clipboard_operation:
            return False
        if not self.current_path:
            return False
        return self._clipboard_session_key == self._get_current_session_key()

    def _prompt_for_new_item(
        self,
        heading: str,
        body: str,
        default_name: str,
        confirm_label: str,
        callback,
    ) -> None:
        dialog = Adw.AlertDialog(
            heading=heading,
            body=body,
            close_response="cancel",
        )

        entry = Gtk.Entry(text=default_name, hexpand=True, activates_default=True)
        entry.select_region(0, -1)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("confirm", confirm_label)
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("confirm")

        def on_response(dlg, response, *_args):
            if response != "confirm":
                return
            name = InputSanitizer.sanitize_filename(entry.get_text().strip())
            if not name:
                self._show_toast(_("Name cannot be empty."))
                return
            callback(name)

        dialog.connect("response", on_response)
        dialog.present(self.parent_window)

    def _show_rsync_missing_notification(self):
        """Inform the user that rsync is required for optimized transfers."""
        message = _(
            "rsync is not installed on the remote host. Install the rsync package or use SFTP for transfers."
        )
        if hasattr(self.parent_window, "toast_overlay"):
            toast = Adw.Toast(title=message)
            self.parent_window.toast_overlay.add_toast(toast)
        else:
            self.logger.warning(message)

    def _check_remote_rsync_requirement(self):
        """Verify rsync availability for SSH sessions and warn when missing."""
        session = self.session_item
        operations = self.operations
        if not session or not operations or not session.is_ssh():
            return

        session_key = self._get_session_identifier(session)
        if session_key in self._rsync_checks_in_progress:
            return

        self._rsync_checks_in_progress.add(session_key)

        def worker(session_ref: SessionItem, ops_ref: FileOperations, key: str):
            rsync_available = self._check_rsync_on_remote(ops_ref, session_ref, key)
            GLib.idle_add(lambda: self._finalize_rsync_check(key, rsync_available))

        threading.Thread(
            target=worker, args=(session, operations, session_key), daemon=True
        ).start()

    def _check_rsync_on_remote(
        self, ops_ref: FileOperations, session_ref: SessionItem, key: str
    ) -> bool:
        """Check if rsync is available on the remote host."""
        try:
            return ops_ref.check_command_available(
                "rsync", use_cache=False, session_override=session_ref
            )
        except Exception as exc:
            self.logger.error(f"Failed to verify rsync availability for {key}: {exc}")
            return True  # Assume available on error to avoid blocking

    def _finalize_rsync_check(self, key: str, rsync_available: bool) -> bool:
        """Finalize rsync check and notify user if needed."""
        self._rsync_checks_in_progress.discard(key)
        current_session = self.session_item

        if not self._is_current_session_for_key(current_session, key):
            return GLib.SOURCE_REMOVE

        self._rsync_status[key] = rsync_available
        if not rsync_available:
            self._notify_rsync_missing_if_needed(key)
        else:
            self._rsync_notified_sessions.discard(key)

        return GLib.SOURCE_REMOVE

    def _is_current_session_for_key(
        self, current_session: Optional[SessionItem], key: str
    ) -> bool:
        """Check if the current session matches the given key."""
        if not current_session or not current_session.is_ssh():
            return False
        return self._get_session_identifier(current_session) == key

    def _notify_rsync_missing_if_needed(self, key: str) -> None:
        """Show notification if rsync is missing and not already notified."""
        if key not in self._rsync_notified_sessions:
            self.logger.info(
                f"rsync not detected on remote session {key}. Prompting user."
            )
            self._show_rsync_missing_notification()
            self._rsync_notified_sessions.add(key)

    def _finish_rebinding(self) -> bool:
        self._is_rebinding = False
        return GLib.SOURCE_REMOVE

    def unbind(self):
        """Unbinds from the current terminal, effectively pausing updates."""
        if (
            self.bound_terminal
            and self.directory_change_handler_id > 0
            and GObject.signal_handler_is_connected(
                self.bound_terminal, self.directory_change_handler_id
            )
        ):
            self.bound_terminal.disconnect(self.directory_change_handler_id)
        self.bound_terminal = None
        self.directory_change_handler_id = 0
        self.logger.info("File manager unbound from terminal.")

    def shutdown(self, widget):
        self.logger.info("Shutting down FileManager, cancelling active transfers.")

        if self.settings_manager is not None and self.settings_manager.get(
            "clear_remote_edit_files_on_exit", True
        ):
            self.logger.info(
                "Clearing all temporary remote edit files for this file manager instance."
            )
            self.cleanup_all_temp_files()

        if hasattr(
            self, "temp_files_changed_handler_id"
        ) and GObject.signal_handler_is_connected(
            self, self.temp_files_changed_handler_id
        ):
            self.disconnect(self.temp_files_changed_handler_id)
            del self.temp_files_changed_handler_id

        if self.transfer_manager:
            for transfer_id in self.transfer_manager.active_transfers.copy():
                self.transfer_manager.cancel_transfer(transfer_id)

        if self.transfer_history_window:
            self.transfer_history_window.destroy()
            self.transfer_history_window = None

        if self.operations:
            self.operations.shutdown()

        self.unbind()

    def destroy(self):
        """
        Explicitly destroys the FileManager and its components to break reference cycles.
        """
        if self._is_destroyed:
            return
        self._is_destroyed = True
        self.logger.info("Destroying FileManager instance to prevent memory leaks.")
        self.shutdown(None)

        self._cleanup_file_monitors()
        self._cleanup_edited_metadata()
        self._cleanup_model_references()
        self._cleanup_data_stores()
        self._nullify_references()
        self.logger.info("FileManager destroyed.")

    def _cleanup_file_monitors(self) -> None:
        """Cancel and clear all file monitors."""
        if not hasattr(self, "file_monitors") or not self.file_monitors:
            return
        for monitor in self.file_monitors.values():
            if monitor:
                monitor.cancel()
        self.file_monitors.clear()

    def _cleanup_edited_metadata(self) -> None:
        """Clear edited file metadata."""
        if hasattr(self, "edited_file_metadata"):
            self.edited_file_metadata.clear()

    def _cleanup_model_references(self) -> None:
        """Detach model from view and clear model wrappers."""
        if hasattr(self, "column_view") and self.column_view:
            self.column_view.set_model(None)

        if hasattr(self, "selection_model"):
            self.selection_model = None
        if hasattr(self, "sorted_store"):
            self.sorted_store = None
        if hasattr(self, "filtered_store"):
            self.filtered_store = None

    def _cleanup_data_stores(self) -> None:
        """Clear data store and scrolled window."""
        if hasattr(self, "store") and self.store:
            self.store.remove_all()
            self.store = None  # type: ignore[assignment]

        if hasattr(self, "scrolled_window") and self.scrolled_window:
            self.scrolled_window = None  # type: ignore[assignment]

    def _nullify_references(self) -> None:
        """Nullify references to break Python-side cycles."""
        self._parent_window_ref = None  # type: ignore[assignment]
        self._terminal_manager_ref = None  # type: ignore[assignment]
        self.settings_manager = None
        self.operations = None
        self.transfer_manager = None  # type: ignore[assignment]
        self.column_view = None
        self.main_box = None
        self.revealer = None
        self.bound_terminal = None
        self.session_item = None

    def get_temp_files_info(self) -> List[Dict]:
        """Returns information about currently edited temporary files."""
        return list(self.edited_file_metadata.values())

    def cleanup_all_temp_files(self, key_to_clear: Optional[tuple] = None):
        """
        Cleans up temporary files. If a specific key is provided, only that
        file is cleaned. Otherwise, all temporary files are cleaned.
        """
        if key_to_clear:
            self._cleanup_edited_file(key_to_clear)
        else:
            for key in self.edited_file_metadata.copy():
                self._cleanup_edited_file(key)

    def _get_terminal_current_directory(self):
        if not self.bound_terminal:
            return None
        try:
            uri = self.bound_terminal.get_current_directory_uri()
            if uri:
                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    return unquote(parsed_uri.path)
        except Exception as e:
            self.logger.debug(f"Could not get terminal directory URI: {e}")
        return None

    def _get_default_directory_for_session(self) -> str:
        """
        Returns a sensible default directory when OSC7 tracking is not available.
        For local sessions, returns the user's home directory.
        For SSH sessions, queries the remote home directory.
        """
        if not self.session_item:
            return os.path.expanduser("~")

        if self.session_item.is_local():
            return os.path.expanduser("~")
        else:
            # For SSH sessions, query the remote home directory
            if self.operations:
                success, output = self.operations.execute_command_on_session(
                    [
                        "echo",
                        "$HOME",
                    ]
                )
                if success and output.strip():
                    return output.strip()
            # Fallback to root if we can't determine the home directory
            return "/"

    def _on_terminal_directory_changed(self, _terminal, _param_spec):
        if self._is_rebinding:
            return

        try:
            uri = self.bound_terminal.get_current_directory_uri()
            if not uri:
                return

            parsed_uri = urlparse(uri)
            if parsed_uri.scheme != "file":
                return

            new_path = unquote(parsed_uri.path)

            if not os.path.isabs(new_path):
                self.logger.warning(
                    f"Received relative path from terminal: {new_path}. Resolving against current path: {self.current_path}"
                )
                new_path = os.path.normpath(os.path.join(self.current_path, new_path))

            # Event-driven check for our pending 'cd' command
            if (
                self._pending_command
                and self._pending_command["type"] == "cd"
                and new_path == self._pending_command["path"]
            ):
                self.logger.info(f"Programmatic CD to '{new_path}' confirmed.")
                self._confirm_pending_command()

            if new_path != self.current_path:
                source = "filemanager" if self._fm_initiated_cd else "terminal"
                self.refresh(new_path, source=source)
        except Exception as e:
            self.logger.error(f"Failed to handle terminal directory change: {e}")

    def get_main_widget(self):
        return self.revealer

    def _build_ui(self):
        # Use NONE transition for instant show/hide
        # Note: GTK4 Revealer transitions reveal content within allocated space,
        # they don't slide the widget itself from screen edge
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.NONE,
        )
        self.revealer.set_size_request(-1, self.FILE_MANAGER_MIN_HEIGHT)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.set_size_request(-1, self.FILE_MANAGER_MIN_HEIGHT)
        self.main_box.add_css_class("file-manager-main-box")
        # Add background class to ensure solid background while loading
        self.main_box.add_css_class("background")

        self.scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        # Also add background to scrolled window to prevent transparency during load
        self.scrolled_window.add_css_class("background")

        self.store = Gio.ListStore.new(FileItem)
        self.filtered_store = Gtk.FilterListModel(model=self.store)

        self.column_view = self._create_detailed_column_view()
        a11y_label(self.column_view, _("File list"))
        self.scrolled_window.set_child(self.column_view)

        # Drop target for external files, attached to the stable ScrolledWindow
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("accept", self._on_drop_accept)
        drop_target.connect("enter", self._on_drop_enter, self.scrolled_window)
        drop_target.connect("leave", self._on_drop_leave, self.scrolled_window)
        drop_target.connect("drop", self._on_files_dropped, self.scrolled_window)
        self.scrolled_window.add_controller(drop_target)

        scrolled_bg_click = Gtk.GestureClick.new()
        scrolled_bg_click.set_button(Gdk.BUTTON_SECONDARY)
        scrolled_bg_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scrolled_bg_click.set_exclusive(True)
        scrolled_bg_click.connect("pressed", self._on_scrolled_window_background_click)
        self.scrolled_window.add_controller(scrolled_bg_click)

        self.action_bar = Gtk.ActionBar()

        refresh_button = icon_button("view-refresh-symbolic")
        refresh_button.connect("clicked", lambda _: self.refresh(source="filemanager"))
        self.tooltip_helper.add_tooltip(refresh_button, _("Refresh"))
        a11y_label(refresh_button, _("Refresh file list"))
        self.action_bar.pack_start(refresh_button)

        self.hidden_files_toggle = Gtk.ToggleButton()
        self.hidden_files_toggle.set_child(icon_image("view-visible-symbolic"))
        self.hidden_files_toggle.connect("toggled", self._on_hidden_toggle)
        a11y_label(self.hidden_files_toggle, _("Show hidden files"))
        self.tooltip_helper.add_tooltip(
            self.hidden_files_toggle, _("Show hidden files")
        )
        self.action_bar.pack_start(self.hidden_files_toggle)

        self.breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.breadcrumb_box.add_css_class("breadcrumb-trail")
        self.breadcrumb_box.set_hexpand(True)
        self.action_bar.pack_start(self.breadcrumb_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.add_css_class("file-manager-filter")
        self.search_entry.set_placeholder_text(_("Filter files..."))
        a11y_label(self.search_entry, _("Filter files"))
        self.search_entry.set_max_width_chars(12)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        self.search_entry.connect("delete-text", self._on_search_delete_text)

        # Search button for recursive search (visible when recursive mode is on)
        self.recursive_search_button = icon_button(
            "system-search-symbolic", use_bundled=False
        )  # System icon
        self.tooltip_helper.add_tooltip(
            self.recursive_search_button, _("Start Recursive Search")
        )
        a11y_label(self.recursive_search_button, _("Start recursive search"))
        self.recursive_search_button.set_valign(Gtk.Align.CENTER)
        self.recursive_search_button.connect(
            "clicked", self._on_recursive_search_button_clicked
        )
        self.recursive_search_button.set_visible(False)

        # Cancel button with spinner for ongoing recursive search
        self.recursive_search_cancel_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        self.recursive_search_cancel_box.set_valign(Gtk.Align.CENTER)

        self.recursive_search_spinner = Gtk.Spinner()
        self.recursive_search_spinner.set_size_request(16, 16)
        self.recursive_search_cancel_box.append(self.recursive_search_spinner)

        self.recursive_search_cancel_button = icon_button("process-stop-symbolic")
        self.tooltip_helper.add_tooltip(
            self.recursive_search_cancel_button, _("Cancel Search")
        )
        a11y_label(self.recursive_search_cancel_button, _("Cancel search"))
        self.recursive_search_cancel_button.add_css_class("destructive-action")
        self.recursive_search_cancel_button.connect(
            "clicked", self._on_cancel_recursive_search
        )
        self.recursive_search_cancel_box.append(self.recursive_search_cancel_button)
        self.recursive_search_cancel_box.set_visible(False)

        # Recursive search toggle - using a compact Switch instead of SwitchRow
        self.recursive_search_switch = Gtk.Switch()
        self.recursive_search_switch.set_active(False)
        self.recursive_search_switch.set_valign(Gtk.Align.CENTER)
        a11y_label(self.recursive_search_switch, _("Search in subfolders"))
        self.recursive_search_switch.connect(
            "notify::active", self._on_recursive_switch_toggled
        )

        recursive_label = Gtk.Label(label=_("Search in subfolders"))
        recursive_label.set_valign(Gtk.Align.CENTER)
        recursive_label.add_css_class("dim-label")

        switch_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        switch_container.set_valign(Gtk.Align.CENTER)
        switch_container.append(recursive_label)
        switch_container.append(self.recursive_search_switch)
        switch_container.set_margin_start(6)
        self.action_bar.pack_end(switch_container)
        self.action_bar.pack_end(self.recursive_search_cancel_box)
        self.action_bar.pack_end(self.recursive_search_button)
        self.action_bar.pack_end(self.search_entry)

        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        search_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.search_entry.add_controller(search_key_controller)

        history_button = icon_button("view-history-symbolic")
        self.tooltip_helper.add_tooltip(history_button, _("Transfer History"))
        a11y_label(history_button, _("Transfer history"))
        history_button.connect("clicked", self._on_show_transfer_history)
        self.action_bar.pack_end(history_button)

        self.upload_button = icon_button("go-up-symbolic")
        self.tooltip_helper.add_tooltip(self.upload_button, _("Send Files"))
        a11y_label(self.upload_button, _("Send files"))
        self.upload_button.connect("clicked", self._on_upload_clicked)
        self.action_bar.pack_end(self.upload_button)

        progress_widget = self.transfer_manager.create_progress_widget()
        self.main_box.append(progress_widget)

        self.main_box.append(self.scrolled_window)
        self.main_box.append(self.action_bar)
        self.revealer.set_child(self.main_box)

        self._setup_filtering_and_sorting()

    def _apply_background_transparency(self):
        """Apply background transparency to the file manager."""
        try:
            # Get settings from parent window's settings manager
            if hasattr(self.parent_window, "settings_manager"):
                settings_manager = self.parent_window.settings_manager
                transparency = settings_manager.get("headerbar_transparency", 0)
                self.logger.info(f"File manager transparency: {transparency}")

                if transparency > 0:
                    # Calculate opacity using the same formula as terminal transparency
                    alpha = max(0.0, min(1.0, 1.0 - (transparency / 100.0) ** 1.6))
                    self.logger.info(f"Calculated alpha for file manager: {alpha}")

                    # Apply opacity directly to the revealer widget
                    self.revealer.set_opacity(alpha)
                    self.logger.info(
                        f"File manager opacity set to {alpha} using widget property"
                    )
                else:
                    # Reset to full opacity when transparency is 0
                    self.revealer.set_opacity(1.0)
                    self.logger.info(
                        "File manager transparency is 0, setting full opacity"
                    )
        except Exception as e:
            self.logger.warning(
                f"Failed to apply background transparency to file manager: {e}"
            )

    def _update_action_bar_for_session_type(self):
        """Shows or hides UI elements based on whether the session is remote."""
        is_remote = self._is_remote_session()
        self.upload_button.set_visible(is_remote)

    def _update_breadcrumb(self):
        child = self.breadcrumb_box.get_first_child()
        while child:
            self.breadcrumb_box.remove(child)
            child = self.breadcrumb_box.get_first_child()

        path = Path(self.current_path)

        if not path.parts or path.parts == ("/",):
            btn = Gtk.Button(label="/")
            btn.add_css_class("flat")
            btn.connect("clicked", self._on_breadcrumb_button_clicked, "/")
            a11y_label(btn, _("Navigate to root"))
            self.breadcrumb_box.append(btn)
            return

        accumulated_path = Path()
        for i, part in enumerate(path.parts):
            display_name = part if i > 0 else "/"
            if i == 0 and part == "/":
                accumulated_path = Path(part)
            else:
                accumulated_path = accumulated_path / part
                separator = Gtk.Label(label="›")
                separator.add_css_class("dim-label")
                self.breadcrumb_box.append(separator)

            btn = Gtk.Button(label=display_name)
            btn.add_css_class("flat")
            btn.connect(
                "clicked", self._on_breadcrumb_button_clicked, str(accumulated_path)
            )
            a11y_label(btn, _("Navigate to {}").format(display_name))
            self.breadcrumb_box.append(btn)

    def _on_breadcrumb_button_clicked(self, button, path_to_navigate):
        if path_to_navigate != self.current_path:
            if self.bound_terminal:
                self._fm_initiated_cd = True
                command = f'cd "{path_to_navigate}"\n'
                self.bound_terminal.feed_child(command.encode("utf-8"))
            else:
                self.refresh(path_to_navigate, source="filemanager")

    def _setup_filtering_and_sorting(self):
        self.combined_filter = Gtk.CustomFilter()
        self.combined_filter.set_filter_func(self._filter_files)
        self.filtered_store.set_filter(self.combined_filter)

    def _filter_files(self, file_item):
        search_text = getattr(self, "search_entry", None)
        search_term = search_text.get_text().lower().strip() if search_text else ""
        show_hidden = self.hidden_files_toggle.get_active()

        # Handle parent directory navigation
        if file_item.name == "..":
            return not search_term  # Show ".." only when not searching

        # Apply hidden file filter first
        if not show_hidden and self._is_hidden_file(file_item):
            return False

        # If not searching, show all (non-hidden handled above)
        if not search_term:
            return True

        # For recursive search results, always show (filtering already done)
        if self.recursive_search_enabled and self._showing_recursive_results:
            return True

        # Standard search: match against filename
        return search_term in file_item.name.lower()

    def _is_hidden_file(self, file_item) -> bool:
        """Check if a file should be considered hidden."""
        if self.recursive_search_enabled and self._showing_recursive_results:
            # For recursive results, check the last component of the path
            name_to_check = file_item.name.split("/")[-1]
            return name_to_check.startswith(".")
        return file_item.name.startswith(".")

    def _dolphin_sort_priority(
        self, file_item_a, file_item_b, secondary_sort_func=None
    ):
        if file_item_a.name == "..":
            return -1
        if file_item_b.name == "..":
            return 1

        def get_type(item):
            return 0 if item.is_directory_like else 1

        a_type = get_type(file_item_a)
        b_type = get_type(file_item_b)

        if a_type != b_type:
            return a_type - b_type

        if secondary_sort_func:
            return secondary_sort_func(file_item_a, file_item_b)

        name_a = file_item_a.name.lower()
        name_b = file_item_b.name.lower()
        return (name_a > name_b) - (name_a < name_b)

    def _sort_by_name(self, a, b, *_):
        return self._dolphin_sort_priority(a, b)

    def _sort_by_permissions(self, a, b, *_):
        return self._dolphin_sort_priority(
            a,
            b,
            lambda x, y: (
                (x.permissions > y.permissions) - (x.permissions < y.permissions)
            ),
        )

    def _sort_by_owner(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.owner > y.owner) - (x.owner < y.owner)
        )

    def _sort_by_group(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.group > y.group) - (x.group < y.group)
        )

    def _sort_by_size(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.size > y.size) - (x.size < y.size)
        )

    def _sort_by_date(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.date > y.date) - (x.date < y.date)
        )

    def _on_hidden_toggle(self, _toggle_button):
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _navigate_up_directory(self):
        """Navigate up one directory level, preserving user input.

        Returns False for GLib.idle_add callback compatibility.
        """
        if self._is_destroyed:
            return False
        if self.current_path == "/":
            return False

        parent_path = str(Path(self.current_path).parent)
        if self.bound_terminal:
            command = ["cd", parent_path]
            self._execute_verified_command(
                command, command_type="cd", expected_path=parent_path
            )
        else:
            if parent_path != self.current_path:
                self.refresh(parent_path, source="filemanager")
        return False

    def _deferred_activate_row(self, col_view, position):
        """Deferred row activation to allow focus events to be processed properly.

        Returns False for GLib.idle_add callback compatibility.
        """
        if self._is_destroyed:
            return False
        self._on_row_activated(col_view, position)
        return False

    def _create_column(self, title, sorter, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        factory.connect("unbind", self._unbind_cell)  # MODIFIED: Connect unbind
        column = Gtk.ColumnViewColumn(
            title=title, factory=factory, expand=expand, resizable=True
        )
        column.set_sorter(sorter)
        return column

    def get_selected_items(self) -> List[FileItem]:
        """Gets all selected items from the ColumnView."""
        items: list[FileItem] = []
        if not hasattr(self, "selection_model"):
            return items

        selection = self.selection_model.get_selection()
        size = selection.get_size()
        for i in range(size):
            position = selection.get_nth(i)
            if item := self.sorted_store.get_item(position):
                items.append(item)
        return items

    def _create_detailed_column_view(self) -> Gtk.ColumnView:
        col_view = Gtk.ColumnView()
        col_view.set_show_column_separators(True)
        col_view.set_show_row_separators(True)

        self.name_sorter = Gtk.CustomSorter.new(self._sort_by_name, None)
        self.size_sorter = Gtk.CustomSorter.new(self._sort_by_size, None)
        self.date_sorter = Gtk.CustomSorter.new(self._sort_by_date, None)
        self.perms_sorter = Gtk.CustomSorter.new(self._sort_by_permissions, None)
        self.owner_sorter = Gtk.CustomSorter.new(self._sort_by_owner, None)
        self.group_sorter = Gtk.CustomSorter.new(self._sort_by_group, None)

        col_view.append_column(
            self._create_column(
                _("Name"),
                self.name_sorter,
                self._setup_name_cell,
                self._bind_name_cell,
                expand=True,
            )
        )
        col_view.append_column(
            self._create_column(
                _("Size"), self.size_sorter, self._setup_size_cell, self._bind_size_cell
            )
        )
        col_view.append_column(
            self._create_column(
                _("Date Modified"),
                self.date_sorter,
                self._setup_text_cell,
                self._bind_date_cell,
            )
        )
        col_view.append_column(
            self._create_column(
                _("Permissions"),
                self.perms_sorter,
                self._setup_text_cell,
                self._bind_permissions_cell,
            )
        )
        col_view.append_column(
            self._create_column(
                _("Owner"),
                self.owner_sorter,
                self._setup_text_cell,
                self._bind_owner_cell,
            )
        )
        col_view.append_column(
            self._create_column(
                _("Group"),
                self.group_sorter,
                self._setup_text_cell,
                self._bind_group_cell,
            )
        )

        view_sorter = col_view.get_sorter()
        self.sorted_store = Gtk.SortListModel(
            model=self.filtered_store, sorter=view_sorter
        )
        self.selection_model = Gtk.MultiSelection(model=self.sorted_store)
        col_view.set_model(self.selection_model)
        col_view.sort_by_column(
            col_view.get_columns().get_item(0), Gtk.SortType.ASCENDING
        )

        col_view.connect("activate", self._on_row_activated)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_column_view_key_pressed)
        key_controller.connect("key-released", self._on_column_view_key_released)
        col_view.add_controller(key_controller)

        background_click = Gtk.GestureClick.new()
        background_click.set_button(Gdk.BUTTON_SECONDARY)
        background_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        background_click.set_exclusive(True)
        background_click.connect("pressed", self._on_column_view_background_click)
        col_view.add_controller(background_click)

        return col_view

    def _setup_name_cell(self, factory, list_item):
        box = Gtk.Box(spacing=6, orientation=Gtk.Orientation.HORIZONTAL)
        box.append(Gtk.Image())
        label = Gtk.Label(xalign=0.0)
        box.append(label)
        link_icon = Gtk.Image()
        link_icon.set_visible(False)
        box.append(link_icon)
        list_item.set_child(box)

    def _bind_cell_common(self, list_item):
        """Common logic for binding cells, including adding the right-click gesture."""
        row = list_item.get_child().get_parent()
        if row and not hasattr(row, "right_click_gesture"):
            right_click_gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            right_click_gesture.connect(
                "released", self._on_item_right_click, list_item
            )
            row.add_controller(right_click_gesture)
            row.right_click_gesture = right_click_gesture

    def _unbind_cell(self, factory, list_item):
        """Disconnects handlers to prevent memory leaks."""
        row = list_item.get_child().get_parent()
        if row and hasattr(row, "right_click_gesture"):
            row.remove_controller(row.right_click_gesture)
            delattr(row, "right_click_gesture")

    def _bind_name_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        box = list_item.get_child()
        icon = box.get_first_child()
        label = icon.get_next_sibling()
        link_icon = label.get_next_sibling()
        file_item: FileItem = list_item.get_item()
        icon.set_from_icon_name(file_item.icon_name)
        display_name = file_item.name
        if file_item.is_directory and display_name.endswith("/"):
            display_name = display_name[:-1]
        label.set_text(display_name)
        if file_item.is_link:
            link_icon.set_from_icon_name("emblem-symbolic-link-symbolic")
            link_icon.set_visible(True)
        else:
            link_icon.set_visible(False)

    def _setup_text_cell(self, factory, list_item):
        label = Gtk.Label(xalign=0.0)
        list_item.set_child(label)

    def _setup_size_cell(self, factory, list_item):
        label = Gtk.Label(xalign=1.0)
        list_item.set_child(label)

    def _bind_permissions_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.permissions)

    def _bind_owner_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.owner)

    def _bind_group_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.group)

    def _bind_size_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        size = file_item.size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024**2:
            size_str = f"{size / 1024:.1f} KB"
        elif size < 1024**3:
            size_str = f"{size / 1024**2:.1f} MB"
        else:
            size_str = f"{size / 1024**3:.1f} GB"
        label.set_text(size_str)

    def _bind_date_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        date_str = file_item.date.strftime("%Y-%m-%d %H:%M")
        label.set_text(date_str)

    def _confirm_pending_command(self):
        """
        Confirms a pending command was successful and restores user input, as per the new rule.
        """
        if self._command_timeout_id > 0:
            GLib.source_remove(self._command_timeout_id)
            self._command_timeout_id = 0

        # ALWAYS restore the user's input on completion, success or failure.
        if self.bound_terminal:
            self.bound_terminal.feed_child(b"\x19")  # CTRL+Y (Yank)

        self._pending_command = None

    def _execute_verified_command(
        self,
        command_list: List[str],
        command_type: str,
        expected_path: Optional[str] = None,
    ):
        """
        Executes a command in the terminal, preserving user input and verifying
        its completion via a timeout and a subsequent confirmation event.
        """
        if not self.bound_terminal:
            return

        # Clean up any previous pending operation
        if self._command_timeout_id > 0:
            GLib.source_remove(self._command_timeout_id)

        command_str = shlex.join(command_list)

        # Set state for the new operation
        self._pending_command = {"type": command_type, "str": command_str}
        if command_type == "cd":
            self._pending_command["path"] = expected_path

        # Preserve user input by cutting it
        self.bound_terminal.feed_child(b"\x01")  # CTRL+A: Beginning of line
        self.bound_terminal.feed_child(b"\x0b")  # CTRL+K: Kill to end of line

        # Send command
        self.bound_terminal.feed_child(f"{command_str}\n".encode("utf-8"))

        # For non-cd commands, success is confirmed by the refresh completing
        if command_type != "cd":
            GLib.timeout_add(15, lambda: self.refresh(source="filemanager"))

    def _on_row_activated(self, col_view, position):
        item: FileItem = col_view.get_model().get_item(position)
        if not item:
            return

        if item.is_directory_like:
            self._navigate_to_directory(item)
        else:
            self._open_file(item)

    def _navigate_to_directory(self, item: FileItem):
        """Handle navigation to a directory."""
        new_path = self._compute_navigation_path(item)
        if not new_path:
            return

        if self.bound_terminal:
            self._fm_initiated_cd = True
            self._execute_verified_command(
                ["cd", new_path], command_type="cd", expected_path=new_path
            )
        self.refresh(new_path, source="filemanager")

    def _compute_navigation_path(self, item: FileItem) -> str:
        """Compute the new path for directory navigation."""
        if item.name == "..":
            if self.current_path == "/":
                return ""
            return str(Path(self.current_path).parent)
        base_path = self.current_path.rstrip("/")
        return f"{base_path}/{item.name}"

    def _open_file(self, item: FileItem):
        """Handle opening a file."""
        if self._is_remote_session():
            self._on_open_edit_action(None, None, [item])
        else:
            full_path = Path(self.current_path).joinpath(item.name)
            self._open_local_file(full_path)

    def set_visibility(self, visible: bool, source: str = "filemanager"):
        self.revealer.set_reveal_child(visible)
        if visible:
            self.refresh(source=source)
            self._apply_background_transparency()
            if source == "filemanager":
                self.column_view.grab_focus()
        else:
            if self.bound_terminal:
                self.bound_terminal.grab_focus()

    def refresh(
        self,
        path: str | None = None,
        source: str = "filemanager",
        clear_search: bool = True,
    ):
        if hasattr(self, "search_entry") and clear_search:
            self.search_entry.set_text("")
        if path:
            self.current_path = path
        self._update_breadcrumb()
        if self.store:
            self.store.remove_all()

        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(False)
            self.search_entry.set_placeholder_text(_("Loading..."))

        # Use global AsyncTaskManager for I/O-bound file listing
        AsyncTaskManager.get().submit_io(
            self._list_files_thread, self.current_path, source
        )

    def _list_files_thread(self, requested_path: str, source: str = "filemanager"):
        """Task 1: UI Batching - Process files in batches to avoid UI freezing.

        Uses a short timeout to prevent UI freeze when SSH connection is lost.
        """
        try:
            if self._is_destroyed:
                return

            operations = self.operations
            if not operations:
                self._schedule_update_with_error(
                    requested_path, "Operations not initialized", source
                )
                return

            path_for_ls = self._normalize_path_for_ls(requested_path)
            command = ["ls", "-la", "--classify", "--full-time", path_for_ls]
            success, output = operations.execute_command_on_session(command, timeout=8)

            if not success:
                self._handle_list_error(requested_path, output, source)
                return

            all_items = self._parse_ls_output(output, requested_path)
            GLib.idle_add(self._set_store_items, all_items, requested_path, source)

        except Exception as e:
            self.logger.error(f"Error in background file listing: {e}")
            self._schedule_update_with_error(requested_path, str(e), source)

    def _normalize_path_for_ls(self, path: str) -> str:
        """Ensure path ends with slash for ls command."""
        return path if path.endswith("/") else f"{path}/"

    def _schedule_update_with_error(self, path: str, error: str, source: str) -> None:
        """Schedule an error update on the main thread."""
        GLib.idle_add(self._update_store_with_files, path, [], error, source)

    def _handle_list_error(self, requested_path: str, output: str, source: str) -> None:
        """Handle errors from ls command."""
        is_connection_error = self._is_connection_error(output)

        if is_connection_error:
            self.logger.warning(
                f"Connection issue while listing '{requested_path}': {output}"
            )
            error_msg = _("Connection lost. Please check your network connection.")
        else:
            self.logger.warning(
                f"Failed to list '{requested_path}': {output}. Reverting to last successful path."
            )
            error_msg = _(
                "Could not list directory contents. The path may not exist or you may not have permission."
            )

        if self._should_fallback(is_connection_error, requested_path):
            GLib.idle_add(
                self._fallback_to_accessible_path,
                self._last_successful_path,
                source,
            )
        else:
            self._schedule_update_with_error(requested_path, error_msg, source)

    def _is_connection_error(self, output: str) -> bool:
        """Check if output indicates a connection error."""
        lower = output.lower()
        return any(
            term in lower
            for term in ["timed out", "timeout", "connection", "network", "unreachable"]
        )

    def _should_fallback(self, is_connection_error: bool, requested_path: str) -> bool:
        """Determine if we should fallback to last successful path."""
        return bool(
            not is_connection_error
            and self._last_successful_path
            and self._last_successful_path != requested_path
        )

    def _parse_ls_output(self, output: str, requested_path: str) -> list:
        """Parse ls output and return sorted file items."""
        lines = output.strip().split("\n")[1:]  # Skip total line
        directories = []
        files = []
        parent_item = None

        for line in lines:
            if self._is_destroyed or requested_path != self.current_path:
                return []

            file_item = FileItem.from_ls_line(line)
            if not file_item:
                continue

            if file_item.name == "..":
                parent_item = file_item
            elif file_item.name not in [".", ".."]:
                self._resolve_link_target(file_item, requested_path)
                if file_item.is_directory_like:
                    directories.append(file_item)
                else:
                    files.append(file_item)

        directories.sort(key=lambda x: x.name.lower())
        files.sort(key=lambda x: x.name.lower())

        all_items = []
        if requested_path != "/" and parent_item:
            all_items.append(parent_item)
        all_items.extend(directories)
        all_items.extend(files)
        return all_items

    def _resolve_link_target(self, file_item: FileItem, base_path: str) -> None:
        """Resolve relative symlink targets to absolute paths."""
        if file_item.is_link and file_item._link_target:
            if not file_item._link_target.startswith("/"):
                file_item._link_target = (
                    f"{base_path.rstrip('/')}/{file_item._link_target}"
                )

    def _set_store_items(self, items, requested_path, source):
        """Set all store items in a single operation for optimal performance.

        GTK4's ColumnView uses virtual scrolling (only visible rows are rendered),
        so adding all items at once is more efficient than batching.

        Returns False for GLib.idle_add callback compatibility.
        """
        if self._is_destroyed:
            return False

        # Verify we're still on the same path
        if requested_path != self.current_path:
            self.logger.info(
                f"Discarding stale file list for '{requested_path}'. Current path is '{self.current_path}'."
            )
            return False

        if self.store is not None:
            # Single splice replaces all items - more efficient than multiple operations
            self.store.splice(0, self.store.get_n_items(), items)

        # Track this as the last successfully listed path (for permission denied fallback)
        self._last_successful_path = requested_path

        self._showing_recursive_results = False
        self._recursive_search_in_progress = False
        self._restore_search_entry(source)
        return False

    def _update_store_with_files(
        self,
        requested_path: str,
        file_items,
        error_message,
        source: str = "filemanager",
    ):
        """Update store with file items after listing.

        Returns False for GLib.idle_add callback compatibility.
        """
        # Skip if destroyed
        if self._is_destroyed:
            return False

        if requested_path != self.current_path:
            self.logger.info(
                f"Discarding stale file list for '{requested_path}'. Current path is '{self.current_path}'."
            )
            return False

        if error_message:
            self.logger.error(f"Error listing files: {error_message}")

        if self.store is not None:
            self.store.splice(0, self.store.get_n_items(), file_items)
        self._showing_recursive_results = False
        self._recursive_search_in_progress = False

        # If a non-cd command was pending, the completion of the refresh confirms it.
        if self._pending_command and self._pending_command["type"] != "cd":
            self.logger.info(
                f"Command '{self._pending_command['str']}' confirmed by successful refresh."
            )
            self._confirm_pending_command()

        self._restore_search_entry(source)
        return False

    def _fallback_to_accessible_path(self, fallback_path: str, source: str):
        """Navigate to an accessible fallback path when permission denied on current path.

        Returns False for GLib.idle_add callback compatibility.
        """
        if self._is_destroyed:
            return False
        self.logger.info(f"Switching file manager to accessible path: {fallback_path}")
        self.current_path = fallback_path
        self._update_breadcrumb()
        # Re-list the fallback directory using global AsyncTaskManager
        AsyncTaskManager.get().submit_io(self._list_files_thread, fallback_path, source)
        return False

    def _update_search_placeholder(self, override: Optional[str] = None) -> None:
        if not hasattr(self, "search_entry"):
            return
        if override is not None:
            self.search_entry.set_placeholder_text(override)
            return
        if self._recursive_search_in_progress:
            self.search_entry.set_placeholder_text(_("Searching..."))
        elif self.recursive_search_enabled:
            self.search_entry.set_placeholder_text(_("Type and press Enter..."))
        else:
            self.search_entry.set_placeholder_text(_("Filter files..."))

    def _restore_search_entry(self, source: str = "filemanager"):
        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(True)
            self._update_search_placeholder()

        if hasattr(self, "combined_filter"):
            self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
        if hasattr(self, "sorted_store"):
            sorter = self.sorted_store.get_sorter()
            if sorter:
                sorter.changed(Gtk.SorterChange.DIFFERENT)
        if hasattr(self, "column_view") and self.column_view:
            if self.selection_model and self.selection_model.get_n_items() > 0:
                self.selection_model.select_item(0, True)
                self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
                if source == "filemanager":
                    self.column_view.grab_focus()
        return False

    def _is_remote_session(self) -> bool:
        return bool(self.session_item and not self.session_item.is_local())

    def _should_show_general_menu(self, list_item: Any, position: int) -> bool:
        """Check if general context menu should be shown instead of item menu."""
        if not isinstance(list_item, Gtk.ListItem):
            return True
        if position == Gtk.INVALID_LIST_POSITION:
            return True
        if self.selection_model is None:
            return True
        if position >= self.selection_model.get_n_items():
            return True
        return False

    def _handle_item_selection(self, position: int) -> list:
        """Handle item selection and return actionable items."""
        if not self.selection_model.is_selected(position):
            self.selection_model.unselect_all()
            self.selection_model.select_item(position, True)
        selected_items = self.get_selected_items()
        if not selected_items:
            return []
        return [item for item in selected_items if item.name != ".."]

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        try:
            row = gesture.get_widget()
            if not row:
                self._show_general_context_menu(x, y)
                return

            try:
                translated_x, translated_y = row.translate_coordinates(
                    self.column_view, x, y
                )
            except TypeError:
                translated_x, translated_y = x, y

            position = (
                list_item.get_position()
                if isinstance(list_item, Gtk.ListItem)
                else Gtk.INVALID_LIST_POSITION
            )

            if self._should_show_general_menu(list_item, position):
                self._show_general_context_menu(translated_x, translated_y)
                return

            actionable_items = self._handle_item_selection(position)
            if actionable_items:
                self._show_context_menu(actionable_items, translated_x, translated_y)
            else:
                self._show_general_context_menu(translated_x, translated_y)
        except Exception as e:
            self.logger.error(f"Error in right-click handler: {e}")

    def _on_column_view_background_click(self, gesture, n_press, x, y):
        try:
            target = self.column_view.pick(int(x), int(y), Gtk.PickFlags.DEFAULT)
            css_name = target.get_css_name() if isinstance(target, Gtk.Widget) else None
            self.logger.info(
                f"ColumnView background click at ({x}, {y}) target={type(target).__name__ if target else None} css={css_name}"
            )

            is_row_target = False
            widget = target if isinstance(target, Gtk.Widget) else None
            while widget:
                css = widget.get_css_name()
                if css in {"columnviewrow", "listitem", "row"}:
                    is_row_target = True
                    break
                widget = widget.get_parent()

            if is_row_target:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return

            if self.selection_model:
                self.selection_model.unselect_all()

            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_general_context_menu(x, y)
        except Exception as e:
            self.logger.error(f"Error in background right-click handler: {e}")

    def _on_scrolled_window_background_click(self, gesture, n_press, x, y):
        try:
            widget = gesture.get_widget()
            tx, ty = x, y
            if widget:
                try:
                    translated = widget.translate_coordinates(self.column_view, x, y)
                    if translated:
                        tx, ty = translated
                except Exception as e:
                    self.logger.debug(f"Coordinate translation failed: {e}")

            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._on_column_view_background_click(gesture, n_press, tx, ty)
        except Exception as e:
            self.logger.error(
                f"Error in scrolled window background right-click handler: {e}"
            )

    def _show_general_context_menu(self, x, y):
        menu = Gio.Menu()

        creation_section = Gio.Menu()
        creation_section.append(_("Create Folder"), "context.create_folder")
        creation_section.append(_("Create File"), "context.create_file")
        menu.append_section(None, creation_section)

        if self._can_paste():
            clipboard_section = Gio.Menu()
            clipboard_section.append(_("Paste"), "context.paste")
            menu.append_section(None, clipboard_section)

        popover = create_themed_popover_menu(menu, self.main_box)

        # Keep reference to prevent GC
        self._active_popover = popover
        popover.connect("closed", lambda *_: setattr(self, "_active_popover", None))

        self._setup_general_context_actions(popover)

        # Translate coordinates from column_view to main_box
        point = Graphene.Point()
        point.x, point.y = x, y
        success, translated = self.column_view.compute_point(self.main_box, point)
        if success:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = (
                int(translated.x),
                int(translated.y),
                1,
                1,
            )
            popover.set_pointing_to(rect)
        else:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
        popover.popup()

    def _show_context_menu(self, items: List[FileItem], x, y):
        menu_model = self._create_context_menu_model(items)
        popover = create_themed_popover_menu(menu_model, self.main_box)

        # Keep reference to prevent GC
        self._active_popover = popover
        popover.connect("closed", lambda *_: setattr(self, "_active_popover", None))

        self._setup_context_actions(popover, items)

        # Translate coordinates from column_view to main_box
        point = Graphene.Point()
        point.x, point.y = x, y
        success, translated = self.column_view.compute_point(self.main_box, point)
        if success:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = (
                int(translated.x),
                int(translated.y),
                1,
                1,
            )
            popover.set_pointing_to(rect)
        else:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
        popover.popup()

    def _on_search_key_pressed(self, controller, keyval, _keycode, state):
        """Handle key presses on the search entry for list navigation."""
        if not self.selection_model:
            return Gdk.EVENT_PROPAGATE

        current_pos = self._get_current_selection_position()

        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            return self._handle_arrow_key_navigation(keyval, current_pos)

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return self._handle_enter_key_in_search(current_pos)

        if keyval == Gdk.KEY_BackSpace:
            return self._handle_backspace_in_search(controller)

        return Gdk.EVENT_PROPAGATE

    def _get_current_selection_position(self):
        """Get the current selection position in the list."""
        selection = self.selection_model.get_selection()
        if selection.get_size() > 0:
            return selection.get_nth(0)
        return Gtk.INVALID_LIST_POSITION

    def _handle_arrow_key_navigation(self, keyval, current_pos):
        """Handle Up/Down arrow key navigation."""
        if current_pos == Gtk.INVALID_LIST_POSITION:
            new_pos = 0
        else:
            delta = -1 if keyval == Gdk.KEY_Up else 1
            new_pos = current_pos + delta

        if 0 <= new_pos < self.sorted_store.get_n_items():
            self.selection_model.select_item(new_pos, True)
            self.column_view.scroll_to(new_pos, None, Gtk.ListScrollFlags.NONE, None)

        return Gdk.EVENT_STOP

    def _handle_enter_key_in_search(self, current_pos):
        """Handle Enter key in search entry."""
        if self.recursive_search_enabled and not self._showing_recursive_results:
            return Gdk.EVENT_PROPAGATE

        if current_pos != Gtk.INVALID_LIST_POSITION:
            self._on_row_activated(self.column_view, current_pos)
        return Gdk.EVENT_STOP

    def _handle_backspace_in_search(self, controller):
        """Handle Backspace key in search entry."""
        if not self.search_entry.get_text().strip():
            controller.stop_emission("key-pressed")
            self._navigate_up_directory()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_column_view_key_pressed(self, controller, keyval, _keycode, state):
        """Handle key presses on the column view for instant filtering."""
        unicode_val = Gdk.keyval_to_unicode(keyval)
        if unicode_val != 0:
            char = chr(unicode_val)
            if char.isprintable():
                self.search_entry.set_text(char)
                self.search_entry.set_position(-1)
                self.search_entry.grab_focus()
                return Gdk.EVENT_STOP

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if (
                self.selection_model
                and self.selection_model.get_selection().get_size() > 0
            ):
                pos = self.selection_model.get_selection().get_nth(0)
                self._on_row_activated(self.column_view, pos)
                return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_BackSpace:
            if not self.search_entry.get_text().strip():
                self._navigate_up_directory()
                return Gdk.EVENT_STOP

        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            selected_items = [
                item for item in self.get_selected_items() if item.name != ".."
            ]
            if selected_items:
                self._on_delete_action(None, None, selected_items)
                return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_Menu or (
            keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK
        ):
            selected_items = self.get_selected_items()
            if selected_items:
                self._show_context_menu(selected_items, 0, 0)
            else:
                self._show_general_context_menu(0, 0)
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_column_view_key_released(self, controller, keyval, _keycode, state):
        """Handle key releases on the column view for context menu."""
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            selected_items = self.get_selected_items()
            if selected_items:
                self._show_context_menu(selected_items, 0, 0)
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _create_context_menu_model(self, items: List[FileItem]):
        menu = Gio.Menu()
        num_items = len(items)

        # Open/Edit section for single files
        if num_items == 1 and not items[0].is_directory:
            open_section = Gio.Menu()
            open_section.append(_("Open/Edit"), "context.open_edit")
            open_section.append(_("Open With..."), "context.open_with")
            menu.append_section(None, open_section)

        # Rename section for single items
        if num_items == 1:
            rename_section = Gio.Menu()
            rename_section.append(_("Rename"), "context.rename")
            menu.append_section(None, rename_section)

        # Clipboard section
        clipboard_section = Gio.Menu()
        clipboard_section.append(_("Copy"), "context.copy")
        clipboard_section.append(_("Cut"), "context.cut")
        if self._can_paste():
            clipboard_section.append(_("Paste"), "context.paste")
        menu.append_section(None, clipboard_section)

        # Download section for remote sessions
        if self._is_remote_session():
            download_section = Gio.Menu()
            download_section.append(_("Download"), "context.download")
            menu.append_section(None, download_section)

        # Permissions section
        permissions_section = Gio.Menu()
        permissions_section.append(_("Permissions"), "context.chmod")
        menu.append_section(None, permissions_section)

        # Delete section
        delete_section = Gio.Menu()
        delete_item = Gio.MenuItem.new(_("Delete"), "context.delete")
        delete_item.set_attribute_value(
            "class", GLib.Variant("s", "destructive-action")
        )
        delete_section.append_item(delete_item)
        menu.append_section(None, delete_section)

        return menu

    def _setup_action_group(
        self,
        popover,
        actions: dict,
        group_name: str = "context",
        items: List[FileItem] | None = None,
    ):
        """Generic helper to setup action groups with callbacks.

        Args:
            popover: The popover to attach the action group to
            actions: Dict mapping action names to callbacks
            group_name: Name of the action group (default: "context")
            items: Optional list of FileItem objects to pass to callbacks
        """
        action_group = Gio.SimpleActionGroup()
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            if name == "paste":
                action.set_enabled(self._can_paste())
                action.connect("activate", lambda a, _, cb=callback: cb())
            elif items is not None:
                action.connect(
                    "activate",
                    lambda a, _, cb=callback, itms=list(items): cb(a, _, itms),
                )
            else:
                action.connect("activate", lambda a, _, cb=callback: cb())
            action_group.add_action(action)
        popover.insert_action_group(group_name, action_group)

    def _setup_context_actions(self, popover, items: List[FileItem]):
        actions = {
            "open_edit": self._on_open_edit_action,
            "open_with": self._on_open_with_action,
            "rename": self._on_rename_action,
            "copy": self._on_copy_action,
            "cut": self._on_cut_action,
            "paste": self._on_paste_action,
            "chmod": self._on_chmod_action,
            "download": self._on_download_action,
            "delete": self._on_delete_action,
        }
        self._setup_action_group(popover, actions, "context", items)

    def _on_create_folder_action(self, *_args):
        base_path = PurePosixPath(self.current_path or "/")

        def create_folder(name: str):
            target_path = str(base_path / name)
            command = ["mkdir", "-p", target_path]
            self._execute_verified_command(command, command_type="mkdir")
            self._show_toast(_("Create folder command sent to terminal"))

        self._prompt_for_new_item(
            heading=_("Create Folder"),
            body=_("Enter a name for the new folder:"),
            default_name=_("New Folder"),
            confirm_label=_("Create"),
            callback=create_folder,
        )

    def _on_create_file_action(self, *_args):
        base_path = PurePosixPath(self.current_path or "/")

        def create_file(name: str):
            target_path = str(base_path / name)
            command = ["touch", target_path]
            self._execute_verified_command(command, command_type="touch")
            self._show_toast(_("Create file command sent to terminal"))

        self._prompt_for_new_item(
            heading=_("Create File"),
            body=_("Enter a name for the new file:"),
            default_name=_("New File"),
            confirm_label=_("Create"),
            callback=create_file,
        )

    def _set_clipboard_operation(
        self, items: List[FileItem], operation: str, toast_message: str
    ):
        """Generic helper to set clipboard items for copy/cut operations.

        Args:
            items: List of FileItem objects to add to clipboard
            operation: Either "copy" or "cut"
            toast_message: Message to display in toast notification
        """
        selectable_items = [item for item in items if item.name != ".."]
        if not selectable_items:
            self._show_toast(
                _("No items selected to {operation}.").format(operation=operation)
            )
            return

        base_path = PurePosixPath(self.current_path or "/")
        self._clipboard_items = [
            {
                "name": item.name,
                "path": str(base_path / item.name),
                "is_directory": item.is_directory,
            }
            for item in selectable_items
        ]
        self._clipboard_operation = operation
        self._clipboard_session_key = self._get_current_session_key()
        self._show_toast(toast_message)

    def _on_copy_action(self, _action, _param, items: List[FileItem]):
        self._set_clipboard_operation(items, "copy", _("Items copied to clipboard."))

    def _on_cut_action(self, _action, _param, items: List[FileItem]):
        self._set_clipboard_operation(items, "cut", _("Items marked for move."))

    def _on_paste_action(self):
        if not self._can_paste():
            self._show_toast(_("Nothing to paste."))
            return

        destination_dir = str(PurePosixPath(self.current_path or "/"))
        sources = [entry["path"] for entry in self._clipboard_items]

        if self._clipboard_operation == "cut":
            if all(
                str(PurePosixPath(source).parent) == destination_dir
                for source in sources
            ):
                self._show_toast(_("Items are already in this location."))
                return
            command = ["mv"] + sources + [destination_dir]
            command_type = "mv"
            toast_message = _("Move command sent to terminal")
            self._clear_clipboard()
        else:
            command = ["cp", "-a"] + sources + [destination_dir]
            command_type = "cp"
            toast_message = _("Copy command sent to terminal")

        self._execute_verified_command(command, command_type=command_type)
        self._show_toast(toast_message)

    def _setup_general_context_actions(self, popover):
        actions = {
            "create_folder": self._on_create_folder_action,
            "create_file": self._on_create_file_action,
            "paste": self._on_paste_action,
        }
        self._setup_action_group(popover, actions, "context")

    def _on_delete_action(self, _action, _param, items: List[FileItem]):
        count = len(items)
        if count == 1:
            title = _("Delete File")
            body = _(
                "Are you sure you want to permanently delete '{name}'?\n\nThis action cannot be undone."
            ).format(name=items[0].name)
        else:
            title = _("Delete Multiple Items")
            body = _(
                "Are you sure you want to permanently delete these {count} items?\n\nThis action cannot be undone."
            ).format(count=count)

        dialog = Adw.AlertDialog(heading=title, body=body, close_response="cancel")
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_dialog_response, items)
        dialog.present(self.parent_window)

    def _on_delete_dialog_response(self, dialog, response, items: List[FileItem]):
        if response == "delete":
            paths_to_delete = [
                f"{self.current_path.rstrip('/')}/{item.name}" for item in items
            ]
            command = ["rm", "-rf"] + paths_to_delete
            self._execute_verified_command(command, command_type="rm")
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Delete command sent to terminal"))
            )

    def _on_chmod_action(self, _action, _param, items: List[FileItem]):
        self._show_permissions_dialog(items)

    def _show_permissions_dialog(self, items: List[FileItem]):
        is_multi = len(items) > 1
        title = (
            _("Set Permissions for {count} Items").format(count=len(items))
            if is_multi
            else _("Permissions for {name}").format(name=items[0].name)
        )
        current_perms = "" if is_multi else items[0].permissions
        body = (
            _("Set new file permissions.")
            if is_multi
            else _("Set file permissions for: {name}\nCurrent: {perms}").format(
                name=items[0].name, perms=current_perms
            )
        )

        dialog = Adw.AlertDialog(heading=title, body=body, close_response="cancel")
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_size_request(350, -1)

        owner_group = Adw.PreferencesGroup(title=_("Owner"))
        owner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.owner_read, self.owner_write, self.owner_execute = (
            Gtk.CheckButton(label=_("Read")),
            Gtk.CheckButton(label=_("Write")),
            Gtk.CheckButton(label=_("Execute")),
        )
        owner_box.append(self.owner_read)
        owner_box.append(self.owner_write)
        owner_box.append(self.owner_execute)
        owner_row = Adw.ActionRow(child=owner_box)
        owner_group.add(owner_row)
        content_box.append(owner_group)

        group_group = Adw.PreferencesGroup(title=_("Group"))
        group_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.group_read, self.group_write, self.group_execute = (
            Gtk.CheckButton(label=_("Read")),
            Gtk.CheckButton(label=_("Write")),
            Gtk.CheckButton(label=_("Execute")),
        )
        group_box.append(self.group_read)
        group_box.append(self.group_write)
        group_box.append(self.group_execute)
        group_row = Adw.ActionRow(child=group_box)
        group_group.add(group_row)
        content_box.append(group_group)

        others_group = Adw.PreferencesGroup(title=_("Others"))
        others_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.others_read, self.others_write, self.others_execute = (
            Gtk.CheckButton(label=_("Read")),
            Gtk.CheckButton(label=_("Write")),
            Gtk.CheckButton(label=_("Execute")),
        )
        others_box.append(self.others_read)
        others_box.append(self.others_write)
        others_box.append(self.others_execute)
        others_row = Adw.ActionRow(child=others_box)
        others_group.add(others_row)
        content_box.append(others_group)

        self.mode_label = Gtk.Label(halign=Gtk.Align.CENTER, margin_top=12)
        content_box.append(self.mode_label)
        dialog.set_extra_child(content_box)

        if not is_multi:
            self._parse_permissions(items[0].permissions)
        self._update_mode_display()

        for checkbox in [
            self.owner_read,
            self.owner_write,
            self.owner_execute,
            self.group_read,
            self.group_write,
            self.group_execute,
            self.others_read,
            self.others_write,
            self.others_execute,
        ]:
            checkbox.connect("toggled", lambda _: self._update_mode_display())

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("apply", _("Apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_chmod_dialog_response, items)
        dialog.present(self.parent_window)

    def _on_chmod_dialog_response(self, dialog, response, items: List[FileItem]):
        if response == "apply":
            mode = self._calculate_mode()
            paths_to_change = [
                f"{self.current_path.rstrip('/')}/{item.name}" for item in items
            ]
            command = ["chmod", mode] + paths_to_change
            self._execute_verified_command(command, command_type="chmod")
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Chmod command sent to terminal"))
            )

    def _parse_permissions(self, perms_str: str):
        if len(perms_str) < 10:
            return
        self.owner_read.set_active(perms_str[1] == "r")
        self.owner_write.set_active(perms_str[2] == "w")
        self.owner_execute.set_active(perms_str[3] in "xs")
        self.group_read.set_active(perms_str[4] == "r")
        self.group_write.set_active(perms_str[5] == "w")
        self.group_execute.set_active(perms_str[6] in "xs")
        self.others_read.set_active(perms_str[7] == "r")
        self.others_write.set_active(perms_str[8] == "w")
        self.others_execute.set_active(perms_str[9] in "xs")

    def _calculate_mode(self) -> str:
        owner = (
            (4 * self.owner_read.get_active())
            + (2 * self.owner_write.get_active())
            + (1 * self.owner_execute.get_active())
        )
        group = (
            (4 * self.group_read.get_active())
            + (2 * self.group_write.get_active())
            + (1 * self.group_execute.get_active())
        )
        others = (
            (4 * self.others_read.get_active())
            + (2 * self.others_write.get_active())
            + (1 * self.others_execute.get_active())
        )
        return f"{owner}{group}{others}"

    def _update_mode_display(self):
        mode = self._calculate_mode()
        self.mode_label.set_text(f"Numeric mode: {mode}")
