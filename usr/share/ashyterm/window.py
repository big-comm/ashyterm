# ashyterm/window.py

import weakref
from pathlib import Path
from typing import List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

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

        # Core properties and dependencies
        self.settings_manager = settings_manager
        self.is_main_window = True
        self._cleanup_performed = False
        self._force_closing = False
        self.layouts: List[LayoutItem] = []
        self.active_temp_files = weakref.WeakKeyDictionary()

        # Initial state from command line or other windows
        self.initial_working_directory = kwargs.get("initial_working_directory")
        self.initial_execute_command = kwargs.get("initial_execute_command")
        self.close_after_execute = kwargs.get("close_after_execute", False)
        self.initial_ssh_target = kwargs.get("initial_ssh_target")
        self._is_for_detached_tab = kwargs.get("_is_for_detached_tab", False)

        # Window setup
        self._setup_initial_window_size()
        self.set_title(_("Ashy Terminal"))
        self.set_icon_name("ashyterm")

        # Component Initialization
        self._create_managers_and_ui()
        self._connect_component_signals()
        self._load_initial_data()
        self._setup_window_events()

        if not self._is_for_detached_tab:
            GLib.idle_add(self._create_initial_tab_safe)
        self.logger.info("Main window initialization completed")

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
        self.cleanup_button = self.ui_builder.cleanup_button
        self.cleanup_popover = self.ui_builder.cleanup_popover
        self.font_sizer_widget = self.ui_builder.font_sizer_widget
        self.scrolled_tab_bar = self.ui_builder.scrolled_tab_bar
        self.single_tab_title_widget = self.ui_builder.single_tab_title_widget
        self.title_stack = self.ui_builder.title_stack
        self.toast_overlay = self.ui_builder.toast_overlay
        self.search_entry = self.ui_builder.search_entry
        self.tab_manager.scrolled_tab_bar = self.scrolled_tab_bar

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
        self.terminal_manager.on_terminal_directory_changed = (
            self._on_terminal_directory_changed
        )
        self.terminal_manager.set_terminal_exit_handler(self._on_terminal_exit)
        self.tab_manager.get_view_stack().connect(
            "notify::visible-child", self._on_tab_changed
        )
        self.settings_manager.add_change_listener(self._on_setting_changed)
        self.file_manager_button.connect("toggled", self._on_toggle_file_manager)
        self.cleanup_button.connect("notify::active", self._on_cleanup_button_toggled)

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
        """Handles key press events for tab navigation."""
        if state & Gdk.ModifierType.ALT_MASK:
            if keyval == Gdk.KEY_Page_Down:
                self.tab_manager.select_next_tab()
                return Gdk.EVENT_STOP
            if keyval == Gdk.KEY_Page_Up:
                self.tab_manager.select_previous_tab()
                return Gdk.EVENT_STOP

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
        if key == "auto_hide_sidebar":
            self.sidebar_manager.handle_auto_hide_change(new_value)
        elif key in [
            "font",
            "color_scheme",
            "transparency",
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
            if self.font_sizer_widget and key == "font":
                self.font_sizer_widget.update_display()

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
            self.tab_manager.create_local_tab(title=session.name)
        else:
            self.tab_manager.create_ssh_tab(session)

    def _on_terminal_focus_changed(self, terminal, _from_sidebar: bool) -> None:
        page = self.tab_manager.get_page_for_terminal(terminal)
        # NOVO: Adicionar verificação para 'content_paned' para evitar erro em abas destacadas.
        # Esta verificação previne o crash e corrige o problema de sincronização do file manager.
        if not page or not hasattr(page, "content_paned"):
            return
        # FIM DA ALTERAÇÃO

        if page.content_paned.get_end_child():
            fm = self.tab_manager.file_managers.get(page)
            if fm:
                fm.rebind_terminal(terminal)

    def _on_terminal_directory_changed(
        self, terminal, new_title: str, osc7_info
    ) -> None:
        page = self.tab_manager.get_page_for_terminal(terminal)
        if page:
            self.tab_manager.set_tab_title(page, new_title)
        self._update_tab_layout()

    def _on_tab_changed(self, view_stack, _param):
        """Handle tab changes."""
        if not self.tab_manager.active_tab:
            return

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

        content = page_to_detach.get_child()
        title = tab_widget._base_title
        icon_name = getattr(tab_widget, "_icon_name", "computer-symbolic")

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
        new_window = app.create_new_window(_is_for_detached_tab=True)
        new_window.tab_manager.re_attach_detached_page(content, title, icon_name)

        new_window._update_tab_layout()
        new_window.present()

    # --- Window Lifecycle and State ---

    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab, trying to restore session first."""
        try:
            if not self.state_manager.restore_session_state():
                if self.tab_manager.get_tab_count() == 0:
                    if self.initial_ssh_target:
                        user, host = self.initial_ssh_target.split("@", 1)
                        session = SessionItem(
                            name=self.initial_ssh_target,
                            session_type="ssh",
                            user=user,
                            host=host,
                        )
                        self.tab_manager.create_ssh_tab(session)
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
        for fm in self.tab_manager.file_managers.values():
            fm.shutdown(None)

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    # --- Public API & Helpers ---

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

    def _on_quit_application_requested(self) -> None:
        """Handle quit request from tab manager."""
        if app := self.get_application():
            app.quit()
        else:
            self.destroy()

    def _on_terminal_exit(self, terminal, child_status, identifier):
        pass

    def _on_new_tab_clicked(self, _button) -> None:
        self.action_handler.new_local_tab(None, None)

    def _on_edit_selected_clicked(self, _button) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self.action_handler.edit_session(None, None)
        elif isinstance(item, SessionFolder):
            self.action_handler.edit_folder(None, None)

    def _update_cleanup_button_visibility(self):
        """Show or hide the cleanup button based on the total count of temp files."""
        total_count = sum(self.active_temp_files.values())
        self.cleanup_button.set_visible(total_count > 0)

    def _on_cleanup_button_toggled(self, button, gparam):
        """Popula e mostra o popover de limpeza de arquivos temporários."""
        pass

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

        # Iterate through all file managers associated with tabs
        for page, fm in self.tab_manager.file_managers.items():
            if fm in self.active_temp_files and self.active_temp_files[fm] > 0:
                has_files = True
                group = Adw.PreferencesGroup(title=fm.session_item.name)
                content_container.append(group)
                for info in fm.get_temp_files_info():
                    row = Adw.ActionRow(title=info["remote_path"], subtitle="")
                    row.set_title_selectable(True)
                    remove_button = Gtk.Button(
                        icon_name="edit-delete-symbolic",
                        css_classes=["flat", "circular"],
                        tooltip_text=_("Remove this temporary file"),
                    )
                    dir_path = str(Path(info["local_file_path"]).parent)
                    remove_button.connect(
                        "clicked",
                        lambda _,
                        fm_instance=fm,
                        dp=dir_path: self._on_clear_single_temp_file_clicked(
                            fm_instance, dp
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

    def _on_clear_single_temp_file_clicked(self, file_manager, dir_path_to_clear):
        """Callback to clear a single temporary file and its directory."""
        file_manager.cleanup_all_temp_files(dir_path_to_clear)
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
