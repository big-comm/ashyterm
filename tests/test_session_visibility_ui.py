"""Favorite visibility controls remain reachable with an empty sidebar."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from ashyterm.sessions.models import SessionFolder, SessionItem
from ashyterm.sessions.results import OperationResult
from ashyterm.ui.actions import WindowActions
from ashyterm.ui.dialogs.session_visibility_dialog import SessionVisibilityDialog
from ashyterm.ui.menus import create_root_menu, create_session_menu
from ashyterm.ui.sidebar_manager import SidebarManager
from ashyterm.ui.widgets.inline_context_menu import InlineContextMenu


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def make_dialog(sessions, success=True):
    def set_hidden(session, hidden):
        if success:
            session.hidden = hidden
        return OperationResult(success, "Save failed" if not success else "")

    operations = SimpleNamespace(set_session_hidden=MagicMock(side_effect=set_hidden))
    dialog = SessionVisibilityDialog(SimpleNamespace(
        session_operations=operations, session_store=sessions,
    ))
    rows = [widget for widget in walk(dialog.get_visible_page()) if isinstance(widget, Adw.SwitchRow)]
    return dialog, rows, operations


def test_management_lists_hidden_sessions_and_switches_both_ways():
    session = SessionItem("github-crazy", host="github.com", source="ssh_config", hidden=True)
    _dialog, rows, operations = make_dialog([session])
    assert len(rows) == 1
    assert not rows[0].get_active()
    assert "github.com" in rows[0].get_subtitle()

    rows[0].set_active(True)
    assert not session.hidden
    rows[0].set_active(False)
    assert session.hidden
    assert operations.set_session_hidden.call_count == 2


def test_failed_save_restores_switch_without_retry_loop():
    session = SessionItem("github", hidden=True)
    _dialog, rows, operations = make_dialog([session], success=False)

    rows[0].set_active(True)

    assert not rows[0].get_active()
    assert session.hidden
    operations.set_session_hidden.assert_called_once_with(session, False)


def test_management_handles_empty_store():
    _dialog, rows, _operations = make_dialog([])
    assert rows == []


@pytest.mark.parametrize("item, action", [
    (None, "manage_session_visibility"),
    (SessionItem("server"), "edit_session"),
    (SessionFolder("work"), "edit_folder"),
])
def test_pencil_manages_visibility_without_changing_selected_item_edit(item, action):
    sidebar = SimpleNamespace(
        _close_popover_if_active=MagicMock(),
        session_tree=SimpleNamespace(get_selected_item=lambda: item),
        window=SimpleNamespace(action_handler=MagicMock()),
    )
    SidebarManager._on_edit_selected_clicked(sidebar, None)
    getattr(sidebar.window.action_handler, action).assert_called_once_with()


def menu_actions(menu):
    return {
        value.get_string() for index in range(menu.get_n_items())
        if (value := menu.get_item_attribute_value(index, "action", None)) is not None
    }


def test_context_menus_offer_hide_and_restore_access():
    assert "win.hide-session" in menu_actions(create_session_menu(SessionItem("server"), None, 0))
    assert "win.manage-session-visibility" in menu_actions(create_root_menu())
    inline = InlineContextMenu(MagicMock())
    inline.show_for_session(SessionItem("server"), None, False)
    actions = {widget.get_action_name() for widget in walk(inline) if hasattr(widget, "get_action_name")}
    assert "win.hide-session" in actions
    inline.show_for_root(False)
    actions = {widget.get_action_name() for widget in walk(inline) if hasattr(widget, "get_action_name")}
    assert "win.manage-session-visibility" in actions


def test_hide_action_uses_current_selection_and_offers_management():
    session = SessionItem("github")
    window = SimpleNamespace(
        session_tree=SimpleNamespace(get_selected_item=lambda: session),
        session_operations=SimpleNamespace(set_session_hidden=MagicMock(return_value=OperationResult(True))),
        toast_overlay=SimpleNamespace(add_toast=MagicMock()),
    )
    actions = WindowActions(window)

    actions.hide_session()

    window.session_operations.set_session_hidden.assert_called_once_with(session, True)
    toast = window.toast_overlay.add_toast.call_args.args[0]
    assert toast.get_button_label()
