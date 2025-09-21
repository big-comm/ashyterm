# ashyterm/filemanager/manager.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import os
import subprocess
import tempfile
import threading
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from ..sessions.models import SessionItem
from ..terminal.manager import TerminalManager as TerminalManagerType
from ..utils.logger import get_logger
from ..utils.security import InputSanitizer, ensure_secure_directory_permissions
from ..utils.translation_utils import _
from .models import FileItem
from .operations import FileOperations
from .transfer_dialog import TransferManagerDialog
from .transfer_manager import TransferManager, TransferType

CSS_DATA = b"""
.transfer-progress-bar progressbar > trough {
    background-color: @borders;
    border-radius: 4px;
    border-style: solid;
    border-width: 1px;
    border-color: alpha(@theme_fg_color, 0.1);
}

.transfer-progress-bar progressbar > progress {
    background-image: none;
    background-color: @accent_bg_color;
    border-radius: 4px;
}
"""


class FileManager(GObject.Object):
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
        self.parent_window = parent_window
        self.terminal_manager = terminal_manager
        self.settings_manager = settings_manager
        self.transfer_history_window = None

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS_DATA)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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
        self.file_monitors = {}
        self.edited_file_metadata = {}
        self._is_rebinding = False  # Flag to prevent race conditions during rebind

        # State for verified command execution
        self._pending_command = None
        self._command_timeout_id = 0

        self._build_ui()

        self.bound_terminal = None
        self.directory_change_handler_id = 0

        self.revealer.connect("destroy", self.shutdown)

        self.logger.info("FileManager instance created, awaiting terminal binding.")

    def reparent(self, new_parent_window, new_terminal_manager):
        """Updates internal references when moved to a new window."""
        self.logger.info("Reparenting FileManager to a new window.")
        self.parent_window = new_parent_window
        self.terminal_manager = new_terminal_manager

    def rebind_terminal(self, new_terminal: Vte.Terminal):
        """
        Binds the file manager to a new terminal instance, dynamically adjusting
        its context (local vs. remote) based on the terminal's current state.
        """
        self._is_rebinding = True  # Set flag to prevent race conditions
        if self.bound_terminal and self.directory_change_handler_id > 0:
            if GObject.signal_handler_is_connected(
                self.bound_terminal, self.directory_change_handler_id
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

        self.directory_change_handler_id = self.bound_terminal.connect(
            "notify::current-directory-uri", self._on_terminal_directory_changed
        )
        self._fm_initiated_cd = False

        self._update_action_bar_for_session_type()
        terminal_dir = self._get_terminal_current_directory() or "/"

        terminal_dir_path = Path(terminal_dir).resolve()
        current_path_path = Path(self.current_path).resolve()
        if terminal_dir_path != current_path_path:
            self.logger.info(
                f"Terminal directory changed from {self.current_path} to {terminal_dir}, refreshing."
            )
            self.refresh(terminal_dir, source="terminal")

        GLib.timeout_add(100, self._finish_rebinding)

    def _finish_rebinding(self) -> bool:
        self._is_rebinding = False
        return GLib.SOURCE_REMOVE

    def unbind(self):
        """Unbinds from the current terminal, effectively pausing updates."""
        if self.bound_terminal and self.directory_change_handler_id > 0:
            if GObject.signal_handler_is_connected(
                self.bound_terminal, self.directory_change_handler_id
            ):
                self.bound_terminal.disconnect(self.directory_change_handler_id)
        self.bound_terminal = None
        self.directory_change_handler_id = 0
        self.logger.info("File manager unbound from terminal.")

    def shutdown(self, widget):
        self.logger.info("Shutting down FileManager, cancelling active transfers.")

        if hasattr(self, "temp_files_changed_handler_id"):
            if GObject.signal_handler_is_connected(
                self, self.temp_files_changed_handler_id
            ):
                self.disconnect(self.temp_files_changed_handler_id)
            del self.temp_files_changed_handler_id

        if self.transfer_manager:
            for transfer_id in list(self.transfer_manager.active_transfers.keys()):
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
        self.logger.info("Destroying FileManager instance to prevent memory leaks.")
        self.shutdown(None)

        # MODIFIED: Proactively clear the store and model to break cycles
        if self.store:
            self.store.remove_all()
        if self.column_view:
            self.column_view.set_model(None)

        # Nullify references to break Python-side cycles
        self.parent_window = None
        self.terminal_manager = None
        self.settings_manager = None
        self.operations = None
        self.transfer_manager = None
        self.store = None
        self.column_view = None
        self.main_box = None
        self.revealer = None
        self.logger.info("FileManager destroyed.")

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
            for key in list(self.edited_file_metadata.keys()):
                self._cleanup_edited_file(key)

    def _get_terminal_current_directory(self):
        if not self.bound_terminal:
            return None
        try:
            uri = self.bound_terminal.get_current_directory_uri()
            if uri:
                from urllib.parse import unquote, urlparse

                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    return unquote(parsed_uri.path)
        except Exception:
            pass
        return None

    def _on_terminal_directory_changed(self, _terminal, _param_spec):
        if self._is_rebinding:
            return

        try:
            uri = self.bound_terminal.get_current_directory_uri()
            if not uri:
                return

            from urllib.parse import unquote, urlparse

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
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        self.revealer.set_size_request(-1, 200)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.set_size_request(-1, 200)
        self.main_box.add_css_class("file-manager-main-box")

        self.scrolled_window = Gtk.ScrolledWindow(vexpand=True)

        self.store = Gio.ListStore.new(FileItem)
        self.filtered_store = Gtk.FilterListModel(model=self.store)

        self.column_view = self._create_detailed_column_view()
        self.scrolled_window.set_child(self.column_view)

        # Drop target for external files, attached to the stable ScrolledWindow
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("accept", self._on_drop_accept)
        drop_target.connect("enter", self._on_drop_enter, self.scrolled_window)
        drop_target.connect("leave", self._on_drop_leave, self.scrolled_window)
        drop_target.connect("drop", self._on_files_dropped, self.scrolled_window)
        self.scrolled_window.add_controller(drop_target)

        self.action_bar = Gtk.ActionBar()

        refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_button.connect("clicked", lambda _: self.refresh(source="filemanager"))
        refresh_button.set_tooltip_text(_("Refresh"))
        self.action_bar.pack_start(refresh_button)

        self.hidden_files_toggle = Gtk.ToggleButton()
        self.hidden_files_toggle.set_icon_name("view-reveal-symbolic")
        self.hidden_files_toggle.connect("toggled", self._on_hidden_toggle)
        self.hidden_files_toggle.set_tooltip_text(_("Show hidden files"))
        self.action_bar.pack_start(self.hidden_files_toggle)

        self.breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.breadcrumb_box.add_css_class("breadcrumb-trail")
        self.breadcrumb_box.set_hexpand(True)
        self.action_bar.pack_start(self.breadcrumb_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_("Filter..."))
        self.search_entry.set_max_width_chars(12)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        self.search_entry.connect("delete-text", self._on_search_delete_text)
        self.action_bar.pack_end(self.search_entry)

        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        search_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.search_entry.add_controller(search_key_controller)

        history_button = Gtk.Button.new_from_icon_name("folder-download-symbolic")
        history_button.set_tooltip_text(_("Transfer History"))
        history_button.connect("clicked", self._on_show_transfer_history)
        self.action_bar.pack_end(history_button)

        self.upload_button = Gtk.Button.new_from_icon_name("upload-symbolic")
        self.upload_button.set_tooltip_text(_("Upload Files"))
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

        if search_term:
            if file_item.name == "..":
                return False
            return search_term in file_item.name.lower()

        if file_item.name == "..":
            return True

        show_hidden = self.hidden_files_toggle.get_active()
        if not show_hidden and file_item.name.startswith("."):
            return False

        return True

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
            lambda x, y: (x.permissions > y.permissions)
            - (x.permissions < y.permissions),
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

    def _on_search_changed(self, search_entry):
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
        if hasattr(self, "column_view") and self.column_view:
            if self.selection_model and self.selection_model.get_n_items() > 0:
                self.selection_model.select_item(0, True)
                self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

    def _on_search_activate(self, search_entry):
        """Handle activation (Enter key) on the search entry to open selected item."""
        if self.selection_model and self.selection_model.get_selection().get_size() > 0:
            position = self.selection_model.get_selection().get_nth(0)
            GLib.idle_add(self._deferred_activate_row, self.column_view, position)

    def _on_search_delete_text(self, search_entry, start_pos, end_pos):
        """Handle text deletion in search entry for backspace navigation."""
        current_text = search_entry.get_text()
        if start_pos == 0 and end_pos == len(current_text):
            GLib.idle_add(self._navigate_up_directory)

    def _navigate_up_directory(self):
        """Navigate up one directory level, preserving user input."""
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
        """Deferred row activation to allow focus events to be processed properly."""
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
        items = []
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

        return col_view

    def _setup_name_cell(self, factory, list_item):
        box = Gtk.Box(spacing=6, orientation=Gtk.Orientation.HORIZONTAL)
        box.append(Gtk.Image())
        label = Gtk.Label(xalign=0.0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
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
                "released", self._on_item_right_click, list_item.get_position()
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

    def _on_command_timeout(self):
        """
        Handles command failure. This is one of two places where user input is restored.
        """
        if not self._pending_command:
            return GLib.SOURCE_REMOVE

        self.logger.warning(
            f"Command '{self._pending_command['str']}' timed out. Assuming failure."
        )

        # Restore user's original input because the command failed
        self.bound_terminal.feed_child(b"\x19")  # CTRL+Y (Yank)

        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Command Failed"),
            body=_(
                "The command did not complete in time. The terminal may be busy or unresponsive. Your original input has been restored."
            ),
            close_response="ok",
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()

        # Clean up state
        self._pending_command = None
        self._command_timeout_id = 0
        return GLib.SOURCE_REMOVE

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

        command_str = " ".join(
            f'"{arg}"' if " " in arg else arg for arg in command_list
        )

        # Set state for the new operation
        self._pending_command = {"type": command_type, "str": command_str}
        if command_type == "cd":
            self._pending_command["path"] = expected_path

        # Preserve user input by cutting it
        self.bound_terminal.feed_child(b"\x01")  # CTRL+A: Beginning of line
        self.bound_terminal.feed_child(b"\x0b")  # CTRL+K: Kill to end of line

        # Send command
        self.bound_terminal.feed_child(f"{command_str}\n".encode("utf-8"))

        # Set a safety timeout
        self._command_timeout_id = GLib.timeout_add(5000, self._on_command_timeout)

        # For non-cd commands, success is confirmed by the refresh completing
        if command_type != "cd":
            GLib.timeout_add(500, lambda: self.refresh(source="filemanager"))

    def _on_row_activated(self, col_view, position):
        item: FileItem = col_view.get_model().get_item(position)
        if not item:
            return

        if item.is_directory_like:
            new_path = ""
            if item.name == "..":
                if self.current_path != "/":
                    new_path = str(Path(self.current_path).parent)
            else:
                base_path = self.current_path.rstrip("/")
                new_path = f"{base_path}/{item.name}"

            if not new_path:
                return

            if self.bound_terminal:
                self._fm_initiated_cd = True
                self._execute_verified_command(
                    ["cd", new_path], command_type="cd", expected_path=new_path
                )
                # Optimistically refresh the UI. The directory change handler will confirm.
                self.refresh(new_path, source="filemanager")
            else:
                self.refresh(new_path, source="filemanager")

        else:
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

    def refresh(self, path: str = None, source: str = "filemanager"):
        if hasattr(self, "search_entry"):
            self.search_entry.set_text("")
        if path:
            self.current_path = path
        self._update_breadcrumb()
        self.store.remove_all()

        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(False)
            self.search_entry.set_placeholder_text(_("Loading..."))

        thread = threading.Thread(
            target=self._list_files_thread,
            args=(self.current_path, source),
            daemon=True,
            name="FileListingThread",
        )
        thread.start()

    def _list_files_thread(self, requested_path: str, source: str = "filemanager"):
        try:
            if not self.operations:
                self.logger.warning("File operations not available. Cannot list files.")
                GLib.idle_add(
                    self._update_store_with_files,
                    requested_path,
                    [],
                    "Operations not initialized",
                    source,
                )
                return

            path_for_ls = requested_path
            if not path_for_ls.endswith("/"):
                path_for_ls += "/"

            command = ["ls", "-la", "--classify", "--full-time", path_for_ls]
            success, output = self.operations.execute_command_on_session(command)

            file_items = []
            parent_item = None
            if success:
                lines = output.strip().split("\n")[1:]
                for line in lines:
                    file_item = FileItem.from_ls_line(line)
                    if file_item:
                        if file_item.name == "..":
                            parent_item = file_item
                        elif file_item.name not in [".", ".."]:
                            if file_item.is_link and file_item._link_target:
                                if not file_item._link_target.startswith("/"):
                                    file_item._link_target = f"{requested_path.rstrip('/')}/{file_item._link_target}"
                            file_items.append(file_item)

            if requested_path != "/":
                if parent_item:
                    file_items.insert(0, parent_item)

            GLib.idle_add(
                self._update_store_with_files,
                requested_path,
                file_items,
                output if not success else "",
                source,
            )

        except Exception as e:
            self.logger.error(f"Error in background file listing: {e}")
            GLib.idle_add(
                self._update_store_with_files, requested_path, [], str(e), source
            )

    def _update_store_with_files(
        self,
        requested_path: str,
        file_items,
        error_message,
        source: str = "filemanager",
    ):
        if requested_path != self.current_path:
            self.logger.info(
                f"Discarding stale file list for '{requested_path}'. Current path is '{self.current_path}'."
            )
            return False

        if error_message:
            self.logger.error(f"Error listing files: {error_message}")

        self.store.splice(0, self.store.get_n_items(), file_items)

        # If a non-cd command was pending, the completion of the refresh confirms it.
        if self._pending_command and self._pending_command["type"] != "cd":
            self.logger.info(
                f"Command '{self._pending_command['str']}' confirmed by successful refresh."
            )
            self._confirm_pending_command()

        self._restore_search_entry(source)
        return False

    def _restore_search_entry(self, source: str = "filemanager"):
        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(True)
            self.search_entry.set_placeholder_text(_("Filter files..."))

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
        return self.session_item and not self.session_item.is_local()

    def _on_item_right_click(self, gesture, n_press, x, y, position):
        try:
            row = gesture.get_widget()
            translated_x, translated_y = row.translate_coordinates(
                self.column_view, x, y
            )
            if not self.selection_model.is_selected(position):
                self.selection_model.unselect_all()
                self.selection_model.select_item(position, True)

            selected_items = self.get_selected_items()
            if selected_items:
                actionable_items = [
                    item for item in selected_items if item.name != ".."
                ]
                if actionable_items:
                    self._show_context_menu(
                        actionable_items, translated_x, translated_y
                    )
            else:
                self._show_general_context_menu(translated_x, translated_y)
        except Exception as e:
            self.logger.error(f"Error in right-click handler: {e}")

    def _show_general_context_menu(self, x, y):
        menu = Gio.Menu()
        menu.append(_("Refresh"), "app.refresh")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        if popover.get_parent() is not None:
            popover.unparent()
        popover.set_parent(self.column_view)
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(x), int(y)
        popover.set_pointing_to(rect)
        popover.set_has_arrow(False)
        popover.popup()

    def _show_context_menu(self, items: List[FileItem], x, y):
        menu_model = self._create_context_menu_model(items)
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        if popover.get_parent() is not None:
            popover.unparent()
        popover.set_parent(self.column_view)
        self._setup_context_actions(popover, items)
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(x), int(y)
        popover.set_pointing_to(rect)
        popover.set_has_arrow(False)
        popover.popup()

    def _on_search_key_pressed(self, controller, keyval, _keycode, state):
        """Handle key presses on the search entry for list navigation."""
        if not self.selection_model:
            return Gdk.EVENT_PROPAGATE

        current_pos = (
            self.selection_model.get_selection().get_nth(0)
            if self.selection_model.get_selection().get_size() > 0
            else Gtk.INVALID_LIST_POSITION
        )

        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            if current_pos == Gtk.INVALID_LIST_POSITION:
                new_pos = 0
            else:
                delta = -1 if keyval == Gdk.KEY_Up else 1
                new_pos = current_pos + delta

            if 0 <= new_pos < self.sorted_store.get_n_items():
                self.selection_model.select_item(new_pos, True)
                self.column_view.scroll_to(
                    new_pos, None, Gtk.ListScrollFlags.NONE, None
                )

            return Gdk.EVENT_STOP

        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if current_pos != Gtk.INVALID_LIST_POSITION:
                self._on_row_activated(self.column_view, current_pos)
            return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_BackSpace:
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

        if num_items == 1 and not items[0].is_directory:
            menu.append(_("Open/Edit"), "context.open_edit")
            menu.append(_("Open With..."), "context.open_with")
            menu.append_section(None, Gio.Menu())

        if num_items == 1:
            menu.append(_("Rename"), "context.rename")

        if self._is_remote_session():
            menu.append_section(None, Gio.Menu())
            menu.append(_("Download"), "context.download")

        menu.append_section(None, Gio.Menu())
        menu.append(_("Permissions"), "context.chmod")

        menu.append_section(None, Gio.Menu())
        delete_item = Gio.MenuItem.new(_("Delete"), "context.delete")
        delete_item.set_attribute_value(
            "class", GLib.Variant("s", "destructive-action")
        )
        menu.append_item(delete_item)

        return menu

    def _setup_context_actions(self, popover, items: List[FileItem]):
        action_group = Gio.SimpleActionGroup()
        actions = {
            "open_edit": self._on_open_edit_action,
            "open_with": self._on_open_with_action,
            "rename": self._on_rename_action,
            "chmod": self._on_chmod_action,
            "download": self._on_download_action,
            "delete": self._on_delete_action,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate", lambda a, _, cb=callback, itms=items: cb(a, _, itms)
            )
            action_group.add_action(action)
        popover.insert_action_group("context", action_group)

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

    def _on_download_action(self, _action, _param, items: List[FileItem]):
        dialog = Gtk.FileDialog(
            title=_("Select Destination Folder"),
            modal=True,
            accept_label=_("Download Here"),
        )
        dialog.select_folder(
            self.parent_window, None, self._on_download_dialog_response, items
        )

    def _on_download_dialog_response(self, source, result, items: List[FileItem]):
        try:
            dest_folder = source.select_folder_finish(result)
            if dest_folder:
                dest_path = Path(dest_folder.get_path())

                def on_download_success(local_path, remote_path):
                    """Refreshes view if download was to the current local directory."""
                    if not self._is_remote_session():
                        # Check if the download destination is the current view
                        if (
                            Path(self.current_path).resolve()
                            == Path(local_path).parent.resolve()
                        ):
                            self.logger.info(
                                "Download to current local directory completed. Refreshing view."
                            )
                            self.refresh(source="filemanager")

                for item in items:
                    transfer_id = self.transfer_manager.add_transfer(
                        filename=item.name,
                        local_path=str(dest_path / item.name),
                        remote_path=f"{self.current_path.rstrip('/')}/{item.name}",
                        file_size=item.size,
                        transfer_type=TransferType.DOWNLOAD,
                        is_cancellable=True,
                        is_directory=item.is_directory_like,
                    )
                    self._start_cancellable_transfer(
                        transfer_id,
                        "Downloading",
                        self._background_download_worker,
                        on_success_callback=on_download_success,
                    )
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.parent_window._show_error_dialog(_("Error"), e.message)

    def _on_upload_action(self, _action, _param, _file_item: FileItem):
        dialog = Gtk.FileDialog(
            title=_("Upload File(s) to This Folder"),
            modal=True,
            accept_label=_("Upload"),
        )
        dialog.open_multiple(self.parent_window, None, self._on_upload_dialog_response)

    def _on_upload_dialog_response(self, source, result):
        try:
            files = source.open_multiple_finish(result)
            if files:
                for gio_file in files:
                    self._initiate_upload(Path(gio_file.get_path()))
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.parent_window._show_error_dialog(_("Error"), e.message)

    def _initiate_upload(self, local_path: Path):
        """Helper to start the upload process for a single local path."""
        remote_path = f"{self.current_path.rstrip('/')}/{local_path.name}"
        file_size = local_path.stat().st_size if local_path.exists() else 0
        transfer_id = self.transfer_manager.add_transfer(
            filename=local_path.name,
            local_path=str(local_path),
            remote_path=remote_path,
            file_size=file_size,
            transfer_type=TransferType.UPLOAD,
            is_cancellable=True,
            is_directory=local_path.is_dir(),
        )
        self._start_cancellable_transfer(
            transfer_id,
            "Uploading",
            self._background_upload_worker,
            on_success_callback=lambda _, __: GLib.idle_add(
                lambda: self.refresh(source="filemanager")
            ),
        )

    def _on_upload_clicked(self, button):
        self._on_upload_action(None, None, None)

    def _on_transfer_history_destroyed(self, widget):
        self.transfer_history_window = None

    def _on_show_transfer_history(self, button):
        if self.transfer_history_window:
            self.transfer_history_window.present()
            return

        self.transfer_history_window = TransferManagerDialog(
            self.transfer_manager, self.parent_window
        )
        self.transfer_history_window.connect(
            "destroy", self._on_transfer_history_destroyed
        )
        self.transfer_history_window.present()

    def _on_drop_accept(self, target, drop):
        return self._is_remote_session()

    def _on_drop_enter(self, target, x, y, scrolled_window):
        scrolled_window.add_css_class("drop-target")
        return Gdk.DragAction.COPY

    def _on_drop_leave(self, target, scrolled_window):
        scrolled_window.remove_css_class("drop-target")

    def _on_files_dropped(self, drop_target, value, x, y, scrolled_window):
        scrolled_window.remove_css_class("drop-target")
        if not self._is_remote_session():
            return False

        files_to_upload = []
        if isinstance(value, Gdk.FileList):
            for file in value.get_files():
                if path_str := file.get_path():
                    files_to_upload.append(Path(path_str))

        if files_to_upload:
            self._show_upload_confirmation_dialog(files_to_upload)

        return True

    def _show_upload_confirmation_dialog(self, local_paths: List[Path]):
        count = len(local_paths)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Confirm Upload"),
            body=_(
                "You are about to upload {count} item(s) to:\n<b>{dest}</b>\n\nDo you want to proceed?"
            ).format(count=count, dest=self.current_path),
            body_use_markup=True,
            close_response="cancel",
        )

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, min_content_height=100, max_content_height=200
        )
        list_box = Gtk.ListBox(css_classes=["boxed-list"])
        scrolled_window.set_child(list_box)

        for path in local_paths:
            list_box.append(Gtk.Label(label=path.name, xalign=0.0))

        dialog.set_extra_child(scrolled_window)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("upload", _("Upload"))
        dialog.set_response_appearance("upload", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("upload")
        dialog.connect("response", self._on_upload_confirmation_response, local_paths)
        dialog.present()

    def _on_upload_confirmation_response(self, dialog, response_id, local_paths):
        if response_id == "upload":
            for path in local_paths:
                self._initiate_upload(path)

    def _get_local_path_for_remote_file(
        self, session: SessionItem, remote_path: str
    ) -> Path:
        """Constructs a deterministic, human-readable local path for a remote file."""
        sanitized_session_name = InputSanitizer.sanitize_filename(session.name).replace(
            " ", "_"
        )
        # Remove leading slash from remote_path to prevent it being treated as an absolute path
        clean_remote_path = remote_path.lstrip("/")
        local_path = self.remote_edit_dir / sanitized_session_name / clean_remote_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return local_path

    def _on_open_edit_action(self, _action, _param, items: List[FileItem]):
        if not items:
            return
        file_item = items[0]  # Open/Edit only works on single items

        if not self._is_remote_session():
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._open_local_file(full_path)
            return

        remote_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
        edit_key = (self.session_item.name, remote_path)

        if edit_key in self.edited_file_metadata:
            metadata = self.edited_file_metadata[edit_key]
            local_path = Path(metadata["local_file_path"])
            last_known_ts = metadata["timestamp"]

            current_remote_ts = self.operations.get_remote_file_timestamp(remote_path)

            if (
                current_remote_ts
                and last_known_ts
                and current_remote_ts > last_known_ts
            ):
                self._show_conflict_on_open_dialog(local_path, remote_path, file_item)
            else:
                self.logger.info(f"Opening existing local copy for {remote_path}")
                self._open_local_file(local_path)
        else:
            self._download_and_execute(file_item, self._open_and_monitor_local_file)

    def _show_conflict_on_open_dialog(self, local_path, remote_path, file_item):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("File Has Changed on Server"),
            body=_(
                "The file '{filename}' has been modified on the server since you last opened it. Your local changes will be lost if you download the new version."
            ).format(filename=file_item.name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open-local", _("Open Local Version"))
        dialog.add_response("download-new", _("Download New Version"))
        dialog.set_response_appearance(
            "download-new", Adw.ResponseAppearance.DESTRUCTIVE
        )

        def on_response(d, response_id):
            if response_id == "open-local":
                self._open_local_file(local_path)
            elif response_id == "download-new":
                self._download_and_execute(file_item, self._open_and_monitor_local_file)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_open_with_action(self, _action, _param, items: List[FileItem]):
        if not items:
            return
        file_item = items[0]  # Open With only works on single items

        if self._is_remote_session():
            self._download_and_execute(file_item, self._show_open_with_dialog)
        else:
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._show_open_with_dialog(full_path, remote_path=None)

    def _download_and_execute(self, file_item: FileItem, on_success_callback):
        remote_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
        timestamp = self.operations.get_remote_file_timestamp(remote_path)
        if timestamp is None:
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Could not get remote file details."))
            )
            return

        local_path = self._get_local_path_for_remote_file(
            self.session_item, remote_path
        )

        transfer_id = self.transfer_manager.add_transfer(
            filename=file_item.name,
            local_path=str(local_path),
            remote_path=remote_path,
            file_size=file_item.size,
            transfer_type=TransferType.DOWNLOAD,
            is_cancellable=True,
            is_directory=file_item.is_directory_like,
        )
        success_callback_with_ts = partial(
            on_success_callback, initial_timestamp=timestamp
        )
        self._start_cancellable_transfer(
            transfer_id,
            "Downloading",
            self._background_download_worker,
            success_callback_with_ts,
        )

    def _start_cancellable_transfer(
        self, transfer_id, _verb, worker_func, on_success_callback
    ):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        thread = threading.Thread(
            target=worker_func, args=(transfer_id, on_success_callback), daemon=True
        )
        thread.start()

    def _background_download_worker(self, transfer_id, on_success_callback):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        try:
            self.transfer_manager.start_transfer(transfer_id)
            completion_callback = partial(
                self._on_transfer_complete, on_success_callback
            )
            self.operations.start_download_with_progress(
                transfer_id,
                self.session_item,
                transfer.remote_path,
                Path(transfer.local_path),
                is_directory=transfer.is_directory,
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=completion_callback,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            GLib.idle_add(
                self._on_transfer_complete,
                on_success_callback,
                transfer_id,
                False,
                str(e),
            )

    def _background_upload_worker(self, transfer_id, on_success_callback):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        try:
            self.transfer_manager.start_transfer(transfer_id)
            completion_callback = partial(
                self._on_transfer_complete, on_success_callback
            )
            self.operations.start_upload_with_progress(
                transfer_id,
                self.session_item,
                Path(transfer.local_path),
                transfer.remote_path,
                is_directory=transfer.is_directory,
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=completion_callback,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            GLib.idle_add(
                self._on_transfer_complete,
                on_success_callback,
                transfer_id,
                False,
                str(e),
            )

    def _on_transfer_complete(self, on_success_callback, transfer_id, success, message):
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
            if on_success_callback:
                transfer = self.transfer_manager.history[0]
                if transfer:
                    on_success_callback(Path(transfer.local_path), transfer.remote_path)
        else:
            self.transfer_manager.fail_transfer(transfer_id, message)
            if message == "Cancelled":
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Transfer cancelled."))
                )

    def _show_open_with_dialog(
        self,
        local_path: Path,
        remote_path: Optional[str] = None,
        initial_timestamp: Optional[int] = None,
    ):
        try:
            local_gio_file = Gio.File.new_for_path(str(local_path))
            dialog = Gtk.AppChooserDialog.new(
                self.parent_window, Gtk.DialogFlags.MODAL, local_gio_file
            )
            dialog.set_default_size(550, 450)
            dialog.set_title(_("Open With..."))

            def on_response(d, response_id):
                if response_id == Gtk.ResponseType.OK:
                    app_info = d.get_app_info()
                    if app_info:
                        if remote_path:
                            self._open_and_monitor_local_file(
                                local_path, remote_path, app_info, initial_timestamp
                            )
                        else:
                            self._open_local_file(local_path, app_info)
                d.destroy()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show 'Open With' dialog: {e}")
        return False

    def _open_local_file(self, local_path: Path, app_info: Gio.AppInfo = None):
        """Opens a local file with a specific app or the default."""
        local_gio_file = Gio.File.new_for_path(str(local_path))

        if not app_info:
            try:
                content_type = Gio.content_type_guess(str(local_path), None)[0]
                app_info = Gio.AppInfo.get_default_for_type(content_type, False)
            except Exception as e:
                self.logger.warning(
                    f"Could not find default app info for {local_path}: {e}"
                )
                app_info = None
        try:
            if app_info:
                app_info.launch([local_gio_file], None)
            else:
                subprocess.Popen(["xdg-open", str(local_path)])
        except Exception as e:
            self.logger.error(f"Failed to open local file {local_path}: {e}")
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Failed to open file."))
            )

    def _open_and_monitor_local_file(
        self,
        local_path: Path,
        remote_path: str,
        app_info: Gio.AppInfo = None,
        initial_timestamp: Optional[int] = None,
    ):
        local_gio_file = Gio.File.new_for_path(str(local_path))

        if not app_info:
            content_type = Gio.content_type_guess(str(local_path), None)[0]
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)

        if app_info:
            app_info.launch([local_gio_file], None)
        else:
            subprocess.Popen(["xdg-open", str(local_path)])

        edit_key = (self.session_item.name, remote_path)

        if edit_key in self.file_monitors:
            self.file_monitors[edit_key].cancel()

        monitor = local_gio_file.monitor(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_local_file_saved, remote_path, local_path)
        self.file_monitors[edit_key] = monitor

        self.edited_file_metadata[edit_key] = {
            "session_name": self.session_item.name,
            "remote_path": remote_path,
            "local_file_path": str(local_path),
            "timestamp": initial_timestamp,
        }
        self.emit("temp-files-changed", len(self.edited_file_metadata))

        app = self.parent_window.get_application()
        if app:
            notification = Gio.Notification.new(_("Ashy Terminal"))
            notification.set_body(
                _("File is open. Saving it will upload changes back to the server.")
            )
            notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
            app.send_notification(f"ashy-file-open-{remote_path}", notification)

        return False

    def _on_local_file_saved(
        self, _monitor, _file, _other_file, event_type, remote_path, local_path
    ):
        if event_type == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            threading.Thread(
                target=self._check_conflict_and_upload,
                args=(local_path, remote_path),
                daemon=True,
            ).start()

    def _check_conflict_and_upload(self, local_path: Path, remote_path: str):
        """Checks for remote changes before uploading the local file."""
        edit_key = (self.session_item.name, remote_path)
        metadata = self.edited_file_metadata.get(edit_key)
        if not metadata:
            self.logger.warning(
                f"No metadata for edited file {local_path}, cannot upload."
            )
            return

        last_known_timestamp = metadata.get("timestamp")
        current_remote_timestamp = self.operations.get_remote_file_timestamp(
            remote_path
        )

        if current_remote_timestamp is None:
            self.logger.error(
                f"Could not verify remote timestamp for {remote_path}. Aborting upload."
            )
            GLib.idle_add(
                self.parent_window.toast_overlay.add_toast,
                Adw.Toast(title=_("Upload failed: Could not verify remote file.")),
            )
            return

        if (
            last_known_timestamp is not None
            and current_remote_timestamp > last_known_timestamp
        ):
            self.logger.warning(f"Conflict detected for {remote_path}. Prompting user.")
            GLib.idle_add(self._show_conflict_dialog, local_path, remote_path)
        else:
            self.logger.info(f"No conflict for {remote_path}. Proceeding with upload.")
            self._upload_on_save_thread(local_path, remote_path)

    def _show_conflict_dialog(self, local_path: Path, remote_path: str):
        """Shows a dialog to the user to resolve an edit conflict."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("File Conflict"),
            body=_(
                "The file '{filename}' has been modified on the server since you started editing it. How would you like to proceed?"
            ).format(filename=local_path.name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel Upload"))
        dialog.add_response("overwrite", _("Overwrite Server File"))
        dialog.add_response("save-as", _("Save as New File"))
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_response(d, response_id):
            if response_id == "overwrite":
                self._upload_on_save_thread(local_path, remote_path)
            elif response_id == "save-as":
                self._prompt_for_new_filename_and_upload(local_path, remote_path)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _prompt_for_new_filename_and_upload(self, local_path: Path, remote_path: str):
        """Prompts for a new filename and uploads the file."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Save As"),
            body=_("Enter a new name for the file on the server:"),
            close_response="cancel",
        )
        entry = Gtk.Entry(text=f"{local_path.stem}-copy{local_path.suffix}")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_default_response("save")

        def on_response(d, response_id):
            if response_id == "save":
                new_name = entry.get_text().strip()
                if new_name:
                    new_remote_path = str(Path(remote_path).parent / new_name)
                    self._upload_on_save_thread(local_path, new_remote_path)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_save_upload_complete(self, transfer_id, success, message):
        """Callback to finalize transfer and show system notification."""
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
            transfer = next(
                (t for t in self.transfer_manager.history if t.id == transfer_id), None
            )
            if transfer:
                edit_key = (self.session_item.name, transfer.remote_path)
                if edit_key in self.edited_file_metadata:
                    new_ts = self.operations.get_remote_file_timestamp(
                        transfer.remote_path
                    )
                    if new_ts:
                        self.edited_file_metadata[edit_key]["timestamp"] = new_ts
        else:
            self.transfer_manager.fail_transfer(transfer_id, message)

        app = self.parent_window.get_application()
        if not app:
            return

        transfer = next(
            (t for t in self.transfer_manager.history if t.id == transfer_id), None
        )
        if not transfer:
            return

        notification = Gio.Notification.new(_("Ashy Terminal"))
        if success:
            notification.set_title(_("Upload Complete"))
            notification.set_body(
                _("'{filename}' has been saved to the server.").format(
                    filename=transfer.filename
                )
            )
        else:
            notification.set_title(_("Upload Failed"))
            notification.set_body(
                _("Could not save '{filename}' to the server: {error}").format(
                    filename=transfer.filename, error=message
                )
            )
        notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
        app.send_notification(f"ashy-upload-complete-{transfer_id}", notification)

    def _upload_on_save_thread(self, local_path, remote_path):
        """Handles uploading a file on save using the TransferManager."""
        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            transfer_id = self.transfer_manager.add_transfer(
                filename=local_path.name,
                local_path=str(local_path),
                remote_path=remote_path,
                file_size=file_size,
                transfer_type=TransferType.UPLOAD,
                is_cancellable=True,
                is_directory=local_path.is_dir(),
            )
            self.operations.start_upload_with_progress(
                transfer_id,
                self.session_item,
                local_path,
                remote_path,
                is_directory=local_path.is_dir(),
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=self._on_save_upload_complete,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            self.logger.error(f"Failed to initiate upload-on-save: {e}")

    def _on_rename_action(self, _action, _param, items: List[FileItem]):
        if not items or len(items) > 1:
            return
        file_item = items[0]
        dialog = Adw.AlertDialog(
            heading=_("Rename"),
            body=_("Enter a new name for '{name}'").format(name=file_item.name),
            close_response="cancel",
        )
        entry = Gtk.Entry(text=file_item.name, hexpand=True, activates_default=True)
        entry.select_region(0, -1)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.connect("response", self._on_rename_dialog_response, file_item, entry)
        dialog.present(self.parent_window)

    def _on_rename_dialog_response(self, dialog, response, file_item, entry):
        if response == "rename":
            new_name = entry.get_text().strip()
            if new_name and new_name != file_item.name:
                old_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
                new_path = f"{self.current_path.rstrip('/')}/{new_name}"
                command = ["mv", old_path, new_path]
                self._execute_verified_command(command, command_type="mv")
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Rename command sent to terminal"))
                )

    def _cleanup_edited_file(self, edit_key: tuple):
        """Cleans up all resources associated with a closed temporary file."""
        metadata = self.edited_file_metadata.pop(edit_key, None)
        if not metadata:
            return False

        monitor = self.file_monitors.pop(edit_key, None)
        if monitor:
            monitor.cancel()

        try:
            local_path = Path(metadata["local_file_path"])
            if local_path.exists():
                local_path.unlink()
                self.logger.info(f"Removed temporary file: {local_path}")
                # Clean up empty parent directories
                try:
                    parent = local_path.parent
                    while parent != self.remote_edit_dir and not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent
                except OSError as e:
                    self.logger.warning(f"Could not remove empty parent dir: {e}")
        except Exception as e:
            self.logger.error(
                f"Failed to remove temporary file for key {edit_key}: {e}"
            )

        self.emit("temp-files-changed", len(self.edited_file_metadata))
        return False
