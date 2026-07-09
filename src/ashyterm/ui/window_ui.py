# ashyterm/ui/window_ui.py

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..utils.icons import icon_button
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _
from ..utils.accessibility import set_label as a11y_label
from . import command_toolbar as _cmd_toolbar
from .header_bar_builder import build_header_bar as _build_header_bar
from .sidebar_builder import build_sidebar as _build_sidebar

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

        # Initialize tooltip helper for custom tooltips (global singleton)
        self.tooltip_helper = get_tooltip_helper()

        # WM settings for dynamic button layout (may not exist on all DEs like KDE)
        self.wm_settings = None
        self._wm_button_layout = ":"  # Default: colon only = no buttons
        self._kde_borderless_maximized = False  # KDE-specific setting
        self._is_kde = os.environ.get("XDG_CURRENT_DESKTOP", "").upper() in (
            "KDE",
            "PLASMA",
        )

        # Try to read GNOME WM settings (works on GNOME and some GTK-based DEs)
        try:
            self.wm_settings = Gio.Settings.new("org.gnome.desktop.wm.preferences")
            self._wm_button_layout = self.wm_settings.get_string("button-layout")
            self.wm_settings.connect(
                "changed::button-layout", self._on_button_layout_changed
            )
        except Exception:
            # Schema not available (e.g., on KDE without GNOME settings)
            self.logger.debug(
                "org.gnome.desktop.wm.preferences not available, "
                "using default button behavior"
            )

        # On KDE, check kwinrc for BorderlessMaximizedWindows setting
        if self._is_kde:
            self._kde_borderless_maximized = self._check_kde_borderless_maximized()
            self.logger.debug(
                f"KDE detected, BorderlessMaximizedWindows={self._kde_borderless_maximized}"
            )

        # Track headerbar buttons visibility state
        self._headerbar_buttons_hidden = False

        # Connect to window maximized state changes
        self.window.connect("notify::maximized", self._on_maximized_changed)

        # --- Widgets to be created and exposed ---
        self.header_bar = None
        self.flap = None
        self.sidebar_box = None
        self.sidebar_popover = None
        self.sidebar_content_stack = None
        self.inline_context_menu_box = None
        self.toggle_sidebar_button = None
        self.file_manager_button = None
        self.command_manager_button = None
        self.cleanup_button = None
        self.font_sizer_widget: Any = None
        self.scrolled_tab_bar = None
        self.tab_list_button = None
        self.single_tab_title_widget = None
        self.title_stack = None
        self.toast_overlay = None
        self.content_overlay = None
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
        self.ai_chat_panel: Any = None
        self.ai_paned = None
        self.command_toolbar = None  # Toolbar for pinned commands

    def build_ui(self) -> None:
        """Constructs the entire UI and sets it on the parent window."""
        self._setup_styles()
        main_box = self._setup_main_structure()
        self.window.set_content(main_box)
        self.logger.info("Main window UI constructed successfully.")

    def _load_css(self, filename: str, priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION) -> None:
        """Load a CSS file from the styles directory and apply it globally."""
        provider = Gtk.CssProvider()
        css_path = _STYLES_DIR / filename
        if css_path.exists():
            provider.load_from_path(str(css_path))
            self.logger.debug(f"Loaded CSS from {css_path}")
        else:
            self.logger.warning(f"CSS file not found: {css_path}")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, priority,
        )

    def _setup_styles(self) -> None:
        """Applies application-wide CSS for various custom widgets."""
        self._load_css("window.css")
        self._load_css("components.css")

        # Deferred: not needed for initial window render
        def load_deferred_styles():
            self._load_css("tab_groups.css")
            self._load_css("dialogs.css")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(load_deferred_styles, priority=GLib.PRIORITY_LOW)

        # Separate provider for window borders (conditional on maximized state)
        self.border_provider = Gtk.CssProvider()
        self._update_border_css()
        # Scoped to this window so a second window doesn't inherit the border state.
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
        a11y_label(self.terminal_search_entry, _("Search in terminal"))
        self.search_bar.connect_entry(self.terminal_search_entry)
        self.search_prev_button = icon_button("go-up-symbolic")
        a11y_label(self.search_prev_button, _("Previous match"))
        self.search_next_button = icon_button("go-down-symbolic")
        a11y_label(self.search_next_button, _("Next match"))

        # Create the BroadcastBar
        self.broadcast_bar = Gtk.SearchBar()
        self.broadcast_bar.add_css_class("broadcast-bar")
        broadcast_box = Gtk.Box(spacing=6)
        self.broadcast_entry = Gtk.Entry(
            hexpand=True,
            placeholder_text=_("Type your command here and press ENTER..."),
        )
        self.broadcast_entry.add_css_class("broadcast-entry")
        a11y_label(self.broadcast_entry, _("Broadcast command"))
        self.broadcast_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "utilities-terminal-symbolic"
        )
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
        a11y_label(self.case_sensitive_switch, _("Case sensitive search"))
        self.tooltip_helper.add_tooltip(
            self.case_sensitive_switch, _("Case sensitive search")
        )
        case_sensitive_box = Gtk.Box(spacing=6)
        case_sensitive_label = Gtk.Label(label=_("Case sensitive"))
        case_sensitive_box.append(case_sensitive_label)
        case_sensitive_box.append(self.case_sensitive_switch)

        # Regex switch
        self.regex_switch = Gtk.Switch()
        a11y_label(self.regex_switch, _("Use regular expressions"))
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

        # Create the command toolbar for pinned commands
        self.command_toolbar = self._create_command_toolbar()
        main_box.append(self.command_toolbar)

        # Create the main content area (OverlaySplitView)
        self.flap = Adw.OverlaySplitView()
        self.sidebar_box = self._create_sidebar()
        self.flap.set_sidebar(self.sidebar_box)

        self.sidebar_popover = Gtk.Popover(
            position=Gtk.PositionType.BOTTOM, has_arrow=True, autohide=True
        )
        self.sidebar_popover.add_css_class("ashyterm-popover")
        self.sidebar_popover.add_css_class(
            "sidebar-popover"
        )  # Specific class for sidebar
        self.sidebar_popover.set_size_request(200, 800)

        content_area = self._create_content_area()
        self.flap.set_content(content_area)

        # Add the OverlaySplitView as the main content of the window
        main_box.append(self.flap)
        return main_box

    def _create_header_bar(self) -> Adw.HeaderBar:
        return _build_header_bar(self)

    def _on_button_layout_changed(self, settings, key):
        """Handle dynamic changes to window button layout."""
        # Update cached button layout
        self._wm_button_layout = settings.get_string("button-layout")

        if self.header_bar is None:
            return

        # Remove all buttons from header_bar
        buttons = [
            self.toggle_sidebar_button,
            self.file_manager_button,
            self.command_manager_button,
            self.search_button,
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
            self.header_bar.pack_end(self.command_manager_button)
            self.header_bar.pack_end(self.search_button)
            self.header_bar.pack_end(self.cleanup_button)
            self.header_bar.pack_start(self.menu_button)
            self.header_bar.pack_start(self.new_tab_button)
        else:
            # Normal packing
            self.header_bar.pack_start(self.toggle_sidebar_button)
            self.header_bar.pack_start(self.file_manager_button)
            self.header_bar.pack_start(self.command_manager_button)
            self.header_bar.pack_start(self.search_button)
            self.header_bar.pack_start(self.cleanup_button)
            self.header_bar.pack_end(self.menu_button)
            self.header_bar.pack_end(self.new_tab_button)

    def _create_command_toolbar(self) -> Gtk.WindowHandle:
        """Create the toolbar for pinned command buttons.

        The toolbar is wrapped in a Gtk.WindowHandle to allow dragging
        the window by clicking and dragging on the toolbar area.
        """
        toolbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            css_classes=["command-toolbar"],
        )
        # No margins - toolbar should extend edge to edge like headerbar

        # Wrap toolbar in WindowHandle to enable window dragging
        window_handle = Gtk.WindowHandle()
        window_handle.set_child(toolbar)
        # Start hidden — only shown after deferred population finds pinned commands
        window_handle.set_visible(False)

        # Store reference to the inner toolbar for population
        self._toolbar_inner = toolbar

        # Populate with pinned commands - DEFERRED to avoid blocking startup
        # We use a low priority to let the window render first
        GLib.idle_add(
            lambda: (self._populate_command_toolbar(toolbar), GLib.SOURCE_REMOVE)[1],
            priority=GLib.PRIORITY_LOW,
        )

        return window_handle

    # ── Toolbar delegators ──────────────────────────────────
    # Pinned-command button layout + right-click popover live in
    # ``command_toolbar``. The window builder keeps ownership of the
    # Gtk.Box and forwards the callbacks the module asks for.

    def _populate_command_toolbar(self, toolbar: Gtk.Box):
        _cmd_toolbar.populate_command_toolbar(
            toolbar,
            parent_handle=self.command_toolbar,
            logger=self.logger,
            on_click=self._on_toolbar_command_clicked,
            on_right_click=self._on_toolbar_command_right_click,
            tooltip_helper=self.tooltip_helper,
        )

    def _create_toolbar_command_button(self, command) -> Gtk.Button:
        return _cmd_toolbar.create_toolbar_command_button(
            command,
            on_click=self._on_toolbar_command_clicked,
            on_right_click=self._on_toolbar_command_right_click,
            tooltip_helper=self.tooltip_helper,
        )

    def _on_toolbar_command_clicked(self, button):
        """Delegate the click to the window, which owns command execution."""
        command = button._command
        if hasattr(self.window, "execute_toolbar_command"):
            self.window.execute_toolbar_command(command)

    def _on_toolbar_command_right_click(
        self, gesture, n_press, x, y, command, anchor=None
    ):
        """Show the per-button options popover.

        ``anchor`` is passed when the signal originates from
        ``command_toolbar``; legacy call sites that omit it fall back
        to the gesture's widget.
        """
        anchor = anchor or gesture.get_widget()
        popover = _cmd_toolbar.build_right_click_popover(
            command=command,
            anchor=anchor,
            on_set_mode=self._set_toolbar_display_mode,
            on_unpin=self._unpin_toolbar_command,
        )
        popover.popup()

    def _set_toolbar_display_mode(self, command, mode: str):
        _cmd_toolbar.set_toolbar_display_mode(
            command,
            mode,
            refresh=lambda: self._populate_command_toolbar(self._toolbar_inner),
        )

    def _unpin_toolbar_command(self, command):
        _cmd_toolbar.unpin_toolbar_command(
            command,
            refresh=lambda: self._populate_command_toolbar(self._toolbar_inner),
        )

    def _create_sidebar(self) -> Gtk.Widget:
        return _build_sidebar(self)

    def _create_content_area(self) -> Gtk.Widget:
        """Create the main content area with tabs, file manager, and AI panel."""
        view_stack = self.tab_manager.get_view_stack()
        view_stack.add_css_class("terminal-tab-view")

        self.toast_overlay = Adw.ToastOverlay(child=view_stack)
        self.toast_overlay.set_vexpand(True)

        # Wrap toast_overlay in a Gtk.Overlay for general-purpose overlays
        # (e.g. the first-run welcome screen). Adw.ToastOverlay only supports
        # toasts; Gtk.Overlay provides add_overlay()/remove_overlay().
        self.content_overlay = Gtk.Overlay(child=self.toast_overlay)
        self.content_overlay.set_vexpand(True)

        # Use Paned for AI panel resize capability
        self.ai_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.ai_paned.set_start_child(self.content_overlay)
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

        from .widgets.ai_chat import AIChatPanel

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

        # Focus the input field after the panel is mapped
        GLib.idle_add(self.ai_chat_panel.focus_input)

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
        self.border_provider.load_from_string(css)

    def _check_kde_borderless_maximized(self) -> bool:
        """Check KDE's kwinrc for BorderlessMaximizedWindows setting.

        Returns:
            True if KDE is configured to remove borders from maximized windows.
        """
        try:
            # KDE stores window manager settings in ~/.config/kwinrc
            kwinrc_path = Path.home() / ".config" / "kwinrc"
            if not kwinrc_path.exists():
                return False

            content = kwinrc_path.read_text()
            # Look for the [Windows] section and BorderlessMaximizedWindows setting
            in_windows_section = False
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("["):
                    in_windows_section = line.lower() == "[windows]"
                elif in_windows_section and line.lower().startswith(
                    "borderlessmaximizedwindows"
                ):
                    # Parse the value (format: BorderlessMaximizedWindows=true)
                    if "=" in line:
                        value = line.split("=", 1)[1].strip().lower()
                        return value in ("true", "1", "yes")
            return False
        except Exception as e:
            self.logger.debug(f"Could not read KDE kwinrc: {e}")
            return False

    def _should_hide_headerbar_buttons(self) -> bool:
        """Determine if headerbar buttons should be hidden when maximized.

        This supports desktop environments like KDE Plasma with
        "Active Window Control" or "Borderless Maximized Windows"
        where window control buttons are shown in the panel instead.

        Returns:
            True if headerbar buttons should be hidden, False otherwise.
        """
        setting = self.settings_manager.get(
            "hide_headerbar_buttons_when_maximized", "auto"
        )

        if setting == "never":
            return False
        elif setting == "always":
            return True
        else:  # "auto" - detect from environment
            # On KDE, check if BorderlessMaximizedWindows is enabled
            if self._is_kde and self._kde_borderless_maximized:
                return True

            # On GNOME and other DEs, check if button-layout is empty
            # Format: "buttons-on-left:buttons-on-right"
            # Examples:
            #   "close,minimize,maximize:" - buttons on left
            #   ":close,minimize,maximize" - buttons on right
            #   ":" or "" - no buttons (DE manages them externally)
            layout = self._wm_button_layout.strip()
            if not layout or layout == ":":
                return True
            # Check if both sides are empty
            parts = layout.split(":")
            left_buttons = parts[0].strip() if len(parts) > 0 else ""
            right_buttons = parts[1].strip() if len(parts) > 1 else ""
            # If no actual button names on either side, hide them
            return not (left_buttons or right_buttons)

    def _update_headerbar_buttons_visibility(self):
        """Update headerbar title buttons visibility based on maximized state."""
        if self.header_bar is None:
            return

        is_maximized = self.window.is_maximized()
        should_hide = is_maximized and self._should_hide_headerbar_buttons()

        # Only update if state changed to avoid unnecessary redraws
        if should_hide != self._headerbar_buttons_hidden:
            self._headerbar_buttons_hidden = should_hide
            # Use Adw.HeaderBar methods to show/hide window control buttons
            self.header_bar.set_show_start_title_buttons(not should_hide)
            self.header_bar.set_show_end_title_buttons(not should_hide)

            if should_hide:
                self.logger.debug(
                    "Hiding headerbar title buttons (maximized with external controls)"
                )
            else:
                self.logger.debug("Showing headerbar title buttons")

    def _on_maximized_changed(self, window, param):
        """Handle window maximized state changes."""
        self._update_border_css()
        self._update_headerbar_buttons_visibility()
