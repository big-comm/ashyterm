"""Tests for remote edit timestamp lookups."""

from unittest.mock import MagicMock

from ashyterm.core import tasks as tasks_module
from ashyterm.filemanager import transfers as transfers_module
from ashyterm.filemanager.transfers import FileTransferMixin


class _FakeTaskManager:
    def __init__(self):
        self.submitted = []

    def submit_io(self, func, *args):
        self.submitted.append((func, args))


class _FakeTransfer:
    _get_remote_file_timestamp_async = FileTransferMixin._get_remote_file_timestamp_async
    _dispatch_remote_file_timestamp = FileTransferMixin._dispatch_remote_file_timestamp


def _fake_transfer(operations, *, destroyed=False):
    fm = _FakeTransfer()
    fm.operations = operations
    fm.logger = MagicMock()
    fm._is_destroyed = destroyed
    return fm


def test_remote_timestamp_lookup_runs_in_io_executor(monkeypatch):
    task_manager = _FakeTaskManager()
    idle_calls = []

    monkeypatch.setattr(
        tasks_module.AsyncTaskManager, "get", staticmethod(lambda: task_manager)
    )
    monkeypatch.setattr(
        transfers_module.GLib,
        "idle_add",
        lambda callback, *args: idle_calls.append((callback, args)),
    )

    operations = MagicMock()
    operations.get_remote_file_timestamp.return_value = 123
    callback = MagicMock(return_value=False)
    fm = _fake_transfer(operations)

    FileTransferMixin._get_remote_file_timestamp_async(fm, "/remote/file.txt", callback)

    operations.get_remote_file_timestamp.assert_not_called()
    assert len(task_manager.submitted) == 1

    worker, args = task_manager.submitted[0]
    assert args == ()
    worker()

    operations.get_remote_file_timestamp.assert_called_once_with("/remote/file.txt")
    assert idle_calls == [(fm._dispatch_remote_file_timestamp, (callback, 123))]


def test_remote_timestamp_dispatch_skips_destroyed_manager():
    callback = MagicMock()
    fm = _fake_transfer(MagicMock(), destroyed=True)

    result = fm._dispatch_remote_file_timestamp(callback, 123)

    assert result == transfers_module.GLib.SOURCE_REMOVE
    callback.assert_not_called()
