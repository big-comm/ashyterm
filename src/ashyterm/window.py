# ashyterm/window.py

import threading
import weakref
from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from .sessions.models import LayoutItem, SessionFolder, SessionItem
from .sessions.operations import SessionOperations

# Lazy import: from .sessions.storage import load_folders_to_store, load_sessions_and_folders, load_sessions_to_store
from .sessions.tree import SessionTreeView
from .settings.manager import SettingsManager
from .state.window_state import WindowStateManager
from .terminal.ai_assistant import TerminalAiAssistant
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .ui.actions import WindowActions
from .ui.sidebar_manager import SidebarManager
from .ui.window_ui import WindowUIBuilder
from .ui.search_manager import SearchManager
from .ui.broadcast_manager import BroadcastManager
from .utils.exceptions import UIError
from .utils.icons import icon_image
from .utils.logger import get_logger
from .utils.security import validate_session_data
from .utils.syntax_utils import get_bash_pango_markup
from .utils.translation_utils import _

# Constants
APP_TITLE = _("Ashy Terminal")
MSG_NO_ACTIVE_TERMINAL = _("No active terminal to send command to.")
# Bracketed paste mode escape sequences
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


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
        self.command_manager_dialog = None  # For Command Manager dialog

        # Search state tracking
        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self.search_active = False

        # Active modal dialog counter for safe focus management
        self.active_modals_count = 0

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
        self.set_title(APP_TITLE)
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
                            tooltip_text=_("Close Pane"),
                        )
                        new_close_button.set_child(icon_image("window-close-symbolic"))
                        new_close_button.add_css_class("flat")
                        new_close_button.connect(
                            "clicked",
                            lambda _, term=terminal_widget: self.tab_manager.close_pane(
                                term
                            ),
                        )

                        new_move_button = Gtk.Button(
                            tooltip_text=_("Move to New Tab"),
                        )
                        new_move_button.set_child(
                            icon_image("select-rectangular-symbolic")
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

        # NOTE: Initial tab creation is deferred to _on_window_mapped()
        # This prevents the duplicate prompt issue caused by resize SIGWINCH
        # when the terminal is created before the window has its final dimensions.

        # Apply visual settings immediately to ensure correct appearance on startup
        self._apply_initial_visual_settings()

        # Deferred initialization for data loading
        def _deferred_init():
            if not self._is_for_detached_tab:
                self._load_initial_data()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_deferred_init)

        self.logger.info("Main window initialization completed")

    # NEW: Method to apply all visual settings on window creation.
    def _apply_initial_visual_settings(self) -> None:
        """Applies all visual settings upon window creation."""
        self.logger.info("Applying initial visual settings to new window.")
        # Apply theme first, as it might affect colors used by other settings.
        self.settings_manager._update_app_theme_css(self)

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
        # Start terminal pre-creation in background for faster first tab
        if not self._is_for_detached_tab:
            self.terminal_manager.prepare_initial_terminal()
        self.ai_assistant = TerminalAiAssistant(
            self, self.settings_manager, self.terminal_manager
        )
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

        # Search and Broadcast Managers
        self.search_manager = SearchManager(self)
        self.broadcast_manager = BroadcastManager(self)

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
        self.cleanup_button = self.ui_builder.cleanup_button
        self.cleanup_popover = self.ui_builder.cleanup_popover
        self.font_sizer_widget = self.ui_builder.font_sizer_widget
        self.scrolled_tab_bar = self.ui_builder.scrolled_tab_bar
        self.single_tab_title_widget = self.ui_builder.single_tab_title_widget
        self.title_stack = self.ui_builder.title_stack
        self.toast_overlay = self.ui_builder.toast_overlay
        self.search_bar = self.ui_builder.search_bar
        self.search_button = self.ui_builder.search_button
        self.broadcast_bar = self.ui_builder.broadcast_bar
        self.broadcast_button = self.ui_builder.broadcast_button
        self.broadcast_entry = self.ui_builder.broadcast_entry
        # Assign the correctly named widgets
        self.terminal_search_entry = self.ui_builder.terminal_search_entry
        self.search_entry = self.ui_builder.sidebar_search_entry
        self.search_prev_button = self.ui_builder.search_prev_button
        self.search_next_button = self.ui_builder.search_next_button
        self.search_occurrence_label = self.ui_builder.search_occurrence_label
        self.case_sensitive_switch = self.ui_builder.case_sensitive_switch
        self.regex_switch = self.ui_builder.regex_switch
        self.command_toolbar = self.ui_builder.command_toolbar
        self.tab_manager.scrolled_tab_bar = self.scrolled_tab_bar

        # NOTE: Headerbar and theme styling is now handled in _apply_initial_visual_settings
        # to avoid redundant CSS applications during initialization

    def _connect_component_signals(self) -> None:
        """
        Connects signals and callbacks between the window and its managers.
        """
        self._setup_actions()
        self._setup_keyboard_shortcuts()

        self.session_tree.on_session_activated = self._on_session_activated
        self.session_tree.on_layout_activated = self.state_manager.restore_saved_layout
        self.session_tree.on_folder_expansion_changed = (
            self.sidebar_manager.update_sidebar_sizes
        )
        self.terminal_manager.on_terminal_focus_changed = (
            self._on_terminal_focus_changed
        )
        self.terminal_manager.set_terminal_exit_handler(self._on_terminal_exit)
        self.terminal_manager.set_ssh_file_drop_callback(self._on_ssh_file_dropped)
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

    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        self.connect("close-request", self._on_window_close_request)
        # Connect window state change signals
        self.connect("notify::default-width", self._on_window_size_changed)
        self.connect("notify::default-height", self._on_window_size_changed)
        self.connect("notify::maximized", self._on_window_maximized_changed)

        # Defer terminal creation until window is mapped (has final dimensions)
        # This prevents the duplicate prompt issue caused by resize SIGWINCH
        if not self._is_for_detached_tab:
            self._initial_tab_created = False
            self.connect("map", self._on_window_mapped)

        # DEBUG: Start periodic modal window monitor
        if self.settings_manager.get("debug_mode", False):
            self._start_modal_window_monitor()

    def _start_modal_window_monitor(self) -> None:
        """Start periodic logging of all modal windows for debugging."""

        def log_modal_windows():
            modal_windows = []
            for window in Gtk.Window.list_toplevels():
                if window == self:
                    continue
                try:
                    is_modal = hasattr(window, "get_modal") and window.get_modal()
                    is_visible = (
                        window.is_visible() if hasattr(window, "is_visible") else False
                    )
                    transient = window.get_transient_for()

                    if is_modal or transient == self:
                        modal_windows.append(
                            {
                                "class": window.__class__.__name__,
                                "title": window.get_title()
                                if hasattr(window, "get_title")
                                else "N/A",
                                "modal": is_modal,
                                "visible": is_visible,
                                "transient_for": transient.__class__.__name__
                                if transient
                                else None,
                            }
                        )
                except Exception as e:
                    self.logger.debug(f"Error inspecting window: {e}")

            if modal_windows:
                self.logger.warning(
                    f"[MODAL_DEBUG] Active modal/transient windows: {modal_windows}"
                )

            return True  # Continue polling

        # Poll every 2 seconds
        GLib.timeout_add_seconds(2, log_modal_windows)
        self.logger.info("[MODAL_DEBUG] Modal window monitor started (debug_mode=True)")

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

    def _on_window_size_changed(self, window, _param_spec) -> None:
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

    def _on_window_maximized_changed(self, window, _param_spec) -> None:
        """Handle window maximized state changes to save to settings."""
        if not self.settings_manager.get("remember_window_state", True):
            return

        maximized = self.is_maximized()
        self.settings_manager.set("window_maximized", maximized)

    def _on_window_mapped(self, window) -> None:
        """
        Handle window map signal - create initial tab after window has final dimensions.

        This is crucial to prevent the duplicate prompt issue: when the terminal
        is spawned before the window is mapped, the shell receives a SIGWINCH
        (window change) signal during resize which causes it to redraw the prompt.

        By waiting until the window is mapped, we ensure:
        1. The window has its final dimensions
        2. The terminal PTY is created with the correct size
        3. No resize SIGWINCH is sent to the shell during initialization
        """
        # Re-apply headerbar transparency on map to ensure it takes effect
        if hasattr(self, "header_bar"):
            self.settings_manager._update_app_theme_css(self)

        if self._initial_tab_created:
            return

        self._initial_tab_created = True
        self.logger.debug("Window mapped - creating initial tab with final dimensions")

        # Small delay to ensure window size is fully settled
        # This is especially important when the window is maximized
        def create_tab_deferred():
            self._create_initial_tab_safe()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(create_tab_deferred)

    def _load_initial_data_and_tab(self) -> None:
        """
        Loads initial data and then creates the initial tab.
        Called from deferred initialization to allow the UI to show up immediately.
        """
        # Create the tab first (faster perceived startup)
        self._create_initial_tab_safe()
        # Then load session data (can happen while user sees the terminal)
        self._load_initial_data()

    def _load_initial_data(self) -> None:
        """Load initial sessions, folders, and layouts data."""

        # ASYNC LOADING: Move heavy JSON parsing to background thread
        def load_data_background():
            try:
                # Lazy import - these are only needed during data loading
                from .sessions.storage import load_sessions_and_folders

                # This happens in the thread - safe if it just reads files
                self.state_manager.load_layouts()
                sessions_data, folders_data = load_sessions_and_folders()

                # Schedule store update on main thread
                GLib.idle_add(
                    self._update_stores_with_data, sessions_data, folders_data
                )
            except Exception as e:
                # Schedule error handling on main thread
                GLib.idle_add(self._handle_load_error, e)

        # Start background loading
        threading.Thread(target=load_data_background, daemon=True).start()

    def _update_stores_with_data(self, sessions_data, folders_data):
        """Callback to update stores on main thread after background load."""
        try:
            from .sessions.storage import (
                load_folders_to_store,
                load_sessions_to_store,
            )

            load_sessions_to_store(self.session_store, sessions_data)
            load_folders_to_store(self.folder_store, folders_data)
            self.refresh_tree()

            # SSH import can be fast, or we can background it too.
            # For now, keeping it here as it might depend on implementation details.
            # If it parses files, better to background it, but let's see.
            import_result = self.session_operations.import_sessions_from_ssh_config()
            if import_result.success:
                self.logger.info(import_result.message)
                self.refresh_tree()
            elif import_result.message:
                self.logger.debug(f"SSH config import skipped: {import_result.message}")

            self.logger.info(
                f"Loaded {self.session_store.get_n_items()} sessions, "
                f"{self.folder_store.get_n_items()} folders, "
                f"and {len(self.layouts)} layouts"
            )
        except Exception as e:
            self._handle_load_error(e)

        return GLib.SOURCE_REMOVE

    def _handle_load_error(self, e):
        """Handle errors during data loading on main thread."""
        self.logger.error(f"Failed to load initial data: {e}")
        self._show_error_dialog(
            _("Data Loading Error"),
            _(
                "Failed to load saved sessions and folders. Starting with empty configuration."
            ),
        )
        return GLib.SOURCE_REMOVE

    # --- Event Handlers & Callbacks ---

    def _handle_escape_key(self, keyval) -> bool:
        """Handle Escape key to cancel tab move mode."""
        if keyval == Gdk.KEY_Escape:
            if self.tab_manager.cancel_tab_move_if_active():
                return True
        return False

    def _handle_search_shortcut(self, keyval, state) -> bool:
        """Handle Ctrl+Shift+F for search toggle."""
        is_ctrl_shift = (
            state & Gdk.ModifierType.CONTROL_MASK
            and state & Gdk.ModifierType.SHIFT_MASK
        )
        is_f_key = keyval == Gdk.KEY_f or keyval == Gdk.KEY_F
        if is_ctrl_shift and is_f_key:
            current_mode = self.search_bar.get_search_mode()
            self.search_bar.set_search_mode(not current_mode)
            if not current_mode:
                self.terminal_search_entry.grab_focus()
            return True
        return False

    def _handle_dynamic_shortcuts(self, accel_string: str) -> bool:
        """Handle dynamically configured shortcuts."""
        if not accel_string:
            return False
        shortcut_actions = {
            self.settings_manager.get_shortcut(
                "next-tab"
            ): self.tab_manager.select_next_tab,
            self.settings_manager.get_shortcut(
                "previous-tab"
            ): self.tab_manager.select_previous_tab,
            self.settings_manager.get_shortcut(
                "ai-assistant"
            ): self._on_ai_assistant_requested,
        }
        if action := shortcut_actions.get(accel_string):
            action()
            return True
        # Handle split shortcuts separately (require terminal check)
        split_h = self.settings_manager.get_shortcut("split-horizontal")
        split_v = self.settings_manager.get_shortcut("split-vertical")
        if accel_string in (split_h, split_v):
            if terminal := self.tab_manager.get_selected_terminal():
                if accel_string == split_h:
                    self.tab_manager.split_horizontal(terminal)
                else:
                    self.tab_manager.split_vertical(terminal)
            return True
        return False

    def _handle_alt_number_shortcuts(self, keyval, state) -> bool:
        """Handle Alt+Number for quick tab switching."""
        if not (state & Gdk.ModifierType.ALT_MASK):
            return False
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
            return True
        return False

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handles key press events for tab navigation and search."""
        # Emergency escape: Ctrl+Shift+Escape closes any ghost dialogs
        if self._handle_emergency_dialog_close(keyval, state):
            return Gdk.EVENT_STOP
        if self._handle_escape_key(keyval):
            return Gdk.EVENT_STOP
        if self._handle_search_shortcut(keyval, state):
            return Gdk.EVENT_STOP
        accel_string = Gtk.accelerator_name(
            keyval, state & Gtk.accelerator_get_default_mod_mask()
        )
        if self._handle_dynamic_shortcuts(accel_string):
            return Gdk.EVENT_STOP
        if self._handle_alt_number_shortcuts(keyval, state):
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _handle_emergency_dialog_close(self, keyval, state) -> bool:
        """Handle Ctrl+Shift+Escape to close any blocking dialogs."""
        ctrl_shift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if keyval == Gdk.KEY_Escape and (state & ctrl_shift) == ctrl_shift:
            self.logger.warning("Emergency dialog close triggered (Ctrl+Shift+Escape)")
            self._force_close_all_dialogs()
            return True
        return False

    def _force_close_all_dialogs(self):
        """Force close all dialogs and transient windows."""
        # 1. Force close Adw.Dialogs if available
        if hasattr(self, "get_dialogs"):
            dialogs = self.get_dialogs()
            for i in range(dialogs.get_n_items()):
                if dialog := dialogs.get_item(i):
                    self.logger.info(f"Force closing dialog: {dialog}")
                    dialog.force_close()

        # 2. Force close any other transient modal windows
        for window in Gtk.Window.list_toplevels():
            if (
                window.get_transient_for() == self
                and window != self
                and hasattr(window, "get_modal")
                and window.get_modal()
            ):
                self.logger.info(f"Force closing transient: {window}")
                window.set_modal(False)
                window.close()

    def _on_ai_assistant_requested(self, *_args) -> None:
        if not getattr(self, "ai_assistant", None):
            return

        if not self.settings_manager.get("ai_assistant_enabled", False):
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_(
                        "Enable the AI assistant in Preferences > Terminal > AI Assistant."
                    )
                )
            )
            return

        missing = self.ai_assistant.missing_configuration()
        if missing:
            labels = {
                "provider": _("Provider"),
                "model": _("Model"),
                "api_key": _("API key"),
                "base_url": _("Base URL"),
            }
            readable = ", ".join(labels.get(item, item) for item in missing)
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_("Configure {items} in AI Assistant settings.").format(
                        items=readable
                    )
                )
            )
            return

        # Toggle AI overlay panel instead of showing dialog
        self.ui_builder.toggle_ai_panel()

    def show_ai_response_dialog(
        self,
        terminal: Vte.Terminal,
        reply: str,
        commands: List[Dict[str, str]],
        _code_snippets: List[Dict[str, str]],
    ) -> None:
        dialog_dimensions = self._calculate_ai_dialog_dimensions(reply, commands)

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("AI Assistant"),
            body=_("Here is what I found."),
            close_response="close",
        )
        dialog.set_default_size(*dialog_dimensions)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")

        content_box = self._create_ai_dialog_content(reply, commands, terminal, dialog)
        dialog.set_extra_child(content_box)

        def on_dialog_response(dlg, _response_id):
            dlg.destroy()

        dialog.connect("response", on_dialog_response)
        dialog.present()

    def _calculate_ai_dialog_dimensions(
        self, reply: str, commands: List[Dict[str, str]]
    ) -> tuple:
        """Calculate dialog dimensions based on content."""
        reply_lines = reply.splitlines() or [reply]
        max_line_length = max(len(line) for line in reply_lines)
        total_lines = len(reply_lines)

        for item in commands:
            if isinstance(item, dict):
                command_text = (item.get("command") or "").strip()
                description_text = (item.get("description") or "").strip()
                max_line_length = max(
                    max_line_length, len(command_text), len(description_text)
                )
            elif isinstance(item, str):
                max_line_length = max(max_line_length, len(item))

        approx_width = max(780, min(1200, max_line_length * 7 + 320))
        base_height = 460 if total_lines < 10 else 500
        height = min(820, max(420, base_height))

        return int(approx_width), int(height)

    def _create_ai_dialog_content(
        self,
        reply: str,
        commands: List[Dict[str, str]],
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> Gtk.Box:
        """Create the content box for AI response dialog."""
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        self._add_reply_section(content_box, reply)
        if commands:
            self._add_commands_section(content_box, commands, terminal, dialog)

        return content_box

    def _create_info_block(
        self, content_box: Gtk.Box, title: str, margin_top: int = 0
    ) -> Gtk.Box:
        """Create a styled info block with title."""
        frame = Gtk.Frame()
        frame.add_css_class("card")
        frame.set_hexpand(True)
        if margin_top:
            frame.set_margin_top(margin_top)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(12)
        inner.set_margin_bottom(12)
        inner.set_margin_start(16)
        inner.set_margin_end(16)

        heading = Gtk.Label(label=title, halign=Gtk.Align.START)
        heading.add_css_class("heading")
        inner.append(heading)

        frame.set_child(inner)
        content_box.append(frame)
        return inner

    def _add_reply_section(self, content_box: Gtk.Box, reply: str) -> None:
        """Add the reply section to the dialog."""
        reply_box = self._create_info_block(content_box, _("Response"))
        reply_lines = reply.splitlines() or [reply]

        reply_view = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            hexpand=True,
            vexpand=True,
        )
        reply_view.add_css_class("monospace")
        reply_buffer = reply_view.get_buffer()
        reply_buffer.set_text(reply.strip())

        reply_scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        reply_scrolled.set_min_content_height(max(140, min(300, len(reply_lines) * 20)))
        reply_scrolled.set_child(reply_view)
        reply_box.append(reply_scrolled)

    def _add_commands_section(
        self,
        content_box: Gtk.Box,
        commands: List[Dict[str, str]],
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> None:
        """Add the commands section to the dialog."""
        commands_box = self._create_info_block(
            content_box, _("Suggested Commands"), margin_top=6
        )

        for command_info in commands:
            command_text, description = self._extract_command_info(command_info)
            if not command_text:
                continue

            row = self._create_command_row(command_text, description, terminal, dialog)
            commands_box.append(row)

    def _extract_command_info(self, command_info) -> tuple:
        """Extract command and description from command info."""
        if isinstance(command_info, dict):
            command_text = (command_info.get("command") or "").strip()
            description = (command_info.get("description") or "").strip()
        elif isinstance(command_info, str):
            command_text = command_info.strip()
            description = ""
        else:
            command_text = ""
            description = ""
        return command_text, description

    def _create_command_row(
        self,
        command_text: str,
        description: str,
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> Gtk.Box:
        """Create a row for a command in the dialog."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        info_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )

        highlighted_cmd = get_bash_pango_markup(command_text)
        command_label = Gtk.Label(
            label=f"<tt>{highlighted_cmd}</tt>",
            use_markup=True,
            halign=Gtk.Align.START,
            hexpand=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        info_box.append(command_label)

        if description:
            desc_label = Gtk.Label(
                label=description,
                halign=Gtk.Align.START,
                hexpand=True,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
            desc_label.add_css_class("dim-label")
            info_box.append(desc_label)

        row.append(info_box)

        run_button = Gtk.Button(label=_("Run"))
        run_button.connect(
            "clicked", self._on_ai_command_clicked, dialog, terminal, command_text
        )
        row.append(run_button)

        return row

    def _on_ai_command_clicked(
        self,
        _button: Gtk.Button,
        dialog: Adw.MessageDialog,
        terminal: Vte.Terminal,
        command: str,
    ) -> None:
        if self._execute_ai_command(terminal, command):
            dialog.destroy()

    def _execute_ai_command(self, terminal: Vte.Terminal, command: str) -> bool:
        command = (command or "").strip()
        if not command:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Command is empty, nothing to run."))
            )
            return False
        try:
            terminal.feed_child(f"{command}\n".encode("utf-8"))
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Command sent to the terminal."))
            )
            return True
        except Exception as exc:
            self.logger.error("Failed to execute AI command '%s': %s", command, exc)
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Failed to execute the command."))
            )
            return False

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Handle changes from the settings manager."""
        if getattr(self, "ai_assistant", None):
            self.ai_assistant.handle_setting_changed(key, old_value, new_value)

        if key == "ai_assistant_enabled":
            self._handle_ai_assistant_setting_change(new_value)
        elif key == "gtk_theme":
            self._handle_gtk_theme_change(new_value)
        elif key == "auto_hide_sidebar":
            self.sidebar_manager.handle_auto_hide_change(new_value)
        elif key == "tab_alignment":
            self.tab_manager._update_tab_alignment()
        elif self._is_terminal_appearance_key(key):
            self._handle_terminal_appearance_change(key)

        if key == "hide_headerbar_buttons_when_maximized":
            self.ui_builder._update_headerbar_buttons_visibility()

    def _handle_ai_assistant_setting_change(self, new_value) -> None:
        """Handle AI assistant enabled/disabled."""
        self.ui_builder.update_ai_button_visibility()
        if not new_value and self.ui_builder.is_ai_panel_visible():
            self.ui_builder.hide_ai_panel()

    def _handle_gtk_theme_change(self, new_value) -> None:
        """Handle GTK theme changes."""
        style_manager = Adw.StyleManager.get_default()
        if new_value == "light":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif new_value in ["dark", "terminal"]:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

        self.settings_manager._update_app_theme_css(self)

    def _is_terminal_appearance_key(self, key: str) -> bool:
        """Check if the key is a terminal appearance setting."""
        terminal_keys = {
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
        }
        return key in terminal_keys

    def _handle_terminal_appearance_change(self, key: str) -> None:
        """Handle terminal appearance setting changes."""
        self.terminal_manager.apply_settings_to_all_terminals()

        # Re-apply terminal theme if color scheme changes OR if headerbar transparency changes
        if key in ["color_scheme", "headerbar_transparency"]:
            self.settings_manager._update_app_theme_css(self)

        if key in ["transparency", "headerbar_transparency"]:
            self._update_file_manager_transparency()

        if self.font_sizer_widget and key == "font":
            self.font_sizer_widget.update_display()

    def _on_color_scheme_changed(self, dialog, idx):
        """Handle color scheme changes from the dialog."""
        self.terminal_manager.apply_settings_to_all_terminals()
        self.settings_manager._update_app_theme_css(self)

        # Refresh shell input highlighter to use new color scheme palette
        try:
            from .terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

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
        self.search_manager.hide_if_terminal_changed()

        # Trigger a title update to reflect the newly focused pane
        self.terminal_manager._update_title(terminal)

    def _on_tab_changed(self, view_stack, _param):
        """Handle tab changes."""
        if not self.tab_manager.active_tab:
            return

        # Hide search when switching tabs
        self.search_manager.hide_if_terminal_changed()

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
                    terminals_to_move.append(
                        {
                            "id": terminal_id,
                            "info": terminal_info,
                            "widget": terminal,
                        }
                    )

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

    # --- Broadcast Handlers ---

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

        # Check for multiple tabs - apply close policy
        tab_count = self.tab_manager.get_tab_count()
        if tab_count > 1:
            close_policy = self.settings_manager.get(
                "close_multiple_tabs_policy", "ask"
            )
            if close_policy == "ask":
                self._show_close_multiple_tabs_dialog()
                return Gdk.EVENT_STOP
            elif close_policy == "save_and_close":
                # Save tabs and close without asking
                self.state_manager.save_session_state()
                return self._continue_close_process()
            # "just_close" - proceed without saving or asking
            self.state_manager.clear_session_state()

        return self._continue_close_process()

    def _show_close_multiple_tabs_dialog(self) -> None:
        """Show dialog when closing with multiple tabs open."""
        tab_count = self.tab_manager.get_tab_count()
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Close {} Tabs?").format(tab_count),
            body=_(
                "You have {} open tabs. Would you like to save them to restore next time?"
            ).format(tab_count),
            close_response="cancel",
        )
        dialog.add_response("close", _("Close"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save and Close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        dialog.connect("response", self._on_close_multiple_tabs_dialog_response)
        dialog.present()

    def _on_close_multiple_tabs_dialog_response(self, dialog, response_id):
        """Handle response from close multiple tabs dialog."""
        dialog.close()
        if response_id == "save":
            self.state_manager.save_session_state()
            self._continue_close_process(force_close=True)
        elif response_id == "close":
            self.state_manager.clear_session_state()
            self._continue_close_process(force_close=True)
        # "cancel" - do nothing, window stays open

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

        # Clean up CSS providers to prevent memory leaks
        self.settings_manager.cleanup_css_providers(self)

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    # --- Public API & Helpers ---

    def create_local_tab(
        self,
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ):
        """Public method to create a local tab."""
        return self.tab_manager.create_local_tab(
            working_directory=working_directory,
            execute_command=execute_command,
            close_after_execute=close_after_execute,
        )

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
            return self.tab_manager.create_ssh_tab(
                session, initial_command=initial_command
            )
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
        return self.tab_manager.create_local_tab(
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
        self.set_title(APP_TITLE)

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
                        f"{APP_TITLE} - {page.get_title()}"
                    )
            else:
                self.single_tab_title_widget.set_title(APP_TITLE)

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
        if getattr(self, "ai_assistant", None):
            self.ai_assistant.clear_conversation_for_terminal(terminal)

    def _on_ssh_file_dropped(self, terminal_id, local_paths, session, ssh_target):
        """Handle files dropped on SSH terminal - open file manager and let it handle upload."""
        self.logger.info(
            f"SSH file drop: {len(local_paths)} files for terminal {terminal_id}"
        )

        terminal, page = self._get_terminal_and_page_for_drop(terminal_id)
        if not terminal or not page:
            return

        fm = self.tab_manager.file_managers.get(page)
        remote_dir = self._get_remote_dir_from_terminal(terminal)

        if self._can_upload_directly(fm):
            self._upload_files_directly(fm, local_paths, remote_dir)
        else:
            self._store_pending_drop_and_activate_fm(
                local_paths,
                remote_dir,
                session,
                ssh_target,
                terminal_id,
                page,
                terminal,
            )

    def _get_terminal_and_page_for_drop(self, terminal_id):
        """Get terminal and page for file drop operation."""
        terminal = self.terminal_manager.registry.get_terminal(terminal_id)
        if not terminal:
            self.logger.warning(f"Terminal {terminal_id} not found for file drop")
            return None, None

        page = self.tab_manager.get_page_for_terminal(terminal)
        if not page:
            self.logger.warning(f"Page not found for terminal {terminal_id}")
            return None, None

        return terminal, page

    def _get_remote_dir_from_terminal(self, terminal):
        """Try to get the current remote directory from the terminal."""
        from urllib.parse import unquote, urlparse

        try:
            uri = terminal.get_current_directory_uri()
            if uri:
                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    remote_dir = unquote(parsed_uri.path)
                    self.logger.info(f"Got remote directory from OSC7: {remote_dir}")
                    return remote_dir
        except Exception as e:
            self.logger.debug(f"Could not get terminal directory URI: {e}")
        return None

    def _can_upload_directly(self, fm) -> bool:
        """Check if file manager is ready for direct upload."""
        return (
            fm and fm._is_remote_session() and not getattr(fm, "_is_rebinding", False)
        )

    def _upload_files_directly(self, fm, local_paths, remote_dir):
        """Upload files directly via the file manager."""
        from pathlib import Path

        if remote_dir:
            fm.current_path = remote_dir
        paths = [Path(p) for p in local_paths]
        fm._show_upload_confirmation_dialog(paths)
        self.logger.info(f"Upload initiated for {len(paths)} files via file manager")

    def _store_pending_drop_and_activate_fm(
        self, local_paths, remote_dir, session, ssh_target, terminal_id, page, terminal
    ):
        """Store pending drop info and activate file manager."""
        self._pending_drop_files = local_paths
        self._pending_drop_remote_dir = remote_dir
        self._pending_drop_session = session
        self._pending_drop_ssh_target = ssh_target
        self._pending_drop_terminal_id = terminal_id
        self._pending_drop_page = page
        self._pending_drop_attempts = 0
        self._pending_drop_terminal = terminal

        # Ensure file manager panel is visible
        if not self.file_manager_button.get_active():
            self.file_manager_button.set_active(True)

        # After activation, force rebind to the correct terminal
        fm = self.tab_manager.file_managers.get(page)
        if fm:
            self.logger.info(
                f"Force rebinding FM to drop target terminal {terminal_id}"
            )
            fm.rebind_terminal(terminal)

        GLib.timeout_add(300, self._check_pending_drop_upload)

    def _check_pending_drop_upload(self):
        """Check if file manager is ready and process pending drop upload."""
        if not hasattr(self, "_pending_drop_files") or not self._pending_drop_files:
            return False

        self._pending_drop_attempts = getattr(self, "_pending_drop_attempts", 0) + 1
        if self._pending_drop_attempts > 30:
            self.logger.warning(
                "Timed out waiting for file manager to be ready for upload"
            )
            self._clear_pending_drop()
            return False

        page = getattr(self, "_pending_drop_page", None)
        if not page:
            self.logger.warning("No page stored for pending drop")
            self._clear_pending_drop()
            return False

        fm = self.tab_manager.file_managers.get(page)
        wait_result = self._wait_for_file_manager_ready(fm)
        if wait_result is not None:
            return wait_result

        # File manager is ready - proceed with upload
        self._execute_pending_upload(fm)
        return False

    def _wait_for_file_manager_ready(self, fm):
        """Wait for file manager to be ready. Returns True to continue, False to stop, None if ready."""
        if not fm:
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: File manager not yet created"
            )
            return True

        if getattr(fm, "_is_rebinding", False):
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: File manager still rebinding"
            )
            return True

        if not fm.session_item:
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: Session item not set"
            )
            return True

        if not fm._is_remote_session():
            self._try_rebind_if_needed(fm)
            return True

        return None  # Ready

    def _try_rebind_if_needed(self, fm):
        """Attempt rebind to correct terminal if needed."""
        if self._pending_drop_attempts % 5 != 1:
            return

        terminal = getattr(self, "_pending_drop_terminal", None)
        if not terminal:
            terminal = self.terminal_manager.registry.get_terminal(
                getattr(self, "_pending_drop_terminal_id", None)
            )
        if terminal:
            self.logger.info(
                f"Rebinding FM to terminal (attempt {self._pending_drop_attempts})"
            )
            fm.rebind_terminal(terminal)

    def _execute_pending_upload(self, fm):
        """Execute the pending upload operation."""
        from pathlib import Path

        self.logger.info(
            f"File manager ready after {self._pending_drop_attempts} attempts"
        )

        if self._pending_drop_remote_dir:
            fm.current_path = self._pending_drop_remote_dir

        paths = [Path(p) for p in self._pending_drop_files]
        fm._show_upload_confirmation_dialog(paths)
        self.logger.info(
            f"Pending upload initiated for {len(paths)} files via file manager"
        )
        self._clear_pending_drop()

    def _clear_pending_drop(self):
        """Clear all pending drop state."""
        self._pending_drop_files = None
        self._pending_drop_remote_dir = None
        self._pending_drop_session = None
        self._pending_drop_ssh_target = None
        self._pending_drop_terminal_id = None
        self._pending_drop_page = None
        self._pending_drop_terminal = None
        self._pending_drop_attempts = 0

    def _on_quit_application_requested(self) -> None:
        """Handle quit request from tab manager."""
        if app := self.get_application():
            app.quit()
        else:
            self.destroy()

    def _update_file_manager_transparency(self):
        """Update transparency for all file managers and AI panel when settings change."""
        # Transparency is handled by .main-header-bar CSS class globally

        for file_manager in self.tab_manager.file_managers.values():
            try:
                file_manager._apply_background_transparency()
                # Also update headerbar transparency for any open dialogs
                if (
                    hasattr(file_manager, "transfer_history_window")
                    and file_manager.transfer_history_window
                ):
                    # Transparency is handled by CSS classes globally
                    pass
            except Exception as e:
                self.logger.warning(f"Failed to update file manager transparency: {e}")

        # Update AI chat panel transparency
        if hasattr(self, "ui_builder") and self.ui_builder.ai_chat_panel:
            try:
                self.ui_builder.ai_chat_panel.update_transparency()
            except Exception as e:
                self.logger.warning(f"Failed to update AI chat panel transparency: {e}")

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
                    css_classes=["flat", "circular"],
                    tooltip_text=_("Remove this temporary file"),
                )
                remove_button.set_child(icon_image("edit-delete-symbolic"))

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

    def _show_command_manager_dialog(self):
        """Creates and shows the Command Manager dialog, or closes it if already visible."""
        if self.command_manager_dialog is None:
            # Lazy import - only load when the dialog is first opened
            from .ui.dialogs.command_manager_dialog import CommandManagerDialog

            self.command_manager_dialog = CommandManagerDialog(
                self, self.settings_manager
            )
            self.command_manager_dialog.connect(
                "command-selected", self._on_command_selected_from_manager
            )
        if self.command_manager_dialog.get_visible():
            self.command_manager_dialog.close()
        else:
            self.command_manager_dialog.present()

    def _on_command_selected_from_manager(
        self, dialog, command_text: str, execute: bool
    ):
        """Callback for when a command is selected from the Command Manager."""
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            if execute:
                # Execute the command (add newline)
                command_bytes = command_text.encode("utf-8") + b"\n"
                terminal.feed_child(command_bytes)
            else:
                # Use bracketed paste to insert command without auto-executing
                paste_data = PASTE_START + command_text.encode("utf-8") + PASTE_END
                terminal.feed_child(paste_data)
            terminal.grab_focus()
        else:
            self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    def _broadcast_command_to_all(self, command_text: str):
        """Send a command to all open terminals."""
        self.broadcast_manager.broadcast_to_all(command_text)

    def move_layout(self, layout_name: str, old_folder: str, new_folder: str) -> None:
        """Delegate layout move operation to state manager.

        Args:
            layout_name: Name of the layout to move.
            old_folder: Current folder path of the layout.
            new_folder: Target folder path for the layout.
        """
        self.state_manager.move_layout(layout_name, old_folder, new_folder)

    # ─── Command Toolbar Methods ────────────────────────────────────────────

    def refresh_command_toolbar(self) -> None:
        """Refresh the command toolbar with current pinned commands."""
        if hasattr(self.ui_builder, "_populate_command_toolbar"):
            self.ui_builder._populate_command_toolbar(self.ui_builder._toolbar_inner)

    def execute_toolbar_command(self, command) -> None:
        """Execute a command from the toolbar.

        Args:
            command: CommandButton object to execute.
        """
        from .data.command_manager_models import ExecutionMode
        from .ui.dialogs.command_manager_dialog import CommandFormDialog

        terminal = self.tab_manager.get_selected_terminal()

        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            # Show form dialog first
            dialog = CommandFormDialog(
                self, command, send_to_all=False, settings_manager=self.settings_manager
            )
            dialog.connect("command-ready", self._on_toolbar_form_command_ready)
            dialog.present()
        else:
            # Build command directly
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE

            if terminal:
                if execute:
                    command_bytes = cmd_text.encode("utf-8") + b"\n"
                    terminal.feed_child(command_bytes)
                else:
                    paste_data = PASTE_START + cmd_text.encode("utf-8") + PASTE_END
                    terminal.feed_child(paste_data)
                terminal.grab_focus()
            else:
                self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    def _on_toolbar_form_command_ready(
        self, dialog, command: str, execute: bool, send_to_all: bool
    ):
        """Handle command ready from toolbar form dialog."""
        if send_to_all:
            self._send_command_to_all_terminals(command, execute)
        else:
            self._send_command_to_active_terminal(command, execute)

    def _send_command_to_all_terminals(self, command: str, execute: bool):
        """Send command to all open terminals."""
        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        for terminal in all_terminals:
            self._feed_command_to_terminal(terminal, command, execute)
        if all_terminals:
            all_terminals[-1].grab_focus()

    def _send_command_to_active_terminal(self, command: str, execute: bool):
        """Send command to the active terminal."""
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            self._feed_command_to_terminal(terminal, command, execute)
            terminal.grab_focus()
        else:
            self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    def _feed_command_to_terminal(self, terminal, command: str, execute: bool) -> None:
        """Feed a command to a terminal, optionally executing it."""
        if execute:
            terminal.feed_child(command.encode("utf-8") + b"\n")
        else:
            paste_data = PASTE_START + command.encode("utf-8") + PASTE_END
            terminal.feed_child(paste_data)
