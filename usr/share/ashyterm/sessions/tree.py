# sessions/tree.py

import threading
import time
from typing import Optional, Callable, Dict, Tuple, Union, Any, List

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, Gdk, GLib

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
from ..ui.menus import (
    create_session_menu, create_folder_menu, create_root_menu, setup_context_menu
)


class TreeItemData:
    """Data structure for tracking tree view items."""
    
    def __init__(self, item: Union[SessionItem, SessionFolder], item_type: str, 
                 tree_iter: Gtk.TreeIter, path: str):
        """
        Initialize tree item data.
        
        Args:
            item: The actual SessionItem or SessionFolder
            item_type: Type identifier ('session' or 'folder')
            tree_iter: GTK TreeIter reference
            path: Display path for the item
        """
        self.item = item
        self.item_type = item_type
        self.tree_iter = tree_iter
        self.path = path
        self.created_at = time.time()
        self.last_accessed = time.time()
        self.access_count = 0
    
    def mark_accessed(self) -> None:
        """Mark item as accessed."""
        self.last_accessed = time.time()
        self.access_count += 1


class TreeViewRegistry:
    """Registry for tracking tree view items and their state."""
    
    def __init__(self):
        """Initialize tree view registry."""
        self.logger = get_logger('ashyterm.sessions.tree.registry')
        self._items: Dict[str, TreeItemData] = {}
        self._lock = threading.RLock()
        self._next_id = 1
    
    def register_item(self, item: Union[SessionItem, SessionFolder], 
                     item_type: str, tree_iter: Gtk.TreeIter) -> str:
        """
        Register a tree item.
        
        Args:
            item: SessionItem or SessionFolder
            item_type: Type identifier
            tree_iter: GTK TreeIter
            
        Returns:
            Item ID
        """
        with self._lock:
            item_id = f"{item_type}_{self._next_id}"
            self._next_id += 1
            
            path = getattr(item, 'folder_path', '') if item_type == 'session' else getattr(item, 'path', '')
            
            self._items[item_id] = TreeItemData(item, item_type, tree_iter, path)
            
            self.logger.debug(f"Tree item registered: {item_id} ({item_type}: {getattr(item, 'name', 'Unknown')})")
            return item_id
    
    def unregister_item(self, item_id: str) -> bool:
        """Unregister a tree item."""
        with self._lock:
            if item_id in self._items:
                item_data = self._items.pop(item_id)
                self.logger.debug(f"Tree item unregistered: {item_id}")
                return True
            return False
    
    def get_item_data(self, item_id: str) -> Optional[TreeItemData]:
        """Get tree item data by ID."""
        with self._lock:
            return self._items.get(item_id)
    
    def find_item_by_reference(self, item: Union[SessionItem, SessionFolder]) -> Optional[str]:
        """Find item ID by object reference."""
        with self._lock:
            for item_id, item_data in self._items.items():
                if item_data.item == item:
                    return item_id
            return None
    
    def clear_all(self) -> None:
        """Clear all registered items."""
        with self._lock:
            self._items.clear()
            self.logger.debug("All tree items cleared")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            stats = {
                'total_items': len(self._items),
                'sessions': sum(1 for item in self._items.values() if item.item_type == 'session'),
                'folders': sum(1 for item in self._items.values() if item.item_type == 'folder')
            }
            return stats


class SessionTreeView:
    """Enhanced tree view manager for sessions and folders in the sidebar."""
    
    def __init__(self, parent_window, session_store: Gio.ListStore, folder_store: Gio.ListStore, settings_manager):
        """
        Initialize enhanced session tree view.
        
        Args:
            session_store: Store containing SessionItem objects
            folder_store: Store containing SessionFolder objects
        """
        self.logger = get_logger('ashyterm.sessions.tree')
        self.parent_window = parent_window
        self.session_store = session_store
        self.folder_store = folder_store
        self.platform_info = get_platform_info()
        
        # Core components
        self.operations = SessionOperations(session_store, folder_store, settings_manager)
        self.registry = TreeViewRegistry()
        
        # Thread safety
        self._ui_lock = threading.RLock()
        self._focus_lock = threading.Lock()
        self._context_lock = threading.Lock()
        
        # UI components
        self.tree_store = Gtk.TreeStore(str, str, str, str)  # name, type, icon, path
        self.tree_view = self._create_tree_view()
        
        # State management
        self._current_selection: Optional[Union[SessionItem, SessionFolder]] = None
        self._focus_state = False
        self._expanded_paths: set = set()
        
        # --- CHANGED: State variables moved here from CommTerminalWindow ---
        # Clipboard for cut/copy operations
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut = False
        self._clipboard_timestamp = 0
        
        # Context tracking with enhanced validation
        self.current_session_context: Optional[SessionItem] = None
        self.current_folder_context: Optional[SessionFolder] = None
        self.current_context_position: int = -1
        
        # Security and validation
        self.security_auditor = None
        self._initialize_security()
        
        # Statistics
        self._stats = {
            'tree_refreshes': 0,
            'item_activations': 0,
            'context_menus_shown': 0,
            'clipboard_operations': 0,
            'focus_changes': 0,
            'ui_errors': 0
        }
        
        # Callbacks with enhanced error handling
        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        self.on_folder_expanded: Optional[Callable[[SessionFolder, bool], None]] = None
        self.on_selection_changed: Optional[Callable] = None
        self.on_focus_changed: Optional[Callable[[bool], None]] = None
        
        # Initial population
        self.refresh_tree()
        
        self.logger.info("Enhanced session tree view initialized")
    
    def _initialize_security(self) -> None:
        """Initialize security subsystems."""
        try:
            self.security_auditor = create_security_auditor()
            self.logger.debug("Security auditor initialized for tree view")
        except Exception as e:
            self.logger.warning(f"Security auditor initialization failed: {e}")
    
    def _create_tree_view(self) -> Gtk.TreeView:
        """
        Create and configure the enhanced tree view widget.
        
        Returns:
            Configured Gtk.TreeView with error handling
        """
        try:
            self.logger.debug("Creating enhanced tree view widget")
            
            tree_view = Gtk.TreeView(model=self.tree_store)
            tree_view.set_headers_visible(False)
            tree_view.set_enable_search(True)
            tree_view.set_search_column(0)  # Search by name
            
            # Create column with icon and text
            column = Gtk.TreeViewColumn()
            column.set_title(_("Sessions"))
            column.set_expand(True)
            
            # Icon renderer
            icon_renderer = Gtk.CellRendererPixbuf()
            # GTK4 compatibility - stock-size property was removed in GTK4
            try:
                # Try GTK4 approach - set fixed size
                icon_renderer.set_fixed_size(16, 16)
            except AttributeError:
                try:
                    # Fallback for older GTK versions
                    icon_renderer.set_property("width", 16)
                    icon_renderer.set_property("height", 16)
                except Exception as e:
                    # If all fails, just continue without setting size
                    self.logger.debug(f"Could not set icon renderer size: {e}")

            column.pack_start(icon_renderer, False)
            column.add_attribute(icon_renderer, "icon-name", 2)
            
            # Text renderer with enhanced properties
            text_renderer = Gtk.CellRendererText()
            text_renderer.set_property("ellipsize", 3)  # Ellipsize at end
            column.pack_start(text_renderer, True)
            column.add_attribute(text_renderer, "text", 0)
            
            tree_view.append_column(column)
            
            # Connect signals with error handling
            self._connect_tree_signals(tree_view)
            
            # Set up event controllers
            self._setup_event_controllers(tree_view)
            
            # Platform-specific configurations
            if is_windows():
                # Windows-specific tree view settings
                tree_view.set_grid_lines(Gtk.TreeViewGridLines.NONE)
            
            self.logger.debug("Tree view widget created successfully")
            return tree_view
            
        except Exception as e:
            self.logger.error(f"Tree view creation failed: {e}")
            raise UIError("tree_view", _("Tree view creation failed: {}").format(e))
    
    def _connect_tree_signals(self, tree_view: Gtk.TreeView) -> None:
        """Connect tree view signals with error handling."""
        try:
            # Row activation signal
            tree_view.connect("row-activated", self._on_row_activated_safe)
            
            # Selection changed signal
            selection = tree_view.get_selection()
            selection.connect("changed", self._on_selection_changed_safe)
            
            # Row expansion signals
            tree_view.connect("row-expanded", self._on_row_expanded_safe)
            tree_view.connect("row-collapsed", self._on_row_collapsed_safe)
            
            self.logger.debug("Tree view signals connected")
            
        except Exception as e:
            self.logger.error(f"Signal connection failed: {e}")
            raise UIError("tree_signals", _("Signal connection failed: {}").format(e))
    
    def _setup_event_controllers(self, tree_view: Gtk.TreeView) -> None:
        """Set up event controllers for the tree view with comprehensive handling."""
        try:
            # Right-click for context menu
            right_click = Gtk.GestureClick()
            right_click.set_button(Gdk.BUTTON_SECONDARY)
            right_click.connect("released", self._on_right_click_safe)
            tree_view.add_controller(right_click)
            
            # Left-click for focus tracking
            left_click = Gtk.GestureClick()
            left_click.set_button(Gdk.BUTTON_PRIMARY)
            left_click.connect("pressed", self._on_left_click_safe)
            tree_view.add_controller(left_click)
            
            # Keyboard events
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_key_pressed_safe)
            tree_view.add_controller(key_controller)
            
            # Focus events
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_focus_in_safe)
            focus_controller.connect("leave", self._on_focus_out_safe)
            tree_view.add_controller(focus_controller)
            
            # Drag and drop (future enhancement)
            # drag_source = Gtk.DragSource()
            # tree_view.add_controller(drag_source)
            
            self.logger.debug("Event controllers configured")
            
        except Exception as e:
            self.logger.error(f"Event controller setup failed: {e}")
            raise UIError("event_controllers", _("Event controller setup failed: {}").format(e))
    
    def get_widget(self) -> Gtk.TreeView:
        """Get the tree view widget."""
        return self.tree_view
    
    def refresh_tree(self) -> bool:
        """
        Refresh the tree view from the stores with comprehensive error handling.
        
        Returns:
            True if refresh was successful
        """
        with self._ui_lock:
            try:
                self.logger.debug("Refreshing session tree view")
                start_time = time.time()
                
                # Store expanded state
                self._store_expanded_state()
                
                # Clear existing data
                self.tree_store.clear()
                self.registry.clear_all()
                
                # Track folder iterators for hierarchy building
                folder_iters: Dict[str, Gtk.TreeIter] = {}
                
                # Add folders first, sorted by depth and path
                folders_added = self._add_folders_to_tree(folder_iters)
                
                # Add sessions
                sessions_added = self._add_sessions_to_tree(folder_iters)
                
                # Restore expanded state
                self._restore_expanded_state()
                
                # Update statistics
                self._stats['tree_refreshes'] += 1
                
                refresh_time = time.time() - start_time
                self.logger.info(f"Tree refreshed: {sessions_added} sessions, {folders_added} folders (took {refresh_time:.3f}s)")
                
                return True
                
            except Exception as e:
                self._stats['ui_errors'] += 1
                self.logger.error(f"Tree refresh failed: {e}")
                log_error_with_context(e, "tree refresh", "ashyterm.sessions.tree")
                
                # Try to recover with empty tree
                try:
                    self.tree_store.clear()
                    self.registry.clear_all()
                except Exception as recovery_error:
                    self.logger.error(f"Tree recovery failed: {recovery_error}")
                
                return False
    
    def _add_folders_to_tree(self, folder_iters: Dict[str, Gtk.TreeIter]) -> int:
        """
        Add folders to tree with error handling.
        
        Args:
            folder_iters: Dictionary to store folder iterators
            
        Returns:
            Number of folders added
        """
        try:
            folders = []
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder):
                    folders.append(folder)
            
            # Sort folders by depth (root folders first, then children)
            sorted_folders = sorted(folders, key=lambda f: (f.path.count('/'), f.path))
            
            folders_added = 0
            for folder in sorted_folders:
                try:
                    # Validate folder before adding
                    if not folder.validate():
                        errors = folder.get_validation_errors()
                        self.logger.warning(f"Skipping invalid folder '{folder.name}': {errors}")
                        continue
                    
                    parent_iter = folder_iters.get(folder.parent_path) if folder.parent_path else None
                    folder_iter = self.tree_store.append(
                        parent_iter,
                        [folder.name, "folder", "folder-symbolic", folder.path]
                    )
                    folder_iters[folder.path] = folder_iter
                    
                    # Register with registry
                    self.registry.register_item(folder, "folder", folder_iter)
                    
                    folders_added += 1
                    
                except Exception as e:
                    self.logger.error(f"Failed to add folder '{folder.name}': {e}")
                    continue
            
            return folders_added
            
        except Exception as e:
            self.logger.error(f"Failed to add folders to tree: {e}")
            return 0
    
    def _add_sessions_to_tree(self, folder_iters: Dict[str, Gtk.TreeIter]) -> int:
        """
        Add sessions to tree with validation and error handling.
        
        Args:
            folder_iters: Dictionary of folder iterators
            
        Returns:
            Number of sessions added
        """
        try:
            sessions_added = 0
            
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem):
                    try:
                        # Validate session before adding
                        if not session.validate():
                            errors = session.get_validation_errors()
                            self.logger.warning(f"Skipping invalid session '{session.name}': {errors}")
                            continue
                        
                        # Security validation for SSH sessions
                        if self.security_auditor and session.is_ssh():
                            try:
                                session_data = session.to_dict()
                                is_valid, validation_errors = validate_session_data(session_data)
                                if not is_valid:
                                    self.logger.warning(f"Session '{session.name}' security validation failed: {validation_errors}")
                                    # Still add but log the warning
                            except Exception as e:
                                self.logger.warning(f"Security validation failed for session '{session.name}': {e}")
                        
                        parent_iter = folder_iters.get(session.folder_path) if session.folder_path else None
                        icon_name = "computer-symbolic" if session.is_local() else "network-server-symbolic"
                        
                        session_iter = self.tree_store.append(
                            parent_iter,
                            [session.name, "session", icon_name, session.folder_path]
                        )
                        
                        # Register with registry
                        self.registry.register_item(session, "session", session_iter)
                        
                        sessions_added += 1
                        
                    except Exception as e:
                        self.logger.error(f"Failed to add session '{session.name}': {e}")
                        continue
            
            return sessions_added
            
        except Exception as e:
            self.logger.error(f"Failed to add sessions to tree: {e}")
            return 0
    
    def _store_expanded_state(self) -> None:
        """Store current expanded state of tree nodes."""
        try:
            self._expanded_paths.clear()
            
            def store_expanded(model, path, iter_ref, user_data):
                if self.tree_view.row_expanded(path):
                    path_str = model.get_string_from_iter(iter_ref)
                    self._expanded_paths.add(path_str)
                return False
            
            self.tree_store.foreach(store_expanded, None)
            
            self.logger.debug(f"Stored {len(self._expanded_paths)} expanded paths")
            
        except Exception as e:
            self.logger.error(f"Failed to store expanded state: {e}")
    
    def _restore_expanded_state(self) -> None:
        """Restore previously expanded state of tree nodes."""
        try:
            if not self._expanded_paths:
                return
            
            def restore_expanded(model, path, iter_ref, user_data):
                path_str = model.get_string_from_iter(iter_ref)
                if path_str in self._expanded_paths:
                    self.tree_view.expand_row(path, False)
                return False
            
            # Use timeout to allow UI to settle
            GLib.timeout_add(50, lambda: (
                self.tree_store.foreach(restore_expanded, None),
                False  # Don't repeat
            )[1])
            
        except Exception as e:
            self.logger.error(f"Failed to restore expanded state: {e}")
    
    # Safe event handlers with comprehensive error handling
    def _on_row_activated_safe(self, tree_view: Gtk.TreeView, path: Gtk.TreePath, 
                              column: Gtk.TreeViewColumn) -> None:
        """Safe row activation handler with error handling."""
        try:
            self._stats['item_activations'] += 1
            
            model = tree_view.get_model()
            tree_iter = model.get_iter(path)
            
            if tree_iter:
                item = self._find_item_by_tree_iter_safe(tree_iter, model)
                if isinstance(item, SessionItem):
                    # Validate session before activation
                    if not item.validate():
                        errors = item.get_validation_errors()
                        self.logger.error(f"Cannot activate invalid session '{item.name}': {errors}")
                        return
                    
                    # Security check for SSH sessions
                    if self.security_auditor and item.is_ssh():
                        try:
                            session_data = item.to_dict()
                            is_valid, validation_errors = validate_session_data(session_data)
                            if not is_valid:
                                self.logger.error(f"Cannot activate session with security issues: {validation_errors}")
                                return
                        except Exception as e:
                            self.logger.warning(f"Security validation failed for session activation: {e}")
                    
                    # Mark session as used
                    item.mark_used()
                    
                    if self.on_session_activated:
                        self.on_session_activated(item)
                    
                    log_session_event("activated", item.name, f"from tree view")
                    
                elif isinstance(item, SessionFolder):
                    # Toggle folder expansion
                    if tree_view.row_expanded(path):
                        tree_view.collapse_row(path)
                    else:
                        tree_view.expand_row(path, False)
                    
                    if self.on_folder_expanded:
                        self.on_folder_expanded(item, not tree_view.row_expanded(path))
                    
                    log_session_event("folder_toggled", item.name, f"expanded: {tree_view.row_expanded(path)}")
                
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Row activation failed: {e}")
            log_error_with_context(e, "row activation", "ashyterm.sessions.tree")
    
    def _on_selection_changed_safe(self, selection: Gtk.TreeSelection) -> None:
        """Safe selection change handler."""
        try:
            model, tree_iter = selection.get_selected()
            
            if tree_iter:
                item = self._find_item_by_tree_iter_safe(tree_iter, model)
                self._current_selection = item
                
                # --- CHANGED: Set local context instead of parent window's ---
                if isinstance(item, SessionItem):
                    self.current_session_context = item
                    self.current_folder_context = None
                elif isinstance(item, SessionFolder):
                    self.current_folder_context = item
                    self.current_session_context = None
                else:
                    self.current_session_context = None
                    self.current_folder_context = None

                if item:
                    # Find registry entry and mark as accessed
                    item_id = self.registry.find_item_by_reference(item)
                    if item_id:
                        item_data = self.registry.get_item_data(item_id)
                        if item_data:
                            item_data.mark_accessed()
            else:
                self._current_selection = None
                self.current_session_context = None
                self.current_folder_context = None
            
            if self.on_selection_changed:
                self.on_selection_changed()
                
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Selection change handling failed: {e}")
    
    def _on_row_expanded_safe(self, tree_view: Gtk.TreeView, tree_iter: Gtk.TreeIter, 
                             path: Gtk.TreePath) -> None:
        """Safe row expansion handler."""
        try:
            model = tree_view.get_model()
            item = self._find_item_by_tree_iter_safe(tree_iter, model)
            
            if isinstance(item, SessionFolder):
                path_str = model.get_string_from_iter(tree_iter)
                self._expanded_paths.add(path_str)
                
                if self.on_folder_expanded:
                    self.on_folder_expanded(item, True)
                    
        except Exception as e:
            self.logger.error(f"Row expansion handling failed: {e}")
    
    def _on_row_collapsed_safe(self, tree_view: Gtk.TreeView, tree_iter: Gtk.TreeIter, 
                              path: Gtk.TreePath) -> None:
        """Safe row collapse handler."""
        try:
            model = tree_view.get_model()
            item = self._find_item_by_tree_iter_safe(tree_iter, model)
            
            if isinstance(item, SessionFolder):
                path_str = model.get_string_from_iter(tree_iter)
                self._expanded_paths.discard(path_str)
                
                if self.on_folder_expanded:
                    self.on_folder_expanded(item, False)
                    
        except Exception as e:
            self.logger.error(f"Row collapse handling failed: {e}")
    
    def _on_left_click_safe(self, gesture, n_press, x, y) -> None:
        """Safe left-click handler for focus and selection."""
        try:
            self._update_focus_state(True)
            
            # Update selection
            path_info = self.tree_view.get_path_at_pos(int(x), int(y))
            if path_info:
                path, _, _, _ = path_info
                selection = self.tree_view.get_selection()
                selection.select_path(path)
            
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Left click handling failed: {e}")

    def _on_right_click_safe(self, gesture, n_press, x, y) -> None:
        """Safe right-click handler for context menu."""
        with self._context_lock:
            try:
                self._stats['context_menus_shown'] += 1
                self._update_focus_state(True)
                
                path_info = self.tree_view.get_path_at_pos(int(x), int(y))
                menu: Optional[Gtk.PopoverMenu] = None
                
                if path_info:
                    # Right-click on an item
                    path, _, _, _ = path_info
                    model = self.tree_view.get_model()
                    tree_iter = model.get_iter(path)
                    
                    if tree_iter:
                        # Select the item
                        selection = self.tree_view.get_selection()
                        selection.select_path(path)
                        
                        # Find the actual item
                        item = self._find_item_by_tree_iter_safe(tree_iter, model)
                        if isinstance(item, SessionItem):
                            # --- CHANGED: Set local context ---
                            self.current_session_context = item
                            self.current_folder_context = None
                            
                            # Validate session for context menu
                            menu_valid = True
                            if not item.validate():
                                menu_valid = False
                                self.logger.warning(f"Context menu for invalid session: {item.name}")
                            
                            if menu_valid:
                                menu = create_session_menu(
                                    self.parent_window, item, self.session_store,
                                    self._find_item_position(item),
                                    self.folder_store,
                                    self.has_clipboard_content() # --- CHANGED: Use local state ---
                                )
                            
                        elif isinstance(item, SessionFolder):
                            # --- CHANGED: Set local context ---
                            self.current_folder_context = item
                            self.current_session_context = None
                            
                            # Validate folder for context menu
                            menu_valid = True
                            if not item.validate():
                                menu_valid = False
                                self.logger.warning(f"Context menu for invalid folder: {item.name}")
                            
                            if menu_valid:
                                menu = create_folder_menu(
                                    self.parent_window, item, self.folder_store,
                                    self._find_item_position(item),
                                    self.session_store,
                                    self.has_clipboard_content() # --- CHANGED: Use local state ---
                                )
                else:
                    # --- CHANGED: Clear local context ---
                    self.current_session_context = None
                    self.current_folder_context = None
                    menu = create_root_menu(self.parent_window, self.has_clipboard_content()) # --- CHANGED: Use local state ---
                
                if menu:
                    setup_context_menu(self.tree_view, menu, x, y)
                
            except Exception as e:
                self._stats['ui_errors'] += 1
                self.logger.error(f"Right click handling failed: {e}")
                log_error_with_context(e, "context menu", "ashyterm.sessions.tree")
    
    def _on_key_pressed_safe(self, controller, keyval, keycode, state) -> bool:
        """Safe keyboard event handler."""
        try:
            if keyval == Gdk.KEY_Delete:
                self._delete_selected_item_safe()
                return Gdk.EVENT_STOP
            elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._activate_selected_item_safe()
                return Gdk.EVENT_STOP
            elif keyval == Gdk.KEY_F2:  # Rename
                self._rename_selected_item_safe()
                return Gdk.EVENT_STOP
            elif state & Gdk.ModifierType.CONTROL_MASK:
                if keyval == Gdk.KEY_c:  # Copy
                    self._copy_selected_item_safe()
                    return Gdk.EVENT_STOP
                elif keyval == Gdk.KEY_x:  # Cut
                    self._cut_selected_item_safe()
                    return Gdk.EVENT_STOP
                elif keyval == Gdk.KEY_v:  # Paste
                    # --- CHANGED: Determine paste target from local context ---
                    target_path = ""
                    selected_item = self.get_selected_item()
                    if isinstance(selected_item, SessionFolder):
                        target_path = selected_item.path
                    elif isinstance(selected_item, SessionItem):
                        target_path = selected_item.folder_path
                    self._paste_item_safe(target_path)
                    return Gdk.EVENT_STOP
            
            return Gdk.EVENT_PROPAGATE
            
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Key press handling failed: {e}")
            return Gdk.EVENT_PROPAGATE
    
    def _on_focus_in_safe(self, controller) -> None:
        """Safe focus in handler."""
        try:
            self._update_focus_state(True)
        except Exception as e:
            self.logger.error(f"Focus in handling failed: {e}")
    
    def _on_focus_out_safe(self, controller) -> None:
        """Safe focus out handler."""
        try:
            self._update_focus_state(False)
        except Exception as e:
            self.logger.error(f"Focus out handling failed: {e}")
    
    def _update_focus_state(self, has_focus: bool) -> None:
        """Update focus state with thread safety."""
        with self._focus_lock:
            if self._focus_state != has_focus:
                self._focus_state = has_focus
                self._stats['focus_changes'] += 1
                
                if self.on_focus_changed:
                    self.on_focus_changed(has_focus)
    
    def _find_item_by_tree_iter_safe(self, tree_iter: Gtk.TreeIter, 
                                    model: Gtk.TreeStore) -> Optional[Union[SessionItem, SessionFolder]]:
        """
        Safely find the actual item corresponding to a tree iterator.
        
        Args:
            tree_iter: Tree iterator
            model: Tree model
            
        Returns:
            SessionItem or SessionFolder, or None if not found
        """
        try:
            item_name = model[tree_iter][0]
            item_type = model[tree_iter][1]
            path_data = model[tree_iter][3]
            
            if item_type == "session":
                # Find session by name and folder path
                for i in range(self.session_store.get_n_items()):
                    session = self.session_store.get_item(i)
                    if (isinstance(session, SessionItem) and 
                        session.name == item_name and 
                        session.folder_path == path_data):
                        return session
            elif item_type == "folder":
                # Find folder by path
                for i in range(self.folder_store.get_n_items()):
                    folder = self.folder_store.get_item(i)
                    if (isinstance(folder, SessionFolder) and 
                        folder.name == item_name and 
                        folder.path == path_data):
                        return folder
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding item by tree iterator: {e}")
            return None
    
    def _find_item_position(self, item: Union[SessionItem, SessionFolder]) -> int:
        """Safely find the position of an item in its store."""
        try:
            if isinstance(item, SessionItem):
                for i in range(self.session_store.get_n_items()):
                    if self.session_store.get_item(i) == item:
                        return i
            elif isinstance(item, SessionFolder):
                for i in range(self.folder_store.get_n_items()):
                    if self.folder_store.get_item(i) == item:
                        return i
            return -1
        except Exception as e:
            self.logger.error(f"Error finding item position: {e}")
            return -1
    
    def _delete_selected_item_safe(self) -> None:
        """Safely delete the currently selected item."""
        try:
            selection = self.tree_view.get_selection()
            model, tree_iter = selection.get_selected()
            
            if tree_iter:
                item = self._find_item_by_tree_iter_safe(tree_iter, model)
                if isinstance(item, SessionItem):
                    result = self.operations.remove_session(item)
                    if result.success:
                        self.refresh_tree()
                        log_session_event("deleted", item.name, "from tree view")
                    else:
                        self.logger.error(f"Failed to delete session: {result.message}")
                        
                elif isinstance(item, SessionFolder):
                    result = self.operations.remove_folder(item)
                    if result.success:
                        self.refresh_tree()
                        log_session_event("folder_deleted", item.name, "from tree view")
                    else:
                        self.logger.error(f"Failed to delete folder: {result.message}")
                        
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Delete selected item failed: {e}")
    
    def _activate_selected_item_safe(self) -> None:
        """Safely activate the currently selected item."""
        try:
            selection = self.tree_view.get_selection()
            model, tree_iter = selection.get_selected()
            
            if tree_iter:
                path = model.get_path(tree_iter)
                self._on_row_activated_safe(self.tree_view, path, None)
                
        except Exception as e:
            self._stats['ui_errors'] += 1
            self.logger.error(f"Activate selected item failed: {e}")
    
    def _rename_selected_item_safe(self) -> None:
        """Safely rename the currently selected item."""
        try:
            # This would open a rename dialog
            # Implementation depends on UI dialog system
            selection = self.tree_view.get_selection()
            model, tree_iter = selection.get_selected()
            
            if tree_iter:
                item = self._find_item_by_tree_iter_safe(tree_iter, model)
                if item:
                    self.logger.debug(f"Rename requested for: {getattr(item, 'name', 'Unknown')}")
                    # Would trigger rename dialog here
                    
        except Exception as e:
            self.logger.error(f"Rename selected item failed: {e}")
    
    # --- CHANGED: New clipboard methods added ---
    def cut_item(self, item: Union[SessionItem, SessionFolder]) -> bool:
        """
        Cut an item to clipboard with validation.
        
        Args:
            item: Item to cut
            
        Returns:
            True if operation successful
        """
        try:
            if isinstance(item, SessionItem):
                if not item.validate():
                    self.logger.error(f"Cannot cut invalid session: {item.name}")
                    return False
            elif isinstance(item, SessionFolder):
                if not item.validate():
                    self.logger.error(f"Cannot cut invalid folder: {item.name}")
                    return False
            
            self._clipboard_item = item
            self._clipboard_is_cut = True
            self._clipboard_timestamp = time.time()
            self._stats['clipboard_operations'] += 1
            
            self.logger.debug(f"Item cut to clipboard: {getattr(item, 'name', 'Unknown')}")
            return True
            
        except Exception as e:
            self.logger.error(f"Cut item failed: {e}")
            return False
    
    def copy_item(self, item: Union[SessionItem, SessionFolder]) -> bool:
        """
        Copy an item to clipboard with validation.
        
        Args:
            item: Item to copy
            
        Returns:
            True if operation successful
        """
        try:
            if isinstance(item, SessionItem):
                if not item.validate():
                    self.logger.error(f"Cannot copy invalid session: {item.name}")
                    return False
                self._clipboard_item = SessionItem.from_dict(item.to_dict())
            elif isinstance(item, SessionFolder):
                if not item.validate():
                    self.logger.error(f"Cannot copy invalid folder: {item.name}")
                    return False
                self._clipboard_item = SessionFolder.from_dict(item.to_dict())
            else:
                return False
            
            self._clipboard_is_cut = False
            self._clipboard_timestamp = time.time()
            self._stats['clipboard_operations'] += 1
            
            self.logger.debug(f"Item copied to clipboard: {getattr(item, 'name', 'Unknown')}")
            return True
            
        except Exception as e:
            self.logger.error(f"Copy item failed: {e}")
            return False
    
    def paste_item(self, target_folder_path: str = "") -> bool:
        """
        Paste clipboard item to target folder with comprehensive validation.
        
        Args:
            target_folder_path: Target folder path (empty for root)
            
        Returns:
            True if paste was successful
        """
        try:
            if not self._clipboard_item:
                self.logger.debug("No clipboard content to paste")
                return False
            
            # Check clipboard age (expire after 10 minutes)
            if time.time() - self._clipboard_timestamp > 600:
                self.logger.warning("Clipboard content expired")
                self._clipboard_item = None
                return False
            
            success = False
            
            if self._clipboard_is_cut:
                # Move operation
                if isinstance(self._clipboard_item, SessionItem):
                    result = self.operations.move_session_to_folder(self._clipboard_item, target_folder_path)
                    success = result.success
                    if success:
                        log_session_event("moved", self._clipboard_item.name, f"via clipboard to: {target_folder_path}")
                    else:
                        self.logger.error(f"Move failed: {result.message}")
                        
                elif isinstance(self._clipboard_item, SessionFolder):
                    # Move folder (update parent path)
                    old_parent = self._clipboard_item.parent_path
                    self._clipboard_item.parent_path = target_folder_path
                    self._clipboard_item.path = target_folder_path + "/" + self._clipboard_item.name if target_folder_path else "/" + self._clipboard_item.name
                    
                    # This would need folder update operation
                    success = True  # Simplified for now
                    
                    if success:
                        log_session_event("folder_moved", self._clipboard_item.name, f"via clipboard from: {old_parent} to: {target_folder_path}")
                
                if success:
                    self._clipboard_item = None
                    self._clipboard_is_cut = False
            else:
                # Copy operation
                if isinstance(self._clipboard_item, SessionItem):
                    new_session = SessionItem.from_dict(self._clipboard_item.to_dict())
                    new_session.folder_path = target_folder_path
                    
                    result = self.operations.add_session(new_session)
                    success = result.success
                    if success:
                        log_session_event("copied", new_session.name, f"via clipboard to: {target_folder_path}")
                    else:
                        self.logger.error(f"Copy failed: {result.message}")
                        
                elif isinstance(self._clipboard_item, SessionFolder):
                    # TODO: Implement recursive folder copy
                    self.logger.warning("Folder copying not yet implemented")
                    success = False
            
            if success:
                self.refresh_tree()
                self._stats['clipboard_operations'] += 1
            
            return success
            
        except Exception as e:
            self.logger.error(f"Paste item failed: {e}")
            return False
    
    def _cut_selected_item_safe(self) -> None:
        """Safely cut the selected item."""
        try:
            item = self.get_selected_item()
            if item:
                self.cut_item(item)
        except Exception as e:
            self.logger.error(f"Cut selected item failed: {e}")
    
    def _copy_selected_item_safe(self) -> None:
        """Safely copy the selected item."""
        try:
            item = self.get_selected_item()
            if item:
                self.copy_item(item)
        except Exception as e:
            self.logger.error(f"Copy selected item failed: {e}")
    
    def _paste_item_safe(self, target_folder_path: str) -> None:
        """Safely paste to target folder."""
        try:
            self.paste_item(target_folder_path)
        except Exception as e:
            self.logger.error(f"Paste item failed: {e}")
    
    def get_selected_item(self) -> Optional[Union[SessionItem, SessionFolder]]:
        """Get the currently selected item with error handling."""
        try:
            return self._current_selection
        except Exception as e:
            self.logger.error(f"Get selected item failed: {e}")
            return None
    
    def has_clipboard_content(self) -> bool:
        """Check if clipboard has valid content."""
        try:
            if not self._clipboard_item:
                return False
            
            # Check if content is expired
            if time.time() - self._clipboard_timestamp > 600:  # 10 minutes
                self._clipboard_item = None
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Clipboard content check failed: {e}")
            return False
    
    def expand_folder(self, folder_path: str) -> bool:
        """
        Expand a folder in the tree view.
        
        Args:
            folder_path: Path of folder to expand
            
        Returns:
            True if folder was found and expanded
        """
        try:
            # Find the folder in the tree model
            def find_and_expand(model, path, iter_ref, user_data):
                item_type = model[iter_ref][1]
                item_path = model[iter_ref][3]
                
                if item_type == "folder" and item_path == folder_path:
                    self.tree_view.expand_row(path, False)
                    return True  # Stop iteration
                return False
            
            self.tree_store.foreach(find_and_expand, None)
            return True
            
        except Exception as e:
            self.logger.error(f"Expand folder failed: {e}")
            return False
    
    def select_item(self, item: Union[SessionItem, SessionFolder]) -> bool:
        """
        Select a specific item in the tree view.
        
        Args:
            item: Item to select
            
        Returns:
            True if item was found and selected
        """
        try:
            # Find the item in registry
            item_id = self.registry.find_item_by_reference(item)
            if not item_id:
                return False
            
            item_data = self.registry.get_item_data(item_id)
            if not item_data:
                return False
            
            # Get path from tree iter
            path = self.tree_store.get_path(item_data.tree_iter)
            if path:
                selection = self.tree_view.get_selection()
                selection.select_path(path)
                
                # Scroll to item
                self.tree_view.scroll_to_cell(path, None, False, 0.0, 0.0)
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Select item failed: {e}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get tree view statistics.
        
        Returns:
            Dictionary with statistics
        """
        try:
            stats = self._stats.copy()
            stats.update(self.registry.get_statistics())
            stats.update({
                'expanded_paths': len(self._expanded_paths),
                'has_clipboard_content': self.has_clipboard_content(),
                'focus_state': self._focus_state,
                'platform': self.platform_info.platform_type.value
            })
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {'error': str(e)}
    
    def cleanup(self) -> None:
        """Perform cleanup of tree view resources."""
        try:
            self.logger.debug("Starting tree view cleanup")
            
            # Clear clipboard
            self._clipboard_item = None
            self._clipboard_is_cut = False
            
            # Clear registry
            self.registry.clear_all()
            
            # Clear expanded paths
            self._expanded_paths.clear()
            
            # Reset context
            self.current_session_context = None
            self.current_folder_context = None
            
            self.logger.info("Tree view cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Tree view cleanup failed: {e}")