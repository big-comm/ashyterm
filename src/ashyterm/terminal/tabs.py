# ashyterm/terminal/tabs.py

import re
import threading
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional

import gi
from typing import Any

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango, Vte

from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager as SettingsManagerType
from ..utils.accessibility import set_label as a11y_label
from ..utils.icons import icon_button
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .banner_manager import BannerManager
from .fm_integration import FileManagerIntegration
from .manager import TerminalManager
from .pane_manager import PaneManager
from .scroll_handler import ScrollHandler
from .tab_attention import clear_tab_attention, mark_tab_attention
from .tab_close import (
    any_terminal_has_foreground_process as _any_terminal_has_foreground_process_impl,
    build_close_confirmation_dialog as _build_close_confirmation_dialog_impl,
    has_stable_running_process as _has_stable_running_process_impl,
    process_terminals_for_close as _process_terminals_for_close_impl,
)
from .tab_context_menu import show_tab_context_menu as _show_tab_context_menu
from .tab_groups import TabGroupManager
from .tab_groups_controller import TabGroupsController
from .tab_move_controller import TabMoveController
from .tab_restore_controller import TabRestoreController
from .terminal_body import (
    create_terminal_body,
    get_terminal_scroll_host,
    get_terminal_scrolled_window,
)
from .tab_titles import (
    append_terminal_count as _append_terminal_count_impl,
    build_display_title as _build_display_title_impl,
)
from .tab_widget import (
    apply_tab_color as _apply_tab_color_impl,
    contrasting_text_for_rgba as _contrasting_text_for_rgba_impl,
    create_tab_widget as _create_tab_widget_impl,
    generate_unique_sftp_name as _generate_unique_sftp_name_impl,
)

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
    terminal_body: Optional[Gtk.Widget] = None,
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

    # Main content. The dedicated host limits scroll capture to the terminal body.
    body = terminal_body or create_terminal_body(terminal)
    toolbar_view.set_content(body)

    # Attach important widgets for later access
    toolbar_view.terminal = terminal
    toolbar_view.title_label = title_label
    toolbar_view.move_button = move_to_tab_button
    toolbar_view.close_button = close_button
    toolbar_view.header_box = header_box
    toolbar_view.terminal_body = body
    toolbar_view.scrolled_window = get_terminal_scrolled_window(body)
    toolbar_view.scroll_host = get_terminal_scroll_host(body)

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
        # Tab-move mode (drag-reorder) state lives on the controller;
        # the backing properties below keep legacy internal references
        # working until every call site is migrated.
        self.move_controller = TabMoveController(self)

        # Set up tab bar for receiving move drop events
        self._setup_tab_bar_move_handlers()

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_focused_terminal = None

        # Tab groups — data model + UI controller
        self.group_manager = TabGroupManager()
        self.group_controller = TabGroupsController(self)

        # Delegates
        self.scroll_handler = ScrollHandler(self)
        self.fm_handler = FileManagerIntegration(self)
        self.pane_handler = PaneManager(self)
        self.banner_handler = BannerManager(self)
        self.restore_controller = TabRestoreController(self)

        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )
        self.logger.info("Tab manager initialized with custom tab bar")

    def _find_panes_recursive(self, widget, panes_list: List[Adw.ToolbarView]):
        """Recursively find all Adw.ToolbarView panes within a container."""
        self.pane_handler.find_panes_recursive(widget, panes_list)

    def _setup_tab_bar_move_handlers(self):
        """Set up event handlers on the tab bar for tab move operations."""
        # We only need to handle motion on individual tabs, which is done via
        # controllers added in _create_tab_widget. No tab bar level handlers needed
        # since we handle everything at the tab level.

    # ── Tab-move delegators ─────────────────────────────────
    # Move-mode state + logic lives on ``self.move_controller``.

    def _update_move_highlight(self, target_tab: Gtk.Box, side: str):
        self.move_controller.update_highlight(target_tab, side)

    def _clear_tab_drop_highlights(self):
        self.move_controller.clear_highlights()

    def _perform_tab_move(self):
        self.move_controller.perform()

    @property
    def _tab_being_moved(self) -> Optional[Gtk.Box]:
        return self.move_controller.moving_tab

    @_tab_being_moved.setter
    def _tab_being_moved(self, value: Optional[Gtk.Box]) -> None:
        # Legacy writes from inside tabs.py still need to flow into the
        # controller; overall state lifecycle is owned there.
        self.move_controller._moving = value

    @property
    def _drop_target_tab(self) -> Optional[Gtk.Box]:
        return self.move_controller.drop_target

    @_drop_target_tab.setter
    def _drop_target_tab(self, value: Optional[Gtk.Box]) -> None:
        self.move_controller._drop_target = value

    @property
    def _drop_side(self) -> str:
        return self.move_controller.drop_side

    @_drop_side.setter
    def _drop_side(self, value: str) -> None:
        self.move_controller._drop_side = value

    def cancel_tab_move_if_active(self) -> bool:
        """Cancel the tab move operation if one is active. Returns True if cancelled."""
        if self.group_controller.is_moving_group():
            self.group_controller.cancel_move()
            return True
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

    # ── Tab group helpers ────────────────────────────────────

    def get_tab_id(self, tab_widget: Gtk.Box) -> str:
        """Return a stable string identifier for a tab widget."""
        return str(id(tab_widget))

    def _get_tab_by_id(self, tab_id: str) -> Optional[Gtk.Box]:
        """Find a tab widget by its string id."""
        for tab in self.tabs:
            if self.get_tab_id(tab) == tab_id:
                return tab
        return None

    # ── Delegators to TabGroupsController ────────────────────
    # Public API kept so external callers (window_actions, state
    # restore, context-menu handlers) don't need to know about the
    # split.

    def create_group_from_tabs(
        self, tab_widgets: List[Gtk.Box], name: str = ""
    ) -> None:
        self.group_controller.create_group_from_tabs(tab_widgets, name)

    def create_group_from_active_tab(self) -> None:
        self.group_controller.create_group_from_active_tab()

    def ungroup_active_tab(self) -> None:
        self.group_controller.ungroup_active_tab()

    def _ensure_group_tabs_contiguous(self, group_id: str) -> None:
        self.group_controller.ensure_contiguous(group_id)

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
            resolved_dir = effective_working_dir or str(Path.home())
            self._create_tab_for_terminal(terminal, session, working_directory=resolved_dir)
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
        existing_names = (
            getattr(tab, "session_item", None) and tab.session_item.name
            for tab in self.tabs
        )
        return _generate_unique_sftp_name_impl(
            base_session_name,
            existing_names=(name for name in existing_names if name),
        )

    # Kept because tab_restore_controller reaches into it when
    # recreating saved layouts.
    def _replace_sw_scroll_controller(
        self, sw: Gtk.ScrolledWindow, host: Optional[Gtk.Widget] = None
    ) -> None:
        scroll_host = host or get_terminal_scroll_host(sw)
        if scroll_host:
            self.scroll_handler.bind_scroll_controller(scroll_host, sw)

    def _on_terminal_bell(self, terminal: Vte.Terminal) -> None:
        """Keep a background tab highlighted after a bell/BEL is received."""
        page = getattr(terminal, "ashy_parent_page", None)
        if not page:
            return

        tab_widget = self._find_tab_for_page(page)
        if not tab_widget or tab_widget == self.active_tab:
            return

        label = getattr(tab_widget, "label_widget", None)
        if not label:
            return

        mark_tab_attention(tab_widget)

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, session: SessionItem,
        working_directory: Optional[str] = None,
    ) -> None:
        terminal.connect("contents-changed", self.scroll_handler.on_terminal_contents_changed)
        terminal.connect("bell", self._on_terminal_bell)

        terminal_body = create_terminal_body(terminal)
        scrolled_window = get_terminal_scrolled_window(terminal_body)
        scroll_host = get_terminal_scroll_host(terminal_body)
        if scrolled_window and scroll_host:
            self.scroll_handler.bind_scroll_controller(scroll_host, scrolled_window)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        terminal_area = Adw.Bin()
        terminal_area.set_child(terminal_body)

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

        # For local tabs, set initial title to the working directory path
        if session.is_local() and working_directory:
            display_path = self.terminal_manager.osc7_tracker.parser._create_display_path(
                working_directory
            )
            tab_widget._base_title = display_path
            tab_widget.label_widget.set_text(display_path)
            page.set_title(display_path)

        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

        GLib.idle_add(self.scroll_handler.scroll_to_widget, tab_widget)

    def _get_contrasting_text_color(self, bg_color_str: str) -> str:
        return _contrasting_text_for_rgba_impl(bg_color_str)

    def _apply_tab_color(self, widget: Gtk.Widget, color_string: Optional[str]):
        _apply_tab_color_impl(widget, color_string)

    def _create_tab_widget(
        self, _page: Adw.ViewStackPage, session: SessionItem
    ) -> Gtk.Box:
        return _create_tab_widget_impl(self, _page, session)

    def _on_tab_motion(self, controller, x, y, tab_widget):
        """Handle mouse motion over a tab during move mode."""
        moving_group = self.group_controller.is_moving_group()
        if self._tab_being_moved is None and not moving_group:
            return
        if tab_widget == self._tab_being_moved:
            return
        # Skip tabs belonging to the group being moved
        if moving_group and self.group_controller.is_tab_in_moving_group(
            self.get_tab_id(tab_widget)
        ):
            return

        # Determine which half of the tab we're over
        tab_width = tab_widget.get_width()
        side = "left" if x < tab_width / 2 else "right"

        # Update highlight
        self._update_move_highlight(tab_widget, side)

    def _on_tab_leave(self, controller, tab_widget):
        """Handle mouse leaving a tab during move mode."""
        if (
            self._tab_being_moved is None
            and not self.group_controller.is_moving_group()
        ):
            return
        # Clear highlight when leaving a tab
        self._clear_tab_drop_highlights()

    def _on_tab_clicked(self, gesture, _n_press, x, _y, tab_widget):
        # If we're in group move mode, handle the drop
        if self.group_controller.is_moving_group():
            tab_id = self.get_tab_id(tab_widget)
            if not self.group_controller.is_tab_in_moving_group(tab_id):
                tab_width = tab_widget.get_width()
                side = "left" if x < tab_width / 2 else "right"
                self.group_controller.perform_move(tab_widget, side)
            self.group_controller.cancel_move()
            return
        # If we're in tab move mode, handle the drop
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
        # Cancel any in-flight move so the menu interaction doesn't
        # compete with the drop-target highlight state.
        if self._tab_being_moved is not None:
            self._cancel_tab_move()
        if self.group_controller.is_moving_group():
            self.group_controller.cancel_move()

        _show_tab_context_menu(self, tab_widget, x, y)

    def _remove_tab_from_group_action(self, tab_widget: Gtk.Box) -> None:
        self.group_controller.remove_tab_from_group_action(tab_widget)

    def _add_tab_to_group_action(self, tab_widget: Gtk.Box, group_id: str) -> None:
        self.group_controller.add_tab_to_group_action(tab_widget, group_id)

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
        self.move_controller.start(tab_widget)

    def _cancel_tab_move(self) -> None:
        self.move_controller.cancel()

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
        """Rebuilds tab bar based on self.tabs order. Groups are interspersed."""
        # Remove ALL children from tab_bar_box (tabs + old group chips)
        child = self.tab_bar_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.tab_bar_box.remove(child)
            child = next_child

        # Track which groups already have their chip placed
        groups_with_chip: set[str] = set()

        for tab in self.tabs:
            tab_id = self.get_tab_id(tab)
            group = self.group_manager.get_group_for_tab(tab_id)

            if group:
                # Insert chip before the first tab of this group in visual order
                if group.id not in groups_with_chip:
                    chip = self.group_controller.build_chip(group)
                    self.tab_bar_box.append(chip)
                    groups_with_chip.add(group.id)

                tab.add_css_class("in-group")
                self.group_controller.apply_border_color(tab, group.color)
                if group.is_collapsed:
                    tab.set_visible(False)
                else:
                    tab.set_visible(True)
                self.tab_bar_box.append(tab)
            else:
                tab.remove_css_class("in-group")
                # Drop the cached border-color provider so an old group
                # color doesn't bleed through after ungrouping.
                previous = getattr(tab, "_group_border_provider", None)
                if previous is not None:
                    tab.get_style_context().remove_provider(previous)
                    tab._group_border_provider = None
                tab.set_visible(True)
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

    def set_active_tab(self, tab_to_activate: Gtk.Box) -> None:
        if self.active_tab == tab_to_activate:
            return

        # Auto-expand collapsed group if the target tab is hidden
        tab_id = self.get_tab_id(tab_to_activate)
        group = self.group_manager.get_group_for_tab(tab_id)
        if group and group.is_collapsed:
            self.group_manager.toggle_collapsed(group.id)
            self._rebuild_tab_bar_order()

        self._handle_previous_tab_focus()

        if self.active_tab:
            self.active_tab.remove_css_class("active")

        self.active_tab = tab_to_activate
        self.active_tab.add_css_class("active")
        clear_tab_attention(self.active_tab)

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

    def toggle_file_manager_for_active_tab(self, is_active: bool) -> None:
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
        return _any_terminal_has_foreground_process_impl(
            terminals, terminal_manager=self.terminal_manager
        )

    def _confirm_close_tab(self, page: Adw.ViewStackPage, terminals: list) -> None:
        """Show confirmation dialog before closing tab with active process."""
        def on_response(_dialog, response: str) -> None:
            self._on_close_confirm_response(_dialog, response, page, terminals)

        dialog = _build_close_confirmation_dialog_impl(
            parent=self.view_stack.get_root(), on_response=on_response
        )
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
        return _process_terminals_for_close_impl(
            terminals, terminal_manager=self.terminal_manager
        )

    def _has_stable_running_process(self, info: Optional[dict]) -> bool:
        return _has_stable_running_process_impl(info)

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

            # Only touch pane layout when we're actually in a split.
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
        clear_tab_attention(tab)
        # Notify group manager
        self.group_manager.on_tab_removed(self.get_tab_id(tab))
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

    def update_titles_for_terminal(self, terminal: Any, new_title: str, _osc7_info: Any=None) -> None:
        """Updates the tab title and the specific pane title for a terminal."""
        page = self.get_page_for_terminal(terminal)
        if not page:
            return

        # Full path for tooltip
        full_path = _osc7_info.path if _osc7_info else None

        # Update the main tab title
        self.set_tab_title(page, new_title, tooltip=full_path)

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
        return _build_display_title_impl(
            base_title=tab_button._base_title,
            new_title=new_title,
            is_local=tab_button._is_local,
        )

    def _append_terminal_count(self, page, display_title: str) -> str:
        return _append_terminal_count_impl(
            display_title, len(self.get_all_terminals_in_page(page))
        )

    def set_tab_title(self, page: Adw.ViewStackPage, new_title: str, tooltip: Optional[str] = None) -> None:
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

        if tooltip:
            tab_button.set_tooltip_text(tooltip)

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

    def show_error_banner_for_terminal(self, terminal: Any, session_name: str="", error_message: str="", session: Any=None, is_auth_error: bool=False, is_host_key_error: bool=False) -> Any:
        return self.banner_handler.show_error_banner_for_terminal(terminal, session_name, error_message, session, is_auth_error, is_host_key_error)

    def hide_error_banner_for_terminal(self, terminal: Any) -> Any:
        return self.banner_handler.hide_error_banner_for_terminal(terminal)

    def has_error_banner(self, terminal: Any) -> Any:
        return self.banner_handler.has_error_banner(terminal)

    def _handle_banner_action(self, action, terminal, session, terminal_id, config):
        self.banner_handler.handle_banner_action(action, terminal, session, terminal_id, config)

    def _open_session_edit_dialog(self, session, terminal, terminal_id):
        self.banner_handler._open_session_edit_dialog(session, terminal, terminal_id)

    def _fix_host_key_and_retry(self, session, terminal, terminal_id):
        self.banner_handler._fix_host_key_and_retry(session, terminal, terminal_id)

    def _quit_application(self) -> bool:
        if self.on_quit_application:
            self.on_quit_application()
        return False

    # Public split/close API — preserved because window.py and
    # window_actions.py call these directly on the TabManager.
    def close_pane(self, terminal: Vte.Terminal) -> None:
        self.pane_handler.close_pane(terminal)

    def _on_move_to_tab_callback(self, terminal: Vte.Terminal):
        self.pane_handler.on_move_to_tab_callback(terminal)

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self.pane_handler.split_horizontal(focused_terminal)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self.pane_handler.split_vertical(focused_terminal)

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

    def select_next_tab(self) -> None:
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

    def select_previous_tab(self) -> None:
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

    # ── Delegators to TabRestoreController ───────────────────

    def recreate_tab_from_structure(self, structure: dict) -> None:
        self.restore_controller.recreate_tab_from_structure(structure)

    def close_all_tabs(self) -> None:
        self.restore_controller.close_all_tabs()
