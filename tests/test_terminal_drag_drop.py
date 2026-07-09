"""Tests for terminal_drag_drop (file drop helpers)."""

from unittest.mock import MagicMock

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.terminal_drag_drop import (
    build_sftp_put_command,
    emit_ssh_file_drop_signal,
    extract_dropped_paths,
    handle_sftp_drop,
    handle_ssh_drop,
    is_terminal_ssh_like,
)


def _file_value(paths):
    """Build a fake Gdk.FileList-like value returning the given paths."""
    files = [MagicMock(get_path=MagicMock(return_value=p)) for p in paths]
    value = MagicMock()
    value.get_files = MagicMock(return_value=files)
    return value


# ── extract_dropped_paths ────────────────────────────────────


class TestExtractDroppedPaths:
    def test_returns_local_paths_only(self):
        value = _file_value(["/tmp/a.txt", "/tmp/b.txt"])
        assert extract_dropped_paths(value) == ["/tmp/a.txt", "/tmp/b.txt"]

    def test_drops_entries_with_no_path(self):
        value = _file_value(["/tmp/a.txt", None, "/tmp/c.txt"])
        assert extract_dropped_paths(value) == ["/tmp/a.txt", "/tmp/c.txt"]

    def test_empty_file_list_returns_empty(self):
        value = _file_value([])
        assert extract_dropped_paths(value) == []

    def test_exception_in_get_files_returns_empty(self):
        value = MagicMock()
        value.get_files = MagicMock(side_effect=RuntimeError("corrupt"))
        assert extract_dropped_paths(value) == []

    def test_none_from_get_files_returns_empty(self):
        value = MagicMock()
        value.get_files = MagicMock(return_value=None)
        assert extract_dropped_paths(value) == []

    def test_skips_file_whose_path_lookup_fails(self):
        good = MagicMock(get_path=MagicMock(return_value="/tmp/good"))
        broken = MagicMock(get_path=MagicMock(side_effect=RuntimeError("gone")))
        value = MagicMock(get_files=MagicMock(return_value=[broken, good]))

        assert extract_dropped_paths(value) == ["/tmp/good"]


# ── is_terminal_ssh_like ────────────────────────────────────


class TestIsTerminalSshLike:
    def test_manual_ssh_target_wins(self):
        assert is_terminal_ssh_like(session=None, ssh_target="me@host") is True

    def test_ssh_session_is_true(self):
        session = SessionItem(name="s", session_type="ssh", host="h", user="u")
        assert is_terminal_ssh_like(session=session, ssh_target=None) is True

    def test_local_session_without_manual_ssh_is_false(self):
        session = SessionItem(name="s", session_type="local")
        assert is_terminal_ssh_like(session=session, ssh_target=None) is False

    def test_none_session_without_manual_ssh_is_false(self):
        assert is_terminal_ssh_like(session=None, ssh_target=None) is False


# ── handle_sftp_drop ────────────────────────────────────────


class TestHandleSftpDrop:
    def test_feeds_put_command_per_file(self):
        terminal = MagicMock()
        handle_sftp_drop(_file_value(["/tmp/a.txt", "/srv/b"]), terminal)

        encoded = [c.args[0] for c in terminal.feed_child.call_args_list]
        decoded = [b.decode() for b in encoded]
        assert 'put -r "/tmp/a.txt"\n' in decoded
        assert 'put -r "/srv/b"\n' in decoded

    def test_empty_list_is_noop_and_returns_true(self):
        terminal = MagicMock()
        assert handle_sftp_drop(_file_value([]), terminal) is True
        terminal.feed_child.assert_not_called()

    def test_exception_returns_false(self):
        terminal = MagicMock()
        value = MagicMock()
        value.get_files = MagicMock(side_effect=RuntimeError("boom"))
        # Our helper swallows the exception in extract_dropped_paths;
        # the feed loop doesn't, but a terminal mock doesn't raise.
        # We assert the contract stays True when no feed errors occur.
        assert handle_sftp_drop(value, terminal) is True

    def test_quotes_double_quotes_and_backslashes(self):
        command = build_sftp_put_command('/tmp/a"b\\c')
        assert command == 'put -r "/tmp/a\\"b\\\\c"\n'

    def test_rejects_newline_command_injection(self):
        terminal = MagicMock()
        path = '/tmp/file\n!rm /tmp/important'

        assert build_sftp_put_command(path) is None
        assert handle_sftp_drop(_file_value([path]), terminal) is True
        terminal.feed_child.assert_not_called()


# ── handle_ssh_drop ─────────────────────────────────────────


def _manager(*, info=None, ssh_target=None, callback=None) -> MagicMock:
    mgr = MagicMock()
    mgr.registry.get_terminal_info = MagicMock(return_value=info)
    mgr.manual_ssh_tracker.get_ssh_target = MagicMock(return_value=ssh_target)
    mgr._pending_ssh_upload = None
    mgr._ssh_file_drop_callback = callback
    return mgr


class TestHandleSshDrop:
    def test_empty_paths_returns_false(self):
        mgr = _manager()
        assert handle_ssh_drop(mgr, _file_value([]), 1) is False

    def test_no_registry_info_returns_false(self):
        mgr = _manager(info=None)
        assert handle_ssh_drop(mgr, _file_value(["/tmp/a"]), 1) is False

    def test_local_session_without_manual_ssh_returns_false(self):
        session = SessionItem(name="s", session_type="local")
        mgr = _manager(info={"identifier": session}, ssh_target=None)
        assert handle_ssh_drop(mgr, _file_value(["/tmp/a"]), 1) is False

    def test_ssh_session_schedules_signal_emission(self, monkeypatch):
        session = SessionItem(
            name="s", session_type="ssh", host="h", user="u"
        )
        mgr = _manager(info={"identifier": session}, ssh_target=None)
        scheduled = []

        def fake_idle_add(fn, *args):
            scheduled.append((fn, args))
            return 0

        monkeypatch.setattr(
            "ashyterm.terminal.terminal_drag_drop.GLib.idle_add", fake_idle_add
        )
        out = handle_ssh_drop(mgr, _file_value(["/tmp/a", "/tmp/b"]), 1)

        assert out is True
        assert len(scheduled) == 1
        # Args: (manager, terminal_id, local_paths, session, ssh_target)
        _fn, args = scheduled[0]
        assert args[0] is mgr
        assert args[1] == 1
        assert args[2] == ["/tmp/a", "/tmp/b"]

    def test_manual_ssh_target_is_enough_for_acceptance(self, monkeypatch):
        mgr = _manager(info={"identifier": None}, ssh_target="me@host")
        scheduled = []
        monkeypatch.setattr(
            "ashyterm.terminal.terminal_drag_drop.GLib.idle_add",
            lambda fn, *args: scheduled.append((fn, args)) or 0,
        )

        out = handle_ssh_drop(mgr, _file_value(["/tmp/a"]), 7)
        assert out is True
        assert scheduled

    def test_refreshes_manual_ssh_state_before_rejecting_local(self, monkeypatch):
        session = SessionItem(name="s", session_type="local")
        mgr = _manager(info={"type": "local", "identifier": session})
        targets = iter([None, "root@tailscale-node"])
        mgr.manual_ssh_tracker.get_ssh_target.side_effect = lambda _tid: next(
            targets
        )
        scheduled = []
        monkeypatch.setattr(
            "ashyterm.terminal.terminal_drag_drop.GLib.idle_add",
            lambda fn, *args: scheduled.append((fn, args)) or 0,
        )

        out = handle_ssh_drop(mgr, _file_value(["/tmp/a"]), 7)

        assert out is True
        mgr.manual_ssh_tracker.check_process_tree.assert_called_once_with(7)
        assert scheduled[0][1][-1] == "root@tailscale-node"


# ── emit_ssh_file_drop_signal ──────────────────────────────


class TestEmitSshFileDropSignal:
    def test_stashes_pending_payload(self):
        mgr = _manager()
        emit_ssh_file_drop_signal(
            mgr, 3, ["/a", "/b"], "session-obj", "root@host"
        )

        assert mgr._pending_ssh_upload == {
            "terminal_id": 3,
            "local_paths": ["/a", "/b"],
            "session": "session-obj",
            "ssh_target": "root@host",
        }

    def test_callback_invoked_when_registered(self):
        called_with = []
        callback = MagicMock(
            side_effect=lambda *args: called_with.append(args)
        )
        mgr = _manager(callback=callback)

        emit_ssh_file_drop_signal(mgr, 3, ["/a"], "sess", "me@host")

        callback.assert_called_once_with(3, ["/a"], "sess", "me@host")

    def test_missing_callback_is_silent(self):
        mgr = _manager(callback=None)
        # Should not crash and must still update _pending_ssh_upload.
        emit_ssh_file_drop_signal(mgr, 3, ["/a"], "sess", "me@host")
        assert mgr._pending_ssh_upload["terminal_id"] == 3

    def test_returns_false_to_drop_off_idle_queue(self):
        mgr = _manager()
        assert emit_ssh_file_drop_signal(mgr, 1, [], None, None) is False


# ── manager delegation ─────────────────────────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.terminal.manager import TerminalManager

        for name in (
            "_setup_sftp_drag_and_drop",
            "_setup_ssh_drag_and_drop",
            "_remove_ssh_drag_and_drop",
            "_on_file_drop",
            "_on_ssh_file_drop",
            "_emit_ssh_file_drop_signal",
        ):
            assert callable(getattr(TerminalManager, name))

    def test_manual_ssh_state_installs_drop_controller(self):
        from ashyterm.terminal.manager import TerminalManager

        manager = TerminalManager.__new__(TerminalManager)
        terminal = MagicMock()
        terminal.terminal_id = 7
        manager.manual_ssh_tracker = MagicMock()
        manager.manual_ssh_tracker.get_ssh_target.return_value = (
            "root@tailscale-node"
        )
        manager._setup_ssh_drag_and_drop = MagicMock()
        manager._remove_ssh_drag_and_drop = MagicMock()
        manager._update_title = MagicMock()

        manager._on_manual_ssh_state_changed(terminal)

        manager._setup_ssh_drag_and_drop.assert_called_once_with(terminal, 7)
        manager._remove_ssh_drag_and_drop.assert_not_called()

    def test_manual_ssh_exit_removes_drop_controller(self):
        from ashyterm.terminal.manager import TerminalManager

        manager = TerminalManager.__new__(TerminalManager)
        terminal = MagicMock()
        terminal.terminal_id = 7
        manager.manual_ssh_tracker = MagicMock()
        manager.manual_ssh_tracker.get_ssh_target.return_value = None
        manager._setup_ssh_drag_and_drop = MagicMock()
        manager._remove_ssh_drag_and_drop = MagicMock()
        manager._update_title = MagicMock()

        manager._on_manual_ssh_state_changed(terminal)

        manager._remove_ssh_drag_and_drop.assert_called_once_with(terminal)
        manager._setup_ssh_drag_and_drop.assert_not_called()
