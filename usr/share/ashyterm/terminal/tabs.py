# ashyterm/terminal/tabs.py

from typing import Optional, Callable, List
import threading
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Pango
from gi.repository import Vte

from ..sessions.models import SessionItem
from .manager import TerminalManager

# Import new utility systems
from ..utils.logger import get_logger


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
        # Apply custom CSS for headerbar and its children
        css = """
        headerbar {
            min-height: 0px;
        }

        tabbar {
            margin: -8px;
        }

        .terminal-tab-view headerbar entry,
        .terminal-tab-view headerbar spinbutton,
        .terminal-tab-view headerbar button,

        headerbar separator {
            margin-top: -10px;
            margin-bottom: -10px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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
    SCROLL_SENSITIVITY_FACTOR = 0.3

    def __init__(self, terminal_manager: TerminalManager):
        self.logger = get_logger("ashyterm.tabs.manager")
        self.terminal_manager = terminal_manager
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar(view=self.tab_view)
        # Ensure the tab_bar has the 'tabbar' style class for CSS targeting
        self.tab_bar.get_style_context().add_class("tabbar")

        # Inject CSS for AdwTabBar (GTK4/Adwaita)
        css = """
        .tabbar {
            margin: -8px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._focus_lock = threading.Lock()
        self._closing_pages = set()

        # Track terminals being closed individually (split pane closes)
        self._individual_pane_closes = set()

        self._last_focused_terminal = None
        self._focus_timeout_id = None
        self._setup_tab_components()

        # Callback functions
        self.on_tab_selected = None
        self.on_tab_closed = None
        self.on_quit_application = None

        self.logger.info("Tab manager initialized")

    def _setup_tab_components(self) -> None:
        self.tab_view.set_vexpand(True)
        self.tab_view.connect("close-page", self._on_close_page_request)

        # Register this tab manager with the terminal manager for exit events
        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )

    def get_tab_view(self) -> Adw.TabView:
        return self.tab_view

    def get_tab_bar(self) -> Adw.TabBar:
        return self.tab_bar

    def get_selected_page(self) -> Optional[Adw.TabPage]:
        """Get the currently selected tab page."""
        return self.tab_view.get_selected_page()

    def create_local_tab(self, title: str = "Local") -> Optional[Adw.TabPage]:
        terminal = self.terminal_manager.create_local_terminal(title)
        if not terminal:
            return None
        return self._create_tab_for_terminal(terminal, title, "computer-symbolic")

    def create_ssh_tab(self, session: SessionItem) -> Optional[Adw.TabPage]:
        terminal = self.terminal_manager.create_ssh_terminal(session)
        if not terminal:
            return None
        return self._create_tab_for_terminal(
            terminal, session.name, "network-server-symbolic"
        )

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, title: str, icon_name: str
    ) -> Optional[Adw.TabPage]:
        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        page = self.tab_view.add_page(scrolled_window, None)
        page.set_title(title)
        if icon_name:
            page.set_icon(Gio.ThemedIcon.new(icon_name))

        terminal.ashy_parent_page = page

        # Store the base title for terminal count display
        if not hasattr(page, "_base_title"):
            page._base_title = title

        self.tab_view.set_selected_page(page)
        self._schedule_terminal_focus(terminal, title)

        # Update tab titles to reflect current terminal counts
        GLib.idle_add(self.update_all_tab_titles)
        GLib.idle_add(self._update_tab_bar_visibility)

        self.logger.debug(f"Created tab '{title}' with terminal count display")
        return page

    def _on_pane_focus_in(self, controller, terminal):
        self._last_focused_terminal = weakref.ref(terminal)
        self.logger.debug(
            f"Focus tracked on terminal ID: {getattr(terminal, 'terminal_id', 'N/A')}"
        )

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        if self._last_focused_terminal and (terminal := self._last_focused_terminal()):
            if (
                terminal.get_ancestor(Adw.TabView) == self.tab_view
                and terminal.get_realized()
            ):
                return terminal

        page = self.tab_view.get_selected_page()
        if not page:
            return None

        terminals = self.get_all_terminals_in_page(page)
        return terminals[0] if terminals else None

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        with self._creation_lock:
            self.logger.debug(
                f"Splitting terminal (orientation: {orientation.value_nick})"
            )

            terminal_id = getattr(focused_terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else "Local"

            current_directory = None
            try:
                current_uri = focused_terminal.get_current_directory_uri()
                if current_uri:
                    from urllib.parse import urlparse, unquote

                    parsed_uri = urlparse(current_uri)
                    if parsed_uri.scheme == "file":
                        current_directory = unquote(parsed_uri.path)
                        self.logger.debug(
                            f"Will start new split in directory: {current_directory}"
                        )
            except Exception as e:
                self.logger.debug(f"Could not get current directory for split: {e}")

            new_terminal = None
            new_pane_title = "Terminal"
            if isinstance(identifier, SessionItem):
                new_pane_title = identifier.name
                if identifier.is_ssh():
                    new_terminal = self.terminal_manager.create_ssh_terminal(identifier)
                else:
                    new_terminal = self.terminal_manager.create_local_terminal(
                        identifier.name, working_directory=current_directory
                    )
            else:
                new_pane_title = "Local"
                new_terminal = self.terminal_manager.create_local_terminal(
                    new_pane_title, working_directory=current_directory
                )

            if not new_terminal:
                self.logger.error("Failed to create new terminal for split.")
                return

            if hasattr(focused_terminal, "ashy_parent_page"):
                new_terminal.ashy_parent_page = focused_terminal.ashy_parent_page

            new_pane = TerminalPaneWithTitleBar(new_terminal, new_pane_title)
            new_pane.on_close_requested = self.close_pane

            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, new_terminal)
            new_terminal.add_controller(focus_controller)

            widget_to_replace = focused_terminal.get_parent()
            if isinstance(widget_to_replace.get_parent(), TerminalPaneWithTitleBar):
                widget_to_replace = widget_to_replace.get_parent()

            container = widget_to_replace.get_parent()
            if not container:
                self.logger.error(
                    "Cannot split: focused terminal has no parent container."
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            if isinstance(widget_to_replace, Gtk.ScrolledWindow):
                page = self.get_page_for_terminal(focused_terminal)
                title = page.get_title() if page else "Terminal"
                existing_pane = TerminalPaneWithTitleBar(focused_terminal, title)
                existing_pane.on_close_requested = self.close_pane
            else:
                existing_pane = widget_to_replace

            is_start_child = False
            if isinstance(container, Gtk.Paned):
                is_start_child = container.get_start_child() == existing_pane
                if is_start_child:
                    container.set_start_child(None)
                else:
                    container.set_end_child(None)
            elif isinstance(container, Adw.Bin):
                container.set_child(None)

            paned = Gtk.Paned(orientation=orientation)
            paned.set_start_child(existing_pane)
            paned.set_end_child(new_pane)

            if isinstance(container, Gtk.Paned):
                if is_start_child:
                    container.set_start_child(paned)
                else:
                    container.set_end_child(paned)
            elif isinstance(container, Adw.Bin):
                container.set_child(paned)

            GLib.idle_add(lambda: self._set_paned_position(paned))
            self._schedule_terminal_focus(new_terminal, "New Split")
            self.logger.info(
                f"Successfully created split with orientation {orientation.value_nick}"
            )

            # Update tab titles to show new terminal count
            GLib.idle_add(self.update_all_tab_titles)

    def _set_paned_position(self, paned: Gtk.Paned) -> bool:
        orientation = paned.get_orientation()
        alloc = paned.get_allocation()
        total_size = (
            alloc.width if orientation == Gtk.Orientation.HORIZONTAL else alloc.height
        )
        if total_size > 0:
            paned.set_position(total_size // 2)
        return False

    def close_pane(self, focused_terminal: Vte.Terminal) -> None:
        """Close individual split pane - this only closes the specific pane, not all splits."""
        with self._cleanup_lock:
            terminal_id = getattr(focused_terminal, "terminal_id", None)
            self.logger.info(
                f"PANE CLOSE: User requested close of individual split pane for terminal {terminal_id}"
            )

            # Mark this as an individual pane close so the exit handler knows
            if terminal_id:
                self._individual_pane_closes.add(terminal_id)

            # Use the old individual pane closing behavior
            self.terminal_manager.remove_terminal(focused_terminal)

    def _find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        widget = terminal
        while widget:
            parent = widget.get_parent()
            if isinstance(
                widget, (TerminalPaneWithTitleBar, Gtk.ScrolledWindow)
            ) and isinstance(parent, Gtk.Paned):
                return widget, parent
            widget = parent
        return None, None

    def get_all_terminals_in_page(self, page: Adw.TabPage) -> List[Vte.Terminal]:
        terminals = []
        if root_widget := page.get_child():
            self._find_terminals_recursive(root_widget, terminals)
        return terminals

    def _find_terminals_recursive(
        self, widget, terminals_list: List[Vte.Terminal]
    ) -> None:
        if isinstance(widget, TerminalPaneWithTitleBar):
            terminals_list.append(widget.get_terminal())
        elif isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminals_list.append(widget.get_child())
        elif isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_terminals_recursive(start_child, terminals_list)
            if end_child := widget.get_end_child():
                self._find_terminals_recursive(end_child, terminals_list)
        elif hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_terminals_recursive(child, terminals_list)

    def update_terminal_title_in_splits(
        self, terminal: Vte.Terminal, new_title: str
    ) -> None:
        """Update terminal title in split panes and tab titles with terminal count."""
        pane, _ = self._find_pane_and_parent(terminal)
        if pane and isinstance(pane, TerminalPaneWithTitleBar):
            pane.set_title(new_title)

        # Update tab title with terminal count - preserve OSC7 directory info
        page = self.get_page_for_terminal(terminal)
        if page:
            # Update the base title to reflect the directory change from OSC7
            page._base_title = new_title
            self.set_tab_title(page, new_title)
            self.logger.debug(
                f"Updated tab title via OSC7 to show terminal count for '{new_title}'"
            )

    def _on_close_page_request(self, tab_view, page) -> bool:
        """Handle close page request with proper Adwaita workflow."""
        self.logger.debug(f"Close request received for tab page: {page.get_title()}")

        # Check if already closing to prevent race conditions
        if id(page) in self._closing_pages:
            self.logger.debug(f"Page {page.get_title()} already being closed")
            # If already marked as closing, just finalize (likely a terminal exit)
            try:
                self.tab_view.close_page_finish(page, True)
            except Exception as e:
                self.logger.debug(f"Close page finish failed (may be normal): {e}")
            finally:
                self._closing_pages.discard(id(page))
            return True

        # Mark as closing
        self._closing_pages.add(id(page))

        # Request confirmation and start the close process
        self._request_close_tab(page)
        return True  # We handle the close process ourselves

    def _request_close_tab(self, page: Adw.TabPage) -> None:
        """Request tab close - this is for USER-INITIATED closures (close button), never quit app."""
        try:
            self.logger.info(
                f"USER CLOSE: User requested close for tab '{page.get_title()}'"
            )

            # Get all terminals in this tab
            terminals_in_page = self.get_all_terminals_in_page(page)

            if terminals_in_page:
                self.logger.info(
                    f"Terminating {len(terminals_in_page)} terminals in tab: {[getattr(t, 'terminal_id', 'N/A') for t in terminals_in_page]}"
                )

                # Terminate all terminal processes in this tab
                for terminal in terminals_in_page:
                    terminal_id = getattr(terminal, "terminal_id", None)
                    if terminal_id:
                        terminal_info = (
                            self.terminal_manager.registry.get_terminal_info(
                                terminal_id
                            )
                        )
                        if terminal_info and terminal_info.get("status", "") not in [
                            "exited",
                            "spawn_failed",
                        ]:
                            self.logger.info(
                                f"Terminating active terminal {terminal_id}"
                            )
                            # Kill the process directly to avoid triggering natural exit handler
                            pid = terminal_info.get("process_id")
                            if pid:
                                try:
                                    import os
                                    import signal

                                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                                    self.logger.info(
                                        f"Sent SIGTERM to process {pid} for terminal {terminal_id}"
                                    )
                                except Exception as e:
                                    self.logger.warning(
                                        f"Failed to terminate process {pid}: {e}"
                                    )

                        # Clean up terminal resources
                        self.terminal_manager._cleanup_terminal(terminal, terminal_id)

            # Always close the tab, never quit the app for user-initiated closures
            self.tab_view.close_page_finish(page, True)
            self.logger.info(f"Tab '{page.get_title()}' closed by user request")

        except Exception as e:
            self.logger.error(f"User tab close request failed: {e}")
            # Still try to finish close
            try:
                self.tab_view.close_page_finish(page, True)
            except:
                pass
        finally:
            # Always remove from closing set
            self._closing_pages.discard(id(page))

    def _confirm_close_tab(self, page: Adw.TabPage) -> None:
        """Show confirmation dialog for closing tab with active processes."""
        # For now, just close - we can add confirmation dialog later
        # This maintains the old behavior while fixing the close mechanism
        self._finalize_close_tab(page)

    def _finalize_close_tab(self, page: Adw.TabPage) -> None:
        """Finalize closing a tab by terminating processes and removing UI."""
        try:
            # Terminate all terminals in the page
            terminals_in_page = self.get_all_terminals_in_page(page)
            for terminal in terminals_in_page:
                self.terminal_manager.remove_terminal(terminal)

            # Use proper Adwaita close mechanism
            self.tab_view.close_page_finish(page, True)

        except Exception as e:
            self.logger.error(f"Tab finalization failed: {e}")
            # Still try to finish the close
            try:
                self.tab_view.close_page_finish(page, True)
            except:
                pass
        finally:
            # Always remove from closing set
            self._closing_pages.discard(id(page))

    def _on_terminal_process_exited(
        self, terminal: Vte.Terminal, child_status: int, identifier
    ) -> None:
        """Handle terminal process exit - treat natural exits like individual pane closes."""
        try:
            terminal_id = getattr(terminal, "terminal_id", "N/A")
            terminal_name = (
                identifier.name if hasattr(identifier, "name") else str(identifier)
            )

            # Check if this is an individual pane close
            is_individual_pane_close = terminal_id in self._individual_pane_closes
            if is_individual_pane_close:
                self._individual_pane_closes.discard(terminal_id)
                self.logger.info(
                    f"PANE EXIT: Terminal {terminal_id} exited from individual pane close"
                )
            else:
                self.logger.info(
                    f"NATURAL EXIT: Terminal '{terminal_name}' (ID: {terminal_id}) exited naturally with status {child_status}"
                )

            # Find which page this terminal belongs to
            page = self.get_page_for_terminal(terminal)
            if not page:
                self.logger.warning(f"Cannot find page for terminal {terminal_id}")
                self.terminal_manager._cleanup_terminal(terminal, terminal_id)
                return

            # Check if this is a split pane or the main terminal
            pane_to_remove, parent_paned = self._find_pane_and_parent(terminal)

            if parent_paned:
                # This is a split pane - remove just the pane (both for natural exit and pane close)
                self.logger.info(
                    f"Removing individual split pane for terminal {terminal_id}"
                )
                self._remove_pane_ui(pane_to_remove, parent_paned)
                # Clean up backend resources
                self.terminal_manager._cleanup_terminal(terminal, terminal_id)
                # Update tab titles after removing split
                GLib.idle_add(self.update_all_tab_titles)

            else:
                # This is the main/only terminal in a tab
                self.logger.info(
                    f"Main terminal {terminal_id} exited in tab '{page.get_title()}'"
                )

                # Check if there are other terminals in this tab
                all_terminals_in_page = self.get_all_terminals_in_page(page)
                remaining_terminals = [
                    t for t in all_terminals_in_page if t != terminal
                ]

                if remaining_terminals:
                    # There are other terminals in the tab - just clean up this one
                    self.logger.info(
                        f"Other terminals remain in tab, only cleaning up terminal {terminal_id}"
                    )
                    self.terminal_manager._cleanup_terminal(terminal, terminal_id)
                    GLib.idle_add(self.update_all_tab_titles)
                else:
                    # This was the only terminal in the tab - close the entire tab
                    self.logger.info(
                        f"Only terminal in tab - closing entire tab for terminal {terminal_id}"
                    )

                    # Clean up the terminal first
                    self.terminal_manager._cleanup_terminal(terminal, terminal_id)

                    # Handle tab closure based on remaining tabs
                    if self.get_tab_count() == 1:
                        # This is the last tab - quit the application
                        self.logger.info(
                            "Last tab terminal exited - quitting application"
                        )
                        GLib.idle_add(self._quit_application)
                    else:
                        # Multiple tabs exist - just close this tab
                        self.logger.info(
                            f"Closing tab for exited terminal {terminal_id}"
                        )
                        if id(page) not in self._closing_pages:
                            self._closing_pages.add(id(page))
                            self.tab_view.close_page(page)
                            GLib.idle_add(self._update_tab_bar_visibility)

        except Exception as e:
            self.logger.error(f"Terminal exit handling failed: {e}")
            # Emergency cleanup
            try:
                terminal_id = getattr(terminal, "terminal_id", None)
                if terminal_id:
                    self._individual_pane_closes.discard(terminal_id)
                    self.terminal_manager._cleanup_terminal(terminal, terminal_id)
            except:
                pass

    def _handle_terminal_exit_ui(self, terminal: Vte.Terminal) -> None:
        """Legacy method for compatibility - delegates to _on_terminal_process_exited."""
        # Get terminal info for proper parameters
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is not None:
            terminal_info = self.terminal_manager.registry.get_terminal_info(
                terminal_id
            )
            if terminal_info:
                identifier = terminal_info.get("identifier", "Unknown")
                self._on_terminal_process_exited(
                    terminal, 0, identifier
                )  # Assume clean exit
            else:
                self._on_terminal_process_exited(terminal, 0, "Unknown")

    def close_tab(self, page: Optional[Adw.TabPage] = None) -> bool:
        """Close a tab and properly terminate all associated processes."""
        with self._cleanup_lock:
            if page is None:
                page = self.tab_view.get_selected_page()
            if not page:
                return False

            if self.get_tab_count() <= 1:
                self.logger.debug("Cannot close the last tab")
                return False

            # Get all terminals in this tab and terminate their processes
            terminals_in_page = self.get_all_terminals_in_page(page)
            self.logger.info(
                f"Closing tab '{page.get_title()}' with {len(terminals_in_page)} terminals"
            )

            for terminal in terminals_in_page:
                terminal_id = getattr(terminal, "terminal_id", None)
                if terminal_id:
                    # Get terminal info and terminate process
                    terminal_info = self.terminal_manager.registry.get_terminal_info(
                        terminal_id
                    )
                    if terminal_info and terminal_info.get("status", "") not in [
                        "exited",
                        "spawn_failed",
                    ]:
                        pid = terminal_info.get("process_id")
                        if pid:
                            try:
                                import os
                                import signal

                                os.killpg(os.getpgid(pid), signal.SIGTERM)
                                self.logger.info(
                                    f"Terminated process {pid} for terminal {terminal_id}"
                                )
                            except Exception as e:
                                self.logger.warning(
                                    f"Failed to terminate process {pid}: {e}"
                                )

                    # Clean up terminal resources
                    self.terminal_manager._cleanup_terminal(terminal, terminal_id)

            # Close the tab
            self.tab_view.close_page(page)
            return True

    def _schedule_terminal_focus(self, terminal: Vte.Terminal, title: str) -> None:
        def focus_terminal():
            try:
                if terminal and terminal.get_realized():
                    terminal.grab_focus()
                    self.logger.debug(f"Terminal focused: '{title}'")
                    return False
                return True
            except Exception as e:
                self.logger.warning(f"Terminal focus failed for '{title}': {e}")
                return False

        GLib.timeout_add(100, focus_terminal)

    def get_page_for_terminal(self, terminal: Vte.Terminal) -> Optional[Adw.TabPage]:
        """
        Finds the Adw.TabPage associated with a terminal using a stored reference.
        """
        page = getattr(terminal, "ashy_parent_page", None)
        if page is None:
            self.logger.warning(
                f"Could not find Adw.TabPage for terminal {getattr(terminal, 'terminal_id', 'N/A')} via stored attribute."
            )
        return page

    def set_tab_title(self, page: Adw.TabPage, title: str) -> None:
        """Set tab title with terminal count for splits."""
        try:
            if page and title:
                # Store/update the base title (without terminal count)
                if not hasattr(page, "_base_title") or not page._base_title:
                    page._base_title = title

                # Count terminals in this tab
                terminals_in_page = self.get_all_terminals_in_page(page)
                terminal_count = len(terminals_in_page)

                # Create title with count for multiple terminals
                if terminal_count > 1:
                    final_title = f"{page._base_title} ({terminal_count})"
                else:
                    final_title = page._base_title

                page.set_title(final_title)
                self.logger.debug(
                    f"Set tab title to '{final_title}' for {terminal_count} terminals"
                )
        except Exception as e:
            self.logger.error(f"Failed to set tab title: {e}")

    def update_all_tab_titles(self) -> None:
        """Update titles for all tabs to show terminal count."""
        try:
            for i in range(self.get_tab_count()):
                page = self.tab_view.get_nth_page(i)
                if page:
                    # Use the stored base title or extract from current title
                    if hasattr(page, "_base_title") and page._base_title:
                        base_title = page._base_title
                    else:
                        # Extract base title from current title (remove count if present)
                        current_title = page.get_title()
                        if " (" in current_title and current_title.endswith(")"):
                            base_title = current_title[: current_title.rfind(" (")]
                        else:
                            base_title = current_title
                        page._base_title = base_title

                    # Update with current terminal count
                    self.set_tab_title(page, base_title)
        except Exception as e:
            self.logger.error(f"Failed to update tab titles: {e}")

    def get_tab_count(self) -> int:
        return self.tab_view.get_n_pages()

    def get_all_pages(self) -> List[Adw.TabPage]:
        return [
            self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())
        ]

    def get_all_terminals(self) -> List[Vte.Terminal]:
        terminals = []
        for page in self.get_all_pages():
            terminals.extend(self.get_all_terminals_in_page(page))
        return terminals

    def create_initial_tab_if_empty(self) -> Optional[Adw.TabPage]:
        if self.get_tab_count() == 0:
            return self.create_local_tab("Local")
        return None

    def copy_from_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.copy_selection(terminal)
        return False

    def paste_to_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.paste_clipboard(terminal)
        return False

    def select_all_in_current_terminal(self) -> None:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.select_all(terminal)

    def zoom_in_current_terminal(self, step: float = 0.1) -> bool:
        try:
            if terminal := self.get_selected_terminal():
                return self.terminal_manager.zoom_in(terminal, step)
            return False
        except Exception as e:
            self.logger.error(f"Zoom in current terminal failed: {e}")
            return False

    def zoom_out_current_terminal(self, step: float = 0.1) -> bool:
        try:
            if terminal := self.get_selected_terminal():
                return self.terminal_manager.zoom_out(terminal, step)
            return False
        except Exception as e:
            self.logger.error(f"Zoom out current terminal failed: {e}")
            return False

    def zoom_reset_current_terminal(self) -> bool:
        try:
            if terminal := self.get_selected_terminal():
                return self.terminal_manager.zoom_reset(terminal)
            return False
        except Exception as e:
            self.logger.error(f"Zoom reset current terminal failed: {e}")
            return False

    def _update_tab_bar_visibility(self) -> None:
        try:
            tab_count = self.get_tab_count()
            should_show = tab_count > 1
            self.tab_bar.set_visible(should_show)

            # Note: Do NOT create initial tab here automatically
            # Let the application handle what to do when there are no tabs

            self.logger.debug(
                f"Tab bar visibility updated: {should_show} (count: {tab_count})"
            )
        except Exception as e:
            self.logger.error(f"Tab bar visibility update failed: {e}")

    def cleanup(self) -> None:
        with self._cleanup_lock:
            try:
                self.logger.debug("Starting tab manager cleanup")
                if self._focus_timeout_id is not None:
                    GLib.source_remove(self._focus_timeout_id)
                    self._focus_timeout_id = None
                self.close_all_tabs()
                self.logger.info("Tab manager cleanup completed")
            except Exception as e:
                self.logger.error(f"Tab manager cleanup failed: {e}")

    def close_all_tabs(self) -> None:
        """Close all tabs."""
        try:
            pages = self.get_all_pages()
            for page in pages:
                self.close_tab(page)
        except Exception as e:
            self.logger.error(f"Close all tabs failed: {e}")

    def _quit_application(self) -> bool:
        """Trigger application quit via callback."""
        try:
            if self.on_quit_application:
                self.logger.info("Triggering application quit")
                self.on_quit_application()
            else:
                self.logger.warning("No quit application callback set")
        except Exception as e:
            self.logger.error(f"Application quit trigger failed: {e}")
        return False  # Don't repeat

    def _remove_pane_ui(self, pane_to_remove, parent_paned):
        """Helper to remove a single pane from a split view."""
        is_start_child = parent_paned.get_start_child() == pane_to_remove
        survivor_pane = (
            parent_paned.get_end_child()
            if is_start_child
            else parent_paned.get_start_child()
        )

        if not survivor_pane:
            self.logger.error("Survivor pane not found. Aborting UI removal.")
            return

        grandparent = parent_paned.get_parent()
        if not grandparent:
            self.logger.error("Gtk.Paned container has no parent. Aborting.")
            return

        # Detach panes from the paned widget before removing it
        parent_paned.set_start_child(None)
        parent_paned.set_end_child(None)

        # Check if we need to convert back to normal tab mode
        # If the survivor is a TerminalPaneWithTitleBar and grandparent is not a Paned,
        # we're removing the last split, so convert back to normal scrolled window
        needs_normal_mode_conversion = isinstance(
            survivor_pane, TerminalPaneWithTitleBar
        ) and not isinstance(grandparent, Gtk.Paned)

        if needs_normal_mode_conversion:
            self.logger.debug("Converting last split back to normal tab mode")

            # Extract the terminal from the title bar pane
            survivor_terminal = survivor_pane.get_terminal()

            # Remove terminal from the title bar pane's scrolled window
            survivor_scrolled_window = survivor_pane.scrolled_window
            survivor_scrolled_window.set_child(None)

            # Create new normal scrolled window for the terminal
            new_scrolled_window = Gtk.ScrolledWindow(child=survivor_terminal)
            new_scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            new_scrolled_window.set_vexpand(True)
            new_scrolled_window.set_hexpand(True)

            # Replace the paned widget with the normal scrolled window
            if hasattr(grandparent, "set_child"):
                grandparent.set_child(new_scrolled_window)
            else:
                self.logger.warning(
                    f"Unsupported grandparent container type: {type(grandparent)}"
                )

            self.logger.info("Successfully converted split back to normal tab mode")
        else:
            # Normal case: replace the paned widget with the survivor pane in the grandparent
            if isinstance(grandparent, Gtk.Paned):
                is_grandparent_start = grandparent.get_start_child() == parent_paned
                if is_grandparent_start:
                    grandparent.set_start_child(survivor_pane)
                else:
                    grandparent.set_end_child(survivor_pane)
            elif hasattr(grandparent, "set_child"):
                grandparent.set_child(survivor_pane)
            else:
                self.logger.warning(
                    f"Unsupported grandparent container type: {type(grandparent)}"
                )

        self.logger.info("Successfully removed split pane UI and restructured layout.")

        # Update tab titles after removing split
        GLib.idle_add(self.update_all_tab_titles)
