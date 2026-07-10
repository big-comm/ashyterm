"""Tests for persistent terminal tab attention state."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ashyterm.terminal.tab_attention import (
    ATTENTION_CSS_CLASS,
    clear_tab_attention,
    mark_tab_attention,
)
from ashyterm.terminal.tabs import TabManager


def test_mark_tab_attention_adds_css_class() -> None:
    tab_widget = MagicMock()

    mark_tab_attention(tab_widget)

    tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_clear_tab_attention_removes_css_class() -> None:
    tab_widget = MagicMock()

    clear_tab_attention(tab_widget)

    tab_widget.remove_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_background_terminal_bell_marks_tab_until_visited() -> None:
    page = object()
    tab_widget = MagicMock()
    tab_widget.label_widget = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = MagicMock()
    tab_manager._find_tab_for_page = MagicMock(return_value=tab_widget)

    tab_manager._on_terminal_bell(SimpleNamespace(ashy_parent_page=page))

    tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_activating_tab_clears_attention() -> None:
    previous_tab = MagicMock()
    target_tab = MagicMock()
    page = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = previous_tab
    tab_manager.group_manager = MagicMock()
    tab_manager.group_manager.get_group_for_tab.return_value = None
    tab_manager.get_tab_id = MagicMock(return_value="tab-1")
    tab_manager._handle_previous_tab_focus = MagicMock()
    tab_manager.pages = {target_tab: page}
    tab_manager.view_stack = MagicMock()
    tab_manager._get_terminal_to_focus = MagicMock(return_value=None)

    tab_manager.set_active_tab(target_tab)

    target_tab.remove_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)
