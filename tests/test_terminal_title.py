"""Tests for terminal_title helpers (pure title computation)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.terminal_title import (
    compute_title,
    format_duration,
    local_title,
    ssh_title,
)


def _osc7(display_path: str):
    """Minimal stand-in for OSC7Info — only display_path is read."""
    return SimpleNamespace(display_path=display_path)


# ── format_duration ──────────────────────────────────────────


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0s"),
            (5, "5s"),
            (59, "59s"),
            (60, "1m 0s"),
            (125, "2m 5s"),
            (3599, "59m 59s"),
            (3600, "1h 0m"),
            (3661, "1h 1m"),
            (7265, "2h 1m"),
        ],
    )
    def test_known_durations(self, seconds, expected):
        assert format_duration(seconds) == expected

    def test_fractional_seconds_round_down(self):
        assert format_duration(5.9) == "5s"


# ── ssh_title ────────────────────────────────────────────────


class TestSshTitle:
    def test_session_name_with_cwd(self):
        info = {"identifier": SessionItem(name="dev-box", session_type="ssh")}
        assert ssh_title(info, _osc7("/var/log")) == "dev-box:/var/log"

    def test_session_name_without_cwd(self):
        info = {"identifier": SessionItem(name="dev-box", session_type="ssh")}
        assert ssh_title(info, None) == "dev-box"

    def test_no_session_returns_generic_title(self):
        assert ssh_title({}, None) == "Terminal"
        assert ssh_title({"identifier": "not-a-session"}, None) == "Terminal"


# ── local_title ──────────────────────────────────────────────


class TestLocalTitle:
    def test_manual_ssh_target_overrides_identifier(self):
        info = {"identifier": SessionItem(name="local", session_type="local")}
        out = local_title(info, ssh_target="root@prod", osc7_info=_osc7("/"))
        assert out == "root@prod:/"

    def test_manual_ssh_target_without_cwd(self):
        info = {"identifier": SessionItem(name="local", session_type="local")}
        out = local_title(info, ssh_target="root@prod", osc7_info=None)
        assert out == "root@prod"

    def test_osc7_cwd_when_no_manual_ssh(self):
        info = {"identifier": SessionItem(name="local", session_type="local")}
        out = local_title(info, ssh_target=None, osc7_info=_osc7("~/code"))
        assert out == "~/code"

    def test_session_name_fallback(self):
        info = {"identifier": SessionItem(name="local", session_type="local")}
        out = local_title(info, ssh_target=None, osc7_info=None)
        assert out == "local"

    def test_string_identifier_fallback(self):
        out = local_title(
            {"identifier": "Local"}, ssh_target=None, osc7_info=None
        )
        assert out == "Local"

    def test_missing_identifier_stringifies_none(self):
        out = local_title({}, ssh_target=None, osc7_info=None)
        assert out == "None"


# ── compute_title dispatcher ─────────────────────────────────


class TestComputeTitle:
    def test_dispatch_ssh(self):
        info = {
            "type": "ssh",
            "identifier": SessionItem(name="box", session_type="ssh"),
        }
        out = compute_title(
            info, ssh_target=None, sftp_title="n/a", osc7_info=_osc7("/srv")
        )
        assert out == "box:/srv"

    def test_dispatch_local(self):
        info = {
            "type": "local",
            "identifier": SessionItem(name="shell", session_type="local"),
        }
        out = compute_title(
            info, ssh_target="me@host", sftp_title="n/a", osc7_info=None
        )
        assert out == "me@host"

    def test_dispatch_sftp_uses_caller_supplied_title(self):
        info = {
            "type": "sftp",
            "identifier": SessionItem(name="file-svc", session_type="ssh"),
        }
        out = compute_title(
            info, ssh_target=None, sftp_title="SFTP-file-svc", osc7_info=None
        )
        assert out == "SFTP-file-svc"

    def test_unknown_type_fallback(self):
        info = {"type": "pipe"}
        out = compute_title(
            info, ssh_target=None, sftp_title="", osc7_info=None
        )
        assert out == "Terminal"


# ── manager delegation ───────────────────────────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.terminal.manager import TerminalManager

        for name in (
            "_format_duration",
            "_compute_terminal_title",
            "_get_ssh_title",
            "_get_local_title",
            "_get_sftp_title",
        ):
            assert callable(getattr(TerminalManager, name))

    def test_compute_terminal_title_threads_ssh_target(self):
        """The manager must pass the ``manual_ssh_tracker`` lookup into
        the pure renderer for local terminals. Verify the contract end
        to end without spinning up a real TerminalManager.
        """
        from ashyterm.terminal.manager import TerminalManager

        mgr = object.__new__(TerminalManager)
        mgr.manual_ssh_tracker = MagicMock()
        mgr.manual_ssh_tracker.get_ssh_target = MagicMock(return_value="alice@prod")

        # Stub _get_sftp_title so the SFTP branch doesn't require a tab_manager.
        mgr._get_sftp_title = MagicMock(return_value="ignored")

        info = {
            "type": "local",
            "identifier": SessionItem(name="shell", session_type="local"),
        }
        out = TerminalManager._compute_terminal_title(
            mgr, info, terminal_id=1, terminal=MagicMock(), osc7_info=None
        )

        assert out == "alice@prod"
        mgr.manual_ssh_tracker.get_ssh_target.assert_called_once_with(1)
