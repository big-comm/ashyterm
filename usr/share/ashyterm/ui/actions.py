# ashyterm/ui/actions.py

import os
from typing import TYPE_CHECKING, List, Optional, Union

from gi.repository import Adw, Gdk, Gtk

from ..sessions.models import LayoutItem, SessionFolder, SessionItem
from ..utils.logger import get_logger, log_session_event
from ..utils.translation_utils import _
from .dialogs import (
    FolderEditDialog,
    MoveLayoutDialog,
    MoveSessionDialog,
    PreferencesDialog,
    SessionEditDialog,
)

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class WindowActions:
    """
    Handles all Gio.SimpleAction activations for the main window.

    This class isolates the action logic from the window's UI construction
    and component management, promoting the Single Responsibility Principle.
    """

    def __init__(self, window: "CommTerminalWindow"):
        """
        Initializes the action handler.

        Args:
            window: The main CommTerminalWindow instance to operate on.
        """
        self.window = window
        self.logger = get_logger("ashyterm.ui.actions")

        # Create convenient shortcuts to the window's managers
        self.settings_manager = window.settings_manager
        self.terminal_manager = window.terminal_manager
        self.tab_manager = window.tab_manager
        self.session_tree = window.session_tree
        self.session_operations = window.session_operations
        self.session_store = window.session_store
        self.folder_store = window.folder_store

    # --- Tab and Pane Actions ---

    def new_local_tab(self, _action, _param) -> None:
        self.tab_manager.create_local_tab()

    def close_tab(self, _action, _param) -> None:
        if self.tab_manager.active_tab:
            self.tab_manager._on_tab_close_button_clicked(
                None, self.tab_manager.active_tab
            )

    def next_tab(self, _action, _param) -> None:
        self.tab_manager.select_next_tab()

    def previous_tab(self, _action, _param) -> None:
        self.tab_manager.select_previous_tab()

    def split_horizontal(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.split_horizontal(terminal)

    def split_vertical(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.split_vertical(terminal)

    def close_pane(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.close_pane(terminal)

    # --- Terminal Actions ---

    def copy(self, _action, _param) -> None:
        self.tab_manager.copy_from_current_terminal()

    def paste(self, _action, _param) -> None:
        self.tab_manager.paste_to_current_terminal()

    def select_all(self, _action, _param) -> None:
        self.tab_manager.select_all_in_current_terminal()

    def open_url(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                launcher = Gtk.UriLauncher.new(url)
                launcher.launch(self.window, None, None, None)
                delattr(terminal, "_context_menu_url")

    def copy_url(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                Gdk.Display.get_default().get_clipboard().set(url)
                delattr(terminal, "_context_menu_url")

    def zoom_in(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() * 1.1)
            self.window._update_font_sizer_widget()

    def zoom_out(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() / 1.1)
            self.window._update_font_sizer_widget()

    def zoom_reset(self, _action, _param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(1.0)
            self.window._update_font_sizer_widget()

    # --- Session Tree Actions ---

    def connect_sftp(self, _action, _param) -> None:
        selected_item = self.session_tree.get_selected_item()
        if isinstance(selected_item, SessionItem) and selected_item.is_ssh():
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("SFTP not implemented yet"))
            )
            # Auto-hide sidebar popup if it's open
            if (
                hasattr(self.window, "sidebar_popover")
                and self.window.sidebar_popover.get_visible()
            ):
                self.window.sidebar_popover.popdown()

    def edit_session(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            found, position = self.session_store.find(item)
            if found:
                self._show_session_edit_dialog(item, position)

    def duplicate_session(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self.session_operations.duplicate_session(item)
            self.window.refresh_tree()
            # Auto-hide sidebar popup if it's open
            if (
                hasattr(self.window, "sidebar_popover")
                and self.window.sidebar_popover.get_visible()
            ):
                self.window.sidebar_popover.popdown()

    def rename_session(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self._show_rename_dialog(item, True)

    def move_session_to_folder(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            MoveSessionDialog(
                self.window, item, self.folder_store, self.session_operations
            ).present()
            # Auto-hide sidebar popup if it's open
            if (
                hasattr(self.window, "sidebar_popover")
                and self.window.sidebar_popover.get_visible()
            ):
                self.window.sidebar_popover.popdown()

    def delete_selected_items(self, _action=None, _param=None) -> None:
        if items := self.session_tree.get_selected_items():
            self._show_delete_confirmation(items)

    def edit_folder(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            found, position = self.folder_store.find(item)
            if found:
                self._show_folder_edit_dialog(item, position)

    def rename_folder(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            self._show_rename_dialog(item, False)

    def add_session_to_folder(self, _action, _param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            self._show_session_edit_dialog(
                SessionItem(name=_("New Session"), folder_path=item.path), -1
            )

    def cut_item(self, _action, _param) -> None:
        self.session_tree._cut_selected_item()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def copy_item(self, _action, _param) -> None:
        self.session_tree._copy_selected_item()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def paste_item(self, _action, _param) -> None:
        target_path = ""
        if item := self.session_tree.get_selected_item():
            target_path = (
                item.path if isinstance(item, SessionFolder) else item.folder_path
            )
        self.session_tree._paste_item(target_path)
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def paste_item_root(self, _action, _param) -> None:
        self.session_tree._paste_item("")
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def add_session_root(self, _action, _param) -> None:
        self._show_session_edit_dialog(SessionItem(name=_("New Session")), -1)

    def add_folder_root(self, _action, _param) -> None:
        self._show_folder_edit_dialog(SessionFolder(name=_("New Folder")), None)

    # --- Window and Application Actions ---

    def toggle_sidebar_action(self, _action, _param) -> None:
        self.window.toggle_sidebar_button.set_active(
            not self.window.toggle_sidebar_button.get_active()
        )

    def toggle_file_manager(self, _action, _param) -> None:
        """Toggles the visibility of the file manager via its button."""
        if hasattr(self.window, "file_manager_button"):
            self.window.file_manager_button.set_active(
                not self.window.file_manager_button.get_active()
            )

    def preferences(self, _action, _param) -> None:
        dialog = PreferencesDialog(self.window, self.settings_manager)
        dialog.connect(
            "transparency-changed",
            lambda d, v: self.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "font-changed",
            lambda d, f: self.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "shortcut-changed", lambda d: self.window._update_keyboard_shortcuts()
        )
        dialog.present()

    def shortcuts(self, _action, _param) -> None:
        shortcuts_window = Gtk.ShortcutsWindow(transient_for=self.window, modal=True)
        section = Gtk.ShortcutsSection(
            title=_("Keyboard Shortcuts"), section_name="shortcuts"
        )
        terminal_group = Gtk.ShortcutsGroup(title=_("Terminal"))
        for title, accel in [
            (_("New Tab"), "<Control>t"),
            (_("Close Tab"), "<Control>w"),
            (_("New Window"), "<Control>n"),
            (_("Copy"), "<Control><Shift>c"),
            (_("Paste"), "<Control><Shift>v"),
            (_("Select All"), "<Control><Shift>a"),
        ]:
            terminal_group.append(Gtk.ShortcutsShortcut(title=title, accelerator=accel))

        app_group = Gtk.ShortcutsGroup(title=_("Application"))
        for title, accel in [
            (_("Preferences"), "<Control>comma"),
            (_("Toggle Sidebar"), "<Control><Shift>h"),
            (_("Quit"), "<Control>q"),
        ]:
            app_group.append(Gtk.ShortcutsShortcut(title=title, accelerator=accel))
        section.append(terminal_group)
        section.append(app_group)
        shortcuts_window.present()

    def new_window(self, _action, _param) -> None:
        if app := self.window.get_application():
            if new_window := app.create_new_window():
                new_window.present()

    def save_layout(self, _action, _param) -> None:
        self.window.save_current_layout()

    def restore_layout(self, action, param) -> None:
        layout_name = param.get_string()
        self.window.restore_saved_layout(layout_name)

    def delete_layout(self, action, param) -> None:
        layout_name = param.get_string()
        self.window.delete_saved_layout(layout_name)

    def move_layout_to_folder(self, action, param) -> None:
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
            MoveLayoutDialog(self.window, layout, self.folder_store).present()
            # Auto-hide sidebar popup if it's open
            if (
                hasattr(self.window, "sidebar_popover")
                and self.window.sidebar_popover.get_visible()
            ):
                self.window.sidebar_popover.popdown()

    # --- Helper Methods for Dialogs ---

    def _show_session_edit_dialog(self, session: SessionItem, position: int) -> None:
        SessionEditDialog(
            self.window, session, self.session_store, position, self.folder_store
        ).present()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def _show_folder_edit_dialog(
        self, folder: Optional[SessionFolder], position: Optional[int]
    ) -> None:
        FolderEditDialog(
            self.window, self.folder_store, folder, position, is_new=position is None
        ).present()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

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
                        self.session_operations._save_changes_with_backup(
                            "Session renamed"
                        )
                        log_session_event("renamed", f"{old_name} -> {new_name}")
                    else:
                        if isinstance(item, SessionFolder):
                            old_path = item.path
                            item.path = os.path.normpath(
                                f"{item.parent_path}/{new_name}"
                                if item.parent_path
                                else f"/{new_name}"
                            )
                            self.session_operations._update_child_paths(
                                old_path, item.path
                            )
                        self.session_operations._save_changes_with_backup(
                            "Folder renamed"
                        )
                    self.window.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()

    def _show_delete_confirmation(
        self, items: List[Union[SessionItem, SessionFolder]]
    ) -> None:
        if not items:
            return
        count = len(items)
        title = _("Delete Item") if count == 1 else _("Delete Items")
        if count == 1:
            item = items[0]
            item_type = _("Session") if isinstance(item, SessionItem) else _("Folder")
            if isinstance(item, LayoutItem):
                item_type = _("Layout")
            title = _("Delete {type}").format(type=item_type)
            has_children = isinstance(
                item, SessionFolder
            ) and self.session_operations._folder_has_children(item.path)
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
                and self.session_operations._folder_has_children(it.path)
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
                for item in items:
                    if isinstance(item, SessionFolder):
                        self.session_operations.remove_folder(item, force=True)
                    elif isinstance(item, SessionItem):
                        self.session_operations.remove_session(item)
                    elif isinstance(item, LayoutItem):
                        self.window.delete_saved_layout(item.name, confirm=False)
                self.window.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
        # Auto-hide sidebar popup if it's open
        if (
            hasattr(self.window, "sidebar_popover")
            and self.window.sidebar_popover.get_visible()
        ):
            self.window.sidebar_popover.popdown()
