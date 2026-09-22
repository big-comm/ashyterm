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


# --- open-sftp-files --------------------------------------------------------


def _window_with_terminal(target):
    window = SimpleNamespace(
        tab_manager=MagicMock(),
        terminal_manager=MagicMock(),
        toast_overlay=MagicMock(),
    )
    window.tab_manager.get_selected_terminal.return_value = object()
    window.terminal_manager.get_remote_file_target.return_value = target
    return window


def test_open_sftp_files_launches_uri_for_remote_target(monkeypatch):
    from ashyterm.terminal import sftp_open
    from ashyterm.terminal.sftp_open import RemoteFileTarget

    launched = []
    monkeypatch.setattr(
        sftp_open, "launch_file_manager_for_uri", lambda uri: launched.append(uri) or True
    )
    window = _window_with_terminal(
        RemoteFileTarget(host="example.org", user="deploy", port=22, path="/srv")
    )

    WindowActions(window).open_sftp_files()

    assert launched == ["sftp://deploy@example.org/srv"]
    window.toast_overlay.add_toast.assert_not_called()


def test_open_sftp_files_toasts_when_terminal_is_local(monkeypatch):
    from ashyterm.terminal import sftp_open

    monkeypatch.setattr(
        sftp_open, "launch_file_manager_for_uri", lambda uri: pytest.fail("must not launch")
    )
    window = _window_with_terminal(None)

    WindowActions(window).open_sftp_files()

    window.toast_overlay.add_toast.assert_called_once()


def test_open_sftp_files_toasts_when_launch_fails(monkeypatch):
    from ashyterm.terminal import sftp_open
    from ashyterm.terminal.sftp_open import RemoteFileTarget

    monkeypatch.setattr(sftp_open, "launch_file_manager_for_uri", lambda uri: False)
    window = _window_with_terminal(RemoteFileTarget(host="example.org"))

    WindowActions(window).open_sftp_files()

    window.toast_overlay.add_toast.assert_called_once()
