# ashyterm/terminal/tabs.py

import re
import threading
import weakref
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from ..filemanager.manager import FileManager
from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager as SettingsManagerType
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .manager import TerminalManager


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

    # MODIFIED: Apply headerbar transparency settings on creation
    settings_manager.apply_headerbar_transparency(header_box)

    # Title label
    title_label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0)
    title_label.set_hexpand(True)
    title_label.set_halign(Gtk.Align.START)
    header_box.append(title_label)

    # Action buttons
    move_to_tab_button = Gtk.Button(
        icon_name="select-rectangular-symbolic", tooltip_text=_("Move to New Tab")
    )
    move_to_tab_button.add_css_class("flat")
    move_to_tab_button.connect("clicked", lambda _: on_move_to_tab_callback(terminal))

    close_button = Gtk.Button(
        icon_name="window-close-symbolic", tooltip_text=_("Close Pane")
    )
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
        self._update_tab_alignment()
        self.tabs: List[Gtk.Box] = []
        self.pages: weakref.WeakKeyDictionary[Gtk.Box, Adw.ViewStackPage] = (
            weakref.WeakKeyDictionary()
        )
        self.file_managers: weakref.WeakKeyDictionary[
            Adw.ViewStackPage, FileManager
        ] = weakref.WeakKeyDictionary()
        self.active_tab: Optional[Gtk.Box] = None

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_focused_terminal = None

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
        if key == "headerbar_transparency" or key == "gtk_theme":
            self._update_all_pane_headers_transparency()

    def _update_all_pane_headers_transparency(self):
        """Iterates through all panes in all tabs and reapplies transparency."""
        for page in self.pages.values():
            panes = []
            self._find_panes_recursive(page.get_child(), panes)
            for pane in panes:
                if hasattr(pane, "header_box"):
                    self.terminal_manager.settings_manager.apply_headerbar_transparency(
                        pane.header_box
                    )

    def _find_panes_recursive(self, widget, panes_list: List[Adw.ToolbarView]):
        """Recursively find all Adw.ToolbarView panes within a container."""
        if isinstance(widget, Adw.ToolbarView) and hasattr(widget, "terminal"):
            panes_list.append(widget)
            return

        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_panes_recursive(start_child, panes_list)
            if end_child := widget.get_end_child():
                self._find_panes_recursive(end_child, panes_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_panes_recursive(child, panes_list)

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

        terminal = self.terminal_manager.create_local_terminal(
            session=session,
            title=session.name,
            working_directory=working_directory,
            execute_command=execute_command,
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
            sftp_session.name = f"SFTP: {session.name}"
            self._create_tab_for_terminal(terminal, sftp_session)
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

    def _on_terminal_scroll(self, controller, dx, dy):
        """Handles terminal scroll events to apply custom sensitivity."""
        try:
            terminal = controller.get_widget()
            scrolled_window = terminal.get_parent()

            if not isinstance(scrolled_window, Gtk.ScrolledWindow):
                return Gdk.EVENT_PROPAGATE

            vadjustment = scrolled_window.get_vadjustment()
            if not vadjustment:
                return Gdk.EVENT_PROPAGATE

            event = controller.get_current_event()
            device = event.get_device() if event else None
            source = device.get_source() if device else Gdk.InputSource.MOUSE

            if source == Gdk.InputSource.TOUCHPAD:
                sensitivity_percent = self.terminal_manager.settings_manager.get(
                    "touchpad_scroll_sensitivity", 30.0
                )
                sensitivity_factor = sensitivity_percent / 50.0
            else:
                sensitivity_percent = self.terminal_manager.settings_manager.get(
                    "mouse_scroll_sensitivity", 30.0
                )
                sensitivity_factor = sensitivity_percent / 10.0

            step = vadjustment.get_step_increment()
            scroll_amount = dy * step * sensitivity_factor

            new_value = vadjustment.get_value() + scroll_amount
            vadjustment.set_value(new_value)

            return Gdk.EVENT_STOP
        except Exception as e:
            self.logger.warning(f"Error handling custom scroll: {e}")

        return Gdk.EVENT_PROPAGATE

    def _on_terminal_contents_changed(self, terminal: Vte.Terminal):
        """Handles smart scrolling on new terminal output."""
        if not self.terminal_manager.settings_manager.get("scroll_on_output", True):
            return

        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            return

        adjustment = scrolled_window.get_vadjustment()
        if not adjustment:
            return

        # Check if we are scrolled to the bottom (with a small tolerance of 1.0)
        is_at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 1.0
        )

        if is_at_bottom:
            # Defer scrolling to the end to the idle loop. This ensures that the
            # adjustment's 'upper' value is updated before we try to scroll.
            def scroll_to_end():
                adjustment.set_value(
                    adjustment.get_upper() - adjustment.get_page_size()
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(scroll_to_end)

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, session: SessionItem
    ) -> None:
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect("scroll", self._on_terminal_scroll)
        terminal.add_controller(scroll_controller)

        terminal.connect("contents-changed", self._on_terminal_contents_changed)

        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        # Connect to the 'realize' signal to grab focus when the widget is ready.
        # This is a one-shot connection; it disconnects itself after running.
        handler_id_ref = [None]

        def on_terminal_realize_once(widget, *args):
            widget.grab_focus()
            if handler_id_ref[0] and widget.handler_is_connected(handler_id_ref[0]):
                widget.disconnect(handler_id_ref[0])

        handler_id = terminal.connect_after("realize", on_terminal_realize_once)
        handler_id_ref[0] = handler_id

        terminal_area = Adw.Bin()
        terminal_area.set_child(scrolled_window)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
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

        GLib.idle_add(self._scroll_to_widget, tab_widget)

    def _get_contrasting_text_color(self, bg_color_str: str) -> str:
        """Calculates whether black or white text is more readable on a given background color."""
        if not bg_color_str:
            return "#000000"  # Default to black

        try:
            match = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+),?.*\)", bg_color_str)
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
            text_color = self._get_contrasting_text_color(color_string)

            # This CSS is more robust. It overrides theme gradients and sets the color.
            css = f"""
                .custom-tab-button {{
                    background-image: none;
                    background-color: {color_string};
                    border-color: transparent;
                    color: {text_color};
                }}
                .custom-tab-button.active {{
                    background-image: none;
                    background-color: mix({color_string}, @theme_selected_bg_color, 0.7);
                    color: {text_color};
                }}
            """
            provider.load_from_data(css.encode("utf-8"))
            style_context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            widget._color_provider = provider

    def _create_tab_widget(
        self, page: Adw.ViewStackPage, session: SessionItem
    ) -> Gtk.Box:
        tab_widget = Gtk.Box(spacing=6)
        tab_widget.add_css_class("custom-tab-button")
        tab_widget.add_css_class("pill")

        icon_name = None
        if session.name.startswith("SFTP:"):
            icon_name = "folder-remote-symbolic"
        elif session.is_ssh():
            icon_name = "network-server-symbolic"

        if icon_name:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            tab_widget.append(icon)

        label = Gtk.Label(
            label=session.name, ellipsize=Pango.EllipsizeMode.START, xalign=1.0
        )
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
        tab_widget._base_title = session.name
        tab_widget._icon_name = icon_name
        tab_widget._is_local = session.is_local()
        tab_widget.session_item = session

        self._apply_tab_color(tab_widget, session.tab_color)

        return tab_widget

    def _on_tab_clicked(self, _gesture, _n_press, _x, _y, tab_widget):
        self.set_active_tab(tab_widget)

    def _on_tab_right_click(self, _gesture, _n_press, x, y, tab_widget):
        menu = Gio.Menu()
        menu.append(_("Detach Tab"), "win.detach-tab")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        if popover.get_parent() is not None:
            popover.unparent()
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

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        popover.set_pointing_to(rect)
        popover.popup()

    def _request_detach_tab(self, page: Adw.ViewStackPage):
        if self.on_detach_tab_requested:
            self.on_detach_tab_requested(page)

    def _is_widget_in_filemanager(self, widget: Gtk.Widget) -> bool:
        """Checks if a widget is a descendant of the FileManager's main widget."""
        if not widget or not self.active_tab:
            return False

        page = self.pages.get(self.active_tab)
        if not page:
            return False

        fm = self.file_managers.get(page)
        if not fm:
            return False

        fm_widget = fm.get_main_widget()
        current = widget
        while current:
            if current == fm_widget:
                return True
            current = current.get_parent()
        return False

    def set_active_tab(self, tab_to_activate: Gtk.Box):
        if self.active_tab == tab_to_activate:
            return

        if self.active_tab:
            main_window = self.terminal_manager.parent_window
            focus_widget = main_window.get_focus()
            if focus_widget and self._is_widget_in_filemanager(focus_widget):
                self.view_stack.grab_focus()

        if self.active_tab:
            self.active_tab.remove_css_class("active")

        self.active_tab = tab_to_activate
        self.active_tab.add_css_class("active")

        page = self.pages.get(self.active_tab)
        if page:
            self.view_stack.set_visible_child(page.get_child())

            terminal_to_focus = None
            # Check if the page has a remembered focused terminal
            if hasattr(page, "_last_focused_in_page") and page._last_focused_in_page:
                terminal_to_focus = (
                    page._last_focused_in_page()
                )  # This might be None if the ref is dead

            # If no valid remembered terminal, fall back to the first one
            if not terminal_to_focus:
                terminals_in_page = self.get_all_terminals_in_page(page)
                if terminals_in_page:
                    terminal_to_focus = terminals_in_page[0]

            # If we have a terminal, schedule the focus. The schedule function will check if it's realized.
            if terminal_to_focus:
                self._schedule_terminal_focus(terminal_to_focus)

    def toggle_file_manager_for_active_tab(self, is_active: bool):
        """Toggles the file manager's visibility for the currently active tab."""
        if not self.active_tab:
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        page = self.pages.get(self.active_tab)
        if not page:
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        if not hasattr(page, "content_paned"):
            self.logger.warning(
                "Attempted to toggle file manager on a page without a content_paned (likely a detached tab)."
            )
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        paned = page.content_paned
        fm = self.file_managers.get(page)

        if is_active:
            active_terminal = self.get_selected_terminal()
            if not active_terminal:
                if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                    self.terminal_manager.parent_window.file_manager_button.set_active(
                        False
                    )

            if not fm:
                fm = FileManager(
                    self.terminal_manager.parent_window,
                    self.terminal_manager,
                    self.terminal_manager.settings_manager,
                )
                fm.temp_files_changed_handler_id = fm.connect(
                    "temp-files-changed",
                    self.terminal_manager.parent_window._on_temp_files_changed,
                    page,
                )
                self.file_managers[page] = fm

            fm.rebind_terminal(active_terminal)
            paned.set_end_child(fm.get_main_widget())
            last_pos = getattr(
                page,
                "_fm_paned_pos",
                self.terminal_manager.parent_window.get_height() - 250,
            )
            paned.set_position(last_pos)
            fm.set_visibility(True, source="filemanager")
        elif fm:
            page._fm_paned_pos = paned.get_position()
            fm.set_visibility(False, source="filemanager")
            paned.set_end_child(None)

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

            pane_to_remove, parent_container = self._find_pane_and_parent(terminal)
            # MODIFIED: Only manipulate panes if the parent is a Gtk.Paned (i.e., it's a split)
            if isinstance(parent_container, Gtk.Paned):
                self._remove_pane_ui(pane_to_remove, parent_container)

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

            # Explicitly destroy the FileManager instance
            if page in self.file_managers:
                fm = self.file_managers.pop(page)
                fm.destroy()

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

    def update_titles_for_terminal(self, terminal, new_title: str, osc7_info):
        """Updates the tab title and the specific pane title for a terminal."""
        page = self.get_page_for_terminal(terminal)
        if not page:
            return

        # Update the main tab title
        self.set_tab_title(page, new_title)

        # Update the specific pane's title
        pane = self._find_pane_for_terminal(page, terminal)
        if pane and hasattr(pane, "title_label"):
            pane.title_label.set_label(new_title)

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

            # NOVO: Forçar a atualização da UI da janela principal
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

        def focus_task():
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
            else:
                self.logger.warning(
                    f"Could not set focus on terminal {getattr(terminal, 'terminal_id', 'N/A')}: not ready or invalid."
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(focus_task)

    def _find_pane_for_terminal(
        self, page: Adw.ViewStackPage, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Recursively finds the Adw.ToolbarView pane that contains a specific terminal."""

        def find_recursive(widget):
            if (
                isinstance(widget, Adw.ToolbarView)
                and getattr(widget, "terminal", None) == terminal_to_find
            ):
                return widget

            if isinstance(widget, Gtk.Paned):
                if start_child := widget.get_start_child():
                    if found := find_recursive(start_child):
                        return found
                if end_child := widget.get_end_child():
                    if found := find_recursive(end_child):
                        return found

            if hasattr(widget, "get_child") and (child := widget.get_child()):
                return find_recursive(child)

            return None

        return find_recursive(page.get_child())

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

        survivor_terminals = []
        self._find_terminals_recursive(survivor_pane, survivor_terminals)
        survivor_terminal = survivor_terminals[0] if survivor_terminals else None

        parent_paned.set_focus_child(None)
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

        def _restore_focus():
            if survivor_terminal and survivor_terminal.get_realized():
                survivor_terminal.grab_focus()
            return False

        GLib.idle_add(_restore_focus)
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

        if isinstance(identifier, SessionItem):
            session = identifier
        else:
            session = SessionItem(name=str(identifier), session_type="local")

        self._create_tab_for_terminal(terminal, session)
        self.logger.info(f"Terminal {terminal_id} successfully moved to a new tab.")

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _set_paned_position_from_ratio(self, paned: Gtk.Paned, ratio: float) -> bool:
        alloc = paned.get_allocation()
        total_size = (
            alloc.width
            if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
            else alloc.height
        )
        if total_size > 0:
            paned.set_position(int(total_size * ratio))
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
                    else self.terminal_manager.create_local_terminal(session=identifier)
                )
            else:
                new_pane_title = "Local"
                new_terminal = self.terminal_manager.create_local_terminal(
                    title=new_pane_title
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
                self.terminal_manager.settings_manager,
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
                title = "Terminal"
                if uri:
                    from urllib.parse import unquote, urlparse

                    path = unquote(urlparse(uri).path)
                    title = (
                        self.terminal_manager.osc7_tracker.parser._create_display_path(
                            path
                        )
                    )

                pane_to_replace.set_child(None)
                pane_being_split = _create_terminal_pane(
                    focused_terminal,
                    title,
                    self.close_pane,
                    self._on_move_to_tab_callback,
                    self.terminal_manager.settings_manager,
                )
            else:
                pane_being_split = pane_to_replace

            is_start_child = False
            if isinstance(container, Gtk.Paned):
                is_start_child = container.get_start_child() == pane_to_replace
                container.set_focus_child(None)
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

            GLib.idle_add(self._set_paned_position_from_ratio, new_split_paned, 0.5)
            self._schedule_terminal_focus(new_terminal)
            self.update_all_tab_titles()

    def re_attach_detached_page(
        self,
        content: Gtk.Widget,
        title: str,
        session_type: str,
        file_manager_instance: Optional[FileManager] = None,
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

    def recreate_tab_from_structure(self, structure: dict):
        """Recreates a complete tab, including splits, from a saved structure."""
        if not structure:
            return

        root_widget = self._recreate_widget_from_node(structure)
        if not root_widget:
            self.logger.error("Failed to create root widget for tab restoration.")
            return

        # If the restored root is a single pane (ToolbarView), we need to unwrap it
        # to match the structure of a newly created single-terminal tab.
        terminal_area_content = root_widget
        if isinstance(root_widget, Adw.ToolbarView):
            scrolled_win = root_widget.get_content()
            if scrolled_win:
                root_widget.set_content(None)
                terminal_area_content = scrolled_win

        # Now, build the standard tab structure with the restored content
        terminal_area = Adw.Bin()
        terminal_area.set_child(terminal_area_content)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        content_paned.set_start_child(terminal_area)
        content_paned.set_resize_start_child(True)
        content_paned.set_shrink_start_child(False)
        content_paned.set_end_child(None)
        content_paned.set_resize_end_child(False)
        content_paned.set_shrink_end_child(True)

        first_terminal = None
        terminals = []
        self._find_terminals_recursive(root_widget, terminals)
        if terminals:
            first_terminal = terminals[0]

        if not first_terminal:
            self.logger.error("Restored tab contains no terminals.")
            for term in terminals:
                self.terminal_manager.remove_terminal(term)
            return

        terminal_id = getattr(first_terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            session = identifier
        else:
            session = SessionItem(name=str(identifier), session_type="local")

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
            terminal = None
            working_dir = node.get("working_dir")
            initial_command = (
                f'cd "{working_dir}"'
                if working_dir and node["session_type"] == "ssh"
                else None
            )
            title = node.get("session_name", "Terminal")
            session_type = node.get("session_type", "local")

            if session_type == "ssh":
                session = next(
                    (
                        s
                        for s in self.terminal_manager.parent_window.session_store
                        if s.name == node["session_name"]
                    ),
                    None,
                )
                if session and session.is_ssh():
                    terminal = self.terminal_manager.create_ssh_terminal(
                        session, initial_command=initial_command
                    )
                else:
                    self.logger.warning(
                        f"Could not find SSH session '{node['session_name']}' to restore, or type mismatch."
                    )
                    terminal = self.terminal_manager.create_local_terminal(
                        title=f"Missing: {title}"
                    )
            else:  # session_type is local
                session = next(
                    (
                        s
                        for s in self.terminal_manager.parent_window.session_store
                        if s.name == node["session_name"] and s.is_local()
                    ),
                    None,
                )
                terminal = self.terminal_manager.create_local_terminal(
                    session=session, title=title, working_directory=working_dir
                )

            if not terminal:
                return None

            # For splits, we need the pane wrapper. For single terminals, we'll unwrap it later.
            pane_widget = _create_terminal_pane(
                terminal,
                title,
                self.close_pane,
                self._on_move_to_tab_callback,
                self.terminal_manager.settings_manager,
            )

            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, terminal)
            terminal.add_controller(focus_controller)

            return pane_widget

        elif node_type == "paned":
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

        return None

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
