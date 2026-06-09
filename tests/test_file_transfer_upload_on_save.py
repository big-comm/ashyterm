"""Tests for upload-on-save transfer flow."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ashyterm.filemanager.transfers import FileTransferMixin
from ashyterm.filemanager.transfer_manager import TransferType
from ashyterm.sessions.models import SessionItem


def test_upload_on_save_marks_transfer_started_before_upload(tmp_path):
    order = []
    local_file = tmp_path / "note.txt"
    local_file.write_text("hello", encoding="utf-8")
    session = SessionItem(
        name="remote",
        session_type="ssh",
        host="example.com",
        user="alice",
    )

    transfer_manager = MagicMock()
    transfer_manager.add_transfer.return_value = "transfer-1"
    transfer_manager.start_transfer.side_effect = lambda transfer_id: order.append(
        ("start", transfer_id)
    )
    transfer_manager.get_cancellation_event.return_value = None

    operations = MagicMock()
    operations.start_upload_with_progress.side_effect = lambda *args, **kwargs: order.append(
        ("upload", args[0])
    )

    fm = SimpleNamespace(
        transfer_manager=transfer_manager,
        operations=operations,
        session_item=session,
        logger=MagicMock(),
        _on_save_upload_complete=MagicMock(),
    )

    FileTransferMixin._upload_on_save_thread(fm, local_file, "/remote/note.txt")

    transfer_manager.add_transfer.assert_called_once_with(
        filename="note.txt",
        local_path=str(local_file),
        remote_path="/remote/note.txt",
        file_size=5,
        transfer_type=TransferType.UPLOAD,
        is_cancellable=True,
        is_directory=False,
    )
    assert order == [("start", "transfer-1"), ("upload", "transfer-1")]


def test_rename_sanitizes_path_separators():
    toast_overlay = MagicMock()
    fm = SimpleNamespace(
        current_path="/remote",
        _execute_verified_command=MagicMock(),
        parent_window=SimpleNamespace(toast_overlay=toast_overlay),
    )
    file_item = SimpleNamespace(name="old.txt")
    entry = MagicMock()
    entry.get_text.return_value = "new/name.txt"

    FileTransferMixin._on_rename_dialog_response(
        fm, MagicMock(), "rename", file_item, entry
    )

    fm._execute_verified_command.assert_called_once_with(
        ["mv", "/remote/old.txt", "/remote/new_name.txt"], command_type="mv"
    )
    toast_overlay.add_toast.assert_called_once()


def test_remote_save_as_sanitizes_path_separators():
    fm = SimpleNamespace()

    remote_path = FileTransferMixin._build_remote_sibling_path(
        fm, "/remote/old.txt", "copy/name.txt"
    )

    assert remote_path == "/remote/copy_name.txt"
