"""Tests for file transfer user feedback."""

import pytest

from ashyterm.filemanager import transfers
from ashyterm.filemanager.transfers import FileTransferMixin


@pytest.fixture(autouse=True)
def _use_source_messages(monkeypatch):
    """Keep assertions independent from the desktop's active locale."""
    monkeypatch.setattr(transfers, "_", lambda message: message)


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
