# sessions/tree.py

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


class SessionTreeView:
    """Modern tree view manager using Gtk.ListView for sessions and folders."""

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

        # Data model for the ListView
        self.flat_store = Gio.ListStore.new(GObject.GObject)
        
        # UI components
        self.list_view = self._create_list_view()
        self.selection_model = self.list_view.get_model()

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

        self.logger.info("Modern SessionTreeView (ListView) initialized")

    def _create_list_view(self) -> Gtk.ListView:
        """Create and configure the Gtk.ListView widget."""
        self.logger.debug("Creating Gtk.ListView widget")

        # --- MODIFICATION: Use MultiSelection instead of SingleSelection ---
        selection_model = Gtk.MultiSelection(model=self.flat_store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)

        list_view = Gtk.ListView(model=selection_model, factory=factory)

        list_view.connect("activate", self._on_row_activated)
        selection_model.connect("selection-changed", self._on_selection_changed)

        # Context menu for empty area
        right_click_empty = Gtk.GestureClick()
        right_click_empty.set_button(Gdk.BUTTON_SECONDARY)
        right_click_empty.connect("pressed", self._on_empty_area_right_click)
        list_view.add_controller(right_click_empty)

        # --- DRAG AND DROP FOR ROOT FOLDER ---
        drop_target_root = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target_root.connect("accept", self._on_root_drop_accept)
        drop_target_root.connect("drop", self._on_root_drop)
        list_view.add_controller(drop_target_root)
        
        # --- START: Add Key Controller for Ctrl+A and Shift+Select ---
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        list_view.add_controller(key_controller)
        # --- END: Add Key Controller ---

        return list_view

    def _on_factory_setup(self, factory, list_item):
        """Create the widget for a single row in the ListView."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=10, 
            hexpand=True
        )
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(12)
        box.set_margin_end(12)

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

        # Attach context menu gesture to the box, which now fills the whole row
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
        item = list_item.get_item()

        if isinstance(item, SessionFolder):
            label.set_label(item.name)
            icon.set_from_icon_name("folder-symbolic")
            depth = item.path.count("/")
            box.set_margin_start(12 + (depth * 12))
        elif isinstance(item, SessionItem):
            label.set_label(item.name)
            icon_name = (
                "computer-symbolic" if item.is_local() else "network-server-symbolic"
            )
            icon.set_from_icon_name(icon_name)
            depth = item.folder_path.count("/") + 1 if item.folder_path else 1
            box.set_margin_start(12 + (depth * 12))

    # --- DRAG AND DROP CALLBACKS ---

    def _on_drag_prepare(self, source: Gtk.DragSource, x: float, y: float, list_item: Gtk.ListItem) -> Optional[Gdk.ContentProvider]:
        """Prepare the data for a drag operation."""
        item = list_item.get_item()
        
        # Allow dragging both sessions and folders
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
        item = list_item.get_item()
        if not isinstance(item, (SessionItem, SessionFolder)):
            return

        # Try setting cursor with timeout to override GTK default
        try:
            cursor = Gdk.Cursor.new_from_name("grabbing")
            drag.set_cursor(cursor)
            
            # Also try setting it with a small delay
            GLib.timeout_add(10, lambda: drag.set_cursor(cursor) or False)
            
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
        item = list_item.get_item()
        return isinstance(item, SessionFolder)

    def _on_folder_drag_enter(self, target: Gtk.DropTarget, x: float, y: float, list_item: Gtk.ListItem) -> Gdk.DragAction:
        """Provide visual feedback when dragging over a valid folder target."""
        item = list_item.get_item()
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
        target_folder = list_item.get_item()
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
                
                # Find the folder to move
                folder_to_move = None
                for i in range(self.folder_store.get_n_items()):
                    folder = self.folder_store.get_item(i)
                    if isinstance(folder, SessionFolder) and folder.path == source_path:
                        folder_to_move = folder
                        break
                
                if not folder_to_move:
                    self.logger.error(f"Could not find folder '{name}' at path '{source_path}' to move.")
                    return
                
                # Check if trying to move to itself or a child
                if source_path == target_folder_path or target_folder_path.startswith(source_path + "/"):
                    self.logger.debug("Cannot move folder to itself or a child folder.")
                    return
                
                # Update folder path
                old_path = folder_to_move.path
                folder_to_move.parent_path = target_folder_path
                new_name = f"{target_folder_path}/{name}" if target_folder_path else f"/{name}"
                folder_to_move.path = new_name
                
                # Update child paths if needed
                if hasattr(self.operations, '_update_child_paths'):
                    self.operations._update_child_paths(old_path, folder_to_move.path)
                
                # Save changes
                self.operations._save_changes()
                self.refresh_tree()
                self.logger.info("Folder moved successfully via drag-and-drop.")

        except Exception as e:
            self.logger.error(f"Error during drag-and-drop move operation: {e}")
            log_error_with_context(e, "DnD move", "ashyterm.sessions.tree")
            
    def _on_hover_enter(self, controller, x, y, list_item):
        """Set grab cursor when hovering over draggable items."""
        item = list_item.get_item()
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

    def get_widget(self) -> Gtk.ListView:
        """Get the list view widget."""
        return self.list_view

    def refresh_tree(self):
        """Rebuild the flat list from the hierarchical session and folder stores."""
        self.logger.debug("Refreshing session list view")
        self.flat_store.remove_all()
        self._append_children_to_store("")
        self.logger.info(f"Tree refreshed with {self.flat_store.get_n_items()} items.")

    def _append_children_to_store(self, parent_path: str):
        """Recursively traverse the structure and append items to the flat store."""
        folders = sorted(
            self.operations.get_subfolders(parent_path), key=lambda f: f.name
        )
        sessions = sorted(
            self.operations.get_sessions_in_folder(parent_path), key=lambda s: s.name
        )

        for folder in folders:
            self.flat_store.append(folder)
            self._append_children_to_store(folder.path)

        for session in sessions:
            self.flat_store.append(session)

    def _on_row_activated(self, list_view, position):
        """Handle item activation (double-click or Enter)."""
        item = self.flat_store.get_item(position)
        if isinstance(item, SessionItem):
            if self.on_session_activated:
                self.on_session_activated(item)
        elif isinstance(item, SessionFolder):
            self.logger.debug(f"Folder '{item.name}' activated.")

    # --- START: MODIFIED/NEW METHODS FOR MULTI-SELECTION ---
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key presses for selection."""
        # Check for Control modifier for clipboard and select all
        if state & Gdk.ModifierType.CONTROL_MASK:
            # Ctrl+A for Select All
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.selection_model.select_all()
                return Gdk.EVENT_STOP

            # Ctrl+C for Copy
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self._copy_selected_item_safe()
                self.logger.debug("Ctrl+C pressed, copying selected item.")
                return Gdk.EVENT_STOP

            # Ctrl+X for Cut
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                self._cut_selected_item_safe()
                self.logger.debug("Ctrl+X pressed, cutting selected item.")
                return Gdk.EVENT_STOP

            # Ctrl+V for Paste
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                selected_item = self.get_selected_item()
                target_folder_path = ""  # Default to root
                if selected_item:
                    if isinstance(selected_item, SessionFolder):
                        target_folder_path = selected_item.path
                    elif isinstance(selected_item, SessionItem):
                        target_folder_path = selected_item.folder_path
                
                self._paste_item_safe(target_folder_path)
                self.logger.debug(f"Ctrl+V pressed, pasting to '{target_folder_path}'.")
                return Gdk.EVENT_STOP

        # Shift + Up/Down for range selection
        if (state & Gdk.ModifierType.SHIFT_MASK):
            if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
                if self._selection_anchor is None:
                    # If there's no anchor, set it to the last selected item
                    selection = self.selection_model.get_selection()
                    if selection.get_size() > 0:
                        self._selection_anchor = selection.get_nth(0)
                    else:
                        return Gdk.EVENT_PROPAGATE # Nothing to do

                # Determine new position
                if keyval == Gdk.KEY_Up:
                    new_pos = max(0, self._last_selected_pos - 1)
                else: # Down
                    new_pos = min(self.flat_store.get_n_items() - 1, self._last_selected_pos + 1)

                self.selection_model.unselect_all()
                start = min(self._selection_anchor, new_pos)
                end = max(self._selection_anchor, new_pos)
                # The fourth argument 'True' means "select these items".
                self.selection_model.select_range(start, end - start + 1, True)
                
                # Update the "cursor" for the next shift+arrow press
                self._last_selected_pos = new_pos
                
                return Gdk.EVENT_STOP
        
        # --- NEW: Handle Delete key ---
        if keyval == Gdk.KEY_Delete:
            # Trigger the deletion action in the main window
            if hasattr(self.parent_window, '_on_delete_selected_items'):
                self.parent_window._on_delete_selected_items()
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_item_left_click(self, gesture, n_press, x, y, list_item):
        """Handle left click to set the selection anchor correctly."""
        state = gesture.get_current_event_state()
        is_shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # If shift is not pressed, this is a new selection, so we set the anchor.
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
            return self.flat_store.get_item(first_pos)
        return None

    # --- NEW: Method to get ALL selected items ---
    def get_selected_items(self) -> List[Union[SessionItem, SessionFolder]]:
        """Get a list of all currently selected items."""
        items = []
        selection = self.selection_model.get_selection()
        
        # --- FINAL FIX: Use the correct Gtk.Bitset.contains() method ---
        n_items = self.flat_store.get_n_items()
        for i in range(n_items):
            if selection.contains(i):
                item = self.flat_store.get_item(i)
                if item:
                    items.append(item)
        return items
    # --- END: MODIFIED/NEW METHODS FOR MULTI-SELECTION ---

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        """Handle right-click on a specific item row."""
        pos = list_item.get_position()
        
        if not self.selection_model.is_selected(pos):
            self.selection_model.unselect_all()
            self.selection_model.select_item(pos, True)

        item = list_item.get_item()
        menu_model = None

        if isinstance(item, SessionItem):
            if item.validate():
                found, position = self.session_store.find(item)
                if found:
                    menu_model = create_session_menu(
                        item,
                        self.session_store,
                        position,
                        self.folder_store,
                        self.has_clipboard_content(),
                    )
        elif isinstance(item, SessionFolder):
            if item.validate():
                found, position = self.folder_store.find(item)
                if found:
                    menu_model = create_folder_menu(
                        item,
                        self.folder_store,
                        position,
                        self.session_store,
                        self.has_clipboard_content(),
                    )

        if menu_model:
            popover = Gtk.PopoverMenu.new_from_model(menu_model)
            popover.set_parent(list_item.get_child())
            popover.set_has_arrow(False)
            popover.popup()

    def _on_empty_area_right_click(self, gesture, n_press, x, y):
        """Handle right-click on an empty area of the ListView."""
        self.selection_model.unselect_all()
        menu_model = create_root_menu(self.has_clipboard_content())

        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.list_view)
        popover.set_has_arrow(False)

        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)

        popover.popup()

    def has_clipboard_content(self) -> bool:
        """Check if clipboard has valid content."""
        if not self._clipboard_item:
            return False
        if time.time() - self._clipboard_timestamp > 600:  # 10 minutes
            self._clipboard_item = None
            return False
        return True

    # --- START: Clipboard Methods ---
    def _copy_selected_item_safe(self):
        """Safely copy the selected item to the internal clipboard."""
        item = self.get_selected_item()
        if item:
            self._clipboard_item = item
            self._clipboard_is_cut = False
            self._clipboard_timestamp = time.time()
            self.logger.info(f"Copied to clipboard: '{item.name}'")

    def _cut_selected_item_safe(self):
        """Safely cut the selected item to the internal clipboard."""
        item = self.get_selected_item()
        if item:
            self._clipboard_item = item
            self._clipboard_is_cut = True
            self._clipboard_timestamp = time.time()
            self.logger.info(f"Cut to clipboard: '{item.name}'")

    def _paste_item_safe(self, target_folder_path: str):
        """Safely paste the clipboard item to the target folder."""
        if not self.has_clipboard_content():
            self.logger.warning("Paste called with empty or expired clipboard.")
            return

        item_to_paste = self._clipboard_item
        is_cut = self._clipboard_is_cut
        result = None

        # Clear clipboard immediately to prevent re-pasting the same item
        self._clipboard_item = None
        self._clipboard_is_cut = False

        try:
            if is_cut:
                # This is a MOVE operation
                self.logger.info(f"Pasting (move) '{item_to_paste.name}' to '{target_folder_path}'")
                if isinstance(item_to_paste, SessionItem):
                    result = self.operations.move_session_to_folder(item_to_paste, target_folder_path)
                elif isinstance(item_to_paste, SessionFolder):
                    # To move a folder, we create an "updated" version with the new parent path
                    # and then call the update_folder operation.
                    updated_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    updated_folder.parent_path = target_folder_path
                    updated_folder.path = f"{target_folder_path}/{updated_folder.name}" if target_folder_path else f"/{updated_folder.name}"
                    
                    found, position = self.folder_store.find(item_to_paste)
                    if found:
                        result = self.operations.update_folder(position, updated_folder)
                    else:
                        result = OperationResult(False, "Original folder not found.")
            else:
                # This is a COPY (duplicate) operation
                self.logger.info(f"Pasting (copy) '{item_to_paste.name}' to '{target_folder_path}'")
                if isinstance(item_to_paste, SessionItem):
                    # Create a new item from the copied data
                    new_item_data = item_to_paste.to_dict()
                    new_item = SessionItem.from_dict(new_item_data)
                    new_item.folder_path = target_folder_path
                    
                    # Ensure the name is unique in the new location
                    existing_names = self.operations._get_session_names_in_folder(target_folder_path)
                    new_item.name = generate_unique_name(new_item.name, existing_names)
                    
                    result = self.operations.add_session(new_item)
                elif isinstance(item_to_paste, SessionFolder):
                    # This is a shallow copy. A deep copy (including all child sessions/folders)
                    # would require a more complex recursive operation.
                    new_folder_data = item_to_paste.to_dict()
                    new_folder = SessionFolder.from_dict(new_folder_data)
                    new_folder.parent_path = target_folder_path
                    new_folder.path = f"{target_folder_path}/{new_folder.name}" if target_folder_path else f"/{new_folder.name}"
                    
                    result = self.operations.add_folder(new_folder)

            # Handle the result of the operation
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