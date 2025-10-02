# ashyterm/window.py

import weakref
from typing import List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Vte

from .sessions.models import LayoutItem, SessionFolder, SessionItem
from .sessions.operations import SessionOperations
from .sessions.storage import (
    load_folders_to_store,
    load_sessions_and_folders,
    load_sessions_to_store,
)
from .sessions.tree import SessionTreeView
from .settings.manager import SettingsManager
from .state.window_state import WindowStateManager
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .ui.actions import WindowActions
from .ui.dialogs.command_guide_dialog import CommandGuideDialog
from .ui.sidebar_manager import SidebarManager
from .ui.window_ui import WindowUIBuilder
from .utils.exceptions import UIError
from .utils.logger import get_logger
from .utils.security import validate_session_data
from .utils.translation_utils import _


class CommTerminalWindow(Adw.ApplicationWindow):
    """
    Main application window. Acts as the central orchestrator for all major
    components (managers), handling high-level
    user interactions and window lifecycle events.
    """

    def __init__(self, application, settings_manager: SettingsManager, **kwargs):
        super().__init__(application=application)
        self.logger = get_logger("ashyterm.window")
        self.logger.info("Initializing main window")

        # Component Initialization
        self.settings_manager = settings_manager
        self.is_main_window = True
        self._cleanup_performed = False
        self._force_closing = False
        self.layouts: List[LayoutItem] = []
        self.active_temp_files = weakref.WeakKeyDictionary()
        self.command_guide_dialog = None  # MODIFIED: For singleton dialog

        # Search state tracking
        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self.search_active = False

        # Initial state from command line or other windows
        self.initial_working_directory = kwargs.get("initial_working_directory")
        self.initial_execute_command = kwargs.get("initial_execute_command")
        self.close_after_execute = kwargs.get("close_after_execute", False)
        self.initial_ssh_target = kwargs.get("initial_ssh_target")
        self._is_for_detached_tab = kwargs.get("_is_for_detached_tab", False)
        self.detached_terminals_data = kwargs.get("detached_terminals_data")
        self.detached_file_manager = kwargs.get("detached_file_manager")

        # Window setup
        self._setup_initial_window_size()
        self.set_title(_("Ashy Terminal"))
        self.set_icon_name(None)

        # Component Initialization
        self._create_managers_and_ui()
        self._connect_component_signals()
        # MODIFIED: Data loading is now asynchronous to speed up startup.
        # self._load_initial_data()
        self._setup_window_events()

        # Re-register terminals and reconnect signals for a detached tab
        if self._is_for_detached_tab and self.detached_terminals_data:
            self.logger.info(
                f"Re-registering and reconnecting signals for {len(self.detached_terminals_data)} terminals."
            )
            for term_data in self.detached_terminals_data:
                terminal_widget = term_data["widget"]
                terminal_id = term_data["id"]
                terminal_info = term_data["info"]

                # Step 1: Re-register the terminal in the new window's registry
                self.terminal_manager.registry.reregister_terminal(
                    terminal=terminal_widget,
                    terminal_id=terminal_id,
                    terminal_info=terminal_info,
                )

                # Step 2: Reconnect process signals to the new window's manager
                self.terminal_manager._setup_terminal_events(
                    terminal=terminal_widget,
                    identifier=terminal_info.get("identifier"),
                    terminal_id=terminal_id,
                )

                # Step 3: Reconnect UI control signals for split panes
                pane = terminal_widget.get_parent().get_parent()
                if isinstance(pane, Adw.ToolbarView) and hasattr(pane, "close_button"):
                    old_close_button = pane.close_button
                    old_move_button = pane.move_button
                    button_container = old_close_button.get_parent()

                    if button_container:
                        # Create new buttons connected to the NEW tab manager
                        new_close_button = Gtk.Button(
                            icon_name="window-close-symbolic",
                            tooltip_text=_("Close Pane"),
                        )
                        new_close_button.add_css_class("flat")
                        new_close_button.connect(
                            "clicked",
                            lambda _, term=terminal_widget: self.tab_manager.close_pane(
                                term
                            ),
                        )

                        new_move_button = Gtk.Button(
                            icon_name="select-rectangular-symbolic",
                            tooltip_text=_("Move to New Tab"),
                        )
                        new_move_button.add_css_class("flat")
                        new_move_button.connect(
                            "clicked",
                            lambda _,
                            term=terminal_widget: self.tab_manager._on_move_to_tab_callback(
                                term
                            ),
                        )

                        # Replace old buttons with new ones
                        button_container.remove(old_move_button)
                        button_container.remove(old_close_button)
                        button_container.append(new_move_button)
                        button_container.append(new_close_button)

                        # Update references on the pane itself
                        pane.move_button = new_move_button
                        pane.close_button = new_close_button

                        self.logger.info(
                            f"Reconnected UI controls for terminal {terminal_id}"
                        )

        if not self._is_for_detached_tab:
            # MODIFIED: Load data and create initial tab asynchronously.
            GLib.idle_add(self._load_initial_data_and_tab)

        # NEW: Apply all visual settings after the window is fully constructed,
        # especially important for detached windows.
        GLib.idle_add(self._apply_initial_visual_settings)

        self.logger.info("Main window initialization completed")

    # NEW: Method to apply all visual settings on window creation.
    def _apply_initial_visual_settings(self) -> None:
        """Applies all visual settings upon window creation."""
        self.logger.info("Applying initial visual settings to new window.")
        # Apply theme first, as it might affect colors used by other settings.
        if self.settings_manager.get("gtk_theme") == "terminal":
            self.settings_manager.apply_gtk_terminal_theme(self)
        else:
            # Ensure headerbar transparency is correct for non-terminal themes.
            self.settings_manager.apply_headerbar_transparency(self.header_bar)

        # Apply settings to all terminals, which handles terminal transparency.
        self.terminal_manager.apply_settings_to_all_terminals()

    def _create_managers_and_ui(self) -> None:
        """
        Centralize Component Creation and UI Building.
        This method acts as the "assembly line" for the application's main
        components, creating and wiring them together.
        """
        self.logger.info("Creating and wiring core components")
        # Data Stores
        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)

        # Business Logic Layer
        self.session_operations = SessionOperations(
            self.session_store, self.folder_store, self.settings_manager
        )

        # UI/View-Model Layer
        self.terminal_manager = TerminalManager(self, self.settings_manager)
        self.session_tree = SessionTreeView(
            self,
            self.session_store,
            self.folder_store,
            self.settings_manager,
            self.session_operations,
        )
        self.tab_manager = TabManager(
            self.terminal_manager,
            on_quit_callback=self._on_quit_application_requested,
            on_detach_tab_callback=self._on_detach_tab_requested,
            scrolled_tab_bar=None,  # Will be set by UI builder
            on_tab_count_changed=self._update_tab_layout,
        )
        self.terminal_manager.set_tab_manager(self.tab_manager)

        # UI Builder
        self.ui_builder = WindowUIBuilder(self)
        self.ui_builder.build_ui()
        self._assign_ui_components()

        # State and Action Handlers
        self.state_manager = WindowStateManager(self)
        self.action_handler = WindowActions(self)
        self.sidebar_manager = SidebarManager(self, self.ui_builder)

    def _assign_ui_components(self):
        """Assigns widgets created by the UI builder to the window instance."""
        self.header_bar = self.ui_builder.header_bar
        self.flap = self.ui_builder.flap
        self.sidebar_box = self.ui_builder.sidebar_box
        self.sidebar_popover = self.ui_builder.sidebar_popover
        self.toggle_sidebar_button = self.ui_builder.toggle_sidebar_button
        self.file_manager_button = self.ui_builder.file_manager_button
        self.command_guide_button = self.ui_builder.command_guide_button
        self.cleanup_button = self.ui_builder.cleanup_button
        self.cleanup_popover = self.ui_builder.cleanup_popover
        self.font_sizer_widget = self.ui_builder.font_sizer_widget
        self.scrolled_tab_bar = self.ui_builder.scrolled_tab_bar
        self.single_tab_title_widget = self.ui_builder.single_tab_title_widget
        self.title_stack = self.ui_builder.title_stack
        self.toast_overlay = self.ui_builder.toast_overlay
        self.search_bar = self.ui_builder.search_bar
        self.search_button = self.ui_builder.search_button
        # Assign the correctly named widgets
        self.terminal_search_entry = self.ui_builder.terminal_search_entry
        self.search_entry = self.ui_builder.sidebar_search_entry
        self.search_prev_button = self.ui_builder.search_prev_button
        self.search_next_button = self.ui_builder.search_next_button
        self.search_occurrence_label = self.ui_builder.search_occurrence_label
        self.case_sensitive_switch = self.ui_builder.case_sensitive_switch
        self.regex_switch = self.ui_builder.regex_switch
        self.tab_manager.scrolled_tab_bar = self.scrolled_tab_bar

        # Apply initial headerbar transparency
        self.settings_manager.apply_headerbar_transparency(self.header_bar)

        # Apply initial GTK terminal theme if set
        if self.settings_manager.get("gtk_theme") == "terminal":
            self.settings_manager.apply_gtk_terminal_theme(self)

    def _connect_component_signals(self) -> None:
        """
        Connects signals and callbacks between the window and its managers.
        """
        self._setup_actions()
        self._setup_keyboard_shortcuts()
        self._setup_search()

        self.session_tree.on_session_activated = self._on_session_activated
        self.session_tree.on_layout_activated = self.state_manager.restore_saved_layout
        self.session_tree.on_folder_expansion_changed = (
            self.sidebar_manager.update_sidebar_sizes
        )
        self.terminal_manager.on_terminal_focus_changed = (
            self._on_terminal_focus_changed
        )
        self.terminal_manager.set_terminal_exit_handler(self._on_terminal_exit)
        self.tab_manager.get_view_stack().connect(
            "notify::visible-child", self._on_tab_changed
        )
        self.settings_manager.add_change_listener(self._on_setting_changed)
        self.file_manager_button.connect("toggled", self._on_toggle_file_manager)

    def _setup_actions(self) -> None:
        """Set up window-level actions by delegating to the action handler."""
        try:
            self.action_handler.setup_actions()
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")
            raise UIError("window", f"action setup failed: {e}")

    def _setup_keyboard_shortcuts(self) -> None:
        """Sets up window-level keyboard shortcuts for tab navigation."""
        controller = Gtk.EventControllerKey.new()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _setup_search(self) -> None:
        """Connects signals for the terminal search UI."""
        self.search_button.bind_property(
            "active",
            self.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL,
        )
        # Connect the correct search entry to the correct handler
        self.terminal_search_entry.connect(
            "search-changed", self._on_search_text_changed
        )
        self.terminal_search_entry.connect("stop-search", self._on_search_stop)
        self.terminal_search_entry.connect("activate", self._on_search_next)
        self.search_prev_button.connect("clicked", self._on_search_previous)
        self.search_next_button.connect("clicked", self._on_search_next)
        self.search_bar.connect(
            "notify::search-mode-enabled", self._on_search_mode_changed
        )
        # Connect case sensitive switch
        self.case_sensitive_switch.connect(
            "notify::active", self._on_case_sensitive_changed
        )
        # Initialize switch state from settings
        self.case_sensitive_switch.set_active(
            self.settings_manager.get("search_case_sensitive", False)
        )

        # Connect regex switch
        self.regex_switch.connect("notify::active", self._on_regex_changed)
        # Initialize switch state from settings
        self.regex_switch.set_active(
            self.settings_manager.get("search_use_regex", False)
        )

    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        self.connect("close-request", self._on_window_close_request)
        # Connect window state change signals
        self.connect("notify::default-width", self._on_window_size_changed)
        self.connect("notify::default-height", self._on_window_size_changed)
        self.connect("notify::maximized", self._on_window_maximized_changed)

    def _setup_initial_window_size(self) -> None:
        """Set up initial window size and state from settings."""
        if self.settings_manager.get("remember_window_state", True):
            width = self.settings_manager.get("window_width", 1200)
            height = self.settings_manager.get("window_height", 700)
            maximized = self.settings_manager.get("window_maximized", False)

            self.set_default_size(width, height)

            if maximized:
                # Delay maximization to ensure window is realized
                GLib.idle_add(self.maximize)
        else:
            self.set_default_size(1200, 700)

    def _on_window_size_changed(self, window, param_spec) -> None:
        """Handle window size changes to save to settings."""
        if not self.settings_manager.get("remember_window_state", True):
            return

        if not self.is_maximized():
            # Only save size when not maximized
            width = self.get_width()
            height = self.get_height()

            if width > 0 and height > 0:
                self.settings_manager.set("window_width", width)
                self.settings_manager.set("window_height", height)

    def _on_window_maximized_changed(self, window, param_spec) -> None:
        """Handle window maximized state changes to save to settings."""
        if not self.settings_manager.get("remember_window_state", True):
            return

        maximized = self.is_maximized()
        self.settings_manager.set("window_maximized", maximized)

    def _load_initial_data_and_tab(self) -> bool:
        """
        Asynchronously loads initial data and then creates the initial tab.
        This allows the UI to show up immediately.
        """
        self._load_initial_data()
        self._create_initial_tab_safe()
        return GLib.SOURCE_REMOVE  # Run only once

    def _load_initial_data(self) -> None:
        """Load initial sessions, folders, and layouts data."""
        try:
            self.state_manager.load_layouts()
            sessions_data, folders_data = load_sessions_and_folders()
            load_sessions_to_store(self.session_store, sessions_data)
            load_folders_to_store(self.folder_store, folders_data)
            self.refresh_tree()
            self.logger.info(
                f"Loaded {self.session_store.get_n_items()} sessions, "
                f"{self.folder_store.get_n_items()} folders, "
                f"and {len(self.layouts)} layouts"
            )
        except Exception as e:
            self.logger.error(f"Failed to load initial data: {e}")
            self._show_error_dialog(
                _("Data Loading Error"),
                _(
                    "Failed to load saved sessions and folders. Starting with empty configuration."
                ),
            )

    # --- Event Handlers & Callbacks ---

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handles key press events for tab navigation and search."""
        # Check for Ctrl+Shift+F for search - use uppercase F key
        if (
            state & Gdk.ModifierType.CONTROL_MASK
            and state & Gdk.ModifierType.SHIFT_MASK
            and (keyval == Gdk.KEY_f or keyval == Gdk.KEY_F)
        ):
            # Toggle search bar
            current_mode = self.search_bar.get_search_mode()
            self.search_bar.set_search_mode(not current_mode)
            if (
                not current_mode
            ):  # If we're showing the search bar, focus the search entry
                self.terminal_search_entry.grab_focus()
            return True  # Use True instead of Gdk.EVENT_STOP for better compatibility

        # *** CORREÇÃO APLICADA AQUI ***
        # Convert the key press event into a GTK accelerator string.
        accel_string = Gtk.accelerator_name(
            keyval, state & Gtk.accelerator_get_default_mod_mask()
        )

        # Get the currently configured shortcuts from the settings manager.
        next_tab_shortcut = self.settings_manager.get_shortcut("next-tab")
        prev_tab_shortcut = self.settings_manager.get_shortcut("previous-tab")

        # Check if the pressed key combination matches one of our dynamic shortcuts.
        if accel_string and accel_string == next_tab_shortcut:
            self.tab_manager.select_next_tab()
            return Gdk.EVENT_STOP  # Stop the event from reaching the terminal.
        if accel_string and accel_string == prev_tab_shortcut:
            self.tab_manager.select_previous_tab()
            return Gdk.EVENT_STOP  # Stop the event from reaching the terminal.

        # Keep the existing Alt+Number logic for quick tab switching.
        if state & Gdk.ModifierType.ALT_MASK:
            key_to_index = {
                Gdk.KEY_1: 0,
                Gdk.KEY_2: 1,
                Gdk.KEY_3: 2,
                Gdk.KEY_4: 3,
                Gdk.KEY_5: 4,
                Gdk.KEY_6: 5,
                Gdk.KEY_7: 6,
                Gdk.KEY_8: 7,
                Gdk.KEY_9: 8,
                Gdk.KEY_0: 9,
            }
            if keyval in key_to_index:
                index = key_to_index[keyval]
                if index < self.tab_manager.get_tab_count():
                    self.tab_manager.set_active_tab(self.tab_manager.tabs[index])
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Handle changes from the settings manager."""
        if key == "gtk_theme":
            # MODIFIED: This is the correct place to handle theme changes for the window.
            style_manager = Adw.StyleManager.get_default()
            if new_value == "light":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif new_value in ["dark", "terminal"]:
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:  # "default"
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

            if new_value == "terminal":
                self.settings_manager.apply_gtk_terminal_theme(self)
            else:
                self.settings_manager.remove_gtk_terminal_theme(self)

            # Always re-apply headerbar transparency as the base theme might have changed
            self.settings_manager.apply_headerbar_transparency(self.header_bar)

        elif key == "auto_hide_sidebar":
            self.sidebar_manager.handle_auto_hide_change(new_value)
        elif key == "tab_alignment":
            self.tab_manager._update_tab_alignment()
        elif key in [
            "font",
            "color_scheme",
            "transparency",
            "headerbar_transparency",
            "line_spacing",
            "bold_is_bright",
            "cursor_shape",
            "cursor_blink",
            "text_blink_mode",
            "bidi_enabled",
            "sixel_enabled",
            "accessibility_enabled",
            "backspace_binding",
            "delete_binding",
            "cjk_ambiguous_width",
        ]:
            self.terminal_manager.apply_settings_to_all_terminals()
            if (
                key == "color_scheme"
                and self.settings_manager.get("gtk_theme") == "terminal"
            ):
                self.settings_manager.apply_gtk_terminal_theme(self)
            if key in ["transparency", "headerbar_transparency"]:
                self._update_file_manager_transparency()
            if self.font_sizer_widget and key == "font":
                self.font_sizer_widget.update_display()

    def _on_color_scheme_changed(self, dialog, idx):
        """Handle color scheme changes from the dialog."""
        self.terminal_manager.apply_settings_to_all_terminals()
        if self.settings_manager.get("gtk_theme") == "terminal":
            self.settings_manager.apply_gtk_terminal_theme(self)

    def _on_session_activated(self, session: SessionItem) -> None:
        is_valid, errors = validate_session_data(session.to_dict())
        if not is_valid:
            self._show_error_dialog(
                _("Session Validation Error"),
                _("Session validation failed:\n{errors}").format(
                    errors="\n".join(errors)
                ),
            )
            return
        if session.is_local():
            self.tab_manager.create_local_tab(session=session)
        else:
            self.tab_manager.create_ssh_tab(session)

    def _on_terminal_focus_changed(self, terminal, _from_sidebar: bool) -> None:
        page = self.tab_manager.get_page_for_terminal(terminal)
        if not page or not hasattr(page, "content_paned"):
            return

        if page.content_paned.get_end_child():
            fm = self.tab_manager.file_managers.get(page)
            if fm:
                fm.rebind_terminal(terminal)

        # Hide search if focus changes to a different terminal
        self._hide_search_if_terminal_changed()

        # Trigger a title update to reflect the newly focused pane
        self.terminal_manager._update_title(terminal)

    def _on_tab_changed(self, view_stack, _param):
        """Handle tab changes."""
        if not self.tab_manager.active_tab:
            return

        # Hide search when switching tabs
        self._hide_search_if_terminal_changed()

        self._sync_toggle_button_state()
        self._update_font_sizer_widget()
        self._update_tab_layout()

    def _on_toggle_file_manager(self, button: Gtk.ToggleButton):
        """Toggle file manager for the current tab."""
        self.tab_manager.toggle_file_manager_for_active_tab(button.get_active())

    def _on_temp_files_changed(self, file_manager, count, page):
        """Handle signal from a FileManager about its temp file count."""
        if count > 0:
            self.active_temp_files[file_manager] = count
        elif file_manager in self.active_temp_files:
            del self.active_temp_files[file_manager]

        self._update_cleanup_button_visibility()
        self._populate_cleanup_popover()

    def _on_detach_tab_requested(self, page_to_detach: Adw.ViewStackPage):
        """Orchestrates detaching a tab into a new window."""
        if self.tab_manager.get_tab_count() <= 1:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Cannot detach the last tab."))
            )
            return

        tab_widget = next(
            (
                tab
                for tab in self.tab_manager.tabs
                if self.tab_manager.pages.get(tab) == page_to_detach
            ),
            None,
        )
        if not tab_widget:
            return

        fm_to_detach = self.tab_manager.file_managers.pop(page_to_detach, None)

        # Collect and deregister all terminals from the tab
        terminals_to_move = []
        terminals_in_page = self.tab_manager.get_all_terminals_in_page(page_to_detach)
        for terminal in terminals_in_page:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id:
                terminal_info = (
                    self.terminal_manager.registry.deregister_terminal_for_move(
                        terminal_id
                    )
                )
                if terminal_info:
                    terminals_to_move.append({
                        "id": terminal_id,
                        "info": terminal_info,
                        "widget": terminal,
                    })

        content = page_to_detach.get_child()
        title = tab_widget._base_title
        session = getattr(tab_widget, "session_item", None)
        session_type = session.session_type if session else "local"

        self.tab_manager.view_stack.remove(content)
        self.tab_manager.tab_bar_box.remove(tab_widget)
        self.tab_manager.tabs.remove(tab_widget)
        del self.tab_manager.pages[tab_widget]

        if self.tab_manager.active_tab == tab_widget and self.tab_manager.tabs:
            self.tab_manager.set_active_tab(self.tab_manager.tabs[-1])
        elif not self.tab_manager.tabs:
            self.tab_manager.active_tab = None
            if self.get_application():
                self.close()

        app = self.get_application()
        new_window = app.create_new_window(
            _is_for_detached_tab=True,
            detached_terminals_data=terminals_to_move,
            detached_file_manager=fm_to_detach,
        )
        new_window.tab_manager.re_attach_detached_page(
            content, title, session_type, fm_to_detach
        )

        new_window._update_tab_layout()
        new_window.present()

    # --- Search Handlers ---

    def _on_search_mode_changed(self, search_bar, param):
        if search_bar.get_search_mode():
            self.terminal_search_entry.grab_focus()
            # Set current terminal when search mode is enabled
            current_terminal = self.tab_manager.get_selected_terminal()
            if current_terminal:
                self.current_search_terminal = current_terminal
                self.search_active = True
        else:
            self._on_search_stop(self.terminal_search_entry)

    def _on_case_sensitive_changed(self, switch, param):
        """Handle case sensitive switch changes."""
        case_sensitive = switch.get_active()
        self.settings_manager.set("search_case_sensitive", case_sensitive)
        # Re-trigger search if there's text in the search entry
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _on_regex_changed(self, switch, param):
        """Handle regex switch changes."""
        use_regex = switch.get_active()
        self.settings_manager.set("search_use_regex", use_regex)
        # Re-trigger search if there's text in the search entry
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _update_search_occurrence_display(self):
        """Update the search occurrence counter display."""
        if self.search_active and self.search_current_occurrence > 0:
            text = f"{self.search_current_occurrence}"
        else:
            text = ""
        self.search_occurrence_label.set_text(text)

    def _hide_search_if_terminal_changed(self):
        """Hide search if the current terminal is different from when search was started."""
        current_terminal = self.tab_manager.get_selected_terminal()
        if self.search_active and current_terminal != self.current_search_terminal:
            self.search_bar.set_search_mode(False)
            self.search_active = False
            self.current_search_terminal = None
            self._update_search_occurrence_display()

    def _on_search_text_changed(self, search_entry):
        """Handle search text changes immediately."""
        text = search_entry.get_text()

        # If text is empty, clear search immediately
        if not text:
            terminal = self.tab_manager.get_selected_terminal()
            if terminal:
                terminal.search_set_regex(None, 0)
            self.search_active = False
            self.current_search_terminal = None
            self.search_current_occurrence = 0
            self._update_search_occurrence_display()
            return

        # Perform the search immediately
        self._perform_search(text)

    def _search_from_beginning(self, terminal, regex):
        """Search from the beginning of the terminal content."""
        try:
            # Try to find the first match from the beginning
            found = terminal.search_find_next()
            if found:
                # Scroll to show the first match
                v_adjustment = terminal.get_vadjustment()
                if v_adjustment:
                    # Get the position of the match and scroll to it
                    match_col, match_row = terminal.get_cursor_position()
                    # Scroll to show the match
                    v_adjustment.set_value(max(0, match_row - 5))  # Show some context
            return found
        except Exception as e:
            self.logger.debug(f"Error searching from beginning: {e}")
            return False

    def _perform_search(self, text):
        """Perform search immediately for the given text."""
        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            self.logger.warning("Search triggered but no active terminal found.")
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("No active terminal to search in."))
            )
            return

        # Update search state
        self.current_search_terminal = terminal
        self.search_active = True

        # First, clear any existing search to reset the state completely
        terminal.search_set_regex(None, 0)

        try:
            # Use Vte.Regex for search operations
            # PCRE2_MULTILINE (0x00000400) and optionally PCRE2_CASELESS (0x00000008)
            pcre2_flags = 0x00000400  # PCRE2_MULTILINE always enabled
            if not self.settings_manager.get("search_case_sensitive", False):
                pcre2_flags |= 0x00000008  # PCRE2_CASELESS for case insensitive

            use_regex = self.settings_manager.get("search_use_regex", False)
            search_text = text

            if not use_regex:
                # For literal search, escape regex special characters
                import re

                search_text = re.escape(text)

            # Always use new_for_search, but with escaped pattern for literal mode
            regex = Vte.Regex.new_for_search(search_text, -1, pcre2_flags)

            if not regex:
                self.logger.warning(
                    f"Failed to compile search regex for pattern: {text}"
                )
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("Invalid search pattern."))
                )
                return

            # Completely clear search state before setting new regex
            terminal.search_set_regex(None, 0)

            # Set the regex for search FIRST - establish working search
            terminal.search_set_regex(regex, 0)

            # More robust search: try multiple approaches to ensure consistency
            found = False

            # Method 1: Try from current position first
            current_col, current_row = terminal.get_cursor_position()
            found = terminal.search_find_next()

            # Method 2: If not found from current position, search from beginning of scrollback
            if not found:
                try:
                    # Save current scroll position
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the very beginning (including scrollback)
                        v_adjustment.set_value(0.0)

                        # Try searching from beginning immediately
                        found = self._search_from_beginning(terminal, regex)

                except Exception as e:
                    self.logger.debug(f"Error during scroll-based search: {e}")
                    # Fallback to just trying again from current position
                    found = terminal.search_find_next()

            # Count occurrences using the EXACT same regex object
            if found:
                # Just set current occurrence to 1 since we found at least one match
                self.search_current_occurrence = 1
                self._update_search_occurrence_display()
            else:
                self.search_current_occurrence = 0
                self._update_search_occurrence_display()

        except GLib.Error as e:
            self.logger.error(
                f"Invalid regex for search pattern '{text}': {e.message}", exc_info=True
            )
            self.toast_overlay.add_toast(Adw.Toast(title=_("Invalid search pattern.")))
            terminal.search_set_regex(None, 0)

    def _on_search_next(self, button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal and self.search_active:
            # Try to find next match
            found = terminal.search_find_next()

            # If no match found from current position, try wrapping to beginning
            if not found:
                try:
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the beginning and wrap around
                        v_adjustment.set_value(0.0)
                        found = terminal.search_find_next()

                        if found:
                            self.search_current_occurrence = 1  # Wrapped to first match
                except Exception as e:
                    self.logger.debug(f"Error during next search: {e}")
            elif found:
                # Increment current occurrence
                self.search_current_occurrence += 1

            if not found:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("No more matches found."))
                )
            else:
                self._update_search_occurrence_display()

    def _on_search_previous(self, button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal and self.search_active:
            # Try to find previous match
            found = terminal.search_find_previous()

            # If no match found, try wrapping to end
            if not found:
                try:
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the end and wrap around
                        v_adjustment.set_value(
                            v_adjustment.get_upper() - v_adjustment.get_page_size()
                        )
                        found = terminal.search_find_previous()

                        if found:
                            self.search_current_occurrence = 1  # Wrapped to match
                except Exception as e:
                    self.logger.debug(f"Error during previous search: {e}")
            elif found:
                # Decrement current occurrence
                if self.search_current_occurrence > 1:
                    self.search_current_occurrence -= 1

            if not found:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("No more matches found."))
                )
            else:
                self._update_search_occurrence_display()

    def _on_search_stop(self, search_entry):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            terminal.search_set_regex(None, 0)
            terminal.grab_focus()

        # Reset search state
        self.search_active = False
        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self._update_search_occurrence_display()

    # --- Window Lifecycle and State ---

    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab, trying to restore session first."""
        try:
            if not self.state_manager.restore_session_state():
                if self.tab_manager.get_tab_count() == 0:
                    if self.initial_ssh_target:
                        self.create_ssh_tab(self.initial_ssh_target)
                    else:
                        self.tab_manager.create_initial_tab_if_empty(
                            working_directory=self.initial_working_directory,
                            execute_command=self.initial_execute_command,
                            close_after_execute=self.close_after_execute,
                        )
        except Exception as e:
            self.logger.error(f"Failed to create initial tab: {e}")
            self._show_error_dialog(
                _("Initialization Error"),
                _("Failed to initialize terminal: {error}").format(error=str(e)),
            )
        return False

    def _on_window_close_request(self, window) -> bool:
        self.logger.info("Window close request received")
        if self._force_closing:
            return Gdk.EVENT_PROPAGATE

        if len(self.get_application().get_windows()) == 1:
            policy = self.settings_manager.get("session_restore_policy", "never")
            if policy == "ask":
                self._show_save_session_dialog()
                return Gdk.EVENT_STOP
            elif policy == "always":
                self.state_manager.save_session_state()
            else:
                self.state_manager.clear_session_state()

        return self._continue_close_process()

    def _show_save_session_dialog(self):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Save Current Session?"),
            body=_(
                "Do you want to restore these tabs the next time you open Ashy Terminal?"
            ),
            close_response="cancel",
        )
        dialog.add_response("dont-save", _("Don't Save"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save and Close"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        dialog.connect("response", self._on_save_session_dialog_response)
        dialog.present()

    def _on_save_session_dialog_response(self, dialog, response_id):
        dialog.close()
        if response_id == "save":
            self.state_manager.save_session_state()
            self._continue_close_process(force_close=True)
        elif response_id == "dont-save":
            self.state_manager.clear_session_state()
            self._continue_close_process(force_close=True)

    def _continue_close_process(self, force_close=False) -> bool:
        if self.terminal_manager.has_active_ssh_sessions():
            self._show_window_ssh_close_confirmation()
            return Gdk.EVENT_STOP

        self._perform_cleanup()
        if force_close:
            self._force_closing = True
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _show_window_ssh_close_confirmation(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Close Window"),
            body=_(
                "This window has active SSH connections. Closing will disconnect them. Are you sure?"
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("close", _("Close Window"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_response(dlg, response_id):
            if response_id == "close":
                self._force_closing = True
                self._perform_cleanup()
                self.close()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _perform_cleanup(self) -> None:
        if self._cleanup_performed:
            return
        self._cleanup_performed = True
        self.logger.info("Performing window cleanup")
        self.terminal_manager.cleanup_all_terminals()

        if self.settings_manager.get("clear_remote_edit_files_on_exit", False):
            self.logger.info("Clearing all temporary remote edit files on exit.")
            for fm in self.tab_manager.file_managers.values():
                fm.cleanup_all_temp_files()

        for fm in self.tab_manager.file_managers.values():
            fm.shutdown(None)

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    # --- Public API & Helpers ---

    def create_local_tab(self, working_directory=None):
        """Public method to create a local tab."""
        self.tab_manager.create_local_tab(working_directory=working_directory)

    def create_ssh_tab(self, ssh_target: str):
        """Public method to parse an SSH target string and create a tab."""
        try:
            # Robust parsing for [user@]host[:port][/path]
            remote_path = None
            if "/" in ssh_target:
                connection_part, remote_path_part = ssh_target.split("/", 1)
                remote_path = "/" + remote_path_part
            else:
                connection_part = ssh_target

            user_host_port = connection_part
            if "@" in connection_part:
                user, user_host_port = connection_part.split("@", 1)
            else:
                user = ""

            if ":" in user_host_port:
                host, port_str = user_host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host = user_host_port
                port = 22

            session_name = f"{user}@{host}" if user else host

            session = SessionItem(
                name=session_name, session_type="ssh", user=user, host=host, port=port
            )
            initial_command = f"cd '{remote_path}'" if remote_path else None
            self.tab_manager.create_ssh_tab(session, initial_command=initial_command)
        except Exception as e:
            self.logger.error(f"Failed to parse SSH target '{ssh_target}': {e}")
            self._show_error_dialog(
                _("Invalid SSH Target"),
                _("Could not parse the provided SSH connection string."),
            )

    def create_execute_tab(
        self, command: str, working_directory: str, close_after: bool
    ):
        """Public method to create a tab that executes a command."""
        self.tab_manager.create_local_tab(
            working_directory=working_directory,
            execute_command=command,
            close_after_execute=close_after,
        )

    def refresh_tree(self) -> None:
        self.session_tree.refresh_tree()
        self.sidebar_manager.update_sidebar_sizes()

    def _update_tab_layout(self):
        """Update tab layout and window title based on tab count."""
        tab_count = self.tab_manager.get_tab_count()
        self.set_title(_("Ashy Terminal"))

        if tab_count > 1:
            self.title_stack.set_visible_child_name("tabs-view")
        else:
            self.title_stack.set_visible_child_name("title-view")
            if tab_count == 1:
                page = self.tab_manager.view_stack.get_page(
                    self.tab_manager.view_stack.get_visible_child()
                )
                if page:
                    self.single_tab_title_widget.set_title(
                        f"{_('Ashy Terminal')} - {page.get_title()}"
                    )
            else:
                self.single_tab_title_widget.set_title(_("Ashy Terminal"))

    def _update_font_sizer_widget(self):
        if self.font_sizer_widget:
            self.font_sizer_widget.update_display()

    def _sync_toggle_button_state(self):
        """Synchronize toggle button state with file manager visibility."""
        if not self.tab_manager.active_tab:
            self.file_manager_button.set_active(False)
            return

        page = self.tab_manager.pages.get(self.tab_manager.active_tab)
        if page and hasattr(page, "content_paned"):
            is_visible = page.content_paned.get_end_child() is not None
            if self.file_manager_button.get_active() != is_visible:
                self.file_manager_button.set_active(is_visible)
        else:
            self.file_manager_button.set_active(False)

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _on_terminal_exit(self, terminal, child_status, identifier):
        pass

    def _on_quit_application_requested(self) -> None:
        """Handle quit request from tab manager."""
        if app := self.get_application():
            app.quit()
        else:
            self.destroy()

    def _update_file_manager_transparency(self):
        """Update transparency for all file managers when settings change."""
        # Apply headerbar transparency to main window
        if hasattr(self, "header_bar"):
            self.settings_manager.apply_headerbar_transparency(self.header_bar)

        for file_manager in self.tab_manager.file_managers.values():
            try:
                file_manager._apply_background_transparency()
                # Also update headerbar transparency for any open dialogs
                if (
                    hasattr(file_manager, "transfer_history_window")
                    and file_manager.transfer_history_window
                ):
                    self.settings_manager.apply_headerbar_transparency(
                        file_manager.transfer_history_window.header_bar
                    )
            except Exception as e:
                self.logger.warning(f"Failed to update file manager transparency: {e}")

    def _on_new_tab_clicked(self, _button) -> None:
        self.action_handler.new_local_tab(None, None)

    def _update_cleanup_button_visibility(self):
        """Show or hide the cleanup button based on the total count of temp files."""
        total_count = sum(self.active_temp_files.values())
        self.cleanup_button.set_visible(total_count > 0)

    def _populate_cleanup_popover(self):
        """Dynamically build the list of temporary files for the popover."""
        if self.cleanup_popover.get_child():
            self.cleanup_popover.set_child(None)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=10,
            margin_bottom=10,
            margin_start=10,
            margin_end=10,
        )
        scrolled = Gtk.ScrolledWindow(
            propagate_natural_height=True, propagate_natural_width=True
        )
        content_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled.set_child(content_container)

        has_files = False
        all_files = []
        for fm in self.tab_manager.file_managers.values():
            all_files.extend(fm.get_temp_files_info())

        if all_files:
            has_files = True
            group = Adw.PreferencesGroup()
            content_container.append(group)
            for info in all_files:
                row = Adw.ActionRow(
                    title=info["remote_path"], subtitle=info["session_name"]
                )
                row.set_title_selectable(True)
                remove_button = Gtk.Button(
                    icon_name="edit-delete-symbolic",
                    css_classes=["flat", "circular"],
                    tooltip_text=_("Remove this temporary file"),
                )

                # Find the correct file manager instance to call cleanup on
                fm_to_call = next(
                    (
                        fm
                        for fm in self.tab_manager.file_managers.values()
                        if fm.session_item.name == info["session_name"]
                    ),
                    None,
                )

                if fm_to_call:
                    edit_key = (info["session_name"], info["remote_path"])
                    remove_button.connect(
                        "clicked",
                        lambda _,
                        fm_instance=fm_to_call,
                        key=edit_key: self._on_clear_single_temp_file_clicked(
                            fm_instance, key
                        ),
                    )
                row.add_suffix(remove_button)
                group.add(row)

        if not has_files:
            content_container.append(Gtk.Label(label=_("No temporary files found.")))

        clear_button = Gtk.Button(
            label=_("Clear All Temporary Files"),
            css_classes=["destructive-action", "pill"],
            halign=Gtk.Align.CENTER,
            margin_top=10,
        )
        clear_button.connect("clicked", self._on_clear_all_temp_files_clicked)
        clear_button.set_sensitive(has_files)

        box.append(scrolled)
        box.append(clear_button)
        self.cleanup_popover.set_child(box)

    def _on_clear_single_temp_file_clicked(self, file_manager, edit_key):
        """Callback to clear a single temporary file and its directory."""
        file_manager.cleanup_all_temp_files(edit_key)
        self._populate_cleanup_popover()

    def _on_clear_all_temp_files_clicked(self, button):
        """Show confirmation and then clear all temp files."""
        self.cleanup_popover.popdown()
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Clear All Temporary Files?"),
            body=_(
                "This will remove all locally downloaded files for remote editing. "
                "Any unsaved changes in your editor will be lost. This action cannot be undone."
            ),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear All"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_clear_all_confirm)
        dialog.present()

    def _on_clear_all_confirm(self, dialog, response_id):
        dialog.close()
        if response_id == "clear":
            self.logger.info("User confirmed clearing all temporary files.")
            for fm in self.tab_manager.file_managers.values():
                fm.cleanup_all_temp_files()

    def _on_tab_bar_scroll(self, controller, dx, dy):
        """Handles scroll events on the tab bar to move it horizontally."""
        adjustment = self.scrolled_tab_bar.get_hadjustment()
        if not adjustment:
            return Gdk.EVENT_PROPAGATE
        delta = dy + dx

        scroll_amount = delta * 30

        current_value = adjustment.get_value()
        new_value = current_value + scroll_amount

        lower = adjustment.get_lower()
        upper = adjustment.get_upper() - adjustment.get_page_size()
        new_value = max(lower, min(new_value, upper))

        adjustment.set_value(new_value)

        return Gdk.EVENT_STOP

    def _show_command_guide_dialog(self):
        """Creates and shows the command guide dialog, or closes it if already visible."""
        if self.command_guide_dialog is None:
            self.command_guide_dialog = CommandGuideDialog(self)
            self.command_guide_dialog.connect(
                "command-selected", self._on_command_selected_from_guide
            )
        if self.command_guide_dialog.get_visible():
            self.command_guide_dialog.close()
        else:
            self.command_guide_dialog.present()

    def _on_command_selected_from_guide(self, dialog, command_text):
        """Callback for when a command is selected from the guide."""
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            # Use bracketed paste to insert command without auto-executing
            if "\n" in command_text:
                command_text += "\n"
            paste_data = b"\x1b[200~" + command_text.encode("utf-8") + b"\x1b[201~"
            terminal.feed_child(paste_data)
            # Clear selection after pasting
            terminal.feed_child(b"\x1b[C")  # Right arrow to deselect
            terminal.grab_focus()
        else:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("No active terminal to send command to."))
            )
