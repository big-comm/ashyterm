from typing import Optional, Union
import os
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from .settings.manager import SettingsManager
from .settings.config import APP_TITLE, VTE_AVAILABLE
from .sessions.models import SessionItem, SessionFolder
from .sessions.storage import load_sessions_to_store, load_folders_to_store
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .sessions.tree import SessionTreeView
from .ui.dialogs import SessionEditDialog, FolderEditDialog, PreferencesDialog
from .ui.menus import MainApplicationMenu

# Import new utility systems
from .utils.logger import get_logger, log_terminal_event, log_session_event
from .utils.exceptions import (
    AshyTerminalError, VTENotAvailableError, UIError, DialogError,
    handle_exception, ErrorCategory, ErrorSeverity
)
from .utils.security import validate_session_data, create_security_auditor
from .utils.backup import BackupType
from .utils.platform import get_platform_info, is_windows


class CommTerminalWindow(Adw.ApplicationWindow):
    """Main application window with enhanced functionality."""
    
    def __init__(self, application, settings_manager: SettingsManager):
        """
        Initialize the main window.
        
        Args:
            application: Gtk.Application instance
            settings_manager: SettingsManager instance
        """
        super().__init__(application=application)
        
        # Initialize logging
        self.logger = get_logger('ashyterm.window')
        self.logger.info("Initializing main window")
        
        # Core components
        self.settings_manager = settings_manager
        self.is_main_window = True
        self.platform_info = get_platform_info()
        
        # Window configuration
        self.set_default_size(1200, 700)
        self.set_title(APP_TITLE)
        
        # Data stores
        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)
        
        # Component managers
        self.terminal_manager = TerminalManager(self, self.settings_manager)
        self.tab_manager = TabManager(self.terminal_manager)
        self.session_tree = SessionTreeView(self, self.session_store, self.folder_store, self.settings_manager)
        
        # Thread safety
        self._ui_lock = threading.Lock()
        
        # Focus state management
        self._sidebar_has_focus = False
        self._creating_tab = False
        self._last_tab_creation = 0
        self._cleanup_performed = False
        
        # Clipboard for copy/paste operations
        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut = False
        
        # Context tracking for menus
        self.current_session_context: Optional[SessionItem] = None
        self.current_folder_context: Optional[SessionFolder] = None
        
        # Security auditor
        self.security_auditor = None
        
        # Initialize the window
        self._initialize_window()

    def _initialize_window(self) -> None:
        """Initialize window components safely."""
        try:
            self.logger.debug("Starting window initialization")
            
            # Check VTE availability first
            if not VTE_AVAILABLE:
                self.logger.critical("VTE not available")
                raise VTENotAvailableError()
            
            # Initialize security auditor
            try:
                self.security_auditor = create_security_auditor()
                self.logger.debug("Security auditor initialized")
            except Exception as e:
                self.logger.warning(f"Security auditor initialization failed: {e}")
            
            # Initialize component managers
            self.terminal_manager = TerminalManager(self, self.settings_manager)
            self.tab_manager = TabManager(self.terminal_manager)
            self.session_tree = SessionTreeView(self, self.session_store, self.folder_store, self.settings_manager)
            
            self.logger.debug("Component managers initialized")
            
            # Set up the UI
            self._setup_actions()
            self._setup_ui()
            self._setup_callbacks()
            self._load_initial_data()
            self._setup_window_events()
            
            # Create initial tab with delay to ensure UI is ready
            GLib.idle_add(self._create_initial_tab_safe)
            
            self.logger.info("Main window initialization completed")
            
        except Exception as e:
            self.logger.critical(f"Window initialization failed: {e}")
            handle_exception(e, "window initialization", "ashyterm.window", reraise=True)
    
    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab with proper error handling."""
        try:
            if self.tab_manager.get_tab_count() == 0:
                self.logger.debug("Creating initial tab")
                result = self.tab_manager.create_initial_tab_if_empty()
                if result is None:
                    self.logger.warning("Failed to create initial tab")
                    self._show_error_dialog("Terminal Error", 
                                          "Failed to create initial terminal. Check system configuration.")
            return False  # Don't repeat
        except Exception as e:
            self.logger.error(f"Failed to create initial tab: {e}")
            self._show_error_dialog("Initialization Error", 
                                  f"Failed to initialize terminal: {e}")
            return False
    
    def _setup_actions(self) -> None:
        """Set up window-level actions."""
        try:
            actions = [
                # Terminal actions
                ("new-local-tab", self._on_new_local_tab),
                ("close-tab", self._on_close_tab),
                ("copy", self._on_copy),
                ("paste", self._on_paste),
                ("select-all", self._on_select_all),
                
                # Session actions
                ("edit-session", self._on_edit_session),
                ("duplicate-session", self._on_duplicate_session),
                ("rename-session", self._on_rename_session),
                ("move-session-to-folder", self._on_move_session_to_folder),
                ("delete-session", self._on_delete_session),
                
                # Folder actions
                ("edit-folder", self._on_edit_folder),
                ("rename-folder", self._on_rename_folder),
                ("add-session-to-folder", self._on_add_session_to_folder),
                ("delete-folder", self._on_delete_folder),
                
                # Clipboard actions
                ("cut-item", self._on_cut_item),
                ("copy-item", self._on_copy_item),
                ("paste-item", self._on_paste_item),
                ("paste-item-root", self._on_paste_item_root),
                
                # Root actions
                ("add-session-root", self._on_add_session_root),
                ("add-folder-root", self._on_add_folder_root),
                
                # Preferences and utilities
                ("preferences", self._on_preferences),
                ("audit-security", self._on_audit_security),
            ]
            
            for action_name, callback in actions:
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", callback)
                self.add_action(action)
            
            self.logger.debug("Window actions configured")
            
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")
            raise UIError("window", f"action setup failed: {e}")
    
    def _setup_ui(self) -> None:
        """Set up the user interface."""
        try:
            self.logger.debug("Setting up UI components")
            
            # Main vertical box
            main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            
            # Header bar
            header_bar = self._create_header_bar()
            main_box.append(header_bar)
            
            # Horizontal paned layout
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            paned.set_position(220)
            paned.set_resize_start_child(False)
            paned.set_shrink_start_child(False)
            
            # Sidebar
            self.sidebar_box = self._create_sidebar()
            paned.set_start_child(self.sidebar_box)
            
            # Content area (tabs)
            content_box = self._create_content_area()
            paned.set_end_child(content_box)
            
            main_box.append(paned)
            self.set_content(main_box)
            
            # Set initial sidebar visibility
            initial_visible = self.settings_manager.get_sidebar_visible()
            self.sidebar_box.set_visible(initial_visible)
            self.toggle_sidebar_button.set_active(initial_visible)
            self._update_sidebar_button_icon()
            
            self.logger.debug("UI setup completed")
            
        except Exception as e:
            self.logger.error(f"UI setup failed: {e}")
            raise UIError("window", f"UI setup failed: {e}")
    
    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create the header bar with controls."""
        try:
            header_bar = Adw.HeaderBar()
            
            # Sidebar toggle button
            self.toggle_sidebar_button = Gtk.ToggleButton()
            self.toggle_sidebar_button.set_icon_name("view-reveal-symbolic")
            self.toggle_sidebar_button.set_tooltip_text("Toggle Sidebar")
            self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
            header_bar.pack_start(self.toggle_sidebar_button)
            
            # Security audit button (if security auditor available)
            if self.security_auditor:
                audit_button = Gtk.Button.new_from_icon_name("security-high-symbolic")
                audit_button.set_tooltip_text("Security Audit")
                audit_button.set_action_name("win.audit-security")
                header_bar.pack_start(audit_button)
            
            # Preferences button
            preferences_button = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
            preferences_button.set_tooltip_text("Preferences")
            preferences_button.set_action_name("win.preferences")
            header_bar.pack_end(preferences_button)
            
            # Main menu button
            menu_button = Gtk.MenuButton()
            menu_button.set_icon_name("open-menu-symbolic")
            menu_button.set_tooltip_text("Main Menu")
            menu_button.set_menu_model(MainApplicationMenu.create_menu())
            header_bar.pack_end(menu_button)
            
            self.logger.debug("Header bar created")
            return header_bar
            
        except Exception as e:
            self.logger.error(f"Header bar creation failed: {e}")
            raise UIError("header_bar", f"creation failed: {e}")
    
    def _create_sidebar(self) -> Gtk.Box:
        """Create the sidebar with session tree."""
        try:
            sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            
            # Session tree in scrolled window
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_vexpand(True)
            scrolled_window.set_child(self.session_tree.get_widget())
            sidebar_box.append(scrolled_window)
            
            # Toolbar with action buttons
            toolbar = self._create_sidebar_toolbar()
            sidebar_box.append(toolbar)
            
            self.logger.debug("Sidebar created")
            return sidebar_box
            
        except Exception as e:
            self.logger.error(f"Sidebar creation failed: {e}")
            raise UIError("sidebar", f"creation failed: {e}")
    
    def _create_sidebar_toolbar(self) -> Gtk.Box:
        """Create the sidebar toolbar with action buttons."""
        try:
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            toolbar.add_css_class("toolbar")
            
            # Add session button
            add_session_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
            add_session_button.set_tooltip_text("Add Session")
            add_session_button.connect("clicked", self._on_add_session_clicked)
            toolbar.append(add_session_button)
            
            # Add folder button
            add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
            add_folder_button.set_tooltip_text("Add Folder")
            add_folder_button.connect("clicked", self._on_add_folder_clicked)
            toolbar.append(add_folder_button)
            
            # Edit button
            edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit_button.set_tooltip_text("Edit Selected")
            edit_button.connect("clicked", self._on_edit_selected_clicked)
            toolbar.append(edit_button)
            
            # Remove button
            remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic")
            remove_button.set_tooltip_text("Remove Selected")
            remove_button.connect("clicked", self._on_remove_selected_clicked)
            toolbar.append(remove_button)
            
            self.logger.debug("Sidebar toolbar created")
            return toolbar
            
        except Exception as e:
            self.logger.error(f"Sidebar toolbar creation failed: {e}")
            raise UIError("sidebar_toolbar", f"creation failed: {e}")
    
    def _create_content_area(self) -> Gtk.Box:
        """Create the content area with tab view."""
        try:
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content_box.append(self.tab_manager.get_tab_bar())
            content_box.append(self.tab_manager.get_tab_view())
            
            self.logger.debug("Content area created")
            return content_box
            
        except Exception as e:
            self.logger.error(f"Content area creation failed: {e}")
            raise UIError("content_area", f"creation failed: {e}")
    
    def _setup_callbacks(self) -> None:
        """Set up callbacks between components."""
        try:
            # Session tree callbacks
            self.session_tree.on_session_activated = self._on_session_activated
            self.session_tree.on_focus_changed = self._on_tree_focus_changed
            
            # Terminal manager callbacks
            self.terminal_manager.on_terminal_child_exited = self._on_terminal_child_exited
            self.terminal_manager.on_terminal_eof = self._on_terminal_eof
            self.terminal_manager.on_terminal_focus_changed = self._on_terminal_focus_changed
            self.terminal_manager.on_terminal_should_close = self._on_terminal_should_close
            
            # Tab manager callbacks
            self.tab_manager.on_tab_selected = self._on_tab_selected
            self.tab_manager.on_tab_closed = self._on_tab_closed
            
            self.logger.debug("Callbacks configured")
            
        except Exception as e:
            self.logger.error(f"Callback setup failed: {e}")
    
    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        try:
            self.connect("close-request", self._on_window_close_request)
            
            # Set up keyboard handling for focus management
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_window_key_pressed)
            self.add_controller(key_controller)
            
            self.logger.debug("Window events configured")
            
        except Exception as e:
            self.logger.error(f"Window event setup failed: {e}")
    
    def _load_initial_data(self) -> None:
        """Load initial sessions and folders data."""
        try:
            self.logger.debug("Loading initial data")
            
            load_sessions_to_store(self.session_store)
            load_folders_to_store(self.folder_store)
            self.session_tree.refresh_tree()
            
            session_count = self.session_store.get_n_items()
            folder_count = self.folder_store.get_n_items()
            
            self.logger.info(f"Loaded {session_count} sessions and {folder_count} folders")
            
        except Exception as e:
            self.logger.error(f"Failed to load initial data: {e}")
            self._show_error_dialog("Data Loading Error", 
                                  "Failed to load saved sessions and folders. Starting with empty configuration.")
    
    def _update_sidebar_button_icon(self) -> None:
        """Update sidebar toggle button icon."""
        try:
            is_visible = self.sidebar_box.get_visible()
            icon_name = "view-reveal-symbolic" if is_visible else "view-conceal-symbolic"
            self.toggle_sidebar_button.set_icon_name(icon_name)
        except Exception as e:
            self.logger.error(f"Failed to update sidebar button icon: {e}")
    
    # Event handlers
    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Handle sidebar toggle button."""
        try:
            is_visible = button.get_active()
            self.sidebar_box.set_visible(is_visible)
            self.settings_manager.set_sidebar_visible(is_visible)
            self._update_sidebar_button_icon()
            
            self.logger.debug(f"Sidebar visibility changed: {is_visible}")
            
        except Exception as e:
            self.logger.error(f"Sidebar toggle failed: {e}")
    
    def _on_window_key_pressed(self, controller, keyval, keycode, state) -> bool:
        """Handle window-level key presses for focus management."""
        try:
            # Block terminal input if sidebar has focus
            if self._sidebar_has_focus:
                return Gdk.EVENT_PROPAGATE
            
            return Gdk.EVENT_PROPAGATE
        except Exception as e:
            self.logger.error(f"Window key press handling failed: {e}")
            return Gdk.EVENT_PROPAGATE
    
    def _on_tree_focus_changed(self, has_focus: bool) -> None:
        """Handle tree view focus change."""
        try:
            self._sidebar_has_focus = has_focus
            self.logger.debug(f"Sidebar focus changed: {has_focus}")
        except Exception as e:
            self.logger.error(f"Tree focus change handling failed: {e}")

    def _on_terminal_should_close(self, terminal, child_status: int, identifier) -> bool:
        """Handle request to close terminal tab after process exit."""
        try:
            if child_status != 0:
                return False
            
            page = self.tab_manager.get_page_for_terminal(terminal)
            if not page:
                return False

            if self.tab_manager.get_tab_count() <= 1:
                self.logger.info("Last terminal closed. Closing the application window.")
                self.close()
                return True

            self.logger.debug(f"Auto-closing tab for terminal with status {child_status}")
            return self.tab_manager.close_tab(page)
            
        except Exception as e:
            self.logger.error(f"Terminal close handling failed: {e}")
            return False
    
    def _on_terminal_focus_changed(self, terminal, from_sidebar: bool) -> None:
        """Handle terminal focus change."""
        try:
            if not from_sidebar:
                self._sidebar_has_focus = False
        except Exception as e:
            self.logger.error(f"Terminal focus change handling failed: {e}")
    
    def _on_window_close_request(self, window) -> bool:
        """Handle window close request."""
        try:
            self.logger.info("Window close request received")
            
            # Perform cleanup
            self._perform_cleanup()
            
            self.logger.info("Window cleanup completed")
            return Gdk.EVENT_PROPAGATE
            
        except Exception as e:
            self.logger.error(f"Window close handling failed: {e}")
            return Gdk.EVENT_PROPAGATE
    
    def _perform_cleanup(self) -> None:
        """Fast cleanup without hanging."""
        if self._cleanup_performed:
            return
        
        self._cleanup_performed = True
        
        try:
            self.logger.debug("Fast window cleanup - no complex operations")
            
            # Just clear references - don't do complex cleanup
            self.current_session_context = None
            self.current_folder_context = None
            self._clipboard_item = None
            
            self.logger.debug("Fast cleanup completed")
            
        except Exception as e:
            # Don't let cleanup errors prevent shutdown
            pass
    
    # Session tree event handlers
    def _on_session_activated(self, session: SessionItem) -> None:
        """Handle session activation from tree."""
        try:
            if not VTE_AVAILABLE:
                raise VTENotAvailableError()
            
            # Validate session before activation
            if self.security_auditor:
                try:
                    session_data = session.to_dict()
                    is_valid, errors = validate_session_data(session_data)
                    
                    if not is_valid:
                        error_msg = "Session validation failed:\n" + "\n".join(errors)
                        self._show_error_dialog("Session Validation Error", error_msg)
                        return
                    
                    # Perform security audit
                    findings = self.security_auditor.audit_ssh_session(session_data)
                    high_severity_findings = [f for f in findings if f['severity'] in ['high', 'critical']]
                    
                    if high_severity_findings:
                        self.logger.warning(f"High severity security findings for session {session.name}")
                        for finding in high_severity_findings:
                            self.logger.warning(f"Security: {finding['message']}")
                    
                except Exception as e:
                    self.logger.warning(f"Session security validation failed: {e}")
            
            # Create new tab for session
            if session.is_local():
                result = self.tab_manager.create_local_tab(session.name)
                log_terminal_event("created", session.name, "local terminal")
            else:
                result = self.tab_manager.create_ssh_tab(session)
                log_terminal_event("created", session.name, f"SSH to {session.get_connection_string()}")
            
            if result is None:
                self._show_error_dialog("Terminal Creation Failed", 
                                      "Could not create terminal for this session.")
            
        except VTENotAvailableError:
            self._show_error_dialog("VTE Not Available", 
                                  "Cannot open session - VTE library not installed.")
        except Exception as e:
            self.logger.error(f"Session activation failed: {e}")
            self._show_error_dialog("Session Error", f"Failed to activate session: {e}")
    
    def _on_terminal_child_exited(self, terminal, child_status: int, identifier) -> None:
        """Handle terminal child process exit."""
        try:
            terminal_name = identifier if isinstance(identifier, str) else getattr(identifier, 'name', 'Unknown')
            log_terminal_event("exited", terminal_name, f"status {child_status}")
        except Exception as e:
            self.logger.error(f"Terminal exit handling failed: {e}")
    
    def _on_terminal_eof(self, terminal, identifier) -> None:
        """Handle terminal EOF."""
        try:
            terminal_name = identifier if isinstance(identifier, str) else getattr(identifier, 'name', 'Unknown')
            log_terminal_event("eof", terminal_name)
        except Exception as e:
            self.logger.error(f"Terminal EOF handling failed: {e}")
    
    def _on_tab_selected(self, page) -> None:
        """Handle tab selection change."""
        # Tab manager already handles focus
        pass
    
    def _on_tab_closed(self, page, terminal) -> None:
        """Handle tab being closed."""
        # Cleanup already handled by tab manager
        pass
    
    # Action handlers - Terminal actions
    def _on_new_local_tab(self, action, param) -> None:
        """Handle new local tab action with rate limiting."""
        if self._creating_tab:
            self.logger.debug("Tab creation already in progress, ignoring")
            return
        
        # Rate limiting
        current_time = time.time()
        if current_time - self._last_tab_creation < 0.5:  # 500ms rate limit
            self.logger.debug("Tab creation rate limited")
            return
        
        try:
            self._creating_tab = True
            self._last_tab_creation = current_time
            
            if not VTE_AVAILABLE:
                raise VTENotAvailableError()
            
            result = self.tab_manager.create_local_tab()
            if result is None:
                raise AshyTerminalError("Terminal creation failed", 
                                      category=ErrorCategory.TERMINAL,
                                      severity=ErrorSeverity.HIGH)
            
            log_terminal_event("created", "Local Terminal", "new tab")
                
        except VTENotAvailableError:
            self._show_error_dialog("VTE Not Available", 
                                  "Cannot create terminal - VTE library not installed.")
        except Exception as e:
            self.logger.error(f"New local tab creation failed: {e}")
            self._show_error_dialog("Terminal Error", f"Failed to create new tab: {e}")
        finally:
            # Reset flag after delay
            def reset_flag():
                self._creating_tab = False
                return False
            GLib.timeout_add(200, reset_flag)
    
    def _on_close_tab(self, action, param) -> None:
        """Handle close tab action."""
        try:
            success = self.tab_manager.close_tab()
            if success:
                log_terminal_event("closed", "Tab", "user action")
        except Exception as e:
            self.logger.error(f"Tab close failed: {e}")
    
    def _on_copy(self, action, param) -> None:
        """Handle copy action."""
        try:
            success = self.tab_manager.copy_from_current_terminal()
            if not success:
                self.logger.debug("Copy operation failed - no selection or terminal not available")
        except Exception as e:
            self.logger.error(f"Copy operation failed: {e}")
    
    def _on_paste(self, action, param) -> None:
        """Handle paste action."""
        try:
            success = self.tab_manager.paste_to_current_terminal()
            if not success:
                self.logger.debug("Paste operation failed - no clipboard content or terminal not available")
        except Exception as e:
            self.logger.error(f"Paste operation failed: {e}")
    
    def _on_select_all(self, action, param) -> None:
        """Handle select all action."""
        try:
            self.tab_manager.select_all_in_current_terminal()
        except Exception as e:
            self.logger.error(f"Select all operation failed: {e}")
    
    # Action handlers - Session actions
    def _on_edit_session(self, action, param) -> None:
        """Handle edit session action."""
        try:
            if self.current_session_context:
                self._show_session_edit_dialog(self.current_session_context, False)
        except Exception as e:
            self.logger.error(f"Edit session failed: {e}")
            self._show_error_dialog("Edit Error", f"Failed to edit session: {e}")
    
    def _on_duplicate_session(self, action, param) -> None:
        """Handle duplicate session action."""
        try:
            if self.current_session_context:
                duplicated = self.session_tree.operations.duplicate_session(self.current_session_context)
                if duplicated:
                    self.session_tree.refresh_tree()
                    log_session_event("duplicated", self.current_session_context.name)
        except Exception as e:
            self.logger.error(f"Duplicate session failed: {e}")
            self._show_error_dialog("Duplicate Error", f"Failed to duplicate session: {e}")
    
    def _on_rename_session(self, action, param) -> None:
        """Handle rename session action."""
        try:
            if self.current_session_context:
                self._show_rename_dialog(self.current_session_context, True)
        except Exception as e:
            self.logger.error(f"Rename session failed: {e}")
            self._show_error_dialog("Rename Error", f"Failed to rename session: {e}")
    
    def _on_move_session_to_folder(self, action, param) -> None:
        """Handle move session to folder action."""
        try:
            if self.current_session_context:
                self._show_move_session_dialog(self.current_session_context)
        except Exception as e:
            self.logger.error(f"Move session failed: {e}")
            self._show_error_dialog("Move Error", f"Failed to move session: {e}")
    
    def _on_delete_session(self, action, param) -> None:
        """Handle delete session action."""
        try:
            if self.current_session_context:
                self._show_delete_confirmation(self.current_session_context, True)
        except Exception as e:
            self.logger.error(f"Delete session failed: {e}")
            self._show_error_dialog("Delete Error", f"Failed to delete session: {e}")
    
    # Action handlers - Folder actions
    def _on_edit_folder(self, action, param) -> None:
        """Handle edit folder action."""
        try:
            if self.current_folder_context:
                self._show_folder_edit_dialog(self.current_folder_context, False)
        except Exception as e:
            self.logger.error(f"Edit folder failed: {e}")
            self._show_error_dialog("Edit Error", f"Failed to edit folder: {e}")
    
    def _on_rename_folder(self, action, param) -> None:
        """Handle rename folder action."""
        try:
            if self.current_folder_context:
                self._show_rename_dialog(self.current_folder_context, False)
        except Exception as e:
            self.logger.error(f"Rename folder failed: {e}")
            self._show_error_dialog("Rename Error", f"Failed to rename folder: {e}")
    
    def _on_add_session_to_folder(self, action, param) -> None:
        """Handle add session to folder action."""
        try:
            if self.current_folder_context:
                new_session = SessionItem(name="New Session", folder_path=self.current_folder_context.path)
                self._show_session_edit_dialog(new_session, True)
        except Exception as e:
            self.logger.error(f"Add session to folder failed: {e}")
            self._show_error_dialog("Add Error", f"Failed to add session to folder: {e}")
    
    def _on_delete_folder(self, action, param) -> None:
        """Handle delete folder action."""
        try:
            if self.current_folder_context:
                self._show_delete_confirmation(self.current_folder_context, False)
        except Exception as e:
            self.logger.error(f"Delete folder failed: {e}")
            self._show_error_dialog("Delete Error", f"Failed to delete folder: {e}")
    
    # Action handlers - Clipboard actions
    def _on_cut_item(self, action, param) -> None:
        """Handle cut item action."""
        try:
            item = self.current_session_context or self.current_folder_context
            if item:
                self.session_tree.cut_item(item)
                self._clipboard_item = item
                self._clipboard_is_cut = True
        except Exception as e:
            self.logger.error(f"Cut item failed: {e}")
    
    def _on_copy_item(self, action, param) -> None:
        """Handle copy item action."""
        try:
            item = self.current_session_context or self.current_folder_context
            if item:
                self.session_tree.copy_item(item)
                self._clipboard_item = item
                self._clipboard_is_cut = False
        except Exception as e:
            self.logger.error(f"Copy item failed: {e}")
    
    def _on_paste_item(self, action, param) -> None:
        """Handle paste item action."""
        try:
            if self.current_folder_context:
                self.session_tree.paste_item(self.current_folder_context.path)
        except Exception as e:
            self.logger.error(f"Paste item failed: {e}")
    
    def _on_paste_item_root(self, action, param) -> None:
        """Handle paste item to root action."""
        try:
            self.session_tree.paste_item("")
        except Exception as e:
            self.logger.error(f"Paste item to root failed: {e}")
    
    # Action handlers - Root actions
    def _on_add_session_root(self, action, param) -> None:
        """Handle add session to root action."""
        self._on_add_session_clicked(None)
    
    def _on_add_folder_root(self, action, param) -> None:
        """Handle add folder to root action."""
        self._on_add_folder_clicked(None)
    
    def _on_preferences(self, action, param) -> None:
        """Handle preferences action."""
        try:
            dialog = PreferencesDialog(self, self.settings_manager)
            
            # Connect signals for live updates
            dialog.connect("color-scheme-changed", lambda d, i: self.terminal_manager.update_all_terminals())
            dialog.connect("transparency-changed", lambda d, v: self.terminal_manager.update_all_terminals())
            dialog.connect("font-changed", lambda d, f: self.terminal_manager.update_all_terminals())
            dialog.connect("shortcut-changed", lambda d: self._update_keyboard_shortcuts())
            
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Preferences dialog failed: {e}")
            self._show_error_dialog("Preferences Error", f"Failed to open preferences: {e}")
    
    def _on_audit_security(self, action, param) -> None:
        """Handle security audit action."""
        try:
            if not self.security_auditor:
                self._show_error_dialog("Security Audit", "Security auditor not available")
                return
            
            # Audit all sessions
            findings_count = 0
            sessions_audited = 0
            
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem):
                    sessions_audited += 1
                    findings = self.security_auditor.audit_ssh_session(session.to_dict())
                    
                    for finding in findings:
                        if finding['severity'] in ['medium', 'high', 'critical']:
                            findings_count += 1
                            self.logger.warning(f"Security audit - {session.name}: {finding['message']}")
            
            # Show summary
            if findings_count == 0:
                message = f"Security audit completed. {sessions_audited} sessions audited. No significant issues found."
            else:
                message = f"Security audit completed. {sessions_audited} sessions audited. {findings_count} issues found. Check logs for details."
            
            self._show_info_dialog("Security Audit Complete", message)
            
        except Exception as e:
            self.logger.error(f"Security audit failed: {e}")
            self._show_error_dialog("Security Audit Error", f"Security audit failed: {e}")
    
    # Button handlers
    def _on_add_session_clicked(self, button) -> None:
        """Handle add session button click."""
        try:
            new_session = SessionItem(name="New Session")
            self._show_session_edit_dialog(new_session, True)
        except Exception as e:
            self.logger.error(f"Add session button failed: {e}")
            self._show_error_dialog("Add Session Error", f"Failed to add session: {e}")
    
    def _on_add_folder_clicked(self, button) -> None:
        """Handle add folder button click."""
        try:
            # Crie uma nova instância de SessionFolder com um nome padrão
            new_folder = SessionFolder(name="New Folder")
            self._show_folder_edit_dialog(new_folder, True)
        except Exception as e:
            self.logger.error(f"Add folder button failed: {e}")
            self._show_error_dialog("Add Folder Error", f"Failed to add folder: {e}")
    
    def _on_edit_selected_clicked(self, button) -> None:
        """Handle edit selected button click."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self.current_session_context = selected_item
                self._show_session_edit_dialog(selected_item, False)
            elif isinstance(selected_item, SessionFolder):
                self.current_folder_context = selected_item
                self._show_folder_edit_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Edit selected button failed: {e}")
            self._show_error_dialog("Edit Error", f"Failed to edit selected item: {e}")
    
    def _on_remove_selected_clicked(self, button) -> None:
        """Handle remove selected button click."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self.current_session_context = selected_item
                self._show_delete_confirmation(selected_item, True)
            elif isinstance(selected_item, SessionFolder):
                self.current_folder_context = selected_item
                self._show_delete_confirmation(selected_item, False)
        except Exception as e:
            self.logger.error(f"Remove selected button failed: {e}")
            self._show_error_dialog("Remove Error", f"Failed to remove selected item: {e}")
    
    # Helper methods for dialogs
    def _show_session_edit_dialog(self, session: SessionItem, is_new: bool) -> None:
        """Show session edit dialog."""
        try:
            position = -1 if is_new else self.session_tree._find_item_position(session)
            dialog = SessionEditDialog(self, session, self.session_store, position, self.folder_store)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Session edit dialog failed: {e}")
            raise DialogError("session_edit", str(e))
    
    def _show_folder_edit_dialog(self, folder: Optional[SessionFolder], is_new: bool) -> None:
        """Show folder edit dialog."""
        try:
            position = None if is_new else self.session_tree._find_item_position(folder)
            # Passe o flag 'is_new' para o construtor do diálogo
            dialog = FolderEditDialog(self, self.folder_store, folder, position, is_new=is_new)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Folder edit dialog failed: {e}")
            raise DialogError("folder_edit", str(e))
    
    def _show_rename_dialog(self, item: Union[SessionItem, SessionFolder], is_session: bool) -> None:
        """Show rename dialog."""
        try:
            item_type = "Session" if is_session else "Folder"
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=f"Rename {item_type}",
                body=f"Enter new name for \"{item.name}\":"
            )
            
            entry = Gtk.Entry(text=item.name)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("rename", "Rename")
            dialog.set_default_response("rename")
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "rename":
                        new_name = entry.get_text().strip()
                        if new_name and new_name != item.name:
                            old_name = item.name
                            item.name = new_name
                            
                            if is_session:
                                self.session_tree.operations._save_changes()
                                log_session_event("renamed", f"{old_name} -> {new_name}")
                            else:
                                # Handle folder path updates
                                if isinstance(item, SessionFolder):
                                    old_path = item.path
                                    item.path = os.path.normpath(
                                        f"{item.parent_path}/{new_name}" if item.parent_path else f"/{new_name}"
                                    )
                                    self.session_tree.operations._update_child_paths(old_path, item.path)
                                self.session_tree.operations._save_changes()
                            
                            self.session_tree.refresh_tree()
                    dlg.close()
                except Exception as e:
                    self.logger.error(f"Rename dialog response failed: {e}")
                    dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Rename dialog failed: {e}")
            raise DialogError("rename", str(e))
    
    def _show_move_session_dialog(self, session: SessionItem) -> None:
        """Show move session to folder dialog."""
        # Implementation placeholder - would show folder selection dialog
        self._show_info_dialog("Move Session", "Move session functionality will be implemented in folder selection dialog.")
    
    def _show_delete_confirmation(self, item: Union[SessionItem, SessionFolder], is_session: bool) -> None:
        """Show delete confirmation dialog."""
        try:
            item_type = "Session" if is_session else "Folder"
            
            # Verifica se a pasta tem conteúdo antes de mostrar o diálogo de exclusão
            if not is_session and self.session_tree.operations._folder_has_children(item.path):
                body_text = (f"The folder \"{item.name}\" is not empty. "
                            f"Are you sure you want to permanently delete it and all its contents?")
            else:
                body_text = f"Are you sure you want to delete \"{item.name}\"?"

            dialog = Adw.MessageDialog(
                transient_for=self,
                title=f"Delete {item_type}",
                body=body_text
            )
            
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "delete":
                        result = None
                        if is_session:
                            result = self.session_tree.operations.remove_session(item)
                            if result.success:
                                log_session_event("deleted", item.name)
                        else:
                            # Se a pasta não estiver vazia, use force=True
                            force_delete = self.session_tree.operations._folder_has_children(item.path)
                            result = self.session_tree.operations.remove_folder(item, force=force_delete)
                        
                        if result and result.success:
                            self.session_tree.refresh_tree()
                        elif result:
                            self._show_error_dialog(f"Delete {item_type} Error", result.message)

                    dlg.close()
                except Exception as e:
                    self.logger.error(f"Delete confirmation response failed: {e}")
                    dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Delete confirmation dialog failed: {e}")
            raise DialogError("delete_confirmation", str(e))
    
    def _show_error_dialog(self, title: str, message: str) -> None:
        """Show an error dialog to the user."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=title,
                body=message
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(f"Error dialog failed: {e}")
            # Fallback to print
            print(f"ERROR: {title} - {message}")
    
    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show an info dialog to the user."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=title,
                body=message
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(f"Info dialog failed: {e}")
    
    def _update_keyboard_shortcuts(self) -> None:
        """Update keyboard shortcuts after settings change."""
        try:
            if self.get_application():
                self.get_application().refresh_keyboard_shortcuts()
        except Exception as e:
            self.logger.error(f"Keyboard shortcuts update failed: {e}")
    
    # Public methods
    def refresh_tree(self) -> None:
        """Refresh the session tree view."""
        try:
            self.session_tree.refresh_tree()
        except Exception as e:
            self.logger.error(f"Tree refresh failed: {e}")
    
    def get_tab_manager(self) -> TabManager:
        """Get the tab manager instance."""
        return self.tab_manager
    
    def get_terminal_manager(self) -> TerminalManager:
        """Get the terminal manager instance."""
        return self.terminal_manager
    
    def get_session_tree(self) -> SessionTreeView:
        """Get the session tree view instance."""
        return self.session_tree
    
    def destroy(self) -> None:
        """Override destroy to ensure cleanup."""
        try:
            self._perform_cleanup()
            super().destroy()
        except Exception as e:
            self.logger.error(f"Window destroy failed: {e}")