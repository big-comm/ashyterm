# ashyterm/filemanager/manager.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import os
import shlex
import tempfile
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Vte

from ..core.tasks import AsyncTaskManager
from ..sessions.models import SessionItem
from ..terminal.manager import TerminalManager as TerminalManagerType
from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.security import InputSanitizer, ensure_secure_directory_permissions
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _
from ..utils.accessibility import set_label as a11y_label
from .models import FileItem
from .breadcrumb import (
    compute_navigation_path as _compute_navigation_path_impl,
    rebuild_breadcrumb as _rebuild_breadcrumb_impl,
)
from .cleanup import (
    cleanup_data_stores as _cleanup_data_stores_impl,
    cleanup_edited_metadata as _cleanup_edited_metadata_impl,
    cleanup_file_monitors as _cleanup_file_monitors_impl,
    cleanup_model_references as _cleanup_model_references_impl,
    nullify_references as _nullify_references_impl,
)
from .fm_column_view import ColumnViewDelegate
from .fm_context_menu import ContextMenuDelegate
from .ls_output import (
    is_connection_error as _ls_is_connection_error,
    normalize_path_for_ls as _ls_normalize_path,
    parse_ls_output as _ls_parse,
    resolve_link_target as _ls_resolve_link,
    should_fallback as _ls_should_fallback,
)
from .operations import FileOperations
from .search import FileSearchMixin
from .transfer_manager import TransferManager
from .transfers import FileTransferMixin

# CSS for file manager styles is now loaded from:
# data/styles/components.css (loaded by window_ui.py at startup)
# Classes: .transfer-progress-bar, .search-entry-no-icon

MAX_RECURSIVE_RESULTS = 1000
COMMAND_CONFIRM_TIMEOUT_MS = 15_000
COMMAND_REFRESH_DELAY_MS = 250


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
        """Injected dependencies; weakrefs used to break the window↔FM cycle."""
        super().__init__()
        self.logger = get_logger("ashyterm.filemanager.manager")
        self._parent_window_ref = weakref.ref(parent_window)
        self._terminal_manager_ref = weakref.ref(terminal_manager)
        self.settings_manager = settings_manager
        # Use global AsyncTaskManager instead of local executor
        self.transfer_history_window = None
        self.tooltip_helper = get_tooltip_helper()
        self._is_destroyed = False

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
        self._command_refresh_id = 0
        self._clipboard_items: List[Dict[str, Any]] = []
        self._clipboard_operation: Optional[str] = None
        self._clipboard_session_key: Optional[str] = None

        # Recursive search state
        self.recursive_search_enabled = False
        self._showing_recursive_results = False
        self._recursive_search_generation = 0
        self._recursive_search_in_progress = False

        # Delegates for column view and context menu logic
        self._column_view_delegate = ColumnViewDelegate(self)
        self._context_menu_delegate = ContextMenuDelegate(self)

        self._build_ui()

        self.bound_terminal = None
        self.directory_change_handler_id = 0

        self.revealer.connect("destroy", self.shutdown)

        self.logger.info("FileManager instance created, awaiting terminal binding.")

    @property
    def parent_window(self) -> Any:
        """Dereference weakref to get parent window."""
        return self._parent_window_ref()

    @property
    def terminal_manager(self) -> Any:
        """Dereference weakref to get terminal manager."""
        return self._terminal_manager_ref()

    def reparent(self, new_parent_window: Any, new_terminal_manager: Any) -> None:
        """Updates internal references when moved to a new window."""
        self.logger.info("Reparenting FileManager to a new window.")
        self._parent_window_ref = weakref.ref(new_parent_window)
        self._terminal_manager_ref = weakref.ref(new_terminal_manager)

    def rebind_terminal(self, new_terminal: Vte.Terminal) -> None:
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

        used_default_directory = False

        # If OSC7 directory is not available, use a sensible default.
        if not terminal_dir:
            terminal_dir = self._get_default_directory_for_session()
            used_default_directory = True

        terminal_dir_path = os.path.normpath(terminal_dir)
        current_path_path = (
            os.path.normpath(self.current_path) if self.current_path else None
        )
        if current_path_path is None or terminal_dir_path != current_path_path:
            self.logger.info(
                f"Terminal directory changed from {self.current_path} to {terminal_dir}, refreshing."
            )
            self.refresh(terminal_dir, source="terminal")

        if used_default_directory:
            self._resolve_remote_home_directory_async(terminal_dir)

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

        AsyncTaskManager.get().submit_io(worker, session, operations, session_key)

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

    def unbind(self) -> None:
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

    def shutdown(self, widget: Any) -> None:
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

    def destroy(self) -> None:
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

    # ── Cleanup delegators ──────────────────────────────────
    # The teardown pipeline lives in filemanager.cleanup; the
    # delegators below preserve the legacy method names that
    # ``FileManager.destroy()`` already calls.

    def _cleanup_file_monitors(self) -> None:
        _cleanup_file_monitors_impl(self)

    def _cleanup_edited_metadata(self) -> None:
        _cleanup_edited_metadata_impl(self)

    def _cleanup_model_references(self) -> None:
        _cleanup_model_references_impl(self)

    def _cleanup_data_stores(self) -> None:
        _cleanup_data_stores_impl(self)

    def _nullify_references(self) -> None:
        _nullify_references_impl(self)

    def get_temp_files_info(self) -> List[Dict]:
        """Returns information about currently edited temporary files."""
        return list(self.edited_file_metadata.values())

    def cleanup_all_temp_files(self, key_to_clear: Optional[tuple] = None) -> None:
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
        For SSH sessions, returns the configured remote directory or a fast
        fallback. Remote home probing runs asynchronously.
        """
        if not self.session_item:
            return os.path.expanduser("~")

        if self.session_item.is_local():
            return os.path.expanduser("~")

        configured_remote_dir = (self.session_item.sftp_remote_directory or "").strip()
        if configured_remote_dir:
            return configured_remote_dir

        return "/"

    def _resolve_remote_home_directory_async(self, fallback_path: str) -> None:
        """Resolve remote $HOME without blocking the GTK thread."""
        session = self.session_item
        operations = self.operations
        if not session or not operations or not session.is_ssh():
            return
        if (session.sftp_remote_directory or "").strip():
            return

        session_key = self._get_session_identifier(session)

        def worker():
            success, output = operations.execute_command_on_session(
                ["sh", "-lc", 'printf "%s" "$HOME"'],
                session_override=session,
                timeout=5,
            )
            remote_home = output.strip()
            if success and remote_home.startswith("/"):
                GLib.idle_add(
                    self._apply_resolved_remote_home,
                    session_key,
                    fallback_path,
                    remote_home,
                )

        AsyncTaskManager.get().submit_io(worker)

    def _apply_resolved_remote_home(
        self, session_key: str, fallback_path: str, remote_home: str
    ) -> bool:
        if self._is_destroyed:
            return GLib.SOURCE_REMOVE
        current_session = self.session_item
        if not self._is_current_session_for_key(current_session, session_key):
            return GLib.SOURCE_REMOVE
        if self.current_path != fallback_path or remote_home == fallback_path:
            return GLib.SOURCE_REMOVE

        self.logger.info(f"Resolved remote home directory: {remote_home}")
        self.refresh(remote_home, source="terminal")
        return GLib.SOURCE_REMOVE

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

    def get_main_widget(self) -> Any:
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

        self.column_view = self._column_view_delegate.create_detailed_column_view()
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
        scrolled_bg_click.connect("pressed", self._context_menu_delegate.on_scrolled_window_background_click)
        self.scrolled_window.add_controller(scrolled_bg_click)

        self.action_bar = Gtk.ActionBar()

        refresh_button = icon_button("view-refresh-symbolic")
        refresh_button.connect("clicked", lambda _: self.refresh(source="filemanager"))
        self.tooltip_helper.add_tooltip(refresh_button, _("Refresh"))
        a11y_label(refresh_button, _("Refresh file list"))
        self.action_bar.pack_start(refresh_button)

        self.hidden_files_toggle = Gtk.ToggleButton()
        self.hidden_files_toggle.set_child(icon_image("view-visible-symbolic"))
        self.hidden_files_toggle.connect("toggled", self._column_view_delegate.on_hidden_toggle)
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
        search_key_controller.connect("key-pressed", self._context_menu_delegate.on_search_key_pressed)
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

        self._column_view_delegate.setup_filtering_and_sorting()

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
        _rebuild_breadcrumb_impl(
            self.breadcrumb_box,
            self.current_path,
            on_clicked=self._on_breadcrumb_button_clicked,
        )

    def _on_breadcrumb_button_clicked(self, button, path_to_navigate):
        if path_to_navigate != self.current_path:
            if self.bound_terminal:
                self._fm_initiated_cd = True
                command = f'cd "{path_to_navigate}"\n'
                self.bound_terminal.feed_child(command.encode("utf-8"))
            else:
                self.refresh(path_to_navigate, source="filemanager")

    # ── Forwarding stubs to ColumnViewDelegate ──────────────────────────────

    def get_selected_items(self) -> List[FileItem]:
        return self._column_view_delegate.get_selected_items()

    # ── Forwarding stubs to ContextMenuDelegate ─────────────────────────────

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        self._context_menu_delegate.on_item_right_click(gesture, n_press, x, y, list_item)

    def _on_column_view_background_click(self, gesture, n_press, x, y):
        self._context_menu_delegate.on_column_view_background_click(gesture, n_press, x, y)

    def _on_column_view_key_pressed(self, controller, keyval, _keycode, state):
        return self._context_menu_delegate.on_column_view_key_pressed(controller, keyval, _keycode, state)

    def _on_column_view_key_released(self, controller, keyval, _keycode, state):
        return self._context_menu_delegate.on_column_view_key_released(controller, keyval, _keycode, state)

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

    def _confirm_pending_command(self):
        """
        Confirms a pending command was successful and restores user input, as per the new rule.
        """
        self._remove_pending_command_sources()

        # ALWAYS restore the user's input on completion, success or failure.
        if self.bound_terminal:
            self.bound_terminal.feed_child(b"\x19")  # CTRL+Y (Yank)

        self._pending_command = None

    def _remove_pending_command_sources(self) -> None:
        if self._command_timeout_id > 0:
            GLib.source_remove(self._command_timeout_id)
            self._command_timeout_id = 0

        if self._command_refresh_id > 0:
            GLib.source_remove(self._command_refresh_id)
            self._command_refresh_id = 0

    def _confirm_non_cd_pending_command(self) -> None:
        if not self._pending_command or self._pending_command["type"] == "cd":
            return
        self.logger.info(
            f"Command '{self._pending_command['str']}' confirmed by refresh."
        )
        self._confirm_pending_command()

    def _on_pending_command_timeout(self) -> bool:
        self._command_timeout_id = 0
        if self._pending_command:
            self.logger.warning(
                f"Command '{self._pending_command['str']}' was not confirmed before timeout."
            )
            self._confirm_pending_command()
        return GLib.SOURCE_REMOVE

    def _refresh_after_pending_command(self) -> bool:
        self._command_refresh_id = 0
        self.refresh(source="filemanager")
        return GLib.SOURCE_REMOVE

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
        self._remove_pending_command_sources()

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

        self._command_timeout_id = GLib.timeout_add(
            COMMAND_CONFIRM_TIMEOUT_MS, self._on_pending_command_timeout
        )
        if command_type != "cd":
            self._command_refresh_id = GLib.timeout_add(
                COMMAND_REFRESH_DELAY_MS, self._refresh_after_pending_command
            )

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
        return _compute_navigation_path_impl(self.current_path, item)

    def _open_file(self, item: FileItem):
        """Handle opening a file."""
        if self._is_remote_session():
            self._on_open_edit_action(None, None, [item])
        else:
            full_path = Path(self.current_path).joinpath(item.name)
            self._open_local_file(full_path)

    def set_visibility(self, visible: bool, source: str = "filemanager") -> None:
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
    ) -> None:
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
        return _ls_normalize_path(path)

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
        return _ls_is_connection_error(output)

    def _should_fallback(self, is_connection_error: bool, requested_path: str) -> bool:
        return _ls_should_fallback(
            is_connection_err=is_connection_error,
            requested_path=requested_path,
            last_successful_path=self._last_successful_path,
        )

    def _parse_ls_output(self, output: str, requested_path: str) -> list:
        return _ls_parse(
            output,
            requested_path,
            should_abort=lambda: self._is_destroyed
            or requested_path != self.current_path,
        )

    def _resolve_link_target(self, file_item: FileItem, base_path: str) -> None:
        _ls_resolve_link(file_item, base_path)

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
        self._confirm_non_cd_pending_command()
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

        self._confirm_non_cd_pending_command()
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
