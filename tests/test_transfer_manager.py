"""Tests for TransferManager state and signal dispatch."""

from ashyterm.filemanager import transfer_manager as transfer_manager_module
from ashyterm.filemanager.transfer_manager import (
    TransferManager,
    TransferStatus,
    TransferType,
)


def test_start_transfer_dispatches_signal_through_idle(monkeypatch, tmp_path):
    idle_calls = []
    monkeypatch.setattr(
        transfer_manager_module.GLib,
        "idle_add",
        lambda callback, *args: idle_calls.append((callback, args)) or 1,
    )
    manager = TransferManager(str(tmp_path))
    transfer_id = manager.add_transfer(
        filename="a.txt",
        local_path="/tmp/a.txt",
        remote_path="/remote/a.txt",
        file_size=10,
        transfer_type=TransferType.DOWNLOAD,
    )

    manager.start_transfer(transfer_id)

    transfer = manager.get_transfer(transfer_id)
    assert transfer is not None
    assert transfer.status is TransferStatus.IN_PROGRESS
    assert idle_calls[0][0].__self__ is manager
    assert idle_calls[0][0].__name__ == "emit"
    assert idle_calls[0][1] == ("transfer-started", transfer_id)


def test_start_transfer_ignores_unknown_id(monkeypatch, tmp_path):
    idle_add = []
    monkeypatch.setattr(
        transfer_manager_module.GLib,
        "idle_add",
        lambda callback, *args: idle_add.append((callback, args)) or 1,
    )
    manager = TransferManager(str(tmp_path))

    manager.start_transfer("missing")

    assert idle_add == []


def test_update_progress_ignores_unknown_id(monkeypatch, tmp_path):
    idle_add = []
    monkeypatch.setattr(
        transfer_manager_module.GLib,
        "idle_add",
        lambda callback, *args: idle_add.append((callback, args)) or 1,
    )
    manager = TransferManager(str(tmp_path))

    manager.update_progress("missing", 50.0)

    assert idle_add == []
