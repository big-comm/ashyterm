# ashyterm/window.py

import os
from typing import List, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .filemanager.manager import FileManager
from .sessions.models import SessionFolder, SessionItem
from .sessions.storage import (
    load_folders_to_store,
    load_sessions_and_folders,
    load_sessions_to_store,
)
from .sessions.tree import SessionTreeView
from .settings.config import APP_TITLE
from .settings.manager import SettingsManager
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .ui.dialogs import (
    FolderEditDialog,
    MoveSessionDialog,
    PreferencesDialog,
    SessionEditDialog,
)
from .ui.menus import MainApplicationMenu
from .utils.exceptions import (
    DialogError,
    UIError,
    VTENotAvailableError,
    handle_exception,
)
from .utils.logger import get_logger, log_session_event
from .utils.security import validate_session_data
from .utils.translation_utils import _


class CommTerminalWindow(Adw.ApplicationWindow):
    """Main application window with enhanced functionality."""

    def __init__(self, application, settings_manager: SettingsManager, **kwargs):
        super().__init__(application=application)
        self.logger = get_logger("ashyterm.window")
        self.logger.info("Initializing main window")

        self.settings_manager = settings_manager
        self.is_main_window = True
        self.initial_working_directory = kwargs.get("initial_working_directory")
        self.initial_execute_command = kwargs.get("initial_execute_command")
        self.close_after_execute = kwargs.get("close_after_execute", False)
        self.initial_ssh_target = kwargs.get("initial_ssh_target")
        self._is_for_detached_tab = kwargs.get("_is_for_detached_tab", False)

        self.set_default_size(1200, 700)
        self.set_title(APP_TITLE)
        self.set_icon_name("ashyterm")

        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)
        self.terminal_manager = TerminalManager(self, self.settings_manager)
        self.tab_manager = TabManager(self.terminal_manager)
        self.session_tree = SessionTreeView(
            self, self.session_store, self.folder_store, self.settings_manager
        )
        self.tab_file_managers = {}
        self.current_file_manager = None
        self.font_sizer_widget = None
        self.terminal_manager.set_tab_manager(self.tab_manager)
        self._cleanup_performed = False
        self._force_closing = False

        self._initialize_window()

    def _initialize_window(self) -> None:
        """Initialize window components safely."""
        self._setup_styles()
        self._setup_actions()
        self._setup_ui()
        self._setup_callbacks()
        self._load_initial_data()
        self._setup_window_events()
        if not self._is_for_detached_tab:
            GLib.idle_add(self._create_initial_tab_safe)
        self.logger.info("Main window initialization completed")

    def _setup_styles(self) -> None:
        """Applies application-wide CSS for various custom widgets."""
        css = """
        .themeselector { margin: 9px; }
        .themeselector checkbutton {
            padding: 1px; min-height: 44px; min-width: 44px;
            background-clip: content-box; border-radius: 9999px;
            box-shadow: inset 0 0 0 1px @borders;
        }
        .themeselector checkbutton:checked { box-shadow: inset 0 0 0 2px @accent_bg_color; }
        .themeselector checkbutton.follow { background-image: linear-gradient(to bottom right, #fff 49.99%, #202020 50.01%); }
        .themeselector checkbutton.light { background-color: #fff; }
        .themeselector checkbutton.dark { background-color: #202020; }
        .themeselector checkbutton radio {
            -gtk-icon-source: none; border: none; background: none; box-shadow: none;
            min-width: 12px; min-height: 12px; transform: translate(27px, 14px); padding: 2px;
        }
        .themeselector checkbutton radio:checked {
            -gtk-icon-source: -gtk-icontheme("object-select-symbolic");
            background-color: @accent_bg_color; color: @accent_fg_color;
        }
        headerbar.main-header-bar { min-height: 0; padding: 0; border: none; box-shadow: none; }
        .drop-target { background-color: alpha(@theme_selected_bg_color, 0.5); border-radius: 6px; }
        .indented-session { margin-left: 16px; }
        paned separator { background: var(--view-bg-color); }
        popover.menu separator { background-color: color-mix(in srgb, var(--headerbar-border-color) var(--border-opacity), transparent); }
        
        .custom-tab-bar { padding: 0; margin: 0; }
        .custom-tab-button { padding: 4px 8px; margin: 0; border-radius: 0; }
        .custom-tab-button > box { align-items: center; }
        .custom-tab-button button.circular { padding: 2px; min-height: 24px; min-width: 24px; }
        .custom-tab-button.active { background-color: var(--headerbar-shade-color, @theme_selected_bg_color); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab with proper error handling."""
        try:
            if self.tab_manager.get_tab_count() == 0:
                if self.initial_ssh_target:
                    from .sessions.models import SessionItem

                    user_host = self.initial_ssh_target.split("@")
                    user = user_host[0] if len(user_host) > 1 else os.getlogin()
                    host = user_host[-1]
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

    def _setup_actions(self) -> None:
        """Set up window-level actions."""
        try:
            actions = [
                ("new-local-tab", self._on_new_local_tab),
                ("close-tab", self._on_close_tab),
                ("copy", self._on_copy),
                ("paste", self._on_paste),
                ("select-all", self._on_select_all),
                ("split-horizontal", self._on_split_horizontal),
                ("split-vertical", self._on_split_vertical),
                ("close-pane", self._on_close_pane),
                ("open-url", self._on_open_url),
                ("copy-url", self._on_copy_url),
                ("zoom-in", self._on_zoom_in),
                ("zoom-out", self._on_zoom_out),
                ("zoom-reset", self._on_zoom_reset),
                ("connect-sftp", self._on_connect_sftp),
                ("edit-session", self._on_edit_session),
                ("duplicate-session", self._on_duplicate_session),
                ("rename-session", self._on_rename_session),
                ("move-session-to-folder", self._on_move_session_to_folder),
                ("delete-session", self._on_delete_selected_items),
                ("edit-folder", self._on_edit_folder),
                ("rename-folder", self._on_rename_folder),
                ("add-session-to-folder", self._on_add_session_to_folder),
                ("delete-folder", self._on_delete_selected_items),
                ("cut-item", self._on_cut_item),
                ("copy-item", self._on_copy_item),
                ("paste-item", self._on_paste_item),
                ("paste-item-root", self._on_paste_item_root),
                ("add-session-root", self._on_add_session_root),
                ("add-folder-root", self._on_add_folder_root),
                ("toggle-sidebar", self._on_toggle_sidebar_action),
                ("preferences", self._on_preferences),
                ("shortcuts", self._on_shortcuts),
                ("new-window", self._on_new_window),
            ]
            for action_name, callback in actions:
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", callback)
                self.add_action(action)
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")
            raise UIError("window", f"action setup failed: {e}")

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        try:
            main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.header_bar = self._create_header_bar()
            main_box.append(self.header_bar)

            self.flap = Adw.Flap(transition_type=Adw.FlapTransitionType.SLIDE)
            self.sidebar_box = self._create_sidebar()
            self.flap.set_flap(self.sidebar_box)

            content_box = self._create_content_area()
            self.flap.set_content(content_box)

            main_box.append(self.flap)
            self.set_content(main_box)

            initial_visible = self.settings_manager.get_sidebar_visible()
            self.flap.set_reveal_flap(initial_visible)
            self.toggle_sidebar_button.set_active(initial_visible)
            self._update_sidebar_button_icon()
            self._update_tab_layout()

        except Exception as e:
            self.logger.error(f"UI setup failed: {e}")
            raise UIError("window", f"UI setup failed: {e}")

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create the header bar with controls."""
        header_bar = Adw.HeaderBar(css_classes=["main-header-bar"])
        self.toggle_sidebar_button = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic", tooltip_text=_("Toggle Sidebar")
        )
        self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
        header_bar.pack_start(self.toggle_sidebar_button)

        self.file_manager_button = Gtk.ToggleButton(
            icon_name="folder-open-symbolic", tooltip_text=_("File Manager")
        )
        self.file_manager_button.connect("toggled", self._on_toggle_file_manager)
        header_bar.pack_start(self.file_manager_button)

        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", tooltip_text=_("Main Menu")
        )
        popover, self.font_sizer_widget = MainApplicationMenu.create_main_popover(self)
        menu_button.set_popover(popover)
        header_bar.pack_end(menu_button)

        new_tab_button = Gtk.Button.new_from_icon_name("tab-new-symbolic")
        new_tab_button.set_tooltip_text(_("New Tab"))
        new_tab_button.connect("clicked", self._on_new_tab_clicked)
        new_tab_button.add_css_class("flat")
        header_bar.pack_end(new_tab_button)

        self.tab_bar = self.tab_manager.get_tab_bar()
        self.tab_bar.add_css_class("custom-tab-bar")

        scrolled_tab_bar = Gtk.ScrolledWindow()
        scrolled_tab_bar.set_child(self.tab_bar)
        scrolled_tab_bar.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scrolled_tab_bar.set_propagate_natural_height(True)
        scrolled_tab_bar.set_hexpand(True)

        header_bar.set_title_widget(scrolled_tab_bar)

        return header_bar

    def _create_sidebar(self) -> Gtk.Widget:
        """Create the sidebar with session tree."""
        toolbar_view = Adw.ToolbarView(css_classes=["background"])
        scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_child(self.session_tree.get_widget())
        toolbar_view.set_content(scrolled_window)
        toolbar = Gtk.ActionBar()
        add_session_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_session_button.set_tooltip_text(_("Add Session"))
        add_session_button.connect("clicked", self._on_add_session_clicked)
        toolbar.pack_start(add_session_button)
        add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
        add_folder_button.set_tooltip_text(_("Add Folder"))
        add_folder_button.connect("clicked", self._on_add_folder_clicked)
        toolbar.pack_start(add_folder_button)
        edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_button.set_tooltip_text(_("Edit Selected"))
        edit_button.connect("clicked", self._on_edit_selected_clicked)
        toolbar.pack_start(edit_button)
        remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove_button.set_tooltip_text(_("Remove Selected"))
        remove_button.connect("clicked", self._on_remove_selected_clicked)
        toolbar.pack_start(remove_button)
        toolbar_view.add_bottom_bar(toolbar)
        return toolbar_view

    def _create_content_area(self) -> Gtk.Widget:
        """Create the main content area with tabs."""
        main_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        view_stack = self.tab_manager.get_view_stack()
        view_stack.add_css_class("terminal-tab-view")

        self.content_box.append(view_stack)
        main_paned.set_start_child(self.content_box)
        main_paned.set_resize_start_child(True)
        main_paned.set_shrink_start_child(False)

        self.file_manager_placeholder = Gtk.Box(
            css_classes=["background"], visible=False
        )
        main_paned.set_end_child(self.file_manager_placeholder)
        main_paned.set_resize_end_child(False)
        main_paned.set_shrink_end_child(False)
        self.file_manager_placeholder.set_size_request(-1, 200)
        self.main_paned = main_paned

        self.connect(
            "realize", lambda w: main_paned.set_position(self.get_height() - 250)
        )
        self.toast_overlay = Adw.ToastOverlay(child=main_paned)

        return self.toast_overlay

    def _update_tab_layout(self):
        """Update tab layout based on tab count."""
        tab_count = self.tab_manager.get_tab_count()
        self.tab_bar.get_parent().set_visible(tab_count > 0)

        if tab_count <= 1:
            visible_child = self.tab_manager.view_stack.get_visible_child()
            if visible_child:
                page = self.tab_manager.view_stack.get_page(visible_child)
                self.set_title(f"{APP_TITLE} - {page.get_title()}")
        else:
            self.set_title(APP_TITLE)

    def _setup_callbacks(self) -> None:
        """Set up callbacks between components."""
        self.session_tree.on_session_activated = self._on_session_activated
        self.terminal_manager.on_terminal_focus_changed = (
            self._on_terminal_focus_changed
        )
        self.terminal_manager.on_terminal_directory_changed = (
            self._on_terminal_directory_changed
        )
        self.terminal_manager.set_terminal_exit_handler(self._on_terminal_exit)
        self.tab_manager.on_quit_application = self._on_quit_application_requested
        self.tab_manager.on_detach_tab_requested = self._on_detach_tab_requested

        self.tab_manager.get_view_stack().connect(
            "notify::visible-child", self._on_tab_changed
        )

        self.settings_manager.add_change_listener(self._on_setting_changed)

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Handle changes from the settings manager."""
        if key in ["font", "color_scheme", "transparency"]:
            self.terminal_manager.apply_settings_to_all_terminals()
            if self.font_sizer_widget and key == "font":
                self.font_sizer_widget.update_display()

    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        self.connect("close-request", self._on_window_close_request)

    def _load_initial_data(self) -> None:
        """Load initial sessions and folders data efficiently."""
        try:
            sessions_data, folders_data = load_sessions_and_folders()
            load_sessions_to_store(self.session_store, sessions_data)
            load_folders_to_store(self.folder_store, folders_data)
            self.session_tree.refresh_tree()
            self.logger.info(
                f"Loaded {self.session_store.get_n_items()} sessions and {self.folder_store.get_n_items()} folders"
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
        self.toggle_sidebar_button.set_icon_name("sidebar-show-symbolic")

    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Handle sidebar toggle button."""
        is_visible = button.get_active()
        self.flap.set_reveal_flap(is_visible)
        self.settings_manager.set_sidebar_visible(is_visible)
        self._update_sidebar_button_icon()

    def _on_toggle_file_manager(self, button: Gtk.ToggleButton):
        """Toggle file manager for the current tab."""
        current_page_content = self.tab_manager.view_stack.get_visible_child()
        if not current_page_content:
            button.set_active(False)
            return

        current_page = self.tab_manager.view_stack.get_page(current_page_content)
        is_active = button.get_active()

        if is_active:
            active_terminal = self.tab_manager.get_selected_terminal()
            if not active_terminal:
                button.set_active(False)
                return
            file_manager = self.tab_file_managers.get(current_page)
            if not file_manager:
                try:
                    file_manager = FileManager(self, active_terminal)
                    self.tab_file_managers[current_page] = file_manager
                except Exception as e:
                    self.logger.error(f"Failed to create file manager: {e}")
                    button.set_active(False)
                    return
            self.current_file_manager = file_manager
            self.main_paned.set_end_child(file_manager.get_main_widget())
            last_pos = getattr(current_page, "_fm_paned_pos", self.get_height() - 250)
            self.main_paned.set_position(last_pos)
            file_manager.set_visibility(True)
        elif self.current_file_manager:
            if current_page:
                current_page._fm_paned_pos = self.main_paned.get_position()
            self.current_file_manager.set_visibility(False)
            self.main_paned.set_end_child(None)
            self.current_file_manager = None

    def _on_tab_changed(self, view_stack, param):
        """Handle tab changes by switching the file manager."""
        if self.current_file_manager:
            old_page_content = next(
                (
                    p.get_child()
                    for p, fm in self.tab_file_managers.items()
                    if fm == self.current_file_manager
                ),
                None,
            )
            if old_page_content:
                old_page = self.tab_manager.view_stack.get_page(old_page_content)
                if old_page:
                    old_page._fm_paned_pos = self.main_paned.get_position()
            self.current_file_manager.set_visibility(False)
            self.main_paned.set_end_child(None)
            self.current_file_manager = None

        current_page_content = view_stack.get_visible_child()
        if not current_page_content or not self.tab_manager.get_selected_terminal():
            self._sync_toggle_button_state()
            return

        current_page = self.tab_manager.view_stack.get_page(current_page_content)
        if file_manager := self.tab_file_managers.get(current_page):
            self.current_file_manager = file_manager
            self.main_paned.set_end_child(file_manager.get_main_widget())
            last_pos = getattr(current_page, "_fm_paned_pos", self.get_height() - 250)
            self.main_paned.set_position(last_pos)
            file_manager.set_visibility(True)

        self._sync_toggle_button_state()
        self._update_font_sizer_widget()
        self._update_tab_layout()

    def _sync_toggle_button_state(self):
        """Synchronize toggle button state with file manager visibility."""
        if toggle_button := self._get_file_manager_toggle_button():
            should_be_active = self.current_file_manager is not None
            if toggle_button.get_active() != should_be_active:
                handler_id = GLib.signal_handler_find(toggle_button, name="toggled")
                if handler_id > 0:
                    GLib.signal_handler_block(toggle_button, handler_id)
                toggle_button.set_active(should_be_active)
                if handler_id > 0:
                    GLib.signal_handler_unblock(toggle_button, handler_id)

    def _get_file_manager_toggle_button(self) -> Optional[Gtk.ToggleButton]:
        """Get the file manager toggle button from the header bar."""
        return getattr(self, "file_manager_button", None)

    def _on_terminal_exit(self, terminal, child_status, identifier):
        pass

    def _on_terminal_focus_changed(self, terminal, from_sidebar: bool) -> None:
        """Handle terminal focus change."""
        self._update_font_sizer_widget()

    def _on_terminal_directory_changed(
        self, terminal, new_title: str, osc7_info
    ) -> None:
        """Handle OSC7 directory change and update titles."""
        page = self.tab_manager.get_page_for_terminal(terminal)
        if page:
            self.tab_manager.set_tab_title(page, new_title)
        self._update_tab_layout()

    def _on_quit_application_requested(self) -> None:
        """Handle quit request from tab manager."""
        if app := self.get_application():
            app.quit()
        else:
            self.destroy()

    def _on_window_close_request(self, window) -> bool:
        self.logger.info("Window close request received")
        if self._force_closing:
            return Gdk.EVENT_PROPAGATE
        if self.terminal_manager.has_active_ssh_sessions():
            self._show_window_ssh_close_confirmation()
            return Gdk.EVENT_STOP
        self._perform_cleanup()
        return Gdk.EVENT_PROPAGATE

    def _show_window_ssh_close_confirmation(self) -> None:
        ssh_sessions = [
            info["identifier"].name
            for info in self.terminal_manager.registry._terminals.values()
            if info.get("type") == "ssh" and info.get("status") == "running"
        ]
        if not ssh_sessions:
            self._perform_cleanup()
            self.close()
            return
        session_list = "\n".join([f"• {name}" for name in ssh_sessions])
        body_text = f"{_('This window has active SSH connections:')}\n\n{session_list}\n\n{_('Closing will disconnect these sessions.')}\n\n{_('Are you sure you want to close this window?')}"
        dialog = Adw.MessageDialog(
            transient_for=self, title=_("Close Window"), body=body_text
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
        try:
            self.terminal_manager.cleanup_all_terminals()
            self.logger.info("Window cleanup completed")
        except Exception as e:
            self.logger.error(f"Window cleanup error: {e}")

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

    def _on_connect_sftp(self, action, param) -> None:
        selected_item = self.session_tree.get_selected_item()
        if isinstance(selected_item, SessionItem) and selected_item.is_ssh():
            self.toast_overlay.add_toast(Adw.Toast(title=_("SFTP not implemented yet")))

    def _on_new_local_tab(self, action, param) -> None:
        self.tab_manager.create_local_tab()

    def _on_close_tab(self, action, param) -> None:
        if self.tab_manager.active_tab:
            self.tab_manager._on_tab_close_button_clicked(
                None, self.tab_manager.active_tab
            )

    def _on_copy(self, action, param) -> None:
        self.tab_manager.copy_from_current_terminal()

    def _on_paste(self, action, param) -> None:
        self.tab_manager.paste_to_current_terminal()

    def _on_select_all(self, action, param) -> None:
        self.tab_manager.select_all_in_current_terminal()

    def _on_split_horizontal(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.split_horizontal(terminal)

    def _on_split_vertical(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.split_vertical(terminal)

    def _on_close_pane(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            self.tab_manager.close_pane(terminal)

    def _on_zoom_in(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() * 1.1)
            self._update_font_sizer_widget()

    def _on_zoom_out(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(terminal.get_font_scale() / 1.1)
            self._update_font_sizer_widget()

    def _on_zoom_reset(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            terminal.set_font_scale(1.0)
            self._update_font_sizer_widget()

    def _update_font_sizer_widget(self):
        if self.font_sizer_widget:
            self.font_sizer_widget.update_display()

    def _on_edit_session(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            found, position = self.session_store.find(item)
            if found:
                self._show_session_edit_dialog(item, position)

    def _on_duplicate_session(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            new_item = SessionItem.from_dict(item.to_dict())
            existing_names = {
                s.name for s in self.session_store if s.folder_path == item.folder_path
            }
            from ..helpers import generate_unique_name

            new_item.name = generate_unique_name(new_item.name, existing_names)
            self.session_tree.operations.add_session(new_item)
            self.refresh_tree()

    def _on_rename_session(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self._show_rename_dialog(item, True)

    def _on_move_session_to_folder(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            MoveSessionDialog(
                self, item, self.folder_store, self.session_tree.operations
            ).present()

    def _on_delete_selected_items(self, action=None, param=None) -> None:
        if items := self.session_tree.get_selected_items():
            self._show_delete_confirmation(items)

    def _on_edit_folder(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            found, position = self.folder_store.find(item)
            if found:
                self._show_folder_edit_dialog(item, position)

    def _on_rename_folder(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            self._show_rename_dialog(item, False)

    def _on_add_session_to_folder(self, action, param) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionFolder):
            self._show_session_edit_dialog(
                SessionItem(name=_("New Session"), folder_path=item.path), -1
            )

    def _on_cut_item(self, action, param) -> None:
        self.session_tree._cut_selected_item()

    def _on_copy_item(self, action, param) -> None:
        self.session_tree._copy_selected_item()

    def _on_paste_item(self, action, param) -> None:
        target_path = ""
        if item := self.session_tree.get_selected_item():
            target_path = (
                item.path if isinstance(item, SessionFolder) else item.folder_path
            )
        self.session_tree._paste_item(target_path)

    def _on_paste_item_root(self, action, param) -> None:
        self.session_tree._paste_item("")

    def _on_add_session_root(self, action, param) -> None:
        self._show_session_edit_dialog(SessionItem(name=_("New Session")), -1)

    def _on_add_folder_root(self, action, param) -> None:
        self._show_folder_edit_dialog(SessionFolder(name=_("New Folder")), None)

    def _on_preferences(self, action, param) -> None:
        dialog = PreferencesDialog(self, self.settings_manager)
        dialog.connect(
            "color-scheme-changed",
            lambda d, i: self.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "transparency-changed",
            lambda d, v: self.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect(
            "font-changed",
            lambda d, f: self.terminal_manager.apply_settings_to_all_terminals(),
        )
        dialog.connect("shortcut-changed", lambda d: self._update_keyboard_shortcuts())
        dialog.present()

    def _on_shortcuts(self, action, param) -> None:
        shortcuts_window = Gtk.ShortcutsWindow(transient_for=self, modal=True)
        section = Gtk.ShortcutsSection(
            title=_("Keyboard Shortcuts"), section_name="shortcuts"
        )
        terminal_group = Gtk.ShortcutsGroup(title=_("Terminal"))
        for title, accel in [
            (_("New Tab"), "<Control>t"),
            (_("Close Tab"), "<Control>w"),
            (_("New Window"), "<Control>n"),
            (_("Copy"), "<Control><Shift>c"),
            (_("Paste"), "<Control><Shift>v"),
            (_("Select All"), "<Control><Shift>a"),
        ]:
            terminal_group.append(Gtk.ShortcutsShortcut(title=title, accelerator=accel))

        app_group = Gtk.ShortcutsGroup(title=_("Application"))
        for title, accel in [
            (_("Preferences"), "<Control>comma"),
            (_("Toggle Sidebar"), "<Control><Shift>h"),
            (_("Quit"), "<Control>q"),
        ]:
            app_group.append(Gtk.ShortcutsShortcut(title=title, accelerator=accel))
        section.append(terminal_group)
        section.append(app_group)
        shortcuts_window.add_section(section)
        shortcuts_window.present()

    def _on_new_window(self, action, param) -> None:
        if app := self.get_application():
            if new_window := app.create_new_window():
                new_window.present()

    def _on_toggle_sidebar_action(self, action, param) -> None:
        self.toggle_sidebar_button.set_active(
            not self.toggle_sidebar_button.get_active()
        )

    def _on_add_session_clicked(self, button) -> None:
        self._show_session_edit_dialog(SessionItem(name=_("New Session")), -1)

    def _on_new_tab_clicked(self, button) -> None:
        self.tab_manager.create_local_tab()

    def _on_add_folder_clicked(self, button) -> None:
        self._show_folder_edit_dialog(SessionFolder(name=_("New Folder")), None)

    def _on_edit_selected_clicked(self, button) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self._on_edit_session(None, None)
        elif isinstance(item, SessionFolder):
            self._on_edit_folder(None, None)

    def _on_remove_selected_clicked(self, button) -> None:
        self._on_delete_selected_items()

    def _show_session_edit_dialog(self, session: SessionItem, position: int) -> None:
        SessionEditDialog(
            self, session, self.session_store, position, self.folder_store
        ).present()

    def _show_folder_edit_dialog(
        self, folder: Optional[SessionFolder], position: Optional[int]
    ) -> None:
        FolderEditDialog(
            self, self.folder_store, folder, position, is_new=position is None
        ).present()

    def _show_rename_dialog(
        self, item: Union[SessionItem, SessionFolder], is_session: bool
    ) -> None:
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
            if response_id == "rename":
                new_name = entry.get_text().strip()
                if new_name and new_name != item.name:
                    old_name = item.name
                    item.name = new_name
                    if is_session:
                        self.session_tree.operations._save_changes_with_backup(
                            "Session renamed"
                        )
                        log_session_event("renamed", f"{old_name} -> {new_name}")
                    else:
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
                        self.session_tree.operations._save_changes_with_backup(
                            "Folder renamed"
                        )
                    self.session_tree.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _show_delete_confirmation(
        self, items: List[Union[SessionItem, SessionFolder]]
    ) -> None:
        if not items:
            return
        count = len(items)
        title = _("Delete Item") if count == 1 else _("Delete Items")
        if count == 1:
            item = items[0]
            item_type = _("Session") if isinstance(item, SessionItem) else _("Folder")
            title = _("Delete {type}").format(type=item_type)
            has_children = isinstance(
                item, SessionFolder
            ) and self.session_tree.operations._folder_has_children(item.path)
            body_text = (
                _(
                    'The folder "{name}" is not empty. Are you sure you want to permanently delete it and all its contents?'
                ).format(name=item.name)
                if has_children
                else _('Are you sure you want to delete "{name}"?').format(
                    name=item.name
                )
            )
        else:
            body_text = _(
                "Are you sure you want to permanently delete these {count} items?"
            ).format(count=count)
            if any(
                isinstance(it, SessionFolder)
                and self.session_tree.operations._folder_has_children(it.path)
                for it in items
            ):
                body_text += "\n\n" + _(
                    "This will also delete all contents of any selected folders."
                )
        dialog = Adw.MessageDialog(transient_for=self, title=title, body=body_text)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "delete":
                for item in items:
                    if isinstance(item, SessionFolder):
                        self.session_tree.operations.remove_folder(item, force=True)
                    elif isinstance(item, SessionItem):
                        self.session_tree.operations.remove_session(item)
                self.session_tree.refresh_tree()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _show_info_dialog(self, title: str, message: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _update_keyboard_shortcuts(self) -> None:
        if app := self.get_application():
            app.refresh_keyboard_shortcuts()

    def refresh_tree(self) -> None:
        self.session_tree.refresh_tree()

    def get_terminal_manager(self) -> TerminalManager:
        return self.terminal_manager

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    def _on_open_url(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                launcher = Gtk.UriLauncher.new(url)
                launcher.launch(self, None, None, None)
                delattr(terminal, "_context_menu_url")

    def _on_copy_url(self, action, param) -> None:
        if terminal := self.tab_manager.get_selected_terminal():
            if hasattr(terminal, "_context_menu_url"):
                url = terminal._context_menu_url
                Gdk.Display.get_default().get_clipboard().set(url)
                delattr(terminal, "_context_menu_url")

    def create_ssh_tab(self, ssh_target: str) -> bool:
        session = SessionItem(name=ssh_target, session_type="ssh", host=ssh_target)
        self.tab_manager.create_ssh_tab(session)
        return True

    def create_execute_tab(
        self,
        command: str,
        working_directory: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> bool:
        self.tab_manager.create_local_tab(
            title=command,
            working_directory=working_directory,
            execute_command=command,
            close_after_execute=close_after_execute,
        )
        return True

    def create_local_tab(self, working_directory: str = None) -> bool:
        self.tab_manager.create_local_tab(working_directory=working_directory)
        return True

    def _on_detach_tab_requested(self, page_to_detach: Adw.ViewStackPage):
        """Orchestrates detaching a tab into a new window."""
        if self.tab_manager.get_tab_count() <= 1:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Cannot detach the last tab."))
            )
            return

        tab_widget = None
        for tab in self.tab_manager.tabs:
            if self.tab_manager.pages.get(tab) == page_to_detach:
                tab_widget = tab
                break

        if not tab_widget:
            self.logger.error("Could not find tab widget for page to detach.")
            return

        content = page_to_detach.get_child()
        if not content:
            self.logger.error("Page to detach has no content.")
            return

        title = tab_widget._base_title
        icon_name = getattr(tab_widget, "_icon_name", "computer-symbolic")

        # Unparent the content from the source ViewStack. This is the key step.
        self.tab_manager.view_stack.remove(content)

        # Manually perform the cleanup that _close_tab_by_page would do,
        # but without destroying the content.
        was_active = self.tab_manager.active_tab == tab_widget
        self.tab_manager.tab_bar_box.remove(tab_widget)
        self.tab_manager.tabs.remove(tab_widget)
        if tab_widget in self.tab_manager.pages:
            del self.tab_manager.pages[tab_widget]

        if was_active and self.tab_manager.tabs:
            self.tab_manager.set_active_tab(self.tab_manager.tabs[-1])
        elif not self.tab_manager.tabs:
            self.tab_manager.active_tab = None
            if self.get_application():
                self.close()  # Close window if it becomes empty

        app = self.get_application()
        new_window = app.create_new_window(_is_for_detached_tab=True)

        new_page = new_window.tab_manager.re_attach_detached_page(
            content, title, icon_name
        )

        if file_manager := self.tab_file_managers.pop(page_to_detach, None):
            file_manager.parent_window = new_window
            new_window.tab_file_managers[new_page] = file_manager

        new_window._update_tab_layout()
        new_window.present()

    def prepare_for_detach(self):
        """Prepares a newly created window to receive a detached tab by closing its initial empty tab."""
        if self.tab_manager.get_tab_count() > 0:
            self.tab_manager.close_active_tab()
