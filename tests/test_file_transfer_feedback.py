"""Tests for file transfer user feedback."""

from ashyterm.filemanager.transfers import FileTransferMixin


def test_transfer_failure_toast_mentions_remote_io_error():
    title = FileTransferMixin._get_transfer_failure_toast_title(
        object(), "Remote input/output error: failed to read"
    )

    assert "input/output" in title.lower()


def test_transfer_failure_toast_uses_generic_history_hint():
    title = FileTransferMixin._get_transfer_failure_toast_title(
        object(), "rsync exited with code 23"
    )

    assert "history" in title.lower()
