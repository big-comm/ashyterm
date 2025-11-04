# ashyterm/ui/window_ui.py

from typing import TYPE_CHECKING

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .menus import MainApplicationMenu

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class WindowUIBuilder:
    """
    Builds and assembles the GTK/Adw widgets for the main application window.

    This class is responsible for the construction of the UI, separating the
    'what' (the widgets) from the 'how' (the event logic in CommTerminalWindow).
    """

    def __init__(self, window: "CommTerminalWindow"):
        self.window = window
        self.logger = get_logger("ashyterm.ui.builder")
        self.settings_manager = window.settings_manager
        self.tab_manager = window.tab_manager
        self.session_tree = window.session_tree

        # WM settings for dynamic button layout
        self.wm_settings = Gio.Settings.new("org.gnome.desktop.wm.preferences")
        self.wm_settings.connect(
            "changed::button-layout", self._on_button_layout_changed
        )

        # Connect to window maximized state changes
        self.window.connect("notify::maximized", self._on_maximized_changed)

        # --- Widgets to be created and exposed ---
        self.header_bar = None
        self.flap = None
        self.sidebar_box = None
        self.sidebar_popover = None
        self.toggle_sidebar_button = None
        self.file_manager_button = None
        self.command_guide_button = None
        self.cleanup_button = None
        self.font_sizer_widget = None
        self.scrolled_tab_bar = None
        self.single_tab_title_widget = None
        self.title_stack = None
        self.toast_overlay = None
        self.search_bar = None
        self.search_button = None
        self.broadcast_bar = None
        self.broadcast_button = None
        self.broadcast_entry = None
        self.terminal_search_entry = None  # Renamed for clarity
        self.sidebar_search_entry = None  # Renamed for clarity
        self.search_prev_button = None
        self.search_next_button = None
        self.case_sensitive_switch = None
        self.regex_switch = None
        self.search_occurrence_label = None
        self.add_session_button = None
        self.add_folder_button = None
        self.edit_button = None
        self.save_layout_button = None
        self.remove_button = None
        self.menu_button = None
        self.new_tab_button = None

    def build_ui(self):
        """Constructs the entire UI and sets it on the parent window."""
        self._setup_styles()
        main_box = self._setup_main_structure()
        self.window.set_content(main_box)
        self.logger.info("Main window UI constructed successfully.")

    def _set_button_icon(
        self, button: Gtk.Widget, icon_name: str, pixel_size: int = 24
    ) -> None:
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(pixel_size)
        button.set_child(image)

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
        .themeselector checkbutton.terminal { background: linear-gradient(45deg, #000 50%, #fff 50%); }
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
        paned separator { background: var(--view-bg-color); }
        popover.menu separator { background-color: color-mix(in srgb, var(--headerbar-border-color) var(--border-opacity), transparent); }
        #scrolled_tab_bar scrollbar trough { margin: 0px; }
        #scrolled_tab_bar button { min-width:24px; margin-right: 8px; margin: 0px; padding-top: 0px; padding-bottom: 0px; }
        .main-header-bar box { margin: 0px; padding-top: 0px; padding-bottom: 0px; }
        .main-header-bar button,
        .main-header-bar menubutton,
        .main-header-bar togglebutton {
            min-width: 5px;
            min-height: 1px;
            padding: 3px;
            border: none;
            box-shadow: none;
            background-color: transparent;
        }
        .main-header-bar button:hover,
        .main-header-bar menubutton:hover,
        .main-header-bar togglebutton:hover,
        .main-header-bar button:active,
        .main-header-bar menubutton:active,
        .main-header-bar togglebutton:active,
        .main-header-bar togglebutton:checked,
        .main-header-bar togglebutton:checked:hover,
        .main-header-bar togglebutton:checked:active {
            background-color: transparent;
            box-shadow: none;
        }
        .main-header-bar button image,
        .main-header-bar menubutton image,
        .main-header-bar togglebutton image {
            min-width: 24px;
            min-height: 24px;
        }
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
        /* Sidebar toggle button styling - hover only, no active background */
        .sidebar-toggle-button:active,
        .sidebar-toggle-button:checked {
            background: transparent;
        }
        .flipped-icon { transform: scaleX(-1); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Separate provider for window borders (conditional on maximized state)
        self.border_provider = Gtk.CssProvider()
        self._update_border_css()
        # MODIFIED: Apply provider directly to the window, not globally
        self.window.get_style_context().add_provider(
            self.border_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _setup_main_structure(self) -> Gtk.Box:
        """Sets up the main window structure (header, search bar, flap, content)."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.header_bar = self._create_header_bar()
        main_box.append(self.header_bar)

        # Create the SearchBar
        self.search_bar = Gtk.SearchBar()
        search_box = Gtk.Box(spacing=6)
        self.terminal_search_entry = Gtk.SearchEntry(hexpand=True)
        self.search_bar.connect_entry(self.terminal_search_entry)
        self.search_prev_button = Gtk.Button.new_from_icon_name('big-go-up-symbolic')
        self.search_next_button = Gtk.Button.new_from_icon_name('big-go-down-symbolic')

        # Create the BroadcastBar
        self.broadcast_bar = Gtk.SearchBar()
        broadcast_box = Gtk.Box(spacing=6)
        self.broadcast_entry = Gtk.Entry(
            hexpand=True, placeholder_text=_("Type your command here and press ENTER...")
        )
        icon = Gtk.Image.new_from_icon_name('big-utilities-terminal-symbolic')
        self.broadcast_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "utilities-terminal-symbolic")
        broadcast_box.append(self.broadcast_entry)
        self.broadcast_bar.set_child(broadcast_box)
        main_box.append(self.broadcast_bar)

        # Search occurrence counter
        self.search_occurrence_label = Gtk.Label()
        self.search_occurrence_label.add_css_class("dim-label")
        self.search_occurrence_label.set_tooltip_text(_("Current occurrence"))

        # Case sensitive switch
        self.case_sensitive_switch = Gtk.Switch()
        self.case_sensitive_switch.set_tooltip_text(_("Case sensitive search"))
        case_sensitive_box = Gtk.Box(spacing=6)
        case_sensitive_label = Gtk.Label(label=_("Case sensitive"))
        case_sensitive_box.append(case_sensitive_label)
        case_sensitive_box.append(self.case_sensitive_switch)

        # Regex switch
        self.regex_switch = Gtk.Switch()
        self.regex_switch.set_tooltip_text(_("Use regular expressions"))
        regex_box = Gtk.Box(spacing=6)
        regex_label = Gtk.Label(label=_("Regex"))
        regex_box.append(regex_label)
        regex_box.append(self.regex_switch)

        search_box.append(self.terminal_search_entry)
        search_box.append(self.search_occurrence_label)
        search_box.append(case_sensitive_box)
        search_box.append(regex_box)
        search_box.append(self.search_prev_button)
        search_box.append(self.search_next_button)
        self.search_bar.set_child(search_box)
        main_box.append(self.search_bar)

        # Create the main content area (Flap)
        self.flap = Adw.Flap(transition_type=Adw.FlapTransitionType.SLIDE)
        self.sidebar_box = self._create_sidebar()
        self.flap.set_flap(self.sidebar_box)

        self.sidebar_popover = Gtk.Popover(
            position=Gtk.PositionType.BOTTOM, has_arrow=True
        )
        self.sidebar_popover.set_size_request(200, 800)

        content_area = self._create_content_area()
        self.flap.set_content(content_area)

        # Add the Flap as the main content of the window, which will be revealed/hidden by the SearchBar
        main_box.append(self.flap)
        return main_box

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create the header bar with controls."""
        header_bar = Adw.HeaderBar(css_classes=["main-header-bar"])

        # Assign to window early so menus can access it
        self.window.header_bar = header_bar

        # Create buttons
        self.toggle_sidebar_button = Gtk.ToggleButton()
        self.toggle_sidebar_button.set_tooltip_text(_("Toggle Sidebar"))
        self.toggle_sidebar_button.add_css_class("sidebar-toggle-button")
        self.toggle_sidebar_button.add_css_class("flat")
        self._set_button_icon(self.toggle_sidebar_button, 'big-pin-symbolic')

        self.file_manager_button = Gtk.ToggleButton()
        self.file_manager_button.set_tooltip_text(_("File Manager"))
        self.file_manager_button.add_css_class("flat")
        self._set_button_icon(self.file_manager_button, 'big-folder-open-symbolic')

        self.command_guide_button = Gtk.Button()
        self.command_guide_button.set_tooltip_text(_("Command Guide (Ctrl+Shift+P)"))
        self.command_guide_button.set_action_name("win.show-command-guide")
        self.command_guide_button.add_css_class("flat")
        self._set_button_icon(self.command_guide_button, 'big-help-about-symbolic')

        # Add the new search button
        self.search_button = Gtk.ToggleButton()
        self.search_button.set_tooltip_text(_("Search in Terminal"))
        self.search_button.add_css_class("flat")
        self._set_button_icon(self.search_button, 'big-edit-find-symbolic')

        # Add the new Broadcast button
        self.broadcast_button = Gtk.ToggleButton()
        self.broadcast_button.set_tooltip_text(_("Send Command to All Tabs"))
        self.broadcast_button.add_css_class("flat")
        self._set_button_icon(
            self.broadcast_button, 'big-utilities-terminal-symbolic'
        )

        self.ai_assistant_button = Gtk.Button()
        self.ai_assistant_button.set_tooltip_text(_("Ask AI Assistant (Ctrl+Shift+I)"))
        self.ai_assistant_button.add_css_class("flat")
        self._set_button_icon(self.ai_assistant_button, 'big-avatar-default-symbolic')
        self.ai_assistant_button.connect(
            "clicked", lambda _btn: self.window._on_ai_assistant_requested()
        )
        self.cleanup_button = Gtk.MenuButton()
        self.cleanup_button.set_tooltip_text(_("Manage Temporary Files"))
        self.cleanup_button.set_visible(False)
        self.cleanup_button.add_css_class("destructive-action")
        self.cleanup_button.add_css_class("flat")
        self._set_button_icon(self.cleanup_button, 'big-user-trash-symbolic')
        self.cleanup_popover = Gtk.Popover()
        self.cleanup_button.set_popover(self.cleanup_popover)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_tooltip_text(_("Main Menu"))
        self.menu_button.add_css_class("flat")
        self._set_button_icon(self.menu_button, 'big-open-menu-symbolic')
        popover, self.font_sizer_widget = MainApplicationMenu.create_main_popover(
            self.window
        )
        self.menu_button.set_popover(popover)

        self.new_tab_button = Gtk.Button()
        self.new_tab_button.set_tooltip_text(_("New Tab"))
        self.new_tab_button.connect("clicked", self.window._on_new_tab_clicked)
        self.new_tab_button.add_css_class("flat")
        self._set_button_icon(self.new_tab_button, 'big-tab-new-symbolic')

        # Check if window controls are on the left
        button_layout = self.wm_settings.get_string("button-layout")
        if ":" in button_layout:
            left_part = button_layout.split(":")[0]
            window_controls_on_left = any(
                btn in left_part for btn in ["close", "minimize", "maximize"]
            )
        else:
            window_controls_on_left = False

        if window_controls_on_left:
            # Add flipped class to icons
            self.toggle_sidebar_button.add_css_class("flipped-icon")
            self.file_manager_button.add_css_class("flipped-icon")
            self.command_guide_button.add_css_class("flipped-icon")
            self.search_button.add_css_class("flipped-icon")
            self.broadcast_button.add_css_class("flipped-icon")
            self.ai_assistant_button.add_css_class("flipped-icon")
            self.cleanup_button.add_css_class("flipped-icon")
            self.menu_button.add_css_class("flipped-icon")
            self.new_tab_button.add_css_class("flipped-icon")
            # Swap sides: left buttons to right, right buttons to left
            header_bar.pack_end(self.toggle_sidebar_button)
            header_bar.pack_end(self.file_manager_button)
            header_bar.pack_end(self.command_guide_button)
            header_bar.pack_end(self.broadcast_button)
            header_bar.pack_end(self.ai_assistant_button)
            header_bar.pack_end(self.search_button)
            header_bar.pack_end(self.cleanup_button)
            header_bar.pack_start(self.menu_button)
            header_bar.pack_start(self.new_tab_button)
        else:
            # Normal packing
            header_bar.pack_start(self.toggle_sidebar_button)
            header_bar.pack_start(self.file_manager_button)
            header_bar.pack_start(self.command_guide_button)
            header_bar.pack_start(self.broadcast_button)
            header_bar.pack_start(self.ai_assistant_button)
            header_bar.pack_start(self.search_button)
            header_bar.pack_start(self.cleanup_button)
            header_bar.pack_end(self.menu_button)
            header_bar.pack_end(self.new_tab_button)

        self.scrolled_tab_bar = Gtk.ScrolledWindow(
            name="scrolled_tab_bar",
            propagate_natural_height=True,
            hexpand=True,
        )
        self.scrolled_tab_bar.add_css_class("scrolled-tab-bar")
        self.scrolled_tab_bar.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_tab_bar.set_child(self.tab_manager.get_tab_bar())

        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll_controller.connect("scroll", self.window._on_tab_bar_scroll)
        self.scrolled_tab_bar.add_controller(scroll_controller)

        self.single_tab_title_widget = Adw.WindowTitle(title=_("Ashy Terminal"))

        self.title_stack = Gtk.Stack()
        self.title_stack.add_named(self.scrolled_tab_bar, "tabs-view")
        self.title_stack.add_named(self.single_tab_title_widget, "title-view")
        header_bar.set_title_widget(self.title_stack)

        return header_bar

    def _on_button_layout_changed(self, settings, key):
        """Handle dynamic changes to window button layout."""
        if self.header_bar is None:
            return

        # Remove all buttons from header_bar
        buttons = [
            self.toggle_sidebar_button,
            self.file_manager_button,
            self.command_guide_button,
            self.search_button,
            self.broadcast_button,
            self.cleanup_button,
            self.menu_button,
            self.new_tab_button,
        ]
        for btn in buttons:
            if btn and btn.get_parent() is not None:
                btn.get_parent().remove(btn)
            # Remove flipped class
            btn.remove_css_class("flipped-icon")

        # Re-determine layout
        button_layout = settings.get_string("button-layout")
        if ":" in button_layout:
            left_part = button_layout.split(":")[0]
            window_controls_on_left = any(
                btn in left_part for btn in ["close", "minimize", "maximize"]
            )
        else:
            window_controls_on_left = False

        # Re-pack buttons
        if window_controls_on_left:
            # Add flipped class
            for btn in buttons:
                btn.add_css_class("flipped-icon")
            # Swap sides
            self.header_bar.pack_end(self.toggle_sidebar_button)
            self.header_bar.pack_end(self.file_manager_button)
            self.header_bar.pack_end(self.command_guide_button)
            self.header_bar.pack_end(self.broadcast_button)
            self.header_bar.pack_end(self.search_button)
            self.header_bar.pack_end(self.cleanup_button)
            self.header_bar.pack_start(self.menu_button)
            self.header_bar.pack_start(self.new_tab_button)
        else:
            # Normal packing
            self.header_bar.pack_start(self.toggle_sidebar_button)
            self.header_bar.pack_start(self.file_manager_button)
            self.header_bar.pack_start(self.command_guide_button)
            self.header_bar.pack_start(self.broadcast_button)
            self.header_bar.pack_start(self.search_button)
            self.header_bar.pack_start(self.cleanup_button)
            self.header_bar.pack_end(self.menu_button)
            self.header_bar.pack_end(self.new_tab_button)

    def _create_sidebar(self) -> Gtk.Widget:
        """Create the sidebar with session tree."""
        toolbar_view = Adw.ToolbarView(
            css_classes=["background", "sidebar-toolbar-view"]
        )
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.set_halign(Gtk.Align.CENTER)

        self.add_session_button = Gtk.Button.new_from_icon_name('big-list-add-symbolic')
        self.add_session_button.set_tooltip_text(_("Add Session"))
        toolbar.append(self.add_session_button)

        self.add_folder_button = Gtk.Button.new_from_icon_name('big-folder-new-symbolic')
        self.add_folder_button.set_tooltip_text(_("Add Folder"))
        toolbar.append(self.add_folder_button)

        self.edit_button = Gtk.Button.new_from_icon_name('big-document-edit-symbolic')
        self.edit_button.set_tooltip_text(_("Edit Selected"))
        toolbar.append(self.edit_button)

        self.save_layout_button = Gtk.Button.new_from_icon_name(
            "document-save-symbolic"
        )
        self.save_layout_button.set_tooltip_text(_("Save Current Layout"))
        toolbar.append(self.save_layout_button)

        self.remove_button = Gtk.Button.new_from_icon_name('big-user-trash-symbolic')
        self.remove_button.set_tooltip_text(_("Remove Selected"))
        self.remove_button.add_css_class("destructive")
        toolbar.append(self.remove_button)

        toolbar_view.add_top_bar(toolbar)

        scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_child(self.session_tree.get_widget())
        scrolled_window.add_css_class("sidebar-session-tree")
        toolbar_view.set_content(scrolled_window)

        search_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, css_classes=["sidebar-search"]
        )
        self.sidebar_search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search sessions...")
        )
        self.sidebar_search_entry.set_margin_start(6)
        self.sidebar_search_entry.set_margin_end(6)
        search_container.append(self.sidebar_search_entry)
        toolbar_view.add_bottom_bar(search_container)

        return toolbar_view

    def _create_content_area(self) -> Gtk.Widget:
        """Create the main content area with tabs and file manager placeholder."""
        view_stack = self.tab_manager.get_view_stack()
        view_stack.add_css_class("terminal-tab-view")

        self.toast_overlay = Adw.ToastOverlay(child=view_stack)
        return self.toast_overlay

    def _update_border_css(self):
        """Update the window border CSS based on maximized state."""
        if self.window.is_maximized():
            css = ""
        else:
            css = """
            window {
                border-top: 1px solid rgba(90, 90, 90, 0.5);
                border-left: 1px solid rgba(60, 60, 60, 0.5);
                border-right: 1px solid rgba(60, 60, 60, 0.5);
                border-bottom: 1px solid rgba(60, 60, 60, 0.5);
            }
            """
        self.border_provider.load_from_data(css.encode("utf-8"))

    def _on_maximized_changed(self, window, param):
        """Handle window maximized state changes."""
        self._update_border_css()
