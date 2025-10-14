# ashyterm/ui/actions.py

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlparse

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..helpers import generate_unique_name
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
            "toggle-broadcast": self.toggle_broadcast,
            "show-command-guide": self.show_command_guide,
            "preferences": self.preferences,
            "shortcuts": self.shortcuts,
            "new-window": self.new_window,
            "save-layout": self.save_layout,
            "move-layout-to-folder": self.move_layout_to_folder,
            "export-sessions": self.export_sessions,
            "import-sessions": self.import_sessions,
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
        # MODIFIED: Get current working directory from the active local terminal
        working_dir = None
        active_terminal = self.window.tab_manager.get_selected_terminal()
        if active_terminal:
            terminal_id = getattr(active_terminal, "terminal_id", None)
            if terminal_id:
                info = self.window.terminal_manager.registry.get_terminal_info(
                    terminal_id
                )
                # Only use the CWD if the active terminal is a local one
                if info and info.get("type") == "local":
                    uri = active_terminal.get_current_directory_uri()
                    if uri:
                        parsed_uri = urlparse(uri)
                        if parsed_uri.scheme == "file":
                            working_dir = unquote(parsed_uri.path)
                            self.logger.info(
                                f"New local tab will open in directory: {working_dir}"
                            )

        self.window.tab_manager.create_local_tab(working_directory=working_dir)

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
    def toggle_broadcast(self, *_args):
        self.window.broadcast_button.set_active(
            not self.window.broadcast_button.get_active()
        )

    def show_command_guide(self, *_args):
        self.window._show_command_guide_dialog()

    def preferences(self, *_args):
        dialog = PreferencesDialog(self.window, self.window.settings_manager)
        dialog.connect(
            "transparency-changed",
            lambda d, v: self.window.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "headerbar-transparency-changed",
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

    def export_sessions(self, *_args):
        self._close_sidebar_popover_if_active()
        dialog = Gtk.FileDialog(title=_("Export Sessions"), modal=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        dialog.set_initial_name(f"ashyterm-sessions-{timestamp}.json")

        def on_response(file_dialog, result):
            try:
                gio_file = file_dialog.save_finish(result)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    self.window._show_error_dialog(_("Export Error"), e.message)
                return

            if not gio_file:
                return

            file_path = gio_file.get_path()
            if not file_path:
                self.window._show_error_dialog(
                    _("Export Error"),
                    _("Only local file paths are supported for export."),
                )
                return

            try:
                payload = self._build_sessions_export_payload()
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as e:
                self.logger.error(f"Failed to export sessions: {e}")
                self.window._show_error_dialog(
                    _("Export Error"), _("Could not export sessions.")
                )
            else:
                self._show_toast(_("Sessions exported successfully."))

        dialog.save(self.window, None, on_response)

    def import_sessions(self, *_args):
        self._close_sidebar_popover_if_active()
        dialog = Gtk.FileDialog(title=_("Import Sessions"), modal=True)

        def on_response(file_dialog, result):
            try:
                gio_file = file_dialog.open_finish(result)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    self.window._show_error_dialog(_("Import Error"), e.message)
                return

            if not gio_file:
                return

            try:
                if path := gio_file.get_path():
                    with open(path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                else:
                    success, contents, _etag = gio_file.load_contents(None)
                    if not success:
                        raise RuntimeError("Failed to read file contents.")
                    payload = json.loads(contents.decode("utf-8"))
            except Exception as e:
                self.logger.error(f"Failed to load sessions file: {e}")
                self.window._show_error_dialog(
                    _("Import Error"), _("Invalid or unreadable sessions file.")
                )
                return

            try:
                (
                    added_sessions,
                    skipped_sessions,
                    added_folders,
                    skipped_folders,
                ) = self._import_sessions_from_payload(payload)
            except ValueError as e:
                self.window._show_error_dialog(_("Import Error"), str(e))
                return
            except Exception as e:
                self.logger.error(f"Unexpected error importing sessions: {e}")
                self.window._show_error_dialog(
                    _("Import Error"), _("Could not import sessions.")
                )
                return

            if added_sessions or added_folders:
                self.window.refresh_tree()

            summary_parts = [
                _("Imported {count} sessions.").format(count=added_sessions),
                _("Imported {count} folders.").format(count=added_folders),
            ]
            summary = " ".join(summary_parts)
            if skipped_sessions or skipped_folders:
                summary += " " + _(
                    "Skipped {sessions} sessions and {folders} folders."
                ).format(sessions=skipped_sessions, folders=skipped_folders)
            self._show_toast(summary)

        dialog.open(self.window, None, on_response)

    def _show_toast(self, message: str) -> None:
        if hasattr(self.window, "toast_overlay") and self.window.toast_overlay:
            self.window.toast_overlay.add_toast(Adw.Toast(title=message))
        else:
            self.logger.info(message)

    def _build_sessions_export_payload(self) -> Dict[str, Any]:
        sessions_data: List[Dict[str, Any]] = []
        folders_data: List[Dict[str, Any]] = []

        for i in range(self.window.folder_store.get_n_items()):
            folder = self.window.folder_store.get_item(i)
            if isinstance(folder, SessionFolder):
                folders_data.append(folder.to_dict())

        for i in range(self.window.session_store.get_n_items()):
            session = self.window.session_store.get_item(i)
            if not isinstance(session, SessionItem):
                continue
            session_dict = session.to_dict()
            try:
                if session.uses_password_auth():
                    session_dict["auth_value"] = session.auth_value or ""
                else:
                    session_dict["auth_value"] = session_dict.get("auth_value", "")
            except Exception as e:
                self.logger.warning(
                    f"Could not retrieve authentication value for session '{session.name}': {e}"
                )
                session_dict["auth_value"] = ""
            sessions_data.append(session_dict)

        return {
            "version": 1,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "sessions": sessions_data,
            "folders": folders_data,
        }

    def _import_sessions_from_payload(
        self, payload: Dict[str, Any]
    ) -> tuple[int, int, int, int]:
        if not isinstance(payload, dict):
            raise ValueError(_("Invalid sessions file format."))

        sessions_blob = payload.get("sessions", [])
        folders_blob = payload.get("folders", [])
        if not isinstance(sessions_blob, list) or not isinstance(folders_blob, list):
            raise ValueError(_("Invalid sessions file format."))

        operations = self.window.session_operations
        folder_store = self.window.folder_store
        session_store = self.window.session_store

        existing_folder_paths = {
            folder_store.get_item(i).path
            for i in range(folder_store.get_n_items())
            if isinstance(folder_store.get_item(i), SessionFolder)
        }

        added_folders = 0
        skipped_folders = 0
        sorted_folders = sorted(
            (f for f in folders_blob if isinstance(f, dict)),
            key=lambda item: (item.get("path") or "").count("/"),
        )
        for folder_data in sorted_folders:
            try:
                folder_item = SessionFolder.from_dict(folder_data)
            except Exception as e:
                self.logger.error(f"Failed to import folder: {e}")
                skipped_folders += 1
                continue
            if folder_item.path in existing_folder_paths:
                skipped_folders += 1
                continue
            result = operations.add_folder(folder_item)
            if result and result.success:
                existing_folder_paths.add(folder_item.path)
                added_folders += 1
            else:
                if result and result.message:
                    self.logger.warning(
                        f"Failed to import folder '{folder_item.name}': {result.message}"
                    )
                skipped_folders += 1

        existing_names: Dict[str, set[str]] = {}
        existing_by_key: Dict[tuple[str, str], SessionItem] = {}
        for i in range(session_store.get_n_items()):
            item = session_store.get_item(i)
            if isinstance(item, SessionItem):
                existing_names.setdefault(item.folder_path, set()).add(item.name)
                existing_by_key[(item.folder_path or "", item.name)] = item

        added_sessions = 0
        skipped_sessions = 0

        def should_replace_session(existing_session: SessionItem) -> bool:
            dialog = Adw.MessageDialog(
                transient_for=self.window,
                heading=_("Session Already Exists"),
                body=_(
                    'A session named "{name}" already exists. Do you want to replace it?'
                ).format(name=existing_session.name),
                close_response="skip",
            )
            dialog.add_response("skip", _("Skip"))
            dialog.add_response("replace", _("Replace"))
            dialog.set_response_appearance("replace", Adw.ResponseAppearance.SUGGESTED)

            user_choice = {"response": "skip"}

            def on_response(dlg, response_id):
                user_choice["response"] = response_id
                dlg.destroy()

            dialog.connect("response", on_response)
            dialog.present()

            context = GLib.MainContext.default()
            while dialog.get_visible():
                context.iteration(True)

            return user_choice["response"] == "replace"

        for session_data in (s for s in sessions_blob if isinstance(s, dict)):
            data_copy = dict(session_data)
            folder_path = data_copy.get("folder_path", "")
            desired_name = data_copy.get("name") or _("Imported Session")
            names_set = existing_names.setdefault(folder_path, set())
            session_key = (folder_path, desired_name)
            existing_session = existing_by_key.get(session_key)

            if existing_session and desired_name:
                if should_replace_session(existing_session):
                    try:
                        self.window.session_operations.remove_session(existing_session)
                        existing_names.setdefault(folder_path, set()).discard(
                            existing_session.name
                        )
                        existing_by_key.pop(session_key, None)
                    except Exception as e:
                        self.logger.error(
                            f"Failed to replace existing session '{existing_session.name}': {e}"
                        )
                        skipped_sessions += 1
                        continue
                    data_copy["name"] = desired_name
                else:
                    skipped_sessions += 1
                    continue
            else:
                unique_name = generate_unique_name(desired_name, names_set)
                data_copy["name"] = unique_name

            exported_auth = data_copy.get("auth_value", "")

            try:
                session_item = SessionItem.from_dict(data_copy)
            except Exception as e:
                self.logger.error(f"Failed to create session from data: {e}")
                skipped_sessions += 1
                continue

            try:
                if session_item.uses_password_auth():
                    session_item.auth_value = exported_auth or ""
                else:
                    session_item.auth_value = exported_auth
            except Exception as e:
                self.logger.error(
                    f"Failed to restore authentication for session '{session_item.name}': {e}"
                )
                skipped_sessions += 1
                continue

            result = operations.add_session(session_item)
            if result and result.success:
                added_sessions += 1
                names_set.add(session_item.name)
                existing_by_key[(session_item.folder_path or "", session_item.name)] = session_item
            else:
                if result and result.message:
                    self.logger.warning(
                        f"Failed to import session '{session_item.name}': {result.message}"
                    )
                skipped_sessions += 1

        return added_sessions, skipped_sessions, added_folders, skipped_folders

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
        def should_replace_session(existing_session: SessionItem, new_data: Dict[str, Any]) -> bool:
            dialog = Adw.MessageDialog(
                transient_for=self.window,
                heading=_("Session Already Exists"),
                body=_('A session named "{name}" already exists. Do you want to replace it?').format(
                    name=existing_session.name
                ),
                close_response="cancel",
            )
            dialog.add_response("replace", _("Replace"))
            dialog.add_response("skip", _("Skip"))
            dialog.set_response_appearance("replace", Adw.ResponseAppearance.SUGGESTED)

            user_choice = {"response": "skip"}

            def on_response(dlg, response_id):
                user_choice["response"] = response_id
                dlg.destroy()

            dialog.connect("response", on_response)
            dialog.present()

            loop = GLib.MainLoop()

            def on_destroy(_dlg):
                loop.quit()

            dialog.connect("destroy", on_destroy)
            loop.run()

            return user_choice["response"] == "replace"
