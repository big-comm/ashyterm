"""Tests for the lazily built main menu popover (hamburger button).

Regression: the menu used to install an empty placeholder popover and swap
it for the real one from inside the placeholder's own "show" handler. On
GTK 4.22 that freed the placeholder mid-show and crashed the app (SIGSEGV)
on the first click of the main menu button.
"""

from unittest.mock import MagicMock, patch

from ashyterm.ui.window_ui import WindowUIBuilder


def _bare_builder(menu_button) -> WindowUIBuilder:
    builder = WindowUIBuilder.__new__(WindowUIBuilder)
    builder.menu_button = menu_button
    builder._main_menu_popover = None
    builder.font_sizer_widget = None
    builder.window = MagicMock()
    builder.tooltip_helper = MagicMock()
    return builder


def test_setup_registers_create_popup_func_without_placeholder() -> None:
    menu_button = MagicMock()
    builder = _bare_builder(menu_button)

    builder._setup_lazy_menu_popover()

    menu_button.set_create_popup_func.assert_called_once_with(
        builder._ensure_main_menu_popover
    )
    menu_button.set_popover.assert_not_called()


def test_ensure_main_menu_popover_builds_once() -> None:
    menu_button = MagicMock()
    builder = _bare_builder(menu_button)
    popover, font_sizer = MagicMock(), MagicMock()

    with patch(
        "ashyterm.ui.menus.MainApplicationMenu.create_main_popover",
        return_value=(popover, font_sizer),
    ) as create:
        builder._ensure_main_menu_popover(menu_button)
        builder._ensure_main_menu_popover(menu_button)

    create.assert_called_once_with(builder.window)
    menu_button.set_popover.assert_called_once_with(popover)
    assert builder._main_menu_popover is popover
    assert builder.font_sizer_widget is font_sizer
