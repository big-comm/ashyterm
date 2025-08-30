# ashyterm/sessions/tree.py

from typing import Callable, List, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from ..helpers import generate_unique_name
from ..ui.menus import create_folder_menu, create_root_menu, create_session_menu
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .operations import SessionOperations


def _get_children_model(
    item: GObject.GObject, user_data: object
) -> Optional[Gio.ListStore]:
    """Callback for Gtk.TreeListModel to get children of an item."""
    return getattr(item, "children", None)


class SessionTreeView:
    """Manages a modern tree view for sessions and folders using Gtk.ColumnView."""

    def __init__(
        self,
        parent_window: Gtk.Window,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
        operations: SessionOperations,
    ):
        """
        Initializes the SessionTreeView.

        Args:
            parent_window: The parent window, used for dialogs.
            session_store: The Gio.ListStore for SessionItem objects.
            folder_store: The Gio.ListStore for SessionFolder objects.
            settings_manager: The application's settings manager.
            operations: The injected SessionOperations instance for business logic.
        """
        self.logger = get_logger("ashyterm.sessions.tree")
        self.parent_window = parent_window
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager
        self.operations = operations  # Dependency is now injected

        self.root_store = Gio.ListStore.new(GObject.GObject)
        self.tree_model = Gtk.TreeListModel.new(
            self.root_store,
            passthrough=False,
            autoexpand=False,
            create_func=_get_children_model,
            user_data=None,
        )
        self.column_view = self._create_column_view()
        self.selection_model = self.column_view.get_model()
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut: bool = False
        self._is_restoring_state: bool = False
        self._populated_folders = set()
        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        self.refresh_tree()
        self.logger.info("SessionTreeView (ColumnView) initialized")

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

        empty_area_gesture = Gtk.GestureClick.new()
        empty_area_gesture.set_button(Gdk.BUTTON_SECONDARY)
        empty_area_gesture.connect("pressed", self._on_empty_area_right_click)
        column_view.add_controller(empty_area_gesture)

        root_drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        root_drop_target.connect("accept", lambda _, __: True)
        root_drop_target.connect("drop", self._on_root_drop)
        column_view.add_controller(root_drop_target)

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

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

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
        box.remove_css_class("indented-session")
        if isinstance(item, SessionItem) and tree_list_row.get_depth() > 0:
            box.add_css_class("indented-session")
        if isinstance(item, SessionFolder):

            def update_folder_icon(row: Gtk.TreeListRow, _=None) -> None:
                if (
                    row.get_expanded()
                    and row.get_item().path not in self._populated_folders
                ):
                    self._populate_folder_children(row.get_item())
                icon_name = (
                    "folder-open-symbolic"
                    if row.get_expanded()
                    else (
                        "folder-new-symbolic"
                        if any(s.folder_path == item.path for s in self.session_store)
                        or any(f.parent_path == item.path for f in self.folder_store)
                        else "folder-symbolic"
                    )
                )
                icon.set_from_icon_name(icon_name)

            update_folder_icon(tree_list_row)
            icon_handler_id = tree_list_row.connect(
                "notify::expanded", update_folder_icon
            )
            expansion_handler_id = tree_list_row.connect(
                "notify::expanded", self._on_folder_expansion_changed
            )
            list_item.handler_ids = [icon_handler_id, expansion_handler_id]
        elif isinstance(item, SessionItem):
            icon.set_from_icon_name(
                "computer-symbolic" if item.is_local() else "network-server-symbolic"
            )

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
        self, tree_list_row: Gtk.TreeListRow, _param
    ) -> None:
        """Saves the expansion state of a folder when it's changed by the user."""
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
        self, source: Gtk.DragSource, _drag: Gdk.Drag, list_item: Gtk.ListItem
    ) -> None:
        """Sets the drag icon when a drag begins."""
        item = list_item.get_item().get_item()
        paintable = Gtk.WidgetPaintable.new(
            Gtk.Label(label=item.name, css_classes=["drag-icon"])
        )
        source.set_icon(paintable, 0, 0)

    def _on_folder_drop_accept(
        self, _target: Gtk.DropTarget, _drop: Gdk.Drop, list_item: Gtk.ListItem
    ) -> bool:
        """Accepts a drop only if the target is a folder."""
        return isinstance(list_item.get_item().get_item(), SessionFolder)

    def _on_folder_drag_enter(
        self, _target: Gtk.DropTarget, x: float, y: float, list_item: Gtk.ListItem
    ) -> Gdk.DragAction:
        """Adds a CSS class to highlight the drop target folder."""
        if isinstance(list_item.get_item().get_item(), SessionFolder):
            list_item.get_child().add_css_class("drop-target")
            return Gdk.DragAction.MOVE
        return Gdk.DragAction.DEFAULT

    def _on_folder_drag_leave(
        self, _target: Gtk.DropTarget, list_item: Gtk.ListItem
    ) -> None:
        """Removes the highlight CSS class when the drag leaves a folder."""
        list_item.get_child().remove_css_class("drop-target")

    def _on_folder_drop(
        self,
        _target: Gtk.DropTarget,
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
        self, _target: Gtk.DropTarget, value: str, x: float, y: float
    ) -> bool:
        """Handles a drop onto the empty (root) area."""
        self._perform_move(value, "")
        return True

    def _perform_move(self, data_string: str, target_folder_path: str) -> None:
        """
        Delegates the logic to move a session or folder to the operations layer.
        """
        try:
            item_type, name, source_path = data_string.split("|", 2)
            result = None
            if item_type == "session":
                session, _ = self.operations.find_session_by_name_and_path(
                    name, source_path
                )
                if session:
                    result = self.operations.move_session_to_folder(
                        session, target_folder_path
                    )
            elif item_type == "folder":
                folder, _ = self.operations.find_folder_by_path(source_path)
                if folder:
                    result = self.operations.move_folder(folder, target_folder_path)

            if result and result.success:
                self.refresh_tree()
            elif result:
                if hasattr(self.parent_window, "_show_error_dialog"):
                    self.parent_window._show_error_dialog(_("Move Error"), result.message)
        except Exception as e:
            self.logger.error(f"Drag-and-drop move error: {e}")
            if hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Move Error"), str(e))

    def refresh_tree(self) -> None:
        """Rebuilds the entire tree view from the session and folder stores."""
        self._is_restoring_state = True
        self._populated_folders.clear()
        self.root_store.remove_all()
        for i in range(self.folder_store.get_n_items()):
            self.folder_store.get_item(i).clear_children()
        root_items = []
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if not session.folder_path:
                root_items.append(session)
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if not folder.parent_path:
                root_items.append(folder)
        sorted_root = sorted(
            root_items, key=lambda item: (isinstance(item, SessionItem), item.name)
        )
        for item in sorted_root:
            self.root_store.append(item)
        GLib.idle_add(self._apply_expansion_state)

    def _populate_folder_children(self, folder: SessionFolder):
        """Populates the children of a specific folder on-demand."""
        if folder.path in self._populated_folders:
            return
        folder.clear_children()
        children = []
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.folder_path == folder.path:
                children.append(session)
        for i in range(self.folder_store.get_n_items()):
            sub_folder = self.folder_store.get_item(i)
            if sub_folder.parent_path == folder.path:
                children.append(sub_folder)
        sorted_children = sorted(
            children, key=lambda item: (isinstance(item, SessionItem), item.name)
        )
        for child in sorted_children:
            folder.add_child(child)
        self._populated_folders.add(folder.path)

    def _apply_expansion_state(self) -> bool:
        """Restores the expanded state of folders from settings."""
        try:
            expanded_paths = self.settings_manager.get("tree_expanded_folders", [])
            if not expanded_paths:
                self._is_restoring_state = False
                return False
            sorted_paths = sorted(expanded_paths, key=lambda p: p.count("/"))
            for path in sorted_paths:
                row_to_expand = self._find_row_recursively(self.tree_model, path)
                if row_to_expand and not row_to_expand.get_expanded():
                    row_to_expand.set_expanded(True)
        except Exception as e:
            self.logger.error(f"Failed to apply tree expansion state: {e}")
        finally:
            self._is_restoring_state = False
        return GLib.SOURCE_REMOVE

    def _find_row_recursively(self, model, path_to_find):
        for i in range(model.get_n_items()):
            row = model.get_item(i)
            if not row:
                continue
            item = row.get_item()
            if isinstance(item, SessionFolder):
                if item.path == path_to_find:
                    return row
                if path_to_find.startswith(item.path + "/"):
                    if item.path not in self._populated_folders:
                        self._populate_folder_children(item)
                    child_model = self.tree_model.get_model_for_row(row)
                    if child_model:
                        if found_in_child := self._find_row_recursively(
                            child_model, path_to_find
                        ):
                            return found_in_child
        return None

    def _on_row_activated(self, _list_view: Gtk.ListView, position: int) -> None:
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
        _keycode: int,
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
                    target_path = (
                        selected_item.path
                        if isinstance(selected_item, SessionFolder)
                        else selected_item.folder_path
                    )
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
        """Gets all selected items from the tree view."""
        items = []
        selection = self.selection_model.get_selection()
        size = selection.get_size()
        for i in range(size):
            position = selection.get_nth(i)
            if row := self.tree_model.get_item(position):
                items.append(row.get_item())
        return items

    def _on_item_right_click(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
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
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        """Shows a context menu for the empty area (root)."""
        self.selection_model.unselect_all()
        menu_model = create_root_menu(self.has_clipboard_content())
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(self.column_view)
        popover.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        popover.popup()

    def has_clipboard_content(self) -> bool:
        """Checks if there is a valid item in the clipboard."""
        is_valid = self._clipboard_item is not None
        if not is_valid:
            self._clipboard_item = None
        return is_valid

    def _copy_selected_item(self) -> None:
        """Copies the selected item to the internal clipboard."""
        if item := self.get_selected_item():
            self._clipboard_item = item
            self._clipboard_is_cut = False

    def _cut_selected_item(self) -> None:
        """Marks the selected item for cutting."""
        if item := self.get_selected_item():
            self._clipboard_item = item
            self._clipboard_is_cut = True

    def _paste_item(self, target_folder_path: str) -> None:
        """
        Delegates the paste logic to the operations layer.
        """
        if not self.has_clipboard_content():
            return

        item_to_paste = self._clipboard_item
        is_cut = self._clipboard_is_cut
        self._clipboard_item, self._clipboard_is_cut = None, False

        try:
            result = self.operations.paste_item(
                item_to_paste, target_folder_path, is_cut
            )
            if result and result.success:
                self.refresh_tree()
            elif result:
                if hasattr(self.parent_window, "_show_error_dialog"):
                    self.parent_window._show_error_dialog(
                        _("Paste Error"), result.message
                    )
        except Exception as e:
            self.logger.error(f"Paste operation failed: {e}")
            if hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Paste Error"), str(e))
