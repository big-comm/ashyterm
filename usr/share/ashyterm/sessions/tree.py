import threading
import time
from typing import Optional, Callable, Dict, Tuple, Union, Any, List

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gio, Gdk, GLib, GObject, Adw

from .models import SessionItem, SessionFolder
from .operations import SessionOperations, OperationResult

# Import new utility systems
from ..utils.logger import get_logger, log_session_event, log_error_with_context
from ..utils.exceptions import (
    UIError, SessionError, ValidationError, AshyTerminalError,
    handle_exception, create_error_from_exception, ErrorCategory, ErrorSeverity
)
from ..utils.security import validate_session_data, create_security_auditor
from ..utils.platform import get_platform_info, is_windows
from ..utils.translation_utils import _
from ..ui.menus import create_session_menu, create_folder_menu, create_root_menu
from ..utils import generate_unique_name


def _get_children_model(item, user_data):
    """Callback for Gtk.TreeListModel to get children of an item."""
    if hasattr(item, 'children'):
        return item.children
    return None


class SessionTreeView:
    """Modern tree view manager using Gtk.ColumnView for sessions and folders."""

    def __init__(
        self,
        parent_window,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
    ):
        self.logger = get_logger('ashyterm.sessions.tree')
        self.parent_window = parent_window
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager
        
        self.operations = SessionOperations(
            session_store, folder_store, settings_manager
        )

        self.root_store = Gio.ListStore.new(GObject.GObject)
        self.tree_model = Gtk.TreeListModel.new(
            self.root_store,
            passthrough=False,
            autoexpand=False,
            create_func=_get_children_model,
            user_data=None
        )
        
        self.column_view = self._create_column_view()
        self.selection_model = self.column_view.get_model()

        # State management
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut = False
        self._clipboard_timestamp = 0
        self._selection_anchor = None
        self._last_selected_pos = -1
        
        # --- Flag to prevent saving state while restoring it ---
        self._is_restoring_state = False

        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        
        self.refresh_tree()

        self.logger.info("Modern SessionTreeView (ColumnView) initialized")

    def _create_column_view(self) -> Gtk.ColumnView:
        selection_model = Gtk.MultiSelection(model=self.tree_model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        column = Gtk.ColumnViewColumn(title=_("Sessions"), factory=factory)
        column.set_expand(True)
        column_view = Gtk.ColumnView(model=selection_model)
        column_view.append_column(column)
        column_view.connect("activate", self._on_row_activated)
        
        right_click_empty = Gtk.GestureClick()
        right_click_empty.set_button(Gdk.BUTTON_SECONDARY)
        right_click_empty.connect("pressed", self._on_empty_area_right_click)
        column_view.add_controller(right_click_empty)

        drop_target_root = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target_root.connect("accept", self._on_root_drop_accept)
        drop_target_root.connect("drop", self._on_root_drop)
        column_view.add_controller(drop_target_root)
        
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        column_view.add_controller(key_controller)

        return column_view

    def _on_factory_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, hexpand=True)
        box.append(icon)
        box.append(label)
        list_item.set_child(box)

        left_click = Gtk.GestureClick()
        left_click.set_button(Gdk.BUTTON_PRIMARY)
        left_click.connect("pressed", self._on_item_left_click, list_item)
        box.add_controller(left_click)

        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("enter", self._on_hover_enter, list_item)
        motion_controller.connect("leave", self._on_hover_leave, list_item)
        box.add_controller(motion_controller)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("accept", self._on_folder_drop_accept, list_item)
        drop_target.connect("drop", self._on_folder_drop, list_item)
        drop_target.connect("enter", self._on_folder_drag_enter, list_item)
        drop_target.connect("leave", self._on_folder_drag_leave, list_item)
        box.add_controller(drop_target)

    def _on_factory_bind(self, factory, list_item):
        box = list_item.get_child()
        icon = box.get_first_child()
        label = box.get_last_child()
        
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()

        label.set_label(item.name)

        box.remove_css_class("indented-session")
        if isinstance(item, SessionItem) and tree_list_row.get_depth() > 0:
            box.add_css_class("indented-session")

        if isinstance(item, SessionFolder):
            def update_folder_icon(row, _=None):
                if row.get_expanded():
                    icon.set_from_icon_name("folder-open-symbolic")
                else:
                    if item.children and item.children.get_n_items() > 0:
                        icon.set_from_icon_name("folder-new-symbolic")
                    else:
                        icon.set_from_icon_name("folder-symbolic")

            update_folder_icon(tree_list_row)
            
            icon_handler_id = tree_list_row.connect("notify::expanded", update_folder_icon)
            expansion_handler_id = tree_list_row.connect("notify::expanded", self._on_folder_expansion_changed)
            list_item.handler_ids = [icon_handler_id, expansion_handler_id]

        elif isinstance(item, SessionItem):
            icon_name = "computer-symbolic" if item.is_local() else "network-server-symbolic"
            icon.set_from_icon_name(icon_name)

    def _on_factory_unbind(self, factory, list_item):
        if hasattr(list_item, "handler_ids"):
            row = list_item.get_item()
            if row:
                for handler_id in list_item.handler_ids:
                    if GObject.signal_handler_is_connected(row, handler_id):
                        row.disconnect(handler_id)
            del list_item.handler_ids
            
    def _on_folder_expansion_changed(self, tree_list_row, param):
        if self._is_restoring_state:
            return

        try:
            folder = tree_list_row.get_item()
            if not isinstance(folder, SessionFolder):
                return

            expanded_paths = set(self.settings_manager.get("tree_expanded_folders", []))
            if tree_list_row.get_expanded():
                expanded_paths.add(folder.path)
            else:
                expanded_paths.discard(folder.path)
            
            self.settings_manager.set("tree_expanded_folders", list(expanded_paths))
        except Exception as e:
            self.logger.error(f"Failed to save folder expansion state: {e}")

    def _on_drag_prepare(self, source, x, y, list_item):
        item = list_item.get_item().get_item()
        if isinstance(item, SessionItem): data_string = f"session|{item.name}|{item.folder_path}"
        elif isinstance(item, SessionFolder): data_string = f"folder|{item.name}|{item.path}"
        else: return None
        return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_STRING, data_string))

    def _on_drag_begin(self, source, drag, list_item):
        item = list_item.get_item().get_item()
        try: drag.set_cursor(Gdk.Cursor.new_from_name("grabbing"))
        except Exception: pass
        paintable = Gtk.WidgetPaintable(widget=Gtk.Label(label=item.name, css_classes=["drag-icon"]))
        source.set_icon(paintable, 0, 0)

    def _on_root_drop_accept(self, t, d): return True
    def _on_folder_drop_accept(self, t, d, li): return isinstance(li.get_item().get_item(), SessionFolder)
    def _on_folder_drag_enter(self, t, x, y, li):
        if isinstance(li.get_item().get_item(), SessionFolder):
            li.get_child().add_css_class("drop-target")
            return Gdk.DragAction.MOVE
        return Gdk.DragAction.DEFAULT
    def _on_folder_drag_leave(self, t, li): li.get_child().remove_css_class("drop-target")
    def _on_folder_drop(self, t, v, x, y, li):
        target_folder = li.get_item().get_item()
        li.get_child().remove_css_class("drop-target")
        self._perform_move(v, target_folder.path)
        return True
    def _on_root_drop(self, t, v, x, y): self._perform_move(v, ""); return True

    def _perform_move(self, data_string, target_folder_path):
        try:
            item_type, name, source_path = data_string.split('|', 2)
            if item_type == "session":
                result = self.operations.find_session_by_name_and_path(name, source_path)
                if result:
                    session, _ = result
                    if session.folder_path != target_folder_path:
                        self.operations.move_session_to_folder(session, target_folder_path)
                        self.refresh_tree()
            elif item_type == "folder":
                result = self.operations.find_folder_by_path(source_path)
                if result:
                    folder, pos = result
                    if source_path != target_folder_path and not target_folder_path.startswith(source_path + "/"):
                        updated_folder = SessionFolder.from_dict(folder.to_dict())
                        updated_folder.parent_path = target_folder_path
                        updated_folder.path = f"{target_folder_path}/{name}" if target_folder_path else f"/{name}"
                        self.operations.update_folder(pos, updated_folder)
                        self.refresh_tree()
        except Exception as e: self.logger.error(f"DND move error: {e}")

    def _on_hover_enter(self, c, x, y, li):
        if isinstance(li.get_item().get_item(), (SessionItem, SessionFolder)):
            try: li.get_child().set_cursor(Gdk.Cursor.new_from_name("grab"))
            except Exception: pass
    def _on_hover_leave(self, c, li):
        try: li.get_child().set_cursor(None)
        except Exception: pass

    def get_widget(self) -> Gtk.ColumnView:
        return self.column_view

    def refresh_tree(self):
        self.logger.debug("Refreshing session tree view")
        # --- FIX: Set restoring flag before any changes to the model ---
        self._is_restoring_state = True
        
        self.root_store.remove_all()

        folder_map = {}
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            folder.clear_children()
            folder_map[folder.path] = folder

        root_items = []
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.folder_path in folder_map:
                folder_map[session.folder_path].children.append(session)
            else:
                root_items.append(session)
        
        for path, folder in folder_map.items():
            if folder.parent_path in folder_map:
                folder_map[folder.parent_path].children.append(folder)
            else:
                root_items.append(folder)

        sorted_root = sorted(root_items, key=lambda item: (isinstance(item, SessionItem), item.name))
        for item in sorted_root:
            self.root_store.append(item)
        
        for folder in folder_map.values():
            children = [folder.children.get_item(i) for i in range(folder.children.get_n_items())]
            sorted_children = sorted(children, key=lambda item: (isinstance(item, SessionItem), item.name))
            folder.clear_children()
            for child in sorted_children:
                folder.add_child(child)

        self.logger.info(f"Tree refreshed. Scheduling expansion state application.")
        GLib.idle_add(self._apply_expansion_state)

    def _find_row_for_path(self, path: str) -> Optional[Gtk.TreeListRow]:
        for i in range(self.tree_model.get_n_items()):
            row = self.tree_model.get_item(i)
            if row:
                item = row.get_item()
                if isinstance(item, SessionFolder) and item.path == path:
                    return row
        return None

    def _apply_expansion_state(self):
        try:
            expanded_paths = self.settings_manager.get("tree_expanded_folders", [])
            if not expanded_paths:
                return False

            self.logger.debug(f"Applying expansion state for paths: {expanded_paths}")
            sorted_paths = sorted(expanded_paths, key=lambda p: p.count('/'))
            
            for path in sorted_paths:
                row = self._find_row_for_path(path)
                if row and not row.get_expanded():
                    row.set_expanded(True)
        except Exception as e:
            self.logger.error(f"Failed to apply tree expansion state: {e}")
        finally:
            # --- FIX: Reset flag only after restoration is complete ---
            self._is_restoring_state = False
            self.logger.debug("Finished restoring expansion state.")
        return False

    def _on_row_activated(self, list_view, position):
        tree_list_row = self.tree_model.get_item(position)
        if not tree_list_row: return
        
        item = tree_list_row.get_item()
        if isinstance(item, SessionItem):
            if self.on_session_activated:
                self.on_session_activated(item)
        elif isinstance(item, SessionFolder):
            is_expanded = tree_list_row.get_expanded()
            tree_list_row.set_expanded(not is_expanded)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval in (Gdk.KEY_a, Gdk.KEY_A): self.selection_model.select_all(); return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_c, Gdk.KEY_C): self._copy_selected_item_safe(); return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_x, Gdk.KEY_X): self._cut_selected_item_safe(); return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                selected = self.get_selected_item()
                target_path = ""
                if isinstance(selected, SessionFolder): target_path = selected.path
                elif isinstance(selected, SessionItem): target_path = selected.folder_path
                self._paste_item_safe(target_path)
                return Gdk.EVENT_STOP
        if keyval == Gdk.KEY_Delete:
            if hasattr(self.parent_window, '_on_delete_selected_items'):
                self.parent_window._on_delete_selected_items()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_item_left_click(self, gesture, n_press, x, y, list_item):
        state = gesture.get_current_event_state()
        if not (state & Gdk.ModifierType.SHIFT_MASK):
            self._selection_anchor = list_item.get_position()

    def _on_selection_changed(self, selection_model, position, n_items): pass

    def get_selected_item(self) -> Optional[Union[SessionItem, SessionFolder]]:
        selection = self.selection_model.get_selection()
        if selection.get_size() > 0:
            pos = selection.get_nth(0)
            row = self.tree_model.get_item(pos)
            return row.get_item() if row else None
        return None

    def get_selected_items(self) -> List[Union[SessionItem, SessionFolder]]:
        items = []
        selection = self.selection_model.get_selection()
        for i in range(self.tree_model.get_n_items()):
            if selection.contains(i):
                row = self.tree_model.get_item(i)
                if row: items.append(row.get_item())
        return items

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        pos = list_item.get_position()
        if not self.selection_model.is_selected(pos):
            self.selection_model.unselect_all()
            self.selection_model.select_item(pos, True)
        
        item = list_item.get_item().get_item()
        menu_model = None
        if isinstance(item, SessionItem):
            found, position = self.session_store.find(item)
            if found: menu_model = create_session_menu(item, self.session_store, position, self.folder_store, self.has_clipboard_content())
        elif isinstance(item, SessionFolder):
            found, position = self.folder_store.find(item)
            if found: menu_model = create_folder_menu(item, self.folder_store, position, self.session_store, self.has_clipboard_content())
        
        if menu_model:
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(list_item.get_child())
            popover.popup()

    def _on_empty_area_right_click(self, gesture, n_press, x, y):
        self.selection_model.unselect_all()
        menu_model = create_root_menu(self.has_clipboard_content())
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.column_view)
        rect = Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1)
        popover.set_pointing_to(rect)
        popover.popup()

    def has_clipboard_content(self) -> bool:
        if not self._clipboard_item: return False
        if time.time() - self._clipboard_timestamp > 600:
            self._clipboard_item = None
            return False
        return True

    def _copy_selected_item_safe(self):
        item = self.get_selected_item()
        if item: self._clipboard_item, self._clipboard_is_cut, self._clipboard_timestamp = item, False, time.time()

    def _cut_selected_item_safe(self):
        item = self.get_selected_item()
        if item: self._clipboard_item, self._clipboard_is_cut, self._clipboard_timestamp = item, True, time.time()

    def _paste_item_safe(self, target_folder_path: str):
        if not self.has_clipboard_content(): return
        item_to_paste, is_cut = self._clipboard_item, self._clipboard_is_cut
        self._clipboard_item, self._clipboard_is_cut = None, False
        try:
            result = None
            if is_cut:
                if isinstance(item_to_paste, SessionItem):
                    result = self.operations.move_session_to_folder(item_to_paste, target_folder_path)
                elif isinstance(item_to_paste, SessionFolder):
                    updated_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    updated_folder.parent_path, updated_folder.path = target_folder_path, f"{target_folder_path}/{updated_folder.name}" if target_folder_path else f"/{updated_folder.name}"
                    found, pos = self.folder_store.find(item_to_paste)
                    if found: result = self.operations.update_folder(pos, updated_folder)
            else:
                if isinstance(item_to_paste, SessionItem):
                    new_item = SessionItem.from_dict(item_to_paste.to_dict())
                    new_item.folder_path = target_folder_path
                    new_item.name = generate_unique_name(new_item.name, self.operations._get_session_names_in_folder(target_folder_path))
                    result = self.operations.add_session(new_item)
                elif isinstance(item_to_paste, SessionFolder):
                    new_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    new_folder.parent_path, new_folder.path = target_folder_path, f"{target_folder_path}/{new_folder.name}" if target_folder_path else f"/{new_folder.name}"
                    result = self.operations.add_folder(new_folder)
            if result and result.success: self.refresh_tree()
            elif result and hasattr(self.parent_window, '_show_error_dialog'):
                self.parent_window._show_error_dialog(_("Paste Error"), result.message)
        except Exception as e:
            if hasattr(self.parent_window, '_show_error_dialog'):
                self.parent_window._show_error_dialog(_("Paste Error"), str(e))