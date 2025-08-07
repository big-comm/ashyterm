# window.py

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
from .utils.translation_utils import _

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
        self._force_closing = False

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
            
            # Security auditor removed
            self.security_auditor = None
            
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
                    self._show_error_dialog(_("Terminal Error"), 
                                          _("Failed to create initial terminal. Check system configuration."))
            return False  # Don't repeat
        except Exception as e:
            self.logger.error(f"Failed to create initial tab: {e}")
            self._show_error_dialog(_("Initialization Error"), 
                                  _("Failed to initialize terminal: {error}").format(error=str(e)))
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
                
                # Zoom actions
                ("zoom-in", self._on_zoom_in),
                ("zoom-out", self._on_zoom_out),
                ("zoom-reset", self._on_zoom_reset),
                
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
                
                # Interface actions
                ("toggle-sidebar", self._on_toggle_sidebar_action),
                
                # Preferences and utilities
                ("preferences", self._on_preferences),
                ("shortcuts", self._on_shortcuts),
                ("new-window", self._on_new_window),
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
            self.toggle_sidebar_button.set_tooltip_text(_("Toggle Sidebar"))
            self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
            header_bar.pack_start(self.toggle_sidebar_button)
            
            # Main menu button
            menu_button = Gtk.MenuButton()
            menu_button.set_icon_name("open-menu-symbolic")
            menu_button.set_tooltip_text(_("Main Menu"))
            menu_button.set_menu_model(MainApplicationMenu.create_menu())
            header_bar.pack_end(menu_button)
            
            # New tab button
            new_tab_button = Gtk.Button.new_from_icon_name("tab-new-symbolic")
            new_tab_button.set_tooltip_text(_("New Tab"))
            new_tab_button.connect("clicked", self._on_new_tab_clicked)
            new_tab_button.add_css_class("flat")
            header_bar.pack_end(new_tab_button)
            
            self.logger.debug("Header bar created")
            return header_bar
            
        except Exception as e:
            self.logger.error(f"Header bar creation failed: {e}")
            raise UIError("header_bar", f"creation failed: {e}")

    def _create_sidebar(self) -> Gtk.Box:
        """Create the sidebar with session tree."""
        try:
            toolbar_view = Adw.ToolbarView()
            
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_vexpand(True)
            scrolled_window.set_child(self.session_tree.get_widget())
            
            toolbar_view.set_content(scrolled_window)
            
            toolbar = self._create_sidebar_toolbar()
            toolbar_view.add_bottom_bar(toolbar)
            
            # Add CSS classes for identification
            toolbar_view.add_css_class("ashy-sidebar")
            toolbar_view.add_css_class("sidebar-main")
            toolbar.add_css_class("ashy-toolbar")
            scrolled_window.add_css_class("ashy-sidebar-content")
            
            # Force CSS directly to prevent transparency issues
            sidebar_css_provider = Gtk.CssProvider()
            sidebar_css = """.ashy-sidebar { background-color: #2d2d2d; }
    .ashy-toolbar { background-color: #2d2d2d; }"""
            sidebar_css_provider.load_from_data(sidebar_css.encode('utf-8'))
            
            # Apply CSS directly to widgets
            toolbar_view.get_style_context().add_provider(sidebar_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            toolbar.get_style_context().add_provider(sidebar_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            
            self.logger.debug("Sidebar created using Adw.ToolbarView")
            return toolbar_view
            
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
            add_session_button.set_tooltip_text(_("Add Session"))
            add_session_button.connect("clicked", self._on_add_session_clicked)
            toolbar.append(add_session_button)
            
            # Add folder button
            add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
            add_folder_button.set_tooltip_text(_("Add Folder"))
            add_folder_button.connect("clicked", self._on_add_folder_clicked)
            toolbar.append(add_folder_button)
            
            # Edit button
            edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit_button.set_tooltip_text(_("Edit Selected"))
            edit_button.connect("clicked", self._on_edit_selected_clicked)
            toolbar.append(edit_button)
            
            # Remove button
            remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic")
            remove_button.set_tooltip_text(_("Remove Selected"))
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
            
            tab_view = self.tab_manager.get_tab_view()
            tab_view.add_css_class("transparent-tabview")
            content_box.append(tab_view)
            
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
            self._show_error_dialog(_("Data Loading Error"), 
                                  _("Failed to load saved sessions and folders. Starting with empty configuration."))
    
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
        """Handle window close request with SSH session confirmation."""
        try:
            self.logger.info(_("Window close request received"))
            
            # If already force closing, allow it
            if self._force_closing:
                self.logger.info(_("Force closing - allowing window to close"))
                return Gdk.EVENT_PROPAGATE
            
            # Check for active SSH sessions in this window
            has_ssh = self.terminal_manager.has_active_ssh_sessions()
            self.logger.debug(f"Has active SSH sessions: {has_ssh}")
            
            if has_ssh:
                self.logger.info(_("Showing SSH close confirmation dialog"))
                self._show_window_ssh_close_confirmation()
                return Gdk.EVENT_STOP  # Prevent default close
            
            # No SSH sessions, allow normal close
            self.logger.info(_("No SSH sessions, proceeding with normal close"))
            self._perform_cleanup()
            self.logger.info(_("Window cleanup completed"))
            return Gdk.EVENT_PROPAGATE
            
        except Exception as e:
            self.logger.error(_("Window close handling failed: {}").format(e))
            return Gdk.EVENT_PROPAGATE
        
    def _show_window_ssh_close_confirmation(self) -> None:
        """Show confirmation dialog for closing window with active SSH sessions."""
        try:
            ssh_sessions = self.terminal_manager.get_active_ssh_session_names()
            
            if not ssh_sessions:
                self.logger.warning("No SSH sessions found but confirmation was requested")
                self._perform_cleanup()
                self.close()
                return
            
            session_list = "\n".join([f"• {name}" for name in ssh_sessions])
            
            try:
                # Build the message in parts to avoid translation issues
                part1 = _("This window has active SSH connections:")
                part2 = _("Closing will disconnect these sessions.")
                part3 = _("Are you sure you want to close this window?")
                
                body_text = f"{part1}\n\n{session_list}\n\n{part2}\n\n{part3}"
                self.logger.debug(f"Final body text created successfully")
            except Exception as e:
                self.logger.error(f"Failed to format message: {e}")
                # Fallback to English without translation
                body_text = f"This window has active SSH connections:\n\n{session_list}\n\nClosing will disconnect these sessions.\n\nAre you sure you want to close this window?"
                self.logger.info("Using fallback English message")
            
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Close Window"),
                body=body_text
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close Window"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "close":
                        self.logger.info(_("User confirmed window close with active SSH sessions"))
                        self._force_closing = True  # Set flag to avoid loop
                        self._perform_cleanup()
                        self.close()
                    else:
                        self.logger.debug(_("User cancelled window close"))
                    dlg.close()
                except Exception as e:
                    self.logger.error(_("Window SSH close confirmation response failed: {}").format(e))
                    dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(_("Window SSH close confirmation dialog failed: {}").format(e))
            # Fallback to normal close if dialog fails
            self._perform_cleanup()
            self.close()
    
    def _perform_cleanup(self) -> None:
        """Fast cleanup without hanging."""
        if self._cleanup_performed:
            return
        
        self._cleanup_performed = True
        
        try:
            self.logger.debug("Fast window cleanup - no complex operations")
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
            
            # Basic session validation
            try:
                session_data = session.to_dict()
                is_valid, errors = validate_session_data(session_data)
                
                if not is_valid:
                    error_msg = _("Session validation failed:\n{errors}").format(errors="\n".join(errors))
                    self._show_error_dialog(_("Session Validation Error"), error_msg)
                    return
                    
            except Exception as e:
                self.logger.warning(f"Session validation failed: {e}")
            
            # Create new tab for session
            if session.is_local():
                result = self.tab_manager.create_local_tab(session.name)
                log_terminal_event("created", session.name, "local terminal")
            else:
                result = self.tab_manager.create_ssh_tab(session)
                log_terminal_event("created", session.name, f"SSH to {session.get_connection_string()}")
            
            if result is None:
                self._show_error_dialog(_("Terminal Creation Failed"), 
                                      _("Could not create terminal for this session."))
            
        except VTENotAvailableError:
            self._show_error_dialog(_("VTE Not Available"), 
                                  _("Cannot open session - VTE library not installed."))
        except Exception as e:
            self.logger.error(f"Session activation failed: {e}")
            self._show_error_dialog(_("Session Error"), _("Failed to activate session: {error}").format(error=str(e)))
    
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
            self._show_error_dialog(_("VTE Not Available"), 
                                  _("Cannot create terminal - VTE library not installed."))
        except Exception as e:
            self.logger.error(f"New local tab creation failed: {e}")
            self._show_error_dialog(_("Terminal Error"), _("Failed to create new tab: {error}").format(error=str(e)))
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
            
    def _on_zoom_in(self, action, param) -> None:
        """Handle zoom in action."""
        try:
            success = self.tab_manager.zoom_in_current_terminal()
            if not success:
                self.logger.debug("Zoom in failed - no terminal available")
        except Exception as e:
            self.logger.error(f"Zoom in action failed: {e}")

    def _on_zoom_out(self, action, param) -> None:
        """Handle zoom out action."""
        try:
            success = self.tab_manager.zoom_out_current_terminal()
            if not success:
                self.logger.debug("Zoom out failed - no terminal available")
        except Exception as e:
            self.logger.error(f"Zoom out action failed: {e}")

    def _on_zoom_reset(self, action, param) -> None:
        """Handle zoom reset action."""
        try:
            success = self.tab_manager.zoom_reset_current_terminal()
            if not success:
                self.logger.debug("Zoom reset failed - no terminal available")
        except Exception as e:
            self.logger.error(f"Zoom reset action failed: {e}")
    
    # Action handlers - Session actions
    def _on_edit_session(self, action, param) -> None:
        """Handle edit session action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_session_edit_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Edit session failed: {e}")
            self._show_error_dialog(_("Edit Error"), _("Failed to edit session: {error}").format(error=str(e)))
    
    def _on_duplicate_session(self, action, param) -> None:
        """Handle duplicate session action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                duplicated = self.session_tree.operations.duplicate_session(selected_item)
                if duplicated:
                    self.session_tree.refresh_tree()
                    log_session_event("duplicated", selected_item.name)
        except Exception as e:
            self.logger.error(f"Duplicate session failed: {e}")
            self._show_error_dialog(_("Duplicate Error"), _("Failed to duplicate session: {error}").format(error=str(e)))
    
    def _on_rename_session(self, action, param) -> None:
        """Handle rename session action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_rename_dialog(selected_item, True)
        except Exception as e:
            self.logger.error(f"Rename session failed: {e}")
            self._show_error_dialog(_("Rename Error"), _("Failed to rename session: {error}").format(error=str(e)))
    
    def _on_move_session_to_folder(self, action, param) -> None:
        """Handle move session to folder action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_move_session_dialog(selected_item)
        except Exception as e:
            self.logger.error(f"Move session failed: {e}")
            self._show_error_dialog(_("Move Error"), _("Failed to move session: {error}").format(error=str(e)))
    
    def _on_delete_session(self, action, param) -> None:
        """Handle delete session action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_delete_confirmation(selected_item, True)
        except Exception as e:
            self.logger.error(f"Delete session failed: {e}")
            self._show_error_dialog(_("Delete Error"), _("Failed to delete session: {error}").format(error=str(e)))
    
    # Action handlers - Folder actions
    def _on_edit_folder(self, action, param) -> None:
        """Handle edit folder action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                self._show_folder_edit_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Edit folder failed: {e}")
            self._show_error_dialog(_("Edit Error"), _("Failed to edit folder: {error}").format(error=str(e)))
    
    def _on_rename_folder(self, action, param) -> None:
        """Handle rename folder action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                self._show_rename_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Rename folder failed: {e}")
            self._show_error_dialog(_("Rename Error"), _("Failed to rename folder: {error}").format(error=str(e)))
    
    def _on_add_session_to_folder(self, action, param) -> None:
        """Handle add session to folder action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                new_session = SessionItem(name=_("New Session"), folder_path=selected_item.path)
                self._show_session_edit_dialog(new_session, True)
        except Exception as e:
            self.logger.error(f"Add session to folder failed: {e}")
            self._show_error_dialog(_("Add Error"), _("Failed to add session to folder: {error}").format(error=str(e)))
    
    def _on_delete_folder(self, action, param) -> None:
        """Handle delete folder action."""
        try:
            # --- CHANGED: Query the tree for the selected item ---
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                self._show_delete_confirmation(selected_item, False)
        except Exception as e:
            self.logger.error(f"Delete folder failed: {e}")
            self._show_error_dialog(_("Delete Error"), _("Failed to delete folder: {error}").format(error=str(e)))
    
    # Action handlers - Clipboard actions
    def _on_cut_item(self, action, param) -> None:
        """Handle cut item action."""
        try:
            # --- CHANGED: Logic moved to SessionTreeView ---
            self.session_tree._cut_selected_item_safe()
        except Exception as e:
            self.logger.error(f"Cut item failed: {e}")
    
    def _on_copy_item(self, action, param) -> None:
        """Handle copy item action."""
        try:
            # --- CHANGED: Logic moved to SessionTreeView ---
            self.session_tree._copy_selected_item_safe()
        except Exception as e:
            self.logger.error(f"Copy item failed: {e}")
    
    def _on_paste_item(self, action, param) -> None:
        """Handle paste item action."""
        try:
            # --- CHANGED: Logic moved to SessionTreeView ---
            selected_item = self.session_tree.get_selected_item()
            target_path = ""
            if isinstance(selected_item, SessionFolder):
                target_path = selected_item.path
            elif isinstance(selected_item, SessionItem):
                target_path = selected_item.folder_path
            
            self.session_tree._paste_item_safe(target_path)
        except Exception as e:
            self.logger.error(f"Paste item failed: {e}")
    
    def _on_paste_item_root(self, action, param) -> None:
        """Handle paste item to root action."""
        try:
            # --- CHANGED: Logic moved to SessionTreeView ---
            self.session_tree._paste_item_safe("")
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
            self._show_error_dialog(_("Preferences Error"), _("Failed to open preferences: {error}").format(error=str(e)))
    
    def _on_shortcuts(self, action, param) -> None:
        """Handle shortcuts action - show keyboard shortcuts window."""
        try:
            shortcuts_window = Gtk.ShortcutsWindow(
                transient_for=self,
                modal=True
            )
            
            # Create shortcuts section
            section = Gtk.ShortcutsSection(
                title=_("Keyboard Shortcuts"),
                section_name="shortcuts"
            )
            
            # Terminal shortcuts
            terminal_group = Gtk.ShortcutsGroup(
                title=_("Terminal")
            )
            
            terminal_shortcuts = [
                (_("New Tab"), "<Control>t"),
                (_("Close Tab"), "<Control>w"),
                (_("New Window"), "<Control>n"),
                (_("Copy"), "<Control><Shift>c"),
                (_("Paste"), "<Control><Shift>v"),
                (_("Select All"), "<Control><Shift>a"),
            ]
            
            for title, accel in terminal_shortcuts:
                shortcut = Gtk.ShortcutsShortcut(
                    title=title,
                    accelerator=accel
                )
                terminal_group.append(shortcut)

            # Application shortcuts
            app_group = Gtk.ShortcutsGroup(
                title=_("Application")
            )

            app_shortcuts = [
                (_("Preferences"), "<Control>comma"),
                (_("Toggle Sidebar"), "<Control><Shift>h"),
                (_("Quit"), "<Control>q"),
            ]

            for title, accel in app_shortcuts:
                shortcut = Gtk.ShortcutsShortcut(
                    title=title,
                    accelerator=accel
                )
                app_group.append(shortcut)
            
            section.append(terminal_group)
            section.append(app_group)
            shortcuts_window.add_section(section)
            
            shortcuts_window.present()
            
        except Exception as e:
            self.logger.error(f"Shortcuts window failed: {e}")
            self._show_error_dialog(_("Keyboard Shortcuts"), 
                                _("Failed to open shortcuts window: {error}").format(error=str(e)))

    def _on_new_window(self, action, param) -> None:
        """Handle new window action."""
        try:
            # Get the application and create new window
            app = self.get_application()
            if app and hasattr(app, 'create_new_window'):
                new_window = app.create_new_window()
                if new_window:
                    new_window.present()
                else:
                    self._show_error_dialog(_("Nova Janela"), 
                                        _("Falha ao criar nova janela"))
            else:
                self._show_error_dialog(_("Nova Janela"), 
                                    _("Não foi possível criar uma nova janela"))
            
        except Exception as e:
            self.logger.error(f"New window creation failed: {e}")
            self._show_error_dialog(_("Nova Janela"), 
                                _("Falha ao criar nova janela: {error}").format(error=str(e)))
            
    def _on_toggle_sidebar_action(self, action, param) -> None:
        """Handle toggle sidebar action via keyboard shortcut."""
        try:
            self.logger.info("DEBUG: Toggle sidebar action called!")
            
            # Toggle the button state which will trigger the visibility change
            current_state = self.toggle_sidebar_button.get_active()
            self.logger.info(f"DEBUG: Current sidebar state: {current_state}")
            
            self.toggle_sidebar_button.set_active(not current_state)
            
            self.logger.debug(f"Sidebar toggled via keyboard shortcut: {not current_state}")
            
        except Exception as e:
            self.logger.error(f"Toggle sidebar action failed: {e}")
    
    # Button handlers
    def _on_add_session_clicked(self, button) -> None:
        """Handle add session button click."""
        try:
            new_session = SessionItem(name=_("New Session"))
            self._show_session_edit_dialog(new_session, True)
        except Exception as e:
            self.logger.error(f"Add session button failed: {e}")
            self._show_error_dialog(_("Add Session Error"), _("Failed to add session: {error}").format(error=str(e)))
            
    def _on_new_tab_clicked(self, button) -> None:
        """Handle new tab button click in header."""
        try:
            if not VTE_AVAILABLE:
                self._show_error_dialog(_("VTE Not Available"), 
                                    _("Cannot create terminal - VTE library not installed."))
                return
            
            # Use the same logic as new local tab action
            result = self.tab_manager.create_local_tab()
            if result is None:
                self._show_error_dialog(_("Terminal Error"), 
                                    _("Failed to create new tab."))
                return
            
            log_terminal_event("created", "Local Terminal", "header button")
            self.logger.debug("New tab created from header button")
            
        except Exception as e:
            self.logger.error(f"New tab header button failed: {e}")
            self._show_error_dialog(_("Terminal Error"), 
                                _("Failed to create new tab: {error}").format(error=str(e)))
    
    def _on_add_folder_clicked(self, button) -> None:
        """Handle add folder button click."""
        try:
            # Crie uma nova instância de SessionFolder com um nome padrão
            new_folder = SessionFolder(name=_("New Folder"))
            self._show_folder_edit_dialog(new_folder, True)
        except Exception as e:
            self.logger.error(f"Add folder button failed: {e}")
            self._show_error_dialog(_("Add Folder Error"), _("Failed to add folder: {error}").format(error=str(e)))
    
    def _on_edit_selected_clicked(self, button) -> None:
        """Handle edit selected button click."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_session_edit_dialog(selected_item, False)
            elif isinstance(selected_item, SessionFolder):
                self._show_folder_edit_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Edit selected button failed: {e}")
            self._show_error_dialog(_("Edit Error"), _("Failed to edit selected item: {error}").format(error=str(e)))
    
    def _on_remove_selected_clicked(self, button) -> None:
        """Handle remove selected button click."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_delete_confirmation(selected_item, True)
            elif isinstance(selected_item, SessionFolder):
                self._show_delete_confirmation(selected_item, False)
        except Exception as e:
            self.logger.error(f"Remove selected button failed: {e}")
            self._show_error_dialog(_("Remove Error"), _("Failed to remove selected item: {error}").format(error=str(e)))
    
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
            item_type = _("Session") if is_session else _("Folder")
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Rename {type}").format(type=item_type),
                body=_("Enter new name for \"{name}\":").format(name=item.name)
            )
            
            entry = Gtk.Entry(text=item.name)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("rename", _("Rename"))
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
        self._show_info_dialog(_("Move Session"), _("Move session functionality will be implemented in folder selection dialog."))
    
    def _show_delete_confirmation(self, item: Union[SessionItem, SessionFolder], is_session: bool) -> None:
        """Show delete confirmation dialog."""
        try:
            item_type = _("Session") if is_session else _("Folder")
            
            # Verifica se a pasta tem conteúdo antes de mostrar o diálogo de exclusão
            if not is_session and self.session_tree.operations._folder_has_children(item.path):
                body_text = _("The folder \"{name}\" is not empty. Are you sure you want to permanently delete it and all its contents?").format(name=item.name)
            else:
                body_text = _("Are you sure you want to delete \"{name}\"?").format(name=item.name)

            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Delete {type}").format(type=item_type),
                body=body_text
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("delete", _("Delete"))
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
                            self._show_error_dialog(_("Delete {type} Error").format(type=item_type), result.message)

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
            dialog.add_response("ok", _("OK"))
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
            dialog.add_response("ok", _("OK"))
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