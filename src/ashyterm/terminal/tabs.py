# ashyterm/terminal/tabs.py

import re
import threading
import time
import weakref
from typing import TYPE_CHECKING, Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from ..helpers import create_themed_popover_menu
from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager as SettingsManagerType
from ..utils.accessibility import set_label as a11y_label, set_description as a11y_desc
from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .banner_manager import BannerManager
from .fm_integration import FileManagerIntegration
from .manager import TerminalManager
from .pane_manager import PaneManager
from .scroll_handler import ScrollHandler

if TYPE_CHECKING:
    from ..filemanager.manager import FileManager

# Pre-compiled pattern for parsing RGBA color strings
_RGBA_COLOR_PATTERN = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+),?.*\)")

# CSS for tab moving visual feedback is now loaded from:
# data/styles/components.css (loaded by window_ui.py at startup)
# Classes: .tab-moving, .tab-bar-move-mode, .tab-drop-target, .tab-drop-left, .tab-drop-right


def _create_terminal_pane(
    terminal: Vte.Terminal,
    title: str,
    on_close_callback: Callable[[Vte.Terminal], None],
    on_move_to_tab_callback: Callable[[Vte.Terminal], None],
    settings_manager: SettingsManagerType,
) -> Adw.ToolbarView:
    """
    Creates a terminal pane using Adw.ToolbarView with a custom header to avoid GTK baseline warnings.
    """
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_css_class("terminal-pane")

    # Create custom header bar using basic GTK widgets to avoid Adw.HeaderBar baseline issues
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header_box.add_css_class("header-bar")
    header_box.set_hexpand(True)
    header_box.set_valign(Gtk.Align.START)

    # header_box has .header-bar class, which is targeted by ThemeEngine CSS for transparency

    # Title label
    title_label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0)
    title_label.set_hexpand(True)
    title_label.set_halign(Gtk.Align.START)
    header_box.append(title_label)

    # Action buttons (using bundled icons)
    move_to_tab_button = icon_button(
        "select-rectangular-symbolic", tooltip=_("Move to New Tab")
    )
    move_to_tab_button.add_css_class("flat")
    move_to_tab_button.connect("clicked", lambda _: on_move_to_tab_callback(terminal))

    close_button = icon_button("window-close-symbolic", tooltip=_("Close Pane"))
    close_button.add_css_class("flat")
    close_button.connect("clicked", lambda _: on_close_callback(terminal))

    header_box.append(move_to_tab_button)
    header_box.append(close_button)

    toolbar_view.add_top_bar(header_box)

    # Main content (the terminal)
    scrolled_window = Gtk.ScrolledWindow(child=terminal)
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled_window.set_vexpand(True)
    scrolled_window.set_hexpand(True)
    toolbar_view.set_content(scrolled_window)

    # Attach important widgets for later access
    toolbar_view.terminal = terminal
    toolbar_view.title_label = title_label
    toolbar_view.move_button = move_to_tab_button
    toolbar_view.close_button = close_button
    # MODIFIED: Store a reference to the header box for live updates
    toolbar_view.header_box = header_box

    return toolbar_view


class TabManager:
    def __init__(
        self,
        terminal_manager: TerminalManager,
        on_quit_callback: Callable[[], None],
        on_detach_tab_callback: Callable[[Adw.ViewStackPage], None],
        scrolled_tab_bar: Gtk.ScrolledWindow,
        on_tab_count_changed: Optional[Callable[[], None]] = None,
    ):
        """
        Initializes the TabManager.

        Args:
            terminal_manager: The central manager for terminal instances.
            on_quit_callback: A function to call when the last tab closes.
            on_detach_tab_callback: A function to call to detach a tab into a new window.
            scrolled_tab_bar: The ScrolledWindow containing the tab bar.
            on_tab_count_changed: A function to call when the number of tabs changes.
        """
        self.logger = get_logger("ashyterm.tabs.manager")
        self.terminal_manager = terminal_manager
        self.on_quit_application = on_quit_callback
        self.on_detach_tab_requested = on_detach_tab_callback
        self.scrolled_tab_bar = scrolled_tab_bar
        self.on_tab_count_changed = on_tab_count_changed

        self.view_stack = Adw.ViewStack()
        self.tab_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._update_tab_alignment()
        self.tabs: List[Gtk.Box] = []
        self.pages: weakref.WeakKeyDictionary[Gtk.Box, Adw.ViewStackPage] = (
            weakref.WeakKeyDictionary()
        )
        self.file_managers: weakref.WeakKeyDictionary[
            Adw.ViewStackPage, "FileManager"
        ] = weakref.WeakKeyDictionary()
        self.active_tab: Optional[Gtk.Box] = None
        self._tab_being_moved: Optional[Gtk.Box] = None  # Track tab in move mode
        self._drop_target_tab: Optional[Gtk.Box] = None  # Tab under cursor during move
        self._drop_side: str = (
            "left"  # "left" or "right" - which side of target to drop
        )

        # Set up tab bar for receiving move drop events
        self._setup_tab_bar_move_handlers()

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_focused_terminal = None

        # Delegates
        self.scroll_handler = ScrollHandler(self)
        self.fm_handler = FileManagerIntegration(self)
        self.pane_handler = PaneManager(self)
        self.banner_handler = BannerManager(self)

        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )
        # MODIFIED: Listen for settings changes to update pane headers live
        self.terminal_manager.settings_manager.add_change_listener(
            self._on_setting_changed
        )
        self.logger.info("Tab manager initialized with custom tab bar")

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Callback for settings changes to update UI elements live."""
        pass

    def _find_panes_recursive(self, widget, panes_list: List[Adw.ToolbarView]):
        """Recursively find all Adw.ToolbarView panes within a container."""
        self.pane_handler.find_panes_recursive(widget, panes_list)

    def _setup_tab_bar_move_handlers(self):
        """Set up event handlers on the tab bar for tab move operations."""
        # We only need to handle motion on individual tabs, which is done via
        # controllers added in _create_tab_widget. No tab bar level handlers needed
        # since we handle everything at the tab level.

    def _update_move_highlight(self, target_tab: Gtk.Box, side: str):
        """Update the visual highlight for the drop target."""
        self._clear_tab_drop_highlights()
        if target_tab and side:
            self._drop_target_tab = target_tab
            self._drop_side = side
            if side == "left":
                target_tab.add_css_class("tab-drop-left")
            else:
                target_tab.add_css_class("tab-drop-right")

    def _clear_tab_drop_highlights(self):
        """Remove drop target highlights from all tabs."""
        self._drop_target_tab = None
        for tab in self.tabs:
            tab.remove_css_class("tab-drop-target")
            tab.remove_css_class("tab-drop-left")
            tab.remove_css_class("tab-drop-right")

    def _perform_tab_move(self):
        """Perform the actual tab move based on current drop target and side."""
        if not self._tab_being_moved or not self._drop_target_tab:
            return

        moving_tab = self._tab_being_moved
        target_tab = self._drop_target_tab
        side = self._drop_side

        if moving_tab == target_tab:
            return

        # Get current indices
        moving_idx = self.tabs.index(moving_tab)
        target_idx = self.tabs.index(target_tab)

        # Calculate final position
        if side == "left":
            # Insert before target
            new_idx = target_idx
        else:
            # Insert after target
            new_idx = target_idx + 1

        # Adjust if moving from before the target
        if moving_idx < new_idx:
            new_idx -= 1

        # Only move if position actually changes
        if moving_idx == new_idx:
            return

        # Remove from old position
        self.tabs.remove(moving_tab)

        # Insert at new position
        self.tabs.insert(new_idx, moving_tab)

        # Rebuild visual order
        self._rebuild_tab_bar_order()

        self.logger.info(
            f"Tab '{moving_tab.label_widget.get_text()}' moved from {moving_idx} to {new_idx}"
        )

    def cancel_tab_move_if_active(self) -> bool:
        """Cancel the tab move operation if one is active. Returns True if cancelled."""
        if self._tab_being_moved is not None:
            self._cancel_tab_move()
            return True
        return False

    def _update_tab_alignment(self):
        """Updates the tab bar alignment based on the current setting."""
        alignment = self.terminal_manager.settings_manager.get(
            "tab_alignment", "center"
        )
        if alignment == "left":
            self.tab_bar_box.set_halign(Gtk.Align.START)
        else:  # center or any other value defaults to center
            self.tab_bar_box.set_halign(Gtk.Align.CENTER)

    def get_view_stack(self) -> Adw.ViewStack:
        return self.view_stack

    def get_tab_bar(self) -> Gtk.Box:
        return self.tab_bar_box

    def copy_from_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.copy_selection(terminal)
            return True
        return False

    def paste_to_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.paste_clipboard(terminal)
            return True
        return False

    def select_all_in_current_terminal(self) -> None:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.select_all(terminal)

    def clear_current_terminal(self) -> bool:
        """Reset the active terminal, clearing both screen and scrollback."""
        if terminal := self.get_selected_terminal():
            self.terminal_manager.clear_terminal(terminal)
            return True
        return False

    def create_initial_tab_if_empty(
        self,
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> None:
        if self.get_tab_count() == 0:
            self.create_local_tab(
                working_directory=working_directory,
                execute_command=execute_command,
                close_after_execute=close_after_execute,
            )

    def create_local_tab(
        self,
        session: Optional[SessionItem] = None,
        title: str = "Local",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Optional[Vte.Terminal]:
        if session is None:
            session = SessionItem(name=title, session_type="local")

        # Use session's local_working_directory if not overridden
        effective_working_dir = working_directory
        if effective_working_dir is None and hasattr(
            session, "local_working_directory"
        ):
            effective_working_dir = session.local_working_directory or None

        # Use session's local_startup_command if not overridden
        effective_command = execute_command
        if effective_command is None and hasattr(session, "local_startup_command"):
            effective_command = session.local_startup_command or None

        terminal = self.terminal_manager.create_local_terminal(
            session=session,
            title=session.name,
            working_directory=effective_working_dir,
            execute_command=effective_command,
            close_after_execute=close_after_execute,
        )
        if terminal:
            self._create_tab_for_terminal(terminal, session)
        return terminal

    def create_ssh_tab(
        self, session: SessionItem, initial_command: Optional[str] = None
    ) -> Optional[Vte.Terminal]:
        terminal = self.terminal_manager.create_ssh_terminal(
            session, initial_command=initial_command
        )
        if terminal:
            self._create_tab_for_terminal(terminal, session)
        return terminal

    def create_sftp_tab(self, session: SessionItem) -> Optional[Vte.Terminal]:
        """Creates a new tab with an SFTP terminal for the specified session."""
        terminal = self.terminal_manager.create_sftp_terminal(session)
        if terminal:
            sftp_session = SessionItem.from_dict(session.to_dict())
            sftp_session.name = self._generate_unique_sftp_name(session.name)
            self._create_tab_for_terminal(terminal, sftp_session)
        return terminal

    def _generate_unique_sftp_name(self, base_session_name: str) -> str:
        base_title = f"SFTP-{base_session_name}"
        existing_titles = []
        for tab in self.tabs:
            session_item = getattr(tab, "session_item", None)
            if isinstance(session_item, SessionItem) and session_item.name.startswith(
                base_title
            ):
                existing_titles.append(session_item.name)

        if base_title not in existing_titles:
            return base_title

        suffix = 1
        while True:
            candidate = f"{base_title}({suffix})"
            if candidate not in existing_titles:
                return candidate
            suffix += 1

    def _scroll_to_widget(self, widget: Gtk.Widget) -> None:
        self.scroll_handler.scroll_to_widget(widget)

    def _replace_sw_scroll_controller(self, sw: Gtk.ScrolledWindow) -> None:
        self.scroll_handler.replace_sw_scroll_controller(sw)

    def _on_terminal_scroll(self, controller, dx, dy):
        return self.scroll_handler.on_terminal_scroll(controller, dx, dy)

    def _handle_scroll_zoom(self, dy):
        self.scroll_handler._handle_scroll_zoom(dy)

    def _get_scroll_input_source(self, controller):
        return self.scroll_handler._get_scroll_input_source(controller)

    def _calculate_scroll_amount(self, dy, vadjustment, source):
        return self.scroll_handler._calculate_scroll_amount(dy, vadjustment, source)

    def _track_kinetic_scroll(self, sw, source, scroll_amount):
        self.scroll_handler._track_kinetic_scroll(sw, source, scroll_amount)

    def _start_kinetic_deceleration(self, sw):
        return self.scroll_handler._start_kinetic_deceleration(sw)

    def _kinetic_tick(self, sw):
        return self.scroll_handler._kinetic_tick(sw)

    def _on_terminal_contents_changed(self, terminal: Vte.Terminal):
        self.scroll_handler.on_terminal_contents_changed(terminal)

    def _on_terminal_bell(self, terminal: Vte.Terminal) -> None:
        """Flash the tab label briefly when a bell/BEL is received in a background tab."""
        page = getattr(terminal, "ashy_parent_page", None)
        if not page:
            return

        tab_widget = self._find_tab_for_page(page)
        if not tab_widget or tab_widget == self.active_tab:
            return

        label = getattr(tab_widget, "label_widget", None)
        if not label:
            return

        # Use CSS animation: add class, remove after timeout
        if hasattr(tab_widget, "_bell_timeout_id"):
            GLib.source_remove(tab_widget._bell_timeout_id)

        tab_widget.add_css_class("tab-bell")

        def _remove_bell_class():
            tab_widget.remove_css_class("tab-bell")
            if hasattr(tab_widget, "_bell_timeout_id"):
                del tab_widget._bell_timeout_id
            return GLib.SOURCE_REMOVE

        tab_widget._bell_timeout_id = GLib.timeout_add(1500, _remove_bell_class)

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, session: SessionItem
    ) -> None:
        terminal.connect("contents-changed", self.scroll_handler.on_terminal_contents_changed)
        terminal.connect("bell", self._on_terminal_bell)

        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Replace ScrolledWindow's built-in EventControllerScroll with our
        # own so we have full control over scrolling (sensitivity + kinetic).
        self.scroll_handler.replace_sw_scroll_controller(scrolled_window)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        terminal_area = Adw.Bin()
        terminal_area.set_child(scrolled_window)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        content_paned.add_css_class("terminal-content-paned")
        content_paned.set_start_child(terminal_area)
        content_paned.set_resize_start_child(True)
        content_paned.set_shrink_start_child(False)
        content_paned.set_end_child(None)
        content_paned.set_resize_end_child(False)
        content_paned.set_shrink_end_child(True)

        page_name = f"page_{terminal.terminal_id}"
        page = self.view_stack.add_titled(content_paned, page_name, session.name)
        page.content_paned = content_paned
        terminal.ashy_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

        GLib.idle_add(self.scroll_handler.scroll_to_widget, tab_widget)

    def _get_contrasting_text_color(self, bg_color_str: str) -> str:
        """Calculates whether black or white text is more readable on a given background color."""
        if not bg_color_str:
            return "#000000"  # Default to black

        try:
            match = _RGBA_COLOR_PATTERN.match(bg_color_str)
            if not match:
                return "#000000"

            r, g, b = [int(c) / 255.0 for c in match.groups()]

            # WCAG luminance formula
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b

            return "#000000" if luminance > 0.5 else "#FFFFFF"
        except Exception as e:
            self.logger.warning(f"Could not parse color '{bg_color_str}': {e}")
            return "#000000"

    def _apply_tab_color(self, widget: Gtk.Widget, color_string: Optional[str]):
        style_context = widget.get_style_context()
        if hasattr(widget, "_color_provider"):
            style_context.remove_provider(widget._color_provider)
            del widget._color_provider

        if color_string:
            provider = Gtk.CssProvider()

            # Apply color only to the top part of the tab (top border)
            css = f"""
                .custom-tab-button {{
                    border: 1px solid {color_string};
                }}
            """
            provider.load_from_data(css.encode("utf-8"))
            style_context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            widget._color_provider = provider

    def _create_tab_widget(
        self, _page: Adw.ViewStackPage, session: SessionItem
    ) -> Gtk.Box:
        tab_widget = Gtk.Box(spacing=6)
        tab_widget.add_css_class("custom-tab-button")
        tab_widget.add_css_class("raised")

        icon_name = None
        if session.name.startswith("SFTP-"):
            icon_name = "folder-remote-symbolic"
        elif session.is_ssh():
            icon_name = "network-server-symbolic"

        if icon_name:
            icon = icon_image(icon_name)
            tab_widget.append(icon)

        label = Gtk.Label(
            label=session.name, ellipsize=Pango.EllipsizeMode.START, xalign=1.0
        )
        label.set_width_chars(8)
        tab_widget.append(label)

        close_button = icon_button(
            "window-close-symbolic", css_classes=["circular", "flat"]
        )
        a11y_label(close_button, _("Close tab"))
        tab_widget.append(close_button)
        a11y_label(tab_widget, session.name)
        a11y_desc(tab_widget, _("Terminal tab"))

        left_click = Gtk.GestureClick.new()
        left_click.connect("pressed", self._on_tab_clicked, tab_widget)
        tab_widget.add_controller(left_click)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_tab_right_click, tab_widget)
        tab_widget.add_controller(right_click)

        # Motion controller for hover highlighting during tab move
        # DISABLED FOR WAYLAND DEBUGGING: Manual drag-and-drop can cause freezes
        # motion_controller = Gtk.EventControllerMotion()
        # motion_controller.connect("motion", self._on_tab_motion, tab_widget)
        # motion_controller.connect("leave", self._on_tab_leave, tab_widget)
        # tab_widget.add_controller(motion_controller)

        close_button.connect("clicked", self._on_tab_close_button_clicked, tab_widget)

        tab_widget.label_widget = label
        tab_widget.close_button = close_button  # Store direct reference
        tab_widget._base_title = session.name or f"Terminal-{self.get_tab_count() + 1}"
        tab_widget._is_local = session.is_local()
        tab_widget.session_item = session

        self._apply_tab_color(tab_widget, session.tab_color)

        return tab_widget

    def _on_tab_motion(self, controller, x, y, tab_widget):
        """Handle mouse motion over a tab during move mode."""
        if self._tab_being_moved is None or tab_widget == self._tab_being_moved:
            return

        # Determine which half of the tab we're over
        tab_width = tab_widget.get_width()
        side = "left" if x < tab_width / 2 else "right"

        # Update highlight
        self._update_move_highlight(tab_widget, side)

    def _on_tab_leave(self, controller, tab_widget):
        """Handle mouse leaving a tab during move mode."""
        if self._tab_being_moved is None:
            return
        # Clear highlight when leaving a tab
        self._clear_tab_drop_highlights()

    def _on_tab_clicked(self, gesture, _n_press, x, _y, tab_widget):
        # If we're in move mode, handle the drop
        if self._tab_being_moved is not None:
            if self._tab_being_moved != tab_widget:
                # Determine which half of the tab was clicked
                tab_width = tab_widget.get_width()
                side = "left" if x < tab_width / 2 else "right"

                # Update the drop target and perform the move
                self._drop_target_tab = tab_widget
                self._drop_side = side
                self._perform_tab_move()
            self._cancel_tab_move()
            return
        self.set_active_tab(tab_widget)

    def _on_tab_right_click(self, _gesture, _n_press, x, y, tab_widget):
        # Cancel any ongoing move operation when right-clicking
        if self._tab_being_moved is not None:
            self._cancel_tab_move()

        menu = Gio.Menu()
        menu.append(_("Move Tab"), "win.move-tab")
        menu.append(_("Duplicate Tab"), "win.duplicate-tab")
        menu.append(_("Detach Tab"), "win.detach-tab")

        color_section = Gio.Menu()
        color_section.append(_("Tab Color…"), "win.tab-color")
        session = getattr(tab_widget, "session_item", None)
        if session and session.tab_color:
            color_section.append(_("Clear Tab Color"), "win.clear-tab-color")
        menu.append_section(None, color_section)

        popover = create_themed_popover_menu(menu, tab_widget)

        page = self.pages.get(tab_widget)
        if page:
            action_group = Gio.SimpleActionGroup()

            move_action = Gio.SimpleAction.new("move-tab", None)
            move_action.connect(
                "activate",
                lambda _action, _param, tab=tab_widget: self._start_tab_move(tab),
            )
            action_group.add_action(move_action)

            duplicate_action = Gio.SimpleAction.new("duplicate-tab", None)
            duplicate_action.connect(
                "activate",
                lambda _action, _param, tab=tab_widget: self._duplicate_tab(tab),
            )
            action_group.add_action(duplicate_action)

            action = Gio.SimpleAction.new("detach-tab", None)

            action.connect(
                "activate", lambda a, _, pg=page: self._request_detach_tab(pg)
            )
            action_group.add_action(action)

            color_action = Gio.SimpleAction.new("tab-color", None)
            color_action.connect(
                "activate",
                lambda _a, _p, tab=tab_widget, pop=popover: self._pick_tab_color(
                    tab, pop
                ),
            )
            action_group.add_action(color_action)

            clear_color_action = Gio.SimpleAction.new("clear-tab-color", None)
            clear_color_action.connect(
                "activate",
                lambda _a, _p, tab=tab_widget: self._clear_tab_color(tab),
            )
            action_group.add_action(clear_color_action)

            popover.insert_action_group("win", action_group)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        popover.set_pointing_to(rect)
        popover.popup()

    def _pick_tab_color(self, tab_widget: Gtk.Box, popover: Gtk.Popover) -> None:
        """Open color chooser dialog for a tab."""
        popover.popdown()
        dialog = Gtk.ColorDialog(title=_("Tab Color"))
        dialog.choose_rgba(
            self.view_stack.get_root(), None, None, self._on_tab_color_chosen, tab_widget
        )

    def _on_tab_color_chosen(self, dialog, result, tab_widget) -> None:
        """Handle color chooser result."""
        try:
            color = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return
        color_str = f"rgba({int(color.red * 255)},{int(color.green * 255)},{int(color.blue * 255)},{color.alpha:.2f})"
        self._apply_tab_color(tab_widget, color_str)
        session = getattr(tab_widget, "session_item", None)
        if session:
            session.tab_color = color_str

    def _clear_tab_color(self, tab_widget: Gtk.Box) -> None:
        """Remove custom color from a tab."""
        self._apply_tab_color(tab_widget, None)
        session = getattr(tab_widget, "session_item", None)
        if session:
            session.tab_color = None

    def _request_detach_tab(self, page: Adw.ViewStackPage):
        if self.on_detach_tab_requested:
            self.on_detach_tab_requested(page)

    def _start_tab_move(self, tab_widget: Gtk.Box) -> None:
        """Starts the tab move mode for the given tab widget."""
        if len(self.tabs) < 2:
            self.logger.debug("Cannot move tab: only one tab exists.")
            return

        self._tab_being_moved = tab_widget
        self._current_drop_index = -1
        tab_widget.add_css_class("tab-moving")
        self.tab_bar_box.add_css_class("tab-bar-move-mode")

        # Hide and disable all close buttons during move mode to prevent accidental closing
        for tab in self.tabs:
            close_btn = self._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_visible(False)
                close_btn.set_sensitive(False)

        self.logger.info(f"Tab move started for: {tab_widget.label_widget.get_text()}")

    def _cancel_tab_move(self) -> None:
        """Cancels the current tab move operation."""
        if self._tab_being_moved is not None:
            self._tab_being_moved.remove_css_class("tab-moving")
            self.tab_bar_box.remove_css_class("tab-bar-move-mode")
            self._clear_tab_drop_highlights()

            # Restore all close buttons
            for tab in self.tabs:
                close_btn = self._get_tab_close_button(tab)
                if close_btn:
                    close_btn.set_visible(True)
                    close_btn.set_sensitive(True)

            self.logger.debug("Tab move cancelled.")
            self._tab_being_moved = None

    def _get_tab_close_button(self, tab_widget: Gtk.Box) -> Optional[Gtk.Button]:
        """Get the close button from a tab widget."""
        # Try direct reference first (for newly created tabs)
        if hasattr(tab_widget, "close_button") and tab_widget.close_button:
            return tab_widget.close_button
        # Fallback: iterate through all children to find the close button
        child = tab_widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                return child
            child = child.get_next_sibling()
        return None

    def _rebuild_tab_bar_order(self) -> None:
        """Rebuilds the tab bar widget order to match self.tabs list."""
        # Remove all tabs from the box
        for tab in self.tabs:
            self.tab_bar_box.remove(tab)

        # Re-add them in the correct order
        for tab in self.tabs:
            self.tab_bar_box.append(tab)

    def _duplicate_tab(self, tab_widget: Gtk.Box) -> None:
        """Creates a new tab duplicating the session represented by the given tab widget."""
        page = self.pages.get(tab_widget)
        if not page:
            return

        terminals = self.get_all_terminals_in_page(page)
        if not terminals:
            self.logger.warning("Cannot duplicate tab without terminals.")
            return

        primary_terminal = terminals[0]
        terminal_id = getattr(primary_terminal, "terminal_id", None)
        if not terminal_id:
            self.logger.warning(
                "Primary terminal missing identifier; duplication aborted."
            )
            return

        terminal_info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            self.logger.warning("Terminal info unavailable; duplication aborted.")
            return

        session = getattr(tab_widget, "session_item", None)
        session_copy = (
            SessionItem.from_dict(session.to_dict())
            if isinstance(session, SessionItem)
            else None
        )

        term_type = terminal_info.get("type")
        self._create_duplicate_tab_by_type(
            term_type, session_copy, primary_terminal, tab_widget
        )

    def _create_duplicate_tab_by_type(
        self,
        term_type: str,
        session_copy: Optional[SessionItem],
        primary_terminal: Vte.Terminal,
        tab_widget: Gtk.Box,
    ) -> None:
        """Create a duplicate tab based on terminal type."""
        try:
            if term_type == "local":
                working_directory = self._get_terminal_working_directory(
                    primary_terminal
                )
                self.create_local_tab(
                    session=session_copy,
                    working_directory=working_directory,
                )
                return

            if not session_copy:
                self.logger.warning(
                    f"Cannot duplicate {term_type} tab without session data."
                )
                return

            if term_type == "ssh":
                self.create_ssh_tab(session_copy)
            elif term_type == "sftp":
                self.create_sftp_tab(session_copy)
            else:
                self.logger.warning(
                    f"Unsupported terminal type for duplication: {term_type}"
                )
        except Exception as exc:
            self.logger.error(
                f"Failed to duplicate tab '{tab_widget.label_widget.get_text()}': {exc}"
            )

    def _get_terminal_working_directory(self, terminal: Vte.Terminal) -> Optional[str]:
        """Returns the terminal's current working directory path, if available."""
        uri = terminal.get_current_directory_uri()
        if not uri:
            return None

        try:
            path, _ = GLib.filename_from_uri(uri)
            return path
        except (TypeError, ValueError) as error:
            self.logger.debug(
                f"Could not resolve working directory from '{uri}': {error}"
            )
            return None

    def _is_widget_in_filemanager(self, widget: Gtk.Widget) -> bool:
        return self.fm_handler.is_widget_in_filemanager(widget)

    def set_active_tab(self, tab_to_activate: Gtk.Box):
        if self.active_tab == tab_to_activate:
            return

        self._handle_previous_tab_focus()

        if self.active_tab:
            self.active_tab.remove_css_class("active")

        self.active_tab = tab_to_activate
        self.active_tab.add_css_class("active")

        page = self.pages.get(self.active_tab)
        if not page:
            return

        self.view_stack.set_visible_child(page.get_child())
        terminal_to_focus = self._get_terminal_to_focus(page)
        if terminal_to_focus:
            self._schedule_terminal_focus(terminal_to_focus)

    def _handle_previous_tab_focus(self):
        """Handle focus when switching away from current tab."""
        if not self.active_tab:
            return
        main_window = self.terminal_manager.parent_window
        focus_widget = main_window.get_focus()
        if focus_widget and self.fm_handler.is_widget_in_filemanager(focus_widget):
            self.view_stack.grab_focus()

    def _get_terminal_to_focus(self, page: Adw.ViewStackPage) -> Optional[Vte.Terminal]:
        """Get the terminal to focus when activating a tab."""
        # Check if the page has a remembered focused terminal
        if hasattr(page, "_last_focused_in_page") and page._last_focused_in_page:
            terminal = page._last_focused_in_page()
            if terminal:
                return terminal

        # Fall back to the first terminal
        terminals_in_page = self.get_all_terminals_in_page(page)
        return terminals_in_page[0] if terminals_in_page else None

    def _get_active_tab_page(self):
        """Returns the page for the active tab, or None if not available."""
        if not self.active_tab:
            return None
        return self.pages.get(self.active_tab)

    def _reset_file_manager_button(self):
        self.fm_handler.reset_file_manager_button()

    def _activate_file_manager(self, page, paned, fm):
        self.fm_handler.activate_file_manager(page, paned, fm)

    def _create_file_manager(self, page):
        return self.fm_handler.create_file_manager(page)

    def _calculate_file_manager_position(self, page, paned) -> int:
        return self.fm_handler.calculate_file_manager_position(page, paned)

    def _get_available_paned_height(self, paned) -> int:
        return self.fm_handler.get_available_paned_height(paned)

    def _connect_paned_position_handler(self, paned, page):
        self.fm_handler.connect_paned_position_handler(paned, page)

    def _deactivate_file_manager(self, page, paned, fm):
        self.fm_handler.deactivate_file_manager(page, paned, fm)

    def toggle_file_manager_for_active_tab(self, is_active: bool):
        self.fm_handler.toggle_file_manager_for_active_tab(is_active)

    def _on_file_manager_paned_position_changed(self, paned, _param_spec, page):
        self.fm_handler.on_file_manager_paned_position_changed(paned, _param_spec, page)

    def _on_tab_close_button_clicked(self, button: Gtk.Button, tab_widget: Gtk.Box):
        # If in move mode, ignore close button clicks entirely
        if self._tab_being_moved is not None:
            return

        self.logger.debug(
            f"Close button clicked for tab: {tab_widget.label_widget.get_text()}"
        )
        page = self.pages.get(tab_widget)
        if not page:
            return

        terminals_in_page = self.get_all_terminals_in_page(page)
        self.logger.info(
            f"Close request for tab '{page.get_title()}' with {len(terminals_in_page)} terminals."
        )

        # Check if any terminal has a foreground child process
        if self._any_terminal_has_foreground_process(terminals_in_page):
            self._confirm_close_tab(page, terminals_in_page)
            return

        should_wait_for_exit = self._process_terminals_for_close(terminals_in_page)

        # If no terminal has a stable active process, close the tab immediately
        if not should_wait_for_exit:
            self._close_tab_by_page(page)

    def _any_terminal_has_foreground_process(self, terminals: list) -> bool:
        """Check if any terminal has a running foreground child process."""
        try:
            import psutil
        except ImportError:
            return False

        for terminal in terminals:
            terminal_id = getattr(terminal, "terminal_id", None)
            if not terminal_id:
                continue
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            if not info:
                continue
            pid = info.get("process_id")
            if not pid or pid == -1:
                continue
            try:
                proc = psutil.Process(pid)
                children = proc.children()
                if children:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _confirm_close_tab(self, page: Adw.ViewStackPage, terminals: list) -> None:
        """Show confirmation dialog before closing tab with active process."""
        dialog = Adw.AlertDialog(
            heading=_("Close Tab?"),
            body=_("A process is still running in this tab. Close anyway?"),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("close", _("Close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_close_confirm_response, page, terminals)
        dialog.present(self.view_stack.get_root())

    def _on_close_confirm_response(
        self, dialog, response: str, page: Adw.ViewStackPage, terminals: list
    ) -> None:
        """Handle close confirmation dialog response."""
        if response != "close":
            return

        should_wait_for_exit = self._process_terminals_for_close(terminals)
        if not should_wait_for_exit:
            self._close_tab_by_page(page)

    def _process_terminals_for_close(self, terminals: list) -> bool:
        """Process terminals during tab close and return if should wait for exit."""
        should_wait = False

        for terminal in terminals:
            terminal_id = getattr(terminal, "terminal_id", None)
            is_auto_reconnecting = self.terminal_manager.is_auto_reconnect_active(
                terminal
            )

            if terminal_id and not is_auto_reconnecting:
                info = self.terminal_manager.registry.get_terminal_info(terminal_id)
                if self._has_stable_running_process(info):
                    should_wait = True

            self.terminal_manager.remove_terminal(terminal, force_kill_group=True)

        return should_wait

    def _has_stable_running_process(self, info: Optional[dict]) -> bool:
        """Check if terminal info indicates a stable running process."""
        if not info:
            return False
        pid = info.get("process_id")
        status = info.get("status")
        return pid and pid != -1 and status == "running"

    def _on_terminal_process_exited(
        self, terminal: Vte.Terminal, child_status: int, identifier
    ):
        with self._cleanup_lock:
            page = self.get_page_for_terminal(terminal)
            terminal_id = getattr(terminal, "terminal_id", "N/A")

            self.logger.info(f"[PROCESS_EXITED] Terminal {terminal_id} process exited")
            self.logger.info(
                f"[PROCESS_EXITED] Auto-reconnect active: {self.terminal_manager.is_auto_reconnect_active(terminal)}"
            )

            # IMPORTANT: If auto-reconnect is active, don't do any cleanup
            # The terminal should stay open for reconnection attempts
            if self.terminal_manager.is_auto_reconnect_active(terminal):
                self.logger.info(
                    f"[PROCESS_EXITED] Skipping cleanup for terminal {terminal_id} - auto-reconnect is active"
                )
                return

            pane_to_remove, parent_container = self.pane_handler.find_pane_and_parent(terminal)
            self.logger.info(
                f"[PROCESS_EXITED] Found pane: {pane_to_remove}, parent: {type(parent_container)}"
            )

            # MODIFIED: Only manipulate panes if the parent is a Gtk.Paned (i.e., it's a split)
            if isinstance(parent_container, Gtk.Paned):
                self.logger.info(
                    f"[PROCESS_EXITED] Removing pane from split for terminal {terminal_id}"
                )
                self.pane_handler.remove_pane_ui(pane_to_remove, parent_container)

            self.terminal_manager._cleanup_terminal(terminal, terminal_id)

            if not page:
                return

            active_terminals_in_page = self.get_all_active_terminals_in_page(page)

            if not active_terminals_in_page:
                self.logger.info(
                    f"Last active terminal in tab '{page.get_title()}' exited. Closing tab."
                )
                self._close_tab_by_page(page)

            if self.terminal_manager.registry.get_active_terminal_count() == 0:
                self.logger.info(
                    "Last active terminal in the application has exited. Requesting quit."
                )
                GLib.idle_add(self._quit_application)

    def _close_tab_by_page(self, page: Adw.ViewStackPage):
        tab_to_remove = self._find_tab_for_page(page)
        if not tab_to_remove:
            self.update_all_tab_titles()
            return

        was_active = self.active_tab == tab_to_remove
        self._remove_tab_from_tracking(tab_to_remove)
        self.fm_handler.cleanup_file_manager_for_page(page)

        self.view_stack.remove(page.get_child())

        if was_active and self.tabs:
            self.set_active_tab(self.tabs[-1])
        elif not self.tabs:
            self.active_tab = None

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()

    def _find_tab_for_page(self, page: Adw.ViewStackPage) -> Optional[Gtk.Box]:
        """Find the tab widget associated with a page."""
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                return tab
        return None

    def _remove_tab_from_tracking(self, tab: Gtk.Box):
        """Remove tab from all tracking collections."""
        self.tab_bar_box.remove(tab)
        self.tabs.remove(tab)
        if tab in self.pages:
            del self.pages[tab]

    def _cleanup_file_manager_for_page(self, page: Adw.ViewStackPage):
        self.fm_handler.cleanup_file_manager_for_page(page)

    def get_all_active_terminals_in_page(
        self, page: Adw.ViewStackPage
    ) -> List[Vte.Terminal]:
        active_terminals = []
        all_terminals_in_page = self.get_all_terminals_in_page(page)
        for term in all_terminals_in_page:
            term_id = getattr(term, "terminal_id", None)
            if term_id:
                # If auto-reconnect is active, consider the terminal as active
                # even if it's in a failed/exited state
                if self.terminal_manager.is_auto_reconnect_active(term):
                    active_terminals.append(term)
                    continue

                info = self.terminal_manager.registry.get_terminal_info(term_id)
                if info and info.get("status") not in ["exited", "spawn_failed"]:
                    active_terminals.append(term)
        return active_terminals

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        page_content = self.view_stack.get_visible_child()

        # Check if last focused terminal is valid AND belongs to the current page
        if self._last_focused_terminal and (terminal := self._last_focused_terminal()):
            if terminal.get_realized():
                # Ancestry check to prevent cross-tab focus leakage
                if page_content and (
                    terminal.is_ancestor(page_content) or terminal == page_content
                ):
                    return terminal

        if not page_content:
            return None

        terminals = []
        self.pane_handler.find_terminals_recursive(page_content, terminals)
        return terminals[0] if terminals else None

    def get_all_terminals_in_page(self, page: Adw.ViewStackPage) -> List[Vte.Terminal]:
        terminals = []
        if root_widget := page.get_child():
            self.pane_handler.find_terminals_recursive(root_widget, terminals)
        return terminals

    def get_all_terminals_across_tabs(self) -> List[Vte.Terminal]:
        """Returns a list of all active Vte.Terminal widgets across all tabs."""
        all_terminals = []
        for page in self.pages.values():
            all_terminals.extend(self.get_all_terminals_in_page(page))
        return all_terminals

    def get_page_for_terminal(
        self, terminal: Vte.Terminal
    ) -> Optional[Adw.ViewStackPage]:
        return getattr(terminal, "ashy_parent_page", None)

    def update_titles_for_terminal(self, terminal, new_title: str, _osc7_info=None):
        """Updates the tab title and the specific pane title for a terminal."""
        page = self.get_page_for_terminal(terminal)
        if not page:
            return

        # Update the main tab title
        self.set_tab_title(page, new_title)

        # Update the specific pane's title
        pane = self.banner_handler.find_pane_for_terminal(page, terminal)
        if pane and hasattr(pane, "title_label"):
            pane.title_label.set_label(new_title)

    def _find_tab_button_for_page(self, page):
        """Finds the tab button associated with the given page."""
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                return tab
        return None

    def _build_display_title(self, tab_button, new_title: str) -> str:
        """Builds the display title based on tab type and new title."""
        base_title = tab_button._base_title

        if tab_button._is_local:
            return new_title

        if new_title.startswith(base_title + ":"):
            return new_title

        if new_title == base_title:
            return base_title

        return f"{base_title}: {new_title}"

    def _append_terminal_count(self, page, display_title: str) -> str:
        """Appends terminal count to title if multiple terminals in page."""
        terminal_count = len(self.get_all_terminals_in_page(page))
        if terminal_count > 1:
            return f"{display_title} ({terminal_count})"
        return display_title

    def set_tab_title(self, page: Adw.ViewStackPage, new_title: str) -> None:
        if not (page and new_title):
            return

        tab_button = self._find_tab_button_for_page(page)
        if not tab_button:
            return

        display_title = self._build_display_title(tab_button, new_title)
        display_title = self._append_terminal_count(page, display_title)

        tab_button.label_widget.set_text(display_title)
        page.set_title(display_title)
        a11y_label(tab_button, display_title)

        if hasattr(self.terminal_manager.parent_window, "_update_tab_layout"):
            self.terminal_manager.parent_window._update_tab_layout()

    def update_all_tab_titles(self) -> None:
        """Updates all tab titles based on the current state of the terminal."""
        for tab in self.tabs:
            page = self.pages.get(tab)
            if page:
                terminals = self.get_all_terminals_in_page(page)
                if terminals:
                    main_terminal = terminals[0]
                    uri = main_terminal.get_current_directory_uri()
                    if uri:
                        from urllib.parse import unquote, urlparse

                        path = unquote(urlparse(uri).path)
                        display_path = self.terminal_manager.osc7_tracker.parser._create_display_path(
                            path
                        )
                        self.set_tab_title(page, display_path)
                    else:
                        self.set_tab_title(page, tab._base_title)
                else:
                    self.set_tab_title(page, tab._base_title)

    def get_tab_count(self) -> int:
        return len(self.tabs)

    def _on_pane_focus_in(self, controller, terminal):
        self._last_focused_terminal = weakref.ref(terminal)
        page = self.get_page_for_terminal(terminal)
        if page:
            page._last_focused_in_page = weakref.ref(terminal)

    def _schedule_terminal_focus(self, terminal: Vte.Terminal) -> None:
        """Schedules a deferred focus call for the terminal, ensuring the UI is ready."""
        max_retries = 10
        retry_interval_ms = 50

        def focus_task(retries_left: int) -> bool:
            # Skip focus if a modal dialog is active (Wayland Freeze Fix)
            if self._has_active_modal_dialog():
                # self.logger.debug("Skipping terminal focus - modal dialog is active")
                # Return True to keep checking (wait for dialog to close)
                # Or return False to give up? Better to wait.
                # Actually, if we wait too long we might be fighting.
                # Let's just return SOURCE_REMOVE to stop fighting.
                return GLib.SOURCE_REMOVE

            if (
                terminal
                and terminal.get_realized()
                and terminal.is_visible()
                and terminal.get_can_focus()
            ):
                terminal.grab_focus()
                self.logger.debug(
                    f"Focus set on terminal {getattr(terminal, 'terminal_id', 'N/A')}"
                )
                return GLib.SOURCE_REMOVE

            if retries_left > 0:
                GLib.timeout_add(retry_interval_ms, focus_task, retries_left - 1)
            else:
                self.logger.warning(
                    f"Could not set focus on terminal {getattr(terminal, 'terminal_id', 'N/A')}: not ready after retries."
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(focus_task, max_retries)

    def _has_active_modal_dialog(self) -> bool:
        """Check if any modal dialog is currently active usage safe counter."""
        parent_window = self.terminal_manager.parent_window
        if not parent_window:
            return False

        # Use the safe manual counter if available (added to CommTerminalWindow)
        if hasattr(parent_window, "active_modals_count"):
            return parent_window.active_modals_count > 0

        return False

    def _find_terminal_pane_recursive(self, widget, terminal_to_find):
        return self.banner_handler.find_terminal_pane_recursive(widget, terminal_to_find)

    def _search_paned_children(self, widget, terminal_to_find):
        return self.banner_handler._search_paned_children(widget, terminal_to_find)

    def _search_single_child(self, widget, terminal_to_find):
        return self.banner_handler._search_single_child(widget, terminal_to_find)

    def _find_pane_for_terminal(self, page, terminal_to_find):
        return self.banner_handler.find_pane_for_terminal(page, terminal_to_find)

    def show_error_banner_for_terminal(self, terminal, session_name="", error_message="", session=None, is_auth_error=False, is_host_key_error=False):
        return self.banner_handler.show_error_banner_for_terminal(terminal, session_name, error_message, session, is_auth_error, is_host_key_error)

    def hide_error_banner_for_terminal(self, terminal):
        return self.banner_handler.hide_error_banner_for_terminal(terminal)

    def has_error_banner(self, terminal):
        return self.banner_handler.has_error_banner(terminal)

    def _handle_banner_action(self, action, terminal, session, terminal_id, config):
        self.banner_handler.handle_banner_action(action, terminal, session, terminal_id, config)

    def _open_session_edit_dialog(self, session, terminal, terminal_id):
        self.banner_handler._open_session_edit_dialog(session, terminal, terminal_id)

    def _fix_host_key_and_retry(self, session, terminal, terminal_id):
        self.banner_handler._fix_host_key_and_retry(session, terminal, terminal_id)

    def _find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        return self.pane_handler.find_pane_and_parent(terminal)

    def _find_terminals_recursive(self, widget, terminals_list):
        self.pane_handler.find_terminals_recursive(widget, terminals_list)

    def _quit_application(self) -> bool:
        if self.on_quit_application:
            self.on_quit_application()
        return False

    def _remove_pane_ui(self, pane_to_remove, parent_paned):
        self.pane_handler.remove_pane_ui(pane_to_remove, parent_paned)

    def _get_survivor_pane(self, pane_to_remove, parent_paned):
        return self.pane_handler._get_survivor_pane(pane_to_remove, parent_paned)

    def _get_first_terminal_in_widget(self, widget):
        return self.pane_handler.get_first_terminal_in_widget(widget)

    def _clear_paned_children(self, paned):
        self.pane_handler._clear_paned_children(paned)

    def _reparent_survivor(self, survivor_pane, parent_paned, grandparent):
        self.pane_handler._reparent_survivor(survivor_pane, parent_paned, grandparent)

    def _schedule_focus_restore(self, terminal):
        self.pane_handler.schedule_focus_restore(terminal)

    def close_pane(self, terminal: Vte.Terminal) -> None:
        self.pane_handler.close_pane(terminal)

    def _on_move_to_tab_callback(self, terminal: Vte.Terminal):
        self.pane_handler.on_move_to_tab_callback(terminal)

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self.pane_handler.split_horizontal(focused_terminal)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self.pane_handler.split_vertical(focused_terminal)

    def _set_paned_position_from_ratio(self, paned, ratio):
        return self.pane_handler.set_paned_position_from_ratio(paned, ratio)

    def _get_terminal_identifier_and_title(self, terminal):
        return self.pane_handler._get_terminal_identifier_and_title(terminal)

    def _create_split_terminal(self, identifier, pane_title):
        return self.pane_handler._create_split_terminal(identifier, pane_title)

    def _create_pane_for_split(self, terminal, title):
        return self.pane_handler._create_pane_for_split(terminal, title)

    def _prepare_pane_for_split(self, pane_to_replace, focused_terminal):
        return self.pane_handler._prepare_pane_for_split(pane_to_replace, focused_terminal)

    def _insert_split_paned(self, container, pane_to_replace, pane_being_split, new_pane, orientation, new_terminal):
        return self.pane_handler._insert_split_paned(container, pane_to_replace, pane_being_split, new_pane, orientation, new_terminal)

    def _split_terminal(self, focused_terminal, orientation):
        self.pane_handler._split_terminal(focused_terminal, orientation)

    def _show_split_type_dialog(self, focused_terminal, orientation, identifier, pane_title, page):
        self.pane_handler._show_split_type_dialog(focused_terminal, orientation, identifier, pane_title, page)

    def _show_ssh_session_picker(self, focused_terminal, orientation, page):
        self.pane_handler._show_ssh_session_picker(focused_terminal, orientation, page)

    def _perform_split(self, focused_terminal, orientation, identifier, pane_title, page):
        self.pane_handler._perform_split(focused_terminal, orientation, identifier, pane_title, page)

    def re_attach_detached_page(
        self,
        content: Gtk.Widget,
        title: str,
        session_type: str,
        file_manager_instance: Optional["FileManager"] = None,
    ) -> Adw.ViewStackPage:
        """Creates a new tab for a content widget that was detached from another window."""
        page_name = f"page_detached_{GLib.random_int()}"
        page = self.view_stack.add_titled(content, page_name, title)
        page.content_paned = content

        # Re-create a dummy session for the tab widget
        session = SessionItem(name=title, session_type=session_type)

        for terminal in self.get_all_terminals_in_page(page):
            terminal.ashy_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        if file_manager_instance:
            self.file_managers[page] = file_manager_instance
            file_manager_instance.reparent(
                self.terminal_manager.parent_window, self.terminal_manager
            )

        self.set_active_tab(tab_widget)
        if terminal := self.get_selected_terminal():
            self._schedule_terminal_focus(terminal)

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()
        return page

    def select_next_tab(self):
        """Selects the next tab in the list."""
        if not self.tabs or len(self.tabs) <= 1:
            return
        try:
            current_index = self.tabs.index(self.active_tab)
            next_index = (current_index + 1) % len(self.tabs)
            self.set_active_tab(self.tabs[next_index])
        except (ValueError, IndexError):
            if self.tabs:
                self.set_active_tab(self.tabs[0])

    def select_previous_tab(self):
        """Selects the previous tab in the list."""
        if not self.tabs or len(self.tabs) <= 1:
            return
        try:
            current_index = self.tabs.index(self.active_tab)
            prev_index = (current_index - 1 + len(self.tabs)) % len(self.tabs)
            self.set_active_tab(self.tabs[prev_index])
        except (ValueError, IndexError):
            if self.tabs:
                self.set_active_tab(self.tabs[0])

    def _unwrap_toolbar_view(self, root_widget: Gtk.Widget) -> Gtk.Widget:
        """Unwrap a ToolbarView to get the inner scrolled window."""
        if isinstance(root_widget, Adw.ToolbarView):
            scrolled_win = root_widget.get_content()
            if scrolled_win:
                root_widget.set_content(None)
                return scrolled_win
        return root_widget

    def _build_tab_content_paned(
        self, terminal_area_content: Gtk.Widget
    ) -> tuple[Adw.Bin, Gtk.Paned]:
        """Build the standard tab structure with terminal area and content paned."""
        terminal_area = Adw.Bin()
        terminal_area.set_child(terminal_area_content)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        content_paned.add_css_class("terminal-content-paned")
        content_paned.set_start_child(terminal_area)
        content_paned.set_resize_start_child(True)
        content_paned.set_shrink_start_child(False)
        content_paned.set_end_child(None)
        content_paned.set_resize_end_child(False)
        content_paned.set_shrink_end_child(True)

        return terminal_area, content_paned

    def _get_session_from_terminal(self, terminal: Vte.Terminal) -> "SessionItem":
        """Get or create a SessionItem from a terminal."""
        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            return identifier
        return SessionItem(name=str(identifier), session_type="local")

    def recreate_tab_from_structure(self, structure: dict):
        """Recreates a complete tab, including splits, from a saved structure."""
        if not structure:
            return

        root_widget = self._recreate_widget_from_node(structure)
        if not root_widget:
            self.logger.error("Failed to create root widget for tab restoration.")
            return

        terminal_area_content = self._unwrap_toolbar_view(root_widget)
        _, content_paned = self._build_tab_content_paned(terminal_area_content)

        terminals = []
        self._find_terminals_recursive(root_widget, terminals)

        if not terminals:
            self.logger.error("Restored tab contains no terminals.")
            return

        first_terminal = terminals[0]
        session = self._get_session_from_terminal(first_terminal)

        page_name = f"page_restored_{GLib.random_int()}"
        page = self.view_stack.add_titled(content_paned, page_name, session.name)
        page.content_paned = content_paned
        for term in terminals:
            term.ashy_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        self._schedule_terminal_focus(first_terminal)
        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

    def _recreate_widget_from_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Recursively builds a widget tree from a serialized node."""
        if not node or "type" not in node:
            return None

        node_type = node["type"]

        if node_type == "terminal":
            return self._recreate_terminal_node(node)
        elif node_type == "paned":
            return self._recreate_paned_node(node)

        return None

    def _recreate_terminal_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Recreate a terminal widget from a serialized node.

        Args:
            node: Serialized terminal node dictionary.

        Returns:
            Terminal pane widget or None if creation failed.
        """
        working_dir = node.get("working_dir")
        initial_command = (
            f'cd "{working_dir}"'
            if working_dir and node["session_type"] == "ssh"
            else None
        )
        title = node.get("session_name", "Terminal")
        session_type = node.get("session_type", "local")

        terminal = self._create_terminal_from_session(
            session_type,
            node.get("session_name", ""),
            title,
            working_dir,
            initial_command,
        )

        if not terminal:
            return None

        pane_widget = _create_terminal_pane(
            terminal,
            title,
            self.close_pane,
            self._on_move_to_tab_callback,
            self.terminal_manager.settings_manager,
        )
        sw = pane_widget.get_content()
        if isinstance(sw, Gtk.ScrolledWindow):
            self._replace_sw_scroll_controller(sw)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        return pane_widget

    def _create_terminal_from_session(
        self,
        session_type: str,
        session_name: str,
        title: str,
        working_dir: Optional[str],
        initial_command: Optional[str],
    ) -> Optional[Vte.Terminal]:
        """Create a terminal based on session type.

        Args:
            session_type: "ssh" or "local".
            session_name: Name of the session.
            title: Terminal title.
            working_dir: Working directory path.
            initial_command: Initial command for SSH sessions.

        Returns:
            Terminal widget or None if creation failed.
        """
        if session_type == "ssh":
            session = next(
                (
                    s
                    for s in self.terminal_manager.parent_window.session_store
                    if s.name == session_name
                ),
                None,
            )
            if session and session.is_ssh():
                return self.terminal_manager.create_ssh_terminal(
                    session, initial_command=initial_command
                )
            self.logger.warning(
                f"Could not find SSH session '{session_name}' to restore, or type mismatch."
            )
            return self.terminal_manager.create_local_terminal(
                title=f"Missing: {title}"
            )

        # Local session
        session = next(
            (
                s
                for s in self.terminal_manager.parent_window.session_store
                if s.name == session_name and s.is_local()
            ),
            None,
        )
        return self.terminal_manager.create_local_terminal(
            session=session, title=title, working_directory=working_dir
        )

    def _recreate_paned_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Recreate a paned widget from a serialized node.

        Args:
            node: Serialized paned node dictionary.

        Returns:
            Paned widget or None if creation failed.
        """
        orientation = (
            Gtk.Orientation.HORIZONTAL
            if node["orientation"] == "horizontal"
            else Gtk.Orientation.VERTICAL
        )
        paned = Gtk.Paned(orientation=orientation)

        child1 = self._recreate_widget_from_node(node["child1"])
        child2 = self._recreate_widget_from_node(node["child2"])

        if not child1 or not child2:
            self.logger.error("Failed to recreate children for a split pane.")
            if child1:
                self._find_and_remove_terminals(child1)
            if child2:
                self._find_and_remove_terminals(child2)
            return None

        paned.set_start_child(child1)
        paned.set_end_child(child2)

        ratio = node.get("position_ratio", 0.5)
        GLib.idle_add(self._set_paned_position_from_ratio, paned, ratio)

        return paned

    def _find_and_remove_terminals(self, widget: Gtk.Widget):
        """Finds all terminals in a widget tree and removes them."""
        terminals = []
        self._find_terminals_recursive(widget, terminals)
        for term in terminals:
            self.terminal_manager.remove_terminal(term)

    def close_all_tabs(self):
        """Closes all currently open tabs by simulating a click on each close button."""
        for tab_widget in self.tabs[:]:
            self._on_tab_close_button_clicked(None, tab_widget)
