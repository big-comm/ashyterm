# ashyterm/filemanager/manager.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import subprocess
import tempfile
import threading
from functools import partial
from pathlib import Path
from typing import Optional

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Vte

from ..sessions.models import SessionItem
from ..terminal.manager import TerminalManager as TerminalManagerType
from ..utils.logger import get_logger
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


class FileManager:
    def __init__(
        self,
        parent_window: Gtk.Window,
        terminal_manager: TerminalManagerType,
        terminal_to_bind: Vte.Terminal,
    ):
        """
        Initializes the FileManager.
        Dependencies like TerminalManager are injected for better decoupling.

        Args:
            parent_window: The parent window, used for dialogs.
            terminal_manager: The central manager for terminal instances.
            terminal_to_bind: The initial terminal to bind to.
        """
        self.logger = get_logger("ashyterm.filemanager.manager")
        self.parent_window = parent_window
        self.terminal_manager = terminal_manager
        self.transfer_history_window = None

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS_DATA)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        terminal_id = getattr(terminal_to_bind, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        if not info or not info.get("identifier"):
            raise ValueError(
                "Cannot create FileManager for a terminal without a valid session identifier."
            )

        identifier = info.get("identifier")
        terminal_type = info.get("type", "local")

        if isinstance(identifier, SessionItem):
            self.session_item = identifier
        elif terminal_type == "local":
            self.session_item = SessionItem("Local Terminal", session_type="local")
        else:
            raise ValueError(
                f"Invalid identifier type for terminal: {type(identifier)}"
            )

        self.operations = FileOperations(self.session_item)

        from ..utils.platform import get_config_directory

        self.transfer_manager = TransferManager(
            str(get_config_directory()), self.operations
        )

        self.current_path = "/tmp"
        self.file_monitors = {}

        self._build_ui()

        self.bound_terminal = None
        self.directory_change_handler_id = 0
        self.rebind_terminal(terminal_to_bind)  # Initial bind

        self.revealer.connect("destroy", self.shutdown)

        self.logger.info(
            f"FileManager instance created and bound to terminal: {getattr(self.bound_terminal, 'terminal_id', 'unknown')}"
        )

    def rebind_terminal(self, new_terminal: Vte.Terminal):
        """
        Binds the file manager to a new terminal instance.
        This is crucial for handling focus changes in split panes.
        """
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

        self.directory_change_handler_id = self.bound_terminal.connect(
            "notify::current-directory-uri", self._on_terminal_directory_changed
        )

        # Track directory changes initiated by file manager
        self._fm_initiated_cd = False

        # Only refresh if the directory is different, don't refresh on focus changes
        terminal_dir = self._get_terminal_current_directory()
        if terminal_dir and terminal_dir != self.current_path:
            self.refresh(terminal_dir, source="terminal")

        self.logger.info(
            f"File manager rebound to terminal ID: {getattr(new_terminal, 'terminal_id', 'unknown')}"
        )

    def shutdown(self, widget):
        self.logger.info("Shutting down FileManager, cancelling active transfers.")
        if self.transfer_manager:
            for transfer_id in list(self.transfer_manager.active_transfers.keys()):
                self.transfer_manager.cancel_transfer(transfer_id)

        if self.transfer_history_window:
            self.transfer_history_window.destroy()

        if self.operations:
            self.operations.shutdown()

        if self.bound_terminal and self.directory_change_handler_id > 0:
            if GObject.signal_handler_is_connected(
                self.bound_terminal, self.directory_change_handler_id
            ):
                self.bound_terminal.disconnect(self.directory_change_handler_id)
            self.directory_change_handler_id = 0

    def _get_terminal_current_directory(self):
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
        if not self.revealer.get_child_revealed():
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
            if new_path != self.current_path:
                # Determine source based on whether this was initiated by file manager
                source = "filemanager" if self._fm_initiated_cd else "terminal"
                self._fm_initiated_cd = False  # Reset the flag
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

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.set_size_request(-1, 200)

        scrolled_window = Gtk.ScrolledWindow(vexpand=True)

        self.store = Gio.ListStore.new(FileItem)
        self.filtered_store = Gtk.FilterListModel(model=self.store)

        self.column_view = self._create_detailed_column_view()
        scrolled_window.set_child(self.column_view)

        action_bar = Gtk.ActionBar()

        refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_button.connect("clicked", lambda _: self.refresh(source="filemanager"))
        refresh_button.set_tooltip_text(_("Refresh"))
        action_bar.pack_start(refresh_button)

        self.hidden_files_toggle = Gtk.ToggleButton()
        self.hidden_files_toggle.set_icon_name("view-reveal-symbolic")
        self.hidden_files_toggle.connect("toggled", self._on_hidden_toggle)
        self.hidden_files_toggle.set_tooltip_text(_("Show hidden files"))
        action_bar.pack_start(self.hidden_files_toggle)

        self.breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.breadcrumb_box.add_css_class("breadcrumb-trail")
        self.breadcrumb_box.set_hexpand(True)
        action_bar.pack_start(self.breadcrumb_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_("Filter..."))
        self.search_entry.set_max_width_chars(12)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        self.search_entry.connect("delete-text", self._on_search_delete_text)
        action_bar.pack_end(self.search_entry)

        # NOVO: Controlador de teclado para o campo de busca
        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        search_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.search_entry.add_controller(search_key_controller)

        history_button = Gtk.Button.new_from_icon_name("folder-download-symbolic")
        history_button.set_tooltip_text(_("Transfer History"))
        history_button.connect("clicked", self._on_show_transfer_history)
        action_bar.pack_end(history_button)

        if self._is_remote_session():
            upload_button = Gtk.Button.new_from_icon_name("document-send-symbolic")
            upload_button.set_tooltip_text(_("Upload Files"))
            upload_button.connect("clicked", self._on_upload_clicked)
            action_bar.pack_end(upload_button)

        progress_widget = self.transfer_manager.create_progress_widget()
        main_box.append(progress_widget)

        main_box.append(scrolled_window)
        main_box.append(action_bar)
        self.revealer.set_child(main_box)

        self._setup_filtering_and_sorting()

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
        show_hidden = self.hidden_files_toggle.get_active()
        if not show_hidden:
            if file_item.name.startswith(".") and file_item.name != "..":
                return False

        search_text = getattr(self, "search_entry", None)
        if search_text:
            search_term = search_text.get_text().lower().strip()
            if search_term:
                return search_term in file_item.name.lower()

        return True

    def _dolphin_sort_priority(
        self, file_item_a, file_item_b, secondary_sort_func=None
    ):
        if file_item_a.name == "..":
            return -1
        if file_item_b.name == "..":
            return 1

        def get_type(item):
            return 0 if item.is_directory else 1

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

    def _on_hidden_toggle(self, toggle_button):
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_search_changed(self, search_entry):
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
        # Select first item if available after filtering
        if hasattr(self, "column_view") and self.column_view:
            selection_model = self.column_view.get_model()
            if selection_model and selection_model.get_n_items() > 0:
                selection_model.select_item(0, True)
                self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

    def _on_search_activate(self, search_entry):
        """Handle activation (Enter key) on the search entry to open selected item."""
        selection_model = self.column_view.get_model()
        if selection_model and selection_model.get_selection().get_size() > 0:
            position = selection_model.get_selection().get_nth(0)
            # Defer activation to allow focus events to be processed properly
            GLib.idle_add(self._deferred_activate_row, self.column_view, position)

    def _on_search_delete_text(self, search_entry, start_pos, end_pos):
        """Handle text deletion in search entry for backspace navigation."""
        # Check if the search entry will be empty after deletion
        current_text = search_entry.get_text()
        if start_pos == 0 and end_pos == len(current_text):
            # Full text deletion - will be empty
            GLib.idle_add(self._navigate_up_directory)

    def _navigate_up_directory(self):
        """Navigate up one directory level."""
        if self.bound_terminal:
            self._fm_initiated_cd = True
            command = "cd ..\n"
            self.bound_terminal.feed_child(command.encode("utf-8"))
        else:
            # For local sessions, calculate parent directory
            parent_path = Path(self.current_path).parent
            if str(parent_path) != self.current_path:
                self.refresh(str(parent_path), source="filemanager")
        return False

    def _deferred_activate_row(self, col_view, position):
        """Deferred row activation to allow focus events to be processed properly."""
        self._on_row_activated(col_view, position)
        return False  # Remove from idle queue

    def _create_column(self, title, sorter, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        column = Gtk.ColumnViewColumn(
            title=title, factory=factory, expand=expand, resizable=True
        )
        column.set_sorter(sorter)
        return column

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
        selection_model = Gtk.SingleSelection(model=self.sorted_store)
        col_view.set_model(selection_model)
        col_view.sort_by_column(
            col_view.get_columns().get_item(0), Gtk.SortType.ASCENDING
        )

        col_view.connect("activate", self._on_row_activated)
        right_click_gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click_gesture.connect("pressed", self._on_item_right_click)
        col_view.add_controller(right_click_gesture)

        # NOVO: Controladores de teclado para a lista de arquivos
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_column_view_key_pressed)
        key_controller.connect("key-released", self._on_column_view_key_released)
        col_view.add_controller(key_controller)

        if self._is_remote_session():
            drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
            drop_target.connect("drop", self._on_files_dropped)
            col_view.add_controller(drop_target)

        return col_view

    def _setup_name_cell(self, factory, list_item):
        box = Gtk.Box(spacing=6, orientation=Gtk.Orientation.HORIZONTAL)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0.0))
        link_icon = Gtk.Image()
        link_icon.set_visible(False)
        box.append(link_icon)
        list_item.set_child(box)

    def _bind_name_cell(self, factory, list_item):
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
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.permissions)

    def _bind_owner_cell(self, factory, list_item):
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.owner)

    def _bind_group_cell(self, factory, list_item):
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.group)

    def _bind_size_cell(self, factory, list_item):
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
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        date_str = file_item.date.strftime("%Y-%m-%d %H:%M")
        label.set_text(date_str)

    def _on_row_activated(self, col_view, position):
        item: FileItem = col_view.get_model().get_item(position)
        if not item:
            return

        if item.is_directory:
            new_path = ""
            if item.name == "..":
                if self.current_path != "/":
                    new_path = (
                        "/".join(self.current_path.rstrip("/").split("/")[:-1]) or "/"
                    )
            else:
                base_path = self.current_path.rstrip("/")
                new_path = f"{base_path}/{item.name}"
            if not new_path:
                return
            if self.bound_terminal:
                self._fm_initiated_cd = True
                command = f'cd "{new_path}"\n'
                self.bound_terminal.feed_child(command.encode("utf-8"))
        else:
            if self._is_remote_session():
                self._on_open_edit_action(None, None, item)
            else:
                full_path = Path(self.current_path).joinpath(item.name)
                self._open_local_file(full_path)

    def set_visibility(self, visible: bool, source: str = "filemanager"):
        self.revealer.set_reveal_child(visible)
        if visible:
            self.refresh(source=source)
            # Only grab focus if the visibility change was triggered by file manager interaction
            if source == "filemanager":
                self.column_view.grab_focus()
        else:
            if self.bound_terminal:
                self.bound_terminal.grab_focus()

    def refresh(self, path: str = None, source: str = "filemanager"):
        if path:
            self.current_path = path
            if hasattr(self, "search_entry"):
                self.search_entry.set_text("")

        self._update_breadcrumb()
        self.store.remove_all()

        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(False)
            self.search_entry.set_placeholder_text(_("Loading..."))

        thread = threading.Thread(
            target=self._list_files_thread,
            args=(source,),
            daemon=True,
            name="FileListingThread",
        )
        thread.start()

    def _list_files_thread(self, source: str = "filemanager"):
        try:
            print(self.current_path)
            success, output = self.operations.execute_command_on_session(
                f"ls -la --file-type --full-time '{self.current_path}/'"
            )

            file_items = []
            if success:
                lines = output.strip().split("\n")[1:]
                for line in lines:
                    file_item = FileItem.from_ls_line(line)
                    if file_item and file_item.name not in [".", ".."]:
                        if file_item.is_link and file_item._link_target:
                            if not file_item._link_target.startswith("/"):
                                file_item._link_target = f"{self.current_path.rstrip('/')}/{file_item._link_target}"
                        file_items.append(file_item)

            GLib.idle_add(
                self._update_store_with_files,
                file_items,
                output if not success else "",
                source,
            )

        except Exception as e:
            self.logger.error(f"Error in background file listing: {e}")
            GLib.idle_add(self._update_store_with_files, [], str(e), source)

    def _update_store_with_files(
        self, file_items, error_message, source: str = "filemanager"
    ):
        if error_message:
            self.logger.error(f"Error listing files: {error_message}")

        self.store.splice(0, self.store.get_n_items(), file_items)
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
        # Select first item if available after restoring
        if hasattr(self, "column_view") and self.column_view:
            selection_model = self.column_view.get_model()
            if selection_model and selection_model.get_n_items() > 0:
                selection_model.select_item(0, True)
                self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
                # Only grab focus if the refresh was triggered by file manager interaction
                if source == "filemanager":
                    self.column_view.grab_focus()
        return False

    def _is_remote_session(self) -> bool:
        return not self.session_item.is_local()

    def _on_item_right_click(self, _gesture, _n_press, x, y):
        try:
            selection_model = self.column_view.get_model()
            if not selection_model:
                return
            current_selection = selection_model.get_selection()
            if current_selection and current_selection.get_size() > 0:
                position = current_selection.get_nth(0)
                file_item = selection_model.get_item(position)
                if file_item and file_item.name != "..":
                    self._show_context_menu(file_item, x, y)
            else:
                self._show_general_context_menu(x, y)
        except Exception as e:
            self.logger.error(f"Error in right-click handler: {e}")

    def _show_general_context_menu(self, x, y):
        menu = Gio.Menu()
        menu.append(_("Refresh"), "app.refresh")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.column_view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _show_context_menu(self, file_item, x, y):
        menu_model = self._create_context_menu_model(file_item)
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.column_view)
        self._setup_context_actions(popover, file_item)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    # --- NOVO: Handlers de Interação por Teclado ---

    def _on_search_key_pressed(self, controller, keyval, keycode, state):
        """Handle key presses on the search entry for list navigation."""
        selection_model = self.column_view.get_model()
        if not selection_model:
            return Gdk.EVENT_PROPAGATE

        current_pos = (
            selection_model.get_selection().get_nth(0)
            if selection_model.get_selection().get_size() > 0
            else Gtk.INVALID_LIST_POSITION
        )

        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            if current_pos == Gtk.INVALID_LIST_POSITION:
                new_pos = 0
            else:
                delta = -1 if keyval == Gdk.KEY_Up else 1
                new_pos = current_pos + delta

            if 0 <= new_pos < self.sorted_store.get_n_items():
                selection_model.select_item(new_pos, True)
                self.column_view.scroll_to(
                    new_pos, None, Gtk.ListScrollFlags.NONE, None
                )

            return Gdk.EVENT_STOP

        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if current_pos != Gtk.INVALID_LIST_POSITION:
                self._on_row_activated(self.column_view, current_pos)
            return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_BackSpace:
            # If search entry is empty and user presses backspace, go up one directory
            if not self.search_entry.get_text().strip():
                # Stop the event completely to prevent SearchEntry's default behavior
                controller.stop_emission("key-pressed")
                self._navigate_up_directory()
                return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_column_view_key_pressed(self, controller, keyval, keycode, state):
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
            selection_model = self.column_view.get_model()
            if selection_model and selection_model.get_selection().get_size() > 0:
                pos = selection_model.get_selection().get_nth(0)
                self._on_row_activated(self.column_view, pos)
                return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_BackSpace:
            # If search entry is empty, go up one directory
            if not self.search_entry.get_text().strip():
                self._navigate_up_directory()
                return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_column_view_key_released(self, controller, keyval, keycode, state):
        """Handle key releases on the column view for context menu."""
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            selection_model = self.column_view.get_model()
            if selection_model and selection_model.get_selection().get_size() > 0:
                position = selection_model.get_selection().get_nth(0)
                file_item = selection_model.get_item(position)
                if file_item and file_item.name != "..":
                    # Usamos 0,0 para x,y pois o popover será posicionado relativo à view
                    self._show_context_menu(file_item, 0, 0)
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _create_context_menu_model(self, file_item):
        menu = Gio.Menu()

        if not file_item.is_directory:
            menu.append(_("Open/Edit"), "context.open_edit")
            menu.append(_("Open With..."), "context.open_with")
            menu.append_section(None, Gio.Menu())

        menu.append(_("Rename"), "context.rename")

        if self._is_remote_session() and not file_item.is_directory:
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

    def _setup_context_actions(self, popover, file_item):
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
                "activate", lambda a, _, cb=callback, item=file_item: cb(a, _, item)
            )
            action_group.add_action(action)
        popover.insert_action_group("context", action_group)

    def _on_delete_action(self, _action, _param, file_item: FileItem):
        dialog = Adw.AlertDialog(
            heading=_("Delete File"),
            body=_(
                "Are you sure you want to permanently delete '{name}'?\n\nThis action cannot be undone."
            ).format(name=file_item.name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_dialog_response, file_item)
        dialog.present(self.parent_window)

    def _on_delete_dialog_response(self, dialog, response, file_item):
        if response == "delete":
            full_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
            if self.bound_terminal:
                command = f'rm -rf "{full_path}"\n'
                self.bound_terminal.feed_child(command.encode("utf-8"))
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Delete command sent to terminal"))
                )
                GLib.timeout_add(500, lambda: self.refresh(source="filemanager"))

    def _on_chmod_action(self, _action, _param, file_item: FileItem):
        self._show_permissions_dialog(file_item)

    def _show_permissions_dialog(self, file_item: FileItem):
        dialog = Adw.AlertDialog(
            heading=_("Permissions"),
            body=_("Set file permissions for: {name}\nCurrent: {perms}").format(
                name=file_item.name, perms=file_item.permissions
            ),
            close_response="cancel",
        )
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

        self._parse_permissions(file_item.permissions)
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
        dialog.connect("response", self._on_chmod_dialog_response, file_item)
        dialog.present(self.parent_window)

    def _on_chmod_dialog_response(self, dialog, response, file_item):
        if response == "apply":
            mode = self._calculate_mode()
            full_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
            if self.bound_terminal:
                command = f'chmod {mode} "{full_path}"\n'
                self.bound_terminal.feed_child(command.encode("utf-8"))
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Chmod command sent to terminal"))
                )
                GLib.timeout_add(500, lambda: self.refresh(source="filemanager"))

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

    def _on_download_action(self, _action, _param, file_item: FileItem):
        dialog = Gtk.FileDialog(
            title=_("Save File As..."),
            modal=True,
            accept_label=_("Save"),
            initial_name=file_item.name,
        )
        dialog.save(
            self.parent_window, None, self._on_download_dialog_response, file_item
        )

    def _on_download_dialog_response(self, source, result, file_item):
        try:
            local_file = source.save_finish(result)
            if local_file:
                transfer_id = self.transfer_manager.add_transfer(
                    filename=file_item.name,
                    local_path=str(local_file.get_path()),
                    remote_path=f"{self.current_path.rstrip('/')}/{file_item.name}",
                    file_size=file_item.size,
                    transfer_type=TransferType.DOWNLOAD,
                    is_cancellable=True,
                )
                self._start_cancellable_transfer(
                    transfer_id,
                    "Downloading",
                    self._background_download_worker,
                    on_success_callback=None,
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
                    local_path = Path(gio_file.get_path())
                    remote_path = f"{self.current_path.rstrip('/')}/{local_path.name}"
                    file_size = local_path.stat().st_size if local_path.exists() else 0
                    transfer_id = self.transfer_manager.add_transfer(
                        filename=local_path.name,
                        local_path=str(local_path),
                        remote_path=remote_path,
                        file_size=file_size,
                        transfer_type=TransferType.UPLOAD,
                        is_cancellable=True,
                    )
                    self._start_cancellable_transfer(
                        transfer_id,
                        "Uploading",
                        self._background_upload_worker,
                        on_success_callback=lambda _, __: GLib.idle_add(
                            lambda: self.refresh(source="filemanager")
                        ),
                    )
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.parent_window._show_error_dialog(_("Error"), e.message)

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

    def _on_files_dropped(self, drop_target, value, x, y):
        if not self._is_remote_session():
            return False
        files = list(value) if hasattr(value, "__iter__") else [value]
        if files:
            self._on_upload_action(None, None, None)
        return True

    def _on_open_edit_action(self, _action, _param, file_item: FileItem):
        if self._is_remote_session():
            callback = partial(self._open_and_monitor_local_file, app_info=None)
            self._download_and_execute(file_item, callback)
        else:
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._open_local_file(full_path)

    def _on_open_with_action(self, _action, _param, file_item: FileItem):
        if self._is_remote_session():
            self._download_and_execute(file_item, self._show_open_with_dialog)
        else:
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._show_open_with_dialog(full_path, remote_path=None)

    def _download_and_execute(self, file_item: FileItem, on_success_callback):
        transfer_id = self.transfer_manager.add_transfer(
            filename=file_item.name,
            local_path="",
            remote_path="",
            file_size=file_item.size,
            transfer_type=TransferType.DOWNLOAD,
            is_cancellable=True,
        )
        self._start_cancellable_transfer(
            transfer_id,
            "Downloading",
            self._background_download_worker,
            on_success_callback,
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

        remote_path = f"{self.current_path.rstrip('/')}/{transfer.filename}"
        temp_dir = Path(tempfile.gettempdir()) / "ashyterm_edit"
        temp_dir.mkdir(exist_ok=True)
        local_path = temp_dir / transfer.filename
        transfer.local_path = str(local_path)
        transfer.remote_path = remote_path

        try:
            self.transfer_manager.start_transfer(transfer_id)
            completion_callback = partial(
                self._on_transfer_complete, on_success_callback
            )
            self.operations.start_download_with_progress(
                transfer_id,
                self.session_item,
                remote_path,
                local_path,
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
        self, local_path: Path, remote_path: Optional[str] = None
    ):
        try:
            local_gio_file = Gio.File.new_for_path(str(local_path))
            dialog = Gtk.AppChooserDialog.new(
                self.parent_window, Gtk.DialogFlags.MODAL, local_gio_file
            )
            dialog.set_title(_("Open With..."))

            def on_response(d, response_id):
                if response_id == Gtk.ResponseType.OK:
                    app_info = d.get_app_info()
                    if app_info:
                        if remote_path:  # This is a remote file
                            self._open_and_monitor_local_file(
                                local_path, remote_path, app_info
                            )
                        else:  # This is a local file
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

    def _on_editor_launched(
        self, _context, app_info, _platform_data, local_path, remote_path
    ):
        """Callback for when the editor process has been launched."""
        pid = app_info.get_pid()
        if pid > 0:
            self.logger.info(
                f"Editor for '{local_path.name}' launched with PID {pid}. Watching for exit."
            )
            GLib.child_watch_add(pid, self._on_editor_closed, (local_path, remote_path))
        else:
            self.logger.warning(
                f"Could not get PID for editor of '{local_path.name}'. Temp file will not be cleaned up automatically."
            )

    def _on_editor_closed(self, pid, status, user_data):
        """Callback for when the editor process exits. Cleans up resources."""
        local_path, remote_path = user_data
        self.logger.info(
            f"Editor process {pid} for '{local_path.name}' has exited. Cleaning up."
        )

        # Cancel the file monitor
        monitor = self.file_monitors.pop(remote_path, None)
        if monitor:
            monitor.cancel()

        # Delete the temporary file
        try:
            local_path.unlink()
            self.logger.info(f"Removed temporary file: {local_path}")
        except FileNotFoundError:
            pass
        except Exception as e:
            self.logger.error(f"Failed to remove temporary file {local_path}: {e}")

    def _open_and_monitor_local_file(
        self, local_path: Path, remote_path: str, app_info: Gio.AppInfo = None
    ):
        local_gio_file = Gio.File.new_for_path(str(local_path))

        if not app_info:
            content_type = Gio.content_type_guess(str(local_path), None)[0]
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)

        if app_info:
            launch_context = Gio.AppLaunchContext()
            launch_context.connect(
                "launched", self._on_editor_launched, local_path, remote_path
            )
            app_info.launch([local_gio_file], launch_context)
        else:
            subprocess.Popen(["xdg-open", str(local_path)])
            self.logger.warning(
                f"Opened {local_path.name} with xdg-open. Cannot monitor process exit for cleanup."
            )

        if remote_path in self.file_monitors:
            self.file_monitors[remote_path].cancel()

        monitor = local_gio_file.monitor(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_local_file_saved, remote_path, local_path)
        self.file_monitors[remote_path] = monitor

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
                target=self._upload_on_save_thread,
                args=(local_path, remote_path),
                daemon=True,
            ).start()

    def _on_save_upload_complete(self, transfer_id, success, message):
        """Callback to finalize transfer and show system notification."""
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
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
            )
            self.operations.start_upload_with_progress(
                transfer_id,
                self.session_item,
                local_path,
                remote_path,
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=self._on_save_upload_complete,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            self.logger.error(f"Failed to initiate upload-on-save: {e}")

    def _on_rename_action(self, _action, _param, file_item: FileItem):
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
                if self.bound_terminal:
                    command = f'mv "{old_path}" "{new_path}"\n'
                    self.bound_terminal.feed_child(command.encode("utf-8"))
                    self.parent_window.toast_overlay.add_toast(
                        Adw.Toast(title=_("Rename command sent to terminal"))
                    )
                    GLib.timeout_add(500, lambda: self.refresh(source="filemanager"))
