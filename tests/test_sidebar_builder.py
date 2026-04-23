"""Tests for sidebar_builder."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ashyterm.ui.sidebar_builder import build_sidebar


def _stub_builder() -> SimpleNamespace:
    """Minimal ``WindowUIBuilder`` stub for the sidebar."""
    session_tree = MagicMock()
    # Real Gtk widget since scrolled.set_child rejects MagicMock.
    session_tree.get_widget = MagicMock(return_value=Gtk.Box())
    return SimpleNamespace(
        tooltip_helper=MagicMock(),
        session_tree=session_tree,
        # Fields the builder will write back onto us.
        add_session_button=None,
        add_folder_button=None,
        edit_button=None,
        save_layout_button=None,
        remove_button=None,
        sidebar_search_entry=None,
        sidebar_main_stack=None,
        sidebar_content_stack=None,
        inline_context_menu_box=None,
    )


# ── build_sidebar ───────────────────────────────────────────


class TestBuildSidebar:
    def test_returns_a_widget(self):
        builder = _stub_builder()
        result = build_sidebar(builder)
        assert result is not None

    def test_publishes_all_action_buttons(self):
        builder = _stub_builder()
        build_sidebar(builder)

        for attr in (
            "add_session_button",
            "add_folder_button",
            "edit_button",
            "save_layout_button",
            "remove_button",
        ):
            assert getattr(builder, attr) is not None, f"{attr} not set"

    def test_registers_tooltips_for_each_action(self):
        builder = _stub_builder()
        build_sidebar(builder)

        # Five action buttons each get a tooltip via tooltip_helper.
        assert builder.tooltip_helper.add_tooltip.call_count == 5

    def test_remove_button_gets_destructive_class(self):
        builder = _stub_builder()
        build_sidebar(builder)

        # The destructive class is what surfaces the red "danger"
        # styling on the trash icon.
        classes = builder.remove_button.get_css_classes()
        assert "destructive" in classes

    def test_search_entry_is_created(self):
        builder = _stub_builder()
        build_sidebar(builder)
        assert builder.sidebar_search_entry is not None

    def test_stack_has_normal_and_context_menu_pages(self):
        builder = _stub_builder()
        build_sidebar(builder)

        stack = builder.sidebar_main_stack
        assert stack is not None
        # Both named pages must exist so the tree can switch to the
        # inline context menu when the user right-clicks.
        assert stack.get_child_by_name("normal") is not None
        assert stack.get_child_by_name("context-menu") is not None

    def test_legacy_alias_points_at_same_stack(self):
        builder = _stub_builder()
        build_sidebar(builder)
        # ``sidebar_content_stack`` is the old attribute still read by
        # some callers; it must point at the same live widget.
        assert builder.sidebar_content_stack is builder.sidebar_main_stack

    def test_session_tree_widget_is_embedded(self):
        builder = _stub_builder()
        build_sidebar(builder)
        # The builder must call ``session_tree.get_widget()`` to fetch
        # the widget it embeds in the scrolled area.
        builder.session_tree.get_widget.assert_called()


# ── window_ui delegation ────────────────────────────────────


class TestWindowUiDelegation:
    def test_window_ui_still_exposes_create_sidebar(self):
        from ashyterm.ui.window_ui import WindowUIBuilder

        assert callable(WindowUIBuilder._create_sidebar)
