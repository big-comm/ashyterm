# ashyterm/ui/window_ui.py

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.tooltip_helper import init_tooltip_helper
from ..utils.translation_utils import _

# Lazy import for menus - only loaded when main menu is first shown
# from .menus import MainApplicationMenu

if TYPE_CHECKING:
    from ..window import CommTerminalWindow

# Path to CSS styles directory
_STYLES_DIR = Path(__file__).parent.parent / "data" / "styles"


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

        # Get the application for shortcut lookups
        app = window.get_application()

        # Initialize tooltip helper for custom tooltips (global singleton)
        self.tooltip_helper = init_tooltip_helper(self.settings_manager, app)

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
        self.ai_assistant_button = None
        self.ai_chat_panel = None
        self.ai_paned = None

    def build_ui(self):
        """Constructs the entire UI and sets it on the parent window."""
        self._setup_styles()
        main_box = self._setup_main_structure()
        self.window.set_content(main_box)
        self.logger.info("Main window UI constructed successfully.")

    def _setup_styles(self) -> None:
        """Applies application-wide CSS for various custom widgets."""
        provider = Gtk.CssProvider()
        css_file = _STYLES_DIR / "window.css"

        if css_file.exists():
            provider.load_from_path(str(css_file))
            self.logger.debug(f"Loaded CSS styles from {css_file}")
        else:
            self.logger.warning(f"CSS file not found: {css_file}")

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
        self.search_prev_button = icon_button("go-up-symbolic")
        self.search_next_button = icon_button("go-down-symbolic")

        # Create the BroadcastBar
        self.broadcast_bar = Gtk.SearchBar()
        broadcast_box = Gtk.Box(spacing=6)
        self.broadcast_entry = Gtk.Entry(
            hexpand=True,
            placeholder_text=_("Type your command here and press ENTER..."),
        )
        self.broadcast_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "utilities-terminal-symbolic")
        broadcast_box.append(self.broadcast_entry)
        self.broadcast_bar.set_child(broadcast_box)
        main_box.append(self.broadcast_bar)

        # Search occurrence counter
        self.search_occurrence_label = Gtk.Label()
        self.search_occurrence_label.add_css_class("dim-label")
        self.tooltip_helper.add_tooltip(
            self.search_occurrence_label, _("Current occurrence")
        )

        # Case sensitive switch
        self.case_sensitive_switch = Gtk.Switch()
        self.tooltip_helper.add_tooltip(
            self.case_sensitive_switch, _("Case sensitive search")
        )
        case_sensitive_box = Gtk.Box(spacing=6)
        case_sensitive_label = Gtk.Label(label=_("Case sensitive"))
        case_sensitive_box.append(case_sensitive_label)
        case_sensitive_box.append(self.case_sensitive_switch)

        # Regex switch
        self.regex_switch = Gtk.Switch()
        self.tooltip_helper.add_tooltip(self.regex_switch, _("Use regular expressions"))
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

        # Create buttons (tooltips are added via tooltip_helper below)
        self.toggle_sidebar_button = Gtk.ToggleButton()
        self.toggle_sidebar_button.set_child(icon_image("pin-symbolic"))
        self.toggle_sidebar_button.add_css_class("sidebar-toggle-button")

        self.file_manager_button = Gtk.ToggleButton()
        self.file_manager_button.set_child(icon_image("folder-open-symbolic"))

        self.command_guide_button = Gtk.Button()
        self.command_guide_button.set_child(icon_image("help-about-symbolic"))
        self.command_guide_button.set_action_name("win.show-command-guide")

        # Add the new search button
        self.search_button = Gtk.ToggleButton()
        self.search_button.set_child(icon_image("edit-find-symbolic"))

        # Add the new Broadcast button
        self.broadcast_button = Gtk.ToggleButton()
        self.broadcast_button.set_child(icon_image("utilities-terminal-symbolic"))

        self.ai_assistant_button = Gtk.Button()
        self.ai_assistant_button.set_child(
            icon_image("avatar-default-symbolic", use_bundled=False)
        )  # System icon
        self.ai_assistant_button.add_css_class("flat")
        self.ai_assistant_button.connect(
            "clicked", lambda _btn: self.window._on_ai_assistant_requested()
        )
        # Set initial visibility based on settings
        ai_enabled = self.settings_manager.get("ai_assistant_enabled", False)
        self.ai_assistant_button.set_visible(ai_enabled)

        self.cleanup_button = Gtk.MenuButton(visible=False)
        self.cleanup_button.set_child(icon_image("user-trash-symbolic"))
        self.cleanup_button.add_css_class("destructive-action")
        self.cleanup_button.add_css_class("flat")
        self.cleanup_popover = Gtk.Popover()
        self.cleanup_button.set_popover(self.cleanup_popover)

        # Hide tooltip when cleanup popover is shown
        self.cleanup_popover.connect("show", lambda p: self.tooltip_helper.hide())

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_child(icon_image("open-menu-symbolic"))
        self.menu_button.add_css_class("flat")
        # Lazy initialization: popover is created on first activation
        self._main_menu_popover = None
        self._setup_lazy_menu_popover()

        self.new_tab_button = icon_button("tab-new-symbolic")
        self.new_tab_button.connect("clicked", self.window._on_new_tab_clicked)
        self.new_tab_button.add_css_class("flat")

        # Add custom tooltips to header bar buttons (with dynamic shortcuts where applicable)
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.toggle_sidebar_button, _("Sessions Panel"), "toggle-sidebar"
        )
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.file_manager_button, _("File Manager"), "toggle-file-manager"
        )
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.command_guide_button, _("Command Guide"), "show-command-guide"
        )
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.search_button, _("Search in Terminal"), "toggle-search"
        )
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.broadcast_button, _("Send Command to All Tabs"), "toggle-broadcast"
        )
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.ai_assistant_button, _("Ask AI Assistant"), "ai-assistant"
        )
        self.tooltip_helper.add_tooltip(
            self.cleanup_button, _("Manage Temporary Files")
        )
        self.tooltip_helper.add_tooltip(self.menu_button, _("Main Menu"))
        self.tooltip_helper.add_tooltip_with_shortcut(
            self.new_tab_button, _("New Tab"), "new-local-tab"
        )

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

        # Header with title and toolbar
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Toolbar with action buttons
        toolbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            halign=Gtk.Align.CENTER,
            margin_bottom=4,
        )

        self.add_session_button = icon_button("list-add-symbolic")
        self.tooltip_helper.add_tooltip(self.add_session_button, _("Add Session"))
        toolbar.append(self.add_session_button)

        self.add_folder_button = icon_button("folder-new-symbolic")
        self.tooltip_helper.add_tooltip(self.add_folder_button, _("Add Folder"))
        toolbar.append(self.add_folder_button)

        self.edit_button = icon_button("document-edit-symbolic")
        self.tooltip_helper.add_tooltip(self.edit_button, _("Edit Selected"))
        toolbar.append(self.edit_button)

        self.save_layout_button = icon_button("document-save-symbolic")
        self.tooltip_helper.add_tooltip(
            self.save_layout_button, _("Save Current Layout")
        )
        toolbar.append(self.save_layout_button)

        self.remove_button = icon_button("user-trash-symbolic")
        self.tooltip_helper.add_tooltip(self.remove_button, _("Remove Selected"))
        self.remove_button.add_css_class("destructive")
        toolbar.append(self.remove_button)

        header_box.append(toolbar)
        toolbar_view.add_top_bar(header_box)

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
        """Create the main content area with tabs, file manager, and AI panel."""
        view_stack = self.tab_manager.get_view_stack()
        view_stack.add_css_class("terminal-tab-view")

        self.toast_overlay = Adw.ToastOverlay(child=view_stack)
        self.toast_overlay.set_vexpand(True)

        # Use Paned for AI panel resize capability
        self.ai_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.ai_paned.set_start_child(self.toast_overlay)
        self.ai_paned.set_resize_start_child(True)
        self.ai_paned.set_shrink_start_child(False)

        # AI panel will be added dynamically when needed
        self.ai_paned.set_end_child(None)
        self.ai_paned.set_resize_end_child(True)
        self.ai_paned.set_shrink_end_child(False)

        # Panel will be created on first use
        self.ai_chat_panel = None
        self._ai_panel_visible = False

        return self.ai_paned

    def _setup_lazy_menu_popover(self) -> None:
        """Set up lazy loading for the main menu popover using a lightweight placeholder."""
        # Create an empty placeholder popover
        placeholder = Gtk.Popover()
        placeholder.connect("show", self._on_menu_popover_show)
        self.menu_button.set_popover(placeholder)

    def _on_menu_popover_show(self, popover: Gtk.Popover) -> None:
        """Replace placeholder popover with real menu on first show."""
        if self._main_menu_popover is not None:
            return  # Already initialized

        from .menus import MainApplicationMenu

        real_popover, self.font_sizer_widget = MainApplicationMenu.create_main_popover(
            self.window
        )
        real_popover.connect("show", lambda p: self.tooltip_helper.hide())
        self._main_menu_popover = real_popover
        self.menu_button.set_popover(real_popover)

        # Show the real popover now
        real_popover.popup()

    def _ensure_main_menu_popover(self, button: Gtk.MenuButton) -> None:
        """Lazily create and attach the main menu popover on first click."""
        if self._main_menu_popover is None:
            from .menus import MainApplicationMenu

            popover, self.font_sizer_widget = MainApplicationMenu.create_main_popover(
                self.window
            )
            popover.connect("show", lambda p: self.tooltip_helper.hide())
            self._main_menu_popover = popover
            self.menu_button.set_popover(popover)
        # Popover will be shown automatically by MenuButton

    def _create_ai_chat_panel(self) -> None:
        """Create the AI chat panel widget (lazy initialization)."""
        if self.ai_chat_panel is not None:
            return

        from .widgets.ai_chat_panel import AIChatPanel

        self.ai_chat_panel = AIChatPanel(
            self.window.ai_assistant,
            self.tooltip_helper,
            self.settings_manager,
        )
        self.ai_chat_panel.connect("close-requested", self._on_ai_panel_close)
        self.ai_chat_panel.connect("execute-command", self._on_ai_execute_command)
        self.ai_chat_panel.connect("run-command", self._on_ai_run_command)

    def _on_ai_panel_close(self, _panel) -> None:
        """Handle AI panel close request."""
        self.hide_ai_panel()

    def _on_ai_execute_command(self, _panel, command: str) -> None:
        """Handle command execution request from AI panel - insert into terminal."""
        # Get current terminal and insert command (without newline - user must press Enter)
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            # Feed the command to the terminal without executing (no newline)
            terminal.feed_child(command.encode("utf-8"))

    def _on_ai_run_command(self, _panel, command: str) -> None:
        """Handle run command request from AI panel - insert and execute in terminal."""
        # Get current terminal, insert command and send Ctrl+J (newline) to execute
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            # Feed the command followed by newline (Ctrl+J = 0x0a = \n)
            terminal.feed_child(command.encode("utf-8"))
            terminal.feed_child(b"\n")  # Ctrl+J or Enter to execute

    def show_ai_panel(self, initial_text: Optional[str] = None) -> None:
        """Show the AI chat panel."""
        # Lazy create the panel
        self._create_ai_chat_panel()

        if initial_text:
            self.ai_chat_panel.set_initial_text(initial_text)

        if not self._ai_panel_visible:
            # Add AI panel directly to paned
            self.ai_chat_panel.set_vexpand(True)
            self.ai_chat_panel.set_size_request(-1, 200)  # Minimum height
            self.ai_paned.set_end_child(self.ai_chat_panel)

            # Set position for AI panel (saved height from settings)
            window_height = self.window.get_height()
            saved_height = self.settings_manager.get("ai_panel_height", 350)
            min_height = 200
            saved_height = max(min_height, saved_height)
            target_pos = window_height - saved_height
            self.ai_paned.set_position(target_pos)

            self._ai_panel_visible = True

    def hide_ai_panel(self) -> None:
        """Hide the AI chat panel."""
        if self._ai_panel_visible and self.ai_chat_panel:
            # Save current height to settings
            window_height = self.window.get_height()
            ai_height = window_height - self.ai_paned.get_position()
            min_height = 200
            ai_height = max(min_height, ai_height)
            self.settings_manager.set(
                "ai_panel_height", ai_height, save_immediately=True
            )

            # Remove panel
            self.ai_paned.set_end_child(None)
            self._ai_panel_visible = False

    def toggle_ai_panel(self) -> None:
        """Toggle the AI chat panel visibility."""
        if self._ai_panel_visible:
            self.hide_ai_panel()
        else:
            self.show_ai_panel()

    def is_ai_panel_visible(self) -> bool:
        """Check if the AI panel is currently visible."""
        return self._ai_panel_visible

    def update_ai_button_visibility(self) -> None:
        """Update AI button visibility based on settings."""
        if self.ai_assistant_button:
            enabled = self.settings_manager.get("ai_assistant_enabled", False)
            self.ai_assistant_button.set_visible(enabled)

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
