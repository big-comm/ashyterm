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


class TerminalPaneWithTitleBar(Gtk.Box):
    """A terminal pane with an integrated, Adwaita-native title bar."""

    def __init__(self, terminal: Vte.Terminal, title: str = "Terminal"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.terminal = terminal
        self._title = title
        self.title_bar = self._create_title_bar()
        self.append(self.title_bar)

        current_parent = terminal.get_parent()
        if isinstance(current_parent, Gtk.ScrolledWindow):
            self.scrolled_window = current_parent
            if grandparent := current_parent.get_parent():
                grandparent.set_child(None)
        else:
            self.scrolled_window = Gtk.ScrolledWindow()
            self.scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            if current_parent:
                current_parent.set_child(None)

        if terminal.get_parent() != self.scrolled_window:
            self.scrolled_window.set_child(terminal)

        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        self.append(self.scrolled_window)
        self.on_close_requested: Optional[Callable[[Vte.Terminal], None]] = None

    def _create_title_bar(self) -> Adw.HeaderBar:
        title_bar = Adw.HeaderBar()
        title_bar.set_show_end_title_buttons(False)
        title_bar.set_show_start_title_buttons(False)
        self.title_label = Gtk.Label(
            label=self._title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0
        )
        title_bar.set_title_widget(self.title_label)
        close_button = Gtk.Button(
            icon_name="window-close-symbolic", tooltip_text="Close Pane"
        )
        close_button.connect("clicked", self._on_close_clicked)
        title_bar.pack_end(close_button)
        return title_bar

    def _on_close_clicked(self, button) -> None:
        if self.on_close_requested:
            self.on_close_requested(self.terminal)

    def set_title(self, title: str) -> None:
        self._title = title
        if hasattr(self, "title_label"):
            self.title_label.set_text(title)

    def get_terminal(self) -> Vte.Terminal:
        return self.terminal


class TabManager:
    def __init__(self, terminal_manager: TerminalManager):
        self.logger = get_logger("ashyterm.tabs.manager")
        self.terminal_manager = terminal_manager

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
        self.on_quit_application = None
        self.on_detach_tab_requested: Optional[Callable[[Adw.ViewStackPage], None]] = (
            None
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
    ) -> None:
        terminal = self.terminal_manager.create_local_terminal(
            title=title,
            working_directory=working_directory,
            execute_command=execute_command,
            close_after_execute=close_after_execute,
        )
        if terminal:
            self._create_tab_for_terminal(terminal, title, "computer-symbolic")

    def create_ssh_tab(self, session: SessionItem) -> None:
        terminal = self.terminal_manager.create_ssh_terminal(session)
        if terminal:
            self._create_tab_for_terminal(
                terminal, session.name, "network-server-symbolic"
            )

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

        tab_widget.page_widget = page
        tab_widget.label_widget = label
        tab_widget._base_title = title
        tab_widget._icon_name = icon_name
        tab_widget._is_local = icon_name == "computer-symbolic"

        return tab_widget

    def _on_tab_clicked(self, gesture, n_press, x, y, tab_widget):
        self.set_active_tab(tab_widget)

    def _on_tab_right_click(self, gesture, n_press, x, y, tab_widget):
        menu = Gio.Menu()
        menu.append(_("Detach Tab"), "win.detach-tab")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(tab_widget)

        page = self.pages.get(tab_widget)
        if page:
            action_group = Gio.SimpleActionGroup()
            action = Gio.SimpleAction.new("detach-tab", None)

            # Use a partial or lambda to capture the page object
            action.connect(
                "activate", lambda a, p, pg=page: self._request_detach_tab(pg)
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
        if isinstance(widget, TerminalPaneWithTitleBar):
            terminals_list.append(widget.get_terminal())
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
        if is_last_split and isinstance(survivor_pane, TerminalPaneWithTitleBar):
            scrolled_win_child = survivor_pane.scrolled_window
            if hasattr(grandparent, "set_child"):
                survivor_pane.remove(scrolled_win_child)
                grandparent.set_child(scrolled_win_child)

        GLib.idle_add(self.update_all_tab_titles)

    def close_pane(self, terminal: Vte.Terminal) -> None:
        """Close a single pane within a tab."""
        self.terminal_manager.remove_terminal(terminal)

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

            terminal_id = getattr(focused_terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else "Local"
            new_terminal = None
            new_pane_title = "Terminal"

            if isinstance(identifier, SessionItem):
                new_pane_title = identifier.name
                new_terminal = (
                    self.terminal_manager.create_ssh_terminal(identifier)
                    if identifier.is_ssh()
                    else self.terminal_manager.create_local_terminal(identifier.name)
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
            new_pane = TerminalPaneWithTitleBar(new_terminal, new_pane_title)
            new_pane.on_close_requested = self.close_pane
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, new_terminal)
            new_terminal.add_controller(focus_controller)

            pane_to_replace, container = self._find_pane_and_parent(focused_terminal)
            if not pane_to_replace:
                self.logger.error("Could not find the pane to replace for splitting.")
                self.terminal_manager.remove_terminal(new_terminal)
                return

            if isinstance(pane_to_replace, Gtk.ScrolledWindow):
                title = "Terminal"
                for tab in self.tabs:
                    if self.pages.get(tab) == page:
                        title = tab._base_title
                        break
                pane_being_split = TerminalPaneWithTitleBar(focused_terminal, title)
                pane_being_split.on_close_requested = self.close_pane
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

        # Update parent page for all terminals within the re-attached content
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
        return page

    def close_active_tab(self):
        """A helper to forcefully close the currently active tab."""
        if self.active_tab:
            self._on_tab_close_button_clicked(None, self.active_tab)
