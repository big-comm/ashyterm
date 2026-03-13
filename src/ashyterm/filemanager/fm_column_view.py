# ashyterm/filemanager/fm_column_view.py
"""ColumnView creation, cell factories, sorters and filtering for FileManager."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk

from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .manager import FileManager


class ColumnViewDelegate:
    """Manages ColumnView setup, cell binding, sorting and filtering."""

    def __init__(self, fm: FileManager) -> None:
        self.fm = fm

    # ── Column / ColumnView creation ────────────────────────────────────────

    def create_column(self, title, sorter, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        factory.connect("unbind", self.unbind_cell)
        column = Gtk.ColumnViewColumn(
            title=title, factory=factory, expand=expand, resizable=True
        )
        column.set_sorter(sorter)
        return column

    def create_detailed_column_view(self) -> Gtk.ColumnView:
        fm = self.fm
        col_view = Gtk.ColumnView()
        col_view.set_show_column_separators(True)
        col_view.set_show_row_separators(True)

        fm.name_sorter = Gtk.CustomSorter.new(self.sort_by_name, None)
        fm.size_sorter = Gtk.CustomSorter.new(self.sort_by_size, None)
        fm.date_sorter = Gtk.CustomSorter.new(self.sort_by_date, None)
        fm.perms_sorter = Gtk.CustomSorter.new(self.sort_by_permissions, None)
        fm.owner_sorter = Gtk.CustomSorter.new(self.sort_by_owner, None)
        fm.group_sorter = Gtk.CustomSorter.new(self.sort_by_group, None)

        col_view.append_column(
            self.create_column(
                _("Name"),
                fm.name_sorter,
                self.setup_name_cell,
                self.bind_name_cell,
                expand=True,
            )
        )
        col_view.append_column(
            self.create_column(
                _("Size"),
                fm.size_sorter,
                self.setup_size_cell,
                self.bind_size_cell,
            )
        )
        col_view.append_column(
            self.create_column(
                _("Date Modified"),
                fm.date_sorter,
                self.setup_text_cell,
                self.bind_date_cell,
            )
        )
        col_view.append_column(
            self.create_column(
                _("Permissions"),
                fm.perms_sorter,
                self.setup_text_cell,
                self.bind_permissions_cell,
            )
        )
        col_view.append_column(
            self.create_column(
                _("Owner"),
                fm.owner_sorter,
                self.setup_text_cell,
                self.bind_owner_cell,
            )
        )
        col_view.append_column(
            self.create_column(
                _("Group"),
                fm.group_sorter,
                self.setup_text_cell,
                self.bind_group_cell,
            )
        )

        view_sorter = col_view.get_sorter()
        fm.sorted_store = Gtk.SortListModel(
            model=fm.filtered_store, sorter=view_sorter
        )
        fm.selection_model = Gtk.MultiSelection(model=fm.sorted_store)
        col_view.set_model(fm.selection_model)
        col_view.sort_by_column(
            col_view.get_columns().get_item(0), Gtk.SortType.ASCENDING
        )

        col_view.connect("activate", fm._on_row_activated)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", fm._on_column_view_key_pressed)
        key_controller.connect("key-released", fm._on_column_view_key_released)
        col_view.add_controller(key_controller)

        background_click = Gtk.GestureClick.new()
        background_click.set_button(Gdk.BUTTON_SECONDARY)
        background_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        background_click.set_exclusive(True)
        background_click.connect("pressed", fm._on_column_view_background_click)
        col_view.add_controller(background_click)

        return col_view

    # ── Cell setup / bind ───────────────────────────────────────────────────

    def setup_name_cell(self, factory, list_item):
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
                "released", self.fm._on_item_right_click, list_item
            )
            row.add_controller(right_click_gesture)
            row.right_click_gesture = right_click_gesture

    def unbind_cell(self, factory, list_item):
        """Disconnects handlers to prevent memory leaks."""
        row = list_item.get_child().get_parent()
        if row and hasattr(row, "right_click_gesture"):
            row.remove_controller(row.right_click_gesture)
            delattr(row, "right_click_gesture")

    def bind_name_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        box = list_item.get_child()
        icon = box.get_first_child()
        label = icon.get_next_sibling()
        link_icon = label.get_next_sibling()
        file_item = list_item.get_item()
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

    def setup_text_cell(self, factory, list_item):
        label = Gtk.Label(xalign=0.0)
        list_item.set_child(label)

    def setup_size_cell(self, factory, list_item):
        label = Gtk.Label(xalign=1.0)
        list_item.set_child(label)

    def bind_permissions_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item = list_item.get_item()
        label.set_text(file_item.permissions)

    def bind_owner_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item = list_item.get_item()
        label.set_text(file_item.owner)

    def bind_group_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item = list_item.get_item()
        label.set_text(file_item.group)

    def bind_size_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item = list_item.get_item()
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

    def bind_date_cell(self, factory, list_item):
        self._bind_cell_common(list_item)
        label = list_item.get_child()
        file_item = list_item.get_item()
        date_str = file_item.date.strftime("%Y-%m-%d %H:%M")
        label.set_text(date_str)

    # ── Filtering ───────────────────────────────────────────────────────────

    def setup_filtering_and_sorting(self):
        fm = self.fm
        fm.combined_filter = Gtk.CustomFilter()
        fm.combined_filter.set_filter_func(self.filter_files)
        fm.filtered_store.set_filter(fm.combined_filter)

    def on_hidden_toggle(self, _toggle_button):
        self.fm.combined_filter.changed(Gtk.FilterChange.DIFFERENT)

    def filter_files(self, file_item):
        fm = self.fm
        search_text = getattr(fm, "search_entry", None)
        search_term = search_text.get_text().lower().strip() if search_text else ""
        show_hidden = fm.hidden_files_toggle.get_active()

        if file_item.name == "..":
            return not search_term

        if not show_hidden and self.is_hidden_file(file_item):
            return False

        if not search_term:
            return True

        if fm.recursive_search_enabled and fm._showing_recursive_results:
            return True

        return search_term in file_item.name.lower()

    def is_hidden_file(self, file_item) -> bool:
        fm = self.fm
        if fm.recursive_search_enabled and fm._showing_recursive_results:
            name_to_check = file_item.name.split("/")[-1]
            return name_to_check.startswith(".")
        return file_item.name.startswith(".")

    # ── Sorting ─────────────────────────────────────────────────────────────

    def _dolphin_sort_priority(
        self, file_item_a, file_item_b, secondary_sort_func=None
    ):
        if file_item_a.name == "..":
            return -1
        if file_item_b.name == "..":
            return 1

        a_type = 0 if file_item_a.is_directory_like else 1
        b_type = 0 if file_item_b.is_directory_like else 1

        if a_type != b_type:
            return a_type - b_type

        if secondary_sort_func:
            return secondary_sort_func(file_item_a, file_item_b)

        name_a = file_item_a.name.lower()
        name_b = file_item_b.name.lower()
        return (name_a > name_b) - (name_a < name_b)

    def sort_by_name(self, a, b, *_):
        return self._dolphin_sort_priority(a, b)

    def sort_by_permissions(self, a, b, *_):
        return self._dolphin_sort_priority(
            a,
            b,
            lambda x, y: (
                (x.permissions > y.permissions) - (x.permissions < y.permissions)
            ),
        )

    def sort_by_owner(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.owner > y.owner) - (x.owner < y.owner)
        )

    def sort_by_group(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.group > y.group) - (x.group < y.group)
        )

    def sort_by_size(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.size > y.size) - (x.size < y.size)
        )

    def sort_by_date(self, a, b, *_):
        return self._dolphin_sort_priority(
            a, b, lambda x, y: (x.date > y.date) - (x.date < y.date)
        )

    # ── Selection helpers ───────────────────────────────────────────────────

    def get_selected_items(self) -> List:
        """Gets all selected items from the ColumnView."""
        fm = self.fm
        items = []
        if not hasattr(fm, "selection_model"):
            return items
        selection = fm.selection_model.get_selection()
        size = selection.get_size()
        for i in range(size):
            position = selection.get_nth(i)
            if item := fm.sorted_store.get_item(position):
                items.append(item)
        return items
