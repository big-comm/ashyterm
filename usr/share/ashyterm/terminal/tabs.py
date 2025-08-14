from typing import Optional, Callable, List, Dict, Any
import threading
import time
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango
from gi.repository import Vte

from ..sessions.models import SessionItem
from .manager import TerminalManager

# Import new utility systems
from ..utils.logger import get_logger, log_terminal_event
from ..utils.exceptions import UIError, TerminalError, handle_exception, ErrorSeverity
from ..utils.platform import get_platform_info, is_windows


class TerminalPaneWithTitleBar(Gtk.Box):
    """A terminal pane with an integrated, Adwaita-native title bar."""

    def __init__(self, terminal: Vte.Terminal, title: str = "Terminal"):
        """
        Initialize terminal pane with title bar.

        Args:
            terminal: VTE terminal widget
            title: Title to display in title bar
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.terminal = terminal
        self._title = title

        # Create title bar using Adw.HeaderBar for proper styling
        self.title_bar = self._create_title_bar()
        self.append(self.title_bar)

        # Handle terminal that might already be in a scrolled window
        current_parent = terminal.get_parent()
        if isinstance(current_parent, Gtk.ScrolledWindow):
            self.scrolled_window = current_parent
            grandparent = current_parent.get_parent()
            if grandparent:
                grandparent.set_child(None)
        else:
            self.scrolled_window = Gtk.ScrolledWindow()
            self.scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            if current_parent:
                current_parent.remove(terminal) if hasattr(
                    current_parent, "remove"
                ) else current_parent.set_child(None)

        if terminal.get_parent() != self.scrolled_window:
            self.scrolled_window.set_child(terminal)

        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)

        self.append(self.scrolled_window)

        self.on_close_requested: Optional[Callable[[Vte.Terminal], None]] = None

    def _create_title_bar(self) -> Adw.HeaderBar:
        """Create the title bar widget using Adw.HeaderBar."""
        title_bar = Adw.HeaderBar()
        title_bar.set_show_end_title_buttons(False)
        title_bar.set_show_start_title_buttons(False)

        self.title_label = Gtk.Label(label=self._title)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_xalign(0.0)
        title_bar.set_title_widget(self.title_label)

        close_button = Gtk.Button()
        # close_button.set_css_classes(["flat", "circular"])
        close_button.set_icon_name("window-close-symbolic")
        close_button.set_tooltip_text("Close Pane")
        close_button.connect("clicked", self._on_close_clicked)
        title_bar.pack_end(close_button)

        return title_bar

    def _on_close_clicked(self, button) -> None:
        """Handle close button click."""
        if self.on_close_requested:
            self.on_close_requested(self.terminal)

    def set_title(self, title: str) -> None:
        """Update the title displayed in the title bar."""
        self._title = title
        if hasattr(self, "title_label"):
            self.title_label.set_text(title)

    def get_title(self) -> str:
        """Get the current title."""
        return self._title

    def get_terminal(self) -> Vte.Terminal:
        """Get the terminal widget."""
        return self.terminal

    def get_scrolled_window(self) -> Gtk.ScrolledWindow:
        """Get the scrolled window containing the terminal."""
        return self.scrolled_window


class TabRegistry:
    """Registry for tracking tab-terminal relationships and metadata."""

    def __init__(self):
        self.logger = get_logger("ashyterm.tabs.registry")
        self._tab_terminal_map: Dict[Adw.TabPage, weakref.ref] = {}
        self._terminal_tab_map: Dict[int, weakref.ref] = {}  # terminal id -> tab ref
        self._tab_metadata: Dict[int, Dict[str, Any]] = {}  # tab id -> metadata
        self._lock = threading.RLock()
        self._next_tab_id = 1

    def register_tab(
        self, page: Adw.TabPage, terminal: Vte.Terminal, tab_type: str, title: str
    ) -> int:
        """
        Register a tab-terminal relationship.

        Args:
            page: Tab page
            terminal: Terminal widget
            tab_type: Type of tab ('local' or 'ssh')
            title: Tab title

        Returns:
            Tab ID
        """
        with self._lock:
            tab_id = self._next_tab_id
            self._next_tab_id += 1

            # Get terminal ID from terminal - GTK4 compatibility
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                terminal_id = id(terminal)  # Fallback to object id

            # Store relationships with weak references
            def cleanup_tab_ref(ref):
                self._cleanup_tab_reference(tab_id)

            def cleanup_terminal_ref(ref):
                self._cleanup_terminal_reference(tab_id)

            self._tab_terminal_map[page] = weakref.ref(terminal, cleanup_terminal_ref)
            self._terminal_tab_map[terminal_id] = weakref.ref(page, cleanup_tab_ref)

            # Store metadata
            self._tab_metadata[tab_id] = {
                "type": tab_type,
                "title": title,
                "terminal_id": terminal_id,
                "page_id": id(page),
                "created_at": time.time(),
                "focus_count": 0,
                "last_focused": None,
            }

            self.logger.debug(
                f"Tab registered: ID={tab_id}, type={tab_type}, title='{title}'"
            )
            return tab_id

    def get_terminal_for_page(self, page: Adw.TabPage) -> Optional[Vte.Terminal]:
        """Get terminal for a tab page."""
        with self._lock:
            terminal_ref = self._tab_terminal_map.get(page)
            if terminal_ref:
                return terminal_ref()
            return None

    def get_page_for_terminal(self, terminal: Vte.Terminal) -> Optional[Adw.TabPage]:
        """Get tab page for a terminal."""
        with self._lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                terminal_id = id(terminal)

            page_ref = self._terminal_tab_map.get(terminal_id)
            if page_ref:
                return page_ref()
            return None

    def update_tab_focus(self, page: Adw.TabPage) -> None:
        """Update tab focus statistics."""
        with self._lock:
            # Find tab by page
            for tab_id, metadata in self._tab_metadata.items():
                if metadata["page_id"] == id(page):
                    metadata["focus_count"] += 1
                    metadata["last_focused"] = time.time()
                    break

    def unregister_tab(self, page: Adw.TabPage) -> bool:
        """Unregister a tab."""
        with self._lock:
            if page in self._tab_terminal_map:
                # Find and remove metadata
                page_id = id(page)
                tab_id_to_remove = None

                for tab_id, metadata in self._tab_metadata.items():
                    if metadata["page_id"] == page_id:
                        tab_id_to_remove = tab_id
                        break

                # Remove from maps
                terminal_ref = self._tab_terminal_map.pop(page, None)
                if terminal_ref:
                    terminal = terminal_ref()
                    if terminal:
                        terminal_id = getattr(terminal, "terminal_id", None)
                        if terminal_id is None:
                            terminal_id = id(terminal)
                        self._terminal_tab_map.pop(terminal_id, None)

                # Remove metadata
                if tab_id_to_remove is not None:
                    self._tab_metadata.pop(tab_id_to_remove, None)
                    self.logger.debug(f"Tab unregistered: ID={tab_id_to_remove}")

                return True
            return False

    def _cleanup_tab_reference(self, tab_id: int) -> None:
        """Clean up when tab page is garbage collected."""
        with self._lock:
            self._tab_metadata.pop(tab_id, None)
            self.logger.debug(f"Tab reference cleaned up: ID={tab_id}")

    def _cleanup_terminal_reference(self, tab_id: int) -> None:
        """Clean up when terminal is garbage collected."""
        with self._lock:
            # Find and remove from terminal_tab_map
            terminal_id_to_remove = None
            if tab_id in self._tab_metadata:
                terminal_id_to_remove = self._tab_metadata[tab_id].get("terminal_id")

            if terminal_id_to_remove is not None:
                self._terminal_tab_map.pop(terminal_id_to_remove, None)

    def get_tab_count(self) -> int:
        """Get number of registered tabs."""
        with self._lock:
            return len(self._tab_terminal_map)

    def get_all_pages(self) -> List[Adw.TabPage]:
        """Get list of all tab pages."""
        with self._lock:
            return list(self._tab_terminal_map.keys())

    def get_statistics(self) -> Dict[str, Any]:
        """Get tab statistics."""
        with self._lock:
            return {
                "total_tabs": len(self._tab_metadata),
                "active_tabs": len(self._tab_terminal_map),
                "tab_types": {
                    "local": sum(
                        1 for m in self._tab_metadata.values() if m["type"] == "local"
                    ),
                    "ssh": sum(
                        1 for m in self._tab_metadata.values() if m["type"] == "ssh"
                    ),
                },
            }


class TabManager:
    """Enhanced tab manager with comprehensive functionality and thread safety."""

    # Factor to reduce scroll sensitivity. 0.3 = 30% of the original speed.
    SCROLL_SENSITIVITY_FACTOR = 0.3

    def __init__(self, terminal_manager: TerminalManager):
        """
        Initialize tab manager.

        Args:
            terminal_manager: TerminalManager instance for creating terminals
        """
        self.logger = get_logger("ashyterm.tabs.manager")
        self.terminal_manager = terminal_manager
        self.platform_info = get_platform_info()

        # UI components
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar()

        # Tab management
        self.registry = TabRegistry()

        # Thread safety
        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._focus_lock = threading.Lock()

        # Focus timeout tracking
        self._focus_timeout_id: Optional[int] = None
        self._last_focus_time = 0

        # Rate limiting
        self._last_tab_creation = 0
        self._tab_creation_cooldown = 0.3  # 300ms cooldown

        # Callbacks
        self.on_tab_selected: Optional[Callable] = None
        self.on_tab_closed: Optional[Callable] = None
        self.on_tab_count_changed: Optional[Callable] = (
            None  # New callback for visibility management
        )

        # Statistics
        self._stats = {
            "tabs_created": 0,
            "tabs_closed": 0,
            "tabs_failed": 0,
            "focus_changes": 0,
        }

        self._setup_tab_components()
        # REMOVED: self._setup_terminal_title_bar_css()

        self.logger.info("Tab manager initialized")

    def _setup_tab_components(self) -> None:
        """Configure the tab view and bar components."""
        try:
            # Configure tab view
            self.tab_view.set_vexpand(True)
            self.tab_view.set_hexpand(True)

            # Connect tab selection signal
            self.tab_view.connect(
                "notify::selected-page", self._on_tab_selected_changed
            )
            self.tab_view.connect("page-attached", self._on_page_attached)
            self.tab_view.connect("page-detached", self._on_page_detached)

            # Configure tab bar
            self.tab_bar.set_view(self.tab_view)

            # Platform-specific configurations
            if is_windows():
                # Windows-specific tab configurations
                pass

            self.logger.debug("Tab components configured")

        except Exception as e:
            self.logger.error(f"Tab component setup failed: {e}")
            raise UIError("tab_setup", f"component configuration failed: {e}")

    def get_tab_view(self) -> Adw.TabView:
        """Get the TabView widget."""
        return self.tab_view

    def get_tab_bar(self) -> Adw.TabBar:
        """Get the TabBar widget."""
        return self.tab_bar

    def create_local_tab(self, title: str = "Local") -> Optional[Adw.TabPage]:
        """
        Create a new Local tab with rate limiting and error handling.

        Args:
            title: Tab title

        Returns:
            TabPage if created successfully, None otherwise
        """
        with self._creation_lock:
            try:
                # Rate limiting
                current_time = time.time()
                if current_time - self._last_tab_creation < self._tab_creation_cooldown:
                    self.logger.debug(f"Tab creation rate limited for '{title}'")
                    return None

                self._last_tab_creation = current_time

                self.logger.debug(f"Creating local tab: '{title}'")

                # Create terminal
                terminal = self.terminal_manager.create_local_terminal(title)
                if not terminal:
                    self._stats["tabs_failed"] += 1
                    raise TerminalError("Local creation failed", ErrorSeverity.HIGH)

                # Create tab page
                page = self._create_tab_for_terminal(terminal, title, "", "local")
                if page:
                    self._stats["tabs_created"] += 1
                    log_terminal_event("tab_created", title, "local tab")
                    self.logger.info(f"Local tab created successfully: '{title}'")

                    # Update tab bar visibility
                    self._update_tab_bar_visibility()

                return page

            except Exception as e:
                self._stats["tabs_failed"] += 1
                self.logger.error(f"Local tab creation failed for '{title}': {e}")
                handle_exception(e, f"local tab creation for {title}", "ashyterm.tabs")
                return None

    def create_ssh_tab(self, session: SessionItem) -> Optional[Adw.TabPage]:
        """
        Create a new SSH terminal tab with validation and error handling.

        Args:
            session: SessionItem with SSH configuration

        Returns:
            TabPage if created successfully, None otherwise
        """
        with self._creation_lock:
            try:
                # Rate limiting
                current_time = time.time()
                if current_time - self._last_tab_creation < self._tab_creation_cooldown:
                    self.logger.debug(
                        f"SSH tab creation rate limited for session '{session.name}'"
                    )
                    return None

                self._last_tab_creation = current_time

                self.logger.debug(f"Creating SSH tab for session: '{session.name}'")

                # Create SSH terminal
                terminal = self.terminal_manager.create_ssh_terminal(session)
                if not terminal:
                    self._stats["tabs_failed"] += 1
                    raise TerminalError(
                        f"SSH terminal creation failed for session {session.name}",
                        ErrorSeverity.HIGH,
                    )

                # Create tab page
                page = self._create_tab_for_terminal(
                    terminal, session.name, "network-server-symbolic", "ssh"
                )

                if page:
                    self._stats["tabs_created"] += 1
                    log_terminal_event(
                        "ssh_tab_created",
                        session.name,
                        f"SSH to {session.get_connection_string()}",
                    )
                    self.logger.info(f"SSH tab created successfully: '{session.name}'")

                    # Update tab bar visibility
                    self._update_tab_bar_visibility()

                return page

            except Exception as e:
                self._stats["tabs_failed"] += 1
                self.logger.error(
                    f"SSH tab creation failed for session '{session.name}': {e}"
                )
                handle_exception(
                    e, f"SSH tab creation for {session.name}", "ashyterm.tabs"
                )
                return None

    def _create_tab_for_terminal(
        self,
        terminal: Vte.Terminal,
        title: str,
        icon_name: Optional[str] = None,
        tab_type: str = "local",
    ) -> Optional[Adw.TabPage]:
        """
        Create a tab page for a terminal widget with comprehensive setup.
        """
        try:
            self.logger.debug(
                f"Creating tab page for terminal: '{title}' (type: {tab_type})"
            )

            # Adds scroll controller to decrease sensitivity
            scroll_controller = Gtk.EventControllerScroll()
            scroll_controller.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
            scroll_controller.connect("scroll", self._on_terminal_scroll)
            terminal.add_controller(scroll_controller)

            # Wrap terminal in scrolled window
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            scrolled_window.set_child(terminal)

            # Create tab page
            page = self.tab_view.add_page(scrolled_window, None)
            page.set_title(title)
            if icon_name:
                page.set_icon(Gio.ThemedIcon.new(icon_name))

            # Set tooltip with additional info
            if tab_type == "ssh":
                page.set_tooltip(f"SSH: {title}")
            else:
                page.set_tooltip(f"Local: {title}")

            # Register in registry
            tab_id = self.registry.register_tab(page, terminal, tab_type, title)

            # Store tab ID on page for reference - GTK4 compatibility
            page.tab_id = tab_id

            # Set as selected page
            self.tab_view.set_selected_page(page)

            # Focus terminal with proper delay
            self._schedule_terminal_focus(terminal, title)

            self.logger.debug(
                f"Tab page created successfully: '{title}' (ID: {tab_id})"
            )
            return page

        except Exception as e:
            self.logger.error(f"Tab page creation failed for '{title}': {e}")
            raise UIError("tab_creation", f"failed to create tab page: {e}")

    def _on_terminal_scroll(
        self, controller: Gtk.EventControllerScroll, dx: float, dy: float
    ) -> bool:
        """
        Handles terminal scroll events to reduce sensitivity.

        Args:
            controller: The scroll event controller.
            dx: The horizontal scroll delta.
            dy: The vertical scroll delta.

        Returns:
            True to stop the event from propagating further.
        """
        try:
            terminal = controller.get_widget()
            scrolled_window = terminal.get_parent()

            if not isinstance(scrolled_window, Gtk.ScrolledWindow):
                return Gdk.EVENT_PROPAGATE

            vadjustment = scrolled_window.get_vadjustment()
            if vadjustment:
                # Get the step increment (what one "unit" of scroll does)
                step = vadjustment.get_step_increment()

                # Calculate the new roll amount with the sensitivity factor
                scroll_amount = dy * step * self.SCROLL_SENSITIVITY_FACTOR

                # Apply the new scroll
                new_value = vadjustment.get_value() + scroll_amount
                vadjustment.set_value(new_value)

                # Prevents the original (very fast) scroll event from being processed
                return Gdk.EVENT_STOP
        except Exception as e:
            self.logger.warning(f"Error handling custom scroll: {e}")

        return Gdk.EVENT_PROPAGATE

    def _schedule_terminal_focus(self, terminal: Vte.Terminal, title: str) -> None:
        """
        Schedule terminal focus with proper error handling.

        Args:
            terminal: Terminal to focus
            title: Terminal title for logging
        """

        def focus_terminal():
            try:
                if terminal and terminal.get_realized():
                    terminal.grab_focus()
                    self.logger.debug(f"Terminal focused: '{title}'")
                    return False  # Remove from idle
                else:
                    # Terminal not ready, try again
                    return True
            except Exception as e:
                self.logger.warning(f"Terminal focus failed for '{title}': {e}")
                return False

        # Use timeout instead of idle_add for more predictable timing
        GLib.timeout_add(50, focus_terminal)

    def close_tab(self, page: Optional[Adw.TabPage] = None) -> bool:
        """
        Close a tab and clean up its terminal with enhanced safety.

        Args:
            page: TabPage to close (current tab if None)

        Returns:
            True if tab was closed successfully
        """
        with self._cleanup_lock:
            try:
                if page is None:
                    page = self.tab_view.get_selected_page()

                if not page:
                    self.logger.debug("No page to close")
                    return False

                # Get tab info before closing - GTK4 compatibility
                tab_id = getattr(page, "tab_id", None)
                terminal = self.registry.get_terminal_for_page(page)

                # Clean up terminal
                if terminal:
                    success = self.terminal_manager.remove_terminal(terminal)
                    if success:
                        self.logger.debug(f"Terminal cleaned up for tab ID: {tab_id}")

                # Unregister from registry
                self.registry.unregister_tab(page)

                # Close the tab
                self.tab_view.close_page(page)

                # Update statistics
                self._stats["tabs_closed"] += 1

                # Update tab bar visibility
                self._update_tab_bar_visibility()

                # Call callback if set
                if self.on_tab_closed:
                    self.on_tab_closed(page, terminal)

                self.logger.info(f"Tab closed successfully: ID={tab_id}")
                log_terminal_event("tab_closed", f"Tab {tab_id}", "user action")

                return True

            except Exception as e:
                self.logger.error(f"Tab close failed: {e}")
                handle_exception(e, "tab close", "ashyterm.tabs")
                return False

    def get_selected_page(self) -> Optional[Adw.TabPage]:
        """Get the currently selected tab page."""
        try:
            return self.tab_view.get_selected_page()
        except Exception as e:
            self.logger.error(f"Failed to get selected page: {e}")
            return None

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        """Get the terminal in the currently selected tab."""
        try:
            page = self.get_selected_page()
            if page:
                return self.registry.get_terminal_for_page(page)
            return None
        except Exception as e:
            self.logger.error(f"Failed to get selected terminal: {e}")
            return None

    def get_terminal_for_page(self, page: Adw.TabPage) -> Optional[Vte.Terminal]:
        """Get the terminal widget for a specific page."""
        return self.registry.get_terminal_for_page(page)

    def get_page_for_terminal(self, terminal: Vte.Terminal) -> Optional[Adw.TabPage]:
        """Get the tab page for a specific terminal."""
        return self.registry.get_page_for_terminal(terminal)

    def set_tab_title(self, page: Adw.TabPage, title: str) -> None:
        """
        Set the title of a tab with validation.

        Args:
            page: TabPage to update
            title: New title
        """
        try:
            if page and title:
                page.set_title(title)
                self.logger.debug(f"Tab title updated: '{title}'")
        except Exception as e:
            self.logger.error(f"Failed to set tab title: {e}")

    def get_tab_count(self) -> int:
        """Get the number of open tabs."""
        try:
            return self.tab_view.get_n_pages()
        except Exception as e:
            self.logger.error(f"Failed to get tab count: {e}")
            return 0

    def get_all_pages(self) -> List[Adw.TabPage]:
        """Get list of all tab pages."""
        try:
            pages = []
            for i in range(self.tab_view.get_n_pages()):
                page = self.tab_view.get_nth_page(i)
                if page:
                    pages.append(page)
            return pages
        except Exception as e:
            self.logger.error(f"Failed to get all pages: {e}")
            return []

    def get_all_terminals(self) -> List[Vte.Terminal]:
        """Get list of all terminals in tabs."""
        try:
            terminals = []
            for page in self.get_all_pages():
                terminal = self.registry.get_terminal_for_page(page)
                if terminal:
                    terminals.append(terminal)
            return terminals
        except Exception as e:
            self.logger.error(f"Failed to get all terminals: {e}")
            return []

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        """Splits the focused terminal horizontally."""
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        """Splits the focused terminal vertically."""
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        """Enhanced core logic for splitting a terminal pane, supporting nested splits with title bars."""
        with self._creation_lock:
            self.logger.debug(f"Splitting terminal (orientation: {orientation})")

            new_terminal = self.terminal_manager.create_local_terminal()
            if not new_terminal:
                raise UIError("split", "Failed to create a new terminal for the split.")

            new_terminal_title = "Local"
            new_pane = TerminalPaneWithTitleBar(new_terminal, new_terminal_title)
            new_pane.on_close_requested = self.close_pane

            # Find the widget to be replaced and its parent container
            widget_to_replace = None
            container = None

            current_widget = focused_terminal
            while current_widget:
                parent = current_widget.get_parent()
                if isinstance(parent, (Gtk.Paned, Adw.Bin)):
                    widget_to_replace = current_widget
                    container = parent
                    break
                current_widget = parent

            if not container or not widget_to_replace:
                self.logger.error("Cannot find a suitable container to split.")
                self.terminal_manager.remove_terminal(new_terminal)
                return

            # If the widget to replace is just the ScrolledWindow, it means this is the first split.
            # We need to wrap the existing terminal in a TerminalPaneWithTitleBar as well.
            if isinstance(widget_to_replace, Gtk.ScrolledWindow):
                page = self.get_page_for_terminal(focused_terminal)
                existing_title = page.get_title() if page else "Terminal"
                existing_pane = TerminalPaneWithTitleBar(
                    focused_terminal, existing_title
                )
                existing_pane.on_close_requested = self.close_pane
            else:
                # The widget to replace is already a TerminalPaneWithTitleBar
                existing_pane = widget_to_replace

            # Create new Paned for the split
            paned = Gtk.Paned(orientation=orientation)
            paned.set_start_child(existing_pane)
            paned.set_end_child(new_pane)

            # Replace the old widget with the new paned container
            if isinstance(container, Adw.Bin):
                container.set_child(paned)
            elif isinstance(container, Gtk.Paned):
                if container.get_start_child() == widget_to_replace:
                    container.set_start_child(paned)
                elif container.get_end_child() == widget_to_replace:
                    container.set_end_child(paned)
                else:
                    self.logger.error("Widget to replace not found in paned container")
                    self.terminal_manager.remove_terminal(new_terminal)
                    return
            else:
                self.logger.error(
                    f"Cannot split inside unknown container type: {type(container)}"
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            # Defer setting position to allow widgets to allocate size
            GLib.idle_add(lambda: self._set_paned_position(paned))

            self._schedule_terminal_focus(new_terminal, "New Split")
            log_terminal_event(
                "split",
                "Terminal",
                "horizontal"
                if orientation == Gtk.Orientation.HORIZONTAL
                else "vertical",
            )

            self.logger.info(
                f"Successfully created nested split with title bars, orientation {orientation.value_nick}"
            )

    def _set_paned_position(self, paned: Gtk.Paned) -> bool:
        """Set paned position to 50%."""
        try:
            orientation = paned.get_orientation()
            total_size = (
                paned.get_allocation().width
                if orientation == Gtk.Orientation.HORIZONTAL
                else paned.get_allocation().height
            )
            if total_size > 0:
                paned.set_position(total_size // 2)
        except Exception as e:
            self.logger.debug(f"Could not set paned position: {e}")
        return False  # Do not repeat

    def close_pane(self, focused_terminal: Vte.Terminal) -> None:
        """Closes the currently focused terminal pane, supporting nested splits with title bars."""
        with self._cleanup_lock:

            def do_close():
                page = self.get_page_for_terminal(focused_terminal)
                if not page:
                    return

                # If this is the last terminal in the tab, close the entire tab
                if len(self.get_all_terminals_in_page(page)) <= 1:
                    self.close_tab(page)
                    return

                # Find the terminal pane (could be in ScrolledWindow or TerminalPaneWithTitleBar)
                terminal_container = focused_terminal.get_parent()
                pane_container = None

                if isinstance(terminal_container, Gtk.ScrolledWindow):
                    # Terminal is in a scrolled window, find its parent
                    pane_container = terminal_container.get_parent()
                    if isinstance(pane_container, TerminalPaneWithTitleBar):
                        # Terminal is in a title bar pane
                        paned = pane_container.get_parent()
                    else:
                        # Terminal is in a regular scrolled window
                        paned = pane_container
                        pane_container = terminal_container
                else:
                    # Terminal might be directly in a pane
                    return

                if not isinstance(paned, Gtk.Paned):
                    self.logger.error("Terminal is not in a paned container")
                    return

                # Get the parent of the paned (for reconstruction)
                grandparent = paned.get_parent()
                if not grandparent:
                    return

                # Identify the other child to keep (the survivor)
                other_child = (
                    paned.get_start_child()
                    if paned.get_end_child() == pane_container
                    else paned.get_end_child()
                )

                if not other_child:
                    self.logger.error("No surviving child found in paned")
                    return

                # Clean up the terminal being removed BEFORE restructuring
                self.terminal_manager.remove_terminal(focused_terminal)
                log_terminal_event("pane_closed", "Terminal", "user action")

                # Remove the other child from the paned before destroying it
                paned.remove(other_child)

                # Replace the paned with the surviving child
                if isinstance(grandparent, Adw.Bin):
                    # This was the root split in the tab - remove title bars for single terminal
                    if isinstance(other_child, TerminalPaneWithTitleBar):
                        # Extract the terminal from the title bar pane and put it back in simple scrolled window
                        surviving_terminal = other_child.get_terminal()
                        other_child.scrolled_window.set_child(
                            None
                        )  # Remove terminal from pane

                        simple_scroller = Gtk.ScrolledWindow()
                        simple_scroller.set_policy(
                            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
                        )
                        simple_scroller.set_child(surviving_terminal)

                        grandparent.set_child(simple_scroller)
                        self.logger.debug(
                            "Restored single terminal to tab root without title bar"
                        )
                    else:
                        grandparent.set_child(other_child)
                        self.logger.debug("Restored single terminal to tab root")

                elif isinstance(grandparent, Gtk.Paned):
                    # This was a nested split - replace the paned with the survivor
                    if grandparent.get_start_child() == paned:
                        grandparent.set_start_child(other_child)
                        self.logger.debug("Replaced nested split in start position")
                    elif grandparent.get_end_child() == paned:
                        grandparent.set_end_child(other_child)
                        self.logger.debug("Replaced nested split in end position")
                    else:
                        self.logger.error("Paned not found in grandparent")
                        return

                elif isinstance(grandparent, Gtk.Box):
                    # Handle box container case (less common)
                    grandparent.remove(paned)
                    grandparent.append(other_child)
                    self.logger.debug("Replaced split in box container")

                else:
                    self.logger.error(f"Unknown grandparent type: {type(grandparent)}")
                    return

                # Focus the first terminal in the surviving subtree
                remaining_terminals = []
                self._find_terminals_recursive(other_child, remaining_terminals)
                if remaining_terminals:
                    remaining_terminals[0].grab_focus()
                    self.logger.debug("Focused remaining terminal after pane close")

                self.logger.info("Successfully closed nested split pane with title bar")

            GLib.idle_add(do_close)

    def _find_terminals_recursive(
        self, widget, terminals_list: List[Vte.Terminal]
    ) -> None:
        """Recursively find all VTE terminals in a widget tree, including in title bar panes."""
        if isinstance(widget, Vte.Terminal):
            terminals_list.append(widget)
        elif isinstance(widget, TerminalPaneWithTitleBar):
            terminals_list.append(widget.get_terminal())
        elif isinstance(widget, Gtk.Paned):
            start_child = widget.get_start_child()
            end_child = widget.get_end_child()
            if start_child:
                self._find_terminals_recursive(start_child, terminals_list)
            if end_child:
                self._find_terminals_recursive(end_child, terminals_list)
        elif hasattr(widget, "get_child") and widget.get_child():
            self._find_terminals_recursive(widget.get_child(), terminals_list)

    def get_all_terminals_in_page(self, page: Adw.TabPage) -> List[Vte.Terminal]:
        """Recursively find all terminals within a tab page, including in title bar panes."""
        terminals = []
        root_widget = page.get_child()

        def find_terminals(widget):
            if isinstance(widget, Vte.Terminal):
                terminals.append(widget)
            elif isinstance(widget, TerminalPaneWithTitleBar):
                terminals.append(widget.get_terminal())
            elif isinstance(widget, Gtk.Paned):
                find_terminals(widget.get_start_child())
                find_terminals(widget.get_end_child())
            elif hasattr(widget, "get_child") and widget.get_child():
                find_terminals(widget.get_child())

        if root_widget:
            find_terminals(root_widget)
        return terminals

    def update_terminal_title_in_splits(
        self, terminal: Vte.Terminal, new_title: str
    ) -> None:
        """Update terminal title in split panes when directory changes."""
        try:
            # Find if this terminal is in a title bar pane
            page = self.get_page_for_terminal(terminal)
            if not page:
                return

            # Search for the terminal in the page widget tree
            root_widget = page.get_child()
            self._update_terminal_title_recursive(root_widget, terminal, new_title)

        except Exception as e:
            self.logger.error(f"Failed to update terminal title in splits: {e}")

    def _update_terminal_title_recursive(
        self, widget, target_terminal: Vte.Terminal, new_title: str
    ) -> bool:
        """Recursively search for terminal and update its title bar."""
        if isinstance(widget, TerminalPaneWithTitleBar):
            if widget.get_terminal() == target_terminal:
                widget.set_title(new_title)
                return True
        elif isinstance(widget, Gtk.Paned):
            # Check both children
            start_child = widget.get_start_child()
            end_child = widget.get_end_child()
            if start_child and self._update_terminal_title_recursive(
                start_child, target_terminal, new_title
            ):
                return True
            if end_child and self._update_terminal_title_recursive(
                end_child, target_terminal, new_title
            ):
                return True
        elif hasattr(widget, "get_child") and widget.get_child():
            return self._update_terminal_title_recursive(
                widget.get_child(), target_terminal, new_title
            )

        return False

    def close_all_tabs(self) -> None:
        """Close all tabs quickly without complex cleanup."""
        try:
            self.logger.info("Force closing all tabs")

            # Cancel any timeouts immediately
            if self._focus_timeout_id is not None:
                GLib.source_remove(self._focus_timeout_id)
                self._focus_timeout_id = None

            # Force close without individual cleanup
            tab_count = self.tab_view.get_n_pages()

            # Close all pages at once - much faster
            while self.tab_view.get_n_pages() > 0:
                page = self.tab_view.get_nth_page(0)
                if page:
                    self.tab_view.close_page(page)

            self.logger.info(f"Force closed {tab_count} tabs")

        except Exception as e:
            self.logger.error(f"Force close tabs failed: {e}")
            # Don't let cleanup errors prevent shutdown

    def focus_terminal_in_current_tab(self) -> bool:
        """
        Focus the terminal in the currently selected tab.

        Returns:
            True if terminal was focused successfully
        """
        with self._focus_lock:
            try:
                terminal = self.get_selected_terminal()
                if terminal:
                    terminal.grab_focus()
                    self._stats["focus_changes"] += 1
                    return True
                return False
            except Exception as e:
                self.logger.error(f"Focus terminal failed: {e}")
                return False

    def _on_tab_selected_changed(self, tab_view, param) -> None:
        """Handle tab selection change with enhanced focus management."""
        try:
            page = self.tab_view.get_selected_page()

            if page:
                # Update registry focus tracking
                self.registry.update_tab_focus(page)

                terminal = self.registry.get_terminal_for_page(page)
                if terminal:
                    # Focus the terminal with proper timing
                    def focus_selected_terminal():
                        try:
                            if terminal.get_realized():
                                terminal.grab_focus()
                                self._stats["focus_changes"] += 1
                        except Exception as e:
                            self.logger.error(f"Focus selected terminal failed: {e}")
                        finally:
                            # CRÍTICO: Limpa o ID após a execução para evitar a condição de corrida
                            self._focus_timeout_id = None
                        return False  # Remove da fila de eventos

                    # Cancel any previous timeout that hasn't run yet
                    if self._focus_timeout_id is not None:
                        GLib.source_remove(self._focus_timeout_id)
                        self._focus_timeout_id = None  # Garante que está limpo

                    self._focus_timeout_id = GLib.timeout_add(
                        50, focus_selected_terminal
                    )

            # Call external callback if set
            if self.on_tab_selected:
                self.on_tab_selected(page)

        except Exception as e:
            self.logger.error(f"Tab selection change handling failed: {e}")

    def _on_page_attached(self, tab_view, page, position) -> None:
        """Handle page being attached to tab view."""
        try:
            self.logger.debug(f"Page attached at position {position}")
        except Exception as e:
            self.logger.error(f"Page attached handling failed: {e}")

    def _on_page_detached(self, tab_view, page, position) -> None:
        """Handle page being detached from tab view."""
        try:
            self.logger.debug(f"Page detached from position {position}")
        except Exception as e:
            self.logger.error(f"Page detached handling failed: {e}")

    def create_initial_tab_if_empty(self) -> Optional[Adw.TabPage]:
        """Create an initial local tab if no tabs exist."""
        try:
            if self.get_tab_count() == 0:
                self.logger.debug("Creating initial tab - no tabs exist")
                result = self.create_local_tab("Local")
                if result:
                    self.logger.info("Initial tab created successfully")
                else:
                    self.logger.error("Failed to create initial tab")
                return result
            return None
        except Exception as e:
            self.logger.error(f"Initial tab creation failed: {e}")
            handle_exception(e, "initial tab creation", "ashyterm.tabs")
            return None

    def select_next_tab(self) -> bool:
        """Select the next tab in the tab view."""
        try:
            current_page = self.get_selected_page()
            if not current_page:
                return False

            # Find current page index
            for i in range(self.tab_view.get_n_pages()):
                if self.tab_view.get_nth_page(i) == current_page:
                    # Select next tab (wrap around)
                    next_index = (i + 1) % self.tab_view.get_n_pages()
                    next_page = self.tab_view.get_nth_page(next_index)
                    if next_page:
                        self.tab_view.set_selected_page(next_page)
                        return True
                    break

            return False

        except Exception as e:
            self.logger.error(f"Select next tab failed: {e}")
            return False

    def select_previous_tab(self) -> bool:
        """Select the previous tab in the tab view."""
        try:
            current_page = self.get_selected_page()
            if not current_page:
                return False

            # Find current page index
            for i in range(self.tab_view.get_n_pages()):
                if self.tab_view.get_nth_page(i) == current_page:
                    # Select previous tab (wrap around)
                    prev_index = (i - 1) % self.tab_view.get_n_pages()
                    prev_page = self.tab_view.get_nth_page(prev_index)
                    if prev_page:
                        self.tab_view.set_selected_page(prev_page)
                        return True
                    break

            return False

        except Exception as e:
            self.logger.error(f"Select previous tab failed: {e}")
            return False

    def copy_from_current_terminal(self) -> bool:
        """Copy selection from current terminal."""
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                return self.terminal_manager.copy_selection(terminal)
            return False
        except Exception as e:
            self.logger.error(f"Copy from current terminal failed: {e}")
            return False

    def paste_to_current_terminal(self) -> bool:
        """Paste to current terminal."""
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                return self.terminal_manager.paste_clipboard(terminal)
            return False
        except Exception as e:
            self.logger.error(f"Paste to current terminal failed: {e}")
            return False

    def select_all_in_current_terminal(self) -> None:
        """Select all text in current terminal."""
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                self.terminal_manager.select_all(terminal)
        except Exception as e:
            self.logger.error(f"Select all in current terminal failed: {e}")

    def zoom_in_current_terminal(self, step: float = 0.1) -> bool:
        """
        Zoom in current terminal.

        Args:
            step: Zoom step increment

        Returns:
            True if zoom was successful
        """
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                return self.terminal_manager.zoom_in(terminal, step)
            return False
        except Exception as e:
            self.logger.error(f"Zoom in current terminal failed: {e}")
            return False

    def zoom_out_current_terminal(self, step: float = 0.1) -> bool:
        """
        Zoom out current terminal.

        Args:
            step: Zoom step decrement

        Returns:
            True if zoom was successful
        """
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                return self.terminal_manager.zoom_out(terminal, step)
            return False
        except Exception as e:
            self.logger.error(f"Zoom out current terminal failed: {e}")
            return False

    def zoom_reset_current_terminal(self) -> bool:
        """
        Reset zoom in current terminal.

        Returns:
            True if reset was successful
        """
        try:
            terminal = self.get_selected_terminal()
            if terminal:
                return self.terminal_manager.zoom_reset(terminal)
            return False
        except Exception as e:
            self.logger.error(f"Zoom reset current terminal failed: {e}")
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get tab manager statistics.

        Returns:
            Dictionary with statistics
        """
        try:
            stats = self._stats.copy()
            stats.update(self.registry.get_statistics())
            stats.update({
                "platform": self.platform_info.platform_type.value,
                "current_tabs": self.get_tab_count(),
            })
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {"error": str(e)}

    def _update_tab_bar_visibility(self) -> None:
        """Update tab bar visibility based on tab count."""
        try:
            tab_count = self.get_tab_count()
            should_show = tab_count > 1

            # Update tab bar visibility
            self.tab_bar.set_visible(should_show)

            # Call callback if set (to update window title, etc.)
            if self.on_tab_count_changed:
                self.on_tab_count_changed(tab_count, should_show)

            self.logger.debug(
                f"Tab bar visibility updated: {should_show} (count: {tab_count})"
            )

        except Exception as e:
            self.logger.error(f"Tab bar visibility update failed: {e}")

    def cleanup(self) -> None:
        """Perform cleanup of tab manager resources."""
        with self._cleanup_lock:
            try:
                self.logger.debug("Starting tab manager cleanup")
                
                # Cancel pending timeouts
                if self._focus_timeout_id is not None:
                    GLib.source_remove(self._focus_timeout_id)
                    self._focus_timeout_id = None
                
                # Close all tabs
                self.close_all_tabs()
                
                self.logger.info("Tab manager cleanup completed")
                
            except Exception as e:
                self.logger.error(f"Tab manager cleanup failed: {e}")