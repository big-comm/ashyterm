# ashyterm/terminal/pane_manager.py
"""Pane / split management delegate for TabManager.

Handles horizontal and vertical splits, pane closing, reparenting,
and moving panes to new tabs.
"""

from typing import TYPE_CHECKING, List, Optional

from gi.repository import Adw, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .tabs import TabManager


class PaneManager:
    """Manages pane splitting, closing, and reparenting logic."""

    def __init__(self, tab_manager: "TabManager") -> None:
        self.tm = tab_manager
        self.logger = get_logger("ashyterm.tabs.pane")

    # -- tree helpers ---------------------------------------------------------

    def find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        """Walks up the widget tree to find a terminal's pane and its parent container."""
        widget = terminal
        while widget:
            parent = widget.get_parent()
            if isinstance(parent, (Gtk.Paned, Adw.Bin)):
                return widget, parent
            widget = parent
        return None, None

    def find_terminals_recursive(
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
                self.find_terminals_recursive(start_child, terminals_list)
            if end_child := widget.get_end_child():
                self.find_terminals_recursive(end_child, terminals_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self.find_terminals_recursive(child, terminals_list)

    def find_panes_recursive(
        self, widget, panes_list: List[Adw.ToolbarView]
    ) -> None:
        """Recursively find all ToolbarView panes within a container."""
        if isinstance(widget, Adw.ToolbarView):
            panes_list.append(widget)
            return
        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self.find_panes_recursive(start_child, panes_list)
            if end_child := widget.get_end_child():
                self.find_panes_recursive(end_child, panes_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self.find_panes_recursive(child, panes_list)

    def get_first_terminal_in_widget(self, widget) -> Optional[Vte.Terminal]:
        """Find the first terminal in a widget hierarchy."""
        terminals: List[Vte.Terminal] = []
        self.find_terminals_recursive(widget, terminals)
        return terminals[0] if terminals else None

    # -- close / remove -------------------------------------------------------

    def remove_pane_ui(self, pane_to_remove, parent_paned) -> None:
        """Remove a pane from a Gtk.Paned and reparent the survivor."""
        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning(
                f"Attempted to remove pane from a non-paned container: {type(parent_paned)}"
            )
            return

        survivor_pane = self._get_survivor_pane(pane_to_remove, parent_paned)
        if not survivor_pane:
            return

        grandparent = parent_paned.get_parent()
        if not grandparent:
            return

        survivor_terminal = self.get_first_terminal_in_widget(survivor_pane)
        self._clear_paned_children(parent_paned)
        self._reparent_survivor(survivor_pane, parent_paned, grandparent)
        self.schedule_focus_restore(survivor_terminal)

    def close_pane(self, terminal: Vte.Terminal) -> None:
        """Close a single pane within a tab."""
        self.tm.terminal_manager.remove_terminal(terminal)

    def on_move_to_tab_callback(self, terminal: Vte.Terminal) -> None:
        """Callback to move a terminal from a split pane to a new tab."""
        self.logger.info(f"Request to move terminal {terminal.terminal_id} to new tab.")
        pane_to_remove, parent_paned = self.find_pane_and_parent(terminal)

        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning("Attempted to move a pane that is not in a split.")
            if hasattr(self.tm.terminal_manager.parent_window, "toast_overlay"):
                toast = Adw.Toast(title=_("This is the only pane in the tab."))
                self.tm.terminal_manager.parent_window.toast_overlay.add_toast(toast)
            return

        current_parent = terminal.get_parent()
        if current_parent and hasattr(current_parent, "set_child"):
            current_parent.set_child(None)

        self.remove_pane_ui(pane_to_remove, parent_paned)

        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.tm.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            session = identifier
        else:
            session = SessionItem(name=str(identifier), session_type="local")

        self.tm._create_tab_for_terminal(terminal, session)
        self.logger.info(f"Terminal {terminal_id} successfully moved to a new tab.")

    # -- split ----------------------------------------------------------------

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def set_paned_position_from_ratio(self, paned: Gtk.Paned, ratio: float) -> bool:
        alloc = paned.get_allocation()
        total_size = (
            alloc.width
            if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
            else alloc.height
        )
        if total_size > 0:
            paned.set_position(int(total_size * ratio))
        return False

    def schedule_focus_restore(self, terminal: Optional[Vte.Terminal]) -> None:
        """Schedule focus restoration and title update."""

        def _restore_focus_and_update_titles():
            if terminal and terminal.get_realized():
                terminal.grab_focus()
            self.tm.update_all_tab_titles()
            return False

        GLib.idle_add(_restore_focus_and_update_titles)

    # -- internal helpers -----------------------------------------------------

    def _get_survivor_pane(self, pane_to_remove, parent_paned: Gtk.Paned):
        """Get the pane that survives after removing another pane."""
        is_start_child = parent_paned.get_start_child() == pane_to_remove
        return (
            parent_paned.get_end_child()
            if is_start_child
            else parent_paned.get_start_child()
        )

    def _clear_paned_children(self, paned: Gtk.Paned) -> None:
        """Clear all children from a paned widget."""
        paned.set_focus_child(None)
        paned.set_start_child(None)
        paned.set_end_child(None)

    def _reparent_survivor(self, survivor_pane, parent_paned, grandparent) -> None:
        """Reparent the surviving pane to the grandparent container."""
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

    def _get_terminal_identifier_and_title(self, terminal: Vte.Terminal):
        """Returns the identifier and title for a terminal being split."""
        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.tm.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"
        pane_title = identifier.name if isinstance(identifier, SessionItem) else "Local"
        return identifier, pane_title

    def _create_split_terminal(self, identifier, pane_title: str):
        """Creates a new terminal for splitting based on the identifier type."""
        if isinstance(identifier, SessionItem):
            if identifier.is_ssh():
                return self.tm.terminal_manager.create_ssh_terminal(identifier)
            else:
                effective_working_dir = (
                    getattr(identifier, "local_working_directory", None) or None
                )
                effective_command = (
                    getattr(identifier, "local_startup_command", None) or None
                )
                return self.tm.terminal_manager.create_local_terminal(
                    session=identifier,
                    working_directory=effective_working_dir,
                    execute_command=effective_command,
                )
        return self.tm.terminal_manager.create_local_terminal(title=pane_title)

    def _create_pane_for_split(self, terminal: Vte.Terminal, title: str):
        """Creates a new pane for a split terminal."""
        from .tabs import _create_terminal_pane

        new_pane = _create_terminal_pane(
            terminal,
            title,
            self.close_pane,
            self.on_move_to_tab_callback,
            self.tm.terminal_manager.settings_manager,
        )
        sw = new_pane.get_content()
        if isinstance(sw, Gtk.ScrolledWindow):
            self.tm.scroll_handler.replace_sw_scroll_controller(sw)
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self.tm._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)
        return new_pane

    def _prepare_pane_for_split(self, pane_to_replace, focused_terminal):
        """Prepares the existing pane for splitting, wrapping if necessary."""
        from .tabs import _create_terminal_pane

        if isinstance(pane_to_replace, Gtk.ScrolledWindow):
            uri = focused_terminal.get_current_directory_uri()
            title = "Terminal"
            if uri:
                from urllib.parse import unquote, urlparse

                path = unquote(urlparse(uri).path)
                title = self.tm.terminal_manager.osc7_tracker.parser._create_display_path(
                    path
                )
            pane_to_replace.set_child(None)
            wrapped = _create_terminal_pane(
                focused_terminal,
                title,
                self.close_pane,
                self.on_move_to_tab_callback,
                self.tm.terminal_manager.settings_manager,
            )
            sw = wrapped.get_content()
            if isinstance(sw, Gtk.ScrolledWindow):
                self.tm.scroll_handler.replace_sw_scroll_controller(sw)
            return wrapped
        return pane_to_replace

    def _insert_split_paned(
        self,
        container,
        pane_to_replace,
        pane_being_split,
        new_pane,
        orientation,
        new_terminal,
    ) -> bool:
        """Inserts the new split paned into the container. Returns True on success."""
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
            self.tm.terminal_manager.remove_terminal(new_terminal)
            return False

        GLib.idle_add(self.set_paned_position_from_ratio, new_split_paned, 0.5)
        self.tm._schedule_terminal_focus(new_terminal)
        return True

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        page = self.tm.get_page_for_terminal(focused_terminal)
        if not page:
            self.logger.error("Cannot split: could not find parent page.")
            return

        identifier, pane_title = self._get_terminal_identifier_and_title(
            focused_terminal
        )

        if isinstance(identifier, SessionItem) and identifier.is_ssh():
            self._show_split_type_dialog(
                focused_terminal, orientation, identifier, pane_title, page
            )
            return

        self._perform_split(focused_terminal, orientation, identifier, pane_title, page)

    def _show_split_type_dialog(
        self,
        focused_terminal: Vte.Terminal,
        orientation: Gtk.Orientation,
        identifier: SessionItem,
        pane_title: str,
        page,
    ) -> None:
        """Show dialog asking whether the new split pane should be SSH or Local."""
        dialog = Adw.AlertDialog(
            heading=_("Split Terminal"),
            body=_("Open the new pane as SSH or Local terminal?"),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("local", _("Local"))
        dialog.add_response("ssh_list", _("SSH from List"))
        dialog.add_response("ssh", _("SSH"))
        dialog.set_response_appearance("ssh", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ssh")

        def on_response(_dialog, response_id):
            if response_id == "cancel":
                return
            if response_id == "local":
                self._perform_split(
                    focused_terminal, orientation, "Local", "Local", page
                )
            elif response_id == "ssh_list":
                self._show_ssh_session_picker(
                    focused_terminal, orientation, page
                )
            else:
                self._perform_split(
                    focused_terminal, orientation, identifier, pane_title, page
                )

        dialog.connect("response", on_response)
        dialog.present(self.tm.terminal_manager.parent_window)

    def _show_ssh_session_picker(
        self,
        focused_terminal: Vte.Terminal,
        orientation: Gtk.Orientation,
        page,
    ) -> None:
        """Show a filterable list of saved SSH sessions to choose from for the split."""
        parent_window = self.tm.terminal_manager.parent_window
        session_store = getattr(parent_window, "session_store", None)
        if not session_store:
            self.logger.warning("No session store available for SSH session picker.")
            return

        ssh_sessions = []
        for i in range(session_store.get_n_items()):
            item = session_store.get_item(i)
            if item.is_ssh():
                ssh_sessions.append(item)

        if not ssh_sessions:
            toast = Adw.Toast(title=_("No saved SSH sessions found."))
            if hasattr(parent_window, "toast_overlay"):
                parent_window.toast_overlay.add_toast(toast)
            return

        dialog = Adw.Window(
            transient_for=parent_window,
            modal=True,
            default_width=400,
            default_height=450,
            title=_("Choose SSH Session"),
        )

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)
        content_box.set_margin_top(8)
        content_box.set_margin_bottom(12)

        search_entry = Gtk.SearchEntry(placeholder_text=_("Filter sessions…"))
        content_box.append(search_entry)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("boxed-list")
        scrolled.set_child(listbox)
        content_box.append(scrolled)

        rows_data = []
        for session in ssh_sessions:
            row = Adw.ActionRow(
                title=session.name,
                subtitle=f"{session.user}@{session.host}",
                activatable=True,
            )
            listbox.append(row)
            rows_data.append((row, session))

        def on_filter_changed(entry):
            text = entry.get_text().lower()
            for row, session in rows_data:
                visible = (
                    text in session.name.lower()
                    or text in session.host.lower()
                    or text in session.user.lower()
                )
                row.set_visible(visible)

        search_entry.connect("search-changed", on_filter_changed)

        def on_row_activated(_listbox, row):
            for r, session in rows_data:
                if r is row:
                    dialog.close()
                    self._perform_split(
                        focused_terminal, orientation, session, session.name, page
                    )
                    return

        listbox.connect("row-activated", on_row_activated)

        toolbar_view.set_content(content_box)
        dialog.set_content(toolbar_view)
        dialog.present()

    def _perform_split(
        self,
        focused_terminal: Vte.Terminal,
        orientation: Gtk.Orientation,
        identifier,
        pane_title: str,
        page,
    ) -> None:
        """Execute the actual split after type has been determined."""
        with self.tm._creation_lock:
            new_terminal = self._create_split_terminal(identifier, pane_title)
            if not new_terminal:
                self.logger.error("Failed to create new terminal for split.")
                return

            new_terminal.ashy_parent_page = page
            new_pane = self._create_pane_for_split(new_terminal, pane_title)

            pane_to_replace, container = self.find_pane_and_parent(focused_terminal)
            if not pane_to_replace:
                self.logger.error("Could not find the pane to replace for splitting.")
                self.tm.terminal_manager.remove_terminal(new_terminal)
                return

            pane_being_split = self._prepare_pane_for_split(
                pane_to_replace, focused_terminal
            )

            success = self._insert_split_paned(
                container,
                pane_to_replace,
                pane_being_split,
                new_pane,
                orientation,
                new_terminal,
            )
            if not success:
                return

            self.tm.update_all_tab_titles()

    def find_and_remove_terminals(self, widget: Gtk.Widget) -> None:
        """Finds all terminals in a widget tree and removes them."""
        terminals: List[Vte.Terminal] = []
        self.find_terminals_recursive(widget, terminals)
        for term in terminals:
            self.tm.terminal_manager.remove_terminal(term)
