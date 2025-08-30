# ashyterm/terminal/tabs.py

import threading
import weakref
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .manager import TerminalManager


def _create_terminal_pane(
    terminal: Vte.Terminal,
    title: str,
    on_close_callback: Callable[[Vte.Terminal], None],
    on_move_to_tab_callback: Callable[[Vte.Terminal], None],
) -> Adw.ToolbarView:
    """
    Creates a terminal pane using Adw.ToolbarView, the idiomatic standard
    for a main content area with a toolbar.
    """
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_css_class("terminal-pane")

    # Top title bar
    header_bar = Adw.HeaderBar()
    header_bar.set_show_end_title_buttons(False)
    header_bar.set_show_start_title_buttons(False)

    title_label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0)
    header_bar.set_title_widget(title_label)

    move_to_tab_button = Gtk.Button(
        icon_name="select-rectangular-symbolic", tooltip_text=_("Move to New Tab")
    )
    move_to_tab_button.connect("clicked", lambda _: on_move_to_tab_callback(terminal))

    close_button = Gtk.Button(
        icon_name="window-close-symbolic", tooltip_text=_("Close Pane")
    )
    close_button.connect("clicked", lambda _: on_close_callback(terminal))
    header_bar.pack_end(close_button)
    header_bar.pack_end(move_to_tab_button)
    toolbar_view.add_top_bar(header_bar)

    # Main content (the terminal)
    scrolled_window = Gtk.ScrolledWindow(child=terminal)
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled_window.set_vexpand(True)
    scrolled_window.set_hexpand(True)
    toolbar_view.set_content(scrolled_window)

    # Attach important widgets for later access
    toolbar_view.terminal = terminal
    toolbar_view.title_label = title_label

    return toolbar_view


class TabManager:
    def __init__(
        self,
        terminal_manager: TerminalManager,
        on_quit_callback: Callable[[], None],
        on_detach_tab_callback: Callable[[Adw.ViewStackPage], None],
        scrolled_tab_bar: Gtk.ScrolledWindow,
        on_tab_count_changed: Callable[[], None] = None,
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
        self.tab_bar_box.set_halign(Gtk.Align.CENTER)

        self.tabs: List[Gtk.Box] = []
        self.pages: weakref.WeakKeyDictionary[Gtk.Box, Adw.ViewStackPage] = (
            weakref.WeakKeyDictionary()
        )
        self.active_tab: Optional[Gtk.Box] = None

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_focused_terminal = None

        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )
        self.logger.info("Tab manager initialized with custom tab bar")

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

    def create_initial_tab_if_empty(
        self,
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> None:
        if self.get_tab_count() == 0:
            self.create_local_tab(
                title="Local",
                working_directory=working_directory,
                execute_command=execute_command,
                close_after_execute=close_after_execute,
            )

    def create_local_tab(
        self,
        title: str = "Local",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Optional[Vte.Terminal]:
        terminal = self.terminal_manager.create_local_terminal(
            title=title,
            working_directory=working_directory,
            execute_command=execute_command,
            close_after_execute=close_after_execute,
        )
        if terminal:
            self._create_tab_for_terminal(terminal, title, "computer-symbolic")
        return terminal

    def create_ssh_tab(self, session: SessionItem) -> Optional[Vte.Terminal]:
        terminal = self.terminal_manager.create_ssh_terminal(session)
        if terminal:
            self._create_tab_for_terminal(
                terminal, session.name, "network-server-symbolic"
            )
        return terminal

    def _scroll_to_widget(self, widget: Gtk.Widget) -> bool:
        """Scrolls the tab bar to make the given widget visible."""
        hadjustment = self.scrolled_tab_bar.get_hadjustment()
        if not hadjustment:
            return False

        coords = widget.translate_coordinates(self.scrolled_tab_bar, 0, 0)
        if coords is None:
            return False

        widget_x, _ = coords
        widget_width = widget.get_width()
        viewport_width = self.scrolled_tab_bar.get_width()

        current_scroll_value = hadjustment.get_value()

        if widget_x < 0:
            hadjustment.set_value(current_scroll_value + widget_x)
        elif widget_x + widget_width > viewport_width:
            hadjustment.set_value(
                current_scroll_value + (widget_x + widget_width - viewport_width)
            )

        return False

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, title: str, icon_name: str
    ) -> None:
        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        root_container = Adw.Bin()
        root_container.set_child(scrolled_window)

        page_name = f"page_{terminal.terminal_id}"
        page = self.view_stack.add_titled(root_container, page_name, title)
        terminal.ashy_parent_page = page

        tab_widget = self._create_tab_widget(title, icon_name, page)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        self._schedule_terminal_focus(terminal)
        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

        GLib.idle_add(self._scroll_to_widget, tab_widget)

    def _create_tab_widget(
        self, title: str, icon_name: str, page: Adw.ViewStackPage
    ) -> Gtk.Box:
        tab_widget = Gtk.Box(spacing=6)
        tab_widget.add_css_class("custom-tab-button")
        tab_widget.add_css_class("pill")

        if icon_name != "computer-symbolic":
            icon = Gtk.Image.new_from_icon_name(icon_name)
            tab_widget.append(icon)

        label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.START, xalign=1.0)
        label.set_width_chars(8)
        tab_widget.append(label)

        close_button = Gtk.Button(
            icon_name="window-close-symbolic", css_classes=["circular", "flat"]
        )
        tab_widget.append(close_button)

        left_click = Gtk.GestureClick.new()
        left_click.connect("pressed", self._on_tab_clicked, tab_widget)
        tab_widget.add_controller(left_click)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_tab_right_click, tab_widget)
        tab_widget.add_controller(right_click)

        close_button.connect("clicked", self._on_tab_close_button_clicked, tab_widget)

        tab_widget.label_widget = label
        tab_widget._base_title = title
        tab_widget._icon_name = icon_name
        tab_widget._is_local = icon_name == "computer-symbolic"

        return tab_widget

    def _on_tab_clicked(self, _gesture, _n_press, _x, _y, tab_widget):
        self.set_active_tab(tab_widget)

    def _on_tab_right_click(self, _gesture, _n_press, _x, _y, tab_widget):
        menu = Gio.Menu()
        menu.append(_("Detach Tab"), "win.detach-tab")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(tab_widget)

        page = self.pages.get(tab_widget)
        if page:
            action_group = Gio.SimpleActionGroup()
            action = Gio.SimpleAction.new("detach-tab", None)

            action.connect(
                "activate", lambda a, _, pg=page: self._request_detach_tab(pg)
            )
            action_group.add_action(action)
            popover.insert_action_group("win", action_group)

        popover.popup()

    def _request_detach_tab(self, page: Adw.ViewStackPage):
        if self.on_detach_tab_requested:
            self.on_detach_tab_requested(page)

    def set_active_tab(self, tab_to_activate: Gtk.Box):
        if self.active_tab == tab_to_activate:
            return

        if self.active_tab:
            self.active_tab.remove_css_class("active")

        self.active_tab = tab_to_activate
        self.active_tab.add_css_class("active")

        page = self.pages.get(self.active_tab)
        if page:
            self.view_stack.set_visible_child(page.get_child())
            terminal = self.get_selected_terminal()
            if terminal:
                self._schedule_terminal_focus(terminal)

    def _on_tab_close_button_clicked(self, button: Gtk.Button, tab_widget: Gtk.Box):
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

        for terminal in terminals_in_page:
            self.terminal_manager.remove_terminal(terminal, force_kill_group=True)

    def _on_terminal_process_exited(
        self, terminal: Vte.Terminal, child_status: int, identifier
    ):
        with self._cleanup_lock:
            page = self.get_page_for_terminal(terminal)
            terminal_id = getattr(terminal, "terminal_id", "N/A")

            pane_to_remove, parent_paned = self._find_pane_and_parent(terminal)
            if parent_paned:
                self._remove_pane_ui(pane_to_remove, parent_paned)

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
        tab_to_remove = None
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                tab_to_remove = tab
                break

        if tab_to_remove:
            was_active = self.active_tab == tab_to_remove

            self.tab_bar_box.remove(tab_to_remove)
            self.tabs.remove(tab_to_remove)
            if tab_to_remove in self.pages:
                del self.pages[tab_to_remove]

            self.view_stack.remove(page.get_child())

            if was_active and self.tabs:
                self.set_active_tab(self.tabs[-1])
            elif not self.tabs:
                self.active_tab = None

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()

    def get_all_active_terminals_in_page(
        self, page: Adw.ViewStackPage
    ) -> List[Vte.Terminal]:
        active_terminals = []
        all_terminals_in_page = self.get_all_terminals_in_page(page)
        for term in all_terminals_in_page:
            term_id = getattr(term, "terminal_id", None)
            if term_id:
                info = self.terminal_manager.registry.get_terminal_info(term_id)
                if info and info.get("status") not in ["exited", "spawn_failed"]:
                    active_terminals.append(term)
        return active_terminals

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        if self._last_focused_terminal and (terminal := self._last_focused_terminal()):
            if terminal.get_realized():
                return terminal

        page_content = self.view_stack.get_visible_child()
        if not page_content:
            return None

        terminals = []
        self._find_terminals_recursive(page_content, terminals)
        return terminals[0] if terminals else None

    def get_all_terminals_in_page(self, page: Adw.ViewStackPage) -> List[Vte.Terminal]:
        terminals = []
        if root_widget := page.get_child():
            self._find_terminals_recursive(root_widget, terminals)
        return terminals

    def get_page_for_terminal(
        self, terminal: Vte.Terminal
    ) -> Optional[Adw.ViewStackPage]:
        return getattr(terminal, "ashy_parent_page", None)

    def set_tab_title(self, page: Adw.ViewStackPage, new_title: str) -> None:
        if not (page and new_title):
            return

        tab_button = None
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                tab_button = tab
                break

        if tab_button:
            base_title = tab_button._base_title

            if tab_button._is_local:
                display_title = new_title
            else:
                if new_title.startswith(base_title + ":"):
                    display_title = new_title
                else:
                    display_title = (
                        base_title
                        if new_title == base_title
                        else f"{base_title}: {new_title}"
                    )

            terminal_count = len(self.get_all_terminals_in_page(page))
            if terminal_count > 1:
                display_title = f"{display_title} ({terminal_count})"

            tab_button.label_widget.set_text(display_title)
            page.set_title(display_title)

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

    def _schedule_terminal_focus(self, terminal: Vte.Terminal) -> None:
        def focus_terminal():
            if terminal and terminal.get_realized():
                terminal.grab_focus()
                return False
            return True

        GLib.timeout_add(100, focus_terminal)

    def _find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        """
        Walks up the widget tree from a terminal to find its direct pane
        (the widget that should be replaced in a split) and that pane's
        parent container.
        """
        widget = terminal
        while widget:
            parent = widget.get_parent()
            if isinstance(parent, (Gtk.Paned, Adw.Bin)):
                return widget, parent
            widget = parent
        return None, None

    def _find_terminals_recursive(
        self, widget, terminals_list: List[Vte.Terminal]
    ) -> None:
        """Recursively find all Vte.Terminal widgets within a container."""
        if isinstance(widget, Adw.ToolbarView):
            if hasattr(widget, "terminal") and isinstance(
                widget.terminal, Vte.Terminal
            ):
                terminals_list.append(widget.terminal)
            return

        if isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminals_list.append(widget.get_child())
            return
        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_terminals_recursive(start_child, terminals_list)
            if end_child := widget.get_end_child():
                self._find_terminals_recursive(end_child, terminals_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_terminals_recursive(child, terminals_list)

    def _quit_application(self) -> bool:
        if self.on_quit_application:
            self.on_quit_application()
        return False

    def _remove_pane_ui(self, pane_to_remove, parent_paned):
        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning(
                f"Attempted to remove pane from a non-paned container: {type(parent_paned)}"
            )
            return

        is_start_child = parent_paned.get_start_child() == pane_to_remove
        survivor_pane = (
            parent_paned.get_end_child()
            if is_start_child
            else parent_paned.get_start_child()
        )
        if not survivor_pane:
            return

        grandparent = parent_paned.get_parent()
        if not grandparent:
            return

        parent_paned.set_start_child(None)
        parent_paned.set_end_child(None)

        if isinstance(grandparent, Gtk.Paned):
            is_grandparent_start = grandparent.get_start_child() == parent_paned
            if is_grandparent_start:
                grandparent.set_start_child(survivor_pane)
            else:
                grandparent.set_end_child(survivor_pane)
        elif hasattr(grandparent, "set_child"):
            grandparent.set_child(survivor_pane)

        is_last_split = not isinstance(grandparent, Gtk.Paned)
        if is_last_split and isinstance(survivor_pane, Adw.ToolbarView):
            scrolled_win_child = survivor_pane.get_content()
            if hasattr(grandparent, "set_child"):
                survivor_pane.set_content(None)
                grandparent.set_child(scrolled_win_child)

        GLib.idle_add(self.update_all_tab_titles)

    def close_pane(self, terminal: Vte.Terminal) -> None:
        """Close a single pane within a tab."""
        self.terminal_manager.remove_terminal(terminal)

    def _on_move_to_tab_callback(self, terminal: Vte.Terminal):
        """Callback to move a terminal from a split pane to a new tab."""
        self.logger.info(f"Request to move terminal {terminal.terminal_id} to new tab.")
        pane_to_remove, parent_paned = self._find_pane_and_parent(terminal)

        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning("Attempted to move a pane that is not in a split.")
            if hasattr(self.terminal_manager.parent_window, "toast_overlay"):
                toast = Adw.Toast(title=_("This is the only pane in the tab."))
                self.terminal_manager.parent_window.toast_overlay.add_toast(toast)
            return

        current_parent = terminal.get_parent()
        if current_parent and hasattr(current_parent, "set_child"):
            current_parent.set_child(None)

        self._remove_pane_ui(pane_to_remove, parent_paned)

        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        title = "Terminal"
        icon_name = "computer-symbolic"
        if isinstance(identifier, SessionItem):
            title = identifier.name
            icon_name = (
                "network-server-symbolic"
                if identifier.is_ssh()
                else "computer-symbolic"
            )
        elif isinstance(identifier, str):
            title = identifier

        self._create_tab_for_terminal(terminal, title, icon_name)
        self.logger.info(f"Terminal {terminal_id} successfully moved to a new tab.")

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _set_paned_position(self, paned: Gtk.Paned) -> bool:
        alloc = paned.get_allocation()
        total_size = (
            alloc.width
            if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
            else alloc.height
        )
        if total_size > 0:
            paned.set_position(total_size // 2)
        return False

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        with self._creation_lock:
            page = self.get_page_for_terminal(focused_terminal)
            if not page:
                self.logger.error("Cannot split: could not find parent page.")
                return

            new_terminal = None
            new_pane_title = "Terminal"

            restore_node = getattr(focused_terminal, "_node_to_restore_child2", None)

            if restore_node:
                if restore_node["session_type"] == "ssh":
                    session = next(
                        (
                            s
                            for s in self.terminal_manager.parent_window.session_store
                            if s.name == restore_node["session_name"]
                        ),
                        None,
                    )
                    if session:
                        new_terminal = self.terminal_manager.create_ssh_terminal(
                            session
                        )
                        new_pane_title = session.name
                else:  # local
                    new_terminal = self.terminal_manager.create_local_terminal(
                        title=restore_node["session_name"],
                        working_directory=restore_node.get("working_dir"),
                    )
                    new_pane_title = restore_node["session_name"]
            else:  # Standard split behavior
                terminal_id = getattr(focused_terminal, "terminal_id", None)
                info = self.terminal_manager.registry.get_terminal_info(terminal_id)
                identifier = info.get("identifier") if info else "Local"

                if isinstance(identifier, SessionItem):
                    new_pane_title = identifier.name
                    new_terminal = (
                        self.terminal_manager.create_ssh_terminal(identifier)
                        if identifier.is_ssh()
                        else self.terminal_manager.create_local_terminal(
                            identifier.name
                        )
                    )
                else:
                    new_pane_title = "Local"
                    new_terminal = self.terminal_manager.create_local_terminal(
                        new_pane_title
                    )

            if not new_terminal:
                self.logger.error("Failed to create new terminal for split.")
                return

            new_terminal.ashy_parent_page = page
            new_pane = _create_terminal_pane(
                new_terminal,
                new_pane_title,
                self.close_pane,
                self._on_move_to_tab_callback,
            )
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, new_terminal)
            new_terminal.add_controller(focus_controller)

            pane_to_replace, container = self._find_pane_and_parent(focused_terminal)
            if not pane_to_replace:
                self.logger.error("Could not find the pane to replace for splitting.")
                self.terminal_manager.remove_terminal(new_terminal)
                return

            if isinstance(pane_to_replace, Gtk.ScrolledWindow):
                uri = focused_terminal.get_current_directory_uri()
                if uri:
                    from urllib.parse import unquote, urlparse

                    path = unquote(urlparse(uri).path)
                    title = (
                        self.terminal_manager.osc7_tracker.parser._create_display_path(
                            path
                        )
                    )
                else:
                    title = "Terminal"

                pane_to_replace.set_child(None)
                pane_being_split = _create_terminal_pane(
                    focused_terminal,
                    title,
                    self.close_pane,
                    self._on_move_to_tab_callback,
                )
            else:
                pane_being_split = pane_to_replace

            is_start_child = False
            if isinstance(container, Gtk.Paned):
                is_start_child = container.get_start_child() == pane_to_replace
                if is_start_child:
                    container.set_start_child(None)
                else:
                    container.set_end_child(None)
            elif isinstance(container, Adw.Bin):
                container.set_child(None)

            new_split_paned = Gtk.Paned(orientation=orientation)
            new_split_paned.set_start_child(pane_being_split)
            new_split_paned.set_end_child(new_pane)

            if isinstance(container, Gtk.Paned):
                if is_start_child:
                    container.set_start_child(new_split_paned)
                else:
                    container.set_end_child(new_split_paned)
            elif isinstance(container, Adw.Bin):
                container.set_child(new_split_paned)
            else:
                self.logger.error(
                    f"Cannot re-parent split: unknown container type {type(container)}"
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            GLib.idle_add(lambda: self._set_paned_position(new_split_paned))
            self._schedule_terminal_focus(new_terminal)
            self.update_all_tab_titles()

    def re_attach_detached_page(
        self, content: Gtk.Widget, title: str, icon_name: str
    ) -> Adw.ViewStackPage:
        """Creates a new tab for a content widget that was detached from another window."""
        page_name = f"page_detached_{GLib.random_int()}"
        page = self.view_stack.add_titled(content, page_name, title)

        for terminal in self.get_all_terminals_in_page(page):
            terminal.ashy_parent_page = page

        tab_widget = self._create_tab_widget(title, icon_name, page)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        if terminal := self.get_selected_terminal():
            self._schedule_terminal_focus(terminal)

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()
        return page

    def close_active_tab(self):
        """A helper to forcefully close the currently active tab."""
        if self.active_tab:
            self._on_tab_close_button_clicked(None, self.active_tab)

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
