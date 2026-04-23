# ashyterm/terminal/tab_restore_controller.py
"""Recreate tabs and pane layouts from serialized structures.

``state/window_state.py`` persists each open tab as a dict tree of
terminal/paned nodes. On startup it hands those dicts back here via
``TabManager.recreate_tab_from_structure``. This module owns the
walker that turns them into widgets again, plus the bulk-close path
used on shutdown.

All GTK mutation is funneled through a reference to the owning
``TabManager`` so the data flow stays visible from one file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .tabs import TabManager


class TabRestoreController:
    """Reconstruct tab layouts from serialized state dictionaries."""

    def __init__(self, manager: "TabManager") -> None:
        self.manager = manager
        self.logger = get_logger("ashyterm.tabs.restore")

    # ── public API ───────────────────────────────────────────

    def recreate_tab_from_structure(self, structure: dict) -> None:
        """Recreate a complete tab (including splits) from a saved structure."""
        if not structure:
            return

        root_widget = self.recreate_widget_from_node(structure)
        if not root_widget:
            self.logger.error("Failed to create root widget for tab restoration.")
            return

        terminal_area_content = self.unwrap_toolbar_view(root_widget)
        _, content_paned = self.build_tab_content_paned(terminal_area_content)

        terminals: list = []
        self.manager.pane_handler.find_terminals_recursive(root_widget, terminals)

        if not terminals:
            self.logger.error("Restored tab contains no terminals.")
            return

        first_terminal = terminals[0]
        session = self.session_from_terminal(first_terminal)

        page_name = f"page_restored_{GLib.random_int()}"
        page = self.manager.view_stack.add_titled(
            content_paned, page_name, session.name
        )
        page.content_paned = content_paned
        for term in terminals:
            term.ashy_parent_page = page

        tab_widget = self.manager._create_tab_widget(page, session)
        self.manager.tabs.append(tab_widget)
        self.manager.pages[tab_widget] = page
        self.manager.tab_bar_box.append(tab_widget)

        self.manager.set_active_tab(tab_widget)
        self.manager._schedule_terminal_focus(first_terminal)
        self.manager.update_all_tab_titles()

        if self.manager.on_tab_count_changed:
            self.manager.on_tab_count_changed()

    def close_all_tabs(self) -> None:
        """Close every open tab by firing the close button handler."""
        for tab_widget in self.manager.tabs[:]:
            self.manager._on_tab_close_button_clicked(None, tab_widget)

    # ── helpers (kept public so tests can exercise them) ─────

    def unwrap_toolbar_view(self, root_widget: Gtk.Widget) -> Gtk.Widget:
        """Unwrap an ``Adw.ToolbarView`` to the inner scrolled window."""
        if isinstance(root_widget, Adw.ToolbarView):
            scrolled_win = root_widget.get_content()
            if scrolled_win:
                root_widget.set_content(None)
                return scrolled_win
        return root_widget

    def build_tab_content_paned(
        self, terminal_area_content: Gtk.Widget
    ) -> tuple[Adw.Bin, Gtk.Paned]:
        """Build the standard two-pane tab skeleton (terminal + bottom area)."""
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

    def session_from_terminal(self, terminal: Vte.Terminal) -> SessionItem:
        """Return a ``SessionItem`` describing the session backing a terminal."""
        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.manager.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            return identifier
        return SessionItem(name=str(identifier), session_type="local")

    # ── node walker ──────────────────────────────────────────

    def recreate_widget_from_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Dispatch on ``node["type"]`` to rebuild the matching widget."""
        if not node or "type" not in node:
            return None

        node_type = node["type"]
        if node_type == "terminal":
            return self.recreate_terminal_node(node)
        if node_type == "paned":
            return self.recreate_paned_node(node)
        return None

    def recreate_terminal_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Rebuild a single terminal pane from a serialized node."""
        # Lazy import to keep module-level import graph flat.
        from .tabs import _create_terminal_pane

        working_dir = node.get("working_dir")
        initial_command = (
            f'cd "{working_dir}"'
            if working_dir and node["session_type"] == "ssh"
            else None
        )
        title = node.get("session_name", "Terminal")
        session_type = node.get("session_type", "local")

        terminal = self.create_terminal_from_session(
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
            self.manager.close_pane,
            self.manager._on_move_to_tab_callback,
            self.manager.terminal_manager.settings_manager,
        )
        sw = pane_widget.get_content()
        if isinstance(sw, Gtk.ScrolledWindow):
            self.manager._replace_sw_scroll_controller(sw)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self.manager._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        return pane_widget

    def create_terminal_from_session(
        self,
        session_type: str,
        session_name: str,
        title: str,
        working_dir: Optional[str],
        initial_command: Optional[str],
    ) -> Optional[Vte.Terminal]:
        """Look up the saved session and spawn the matching terminal."""
        store = self.manager.terminal_manager.parent_window.session_store

        if session_type == "ssh":
            session = next(
                (s for s in store if s.name == session_name),
                None,
            )
            if session and session.is_ssh():
                return self.manager.terminal_manager.create_ssh_terminal(
                    session, initial_command=initial_command
                )
            self.logger.warning(
                f"Could not find SSH session '{session_name}' to restore, "
                "or type mismatch."
            )
            return self.manager.terminal_manager.create_local_terminal(
                title=f"Missing: {title}"
            )

        # Local session
        session = next(
            (s for s in store if s.name == session_name and s.is_local()),
            None,
        )
        return self.manager.terminal_manager.create_local_terminal(
            session=session, title=title, working_directory=working_dir
        )

    def recreate_paned_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Rebuild a split pane from a serialized node."""
        orientation = (
            Gtk.Orientation.HORIZONTAL
            if node["orientation"] == "horizontal"
            else Gtk.Orientation.VERTICAL
        )
        paned = Gtk.Paned(orientation=orientation)

        child1 = self.recreate_widget_from_node(node["child1"])
        child2 = self.recreate_widget_from_node(node["child2"])

        if not child1 or not child2:
            self.logger.error("Failed to recreate children for a split pane.")
            if child1:
                self.find_and_remove_terminals(child1)
            if child2:
                self.find_and_remove_terminals(child2)
            return None

        paned.set_start_child(child1)
        paned.set_end_child(child2)

        ratio = node.get("position_ratio", 0.5)
        GLib.idle_add(
            self.manager.pane_handler.set_paned_position_from_ratio, paned, ratio
        )

        return paned

    def find_and_remove_terminals(self, widget: Gtk.Widget) -> None:
        """Collect every terminal in ``widget`` and unregister them."""
        terminals: list = []
        self.manager.pane_handler.find_terminals_recursive(widget, terminals)
        for term in terminals:
            self.manager.terminal_manager.remove_terminal(term)
