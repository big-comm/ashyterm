# ashyterm/ui/broadcast_manager.py
from typing import TYPE_CHECKING, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GObject, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.syntax_utils import get_bash_pango_markup
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class BroadcastManager(GObject.Object):
    """
    Manages broadcasting commands to multiple terminals, decoupling it from the main window.
    """

    def __init__(self, window: "CommTerminalWindow"):
        super().__init__()
        self.window = window
        self.ui = window.ui_builder
        self.tab_manager = window.tab_manager
        self.logger = window.logger

        # State
        self.remember_choice = False
        self.last_selection = []

        self._setup_connections()

    def _setup_connections(self):
        """Connect UI signals to manager methods."""
        self.ui.broadcast_entry.connect("activate", self._on_broadcast_activate)

        # Controller for Escape key
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_key_pressed)
        self.ui.broadcast_bar.add_controller(controller)

        self.ui.broadcast_bar.connect(
            "notify::search-mode-enabled", self._on_broadcast_mode_changed
        )

    def _on_broadcast_mode_changed(self, broadcast_bar, _param):
        if broadcast_bar.get_search_mode():
            self.ui.broadcast_entry.grab_focus()
        else:
            if terminal := self.tab_manager.get_selected_terminal():
                terminal.grab_focus()

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.ui.broadcast_bar.set_search_mode(False)
            return True
        return False

    def _on_broadcast_activate(self, entry: Gtk.Entry):
        """Handle Enter key in broadcast entry."""
        command = entry.get_text().strip()
        if not command:
            return

        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("No open terminals found."))
            )
            return

        if self.remember_choice and self.last_selection:
            selected = []
            for t in all_terminals:
                if self._make_terminal_key(t) in self.last_selection:
                    selected.append(t)

            if selected:
                self.execute_broadcast(command, selected)
                entry.set_text("")
                self.ui.broadcast_bar.set_search_mode(False)
                return

        self.show_confirmation_dialog(command, all_terminals)

    def show_confirmation_dialog(self, command: str, all_terminals: List[Vte.Terminal]):
        """Show the confirmation dialog for broadcasting."""
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=_("Confirm sending of command"),
            body=_(
                "Select which of the <b>{count}</b> open terminals should receive the command below."
            ).format(count=len(all_terminals)),
            body_use_markup=True,
            close_response="cancel",
        )

        highlighted = get_bash_pango_markup(command)
        command_label = Gtk.Label(
            label=f"<tt>{highlighted}</tt>",
            use_markup=True,
            css_classes=["card"],
            halign=Gtk.Align.CENTER,
            margin_top=6,
            margin_bottom=6,
            margin_start=8,
            margin_end=8,
        )

        flow_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            row_spacing=6,
            column_spacing=12,
            min_children_per_line=max(1, min(3, len(all_terminals))),
            max_children_per_line=3,
        )

        selection_controls = []
        for t in all_terminals:
            btn = Gtk.CheckButton(label=self._get_display_name(t))
            btn.set_active(True)
            flow_box.insert(btn, -1)
            selection_controls.append((t, btn))

        container = flow_box
        if len(selection_controls) > 6:
            scrolled = Gtk.ScrolledWindow(
                vexpand=True, hexpand=True, min_content_height=200
            )
            scrolled.set_child(flow_box)
            container = scrolled

        remember_check = Gtk.CheckButton(label=_("Remember my choice"))
        remember_check.set_active(self.remember_choice)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_all=12
        )
        content.append(command_label)
        content.append(
            Gtk.Label(
                label=_("Choose the tabs that should run this command:"),
                halign=Gtk.Align.START,
            )
        )
        content.append(container)
        content.append(remember_check)
        dialog.set_extra_child(content)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("send", _("Send Command"))
        dialog.set_default_response("send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect(
            "response",
            self._on_dialog_response,
            command,
            selection_controls,
            remember_check,
        )
        dialog.present()

    def _on_dialog_response(
        self, dialog, response_id, command, controls, remember_check
    ):
        if response_id == "send":
            selected = [t for t, ctrl in controls if ctrl.get_active()]
            self.remember_choice = remember_check.get_active()
            if self.remember_choice:
                self.last_selection = [self._make_terminal_key(t) for t in selected]
            else:
                self.last_selection = []

            if not selected:
                self.window.toast_overlay.add_toast(
                    Adw.Toast(title=_("No tabs were selected."))
                )
            else:
                self.execute_broadcast(command, selected)

        dialog.close()
        self.ui.broadcast_entry.set_text("")
        self.ui.broadcast_bar.set_search_mode(False)

    def execute_broadcast(self, command: str, terminals: List[Vte.Terminal]):
        """Execute the command on multiple terminals."""
        cmd_bytes = command.encode("utf-8") + b"\n"
        for t in terminals:
            t.feed_child(cmd_bytes)
        self.logger.info(f"Broadcasted command to {len(terminals)} terminals.")

    def _get_display_name(self, terminal: Vte.Terminal) -> str:
        page = self.tab_manager.get_page_for_terminal(terminal)
        if page and page.get_title():
            return page.get_title()

        t_id = getattr(terminal, "terminal_id", "?")
        return _("Terminal {id}").format(id=t_id)

    def _make_terminal_key(self, terminal: Vte.Terminal) -> str:
        session = getattr(terminal, "ashy_session_item", None)
        if isinstance(session, SessionItem):
            return f"{session.session_type}:{session.name}:{session.host}:{session.user}:{session.port}"

        page = self.tab_manager.get_page_for_terminal(terminal)
        return page.get_title() if page else str(getattr(terminal, "terminal_id", ""))

    def broadcast_to_all(self, command_text: str):
        """Directly broadcast to all terminals (e.g. from Command Manager)."""
        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("No open terminals found."))
            )
            return

        # Simple broadcast without confirmation if coming from Command Manager
        self.execute_broadcast(command_text, all_terminals)
