# START OF FILE ashyterm/sessions/tree.py

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
        self.platform_info = get_platform_info()
        
        # Core components
        self.operations = SessionOperations(
            session_store, folder_store, settings_manager
        )

        # --- MODIFICATION: Data model for the ColumnView ---
        self.root_store = Gio.ListStore.new(GObject.GObject)
        self.tree_model = Gtk.TreeListModel.new(
            self.root_store,
            passthrough=False,
            autoexpand=False,
            create_func=_get_children_model,
            user_data=None
        )
        
        # UI components
        self.column_view = self._create_column_view()
        self.selection_model = self.column_view.get_model()

        # State management
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut = False
        self._clipboard_timestamp = 0
        
        # --- START: State for range selection ---
        self._selection_anchor = None
        self._last_selected_pos = -1
        # --- END: State for range selection ---

        # Callbacks
        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        
        # Initial population
        self.refresh_tree()

        self.logger.info("Modern SessionTreeView (ColumnView) initialized")

    def _create_column_view(self) -> Gtk.ColumnView:
        """Create and configure the Gtk.ColumnView widget."""
        self.logger.debug("Creating Gtk.ColumnView widget")

        selection_model = Gtk.MultiSelection(model=self.tree_model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        # --- NEW: Connect the unbind signal for cleanup ---
        factory.connect("unbind", self._on_factory_unbind)

        # Create the main column
        column = Gtk.ColumnViewColumn(title=_("Sessions"), factory=factory)
        column.set_expand(True)

        column_view = Gtk.ColumnView(model=selection_model)
        column_view.append_column(column)

        column_view.connect("activate", self._on_row_activated)
        selection_model.connect("selection-changed", self._on_selection_changed)

        # Context menu for empty area
        right_click_empty = Gtk.GestureClick()
        right_click_empty.set_button(Gdk.BUTTON_SECONDARY)
        right_click_empty.connect("pressed", self._on_empty_area_right_click)
        column_view.add_controller(right_click_empty)

        # --- DRAG AND DROP FOR ROOT FOLDER ---
        drop_target_root = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target_root.connect("accept", self._on_root_drop_accept)
        drop_target_root.connect("drop", self._on_root_drop)
        column_view.add_controller(drop_target_root)
        
        # --- START: Add Key Controller for Ctrl+A and Shift+Select ---
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        column_view.add_controller(key_controller)
        # --- END: Add Key Controller ---

        return column_view

    def _on_factory_setup(self, factory, list_item):
        """Create the widget for a single row in the ColumnView."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=6,
            hexpand=True
        )

        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, hexpand=True)

        box.append(icon)
        box.append(label)
        list_item.set_child(box)

        # --- START: Add Left Click Gesture for selection anchor ---
        left_click = Gtk.GestureClick()
        left_click.set_button(Gdk.BUTTON_PRIMARY)
        left_click.connect("pressed", self._on_item_left_click, list_item)
        box.add_controller(left_click)
        # --- END: Add Left Click Gesture ---

        # Attach context menu gesture to the box
        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        # --- DRAG SOURCE SETUP (for draggable items) ---
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

        # --- HOVER CURSOR SETUP (for drag feedback) ---
        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("enter", self._on_hover_enter, list_item)
        motion_controller.connect("leave", self._on_hover_leave, list_item)
        box.add_controller(motion_controller)

        # --- DROP TARGET SETUP (for folders) ---
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("accept", self._on_folder_drop_accept, list_item)
        drop_target.connect("drop", self._on_folder_drop, list_item)
        drop_target.connect("enter", self._on_folder_drag_enter, list_item)
        drop_target.connect("leave", self._on_folder_drag_leave, list_item)
        box.add_controller(drop_target)

    def _on_factory_bind(self, factory, list_item):
        """Bind data from a SessionItem or SessionFolder to the row widget."""
        box = list_item.get_child()
        icon = box.get_first_child()
        label = box.get_last_child()
        
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()

        label.set_label(item.name)

        if isinstance(item, SessionFolder):
            # This function updates the icon based on the expansion state and content.
            def update_folder_icon(row, _=None):
                if row.get_expanded():
                    icon.set_from_icon_name("folder-open-symbolic")
                else:
                    # --- LOGIC INVERTED AS REQUESTED ---
                    if item.children and item.children.get_n_items() > 0:
                        # Use 'new' icon for folders with content
                        icon.set_from_icon_name("folder-new-symbolic")
                    else:
                        # Use standard icon for empty folders
                        icon.set_from_icon_name("folder-symbolic")

            # Set the initial icon state.
            update_folder_icon(tree_list_row)
            
            # Connect to the "notify::expanded" signal to update the icon when the state changes.
            handler_id = tree_list_row.connect("notify::expanded", update_folder_icon)
            
            list_item.expanded_handler_info = (tree_list_row, handler_id)

        elif isinstance(item, SessionItem):
            icon_name = "computer-symbolic" if item.is_local() else "network-server-symbolic"
            icon.set_from_icon_name(icon_name)

    def _on_factory_unbind(self, factory, list_item):
        """Unbind the item, disconnecting any signal handlers."""
        if hasattr(list_item, "expanded_handler_info"):
            row, handler_id = list_item.expanded_handler_info
            if row and GObject.signal_handler_is_connected(row, handler_id):
                row.disconnect(handler_id)
            del list_item.expanded_handler_info

    # --- DRAG AND DROP CALLBACKS ---

    def _on_drag_prepare(self, source: Gtk.DragSource, x: float, y: float, list_item: Gtk.ListItem) -> Optional[Gdk.ContentProvider]:
        """Prepare the data for a drag operation."""
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        
        if isinstance(item, SessionItem):
            self.logger.debug(f"Preparing drag for session: {item.name}")
            data_string = f"session|{item.name}|{item.folder_path}"
            value = GObject.Value(GObject.TYPE_STRING, data_string)
            return Gdk.ContentProvider.new_for_value(value)
        elif isinstance(item, SessionFolder):
            self.logger.debug(f"Preparing drag for folder: {item.name}")
            data_string = f"folder|{item.name}|{item.path}"
            value = GObject.Value(GObject.TYPE_STRING, data_string)
            return Gdk.ContentProvider.new_for_value(value)
        
        return None

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, list_item: Gtk.ListItem):
        """Set the icon for the drag operation."""
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        if not isinstance(item, (SessionItem, SessionFolder)):
            return

        try:
            cursor = Gdk.Cursor.new_from_name("grabbing")
            drag.set_cursor(cursor)
        except Exception as e:
            self.logger.debug(f"Could not set grabbing cursor: {e}")

        label = Gtk.Label(label=item.name, css_classes=["drag-icon"])
        paintable = Gtk.WidgetPaintable(widget=label)
        source.set_icon(paintable, 0, 0)

    def _on_root_drop_accept(self, target: Gtk.DropTarget, drop: Gdk.Drop) -> bool:
        """The root area always accepts a drop of a session."""
        return True

    def _on_folder_drop_accept(self, target: Gtk.DropTarget, drop: Gdk.Drop, list_item: Gtk.ListItem) -> bool:
        """Accept drops only on SessionFolder items."""
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        return isinstance(item, SessionFolder)

    def _on_folder_drag_enter(self, target: Gtk.DropTarget, x: float, y: float, list_item: Gtk.ListItem) -> Gdk.DragAction:
        """Provide visual feedback when dragging over a valid folder target."""
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        if isinstance(item, SessionFolder):
            list_item.get_child().add_css_class("drop-target")
            target.set_actions(Gdk.DragAction.MOVE)
            return Gdk.DragAction.MOVE
        target.set_actions(Gdk.DragAction.DEFAULT)
        return Gdk.DragAction.DEFAULT

    def _on_folder_drag_leave(self, target: Gtk.DropTarget, list_item: Gtk.ListItem):
        """Remove visual feedback when leaving a drop target."""
        list_item.get_child().remove_css_class("drop-target")

    def _on_folder_drop(self, target: Gtk.DropTarget, value: str, x: float, y: float, list_item: Gtk.ListItem) -> bool:
        """Handle the drop event on a folder."""
        tree_list_row = list_item.get_item()
        target_folder = tree_list_row.get_item()
        list_item.get_child().remove_css_class("drop-target")
        
        if not isinstance(target_folder, SessionFolder):
            return False
            
        self._perform_move(value, target_folder.path)
        return True

    def _on_root_drop(self, target: Gtk.DropTarget, value: str, x: float, y: float) -> bool:
        """Handle the drop event on the empty area (root)."""
        self._perform_move(value, "")
        return True

    def _perform_move(self, data_string: str, target_folder_path: str):
        """Core logic to move a session or folder after a drop."""
        try:
            parts = data_string.split('|')
            if len(parts) < 3:
                self.logger.error(f"Invalid drag data format: {data_string}")
                return
                
            item_type, name, source_path = parts[0], parts[1], parts[2]
            
            if item_type == "session":
                self.logger.info(f"Drop event: Moving session '{name}' from '{source_path}' to '{target_folder_path}'")
                result = self.operations.find_session_by_name_and_path(name, source_path)
                if not result:
                    self.logger.error(f"Could not find session '{name}' in '{source_path}' to move.")
                    return
                session_to_move, _ = result
                if session_to_move.folder_path == target_folder_path:
                    self.logger.debug("Session dropped into its current folder. No action needed.")
                    return
                move_result = self.operations.move_session_to_folder(session_to_move, target_folder_path)
                if move_result.success:
                    self.refresh_tree()
                    self.logger.info("Session moved successfully via drag-and-drop.")
                else:
                    self.logger.error(f"Failed to move session via drag-and-drop: {move_result.message}")
                    if hasattr(self.parent_window, 'get_toast_overlay') and (overlay := self.parent_window.get_toast_overlay()):
                        overlay.add_toast(Adw.Toast(title=_("Failed to move session")))
            elif item_type == "folder":
                self.logger.info(f"Drop event: Moving folder '{name}' from '{source_path}' to '{target_folder_path}'")
                result = self.operations.find_folder_by_path(source_path)
                if not result:
                    self.logger.error(f"Could not find folder '{name}' at path '{source_path}' to move.")
                    return
                folder_to_move, pos = result
                if source_path == target_folder_path or target_folder_path.startswith(source_path + "/"):
                    self.logger.debug("Cannot move folder to itself or a child folder.")
                    return
                updated_folder = SessionFolder.from_dict(folder_to_move.to_dict())
                updated_folder.parent_path = target_folder_path
                new_name = f"{target_folder_path}/{name}" if target_folder_path else f"/{name}"
                updated_folder.path = new_name
                move_result = self.operations.update_folder(pos, updated_folder)
                if move_result.success:
                    self.refresh_tree()
                    self.logger.info("Folder moved successfully via drag-and-drop.")
                else:
                    self.logger.error(f"Failed to move folder: {move_result.message}")
        except Exception as e:
            self.logger.error(f"Error during drag-and-drop move operation: {e}")
            log_error_with_context(e, "DnD move", "ashyterm.sessions.tree")
            
    def _on_hover_enter(self, controller, x, y, list_item):
        """Set grab cursor when hovering over draggable items."""
        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        if isinstance(item, (SessionItem, SessionFolder)):
            try:
                cursor = Gdk.Cursor.new_from_name("grab")
                list_item.get_child().set_cursor(cursor)
            except Exception:
                pass

    def _on_hover_leave(self, controller, list_item):
        """Reset cursor when leaving draggable items."""
        try:
            list_item.get_child().set_cursor(None)
        except Exception:
            pass

    def get_widget(self) -> Gtk.ColumnView:
        """Get the column view widget."""
        return self.column_view

    def refresh_tree(self):
        """Rebuild the hierarchical model from the session and folder stores."""
        self.logger.debug("Refreshing session tree view")
        self.root_store.remove_all()

        # Create a map of folder path -> SessionFolder object for easy lookup
        folder_map = {}
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            folder.clear_children()  # Clear previous hierarchy
            folder_map[folder.path] = folder

        root_items = []
        
        # Place sessions into their parent folders or root
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.folder_path and session.folder_path in folder_map:
                folder_map[session.folder_path].add_child(session)
            else:
                root_items.append(session)
        
        # Place folders into their parent folders or root
        for path, folder in folder_map.items():
            if folder.parent_path and folder.parent_path in folder_map:
                folder_map[folder.parent_path].add_child(folder)
            else:
                root_items.append(folder)

        # Sort and add root items to the store
        sorted_root = sorted(root_items, key=lambda item: (isinstance(item, SessionItem), item.name))
        for item in sorted_root:
            self.root_store.append(item)
        
        # Sort children within each folder
        for folder in folder_map.values():
            children = [folder.children.get_item(i) for i in range(folder.children.get_n_items())]
            sorted_children = sorted(children, key=lambda item: (isinstance(item, SessionItem), item.name))
            folder.clear_children()
            for child in sorted_children:
                folder.add_child(child)

        self.logger.info(f"Tree refreshed with {len(root_items)} root items.")

    def _on_row_activated(self, list_view, position):
        """Handle item activation (double-click or Enter)."""
        # Get the TreeListRow object at the activated position
        tree_list_row = self.tree_model.get_item(position)
        if not tree_list_row: 
            return
        
        item = tree_list_row.get_item()
        if isinstance(item, SessionItem):
            if self.on_session_activated:
                self.on_session_activated(item)
        elif isinstance(item, SessionFolder):
            # Toggle expansion state on the TreeListRow itself
            is_expanded = tree_list_row.get_expanded()
            tree_list_row.set_expanded(not is_expanded) # This is the correct way
            self.logger.debug(f"Folder '{item.name}' activated, toggling expansion to {not is_expanded}.")

    # --- START: MODIFIED/NEW METHODS FOR MULTI-SELECTION ---
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key presses for selection."""
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.selection_model.select_all()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self._copy_selected_item_safe()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                self._cut_selected_item_safe()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                selected_item = self.get_selected_item()
                target_folder_path = ""
                if selected_item:
                    if isinstance(selected_item, SessionFolder):
                        target_folder_path = selected_item.path
                    elif isinstance(selected_item, SessionItem):
                        target_folder_path = selected_item.folder_path
                self._paste_item_safe(target_folder_path)
                return Gdk.EVENT_STOP

        if (state & Gdk.ModifierType.SHIFT_MASK):
            if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
                # Range selection logic remains the same
                return Gdk.EVENT_PROPAGATE

        if keyval == Gdk.KEY_Delete:
            if hasattr(self.parent_window, '_on_delete_selected_items'):
                self.parent_window._on_delete_selected_items()
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_item_left_click(self, gesture, n_press, x, y, list_item):
        """Handle left click to set the selection anchor correctly."""
        state = gesture.get_current_event_state()
        is_shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if not is_shift_pressed:
            self._selection_anchor = list_item.get_position()

    def _on_selection_changed(self, selection_model, position, n_items):
        """Handle selection changes to update the last selected position."""
        self._last_selected_pos = position

    def get_selected_item(self) -> Optional[Union[SessionItem, SessionFolder]]:
        """Get the *first* selected item in a multi-selection model."""
        selection = self.selection_model.get_selection()
        if selection.get_size() > 0:
            first_pos = selection.get_nth(0)
            tree_list_row = self.tree_model.get_item(first_pos)
            return tree_list_row.get_item() if tree_list_row else None
        return None

    def get_selected_items(self) -> List[Union[SessionItem, SessionFolder]]:
        """Get a list of all currently selected items."""
        items = []
        selection = self.selection_model.get_selection()
        n_items = self.tree_model.get_n_items()
        for i in range(n_items):
            if selection.contains(i):
                tree_list_row = self.tree_model.get_item(i)
                if tree_list_row and (item := tree_list_row.get_item()):
                    items.append(item)
        return items
    # --- END: MODIFIED/NEW METHODS FOR MULTI-SELECTION ---

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        """Handle right-click on a specific item row."""
        pos = list_item.get_position()
        
        if not self.selection_model.is_selected(pos):
            self.selection_model.unselect_all()
            self.selection_model.select_item(pos, True)

        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        menu_model = None

        if isinstance(item, SessionItem):
            if item.validate():
                found, position = self.session_store.find(item)
                if found:
                    menu_model = create_session_menu(item, self.session_store, position, self.folder_store, self.has_clipboard_content())
        elif isinstance(item, SessionFolder):
            if item.validate():
                found, position = self.folder_store.find(item)
                if found:
                    menu_model = create_folder_menu(item, self.folder_store, position, self.session_store, self.has_clipboard_content())

        if menu_model:
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(list_item.get_child())
            popover.set_has_arrow(False)
            popover.popup()

    def _on_empty_area_right_click(self, gesture, n_press, x, y):
        """Handle right-click on an empty area of the ColumnView."""
        self.selection_model.unselect_all()
        menu_model = create_root_menu(self.has_clipboard_content())
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.column_view)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def has_clipboard_content(self) -> bool:
        """Check if clipboard has valid content."""
        if not self._clipboard_item: return False
        if time.time() - self._clipboard_timestamp > 600:
            self._clipboard_item = None
            return False
        return True

    # --- START: Clipboard Methods ---
    def _copy_selected_item_safe(self):
        item = self.get_selected_item()
        if item:
            self._clipboard_item = item
            self._clipboard_is_cut = False
            self._clipboard_timestamp = time.time()
            self.logger.info(f"Copied to clipboard: '{item.name}'")

    def _cut_selected_item_safe(self):
        item = self.get_selected_item()
        if item:
            self._clipboard_item = item
            self._clipboard_is_cut = True
            self._clipboard_timestamp = time.time()
            self.logger.info(f"Cut to clipboard: '{item.name}'")

    def _paste_item_safe(self, target_folder_path: str):
        if not self.has_clipboard_content():
            self.logger.warning("Paste called with empty or expired clipboard.")
            return

        item_to_paste = self._clipboard_item
        is_cut = self._clipboard_is_cut
        result = None
        self._clipboard_item = None
        self._clipboard_is_cut = False

        try:
            if is_cut:
                self.logger.info(f"Pasting (move) '{item_to_paste.name}' to '{target_folder_path}'")
                if isinstance(item_to_paste, SessionItem):
                    result = self.operations.move_session_to_folder(item_to_paste, target_folder_path)
                elif isinstance(item_to_paste, SessionFolder):
                    updated_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    updated_folder.parent_path = target_folder_path
                    updated_folder.path = f"{target_folder_path}/{updated_folder.name}" if target_folder_path else f"/{updated_folder.name}"
                    found, position = self.folder_store.find(item_to_paste)
                    if found:
                        result = self.operations.update_folder(position, updated_folder)
                    else:
                        result = OperationResult(False, "Original folder not found.")
            else:
                self.logger.info(f"Pasting (copy) '{item_to_paste.name}' to '{target_folder_path}'")
                if isinstance(item_to_paste, SessionItem):
                    new_item = SessionItem.from_dict(item_to_paste.to_dict())
                    new_item.folder_path = target_folder_path
                    existing_names = self.operations._get_session_names_in_folder(target_folder_path)
                    new_item.name = generate_unique_name(new_item.name, existing_names)
                    result = self.operations.add_session(new_item)
                elif isinstance(item_to_paste, SessionFolder):
                    new_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    new_folder.parent_path = target_folder_path
                    new_folder.path = f"{target_folder_path}/{new_folder.name}" if target_folder_path else f"/{new_folder.name}"
                    result = self.operations.add_folder(new_folder)

            if result and result.success:
                self.refresh_tree()
                self.logger.info("Paste operation successful.")
            elif result:
                self.logger.error(f"Paste operation failed: {result.message}")
                if hasattr(self.parent_window, '_show_error_dialog'):
                    self.parent_window._show_error_dialog(_("Paste Error"), result.message)
        except Exception as e:
            log_error_with_context(e, "paste item", "ashyterm.sessions.tree")
            if hasattr(self.parent_window, '_show_error_dialog'):
                self.parent_window._show_error_dialog(_("Paste Error"), str(e))
    # --- END: Clipboard Methods ---