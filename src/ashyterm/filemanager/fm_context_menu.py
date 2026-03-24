# ashyterm/filemanager/fm_context_menu.py
"""Context menus, clipboard, create/rename/delete and permissions for FileManager."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk

from ..helpers import create_themed_popover_menu
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .manager import FileManager
    from .models import FileItem


class ContextMenuDelegate:
    """Manages context menus, clipboard ops, CRUD ops and permissions dialog."""

    def __init__(self, fm: FileManager) -> None:
        self.fm = fm

    # ── Right-click handlers ────────────────────────────────────────────────

    def on_item_right_click(self, gesture, n_press, x, y, list_item):
        fm = self.fm
        try:
            row = gesture.get_widget()
            if not row:
                self.show_general_context_menu(x, y)
                return

            try:
                translated_x, translated_y = row.translate_coordinates(
                    fm.column_view, x, y
                )
            except TypeError:
                translated_x, translated_y = x, y

            position = (
                list_item.get_position()
                if isinstance(list_item, Gtk.ListItem)
                else Gtk.INVALID_LIST_POSITION
            )

            if self._should_show_general_menu(list_item, position):
                self.show_general_context_menu(translated_x, translated_y)
                return

            actionable_items = self._handle_item_selection(position)
            if actionable_items:
                self.show_context_menu(actionable_items, translated_x, translated_y)
            else:
                self.show_general_context_menu(translated_x, translated_y)
        except Exception as e:
            fm.logger.error(f"Error in right-click handler: {e}")

    def on_column_view_background_click(self, gesture, n_press, x, y):
        fm = self.fm
        try:
            target = fm.column_view.pick(int(x), int(y), Gtk.PickFlags.DEFAULT)
            css_name = target.get_css_name() if isinstance(target, Gtk.Widget) else None
            fm.logger.info(
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

            if fm.selection_model:
                fm.selection_model.unselect_all()

            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.show_general_context_menu(x, y)
        except Exception as e:
            fm.logger.error(f"Error in background right-click handler: {e}")

    def on_scrolled_window_background_click(self, gesture, n_press, x, y):
        fm = self.fm
        try:
            widget = gesture.get_widget()
            tx, ty = x, y
            if widget:
                try:
                    translated = widget.translate_coordinates(fm.column_view, x, y)
                    if translated:
                        tx, ty = translated
                except Exception as e:
                    fm.logger.debug(f"Coordinate translation failed: {e}")

            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.on_column_view_background_click(gesture, n_press, tx, ty)
        except Exception as e:
            fm.logger.error(
                f"Error in scrolled window background right-click handler: {e}"
            )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _should_show_general_menu(self, list_item: Any, position: int) -> bool:
        fm = self.fm
        if position == Gtk.INVALID_LIST_POSITION:
            return True
        item = fm.sorted_store.get_item(position) if fm.sorted_store else None
        if item and item.name == "..":
            return True
        return False

    def _handle_item_selection(self, position: int) -> list:
        fm = self.fm
        selected = fm.get_selected_items()
        if position != Gtk.INVALID_LIST_POSITION:
            clicked_item = fm.sorted_store.get_item(position)
            if clicked_item and clicked_item not in selected:
                fm.selection_model.select_item(position, True)
                selected = [clicked_item]
        return [item for item in selected if item.name != ".."]

    # ── Context menu construction ───────────────────────────────────────────

    def create_context_menu_model(self, items: List[FileItem]):
        fm = self.fm
        menu = Gio.Menu()
        num_items = len(items)

        if num_items == 1 and not items[0].is_directory:
            open_section = Gio.Menu()
            open_section.append(_("Open/Edit"), "context.open_edit")
            open_section.append(_("Open With..."), "context.open_with")
            menu.append_section(None, open_section)

        if num_items == 1:
            rename_section = Gio.Menu()
            rename_section.append(_("Rename"), "context.rename")
            menu.append_section(None, rename_section)

        clipboard_section = Gio.Menu()
        clipboard_section.append(_("Copy"), "context.copy")
        clipboard_section.append(_("Cut"), "context.cut")
        if fm._can_paste():
            clipboard_section.append(_("Paste"), "context.paste")
        menu.append_section(None, clipboard_section)

        if fm._is_remote_session():
            download_section = Gio.Menu()
            download_section.append(_("Download"), "context.download")
            menu.append_section(None, download_section)

        permissions_section = Gio.Menu()
        permissions_section.append(_("Permissions"), "context.chmod")
        menu.append_section(None, permissions_section)

        delete_section = Gio.Menu()
        delete_item = Gio.MenuItem.new(_("Delete"), "context.delete")
        delete_item.set_attribute_value(
            "class", GLib.Variant("s", "destructive-action")
        )
        delete_section.append_item(delete_item)
        menu.append_section(None, delete_section)

        return menu

    def setup_action_group(
        self,
        popover,
        actions: dict,
        group_name: str = "context",
        items: Optional[List[FileItem]] = None,
    ):
        fm = self.fm
        action_group = Gio.SimpleActionGroup()
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            if name == "paste":
                action.set_enabled(fm._can_paste())
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

    def setup_context_actions(self, popover, items: List[FileItem]):
        fm = self.fm
        actions = {
            "open_edit": fm._on_open_edit_action,
            "open_with": fm._on_open_with_action,
            "rename": fm._on_rename_action,
            "copy": self.on_copy_action,
            "cut": self.on_cut_action,
            "paste": self.on_paste_action,
            "chmod": self.on_chmod_action,
            "download": fm._on_download_action,
            "delete": self.on_delete_action,
        }
        self.setup_action_group(popover, actions, "context", items)

    def setup_general_context_actions(self, popover):
        actions = {
            "create_folder": self.on_create_folder_action,
            "create_file": self.on_create_file_action,
            "paste": self.on_paste_action,
        }
        self.setup_action_group(popover, actions, "context")

    def _translate_coords_to_main_box(self, x, y):
        fm = self.fm
        point = Graphene.Point()
        point.x, point.y = x, y
        success, translated = fm.column_view.compute_point(fm.main_box, point)
        if success:
            return int(translated.x), int(translated.y)
        return int(x), int(y)

    def show_general_context_menu(self, x, y):
        fm = self.fm
        menu = Gio.Menu()

        creation_section = Gio.Menu()
        creation_section.append(_("Create Folder"), "context.create_folder")
        creation_section.append(_("Create File"), "context.create_file")
        menu.append_section(None, creation_section)

        if fm._can_paste():
            clipboard_section = Gio.Menu()
            clipboard_section.append(_("Paste"), "context.paste")
            menu.append_section(None, clipboard_section)

        popover = create_themed_popover_menu(menu, fm.main_box)
        fm._active_popover = popover
        popover.connect("closed", lambda *_: setattr(fm, "_active_popover", None))

        self.setup_general_context_actions(popover)

        tx, ty = self._translate_coords_to_main_box(x, y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = tx, ty, 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def show_context_menu(self, items: List[FileItem], x, y):
        fm = self.fm
        menu_model = self.create_context_menu_model(items)
        popover = create_themed_popover_menu(menu_model, fm.main_box)

        fm._active_popover = popover
        popover.connect("closed", lambda *_: setattr(fm, "_active_popover", None))

        self.setup_context_actions(popover, items)

        tx, ty = self._translate_coords_to_main_box(x, y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = tx, ty, 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    # ── CRUD actions ────────────────────────────────────────────────────────

    def on_create_folder_action(self, *_args):
        fm = self.fm
        base_path = PurePosixPath(fm.current_path or "/")

        def create_folder(name: str):
            target_path = str(base_path / name)
            command = ["mkdir", "-p", target_path]
            fm._execute_verified_command(command, command_type="mkdir")
            fm._show_toast(_("Create folder command sent to terminal"))

        fm._prompt_for_new_item(
            heading=_("Create Folder"),
            body=_("Enter a name for the new folder:"),
            default_name=_("New Folder"),
            confirm_label=_("Create"),
            callback=create_folder,
        )

    def on_create_file_action(self, *_args):
        fm = self.fm
        base_path = PurePosixPath(fm.current_path or "/")

        def create_file(name: str):
            target_path = str(base_path / name)
            command = ["touch", target_path]
            fm._execute_verified_command(command, command_type="touch")
            fm._show_toast(_("Create file command sent to terminal"))

        fm._prompt_for_new_item(
            heading=_("Create File"),
            body=_("Enter a name for the new file:"),
            default_name=_("New File"),
            confirm_label=_("Create"),
            callback=create_file,
        )

    # ── Clipboard ───────────────────────────────────────────────────────────

    def _set_clipboard_operation(
        self, items: List[FileItem], operation: str, toast_message: str
    ):
        fm = self.fm
        selectable_items = [item for item in items if item.name != ".."]
        if not selectable_items:
            fm._show_toast(
                _("No items selected to {operation}.").format(operation=operation)
            )
            return

        base_path = PurePosixPath(fm.current_path or "/")
        fm._clipboard_items = [
            {
                "name": item.name,
                "path": str(base_path / item.name),
                "is_directory": item.is_directory,
            }
            for item in selectable_items
        ]
        fm._clipboard_operation = operation
        fm._clipboard_session_key = fm._get_current_session_key()
        fm._show_toast(toast_message)

    def on_copy_action(self, _action, _param, items: List[FileItem]):
        self._set_clipboard_operation(items, "copy", _("Items copied to clipboard."))

    def on_cut_action(self, _action, _param, items: List[FileItem]):
        self._set_clipboard_operation(items, "cut", _("Items marked for move."))

    def on_paste_action(self):
        fm = self.fm
        if not fm._can_paste():
            fm._show_toast(_("Nothing to paste."))
            return

        destination_dir = str(PurePosixPath(fm.current_path or "/"))
        sources = [entry["path"] for entry in fm._clipboard_items]

        if fm._clipboard_operation == "cut":
            if all(
                str(PurePosixPath(source).parent) == destination_dir
                for source in sources
            ):
                fm._show_toast(_("Items are already in this location."))
                return
            command = ["mv"] + sources + [destination_dir]
            command_type = "mv"
            toast_message = _("Move command sent to terminal")
            fm._clear_clipboard()
        else:
            command = ["cp", "-a"] + sources + [destination_dir]
            command_type = "cp"
            toast_message = _("Copy command sent to terminal")

        fm._execute_verified_command(command, command_type=command_type)
        fm._show_toast(toast_message)

    # ── Delete ──────────────────────────────────────────────────────────────

    def on_delete_action(self, _action, _param, items: List[FileItem]):
        fm = self.fm
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
        dialog.present(fm.parent_window)

    def _on_delete_dialog_response(self, dialog, response, items: List[FileItem]):
        fm = self.fm
        if response == "delete":
            paths_to_delete = [
                f"{fm.current_path.rstrip('/')}/{item.name}" for item in items
            ]
            command = ["rm", "-rf"] + paths_to_delete
            fm._execute_verified_command(command, command_type="rm")
            fm.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Delete command sent to terminal"))
            )

    # ── Permissions ─────────────────────────────────────────────────────────

    def on_chmod_action(self, _action, _param, items: List[FileItem]):
        self._show_permissions_dialog(items)

    def _show_permissions_dialog(self, items: List[FileItem]):
        fm = self.fm
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
        self._owner_read = Gtk.CheckButton(label=_("Read"))
        self._owner_write = Gtk.CheckButton(label=_("Write"))
        self._owner_execute = Gtk.CheckButton(label=_("Execute"))
        owner_box.append(self._owner_read)
        owner_box.append(self._owner_write)
        owner_box.append(self._owner_execute)
        owner_row = Adw.ActionRow(child=owner_box)
        owner_group.add(owner_row)
        content_box.append(owner_group)

        group_group = Adw.PreferencesGroup(title=_("Group"))
        group_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self._group_read = Gtk.CheckButton(label=_("Read"))
        self._group_write = Gtk.CheckButton(label=_("Write"))
        self._group_execute = Gtk.CheckButton(label=_("Execute"))
        group_box.append(self._group_read)
        group_box.append(self._group_write)
        group_box.append(self._group_execute)
        group_row = Adw.ActionRow(child=group_box)
        group_group.add(group_row)
        content_box.append(group_group)

        others_group = Adw.PreferencesGroup(title=_("Others"))
        others_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self._others_read = Gtk.CheckButton(label=_("Read"))
        self._others_write = Gtk.CheckButton(label=_("Write"))
        self._others_execute = Gtk.CheckButton(label=_("Execute"))
        others_box.append(self._others_read)
        others_box.append(self._others_write)
        others_box.append(self._others_execute)
        others_row = Adw.ActionRow(child=others_box)
        others_group.add(others_row)
        content_box.append(others_group)

        self._mode_label = Gtk.Label(halign=Gtk.Align.CENTER, margin_top=12)
        content_box.append(self._mode_label)
        dialog.set_extra_child(content_box)

        if not is_multi:
            self._parse_permissions(items[0].permissions)
        self._update_mode_display()

        for checkbox in [
            self._owner_read, self._owner_write, self._owner_execute,
            self._group_read, self._group_write, self._group_execute,
            self._others_read, self._others_write, self._others_execute,
        ]:
            checkbox.connect("toggled", lambda _: self._update_mode_display())

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("apply", _("Apply"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_chmod_dialog_response, items)
        dialog.present(fm.parent_window)

    def _on_chmod_dialog_response(self, dialog, response, items: List[FileItem]):
        fm = self.fm
        if response == "apply":
            mode = self._calculate_mode()
            paths_to_change = [
                f"{fm.current_path.rstrip('/')}/{item.name}" for item in items
            ]
            command = ["chmod", mode] + paths_to_change
            fm._execute_verified_command(command, command_type="chmod")
            fm.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Chmod command sent to terminal"))
            )

    def _parse_permissions(self, perms_str: str):
        if len(perms_str) < 10:
            return
        self._owner_read.set_active(perms_str[1] == "r")
        self._owner_write.set_active(perms_str[2] == "w")
        self._owner_execute.set_active(perms_str[3] in "xs")
        self._group_read.set_active(perms_str[4] == "r")
        self._group_write.set_active(perms_str[5] == "w")
        self._group_execute.set_active(perms_str[6] in "xs")
        self._others_read.set_active(perms_str[7] == "r")
        self._others_write.set_active(perms_str[8] == "w")
        self._others_execute.set_active(perms_str[9] in "xs")

    def _calculate_mode(self) -> str:
        owner = (
            (4 * self._owner_read.get_active())
            + (2 * self._owner_write.get_active())
            + (1 * self._owner_execute.get_active())
        )
        group = (
            (4 * self._group_read.get_active())
            + (2 * self._group_write.get_active())
            + (1 * self._group_execute.get_active())
        )
        others = (
            (4 * self._others_read.get_active())
            + (2 * self._others_write.get_active())
            + (1 * self._others_execute.get_active())
        )
        return f"{owner}{group}{others}"

    def _update_mode_display(self):
        mode = self._calculate_mode()
        self._mode_label.set_text(f"Numeric mode: {mode}")

    # ── Keyboard handlers ───────────────────────────────────────────────────

    def on_search_key_pressed(self, controller, keyval, _keycode, state):
        fm = self.fm
        if not fm.selection_model:
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
        fm = self.fm
        selection = fm.selection_model.get_selection()
        if selection.get_size() > 0:
            return selection.get_nth(0)
        return Gtk.INVALID_LIST_POSITION

    def _handle_arrow_key_navigation(self, keyval, current_pos):
        fm = self.fm
        if current_pos == Gtk.INVALID_LIST_POSITION:
            new_pos = 0
        else:
            delta = -1 if keyval == Gdk.KEY_Up else 1
            new_pos = current_pos + delta

        if 0 <= new_pos < fm.sorted_store.get_n_items():
            fm.selection_model.select_item(new_pos, True)
            fm.column_view.scroll_to(new_pos, None, Gtk.ListScrollFlags.NONE, None)

        return Gdk.EVENT_STOP

    def _handle_enter_key_in_search(self, current_pos):
        fm = self.fm
        if fm.recursive_search_enabled and not fm._showing_recursive_results:
            return Gdk.EVENT_PROPAGATE

        if current_pos != Gtk.INVALID_LIST_POSITION:
            fm._on_row_activated(fm.column_view, current_pos)
        return Gdk.EVENT_STOP

    def _handle_backspace_in_search(self, controller):
        fm = self.fm
        if not fm.search_entry.get_text().strip():
            controller.stop_emission("key-pressed")
            fm._navigate_up_directory()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def on_column_view_key_pressed(self, controller, keyval, _keycode, state):
        fm = self.fm
        unicode_val = Gdk.keyval_to_unicode(keyval)
        if unicode_val != 0:
            char = chr(unicode_val)
            if char.isprintable():
                fm.search_entry.set_text(char)
                fm.search_entry.set_position(-1)
                fm.search_entry.grab_focus()
                return Gdk.EVENT_STOP

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if (
                fm.selection_model
                and fm.selection_model.get_selection().get_size() > 0
            ):
                pos = fm.selection_model.get_selection().get_nth(0)
                fm._on_row_activated(fm.column_view, pos)
                return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_BackSpace:
            if not fm.search_entry.get_text().strip():
                fm._navigate_up_directory()
                return Gdk.EVENT_STOP

        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            selected_items = [
                item for item in fm.get_selected_items() if item.name != ".."
            ]
            if selected_items:
                self.on_delete_action(None, None, selected_items)
                return Gdk.EVENT_STOP

        elif keyval == Gdk.KEY_Menu or (
            keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK
        ):
            selected_items = fm.get_selected_items()
            if selected_items:
                self.show_context_menu(selected_items, 0, 0)
            else:
                self.show_general_context_menu(0, 0)
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def on_column_view_key_released(self, controller, keyval, _keycode, state):
        fm = self.fm
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            selected_items = fm.get_selected_items()
            if selected_items:
                self.show_context_menu(selected_items, 0, 0)
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def on_hidden_toggle(self, _toggle_button):
        self.fm.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
