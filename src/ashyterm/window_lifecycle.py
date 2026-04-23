"""Window lifecycle mixin — init, destroy, cleanup, window state, settings."""

import weakref
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .sessions.models import LayoutItem, SessionFolder, SessionItem
from .terminal.manager import TerminalManager
from .utils.translation_utils import _


class WindowLifecycleMixin:
    """Mixin: window lifecycle, state, settings, signals, cleanup."""

    # ─── Initialization ────────────────────────────────────────────────

    def _lifecycle_init_common(self) -> None:
        """Reset lifecycle flags and per-window state containers."""
        self._cleanup_performed = False
        self._force_closing = False
        self.layouts: List[LayoutItem] = []
        self.active_temp_files: weakref.WeakKeyDictionary[Any, Any] = (
            weakref.WeakKeyDictionary()
        )
        self.command_manager_dialog = None

        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self.search_active = False

    def _setup_initial_window_size(self) -> None:
        """Set up initial window size and state from settings."""
        if self.settings_manager.get("remember_window_state", True):
            width = self.settings_manager.get("window_width", 1200)
            height = self.settings_manager.get("window_height", 700)
            maximized = self.settings_manager.get("window_maximized", False)

            self.set_default_size(width, height)

            if maximized:
                GLib.idle_add(self.maximize)
        else:
            self.set_default_size(1200, 700)

    def _apply_initial_visual_settings(self) -> None:
        """Applies all visual settings upon window creation."""
        self.logger.info("Applying initial visual settings to new window.")
        self.settings_manager._update_app_theme_css(self)
        self.terminal_manager.apply_settings_to_all_terminals()

    # ─── Component Assembly ────────────────────────────────────────────

    def _create_managers_and_ui(self) -> None:
        """Instantiate managers and build the UI tree in dependency order."""
        self.logger.info("Creating and wiring core components")

        from .sessions.operations import SessionOperations
        from .sessions.tree import SessionTreeView
        from .state.window_state import WindowStateManager
        from .terminal.ai_assistant import TerminalAiAssistant
        from .terminal.tabs import TabManager
        from .ui.actions import WindowActions
        from .ui.broadcast_manager import BroadcastManager
        from .ui.search_manager import SearchManager
        from .ui.sidebar_manager import SidebarManager
        from .ui.window_ui import WindowUIBuilder

        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)

        self.session_operations = SessionOperations(
            self.session_store, self.folder_store, self.settings_manager
        )

        self.terminal_manager = TerminalManager(self, self.settings_manager)
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
            scrolled_tab_bar=None,
            on_tab_count_changed=self._update_tab_layout,
        )
        self.terminal_manager.set_tab_manager(self.tab_manager)

        self.ui_builder = WindowUIBuilder(self)
        self.ui_builder.build_ui()
        self._assign_ui_components()

        self.search_manager = SearchManager(self)
        self.broadcast_manager = BroadcastManager(self)

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
        self.content_overlay = self.ui_builder.content_overlay
        self.search_bar = self.ui_builder.search_bar
        self.search_button = self.ui_builder.search_button
        self.broadcast_bar = self.ui_builder.broadcast_bar
        self.broadcast_button = self.ui_builder.broadcast_button
        self.broadcast_entry = self.ui_builder.broadcast_entry
        self.terminal_search_entry = self.ui_builder.terminal_search_entry
        self.search_entry = self.ui_builder.sidebar_search_entry
        self.search_prev_button = self.ui_builder.search_prev_button
        self.search_next_button = self.ui_builder.search_next_button
        self.search_occurrence_label = self.ui_builder.search_occurrence_label
        self.case_sensitive_switch = self.ui_builder.case_sensitive_switch
        self.regex_switch = self.ui_builder.regex_switch
        self.command_toolbar = self.ui_builder.command_toolbar
        self.tab_manager.scrolled_tab_bar = self.scrolled_tab_bar

    # ─── Signal Connections ────────────────────────────────────────────

    def _connect_component_signals(self) -> None:
        """Connects signals and callbacks between the window and its managers."""
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

    # ─── Window Events ─────────────────────────────────────────────────

    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        self.connect("close-request", self._on_window_close_request)
        self.connect("notify::default-width", self._on_window_size_changed)
        self.connect("notify::default-height", self._on_window_size_changed)
        self.connect("notify::maximized", self._on_window_maximized_changed)

        if not self._is_for_detached_tab:
            self._initial_tab_created = False
            self._data_loaded = False
            self._window_mapped = False
            self.connect("map", self._on_window_mapped)

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

            return True

        GLib.timeout_add_seconds(2, log_modal_windows)
        self.logger.info("[MODAL_DEBUG] Modal window monitor started (debug_mode=True)")

    # ─── Window State (size / maximized) ───────────────────────────────

    def _on_window_size_changed(self, window, _param_spec) -> None:
        """Handle window size changes to save to settings."""
        if not self.settings_manager.get("remember_window_state", True):
            return

        if not self.is_maximized():
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

    # ─── Window Map / Deferred Init ────────────────────────────────────

    def _on_window_mapped(self, window) -> None:
        """Handle window map signal — create initial tab after window has final dimensions."""
        if hasattr(self, "header_bar"):
            self.settings_manager._update_app_theme_css(self)

        if self._initial_tab_created:
            return

        self._window_mapped = True
        self.logger.debug("Window mapped - waiting for data before creating tabs")
        self._try_create_initial_tab()

    def _try_create_initial_tab(self) -> None:
        """Create initial tab only when both window is mapped and data is loaded."""
        if self._initial_tab_created:
            return
        if not self._window_mapped or not self._data_loaded:
            return
        self._initial_tab_created = True
        self.logger.debug("Both window mapped and data loaded - creating initial tab")
        GLib.idle_add(self._create_initial_tab_safe)

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

    # ─── Data Loading ──────────────────────────────────────────────────

    def _load_initial_data(self) -> None:
        """Load initial sessions, folders, and layouts data."""

        def load_data_background():
            try:
                from .sessions.storage import load_sessions_and_folders

                self.state_manager.load_layouts()
                sessions_data, folders_data = load_sessions_and_folders()

                GLib.idle_add(
                    self._update_stores_with_data, sessions_data, folders_data
                )
            except Exception as e:
                GLib.idle_add(self._handle_load_error, e)

        from .core.tasks import AsyncTaskManager

        AsyncTaskManager.get().submit_io(load_data_background)

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

            import_result = self.session_operations.import_sessions_from_ssh_config()
            if import_result.success:
                self.logger.info(import_result.message)
                self.refresh_tree()
                if import_result.warnings:
                    skipped = len(import_result.warnings)
                    self.toast_overlay.add_toast(
                        Adw.Toast(
                            title=_("{count} SSH config entries skipped.").format(
                                count=skipped
                            )
                        )
                    )
            elif import_result.message:
                self.logger.debug(f"SSH config import skipped: {import_result.message}")

            self.logger.info(
                f"Loaded {self.session_store.get_n_items()} sessions, "
                f"{self.folder_store.get_n_items()} folders, "
                f"and {len(self.layouts)} layouts"
            )
        except Exception as e:
            self._handle_load_error(e)

        self._data_loaded = True
        self._try_create_initial_tab()

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
        self._data_loaded = True
        self._try_create_initial_tab()
        return GLib.SOURCE_REMOVE

    # ─── Setting Change Handlers ───────────────────────────────────────

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
            self.ui_builder._update_headerbar_button_visibility()

    def _on_ai_assistant_requested(self, *_args) -> None:
        """Toggle the AI assistant panel."""
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

        self.ui_builder.toggle_ai_panel()

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

        if key in ["color_scheme", "headerbar_transparency"]:
            self.settings_manager._update_app_theme_css(self)

        if key in ["transparency", "headerbar_transparency"]:
            self._update_file_manager_transparency()

        if self.ui_builder.font_sizer_widget and key == "font":
            self.ui_builder.font_sizer_widget.update_display()

    def _on_color_scheme_changed(self, dialog, idx):
        """Handle color scheme changes from the dialog."""
        self.terminal_manager.apply_settings_to_all_terminals()
        self.settings_manager._update_app_theme_css(self)

        try:
            from .terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

    # ─── Focus / Session / Terminal Exit ───────────────────────────────

    def _on_terminal_focus_changed(self, terminal, _from_sidebar: bool) -> None:
        page = self.tab_manager.get_page_for_terminal(terminal)
        if not page or not hasattr(page, "content_paned"):
            return

        if page.content_paned.get_end_child():
            fm = self.tab_manager.file_managers.get(page)
            if fm:
                fm.rebind_terminal(terminal)

        self.search_manager.hide_if_terminal_changed()
        self.terminal_manager._update_title(terminal)

    def _on_session_activated(self, session: SessionItem) -> None:
        from .utils.security import validate_session_data

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

    def _on_terminal_exit(self, terminal, child_status, identifier):
        if getattr(self, "ai_assistant", None):
            self.ai_assistant.clear_conversation_for_terminal(terminal)

    def _on_quit_application_requested(self) -> None:
        """Handle quit request from tab manager."""
        if app := self.get_application():
            app.quit()
        else:
            self.destroy()

    # ─── Transparency ──────────────────────────────────────────────────

    def _update_file_manager_transparency(self):
        """Repropagate transparency to every file manager + AI panel."""
        for file_manager in self.tab_manager.file_managers.values():
            try:
                file_manager._apply_background_transparency()
            except Exception as e:
                self.logger.warning(f"Failed to update file manager transparency: {e}")

        if hasattr(self, "ui_builder") and self.ui_builder.ai_chat_panel:
            try:
                self.ui_builder.ai_chat_panel.update_transparency()
            except Exception as e:
                self.logger.warning(f"Failed to update AI chat panel transparency: {e}")

    # ─── Window Close Request ──────────────────────────────────────────

    def _on_window_close_request(self, window) -> bool:
        self.logger.info("Window close request received")
        if self._force_closing:
            return Gdk.EVENT_PROPAGATE

        tab_count = self.tab_manager.get_tab_count()
        if tab_count > 1:
            close_policy = self.settings_manager.get(
                "close_multiple_tabs_policy", "ask"
            )
            if close_policy == "ask":
                self._show_close_multiple_tabs_dialog()
                return Gdk.EVENT_STOP
            elif close_policy == "save_and_close":
                self.state_manager.save_session_state()
                return self._continue_close_process()
            self.state_manager.clear_session_state()

        return self._continue_close_process()

    def _show_close_multiple_tabs_dialog(self) -> None:
        """Show dialog when closing with multiple tabs open."""
        tab_count = self.tab_manager.get_tab_count()
        dialog = Adw.AlertDialog(
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
        dialog.present(self)

    def _on_close_multiple_tabs_dialog_response(self, dialog, response_id):
        """Handle response from close multiple tabs dialog."""
        if response_id == "save":
            self.state_manager.save_session_state()
            self._continue_close_process(force_close=True)
        elif response_id == "close":
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
        dialog = Adw.AlertDialog(
            heading=_("Close Window"),
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

        dialog.connect("response", on_response)
        dialog.present(self)

    # ─── Cleanup / Destroy ─────────────────────────────────────────────

    def _perform_cleanup(self) -> None:
        if self._cleanup_performed:
            return
        self._cleanup_performed = True
        self.logger.info("Performing window cleanup")

        if hasattr(self, "ai_assistant") and self.ai_assistant:
            self.ai_assistant.shutdown()

        self.terminal_manager.cleanup_all_terminals()

        if self.settings_manager.get("clear_remote_edit_files_on_exit", False):
            self.logger.info("Clearing all temporary remote edit files on exit.")
            for fm in self.tab_manager.file_managers.values():
                fm.cleanup_all_temp_files()

        for fm in self.tab_manager.file_managers.values():
            fm.shutdown(None)

        self.settings_manager.cleanup_css_providers(self)

        if hasattr(self, "session_tree") and self.session_tree:
            self.session_tree.disconnect_signals()

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    # ─── Helpers ───────────────────────────────────────────────────────

    def refresh_tree(self) -> None:
        self.session_tree.refresh_tree()
        self.sidebar_manager.update_sidebar_sizes()

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Adw.AlertDialog(heading=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    # ─── Tab Bar Scroll ────────────────────────────────────────────────

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
