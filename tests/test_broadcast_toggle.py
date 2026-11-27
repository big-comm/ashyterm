"""Ctrl+Shift+B / "Broadcast to All Tabs" must open the broadcast bar."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ashyterm.ui.actions import WindowActions
from ashyterm.ui.broadcast_manager import BroadcastManager


class _Window:
    """Minimal stand-in for CommTerminalWindow (weakref-able)."""

    def __init__(self):
        self.ui_builder = SimpleNamespace(
            broadcast_button=Gtk.ToggleButton(),
            broadcast_bar=Gtk.SearchBar(),
            broadcast_entry=Gtk.Entry(),
        )
        self.broadcast_button = self.ui_builder.broadcast_button
        self.tab_manager = MagicMock()
        self.tab_manager.get_selected_terminal.return_value = None
        self.logger = MagicMock()

    def add_css_class(self, _name):
        pass

    def remove_css_class(self, _name):
        pass


def _setup():
    window = _Window()
    manager = BroadcastManager(window)
    return window, manager


def test_toggle_broadcast_action_opens_and_closes_the_bar():
    window, _manager = _setup()
    bar = window.ui_builder.broadcast_bar
    actions = WindowActions(window)

    actions.toggle_broadcast()
    assert bar.get_search_mode() is True

    actions.toggle_broadcast()
    assert bar.get_search_mode() is False


def test_closing_the_bar_resets_the_toggle():
    # Escape and successful sends close the bar directly; the next
    # Ctrl+Shift+B must open it again instead of being swallowed.
    window, _manager = _setup()
    bar = window.ui_builder.broadcast_bar
    actions = WindowActions(window)

    actions.toggle_broadcast()
    bar.set_search_mode(False)
    assert window.broadcast_button.get_active() is False

    actions.toggle_broadcast()
    assert bar.get_search_mode() is True


def test_confirmation_dialog_builds_and_is_presented(monkeypatch):
    # Enter in the broadcast bar opens this dialog; it used to raise
    # TypeError (GtkBox has no "margin_all" property) before showing.
    presented = []
    monkeypatch.setattr(
        Adw.AlertDialog, "present", lambda self, parent: presented.append(self)
    )
    window, manager = _setup()
    window.tab_manager.get_page_for_terminal.return_value = None
    terminals = [SimpleNamespace(terminal_id=1), SimpleNamespace(terminal_id=2)]

    manager.show_confirmation_dialog("echo hi", terminals)

    assert len(presented) == 1
