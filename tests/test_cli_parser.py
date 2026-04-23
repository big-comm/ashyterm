# tests/test_cli_parser.py
"""Tests for ashyterm.cli_parser.CliArgParser."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def parser():
    """Fresh CliArgParser bound to a mocked app."""
    from ashyterm.cli_parser import CliArgParser

    app = MagicMock()
    app.logger = MagicMock()
    return CliArgParser(app)


class TestParseCommandLineArgs:
    def test_no_args_returns_defaults(self, parser):
        result = parser.parse_command_line_args(["ashyterm"])
        assert result == {
            "working_directory": None,
            "execute_command": None,
            "ssh_target": None,
            "close_after_execute": False,
            "force_new_window": False,
        }

    def test_positional_working_directory(self, parser):
        result = parser.parse_command_line_args(["ashyterm", "/tmp"])
        assert result["working_directory"] == "/tmp"

    def test_short_w_flag(self, parser):
        result = parser.parse_command_line_args(["ashyterm", "-w", "/srv"])
        assert result["working_directory"] == "/srv"

    def test_long_working_directory_equals(self, parser):
        result = parser.parse_command_line_args(
            ["ashyterm", "--working-directory=/home/user"]
        )
        assert result["working_directory"] == "/home/user"

    def test_ssh_target(self, parser):
        result = parser.parse_command_line_args(
            ["ashyterm", "--ssh", "user@host.example"]
        )
        assert result["ssh_target"] == "user@host.example"

    def test_ssh_equals_form(self, parser):
        result = parser.parse_command_line_args(
            ["ashyterm", "--ssh=root@10.0.0.1"]
        )
        assert result["ssh_target"] == "root@10.0.0.1"

    def test_execute_stops_parsing(self, parser):
        """Everything after -e is captured verbatim as the command."""
        result = parser.parse_command_line_args(
            ["ashyterm", "-e", "ls", "-la", "/tmp"]
        )
        assert result["execute_command"] == "ls -la /tmp"

    def test_execute_equals_form(self, parser):
        result = parser.parse_command_line_args(["ashyterm", "--execute=htop"])
        assert result["execute_command"] == "htop"

    def test_close_after_execute_flag(self, parser):
        result = parser.parse_command_line_args(
            ["ashyterm", "--close-after-execute", "-e", "date"]
        )
        assert result["close_after_execute"] is True
        assert result["execute_command"] == "date"

    def test_new_window_flag(self, parser):
        result = parser.parse_command_line_args(["ashyterm", "--new-window"])
        assert result["force_new_window"] is True

    def test_multiple_positionals_only_first_used(self, parser):
        result = parser.parse_command_line_args(["ashyterm", "/a", "/b"])
        assert result["working_directory"] == "/a"
        # Extra positional must be logged as a warning.
        parser.logger.warning.assert_called()


class TestCreateTabInWindow:
    def test_ssh_takes_priority(self, parser):
        window = MagicMock()
        parser.create_tab_in_window(window, "u@h", None, None, False)
        window.create_ssh_tab.assert_called_once_with("u@h")
        window.create_execute_tab.assert_not_called()
        window.create_local_tab.assert_not_called()

    def test_execute_falls_back_when_no_ssh(self, parser):
        window = MagicMock()
        parser.create_tab_in_window(window, None, "htop", "/tmp", True)
        window.create_execute_tab.assert_called_once_with("htop", "/tmp", True)

    def test_local_tab_default(self, parser):
        window = MagicMock()
        parser.create_tab_in_window(window, None, None, "/home", False)
        window.create_local_tab.assert_called_once_with("/home")


class TestProcessAndExecuteArgs:
    def test_raises_if_settings_manager_missing(self, parser):
        parser._app.settings_manager = None
        with pytest.raises(RuntimeError):
            parser.process_and_execute_args(["ashyterm"])

    def test_uses_new_window_when_forced(self, parser):
        parser._app.settings_manager.get = MagicMock(return_value="new_tab")
        parser._app.get_windows = MagicMock(return_value=[MagicMock()])
        new_win = MagicMock()
        parser._app.create_new_window = MagicMock(return_value=new_win)

        parser.process_and_execute_args(["ashyterm", "--new-window", "/tmp"])

        parser._app.create_new_window.assert_called_once()
        kwargs = parser._app.create_new_window.call_args.kwargs
        assert kwargs["initial_working_directory"] == "/tmp"

    def test_reuses_existing_window_by_default(self, parser):
        parser._app.settings_manager.get = MagicMock(return_value="new_tab")
        existing = MagicMock()
        parser._app.get_windows = MagicMock(return_value=[existing])

        parser.process_and_execute_args(["ashyterm", "/tmp"])

        existing.create_local_tab.assert_called_once_with("/tmp")
