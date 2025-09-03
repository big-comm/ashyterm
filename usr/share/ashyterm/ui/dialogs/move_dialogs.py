# ashyterm/ui/dialogs/move_dialogs.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from ...sessions.models import LayoutItem, SessionItem
from ...sessions.operations import SessionOperations
from ...utils.translation_utils import _
from .base_dialog import BaseDialog


class MoveSessionDialog(BaseDialog):
    """A dialog to move a session to a different folder."""

    def __init__(
        self,
        parent_window,
        session_to_move: SessionItem,
        folder_store: Gio.ListStore,
        operations: SessionOperations,
    ):
        title = _("Move Session")
        super().__init__(parent_window, title, default_width=400, default_height=250)
        self.session_to_move = session_to_move
        self.folder_store = folder_store
        self.operations = operations
        self.folder_paths_map: dict[str, str] = {}
        self._setup_ui()
        self.logger.info(f"Move session dialog opened for '{session_to_move.name}'")

    def _setup_ui(self):
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        group = Adw.PreferencesGroup(
            title=_("Select Destination"),
            description=_("Choose the folder to move the session '{name}' to.").format(
                name=self.session_to_move.name
            ),
        )
        main_box.append(group)
        folder_row = Adw.ComboRow(
            title=_("Destination Folder"),
            subtitle=_("Select a folder or 'Root' for the top level"),
        )
        self.folder_combo = folder_row
        group.add(folder_row)
        self._populate_folder_combo()
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.set_valign(Gtk.Align.CENTER)
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        move_button = Gtk.Button(label=_("Move"), css_classes=["suggested-action"])
        move_button.set_valign(Gtk.Align.CENTER)
        move_button.connect("clicked", self._on_move_clicked)
        action_bar.pack_end(move_button)
        self.set_default_widget(move_button)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(main_box)
        content_box.append(action_bar)
        self.set_content(content_box)

    def _populate_folder_combo(self):
        folder_model = Gtk.StringList()
        folder_model.append(_("Root"))
        self.folder_paths_map = {_("Root"): ""}
        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        selected_index = 0
        current_path = self.session_to_move.folder_path

        for i, folder in enumerate(folders):
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path
            if folder.path == current_path:
                selected_index = i + 1

        self.folder_combo.set_model(folder_model)
        self.folder_combo.set_selected(selected_index)

    def _on_move_clicked(self, button):
        selected_item = self.folder_combo.get_selected_item()
        if not selected_item:
            return
        display_name = selected_item.get_string()
        target_folder_path = self.folder_paths_map.get(display_name, "")
        if (
            self.session_to_move
            and target_folder_path == self.session_to_move.folder_path
        ):
            self.close()
            return
        result = self.operations.move_session_to_folder(
            self.session_to_move, target_folder_path
        )
        if result.success:
            if (
                result.warnings
                and hasattr(self.parent_window, "get_toast_overlay")
                and (overlay := self.parent_window.get_toast_overlay())
            ):
                overlay.add_toast(Adw.Toast(title=result.warnings[0]))
            self.logger.info(
                f"Session '{self.session_to_move.name}' moved to '{target_folder_path}'"
            )
            self.parent_window.refresh_tree()
            self.close()
        else:
            self._show_error_dialog(_("Move Failed"), result.message)


class MoveLayoutDialog(BaseDialog):
    """A dialog to move a layout to a different folder."""

    def __init__(
        self,
        parent_window,
        layout_to_move: LayoutItem,
        folder_store: Gio.ListStore,
    ):
        title = _("Move Layout")
        super().__init__(parent_window, title, default_width=400, default_height=250)
        self.layout_to_move = layout_to_move
        self.folder_store = folder_store
        self.folder_paths_map: dict[str, str] = {}
        self._setup_ui()
        self.logger.info(f"Move layout dialog opened for '{layout_to_move.name}'")

    def _setup_ui(self):
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        group = Adw.PreferencesGroup(
            title=_("Select Destination"),
            description=_("Choose the folder to move the layout '{name}' to.").format(
                name=self.layout_to_move.name
            ),
        )
        main_box.append(group)
        folder_row = Adw.ComboRow(
            title=_("Destination Folder"),
            subtitle=_("Select a folder or 'Root' for the top level"),
        )
        self.folder_combo = folder_row
        group.add(folder_row)
        self._populate_folder_combo()
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.set_valign(Gtk.Align.CENTER)
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        move_button = Gtk.Button(label=_("Move"), css_classes=["suggested-action"])
        move_button.set_valign(Gtk.Align.CENTER)
        move_button.connect("clicked", self._on_move_clicked)
        action_bar.pack_end(move_button)
        self.set_default_widget(move_button)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(main_box)
        content_box.append(action_bar)
        self.set_content(content_box)

    def _populate_folder_combo(self):
        folder_model = Gtk.StringList()
        folder_model.append(_("Root"))
        self.folder_paths_map = {_("Root"): ""}
        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        selected_index = 0
        current_path = self.layout_to_move.folder_path

        for i, folder in enumerate(folders):
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path
            if folder.path == current_path:
                selected_index = i + 1

        self.folder_combo.set_model(folder_model)
        self.folder_combo.set_selected(selected_index)

    def _on_move_clicked(self, button):
        selected_item = self.folder_combo.get_selected_item()
        if not selected_item:
            return
        display_name = selected_item.get_string()
        target_folder_path = self.folder_paths_map.get(display_name, "")

        if (
            self.layout_to_move
            and target_folder_path == self.layout_to_move.folder_path
        ):
            self.close()
            return

        self.parent_window.move_layout(
            self.layout_to_move.name,
            self.layout_to_move.folder_path,
            target_folder_path,
        )
        self.close()
