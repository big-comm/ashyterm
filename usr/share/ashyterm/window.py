from typing import Optional, Union, List
import os
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from .settings.manager import SettingsManager
from .settings.config import APP_TITLE, VTE_AVAILABLE
from .sessions.models import SessionItem, SessionFolder
from .sessions.storage import load_sessions_to_store, load_folders_to_store
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .sessions.tree import SessionTreeView
from .ui.dialogs import SessionEditDialog, FolderEditDialog, PreferencesDialog, MoveSessionDialog
from .ui.menus import MainApplicationMenu

# Import new utility systems
from .utils.logger import get_logger, log_terminal_event, log_session_event
from .utils.exceptions import (
    AshyTerminalError, VTENotAvailableError, UIError, DialogError,
    handle_exception, ErrorCategory, ErrorSeverity
)
from .utils.security import validate_session_data
from .utils.platform import get_platform_info
from .utils.translation_utils import _

class CommTerminalWindow(Adw.ApplicationWindow):
    """Main application window with enhanced functionality."""
    
    def __init__(self, application, settings_manager: SettingsManager, initial_working_directory: Optional[str] = None):
        """
        Initialize the main window.
        
        Args:
            application: Gtk.Application instance
            settings_manager: SettingsManager instance
            initial_working_directory: Optional initial working directory for terminals
        """
        super().__init__(application=application)
        
        # Initialize logging
        self.logger = get_logger('ashyterm.window')
        self.logger.info("Initializing main window")
        
        # Core components
        self.settings_manager = settings_manager
        self.is_main_window = True
        self.platform_info = get_platform_info()
        self.initial_working_directory = initial_working_directory
        
        if self.initial_working_directory:
            self.logger.info(f"Window initialized with working directory: {self.initial_working_directory}")
        
        # Window configuration
        self.set_default_size(1200, 700)
        self.set_title(APP_TITLE)
        self.set_icon_name("ashyterm")

        # Data stores
        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)
        
        # Component managers
        self.terminal_manager = TerminalManager(self, self.settings_manager)
        self.tab_manager = TabManager(self.terminal_manager)
        self.session_tree = SessionTreeView(self, self.session_store, self.folder_store, self.settings_manager)

        # Connect managers
        self.terminal_manager.set_tab_manager(self.tab_manager)

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

            # Component managers are already initialized in __init__
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
            handle_exception(
                e, "window initialization", "ashyterm.window", reraise=True
            )

    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab with proper error handling."""
        try:
            if self.tab_manager.get_tab_count() == 0:
                self.logger.debug("Creating initial tab")
                result = self.tab_manager.create_initial_tab_if_empty(
                    working_directory=self.initial_working_directory
                )
                if result is None:
                    self.logger.warning("Failed to create initial tab")
                    self._show_error_dialog(
                        _("Terminal Error"),
                        _(
                            "Failed to create initial terminal. Check system configuration."
                        ),
                    )
            return False  # Don't repeat
        except Exception as e:
            self.logger.error(f"Failed to create initial tab: {e}")
            self._show_error_dialog(
                _("Initialization Error"),
                _("Failed to initialize terminal: {error}").format(error=str(e)),
            )
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
                # Splitting actions
                ("split-horizontal", self._on_split_horizontal),
                ("split-vertical", self._on_split_vertical),
                ("close-pane", self._on_close_pane),
                # Open URL
                ("open-url", self._on_open_url),
                ("copy-url", self._on_copy_url),
                # Split navigation actions
                ("focus-pane-up", self._on_focus_pane_up),
                ("focus-pane-down", self._on_focus_pane_down),
                ("focus-pane-left", self._on_focus_pane_left),
                ("focus-pane-right", self._on_focus_pane_right),
                # Zoom actions
                ("zoom-in", self._on_zoom_in),
                ("zoom-out", self._on_zoom_out),
                ("zoom-reset", self._on_zoom_reset),
                # --- START OF MODIFICATION ---
                ("connect-sftp", self._on_connect_sftp),
                # --- END OF MODIFICATION ---
                # Session actions
                ("edit-session", self._on_edit_session),
                ("duplicate-session", self._on_duplicate_session),
                ("rename-session", self._on_rename_session),
                ("move-session-to-folder", self._on_move_session_to_folder),
                ("delete-session", self._on_delete_selected_items), # MODIFIED
                # Folder actions
                ("edit-folder", self._on_edit_folder),
                ("rename-folder", self._on_rename_folder),
                ("add-session-to-folder", self._on_add_session_to_folder),
                ("delete-folder", self._on_delete_selected_items), # MODIFIED
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

            # Adw.Flap for animated sidebar
            self.flap = Adw.Flap()
            self.flap.set_transition_type(Adw.FlapTransitionType.SLIDE)

            # Sidebar
            self.sidebar_box = self._create_sidebar()
            self.flap.set_flap(self.sidebar_box)

            # Content area (tabs)
            content_box = self._create_content_area()
            self.flap.set_content(content_box)

            main_box.append(self.flap)
            self.set_content(main_box)

            # Set initial sidebar visibility
            initial_visible = self.settings_manager.get_sidebar_visible()
            self.flap.set_reveal_flap(initial_visible)
            self.toggle_sidebar_button.set_active(initial_visible)
            self._update_sidebar_button_icon()

            self.logger.debug("UI setup completed with Adw.Flap")

        except Exception as e:
            self.logger.error(f"UI setup failed: {e}")
            raise UIError("window", f"UI setup failed: {e}")

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create the header bar with controls."""
        try:
            self.header_bar = Adw.HeaderBar()
            self.header_bar.add_css_class("main-header-bar")

            # Sidebar toggle button
            self.toggle_sidebar_button = Gtk.ToggleButton()
            self.toggle_sidebar_button.set_icon_name("view-reveal-symbolic")
            self.toggle_sidebar_button.set_tooltip_text(_("Toggle Sidebar"))
            self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
            self.header_bar.pack_start(self.toggle_sidebar_button)

            # Main menu button
            menu_button = Gtk.MenuButton()
            menu_button.set_icon_name("open-menu-symbolic")
            menu_button.set_tooltip_text(_("Main Menu"))
            menu_button.set_menu_model(MainApplicationMenu.create_menu())
            self.header_bar.pack_end(menu_button)

            # New tab button
            new_tab_button = Gtk.Button.new_from_icon_name("tab-new-symbolic")
            new_tab_button.set_tooltip_text(_("New Tab"))
            new_tab_button.connect("clicked", self._on_new_tab_clicked)
            new_tab_button.add_css_class("flat")
            self.header_bar.pack_end(new_tab_button)

            self.logger.debug("Header bar created")
            return self.header_bar

        except Exception as e:
            self.logger.error(f"Header bar creation failed: {e}")
            raise UIError("header_bar", f"creation failed: {e}")

    def _create_sidebar(self) -> Gtk.Widget:
        """Create the sidebar with session tree."""
        try:
            toolbar_view = Adw.ToolbarView()
            toolbar_view.add_css_class("background")

            # Fix CSS conflicts for menu separators after splits
            css = """
            /* Make header separator rule more specific to avoid affecting menu separators */
            .terminal-tab-view headerbar entry,
            .terminal-tab-view headerbar spinbutton,
            .terminal-tab-view headerbar button { 
                margin-top: -10px; 
                margin-bottom: -10px; 
            }
            
            /* Ensure menu separators always have correct styling */
            popover.menu menuitem separator,
            .terminal-tab-view popover.menu menuitem separator {
                border-top: 1px solid @borders;
                margin: 6px 0;
                min-height: 1px;
                max-height: 1px;
                padding: 0;
                background: none;
            }

            /* --- DRAG AND DROP VISUAL FEEDBACK --- */
            .drop-target {
                background-color: alpha(@theme_selected_bg_color, 0.5);
                border-radius: 6px;
            }
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )

            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            scrolled_window.set_vexpand(True)
            scrolled_window.set_child(self.session_tree.get_widget())

            toolbar_view.set_content(scrolled_window)

            # Create and add the bottom toolbar using Gtk.ActionBar for proper styling
            toolbar = Gtk.ActionBar()

            # Add session button
            add_session_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
            add_session_button.set_tooltip_text(_("Add Session"))
            add_session_button.connect("clicked", self._on_add_session_clicked)
            toolbar.pack_start(add_session_button)

            # Add folder button
            add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
            add_folder_button.set_tooltip_text(_("Add Folder"))
            add_folder_button.connect("clicked", self._on_add_folder_clicked)
            toolbar.pack_start(add_folder_button)

            # Edit button
            edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit_button.set_tooltip_text(_("Edit Selected"))
            edit_button.connect("clicked", self._on_edit_selected_clicked)
            toolbar.pack_start(edit_button)

            # Remove button
            remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic")
            remove_button.set_tooltip_text(_("Remove Selected"))
            remove_button.connect("clicked", self._on_remove_selected_clicked)
            toolbar.pack_start(remove_button)

            toolbar_view.add_bottom_bar(toolbar)

            self.logger.debug("Sidebar created using Adw.ToolbarView and Gtk.ActionBar")
            return toolbar_view

        except Exception as e:
            self.logger.error(f"Sidebar creation failed: {e}")
            raise UIError("sidebar", f"creation failed: {e}")
        
    def _create_content_area(self) -> Gtk.Widget:
        """Create the main content area with tabs."""
        try:
            # Main content box - just contains the tab view, tab bar goes in header
            self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            # Get tab components
            self.tab_bar = self.tab_manager.get_tab_bar()
            tab_view = self.tab_manager.get_tab_view()
            tab_view.add_css_class("terminal-tab-view")

            # Configure tab bar for better spacing and put it in header bar as title widget
            self.tab_bar.set_expand_tabs(True)  # Expand tabs to fill available space
            self.tab_bar.set_autohide(False)  # Don't auto-hide when multiple tabs

            # Put tab bar in header bar as title widget for better space utilization
            self.header_bar.set_title_widget(self.tab_bar)

            # Add only tab view to content area
            self.content_box.append(tab_view)

            # Connect to tab events with proper parameter handling
            tab_view.connect("page-attached", self._on_tab_attached)
            tab_view.connect("page-detached", self._on_tab_detached)

            self.logger.debug("Content area created with tabs in header bar")
            return self.content_box

        except Exception as e:
            self.logger.error(f"Content area creation failed: {e}")
            raise UIError("content_area", f"creation failed: {e}")

    def _on_tab_attached(self, tab_view, page, position):
        """Handle tab attached event."""
        try:
            self._update_tab_layout()
            # Update tab titles to show terminal counts
            GLib.idle_add(self.tab_manager.update_all_tab_titles)
        except Exception as e:
            self.logger.error(f"Tab attached handling failed: {e}")

    def _on_tab_detached(self, tab_view, page, position):
        """Handle tab detached event."""
        try:
            self._update_tab_layout()
            # Update remaining tab titles
            GLib.idle_add(self.tab_manager.update_all_tab_titles)
        except Exception as e:
            self.logger.error(f"Tab detached handling failed: {e}")

    def _update_tab_layout(self):
        """Update tab layout based on tab count."""
        try:
            tab_count = self.tab_manager.get_tab_count()

            if tab_count > 1:
                # Multiple tabs - show in header bar with good spacing
                self.tab_bar.set_expand_tabs(True)  # Fill available space
                self.tab_bar.set_autohide(False)  # Always show
                self.tab_bar.set_visible(True)

                # Ensure it's in the header bar
                if self.header_bar.get_title_widget() != self.tab_bar:
                    self.header_bar.set_title_widget(self.tab_bar)
            else:
                # Single tab - hide tab bar and show window title
                self.tab_bar.set_visible(False)
                self.header_bar.set_title_widget(None)  # Show normal window title

            self.logger.debug(
                f"Tab layout updated for {tab_count} tabs, visible in header bar"
            )

        except Exception as e:
            self.logger.error(f"Tab layout update failed: {e}")

    def _on_tab_count_changed(self, tab_count=None, tab_bar_visible=None):
        """Legacy method - delegates to _update_tab_layout."""
        self._update_tab_layout()

    def _setup_callbacks(self) -> None:
        """Set up callbacks between components."""
        try:
            # Session tree callbacks
            self.session_tree.on_session_activated = self._on_session_activated

            # Terminal manager callbacks
            self.terminal_manager.on_terminal_focus_changed = (
                self._on_terminal_focus_changed
            )
            self.terminal_manager.on_terminal_directory_changed = (
                self._on_terminal_directory_changed
            )

            # Tab manager callbacks - simplified
            self.tab_manager.on_quit_application = self._on_quit_application_requested

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

            self.logger.info(
                f"Loaded {session_count} sessions and {folder_count} folders"
            )

        except Exception as e:
            self.logger.error(f"Failed to load initial data: {e}")
            self._show_error_dialog(
                _("Data Loading Error"),
                _(
                    "Failed to load saved sessions and folders. Starting with empty configuration."
                ),
            )

    def _update_sidebar_button_icon(self) -> None:
        """Update sidebar toggle button icon."""
        try:
            is_visible = self.sidebar_box.get_visible()
            icon_name = (
                "view-reveal-symbolic" if is_visible else "view-conceal-symbolic"
            )
            self.toggle_sidebar_button.set_icon_name(icon_name)
        except Exception as e:
            self.logger.error(f"Failed to update sidebar button icon: {e}")

    # Event handlers
    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Handle sidebar toggle button."""
        try:
            is_visible = button.get_active()
            self.flap.set_reveal_flap(is_visible)  # Controls the Flap animation
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

    def _on_terminal_focus_changed(self, terminal, from_sidebar: bool) -> None:
        """Handle terminal focus change."""
        try:
            if not from_sidebar:
                self._sidebar_has_focus = False
        except Exception as e:
            self.logger.error(f"Terminal focus change handling failed: {e}")

    def _on_terminal_directory_changed(
        self, terminal, new_title: str, osc7_info
    ) -> None:
        """Handle OSC7 directory change and update tab title and split pane titles."""
        try:
            # Update title in splits/tabs
            self.tab_manager.update_terminal_title_in_splits(terminal, new_title)

            # Update window title
            if self.tab_manager.get_tab_count() <= 1:
                self.set_title(f"{APP_TITLE} - {new_title}")
            else:
                self.set_title(APP_TITLE)

            if osc7_info:
                self.logger.debug(
                    f"Updated titles to: '{new_title}' for directory: {osc7_info.path}"
                )
            else:
                self.logger.debug(
                    f"Updated titles to: '{new_title}' (no directory info)"
                )

        except Exception as e:
            self.logger.error(f"Terminal directory change handling failed: {e}")

    def _on_quit_application_requested(self) -> None:
        """Handle quit application request from tab manager."""
        try:
            self.logger.info("Quit application requested from tab manager")
            # Use the application's quit method to ensure proper cleanup
            if hasattr(self, "app") and self.app:
                self.app.quit()
            else:
                # Fallback - destroy the window which should trigger app quit
                self.destroy()
        except Exception as e:
            self.logger.error(f"Application quit request failed: {e}")

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
                self.logger.warning(
                    "No SSH sessions found but confirmation was requested"
                )
                self._perform_cleanup()
                self.close()
                return

            session_list = "\n".join([f"â€¢ {name}" for name in ssh_sessions])

            try:
                # Build the message in parts to avoid translation issues
                part1 = _("This window has active SSH connections:")
                part2 = _("Closing will disconnect these sessions.")
                part3 = _("Are you sure you want to close this window?")

                body_text = f"{part1}\n\n{session_list}\n\n{part2}\n\n{part3}"
                self.logger.debug("Final body text created successfully")
            except Exception as e:
                self.logger.error(f"Failed to format message: {e}")
                # Fallback to English without translation
                body_text = f"This window has active SSH connections:\n\n{session_list}\n\nClosing will disconnect these sessions.\n\nAre you sure you want to close this window?"
                self.logger.info("Using fallback English message")

            dialog = Adw.MessageDialog(
                transient_for=self, title=_("Close Window"), body=body_text
            )

            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close Window"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")

            def on_response(dlg, response_id):
                try:
                    if response_id == "close":
                        self.logger.info(
                            _("User confirmed window close with active SSH sessions")
                        )
                        self._force_closing = True  # Set flag to avoid loop
                        self._perform_cleanup()
                        self.close()
                    else:
                        self.logger.debug(_("User cancelled window close"))
                    dlg.close()
                except Exception as e:
                    self.logger.error(
                        _("Window SSH close confirmation response failed: {}").format(e)
                    )
                    dlg.close()

            dialog.connect("response", on_response)
            dialog.present()

        except Exception as e:
            self.logger.error(
                _("Window SSH close confirmation dialog failed: {}").format(e)
            )
            # Fallback to normal close if dialog fails
            self._perform_cleanup()
            self.close()

    def _perform_cleanup(self) -> None:
        """Perform window cleanup with proper state tracking."""
        if self._cleanup_performed:
            return

        self._cleanup_performed = True
        self.logger.info("Performing window cleanup")

        try:
            # CRITICAL FIX: Call TerminalManager's cleanup.
            # This will remove the timer and other resources.
            self.terminal_manager.cleanup_all_terminals()

            # The rest of the existing code to close terminals
            all_terminals = self.tab_manager.get_all_terminals()

            if not all_terminals:
                self.logger.debug("No terminals to clean up")
                return

            self.logger.debug(f"Closing {len(all_terminals)} terminals")

            for terminal in all_terminals:
                try:
                    # The call to close_terminal is now more about the child process
                    self.terminal_manager.close_terminal(terminal)
                except Exception as e:
                    self.logger.error(f"Error closing terminal: {e}")

            self.logger.info("Window cleanup completed")

        except Exception as e:
            self.logger.error(f"Window cleanup error: {e}")

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
                    error_msg = _("Session validation failed:\n{errors}").format(
                        errors="\n".join(errors)
                    )
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
                log_terminal_event(
                    "created", session.name, f"SSH to {session.get_connection_string()}"
                )

            if result is None:
                self._show_error_dialog(
                    _("Terminal Creation Failed"),
                    _("Could not create terminal for this session."),
                )

        except VTENotAvailableError:
            self._show_error_dialog(
                _("VTE Not Available"),
                _("Cannot open session - VTE library not installed."),
            )
        except Exception as e:
            self.logger.error(f"Session activation failed: {e}")
            self._show_error_dialog(
                _("Session Error"),
                _("Failed to activate session: {error}").format(error=str(e)),
            )

    def _on_tab_selected(self, page) -> None:
        """Handle tab selection change."""
        # Tab manager already handles focus
        pass

    # --- START OF MODIFICATION ---
    def _on_connect_sftp(self, action, param) -> None:
        """Handle connect with SFTP action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem) and selected_item.is_ssh():
                self.logger.info(f"SFTP connection requested for session: '{selected_item.name}'")
                
                # Call the TabManager to create an SFTP tab
                result = self.tab_manager.create_sftp_tab(selected_item)
                
                if result is None:
                    self._show_error_dialog(
                        _("SFTP Connection Failed"),
                        _("Could not create SFTP terminal for this session."),
                    )
                else:
                    log_terminal_event("created", selected_item.name, f"SFTP to {selected_item.get_connection_string()}")
            else:
                self.logger.warning("SFTP connection requested for a non-SSH or non-existent session.")

        except Exception as e:
            self.logger.error(f"SFTP connection failed: {e}")
            self._show_error_dialog(
                _("SFTP Error"),
                _("Failed to start SFTP session: {error}").format(error=str(e)),
            )
    # --- END OF MODIFICATION ---

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

            result = self.tab_manager.create_local_tab(working_directory=None)
            if result is None:
                raise AshyTerminalError(
                    "Terminal creation failed",
                    category=ErrorCategory.TERMINAL,
                    severity=ErrorSeverity.HIGH,
                )

            log_terminal_event("created", "Local Terminal", "new tab")

        except VTENotAvailableError:
            self._show_error_dialog(
                _("VTE Not Available"),
                _("Cannot create terminal - VTE library not installed."),
            )
        except Exception as e:
            self.logger.error(f"New local tab creation failed: {e}")
            self._show_error_dialog(
                _("Terminal Error"),
                _("Failed to create new tab: {error}").format(error=str(e)),
            )
        finally:
            # Reset flag after delay
            def reset_flag():
                self._creating_tab = False
                return False

            GLib.timeout_add(200, reset_flag)

    def _on_close_tab(self, action, param) -> None:
        """Handle close tab action."""
        try:
            # Get current page and close it properly
            current_page = self.tab_manager.get_selected_page()
            if current_page:
                # This will trigger the proper Adwaita close flow
                self.tab_manager.tab_view.close_page(current_page)
        except Exception as e:
            self.logger.error(f"Close tab action failed: {e}")

    def _on_copy(self, action, param) -> None:
        """Handle copy action."""
        try:
            success = self.tab_manager.copy_from_current_terminal()
            if not success:
                self.logger.debug(
                    "Copy operation failed - no selection or terminal not available"
                )
        except Exception as e:
            self.logger.error(f"Copy operation failed: {e}")

    def _on_paste(self, action, param) -> None:
        """Handle paste action."""
        try:
            success = self.tab_manager.paste_to_current_terminal()
            if not success:
                self.logger.debug(
                    "Paste operation failed - no clipboard content or terminal not available"
                )
        except Exception as e:
            self.logger.error(f"Paste operation failed: {e}")

    def _on_select_all(self, action, param) -> None:
        """Handle select all action."""
        try:
            self.tab_manager.select_all_in_current_terminal()
        except Exception as e:
            self.logger.error(f"Select all operation failed: {e}")

    # Action handlers - Splitting actions
    def _on_split_horizontal(self, action, param) -> None:
        """Handle horizontal split action."""
        try:
            focused_terminal = self.tab_manager.get_selected_terminal()
            if focused_terminal:
                self.tab_manager.split_vertical(focused_terminal)
        except Exception as e:
            self.logger.error(f"Horizontal split failed: {e}")
            self._show_error_dialog(_("Split Error"), str(e))

    def _on_split_vertical(self, action, param) -> None:
        """Handle vertical split action."""
        try:
            focused_terminal = self.tab_manager.get_selected_terminal()
            if focused_terminal:
                self.tab_manager.split_horizontal(focused_terminal)
        except Exception as e:
            self.logger.error(f"Vertical split failed: {e}")
            self._show_error_dialog(_("Split Error"), str(e))

    def _on_close_pane(self, action, param) -> None:
        """Handle close pane action."""
        try:
            focused_terminal = self.tab_manager.get_selected_terminal()
            if focused_terminal:
                self.tab_manager.close_pane(focused_terminal)
        except Exception as e:
            self.logger.error(f"Close pane failed: {e}")

    def _on_focus_pane_up(self, action, param) -> None:
        """Handle focus pane up action."""
        try:
            self.tab_manager.focus_pane_direction("up")
        except Exception as e:
            self.logger.error(f"Focus pane up failed: {e}")

    def _on_focus_pane_down(self, action, param) -> None:
        """Handle focus pane down action."""
        try:
            self.tab_manager.focus_pane_direction("down")
        except Exception as e:
            self.logger.error(f"Focus pane down failed: {e}")

    def _on_focus_pane_left(self, action, param) -> None:
        """Handle focus pane left action."""
        try:
            self.tab_manager.focus_pane_direction("left")
        except Exception as e:
            self.logger.error(f"Focus pane left failed: {e}")

    def _on_focus_pane_right(self, action, param) -> None:
        """Handle focus pane right action."""
        try:
            self.tab_manager.focus_pane_direction("right")
        except Exception as e:
            self.logger.error(f"Focus pane right failed: {e}")

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
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                found, position = self.session_store.find(selected_item)
                if found:
                    self._show_session_edit_dialog(selected_item, position)
        except Exception as e:
            self.logger.error(f"Edit session failed: {e}")
            self._show_error_dialog(
                _("Edit Error"),
                _("Failed to edit session: {error}").format(error=str(e)),
            )

    def _on_duplicate_session(self, action, param) -> None:
        """Handle duplicate session action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                duplicated = self.session_tree.operations.duplicate_session(
                    selected_item
                )
                if duplicated:
                    self.session_tree.refresh_tree()
                    log_session_event("duplicated", selected_item.name)
        except Exception as e:
            self.logger.error(f"Duplicate session failed: {e}")
            self._show_error_dialog(
                _("Duplicate Error"),
                _("Failed to duplicate session: {error}").format(error=str(e)),
            )

    def _on_rename_session(self, action, param) -> None:
        """Handle rename session action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._show_rename_dialog(selected_item, True)
        except Exception as e:
            self.logger.error(f"Rename session failed: {e}")
            self._show_error_dialog(
                _("Rename Error"),
                _("Failed to rename session: {error}").format(error=str(e)),
            )

    def _on_move_session_to_folder(self, action, param) -> None:
        """Handle move session to folder action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                dialog = MoveSessionDialog(
                    self,
                    selected_item,
                    self.folder_store,
                    self.session_tree.operations
                )
                dialog.present()
        except Exception as e:
            self.logger.error(f"Move session failed: {e}")
            self._show_error_dialog(
                _("Move Error"),
                _("Failed to move session: {error}").format(error=str(e)),
            )

    def get_toast_overlay(self) -> Optional[Adw.ToastOverlay]:
        """Provides access to the window's toast overlay for dialogs."""
        return getattr(self, "toast_overlay", None)
    
    # --- START: NEW/MODIFIED METHODS FOR MULTI-DELETE ---
    def _on_delete_selected_items(self, action=None, param=None) -> None:
        """Handle deleting all selected items from the session tree."""
        try:
            selected_items = self.session_tree.get_selected_items()
            if selected_items:
                self._show_delete_confirmation(selected_items)
        except Exception as e:
            self.logger.error(f"Delete selected items failed: {e}")
            self._show_error_dialog(
                _("Delete Error"),
                _("Failed to delete selected items: {error}").format(error=str(e)),
            )

    # Action handlers - Folder actions
    def _on_edit_folder(self, action, param) -> None:
        """Handle edit folder action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                found, position = self.folder_store.find(selected_item)
                if found:
                    self._show_folder_edit_dialog(selected_item, position)
        except Exception as e:
            self.logger.error(f"Edit folder failed: {e}")
            self._show_error_dialog(
                _("Edit Error"),
                _("Failed to edit folder: {error}").format(error=str(e)),
            )

    def _on_rename_folder(self, action, param) -> None:
        """Handle rename folder action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                self._show_rename_dialog(selected_item, False)
        except Exception as e:
            self.logger.error(f"Rename folder failed: {e}")
            self._show_error_dialog(
                _("Rename Error"),
                _("Failed to rename folder: {error}").format(error=str(e)),
            )

    def _on_add_session_to_folder(self, action, param) -> None:
        """Handle add session to folder action."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionFolder):
                new_session = SessionItem(
                    name=_("New Session"), folder_path=selected_item.path
                )
                self._show_session_edit_dialog(new_session, -1)
        except Exception as e:
            self.logger.error(f"Add session to folder failed: {e}")
            self._show_error_dialog(
                _("Add Error"),
                _("Failed to add session to folder: {error}").format(error=str(e)),
            )

    # Action handlers - Clipboard actions
    def _on_cut_item(self, action, param) -> None:
        """Handle cut item action."""
        try:
            self.session_tree._cut_selected_item_safe()
        except Exception as e:
            self.logger.error(f"Cut item failed: {e}")

    def _on_copy_item(self, action, param) -> None:
        """Handle copy item action."""
        try:
            self.session_tree._copy_selected_item_safe()
        except Exception as e:
            self.logger.error(f"Copy item failed: {e}")

    def _on_paste_item(self, action, param) -> None:
        """Handle paste item action."""
        try:
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
            dialog.connect(
                "color-scheme-changed",
                lambda d, i: self.terminal_manager.update_all_terminals(),
            )
            dialog.connect(
                "transparency-changed",
                lambda d, v: self.terminal_manager.update_all_terminals(),
            )
            dialog.connect(
                "font-changed",
                lambda d, f: self.terminal_manager.update_all_terminals(),
            )
            dialog.connect(
                "shortcut-changed", lambda d: self._update_keyboard_shortcuts()
            )

            dialog.present()

        except Exception as e:
            self.logger.error(f"Preferences dialog failed: {e}")
            self._show_error_dialog(
                _("Preferences Error"),
                _("Failed to open preferences: {error}").format(error=str(e)),
            )

    def _on_shortcuts(self, action, param) -> None:
        """Handle shortcuts action - show keyboard shortcuts window."""
        try:
            shortcuts_window = Gtk.ShortcutsWindow(transient_for=self, modal=True)

            # Create shortcuts section
            section = Gtk.ShortcutsSection(
                title=_("Keyboard Shortcuts"), section_name="shortcuts"
            )

            # Terminal shortcuts
            terminal_group = Gtk.ShortcutsGroup(title=_("Terminal"))

            terminal_shortcuts = [
                (_("New Tab"), "<Control>t"),
                (_("Close Tab"), "<Control>w"),
                (_("New Window"), "<Control>n"),
                (_("Copy"), "<Control><Shift>c"),
                (_("Paste"), "<Control><Shift>v"),
                (_("Select All"), "<Control><Shift>a"),
            ]

            for title, accel in terminal_shortcuts:
                shortcut = Gtk.ShortcutsShortcut(title=title, accelerator=accel)
                terminal_group.append(shortcut)

            # Split navigation shortcuts
            split_group = Gtk.ShortcutsGroup(title=_("Split Navigation"))

            split_shortcuts = [
                (_("Focus Up"), "<Control>Up"),
                (_("Focus Down"), "<Control>Down"),
                (_("Focus Left"), "<Control>Left"),
                (_("Focus Right"), "<Control>Right"),
            ]

            for title, accel in split_shortcuts:
                shortcut = Gtk.ShortcutsShortcut(title=title, accelerator=accel)
                split_group.append(shortcut)

            # Application shortcuts
            app_group = Gtk.ShortcutsGroup(title=_("Application"))

            app_shortcuts = [
                (_("Preferences"), "<Control>comma"),
                (_("Toggle Sidebar"), "<Control><Shift>h"),
                (_("Quit"), "<Control>q"),
            ]

            for title, accel in app_shortcuts:
                shortcut = Gtk.ShortcutsShortcut(title=title, accelerator=accel)
                app_group.append(shortcut)

            section.append(terminal_group)
            section.append(split_group)
            section.append(app_group)
            shortcuts_window.add_section(section)

            shortcuts_window.present()

        except Exception as e:
            self.logger.error(f"Shortcuts window failed: {e}")
            self._show_error_dialog(
                _("Keyboard Shortcuts"),
                _("Failed to open shortcuts window: {error}").format(error=str(e)),
            )

    def _on_new_window(self, action, param) -> None:
        """Handle new window action."""
        try:
            # Get the application and create new window
            app = self.get_application()
            if app and hasattr(app, "create_new_window"):
                new_window = app.create_new_window()
                if new_window:
                    new_window.present()
                else:
                    self._show_error_dialog(
                        _("Nova Janela"), _("Falha ao criar nova janela")
                    )
            else:
                self._show_error_dialog(
                    _("Nova Janela"), _("NÃ£o foi possÃ­vel criar uma nova janela")
                )

        except Exception as e:
            self.logger.error(f"New window creation failed: {e}")
            self._show_error_dialog(
                _("Nova Janela"),
                _("Falha ao criar nova janela: {error}").format(error=str(e)),
            )

    def _on_toggle_sidebar_action(self, action, param) -> None:
        """Handle toggle sidebar action via keyboard shortcut."""
        try:
            self.logger.info("DEBUG: Toggle sidebar action called!")

            # Toggle the button state which will trigger the visibility change
            current_state = self.toggle_sidebar_button.get_active()
            self.logger.info(f"DEBUG: Current sidebar state: {current_state}")

            self.toggle_sidebar_button.set_active(not current_state)

            self.logger.debug(
                f"Sidebar toggled via keyboard shortcut: {not current_state}"
            )

        except Exception as e:
            self.logger.error(f"Toggle sidebar action failed: {e}")

    # Button handlers
    def _on_add_session_clicked(self, button) -> None:
        """Handle add session button click."""
        try:
            new_session = SessionItem(name=_("New Session"))
            self._show_session_edit_dialog(new_session, -1)
        except Exception as e:
            self.logger.error(f"Add session button failed: {e}")
            self._show_error_dialog(
                _("Add Session Error"),
                _("Failed to add session: {error}").format(error=str(e)),
            )

    def _on_new_tab_clicked(self, button) -> None:
        """Handle new tab button click in header."""
        try:
            if not VTE_AVAILABLE:
                self._show_error_dialog(
                    _("VTE Not Available"),
                    _("Cannot create terminal - VTE library not installed."),
                )
                return

            # Use the same logic as new local tab action
            result = self.tab_manager.create_local_tab()
            if result is None:
                self._show_error_dialog(
                    _("Terminal Error"), _("Failed to create new tab.")
                )
                return

            log_terminal_event("created", "Local Terminal", "header button")
            self.logger.debug("New tab created from header button")

        except Exception as e:
            self.logger.error(f"New tab header button failed: {e}")
            self._show_error_dialog(
                _("Terminal Error"),
                _("Failed to create new tab: {error}").format(error=str(e)),
            )

    def _on_add_folder_clicked(self, button) -> None:
        """Handle add folder button click."""
        try:
            new_folder = SessionFolder(name=_("New Folder"))
            self._show_folder_edit_dialog(new_folder, None)
        except Exception as e:
            self.logger.error(f"Add folder button failed: {e}")
            self._show_error_dialog(
                _("Add Folder Error"),
                _("Failed to add folder: {error}").format(error=str(e)),
            )

    def _on_edit_selected_clicked(self, button) -> None:
        """Handle edit selected button click."""
        try:
            selected_item = self.session_tree.get_selected_item()
            if isinstance(selected_item, SessionItem):
                self._on_edit_session(None, None)
            elif isinstance(selected_item, SessionFolder):
                self._on_edit_folder(None, None)
        except Exception as e:
            self.logger.error(f"Edit selected button failed: {e}")
            self._show_error_dialog(
                _("Edit Error"),
                _("Failed to edit selected item: {error}").format(error=str(e)),
            )

    def _on_remove_selected_clicked(self, button) -> None:
        """Handle remove selected button click."""
        self._on_delete_selected_items()

    # Helper methods for dialogs
    def _show_session_edit_dialog(self, session: SessionItem, position: int) -> None:
        """Show session edit dialog."""
        try:
            dialog = SessionEditDialog(
                self, session, self.session_store, position, self.folder_store
            )
            dialog.present()
        except Exception as e:
            self.logger.error(f"Session edit dialog failed: {e}")
            raise DialogError("session_edit", str(e))

    def _show_folder_edit_dialog(
        self, folder: Optional[SessionFolder], position: Optional[int]
    ) -> None:
        """Show folder edit dialog."""
        try:
            is_new = position is None
            dialog = FolderEditDialog(
                self, self.folder_store, folder, position, is_new=is_new
            )
            dialog.present()
        except Exception as e:
            self.logger.error(f"Folder edit dialog failed: {e}")
            raise DialogError("folder_edit", str(e))

    def _show_rename_dialog(
        self, item: Union[SessionItem, SessionFolder], is_session: bool
    ) -> None:
        """Show rename dialog."""
        try:
            item_type = _("Session") if is_session else _("Folder")
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Rename {type}").format(type=item_type),
                body=_('Enter new name for "{name}":').format(name=item.name),
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
                                log_session_event(
                                    "renamed", f"{old_name} -> {new_name}"
                                )
                            else:
                                # Handle folder path updates
                                if isinstance(item, SessionFolder):
                                    old_path = item.path
                                    item.path = os.path.normpath(
                                        f"{item.parent_path}/{new_name}"
                                        if item.parent_path
                                        else f"/{new_name}"
                                    )
                                    self.session_tree.operations._update_child_paths(
                                        old_path, item.path
                                    )
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
        self._show_info_dialog(
            _("Move Session"),
            _(
                "Move session functionality will be implemented in folder selection dialog."
            ),
        )

    def _show_delete_confirmation(self, items: List[Union[SessionItem, SessionFolder]]) -> None:
        """Show delete confirmation dialog for one or more items."""
        try:
            if not items:
                return

            count = len(items)
            title = _("Delete Item") if count == 1 else _("Delete Items")
            
            # Build the message based on the number of items
            if count == 1:
                item = items[0]
                item_type = _("Session") if isinstance(item, SessionItem) else _("Folder")
                title = _("Delete {type}").format(type=item_type)
                
                has_children = isinstance(item, SessionFolder) and self.session_tree.operations._folder_has_children(item.path)
                if has_children:
                    body_text = _("The folder \"{name}\" is not empty. Are you sure you want to permanently delete it and all its contents?").format(name=item.name)
                else:
                    body_text = _("Are you sure you want to delete \"{name}\"?").format(name=item.name)
            else:
                body_text = _("Are you sure you want to permanently delete these {count} items?").format(count=count)
                if any(isinstance(it, SessionFolder) and self.session_tree.operations._folder_has_children(it.path) for it in items):
                    body_text += "\n\n" + _("This will also delete all contents of any selected folders.")

            dialog = Adw.MessageDialog(
                transient_for=self,
                title=title,
                body=body_text
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("delete", _("Delete"))
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "delete":
                        # Loop through all selected items and delete them
                        for item in items:
                            result = None
                            if isinstance(item, SessionItem):
                                result = self.session_tree.operations.remove_session(item)
                                if result.success:
                                    log_session_event("deleted", item.name)
                            elif isinstance(item, SessionFolder):
                                force_delete = self.session_tree.operations._folder_has_children(item.path)
                                result = self.session_tree.operations.remove_folder(item, force=force_delete)
                            
                            if result and not result.success:
                                self._show_error_dialog(_("Delete Error"), result.message)
                                # Stop on first error
                                break
                        
                        # Refresh the tree once after all operations
                        self.session_tree.refresh_tree()

                    dlg.close()
                except Exception as e:
                    self.logger.error(f"Delete confirmation response failed: {e}")
                    dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Delete confirmation dialog failed: {e}")
            raise DialogError("delete_confirmation", str(e))
    # --- END: NEW/MODIFIED METHODS FOR MULTI-DELETE ---
    
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
            
    def _on_open_url(self, action, param) -> None:
        """Handle open URL action."""
        try:
            terminal = self.tab_manager.get_selected_terminal()
            if terminal and hasattr(terminal, '_context_menu_url'):
                url = terminal._context_menu_url
                success = self.terminal_manager._open_hyperlink(url)
                if success:
                    self.logger.info(f"URL opened from context menu: {url}")
                delattr(terminal, '_context_menu_url')
        except Exception as e:
            self.logger.error(f"Open URL action failed: {e}")

    def _on_copy_url(self, action, param) -> None:
        """Handle copy URL action."""
        try:
            terminal = self.tab_manager.get_selected_terminal()
            if terminal and hasattr(terminal, '_context_menu_url'):
                url = terminal._context_menu_url
                clipboard = Gdk.Display.get_default().get_clipboard()
                clipboard.set(url)
                self.logger.info(f"URL copied to clipboard: {url}")
                delattr(terminal, '_context_menu_url')
        except Exception as e:
            self.logger.error(f"Copy URL action failed: {e}")