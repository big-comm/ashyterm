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

        # Callbacks
        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        
        # Initial population
        self.refresh_tree()

        self.logger.info("Modern SessionTreeView (ListView) initialized")

    def _create_list_view(self) -> Gtk.ListView:
        """Create and configure the Gtk.ListView widget."""
        self.logger.debug("Creating Gtk.ListView widget")

        selection_model = Gtk.SingleSelection(model=self.flat_store)

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

        # Attach context menu gesture to the box, which now fills the whole row
        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        # --- DRAG SOURCE SETUP (for draggable items) ---
        drag_source = Gtk.DragSource()
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

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
        if not isinstance(item, SessionItem):
            return None

        self.logger.debug(f"Preparing drag for session: {item.name}")
        data_string = f"{item.name}|{item.folder_path}"
        value = GObject.Value(GObject.TYPE_STRING, data_string)
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, list_item: Gtk.ListItem):
        """Set the icon for the drag operation."""
        item = list_item.get_item()
        if not isinstance(item, SessionItem):
            return

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

    def _perform_move(self, session_data_string: str, target_folder_path: str):
        """Core logic to move a session after a drop."""
        try:
            name, old_folder_path = session_data_string.split('|', 1)
            self.logger.info(f"Drop event: Moving '{name}' from '{old_folder_path}' to '{target_folder_path}'")

            result = self.operations.find_session_by_name_and_path(name, old_folder_path)
            if not result:
                self.logger.error(f"Could not find session '{name}' in '{old_folder_path}' to move.")
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

        except Exception as e:
            self.logger.error(f"Error during drag-and-drop move operation: {e}")
            log_error_with_context(e, "DnD move", "ashyterm.sessions.tree")

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

    def _on_selection_changed(self, selection_model, position, n_items):
        """Handle selection changes."""
        pass

    def get_selected_item(self) -> Optional[Union[SessionItem, SessionFolder]]:
        """Get the currently selected item."""
        return self.selection_model.get_selected_item()

    def _on_item_right_click(self, gesture, n_press, x, y, list_item):
        """Handle right-click on a specific item row."""
        self.selection_model.set_selected(list_item.get_position())
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