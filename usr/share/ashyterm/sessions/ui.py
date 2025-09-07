# ashyterm/filemanager/ui.py

from gi.repository import Adw, Gdk, Gio, Gtk, Pango

from ..utils.translation_utils import _
from .models import FileItem


class FileManagerUI:
    """Handles the construction of the FileManager's GTK widgets."""

    def __init__(self, is_remote_session: bool):
        self.is_remote_session = is_remote_session
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        self.revealer.set_size_request(-1, 200)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.set_size_request(-1, 200)

        scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        self.store = Gio.ListStore.new(FileItem)
        self.column_view = self._create_detailed_column_view()
        scrolled_window.set_child(self.column_view)

        action_bar = self._create_action_bar()

        self.progress_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self.progress_revealer.add_css_class("background")
        main_box.append(self.progress_revealer)

        main_box.append(scrolled_window)
        main_box.append(action_bar)
        self.revealer.set_child(main_box)

    def _create_action_bar(self) -> Gtk.ActionBar:
        action_bar = Gtk.ActionBar()

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text(_("Refresh"))
        action_bar.pack_start(self.refresh_button)

        self.hidden_files_toggle = Gtk.ToggleButton()
        self.hidden_files_toggle.set_icon_name("view-reveal-symbolic")
        self.hidden_files_toggle.set_tooltip_text(_("Show hidden files"))
        action_bar.pack_start(self.hidden_files_toggle)

        self.breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.breadcrumb_box.add_css_class("breadcrumb-trail")
        self.breadcrumb_box.set_hexpand(True)
        action_bar.pack_start(self.breadcrumb_box)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Filter..."), max_width_chars=12
        )
        action_bar.pack_end(self.search_entry)

        self.history_button = Gtk.Button.new_from_icon_name(
            "folder-download-symbolic"
        )
        self.history_button.set_tooltip_text(_("Transfer History"))
        action_bar.pack_end(self.history_button)

        if self.is_remote_session:
            self.upload_button = Gtk.Button.new_from_icon_name(
                "document-send-symbolic"
            )
            self.upload_button.set_tooltip_text(_("Upload Files"))
            action_bar.pack_end(self.upload_button)

        return action_bar

    def _create_detailed_column_view(self) -> Gtk.ColumnView:
        col_view = Gtk.ColumnView(show_column_separators=True, show_row_separators=True)
        col_view.append_column(
            self._create_column(
                _("Name"), "name-sorter", self._setup_name_cell, self._bind_name_cell, True
            )
        )
        col_view.append_column(
            self._create_column(
                _("Size"), "size-sorter", self._setup_size_cell, self._bind_size_cell
            )
        )
        col_view.append_column(
            self._create_column(
                _("Date Modified"), "date-sorter", self._setup_text_cell, self._bind_date_cell
            )
        )
        # Add other columns similarly...
        return col_view

    def _create_column(self, title, sorter_name, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        column = Gtk.ColumnViewColumn(
            title=title, factory=factory, expand=expand, resizable=True
        )
        # Sorter will be set by the manager
        column.sorter_name = sorter_name
        return column

    def _setup_name_cell(self, factory, list_item):
        box = Gtk.Box(spacing=6, orientation=Gtk.Orientation.HORIZONTAL)
        box.append(Gtk.Image())
        label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        box.append(label)
        list_item.set_child(box)

    def _bind_name_cell(self, factory, list_item):
        box = list_item.get_child()
        icon = box.get_first_child()
        label = icon.get_next_sibling()
        file_item: FileItem = list_item.get_item()
        icon.set_from_icon_name(file_item.icon_name)
        label.set_text(file_item.name)

    def _setup_text_cell(self, factory, list_item):
        label = Gtk.Label(xalign=0.0)
        list_item.set_child(label)

    def _setup_size_cell(self, factory, list_item):
        label = Gtk.Label(xalign=1.0)
        list_item.set_child(label)

    def _bind_size_cell(self, factory, list_item):
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        size = file_item.size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024**2:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / 1024**2:.1f} MB"
        label.set_text(size_str)

    def _bind_date_cell(self, factory, list_item):
        label = list_item.get_child()
        file_item: FileItem = list_item.get_item()
        label.set_text(file_item.date.strftime("%Y-%m-%d %H:%M"))

    def update_breadcrumb(self, current_path, on_breadcrumb_button_clicked):
        child = self.breadcrumb_box.get_first_child()
        while child:
            self.breadcrumb_box.remove(child)
            child = self.breadcrumb_box.get_first_child()

        path = Path(current_path)
        accumulated_path = Path()
        for i, part in enumerate(path.parts):
            display_name = part if i > 0 else "/"
            if i == 0 and part == "/":
                accumulated_path = Path(part)
            else:
                accumulated_path = accumulated_path / part
                separator = Gtk.Label(label="›", css_classes=["dim-label"])
                self.breadcrumb_box.append(separator)

            btn = Gtk.Button(label=display_name, css_classes=["flat"])
            btn.connect("clicked", on_breadcrumb_button_clicked, str(accumulated_path))
            self.breadcrumb_box.append(btn)
