# ashyterm/window.py

import json
import os
from typing import Any, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Vte

from .filemanager.manager import FileManager
from .sessions.models import LayoutItem, SessionFolder, SessionItem
from .sessions.operations import SessionOperations
from .sessions.storage import (
    load_folders_to_store,
    load_sessions_and_folders,
    load_sessions_to_store,
)
from .sessions.tree import SessionTreeView
from .settings.config import APP_TITLE, LAYOUT_DIR, STATE_FILE
from .settings.manager import SettingsManager
from .terminal.manager import TerminalManager
from .terminal.tabs import TabManager
from .ui.actions import WindowActions
from .ui.menus import MainApplicationMenu
from .utils.exceptions import (
    UIError,
)
from .utils.logger import get_logger
from .utils.security import sanitize_session_name, validate_session_data
from .utils.translation_utils import _


class CommTerminalWindow(Adw.ApplicationWindow):
    """
    Main application window.
    Acts as the central controller, responsible for creating and wiring together
    all major components (managers) and handling user interactions.
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

        # Initial state from command line or other windows
        self.initial_working_directory = kwargs.get("initial_working_directory")
        self.initial_execute_command = kwargs.get("initial_execute_command")
        self.close_after_execute = kwargs.get("close_after_execute", False)
        self.initial_ssh_target = kwargs.get("initial_ssh_target")
        self._is_for_detached_tab = kwargs.get("_is_for_detached_tab", False)

        # Window setup
        self.set_default_size(1200, 700)
        self.set_title(APP_TITLE)
        self.set_icon_name("ashyterm")

        # Component Initialization
        self._create_managers()
        self._build_ui_and_connect_signals()

    def _create_managers(self) -> None:
        """
        Centralize Component Creation.
        This method acts as the "assembly line" for the application's main
        components. It creates and wires them together.
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
        self.tab_manager = None
        self.action_handler = None

        self.session_tree = SessionTreeView(
            self,
            self.session_store,
            self.folder_store,
            self.settings_manager,
            self.session_operations,
        )

        # On-demand created managers
        self.tab_file_managers = {}
        self.current_file_manager = None

    def _build_ui_and_connect_signals(self) -> None:
        """
        Builds the main UI structure and connects all necessary signals
        and callbacks between components.
        """
        self.font_sizer_widget = None
        self.fm_button_handler_id = 0
        self.single_tab_title_widget = None
        self.scrolled_tab_bar = None
        self.title_stack = None

        self._setup_styles()
        self._setup_ui()
        self._setup_actions()
        self._setup_callbacks()
        self._load_initial_data()
        self._setup_window_events()
        self._setup_keyboard_shortcuts()

        if not self._is_for_detached_tab:
            GLib.idle_add(self._create_initial_tab_safe)
        self.logger.info("Main window initialization completed")

    def _setup_keyboard_shortcuts(self) -> None:
        """Sets up window-level keyboard shortcuts for tab navigation."""
        controller = Gtk.EventControllerKey.new()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handles key press events for the main window."""
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_Page_Down:
                self.tab_manager.select_next_tab()
                return Gdk.EVENT_STOP
            if keyval == Gdk.KEY_Page_Up:
                self.tab_manager.select_previous_tab()
                return Gdk.EVENT_STOP

        elif state & Gdk.ModifierType.ALT_MASK:
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
        .drop-target { background-color: alpha(@theme_selected_bg_color, 0.5); }
        .sessions-tree .card {
            margin: 2px 4px;
            padding: 6px;
            border-radius: 6px;
        }
        .sessions-tree .card:hover {
            background-color: @theme_bg_color;
        }
        .sessions-tree row:selected .card {
            background-color: @accent_bg_color;
            color: @accent_fg_color;
        }
        .indented-session {
            margin-left: 20px;
        }
        .indented-session-depth-1 {
            margin-left: 20px;
        }
        .indented-session-depth-2 {
            margin-left: 40px;
        }
        .indented-session-depth-3 {
            margin-left: 60px;
        }
        .indented-session-depth-4 {
            margin-left: 80px;
        }
        paned separator { background: var(--view-bg-color); }
        popover.menu separator { background-color: color-mix(in srgb, var(--headerbar-border-color) var(--border-opacity), transparent); }
        #scrolled_tab_bar scrollbar trough { margin: 0px; }
        #scrolled_tab_bar button { min-width:24px; margin-right: 8px; }
        headerbar box { margin: 0px; padding-top:0px; padding-bottom:0px; }
        .custom-tab-button { padding-left: 12px; padding-right: 0px; border-radius: 10px; padding-top: 2px; padding-bottom: 2px; }
        .custom-tab-button.active { background: color-mix(in srgb, currentColor 12%, transparent); }
        .custom-tab-button:hover { background: color-mix(in srgb, currentColor 7%, transparent); }
        .sidebar-toolbar-view button {
            min-width: 28px;
            min-height: 28px;
            padding: 4px;
            margin: 0;
            border-radius: 4px;
            transition: all 200ms ease;
            border: none;
            background: transparent;
        }
        .sidebar-toolbar-view button:hover {
            background-color: alpha(@accent_bg_color, 0.1);
            transform: scale(1.05);
        }
        .sidebar-toolbar-view button:active {
            background-color: alpha(@accent_bg_color, 0.2);
            transform: scale(0.95);
        }
        .sidebar-toolbar-view button.destructive:hover {
            background-color: alpha(@destructive_color, 0.1);
        }
        .sidebar-search {
            padding: 8px 12px;
        }
        .sidebar-session-tree row:hover {
            background-color: alpha(@theme_selected_bg_color, 0.4);
            transition: background-color 150ms ease;
        }
        .sidebar-session-tree row:selected {
            background-color: @accent_bg_color;
            color: @accent_fg_color;
            font-weight: 500;
        }
        .sidebar-session-tree row:selected:hover {
            background-color: alpha(@accent_bg_color, 0.9);
        }
        .sidebar-session-tree row:active {
            background-color: alpha(@accent_bg_color, 0.6);
            transition: background-color 100ms ease;
        }
        /* Enhanced button feedback */
        .sidebar-toolbar-view button {
            transition: all 200ms cubic-bezier(0.4, 0, 0.2, 1);
        }
        .sidebar-toolbar-view button:hover {
            background-color: alpha(@accent_bg_color, 0.12);
            transform: scale(1.02);
            box-shadow: 0 1px 3px alpha(@accent_bg_color, 0.2);
        }
        .sidebar-toolbar-view button:active {
            background-color: alpha(@accent_bg_color, 0.18);
            transform: scale(0.98);
            transition-duration: 100ms;
        }
        .sidebar-toolbar-view button.destructive:hover {
            background-color: alpha(@destructive_color, 0.12);
            box-shadow: 0 1px 3px alpha(@destructive_color, 0.2);
        }
        .sidebar-toolbar-view button.destructive:active {
            background-color: alpha(@destructive_color, 0.18);
        }
        /* Responsive design improvements */
            .sidebar-toolbar-view {
                min-width: 350px;
            }
        /* Enhanced toolbar view styling */
        .sidebar-toolbar-view {
            background: @sidebar_bg_color;
            border-right: 1px solid var(--border-color, @borders);
            border-left: 1px solid var(--border-color, @borders);
        }

        .sidebar-session-tree row {
            padding: 4px 12px;
            margin: 1px 8px;
            border-radius: 4px;
            transition: background-color 150ms ease;
        }
        .sidebar-session-tree row:hover {
            background-color: alpha(@theme_selected_bg_color, 0.3);
        }
        .sidebar-session-tree row:selected {
            background-color: @accent_bg_color;
            color: @accent_fg_color;
        }
        .sidebar-session-tree row:selected:hover {
            background-color: alpha(@accent_bg_color, 0.5);
        }
        .top-bar {
            background: var(--secondary-sidebar-bg-color);
        }
        /* Sidebar toggle button styling - hover only, no active background */
        .sidebar-toggle-button:active,
        .sidebar-toggle-button:checked {
            background: transparent;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _create_initial_tab_safe(self) -> bool:
        """Safely create initial tab, trying to restore session first."""
        try:
            if not self._restore_session_state():
                if self.tab_manager.get_tab_count() == 0:
                    if self.initial_ssh_target:
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
        """Set up window-level actions by delegating to the action handler."""
        try:
            actions_map = {
                "new-local-tab": self.action_handler.new_local_tab,
                "close-tab": self.action_handler.close_tab,
                "copy": self.action_handler.copy,
                "paste": self.action_handler.paste,
                "select-all": self.action_handler.select_all,
                "split-horizontal": self.action_handler.split_horizontal,
                "split-vertical": self.action_handler.split_vertical,
                "close-pane": self.action_handler.close_pane,
                "open-url": self.action_handler.open_url,
                "copy-url": self.action_handler.copy_url,
                "zoom-in": self.action_handler.zoom_in,
                "zoom-out": self.action_handler.zoom_out,
                "zoom-reset": self.action_handler.zoom_reset,
                "connect-sftp": self.action_handler.connect_sftp,
                "edit-session": self.action_handler.edit_session,
                "duplicate-session": self.action_handler.duplicate_session,
                "rename-session": self.action_handler.rename_session,
                "move-session-to-folder": self.action_handler.move_session_to_folder,
                "delete-session": self.action_handler.delete_selected_items,
                "edit-folder": self.action_handler.edit_folder,
                "rename-folder": self.action_handler.rename_folder,
                "add-session-to-folder": self.action_handler.add_session_to_folder,
                "delete-folder": self.action_handler.delete_selected_items,
                "cut-item": self.action_handler.cut_item,
                "copy-item": self.action_handler.copy_item,
                "paste-item": self.action_handler.paste_item,
                "paste-item-root": self.action_handler.paste_item_root,
                "add-session-root": self.action_handler.add_session_root,
                "add-folder-root": self.action_handler.add_folder_root,
                "toggle-sidebar": self.action_handler.toggle_sidebar_action,
                "toggle-file-manager": self.action_handler.toggle_file_manager,
                "preferences": self.action_handler.preferences,
                "shortcuts": self.action_handler.shortcuts,
                "new-window": self.action_handler.new_window,
                "save-layout": self.action_handler.save_layout,
                "move-layout-to-folder": self.action_handler.move_layout_to_folder,
            }
            for name, callback in actions_map.items():
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", callback)
                self.add_action(action)

            restore_action = Gio.SimpleAction.new(
                "restore_layout", GLib.VariantType.new("s")
            )
            restore_action.connect("activate", self.action_handler.restore_layout)
            self.add_action(restore_action)

            delete_action = Gio.SimpleAction.new(
                "delete_layout", GLib.VariantType.new("s")
            )
            delete_action.connect("activate", self.action_handler.delete_layout)
            self.add_action(delete_action)

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

            # Create popover for auto-hide mode
            self.sidebar_popover = Gtk.Popover()
            self.sidebar_popover.set_position(Gtk.PositionType.BOTTOM)
            self.sidebar_popover.set_has_arrow(True)
            self.sidebar_popover.connect("closed", self._on_sidebar_popover_closed)
            # Connect to show signal to update size when popover becomes visible
            self.sidebar_popover.connect("show", self._on_sidebar_popover_show)

            # Allow popover to close when clicking outside
            # Remove the click controller that was preventing auto-hide
            # The popover will now close automatically when clicking outside

            # Initial size will be set when popover is shown
            self.sidebar_popover.set_size_request(200, 800)

            popover_provider = Gtk.CssProvider()
            self.sidebar_popover.get_style_context().add_provider(
                popover_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            content_box = self._create_content_area()
            self.flap.set_content(content_box)

            main_box.append(self.flap)
            self.set_content(main_box)

            initial_visible = self.settings_manager.get_sidebar_visible()
            auto_hide = self.settings_manager.get("auto_hide_sidebar", False)

            if auto_hide:
                # In auto-hide mode, don't reveal flap and set up popover
                self.flap.set_reveal_flap(False)
                self.flap.set_flap(None)
                self.sidebar_popover.set_child(self.sidebar_box)
                self.toggle_sidebar_button.set_active(False)
            else:
                # Normal mode: use flap
                self.flap.set_flap(self.sidebar_box)
                self.flap.set_reveal_flap(initial_visible)
                self.toggle_sidebar_button.set_active(initial_visible)

                # Set up responsive design handling
                self.flap.connect("notify::folded", self._on_sidebar_folded_changed)
                self._update_sidebar_responsive_design()
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
        self.toggle_sidebar_button.add_css_class("sidebar-toggle-button")
        self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
        header_bar.pack_start(self.toggle_sidebar_button)

        self.file_manager_button = Gtk.ToggleButton(
            icon_name="folder-open-symbolic", tooltip_text=_("File Manager")
        )
        self.fm_button_handler_id = self.file_manager_button.connect(
            "toggled", self._on_toggle_file_manager
        )
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

        self.scrolled_tab_bar = Gtk.ScrolledWindow()
        self.scrolled_tab_bar.set_name("scrolled_tab_bar")
        self.scrolled_tab_bar.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_tab_bar.set_propagate_natural_height(True)
        self.scrolled_tab_bar.set_hexpand(True)

        self.tab_manager = TabManager(
            self.terminal_manager,
            on_quit_callback=self._on_quit_application_requested,
            on_detach_tab_callback=self._on_detach_tab_requested,
            scrolled_tab_bar=self.scrolled_tab_bar,
            on_tab_count_changed=self._update_tab_layout,
        )
        self.terminal_manager.set_tab_manager(self.tab_manager)
        self.action_handler = WindowActions(self)

        self.tab_bar = self.tab_manager.get_tab_bar()
        self.tab_bar.add_css_class("custom-tab-bar")
        self.scrolled_tab_bar.set_child(self.tab_bar)

        scroll_controller = Gtk.EventControllerScroll.new(
            flags=Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self._on_tab_bar_scroll)
        self.scrolled_tab_bar.add_controller(scroll_controller)

        self.single_tab_title_widget = Adw.WindowTitle(title=APP_TITLE)

        self.title_stack = Gtk.Stack()
        self.title_stack.add_named(self.scrolled_tab_bar, "tabs-view")
        self.title_stack.add_named(self.single_tab_title_widget, "title-view")
        header_bar.set_title_widget(self.title_stack)

        return header_bar

    def _on_tab_bar_scroll(self, controller, dx, dy):
        """Handle mouse scroll events on the tab bar to scroll horizontally."""
        scrolled_window = controller.get_widget()
        hadjustment = scrolled_window.get_hadjustment()
        scroll_speed = 25
        new_value = hadjustment.get_value() + dy * scroll_speed
        lower = hadjustment.get_lower()
        upper = hadjustment.get_upper() - hadjustment.get_page_size()
        new_value = max(lower, min(new_value, upper))
        hadjustment.set_value(new_value)
        return True

    def _create_sidebar(self) -> Gtk.Widget:
        """Create the sidebar with session tree."""
        toolbar_view = Adw.ToolbarView(
            css_classes=["background", "sidebar-toolbar-view"]
        )

        # Add action buttons at the top
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.set_halign(Gtk.Align.CENTER)

        add_session_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_session_button.set_tooltip_text(_("Add Session"))
        add_session_button.connect("clicked", self._on_add_session_clicked)
        toolbar.append(add_session_button)

        add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
        add_folder_button.set_tooltip_text(_("Add Folder"))
        add_folder_button.connect("clicked", self._on_add_folder_clicked)
        toolbar.append(add_folder_button)

        edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_button.set_tooltip_text(_("Edit Selected"))
        edit_button.connect("clicked", self._on_edit_selected_clicked)
        toolbar.append(edit_button)

        save_layout_button = Gtk.Button.new_from_icon_name("document-save-symbolic")
        save_layout_button.set_tooltip_text(_("Save Current Layout"))
        save_layout_button.connect("clicked", self.save_current_layout)
        toolbar.append(save_layout_button)

        remove_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_button.set_tooltip_text(_("Remove Selected"))
        remove_button.connect("clicked", self._on_remove_selected_clicked)
        remove_button.add_css_class("destructive")
        toolbar.append(remove_button)

        toolbar_view.add_top_bar(toolbar)

        scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_child(self.session_tree.get_widget())
        scrolled_window.add_css_class("sidebar-session-tree")
        toolbar_view.set_content(scrolled_window)

        # Add search entry at the bottom with margins
        search_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_container.add_css_class("sidebar-search")
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search sessions..."))
        self.search_entry.set_margin_start(6)
        self.search_entry.set_margin_end(6)
        self.search_entry.connect("search-changed", self._on_search_changed)

        # Add key controller for search entry navigation
        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(search_key_controller)

        search_container.append(self.search_entry)
        toolbar_view.add_bottom_bar(search_container)

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
            "realize", lambda _: self.main_paned.set_position(self.get_height() - 250)
        )
        self.toast_overlay = Adw.ToastOverlay(child=main_paned)

        return self.toast_overlay

    def _update_tab_layout(self):
        """Update tab layout and window title based on tab count."""
        tab_count = self.tab_manager.get_tab_count()
        self.set_title(APP_TITLE)

        if tab_count > 1:
            self.title_stack.set_visible_child_name("tabs-view")
        else:
            self.title_stack.set_visible_child_name("title-view")
            if tab_count == 1:
                visible_child = self.tab_manager.view_stack.get_visible_child()
                if visible_child:
                    page = self.tab_manager.view_stack.get_page(visible_child)
                    page_title = page.get_title() or _("New Tab")
                    self.single_tab_title_widget.set_title(
                        f"{APP_TITLE} - {page_title}"
                    )
                else:
                    self.single_tab_title_widget.set_title(APP_TITLE)
            else:
                self.single_tab_title_widget.set_title(APP_TITLE)

    def _setup_callbacks(self) -> None:
        """Set up callbacks between components."""
        self.session_tree.on_session_activated = self._on_session_activated
        self.session_tree.on_layout_activated = self.restore_saved_layout
        self.session_tree.on_folder_expansion_changed = (
            self._on_folder_expansion_changed
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

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Handle changes from the settings manager."""
        if key == "auto_hide_sidebar":
            self._handle_auto_hide_change(new_value)
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

    def _setup_window_events(self) -> None:
        """Set up window-level event handlers."""
        self.connect("close-request", self._on_window_close_request)
        # Update popover size when window is resized
        self.connect("notify::default-width", self._on_window_size_changed)
        self.connect("notify::default-height", self._on_window_size_changed)

    def _load_initial_data(self) -> None:
        """Load initial sessions and folders data efficiently."""
        try:
            self._load_layouts()
            sessions_data, folders_data = load_sessions_and_folders()
            load_sessions_to_store(self.session_store, sessions_data)
            load_folders_to_store(self.folder_store, folders_data)
            self.session_tree.refresh_tree()
            self.logger.info(
                f"Loaded {self.session_store.get_n_items()} sessions, "
                f"{self.folder_store.get_n_items()} folders, "
                f"and {len(self.layouts)} layouts"
            )

            # Update sidebar size after loading initial data
            self._update_sidebar_sizes()
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
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
        if auto_hide:
            # In auto-hide mode, always show the reveal icon since it's a popover
            self.toggle_sidebar_button.set_icon_name("view-reveal-symbolic")
        else:
            # In normal mode, show appropriate icon based on visibility
            is_visible = self.flap.get_reveal_flap()
            icon_name = (
                "sidebar-hide-symbolic" if is_visible else "sidebar-show-symbolic"
            )
            self.toggle_sidebar_button.set_icon_name(icon_name)

    def _focus_search_entry(self):
        """Focus the search entry in the sidebar when popover opens."""
        if hasattr(self, "search_entry") and self.search_entry:
            self.search_entry.grab_focus()
        return False

    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Handle sidebar toggle button."""
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)

        if auto_hide:
            # In auto-hide mode, use popover instead of flap
            if button.get_active():
                # Move sidebar from flap to popover if needed
                current_parent = self.sidebar_box.get_parent()
                if current_parent == self.flap:
                    self.flap.set_flap(None)
                elif current_parent and current_parent != self.sidebar_popover:
                    # Remove from any other parent
                    if hasattr(current_parent, "remove"):
                        current_parent.remove(self.sidebar_box)
                    elif hasattr(current_parent, "set_child"):
                        current_parent.set_child(None)

                # Only set as child if it's not already the child
                if self.sidebar_popover.get_child() != self.sidebar_box:
                    self.sidebar_popover.set_child(self.sidebar_box)

                # Update popover size based on current window dimensions
                self._update_popover_size()

                if self.sidebar_popover.get_parent() is not None:
                    self.sidebar_popover.unparent()
                self.sidebar_popover.set_parent(button)
                self.sidebar_popover.popup()
                # Focus the search entry when popover opens
                GLib.idle_add(self._focus_search_entry)
                # Keep button active while popover is open
                button.set_active(True)
            else:
                self.sidebar_popover.popdown()
                button.set_active(False)
        else:
            # Normal mode: use flap
            current_parent = self.sidebar_box.get_parent()
            if current_parent == self.sidebar_popover:
                self.sidebar_popover.set_child(None)
            elif current_parent and current_parent != self.flap:
                # Remove from any other parent
                if hasattr(current_parent, "remove"):
                    current_parent.remove(self.sidebar_box)
                elif hasattr(current_parent, "set_child"):
                    current_parent.set_child(None)

            # Only set flap if it's not already set
            if self.flap.get_flap() != self.sidebar_box:
                self.flap.set_flap(self.sidebar_box)
            is_visible = button.get_active()
            self.flap.set_reveal_flap(is_visible)
            self.settings_manager.set_sidebar_visible(is_visible)

            # Update flap size when showing in normal mode
            if is_visible:
                self._update_flap_size()

        self._update_sidebar_button_icon()

    def _on_sidebar_popover_show(self, popover):
        """Handle sidebar popover show event to update size."""
        try:
            # Update size when popover is shown
            self._update_popover_size()
        except Exception as e:
            self.logger.debug(f"Error updating popover size on show: {e}")

    def _on_sidebar_popover_closed(self, popover):
        """Handle sidebar popover closing."""
        self.toggle_sidebar_button.set_active(False)
        self._update_sidebar_button_icon()
        # Clear the search entry and filter when popover closes
        if hasattr(self, "search_entry") and self.search_entry:
            self.search_entry.set_text("")
            # Clear the filter and restore original expansion state
            self.session_tree.clear_search()

    def _update_popover_size(self):
        """Update popover size based on current window dimensions and content."""
        try:
            # Use calculated natural width instead of fixed setting
            sidebar_width = self._calculate_sidebar_natural_width()

            # Get current window dimensions
            window_width = self.get_width()
            window_height = self.get_height()

            # Calculate optimal popover size
            # Width: sidebar_width, but not wider than 80% of window
            popover_width = min(sidebar_width, int(window_width * 0.8))

            # Height: Use 90% of total terminal height
            popover_height = int(window_height * 0.9)

            self.sidebar_popover.set_size_request(popover_width, popover_height)

            self.logger.debug(
                f"Updated popover size: {popover_width}x{popover_height}px "
                f"(window: {window_width}x{window_height}, sidebar: {sidebar_width}px)"
            )

        except Exception as e:
            self.logger.warning(f"Failed to update popover size: {e}")
            # Fallback to default size
            self.sidebar_popover.set_size_request(200, 800)

    def _handle_auto_hide_change(self, auto_hide_enabled: bool):
        """Handle changes to auto_hide_sidebar setting."""
        if auto_hide_enabled:
            # Switch to auto-hide mode
            current_parent = self.sidebar_box.get_parent()
            if current_parent == self.flap:
                self.flap.set_flap(None)
            elif current_parent and current_parent != self.sidebar_popover:
                # Remove from any other parent
                if hasattr(current_parent, "remove"):
                    current_parent.remove(self.sidebar_box)
                elif hasattr(current_parent, "set_child"):
                    current_parent.set_child(None)

            # Only set as child if it's not already the child
            if self.sidebar_popover.get_child() != self.sidebar_box:
                self.sidebar_popover.set_child(self.sidebar_box)

            self.flap.set_reveal_flap(False)
            self.sidebar_popover.popdown()
            self.toggle_sidebar_button.set_active(False)
        else:
            # Switch to normal mode
            current_parent = self.sidebar_box.get_parent()
            if current_parent == self.sidebar_popover:
                self.sidebar_popover.set_child(None)
            elif current_parent and current_parent != self.flap:
                # Remove from any other parent
                if hasattr(current_parent, "remove"):
                    current_parent.remove(self.sidebar_box)
                elif hasattr(current_parent, "set_child"):
                    current_parent.set_child(None)

            # Only set flap if it's not already set
            if self.flap.get_flap() != self.sidebar_box:
                self.flap.set_flap(self.sidebar_box)
            initial_visible = self.settings_manager.get_sidebar_visible()
            self.flap.set_reveal_flap(initial_visible)
            self.toggle_sidebar_button.set_active(initial_visible)

            # Update flap size when switching to normal mode
            if initial_visible:
                self._update_flap_size()

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
                    file_manager = FileManager(
                        self, self.terminal_manager, active_terminal
                    )
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

    def _on_tab_changed(self, view_stack, _param):
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
            self._update_tab_layout()
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
                if self.fm_button_handler_id > 0:
                    toggle_button.handler_block(self.fm_button_handler_id)
                toggle_button.set_active(should_be_active)
                if self.fm_button_handler_id > 0:
                    toggle_button.handler_unblock(self.fm_button_handler_id)

    def _get_file_manager_toggle_button(self) -> Optional[Gtk.ToggleButton]:
        """Get the file manager toggle button from the header bar."""
        return getattr(self, "file_manager_button", None)

    def _on_terminal_exit(self, terminal, child_status, identifier):
        pass

    def _on_terminal_focus_changed(self, terminal, _from_sidebar: bool) -> None:
        """Handle terminal focus change, rebinding file manager if necessary."""
        self._update_font_sizer_widget()
        if self.current_file_manager:
            self.current_file_manager.rebind_terminal(terminal)

    def _find_parent_toolbar_view(
        self, widget: Gtk.Widget
    ) -> Optional[Adw.ToolbarView]:
        """Traverse up the widget tree to find the parent Adw.ToolbarView."""
        parent = widget.get_parent()
        while parent:
            if isinstance(parent, Adw.ToolbarView):
                return parent
            parent = parent.get_parent()
        return None

    def _on_terminal_directory_changed(
        self, terminal, new_title: str, osc7_info
    ) -> None:
        """Handle OSC7 directory change and update titles for both tab and pane."""
        page = self.tab_manager.get_page_for_terminal(terminal)
        if page:
            self.tab_manager.set_tab_title(page, new_title)

        toolbar_view = self._find_parent_toolbar_view(terminal)
        if toolbar_view and hasattr(toolbar_view, "title_label"):
            toolbar_view.title_label.set_text(new_title)

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

        policy = self.settings_manager.get("session_restore_policy", "never")

        if policy == "ask":
            self._show_save_session_dialog()
            return Gdk.EVENT_STOP

        if policy == "always":
            self._save_session_state()
        else:  # "never"
            self._clear_session_state()

        return self._continue_close_process()

    def _on_window_size_changed(self, window, param):
        """Handle window size changes to update popover size."""
        if self.sidebar_popover.get_visible():
            self._update_popover_size()

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
            self._save_session_state()
            self._continue_close_process(force_close=True)
        elif response_id == "dont-save":
            self._clear_session_state()
            self._continue_close_process(force_close=True)
        # If "cancel", do nothing.

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

    def _on_folder_expansion_changed(self) -> None:
        """Handle folder expansion changes by updating sidebar width if needed"""
        try:
            if hasattr(self, "_auto_size_sidebar") and self._auto_size_sidebar:
                # Defer the resize to avoid conflicts during expansion animation
                GLib.idle_add(self._apply_sidebar_autoresize)
        except Exception as e:
            self.logger.error(f"Error handling folder expansion change: {e}")

    def _on_add_session_clicked(self, _button) -> None:
        self.action_handler.add_session_root(None, None)
        # Close popover if it's open (auto-hide mode)
        if hasattr(self, "sidebar_popover") and self.sidebar_popover.get_visible():
            self.sidebar_popover.popdown()

    def _on_new_tab_clicked(self, _button) -> None:
        self.action_handler.new_local_tab(None, None)

    def _on_add_folder_clicked(self, _button) -> None:
        self.action_handler.add_folder_root(None, None)
        # Close popover if it's open (auto-hide mode)
        if hasattr(self, "sidebar_popover") and self.sidebar_popover.get_visible():
            self.sidebar_popover.popdown()

    def _on_edit_selected_clicked(self, _button) -> None:
        if isinstance(item := self.session_tree.get_selected_item(), SessionItem):
            self.action_handler.edit_session(None, None)
        elif isinstance(item, SessionFolder):
            self.action_handler.edit_folder(None, None)

    def _on_remove_selected_clicked(self, _button) -> None:
        self.action_handler.delete_selected_items()

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        """Handle search entry changes by updating the tree filter."""
        search_text = search_entry.get_text().lower()

        if not search_text:
            # If search is cleared, use the new clear_search method
            self.session_tree.clear_search()
        else:
            # Otherwise, set the filter text normally
            self.session_tree.set_filter_text(search_text)

        # Update sidebar size after filter changes
        self._update_sidebar_sizes()

    def _on_search_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle key presses in the search entry for navigation."""
        if keyval == Gdk.KEY_Up:
            # Move focus to the session tree when pressing up arrow
            self.session_tree.get_widget().grab_focus()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

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

    def _update_font_sizer_widget(self):
        if self.font_sizer_widget:
            self.font_sizer_widget.update_display()

    def refresh_tree(self) -> None:
        self.session_tree.refresh_tree()
        # Update sidebar size after tree refresh
        self._update_sidebar_sizes()
        # Also update popover size if it's currently visible
        if hasattr(self, "sidebar_popover") and self.sidebar_popover.get_visible():
            GLib.idle_add(self._update_popover_size)

    def get_terminal_manager(self) -> TerminalManager:
        return self.terminal_manager

    def destroy(self) -> None:
        self._perform_cleanup()
        super().destroy()

    def create_ssh_tab(self, session: SessionItem) -> Optional[Vte.Terminal]:
        return self.tab_manager.create_ssh_tab(session)

    def create_execute_tab(
        self,
        command: str,
        working_directory: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Optional[Vte.Terminal]:
        return self.tab_manager.create_local_tab(
            title=command,
            working_directory=working_directory,
            execute_command=command,
            close_after_execute=close_after_execute,
        )

    def create_local_tab(self, working_directory: str = None) -> Optional[Vte.Terminal]:
        return self.tab_manager.create_local_tab(working_directory=working_directory)

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
            self.logger.error("Could not find tab widget for page to detach.")
            return

        content = page_to_detach.get_child()
        if not content:
            self.logger.error("Page to detach has no content.")
            return

        title = tab_widget._base_title
        icon_name = getattr(tab_widget, "_icon_name", "computer-symbolic")

        self.tab_manager.view_stack.remove(content)

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
                self.close()

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

    def _save_session_state(self):
        state = {"tabs": []}
        for page in self.tab_manager.pages.values():
            tab_content = page.get_child()
            if tab_content:
                tab_structure = self._serialize_widget_tree(tab_content)
                if tab_structure:
                    state["tabs"].append(tab_structure)

        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            self.logger.info("Session state saved successfully.")
        except Exception as e:
            self.logger.error(f"Failed to save session state: {e}")

    def _clear_session_state(self):
        """Removes the state file to prevent restoration on next startup."""
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
                self.logger.info("Session state file removed.")
            except OSError as e:
                self.logger.error(f"Failed to remove state file: {e}")

    def _serialize_widget_tree(self, widget):
        if isinstance(widget, Gtk.Paned):
            position = widget.get_position()
            orientation = widget.get_orientation()
            total_size = (
                widget.get_width()
                if orientation == Gtk.Orientation.HORIZONTAL
                else widget.get_height()
            )
            position_ratio = position / total_size if total_size > 0 else 0.5

            return {
                "type": "paned",
                "orientation": "horizontal"
                if orientation == Gtk.Orientation.HORIZONTAL
                else "vertical",
                "position_ratio": position_ratio,
                "child1": self._serialize_widget_tree(widget.get_start_child()),
                "child2": self._serialize_widget_tree(widget.get_end_child()),
            }

        terminal = None
        if isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminal = widget.get_child()
        elif hasattr(widget, "terminal") and isinstance(widget.terminal, Vte.Terminal):
            terminal = widget.terminal
        elif isinstance(widget, Adw.Bin):
            return self._serialize_widget_tree(widget.get_child())

        if terminal:
            terminal_id = getattr(terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            if not info:
                return None

            uri = terminal.get_current_directory_uri()
            working_dir = None
            if uri:
                from urllib.parse import unquote, urlparse

                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    working_dir = unquote(parsed_uri.path)

            session_info = info.get("identifier")
            if isinstance(session_info, SessionItem):
                return {
                    "type": "terminal",
                    "session_type": "ssh",
                    "session_name": session_info.name,
                    "working_dir": working_dir,
                }
            else:
                return {
                    "type": "terminal",
                    "session_type": "local",
                    "session_name": session_info,
                    "working_dir": working_dir,
                }
        return None

    def _restore_session_state(self) -> bool:
        policy = self.settings_manager.get("session_restore_policy", "never")

        if policy == "never":
            self._clear_session_state()
            return False

        if not os.path.exists(STATE_FILE):
            return False

        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to read session state file: {e}")
            return False

        if not state.get("tabs"):
            return False

        self.logger.info(f"Restoring {len(state['tabs'])} tabs from previous session.")
        for tab_structure in state["tabs"]:
            self.tab_manager.recreate_tab_from_structure(tab_structure)

        self._clear_session_state()
        return True

    def save_current_layout(self, button=None):
        """Prompts for a name and saves the current window layout."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Save Layout"),
            body=_("Enter a name for the current layout:"),
            close_response="cancel",
        )
        entry = Gtk.Entry(
            placeholder_text=_("e.g., 'My Dev Setup'"),
            hexpand=True,
            activates_default=True,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._on_save_layout_dialog_response, entry)
        dialog.present()
        # Close popover if it's open (auto-hide mode)
        if hasattr(self, "sidebar_popover") and self.sidebar_popover.get_visible():
            self.sidebar_popover.popdown()

    def _on_save_layout_dialog_response(self, dialog, response_id, entry):
        dialog.close()
        if response_id == "save":
            layout_name = entry.get_text().strip()
            if not layout_name:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("Layout name cannot be empty."))
                )
                return

            sanitized_name = sanitize_session_name(layout_name).replace(" ", "_")
            target_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

            if os.path.exists(target_file):
                self.logger.warning(f"Overwriting existing layout: {sanitized_name}")

            state = {"tabs": [], "folder_path": ""}
            for page in self.tab_manager.pages.values():
                tab_content = page.get_child()
                if tab_content:
                    tab_structure = self._serialize_widget_tree(tab_content)
                    if tab_structure:
                        state["tabs"].append(tab_structure)

            try:
                with open(target_file, "w") as f:
                    json.dump(state, f, indent=2)
                self.logger.info(f"Layout '{layout_name}' saved successfully.")
                self.toast_overlay.add_toast(Adw.Toast(title=_("Layout Saved")))
                self._load_layouts()
                self.refresh_tree()
            except Exception as e:
                self.logger.error(f"Failed to save layout '{layout_name}': {e}")
                self._show_error_dialog(_("Error Saving Layout"), str(e))

    def restore_saved_layout(self, layout_name: str):
        """Restores a previously saved layout, replacing the current one."""
        sanitized_name = sanitize_session_name(layout_name).replace(" ", "_")
        layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

        def _show_confirmation_dialog():
            """Inner function to create and show the modal dialog."""
            if not os.path.exists(layout_file):
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("Saved layout not found."))
                )
                return

            # Show dialog immediately without delay
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=_("Restore Saved Layout?"),
                body=_(
                    "This will close all current tabs and restore the '{name}' layout. Are you sure?"
                ).format(name=layout_name),
                close_response="cancel",
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("restore", _("Restore Layout"))
            dialog.set_response_appearance(
                "restore", Adw.ResponseAppearance.DESTRUCTIVE
            )
            dialog.set_default_response("cancel")
            dialog.connect(
                "response", self._on_restore_layout_dialog_response, layout_file
            )
            dialog.present()

        # Check if the sidebar popover is visible. If so, close it first
        # and only show the dialog after the popover has fully closed.
        if (
            self.settings_manager.get("auto_hide_sidebar", False)
            and self.sidebar_popover.get_visible()
        ):
            self.logger.info(
                "Sidebar popover is visible. Deferring restore confirmation."
            )

            def on_popover_closed(popover):
                popover.disconnect(handler_id)
                _show_confirmation_dialog()

            handler_id = self.sidebar_popover.connect("closed", on_popover_closed)
            self.sidebar_popover.popdown()
        else:
            # If the popover isn't a factor, show the dialog immediately.
            _show_confirmation_dialog()

    def _on_restore_layout_dialog_response(self, dialog, response_id, layout_file):
        dialog.close()
        if response_id == "restore":
            self._perform_layout_restore(layout_file)

    def _perform_layout_restore(self, layout_file: str):
        """Closes all tabs and restores the layout from the file."""
        try:
            with open(layout_file, "r") as f:
                state = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to read layout file: {layout_file}: {e}")
            self._show_error_dialog(_("Error Restoring Layout"), str(e))
            return

        if not state.get("tabs"):
            self.logger.warning(f"Layout file '{layout_file}' is empty or invalid.")
            return

        self._close_all_tabs()
        GLib.idle_add(self._recreate_tabs_from_state, state)

    def _recreate_tabs_from_state(self, state):
        self.logger.info(f"Restoring {len(state['tabs'])} tabs from saved layout.")
        for tab_structure in state["tabs"]:
            self.tab_manager.recreate_tab_from_structure(tab_structure)
        return False

    def _close_all_tabs(self):
        """Closes all currently open tabs."""
        for tab_widget in self.tab_manager.tabs[:]:
            self.tab_manager._on_tab_close_button_clicked(None, tab_widget)

    def delete_saved_layout(self, layout_name: str, confirm: bool = True):
        """Deletes a saved layout file."""
        if confirm:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=_("Delete Layout?"),
                body=_(
                    "Are you sure you want to permanently delete the layout '{name}'?"
                ).format(name=layout_name),
                close_response="cancel",
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("delete", _("Delete"))
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect(
                "response", self._on_delete_layout_dialog_response, layout_name
            )
            dialog.present()
        else:
            self._perform_delete_layout(layout_name)

    def _on_delete_layout_dialog_response(self, dialog, response_id, layout_name):
        dialog.close()
        if response_id == "delete":
            self._perform_delete_layout(layout_name)

    def _perform_delete_layout(self, layout_name: str):
        try:
            sanitized_name = sanitize_session_name(layout_name).replace(" ", "_")
            layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")
            os.remove(layout_file)
            self.logger.info(f"Layout '{layout_name}' deleted.")
            self.toast_overlay.add_toast(Adw.Toast(title=_("Layout Deleted")))
            self._load_layouts()
            self.refresh_tree()
        except Exception as e:
            self.logger.error(f"Failed to delete layout '{layout_name}': {e}")
            self._show_error_dialog(_("Error Deleting Layout"), str(e))

    def _load_layouts(self):
        """Loads all saved layouts from the layout directory."""
        self.layouts.clear()
        if not os.path.exists(LAYOUT_DIR):
            return
        for layout_file in sorted(os.listdir(LAYOUT_DIR)):
            if layout_file.endswith(".json"):
                layout_name = os.path.splitext(layout_file)[0]
                folder_path = ""
                try:
                    with open(os.path.join(LAYOUT_DIR, layout_file), "r") as f:
                        data = json.load(f)
                        folder_path = data.get("folder_path", "")
                except Exception as e:
                    self.logger.warning(
                        f"Could not read folder_path from {layout_file}: {e}"
                    )
                self.layouts.append(
                    LayoutItem(name=layout_name, folder_path=folder_path)
                )

    def move_layout(self, layout_name: str, old_folder: str, new_folder: str):
        """Moves a layout to a new virtual folder by updating its JSON file."""
        if old_folder == new_folder:
            return

        sanitized_name = sanitize_session_name(layout_name).replace(" ", "_")
        layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

        try:
            state = {}
            if os.path.exists(layout_file):
                with open(layout_file, "r") as f:
                    state = json.load(f)

            state["folder_path"] = new_folder

            with open(layout_file, "w") as f:
                json.dump(state, f, indent=2)

            self.logger.info(f"Moved layout '{layout_name}' to folder '{new_folder}'")
            self._load_layouts()
            self.refresh_tree()
        except Exception as e:
            self.logger.error(f"Failed to move layout '{layout_name}': {e}")
            self._show_error_dialog(_("Error Moving Layout"), str(e))

    def _calculate_sidebar_natural_width(self) -> int:
        """Calculate the natural width required for the sidebar content."""
        if not hasattr(self, "sidebar_box") or not self.sidebar_box:
            return 300  # fallback

        try:
            # Get the session tree widget
            tree_widget = self.session_tree.get_widget()
            if not tree_widget:
                return 300

            # Measure the width requirements of different components
            min_width = 220  # Minimum usable width
            max_width = 500  # Maximum reasonable width
            padding = 32  # Account for margins and padding

            # Get natural width from the tree widget
            tree_width = 250  # Default fallback
            if tree_widget.get_visible():
                tree_req = tree_widget.get_preferred_size()
                if tree_req and tree_req.natural_size.width > 0:
                    tree_width = tree_req.natural_size.width

            # Measure actual text content in the tree
            content_width = self._measure_tree_content_width()
            if content_width > 0:
                tree_width = max(tree_width, content_width)

            # Account for toolbar buttons (approximately 5 buttons * 32px + spacing)
            toolbar_width = 200

            # Account for search entry
            search_width = 200

            # Calculate the maximum natural width needed
            content_width = max(tree_width, toolbar_width, search_width) + padding

            # Constrain to reasonable bounds
            final_width = max(min_width, min(content_width, max_width))

            self.logger.debug(
                f"Calculated sidebar width: {final_width}px (tree: {tree_width}, content: {content_width})"
            )
            return final_width

        except Exception as e:
            self.logger.warning(f"Failed to calculate sidebar natural width: {e}")
            return 300  # fallback to default

    def _measure_tree_content_width(self) -> int:
        """Measure the width of the widest visible text content in the session tree."""
        try:
            # Create a temporary label to measure text width
            temp_label = Gtk.Label()
            temp_label.set_use_markup(False)
            temp_label.add_css_class("sidebar-session-tree")  # Use same styling as tree

            max_width = 0
            expanded_paths = set(self.settings_manager.get("tree_expanded_folders", []))

            # Get all currently visible items and measure them with proper indentation
            visible_items = self._get_visible_tree_items(expanded_paths)

            for item, depth in visible_items:
                if hasattr(item, "name") and item.name:
                    temp_label.set_text(item.name)
                    req = temp_label.get_preferred_size()
                    if req and req.natural_size.width > 0:
                        text_width = req.natural_size.width

                        # Calculate total width including visual elements
                        icon_width = 20  # Icon size
                        indentation = depth * 20  # 20px per indentation level
                        margins = 24  # Left/right margins and spacing

                        total_width = text_width + icon_width + indentation + margins

                        if total_width > max_width:
                            max_width = total_width

                        self.logger.debug(
                            f"Item '{item.name}' (depth {depth}): text={text_width}px, "
                            f"total={total_width}px"
                        )

            temp_label.destroy()

            # Ensure minimum width for empty content
            return max(int(max_width), 180)

        except Exception as e:
            self.logger.debug(f"Failed to measure tree content width: {e}")
            return 180  # Fallback minimum width

    def _get_visible_tree_items(self, expanded_paths: set) -> List[Tuple[Any, int]]:
        """Get all currently visible items in the tree with their indentation depth."""
        visible_items = []

        # Add root-level items (depth 0)
        # Sessions at root
        for i in range(self.session_store.get_n_items()):
            item = self.session_store.get_item(i)
            if item and (not hasattr(item, "folder_path") or not item.folder_path):
                visible_items.append((item, 0))

        # Folders at root
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if folder and (
                not hasattr(folder, "parent_path") or not folder.parent_path
            ):
                visible_items.append((folder, 0))
                # If folder is expanded, add its children recursively
                if folder.path in expanded_paths:
                    visible_items.extend(
                        self._get_folder_children(folder.path, expanded_paths, 1)
                    )

        # Layouts at root
        if hasattr(self, "layouts"):
            for layout in self.layouts:
                if not hasattr(layout, "folder_path") or not layout.folder_path:
                    visible_items.append((layout, 0))

        return visible_items

    def _get_folder_children(
        self, parent_path: str, expanded_paths: set, depth: int
    ) -> List[Tuple[Any, int]]:
        """Recursively get children of a folder at given depth."""
        children = []

        # Add sessions in this folder
        for i in range(self.session_store.get_n_items()):
            item = self.session_store.get_item(i)
            if (
                item
                and hasattr(item, "folder_path")
                and item.folder_path == parent_path
            ):
                children.append((item, depth))

        # Add subfolders in this folder
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if (
                folder
                and hasattr(folder, "parent_path")
                and folder.parent_path == parent_path
            ):
                children.append((folder, depth))
                # If subfolder is expanded, add its children recursively
                if folder.path in expanded_paths:
                    children.extend(
                        self._get_folder_children(
                            folder.path, expanded_paths, depth + 1
                        )
                    )

        # Add layouts in this folder
        if hasattr(self, "layouts"):
            for layout in self.layouts:
                if hasattr(layout, "folder_path") and layout.folder_path == parent_path:
                    children.append((layout, depth))

        return children

    def _update_flap_size(self):
        """Update the flap sidebar width based on content."""
        if not hasattr(self, "sidebar_box") or not self.sidebar_box:
            return

        try:
            natural_width = self._calculate_sidebar_natural_width()
            self.sidebar_box.set_size_request(natural_width, -1)
            self.logger.debug(f"Updated flap sidebar width to {natural_width}px")
        except Exception as e:
            self.logger.warning(f"Failed to update flap size: {e}")

    def _update_sidebar_sizes(self):
        """Update sidebar sizes for both flap and popover modes."""
        try:
            auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
            if auto_hide:
                # In auto-hide mode, update popover size
                if (
                    hasattr(self, "sidebar_popover")
                    and self.sidebar_popover.get_visible()
                ):
                    self._update_popover_size()
            else:
                # In normal mode, update flap size
                if hasattr(self, "flap") and self.flap.get_reveal_flap():
                    self._update_flap_size()
        except Exception as e:
            self.logger.warning(f"Failed to update sidebar sizes: {e}")

    def _update_sidebar_responsive_design(self):
        """Update sidebar styling based on current width for responsive design."""
        if not hasattr(self, "sidebar_box") or not self.sidebar_box:
            return

        # Get current sidebar width
        sidebar_width = self.sidebar_box.get_width()
        if sidebar_width == 0:
            # If width is 0, try to get calculated natural width
            sidebar_width = self._calculate_sidebar_natural_width()

        # Apply compact styling for narrow sidebars (more aggressive threshold)
        if sidebar_width < 260:
            self.sidebar_box.add_css_class("sidebar-compact")
        else:
            self.sidebar_box.remove_css_class("sidebar-compact")

    def _on_sidebar_folded_changed(self, flap, param):
        """Handle sidebar folding changes for responsive design."""
        self._update_sidebar_responsive_design()
