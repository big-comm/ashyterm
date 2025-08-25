import time
from typing import Callable, List, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from ..helpers import generate_unique_name
from ..ui.menus import (create_folder_menu, create_root_menu,
                        create_session_menu)
# Import new utility systems
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .operations import SessionOperations


def _get_children_model(
    item: GObject.GObject, user_data: object
) -> Optional[Gio.ListStore]:
    """Callback for Gtk.TreeListModel to get children of an item."""
    if hasattr(item, "children"):
        return item.children
    return None


class SessionTreeView:
    """
    Manages a modern tree view for sessions and folders using Gtk.ColumnView.

    This class is responsible for building and managing the UI for the session
    list, handling user interactions like selection, activation, context menus,
    drag-and-drop, and keyboard shortcuts. It also persists UI state, such as
    expanded folders.
    """

    def __init__(
        self,
        parent_window: Gtk.Window,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
    ):
        self.logger = get_logger("ashyterm.sessions.tree")
        self.parent_window = parent_window
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager

        self.operations = SessionOperations(
            session_store, folder_store, settings_manager
        )

        # Data model setup
        self.root_store = Gio.ListStore.new(GObject.GObject)
        self.tree_model = Gtk.TreeListModel.new(
            self.root_store,
            passthrough=False,
            autoexpand=False,
            create_func=_get_children_model,
            user_data=None,
        )

        # UI setup
        self.column_view = self._create_column_view()
        self.selection_model = self.column_view.get_model()

        # State management
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut: bool = False
        self._clipboard_timestamp: float = 0.0
        self._is_restoring_state: bool = False

        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None

        self.refresh_tree()
        self.logger.info("Modern SessionTreeView (ColumnView) initialized")

    def get_widget(self) -> Gtk.ColumnView:
        """Returns the main widget for this view."""
        return self.column_view

    def _create_column_view(self) -> Gtk.ColumnView:
        """Creates and configures the Gtk.ColumnView widget."""
        selection_model = Gtk.MultiSelection(model=self.tree_model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        column = Gtk.ColumnViewColumn(title=_("Sessions"), factory=factory, expand=True)
        column_view = Gtk.ColumnView(model=selection_model)
        column_view.append_column(column)
        column_view.connect("activate", self._on_row_activated)

        # --- Event Controllers ---

        # Right-click on empty area
        empty_area_gesture = Gtk.GestureClick.new()
        empty_area_gesture.set_button(Gdk.BUTTON_SECONDARY)
        empty_area_gesture.connect("pressed", self._on_empty_area_right_click)
        column_view.add_controller(empty_area_gesture)

        # Drag-and-drop target for the root level
        root_drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        root_drop_target.connect("accept", lambda t, d: True)
        root_drop_target.connect("drop", self._on_root_drop)
        column_view.add_controller(root_drop_target)

        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        column_view.add_controller(key_controller)

        return column_view

    def _on_factory_setup(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Sets up the widget structure for each row in the ColumnView."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, hexpand=True)
        box.append(icon)
        box.append(label)
        list_item.set_child(box)

        # --- Event Controllers for List Items ---

        # Right-click context menu
        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        # Drag source
        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

        # Drop target (for folders)
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("accept", self._on_folder_drop_accept, list_item)
        drop_target.connect("drop", self._on_folder_drop, list_item)
        drop_target.connect("enter", self._on_folder_drag_enter, list_item)
        drop_target.connect("leave", self._on_folder_drag_leave, list_item)
        box.add_controller(drop_target)

    def _on_factory_bind(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Binds data from a model item to a row widget."""
        box = list_item.get_child()
        icon = box.get_first_child()
        label = box.get_last_child()

        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()

        label.set_label(item.name)

        # Visual styling for sessions inside folders
        box.remove_css_class("indented-session")
        if isinstance(item, SessionItem) and tree_list_row.get_depth() > 0:
            box.add_css_class("indented-session")

        if isinstance(item, SessionFolder):

            def update_folder_icon(row: Gtk.TreeListRow, _=None) -> None:
                if row.get_expanded():
                    icon.set_from_icon_name("folder-open-symbolic")
                else:
                    icon_name = (
                        "folder-new-symbolic"
                        if item.children and item.children.get_n_items() > 0
                        else "folder-symbolic"
                    )
                    icon.set_from_icon_name(icon_name)

            update_folder_icon(tree_list_row)

            # Store handler IDs to disconnect them on unbind
            icon_handler_id = tree_list_row.connect(
                "notify::expanded", update_folder_icon
            )
            expansion_handler_id = tree_list_row.connect(
                "notify::expanded", self._on_folder_expansion_changed
            )
            list_item.handler_ids = [icon_handler_id, expansion_handler_id]

        elif isinstance(item, SessionItem):
            icon_name = (
                "computer-symbolic" if item.is_local() else "network-server-symbolic"
            )
            icon.set_from_icon_name(icon_name)

    def _on_factory_unbind(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Unbinds a row, disconnecting signal handlers."""
        if hasattr(list_item, "handler_ids"):
            row = list_item.get_item()
            if row:
                for handler_id in list_item.handler_ids:
                    if GObject.signal_handler_is_connected(row, handler_id):
                        row.disconnect(handler_id)
            del list_item.handler_ids

    def _on_folder_expansion_changed(
        self, tree_list_row: Gtk.TreeListRow, param
    ) -> None:
        """Saves the expansion state of a folder when it's changed by the user."""
        if self._is_restoring_state:
            return

        try:
            if not (folder := tree_list_row.get_item()) or not isinstance(
                folder, SessionFolder
            ):
                return

            expanded_paths = set(self.settings_manager.get("tree_expanded_folders", []))
            if tree_list_row.get_expanded():
                expanded_paths.add(folder.path)
            else:
                expanded_paths.discard(folder.path)

            self.settings_manager.set("tree_expanded_folders", list(expanded_paths))
        except Exception as e:
            self.logger.error(f"Failed to save folder expansion state: {e}")

    # --- Drag and Drop Handlers ---

    def _on_drag_prepare(
        self, source: Gtk.DragSource, x: float, y: float, list_item: Gtk.ListItem
    ) -> Optional[Gdk.ContentProvider]:
        """Prepares the data for a drag operation."""
        item = list_item.get_item().get_item()
        if isinstance(item, SessionItem):
            data_string = f"session|{item.name}|{item.folder_path}"
        elif isinstance(item, SessionFolder):
            data_string = f"folder|{item.name}|{item.path}"
        else:
            return None
        return Gdk.ContentProvider.new_for_value(data_string)

    def _on_drag_begin(
        self, source: Gtk.DragSource, drag: Gdk.Drag, list_item: Gtk.ListItem
    ) -> None:
        """Sets the drag icon when a drag begins."""
        item = list_item.get_item().get_item()
        paintable = Gtk.WidgetPaintable.new(
            Gtk.Label(label=item.name, css_classes=["drag-icon"])
        )
        source.set_icon(paintable, 0, 0)

    def _on_folder_drop_accept(
        self, target: Gtk.DropTarget, drop: Gdk.Drop, list_item: Gtk.ListItem
    ) -> bool:
        """Accepts a drop only if the target is a folder."""
        return isinstance(list_item.get_item().get_item(), SessionFolder)

    def _on_folder_drag_enter(
        self, target: Gtk.DropTarget, x: float, y: float, list_item: Gtk.ListItem
    ) -> Gdk.DragAction:
        """Adds a CSS class to highlight the drop target folder."""
        if isinstance(list_item.get_item().get_item(), SessionFolder):
            list_item.get_child().add_css_class("drop-target")
            return Gdk.DragAction.MOVE
        return Gdk.DragAction.DEFAULT

    def _on_folder_drag_leave(
        self, target: Gtk.DropTarget, list_item: Gtk.ListItem
    ) -> None:
        """Removes the highlight CSS class when the drag leaves a folder."""
        list_item.get_child().remove_css_class("drop-target")

    def _on_folder_drop(
        self,
        target: Gtk.DropTarget,
        value: str,
        x: float,
        y: float,
        list_item: Gtk.ListItem,
    ) -> bool:
        """Handles a drop onto a folder."""
        target_folder = list_item.get_item().get_item()
        list_item.get_child().remove_css_class("drop-target")
        self._perform_move(value, target_folder.path)
        return True

    def _on_root_drop(
        self, target: Gtk.DropTarget, value: str, x: float, y: float
    ) -> bool:
        """Handles a drop onto the empty (root) area."""
        self._perform_move(value, "")
        return True

    def _perform_move(self, data_string: str, target_folder_path: str) -> None:
        """Executes the logic to move a session or folder."""
        try:
            item_type, name, source_path = data_string.split("|", 2)
            if item_type == "session":
                if session := self.operations.find_session_by_name_and_path(
                    name, source_path
                ):
                    if session.folder_path != target_folder_path:
                        self.operations.move_session_to_folder(
                            session, target_folder_path
                        )
                        self.refresh_tree()
            elif item_type == "folder":
                if folder_result := self.operations.find_folder_by_path(source_path):
                    folder, pos = folder_result
                    # Prevent dropping a folder into itself or a child of itself
                    if (
                        source_path != target_folder_path
                        and not target_folder_path.startswith(source_path + "/")
                    ):
                        updated_folder = SessionFolder.from_dict(folder.to_dict())
                        updated_folder.parent_path = target_folder_path
                        updated_folder.path = (
                            f"{target_folder_path}/{name}"
                            if target_folder_path
                            else f"/{name}"
                        )
                        self.operations.update_folder(pos, updated_folder)
                        self.refresh_tree()
        except Exception as e:
            self.logger.error(f"Drag-and-drop move error: {e}")

    # --- Tree Data Management ---

    def refresh_tree(self) -> None:
        """
        Rebuilds the entire tree view from the session and folder stores.
        This is the single source of truth for updating the view.
        """
        self.logger.debug("Refreshing session tree view")
        self._is_restoring_state = True

        self.root_store.remove_all()

        folder_map = {
            folder.path: folder
            for folder in (
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            )
        }
        for folder in folder_map.values():
            folder.clear_children()

        root_items = []
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if parent_folder := folder_map.get(session.folder_path):
                parent_folder.add_child(session)
            else:
                root_items.append(session)

        for folder in folder_map.values():
            if parent_folder := folder_map.get(folder.parent_path):
                parent_folder.add_child(folder)
            else:
                root_items.append(folder)

        # Sort root items: folders first, then sessions, alphabetically
        sorted_root = sorted(
            root_items, key=lambda item: (isinstance(item, SessionItem), item.name)
        )
        for item in sorted_root:
            self.root_store.append(item)

        # Sort children within each folder
        for folder in folder_map.values():
            children = [
                folder.children.get_item(i)
                for i in range(folder.children.get_n_items())
            ]
            sorted_children = sorted(
                children, key=lambda item: (isinstance(item, SessionItem), item.name)
            )
            folder.clear_children()
            for child in sorted_children:
                folder.add_child(child)

        # Defer restoring expansion state until the UI is idle to prevent glitches
        GLib.idle_add(self._apply_expansion_state)

    def _find_row_for_path(self, path: str) -> Optional[Gtk.TreeListRow]:
        """Finds the TreeListRow corresponding to a folder path."""
        for i in range(self.tree_model.get_n_items()):
            if row := self.tree_model.get_item(i):
                if (
                    (item := row.get_item())
                    and isinstance(item, SessionFolder)
                    and item.path == path
                ):
                    return row
        return None

    def _apply_expansion_state(self) -> bool:
        """Restores the expanded state of folders from settings."""
        try:
            expanded_paths = self.settings_manager.get("tree_expanded_folders", [])
            if not expanded_paths:
                return False  # No need to continue

            self.logger.debug(f"Applying expansion state for paths: {expanded_paths}")
            # Sort by path depth to ensure parents are expanded before children
            sorted_paths = sorted(expanded_paths, key=lambda p: p.count("/"))

            for path in sorted_paths:
                if row := self._find_row_for_path(path):
                    if not row.get_expanded():
                        row.set_expanded(True)
        except Exception as e:
            self.logger.error(f"Failed to apply tree expansion state: {e}")
        finally:
            self._is_restoring_state = False
            self.logger.debug("Finished restoring expansion state.")

        return GLib.SOURCE_REMOVE  # Equivalent to False, stops the idle handler

    # --- User Interaction Handlers ---

    def _on_row_activated(self, list_view: Gtk.ListView, position: int) -> None:
        """Handles item activation (e.g., double-click or Enter key)."""
        if not (tree_list_row := self.tree_model.get_item(position)):
            return

        item = tree_list_row.get_item()
        if isinstance(item, SessionItem):
            if self.on_session_activated:
                self.on_session_activated(item)
        elif isinstance(item, SessionFolder):
            tree_list_row.set_expanded(not tree_list_row.get_expanded())

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handles key presses for shortcuts."""
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.selection_model.select_all()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self._copy_selected_item()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                self._cut_selected_item()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                target_path = ""
                if selected_item := self.get_selected_item():
                    if isinstance(selected_item, SessionFolder):
                        target_path = selected_item.path
                    elif isinstance(selected_item, SessionItem):
                        target_path = selected_item.folder_path
                self._paste_item(target_path)
                return Gdk.EVENT_STOP

        if keyval == Gdk.KEY_Delete:
            if hasattr(self.parent_window, "_on_delete_selected_items"):
                self.parent_window._on_delete_selected_items()
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def get_selected_item(self) -> Optional[Union[SessionItem, SessionFolder]]:
        """Gets the single selected item, or None if multiple/none are selected."""
        selection = self.selection_model.get_selection()
        if selection.get_size() == 1:
            if row := self.tree_model.get_item(selection.get_nth(0)):
                return row.get_item()
        return None

    def get_selected_items(self) -> List[Union[SessionItem, SessionFolder]]:
        """Gets all selected items."""
        items = []
        selection = self.selection_model.get_selection()
        for i in range(self.tree_model.get_n_items()):
            if selection.contains(i):
                if row := self.tree_model.get_item(i):
                    items.append(row.get_item())
        return items

    def _on_item_right_click(
        self,
        gesture: Gtk.GestureClick,
        n_press: int,
        x: float,
        y: float,
        list_item: Gtk.ListItem,
    ) -> None:
        """Shows a context menu for a specific item."""
        pos = list_item.get_position()
        if not self.selection_model.is_selected(pos):
            self.selection_model.unselect_all()
            self.selection_model.select_item(pos, True)

        item = list_item.get_item().get_item()
        menu_model = None
        if isinstance(item, SessionItem):
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
            popover.popup()

    def _on_empty_area_right_click(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        """Shows a context menu for the empty area (root)."""
        self.selection_model.unselect_all()
        menu_model = create_root_menu(self.has_clipboard_content())
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.column_view)
        popover.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        popover.popup()

    # --- Clipboard Operations ---

    def has_clipboard_content(self) -> bool:
        """Checks if there is a valid item in the clipboard."""
        is_valid = self._clipboard_item is not None and (
            time.time() - self._clipboard_timestamp < 600
        )
        if not is_valid:
            self._clipboard_item = None
        return is_valid

    def _copy_selected_item(self) -> None:
        """Copies the selected item to the internal clipboard."""
        if item := self.get_selected_item():
            self._clipboard_item = item
            self._clipboard_is_cut = False
            self._clipboard_timestamp = time.time()

    def _cut_selected_item(self) -> None:
        """Marks the selected item for cutting."""
        if item := self.get_selected_item():
            self._clipboard_item = item
            self._clipboard_is_cut = True
            self._clipboard_timestamp = time.time()

    def _paste_item(self, target_folder_path: str) -> None:
        """Pastes the clipboard item into the target folder."""
        if not self.has_clipboard_content():
            return

        item_to_paste = self._clipboard_item
        is_cut = self._clipboard_is_cut
        self._clipboard_item, self._clipboard_is_cut = None, False

        try:
            result = None
            if is_cut:
                if isinstance(item_to_paste, SessionItem):
                    result = self.operations.move_session_to_folder(
                        item_to_paste, target_folder_path
                    )
                elif isinstance(item_to_paste, SessionFolder):
                    updated_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    updated_folder.parent_path = target_folder_path
                    updated_folder.path = (
                        f"{target_folder_path}/{updated_folder.name}"
                        if target_folder_path
                        else f"/{updated_folder.name}"
                    )
                    found, pos = self.folder_store.find(item_to_paste)
                    if found:
                        result = self.operations.update_folder(pos, updated_folder)
            else:  # Copy
                if isinstance(item_to_paste, SessionItem):
                    new_item = SessionItem.from_dict(item_to_paste.to_dict())
                    new_item.folder_path = target_folder_path
                    new_item.name = generate_unique_name(
                        new_item.name,
                        self.operations._get_session_names_in_folder(
                            target_folder_path
                        ),
                    )
                    result = self.operations.add_session(new_item)
                elif isinstance(item_to_paste, SessionFolder):
                    new_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    new_folder.parent_path = target_folder_path
                    new_folder.path = (
                        f"{target_folder_path}/{new_folder.name}"
                        if target_folder_path
                        else f"/{new_folder.name}"
                    )
                    # Note: Pasting a folder should ideally also copy its contents recursively.
                    # This is a simplification for now.
                    result = self.operations.add_folder(new_folder)

            if result and result.success:
                self.refresh_tree()
            elif result and hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Paste Error"), result.message)
        except Exception as e:
            self.logger.error(f"Paste operation failed: {e}")
            if hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Paste Error"), str(e))
