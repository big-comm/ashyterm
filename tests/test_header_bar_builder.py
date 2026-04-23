"""Tests for header_bar_builder (window header construction)."""

import pytest

from ashyterm.ui.header_bar_builder import (
    _FLIPPABLE_ATTR_NAMES,
    _window_controls_on_left,
)


# ── _window_controls_on_left ────────────────────────────────


class TestWindowControlsOnLeft:
    @pytest.mark.parametrize(
        "layout",
        [
            "close:",
            "close:minimize,maximize",
            "close,minimize:",
            "minimize:maximize",
        ],
    )
    def test_returns_true_when_any_control_on_left(self, layout):
        assert _window_controls_on_left(layout) is True

    @pytest.mark.parametrize(
        "layout",
        [
            ":close,minimize,maximize",
            ":close",
            "appmenu:close",
        ],
    )
    def test_returns_false_when_controls_on_right(self, layout):
        assert _window_controls_on_left(layout) is False

    def test_empty_string_is_right_side(self):
        assert _window_controls_on_left("") is False

    def test_layout_without_colon_is_not_split(self):
        # A malformed layout without the separator should not crash;
        # we default to "controls on right" so the common case works.
        assert _window_controls_on_left("close,minimize,maximize") is False


# ── flippable attribute list ────────────────────────────────


class TestFlippableAttrs:
    def test_flippable_attrs_cover_every_expected_button(self):
        expected = {
            "toggle_sidebar_button",
            "file_manager_button",
            "command_manager_button",
            "search_button",
            "ai_assistant_button",
            "cleanup_button",
            "menu_button",
            "new_tab_button",
        }
        assert set(_FLIPPABLE_ATTR_NAMES) == expected


# ── window_ui delegation ────────────────────────────────────


class TestWindowUiDelegation:
    def test_window_ui_still_exposes_create_header_bar(self):
        from ashyterm.ui.window_ui import WindowUIBuilder

        assert callable(WindowUIBuilder._create_header_bar)
