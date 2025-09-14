# ashyterm/ui/actions.py

import os
from typing import TYPE_CHECKING, List, Optional, Union

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..sessions.models import LayoutItem, SessionFolder, SessionItem
from ..utils.logger import get_logger, log_session_event
from ..utils.translation_utils import _
from .dialogs import (
    FolderEditDialog,
    MoveLayoutDialog,
    MoveSessionDialog,
    PreferencesDialog,
    SessionEditDialog,
    ShortcutsDialog,
)

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class WindowActions:
    """
    Handles all Gio.SimpleAction activations for the main window.
    This class isolates the action logic from the window's UI construction,
    component management, and other responsibilities.
    """

    def __init__(self, window: "CommTerminalWindow"):
        self.window = window
        self.logger = get_logger("ashyterm.ui.actions")

    def setup_actions(self):
        """Creates and registers all window-level actions."""
        actions_map = {
            "new-local-tab": self.new_local_tab,
            "close-tab": self.close_tab,
            "copy": self.copy,
            "paste": self.paste,
            "select-all": self.select_all,
            "split-horizontal": self.split_horizontal,
            "split-vertical": self.split_vertical,
            "close-pane": self.close_pane,
            "open-url": self.open_url,
            "copy-url": self.copy_url,
            "zoom-in": self.zoom_in,
            "zoom-out": self.zoom_out,
            "zoom-reset": self.zoom_reset,
            "connect-sftp": self.connect_sftp,
            "edit-session": self.edit_session,
            "duplicate-session": self.duplicate_session,
            "rename-session": self.rename_session,
            "move-session-to-folder": self.move_session_to_folder,
            "delete-session": self.delete_selected_items,
            "edit-folder": self.edit_folder,
            "rename-folder": self.rename_folder,
            "add-session-to-folder": self.add_session_to_folder,
            "delete-folder": self.delete_selected_items,
            "cut-item": self.cut_item,
            "copy-item": self.copy_item,
            "paste-item": self.paste_item,
            "paste-item-root": self.paste_item_root,
            "add-session-root": self.add_session_root,
            "add-folder-root": self.add_folder_root,
            "toggle-sidebar": self.toggle_sidebar_action,
            "toggle-file-manager": self.toggle_file_manager,
            "preferences": self.preferences,
            "shortcuts": self.shortcuts,
            "new-window": self.new_window,
            "save-layout": self.save_layout,
            "move-layout-to-folder": self.move_layout_to_folder,
        }
        for name, callback in actions_map.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.window.add_action(action)

        restore_action = Gio.SimpleAction.new(
            "restore_layout", GLib.VariantType.new("s")
        )
        restore_action.connect("activate", self.restore_layout)
        self.window.add_action(restore_action)

        delete_action = Gio.SimpleAction.new("delete_layout", GLib.VariantType.new("s"))
        delete_action.connect("activate", self.delete_layout)
        self.window.add_action(delete_action)

    def _close_sidebar_popover_if_active(self):
        """Helper to close the sidebar popover if it's active."""
        if hasattr(self.window, "sidebar_manager"):
            self.window.sidebar_manager._close_popover_if_active()

    # --- Tab and Pane Actions ---

    def new_local_tab(self, *_args):
        self.window.tab_manager.create_local_tab()

    def close_tab(self, *_args):
        if self.window.tab_manager.active_tab:
            self.window.tab_manager._on_tab_close_button_clicked(
                None, self.window.tab_manager.active_tab
            )

    def split_horizontal(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            self.window.tab_manager.split_horizontal(terminal)

    def split_vertical(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            self.window.tab_manager.split_vertical(terminal)

    def close_pane(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            self.window.tab_manager.close_pane(terminal)

    # --- Terminal Actions ---

    def copy(self, *_args):
        self.window.tab_manager.copy_from_current_terminal()

    def paste(self, *_args):
        self.window.tab_manager.paste_to_current_terminal()

    def select_all(self, *_args):
        self.window.tab_manager.select_all_in_current_terminal()

    def open_url(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                success = self.window.terminal_manager._open_hyperlink(url)
                if success:
                    self.logger.info(f"URL opened from context menu: {url}")
                delattr(terminal, "_context_menu_url")

    def copy_url(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                Gdk.Display.get_default().get_clipboard().set(url)
                delattr(terminal, "_context_menu_url")

    def zoom_in(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() * 1.1)
            self.window._update_font_sizer_widget()

    def zoom_out(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() / 1.1)
            self.window._update_font_sizer_widget()

    def zoom_reset(self, *_args):
        if terminal := self.window.tab_manager.get_selected_terminal():
            terminal.set_font_scale(1.0)
            self.window._update_font_sizer_widget()

    # --- Session Tree Actions ---

    def connect_sftp(self, *_args):
        self._close_sidebar_popover_if_active()
        selected_item = self.window.session_tree.get_selected_item()
        if isinstance(selected_item, SessionItem) and selected_item.is_ssh():
            self.window.tab_manager.create_sftp_tab(selected_item)
        else:
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("Please select an SSH session to connect with SFTP."))
            )

    def edit_session(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionItem
        ):
            found, position = self.window.session_store.find(item)
            if found:
                self._show_session_edit_dialog(item, position)

    def duplicate_session(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionItem
        ):
            self.window.session_operations.duplicate_session(item)
            self.window.refresh_tree()

    def rename_session(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionItem
        ):
            self._show_rename_dialog(item, True)

    def move_session_to_folder(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionItem
        ):
            MoveSessionDialog(
                self.window,
                item,
                self.window.folder_store,
                self.window.session_operations,
            ).present()

    def delete_selected_items(self, *_args):
        self._close_sidebar_popover_if_active()
        if items := self.window.session_tree.get_selected_items():
            self._show_delete_confirmation(items)

    def edit_folder(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionFolder
        ):
            found, position = self.window.folder_store.find(item)
            if found:
                self._show_folder_edit_dialog(item, position)

    def rename_folder(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionFolder
        ):
            self._show_rename_dialog(item, False)

    def add_session_to_folder(self, *_args):
        self._close_sidebar_popover_if_active()
        if isinstance(
            item := self.window.session_tree.get_selected_item(), SessionFolder
        ):
            self._show_session_edit_dialog(
                SessionItem(name=_("New Session"), folder_path=item.path), -1
            )

    def cut_item(self, *_args):
        self.window.session_tree._cut_selected_item()

    def copy_item(self, *_args):
        self.window.session_tree._copy_selected_item()

    def paste_item(self, *_args):
        target_path = ""
        if item := self.window.session_tree.get_selected_item():
            target_path = (
                item.path if isinstance(item, SessionFolder) else item.folder_path
            )
        self.window.session_tree._paste_item(target_path)

    def paste_item_root(self, *_args):
        self.window.session_tree._paste_item("")

    def add_session_root(self, *_args):
        self._close_sidebar_popover_if_active()
        self._show_session_edit_dialog(SessionItem(name=_("New Session")), -1)

    def add_folder_root(self, *_args):
        self._close_sidebar_popover_if_active()
        self._show_folder_edit_dialog(SessionFolder(name=_("New Folder")), None)

    # --- Window and Application Actions ---

    def toggle_sidebar_action(self, *_args):
        self.window.toggle_sidebar_button.set_active(
            not self.window.toggle_sidebar_button.get_active()
        )

    def toggle_file_manager(self, *_args):
        self.window.file_manager_button.set_active(
            not self.window.file_manager_button.get_active()
        )

    def preferences(self, *_args):
        dialog = PreferencesDialog(self.window, self.window.settings_manager)
        dialog.connect(
            "transparency-changed",
            lambda d, v: self.window.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "font-changed",
            lambda d, f: self.window.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.present()

    def shortcuts(self, *_args):
        dialog = ShortcutsDialog(self.window)
        dialog.present()

    def new_window(self, *_args):
        if app := self.window.get_application():
            if new_window := app.create_new_window():
                new_window.present()

    def save_layout(self, *_args):
        self._close_sidebar_popover_if_active()
        self.window.state_manager.save_current_layout()

    def restore_layout(self, action, param):
        self._close_sidebar_popover_if_active()
        layout_name = param.get_string()
        self.window.state_manager.restore_saved_layout(layout_name)

    def delete_layout(self, action, param):
        self._close_sidebar_popover_if_active()
        layout_name = param.get_string()
        self.window.state_manager.delete_saved_layout(layout_name)

    def move_layout_to_folder(self, action, param):
        self._close_sidebar_popover_if_active()
        layout_name = param.get_string()
        layout = next(
            (
                layout_item
                for layout_item in self.window.layouts
                if layout_item.name == layout_name
            ),
            None,
        )
        if layout:
            MoveLayoutDialog(self.window, layout, self.window.folder_store).present()

    # --- Helper Methods for Dialogs (Moved from CommTerminalWindow) ---

    def _show_session_edit_dialog(self, session: SessionItem, position: int) -> None:
        SessionEditDialog(
            self.window,
            session,
            self.window.session_store,
            position,
            self.window.folder_store,
        ).present()

    def _show_folder_edit_dialog(
        self, folder: Optional[SessionFolder], position: Optional[int]
    ) -> None:
        FolderEditDialog(
            self.window,
            self.window.folder_store,
            folder,
            position,
            is_new=position is None,
        ).present()

    def _show_rename_dialog(
        self, item: Union[SessionItem, SessionFolder], is_session: bool
    ) -> None:
        item_type = _("Session") if is_session else _("Folder")
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            title=_("Rename {type}").format(type=item_type),
            body=_('Enter new name for "{name}":').format(name=item.name),
        )
        entry = Gtk.Entry(text=item.name)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_default_response("rename")

        def on_response(dlg, response_id):
            if response_id == "rename":
                new_name = entry.get_text().strip()
                if new_name and new_name != item.name:
                    old_name = item.name
                    item.name = new_name
                    if is_session:
                        self.window.session_operations._save_changes()
                        log_session_event("renamed", f"{old_name} -> {new_name}")
                    else:
                        if isinstance(item, SessionFolder):
                            old_path = item.path
                            item.path = os.path.normpath(
                                f"{item.parent_path}/{new_name}"
                                if item.parent_path
                                else f"/{new_name}"
                            )
                            self.window.session_operations._update_child_paths(
                                old_path, item.path
                            )
                        self.window.session_operations._save_changes()
                    self.window.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _show_delete_confirmation(
        self, items: List[Union[SessionItem, SessionFolder, LayoutItem]]
    ) -> None:
        if not items:
            return
        count = len(items)
        title = _("Delete Item") if count == 1 else _("Delete Items")
        item = items[0]
        item_type = "Item"
        if isinstance(item, SessionItem):
            item_type = _("Session")
        elif isinstance(item, SessionFolder):
            item_type = _("Folder")
        elif isinstance(item, LayoutItem):
            item_type = _("Layout")

        if count == 1:
            title = _("Delete {type}").format(type=item_type)
            has_children = isinstance(
                item, SessionFolder
            ) and self.window.session_operations._folder_has_children(item.path)
            body_text = (
                _(
                    'The folder "{name}" is not empty. Are you sure you want to permanently delete it and all its contents?'
                ).format(name=item.name)
                if has_children
                else _('Are you sure you want to delete "{name}"?').format(
                    name=item.name
                )
            )
        else:
            body_text = _(
                "Are you sure you want to permanently delete these {count} items?"
            ).format(count=count)
            if any(
                isinstance(it, SessionFolder)
                and self.window.session_operations._folder_has_children(it.path)
                for it in items
            ):
                body_text += "\n\n" + _(
                    "This will also delete all contents of any selected folders."
                )

        dialog = Adw.MessageDialog(
            transient_for=self.window, title=title, body=body_text
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "delete":
                for item_to_delete in items:
                    if isinstance(item_to_delete, SessionFolder):
                        self.window.session_operations.remove_folder(
                            item_to_delete, force=True
                        )
                    elif isinstance(item_to_delete, SessionItem):
                        self.window.session_operations.remove_session(item_to_delete)
                    elif isinstance(item_to_delete, LayoutItem):
                        self.window.state_manager.delete_saved_layout(
                            item_to_delete.name, confirm=False
                        )
                self.window.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
