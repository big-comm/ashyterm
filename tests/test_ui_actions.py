"""Tests for window action helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.ui.actions import WindowActions


class _FormatTerminal:
    def get_text_format(self, _format):
        return "format text"


class _FallbackTerminal:
    def get_text_format(self, _format):
        raise RuntimeError("format failed")

    def get_text(self, _include_trailing_spaces, _cancellable):
        return ("fallback text", None)


class _BrokenTerminal:
    def get_text_format(self, _format):
        raise RuntimeError("format failed")

    def get_text(self, _include_trailing_spaces, _cancellable):
        raise RuntimeError("text failed")


def test_extract_terminal_text_uses_format_api():
    assert WindowActions._extract_terminal_text(_FormatTerminal()) == "format text"


def test_extract_terminal_text_falls_back_to_get_text():
    assert WindowActions._extract_terminal_text(_FallbackTerminal()) == "fallback text"


def test_extract_terminal_text_raises_when_all_apis_fail():
    with pytest.raises(RuntimeError):
        WindowActions._extract_terminal_text(_BrokenTerminal())


def test_debug_mode_enabled_reads_settings_manager():
    window = SimpleNamespace(settings_manager=MagicMock())
    window.settings_manager.get.return_value = True

    assert WindowActions(window)._debug_mode_enabled() is True


def test_debug_mode_enabled_defaults_false_without_settings_manager():
    window = SimpleNamespace()

    assert WindowActions(window)._debug_mode_enabled() is False
