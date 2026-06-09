"""Tests for FileManager terminal-command confirmation helpers."""

from unittest.mock import MagicMock

from ashyterm.filemanager import manager as manager_module
from ashyterm.filemanager.manager import (
    COMMAND_CONFIRM_TIMEOUT_MS,
    COMMAND_REFRESH_DELAY_MS,
    FileManager,
)


class _FakeFileManager:
    _confirm_pending_command = FileManager._confirm_pending_command
    _confirm_non_cd_pending_command = FileManager._confirm_non_cd_pending_command
    _remove_pending_command_sources = FileManager._remove_pending_command_sources
    _on_pending_command_timeout = FileManager._on_pending_command_timeout
    _refresh_after_pending_command = FileManager._refresh_after_pending_command


def _fake_file_manager(**overrides):
    defaults = dict(
        _command_timeout_id=0,
        _command_refresh_id=0,
        _pending_command=None,
        _is_destroyed=False,
        bound_terminal=MagicMock(),
        current_path="/tmp",
        logger=MagicMock(),
        refresh=MagicMock(return_value=False),
        _restore_search_entry=MagicMock(return_value=False),
        _showing_recursive_results=False,
        _recursive_search_in_progress=False,
        store=None,
    )
    defaults.update(overrides)
    fm = _FakeFileManager()
    for name, value in defaults.items():
        setattr(fm, name, value)
    return fm


def test_non_cd_command_schedules_confirmation_timeout_and_refresh_delay(monkeypatch):
    calls = []

    def fake_timeout_add(delay_ms, callback):
        calls.append((delay_ms, callback))
        return 76 + len(calls)

    monkeypatch.setattr(manager_module.GLib, "timeout_add", fake_timeout_add)
    fm = _fake_file_manager()

    FileManager._execute_verified_command(
        fm, ["mkdir", "-p", "/tmp/new"], command_type="mkdir"
    )

    assert calls[0][0] == COMMAND_CONFIRM_TIMEOUT_MS
    assert calls[0][1].__func__ is FileManager._on_pending_command_timeout
    assert calls[0][1].__self__ is fm
    assert fm._command_timeout_id == 77
    assert calls[1][0] == COMMAND_REFRESH_DELAY_MS
    assert calls[1][1].__func__ is FileManager._refresh_after_pending_command
    assert calls[1][1].__self__ is fm
    assert fm._command_refresh_id == 78
    fm.bound_terminal.feed_child.assert_any_call(b"\x01")
    fm.bound_terminal.feed_child.assert_any_call(b"\x0b")
    fm.bound_terminal.feed_child.assert_any_call(b"mkdir -p /tmp/new\n")


def test_successful_store_update_confirms_non_cd_pending_command(monkeypatch):
    removed_sources = []
    monkeypatch.setattr(
        manager_module.GLib, "source_remove", lambda source_id: removed_sources.append(source_id)
    )
    store = MagicMock()
    store.get_n_items.return_value = 0
    fm = _fake_file_manager(
        _command_timeout_id=42,
        _command_refresh_id=43,
        _pending_command={"type": "mkdir", "str": "mkdir /tmp/new"},
        store=store,
    )

    result = FileManager._set_store_items(fm, [], "/tmp", "filemanager")

    assert result is False
    assert removed_sources == [42, 43]
    assert fm._command_timeout_id == 0
    assert fm._command_refresh_id == 0
    assert fm._pending_command is None
    fm.bound_terminal.feed_child.assert_called_with(b"\x19")


def test_pending_command_timeout_restores_terminal_input(monkeypatch):
    source_remove = MagicMock()
    monkeypatch.setattr(manager_module.GLib, "source_remove", source_remove)
    fm = _fake_file_manager(
        _command_timeout_id=42,
        _command_refresh_id=43,
        _pending_command={"type": "cd", "str": "cd /missing"},
    )

    result = FileManager._on_pending_command_timeout(fm)

    assert result == manager_module.GLib.SOURCE_REMOVE
    assert fm._command_timeout_id == 0
    assert fm._pending_command is None
    source_remove.assert_called_once_with(43)
    fm.bound_terminal.feed_child.assert_called_once_with(b"\x19")


def test_refresh_timeout_clears_timer_before_refresh():
    fm = _fake_file_manager(_command_timeout_id=42, _command_refresh_id=43)

    result = FileManager._refresh_after_pending_command(fm)

    assert result == manager_module.GLib.SOURCE_REMOVE
    assert fm._command_timeout_id == 42
    assert fm._command_refresh_id == 0
    fm.refresh.assert_called_once_with(source="filemanager")
