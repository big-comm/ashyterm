"""Tests for AsyncSSH segmented downloads."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ashyterm.filemanager import accelerated_download as accelerated_download_module
from ashyterm.filemanager.accelerated_download import (
    AcceleratedDownloadCancelled,
    AcceleratedDownloadConfig,
    AcceleratedDownloadError,
    AsyncSSHSegmentedDownloader,
    build_download_chunks,
)
from ashyterm.sessions.models import SessionItem


def _session(**overrides) -> SessionItem:
    data = dict(
        name="prod",
        session_type="ssh",
        host="example.com",
        user="alice",
        port=2222,
        auth_type="key",
        auth_value="/home/alice/.ssh/id_ed25519",
    )
    data.update(overrides)
    return SessionItem(**data)


class FakeRemoteFile:
    def __init__(self, sftp, short_read_offset=None):
        self._sftp = sftp
        self._short_read_offset = short_read_offset
        self.closed = False

    async def read(self, size, offset=None):
        self._sftp.reads.append((offset, size))
        data = self._sftp.payload[offset : offset + size]
        if offset == self._short_read_offset:
            return data[:-1]
        return data

    async def close(self):
        self.closed = True


class FakeSFTP:
    def __init__(self, payload: bytes, short_read_offset=None):
        self.payload = payload
        self.short_read_offset = short_read_offset
        self.reads = []
        self.opened_files = []

    async def stat(self, _path):
        return SimpleNamespace(size=len(self.payload), mtime=1_700_000_000)

    async def open(self, _path, _mode):
        remote_file = FakeRemoteFile(self, self.short_read_offset)
        self.opened_files.append(remote_file)
        return remote_file


class FakeSFTPContext:
    def __init__(self, sftp: FakeSFTP):
        self.sftp = sftp
        self.exited = False

    async def __aenter__(self):
        return self.sftp

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.exited = True


class FakeConnection:
    def __init__(self, sftp_context: FakeSFTPContext):
        self.sftp_context = sftp_context

    def start_sftp_client(self):
        return self.sftp_context


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.exited = False

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.exited = True


class FakeConnectFactory:
    def __init__(self, payload: bytes, short_read_offset=None):
        self.sftp = FakeSFTP(payload, short_read_offset)
        self.sftp_context = FakeSFTPContext(self.sftp)
        self.connection_context = FakeConnectionContext(
            FakeConnection(self.sftp_context)
        )
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.connection_context


def _download(
    tmp_path: Path,
    payload: bytes,
    *,
    parallel_requests: int = 3,
    chunk_size: int = 5,
    cancellation_event=None,
    progress_callback=None,
    session=None,
    short_read_offset=None,
):
    factory = FakeConnectFactory(payload, short_read_offset=short_read_offset)
    downloader = AsyncSSHSegmentedDownloader(
        session or _session(),
        AcceleratedDownloadConfig(
            parallel_requests=parallel_requests,
            chunk_size_bytes=chunk_size,
            connect_timeout=11,
            strict_host_key_checking="no",
        ),
        progress_callback=progress_callback,
        cancellation_event=cancellation_event,
        connect_factory=factory,
    )
    local_path = tmp_path / "download.bin"
    downloader.download("/srv/download.bin", local_path, expected_size=len(payload))
    return local_path, factory


def test_build_download_chunks_covers_file_exactly():
    assert build_download_chunks(0, 4) == []
    assert build_download_chunks(10, 4) == [(0, 4), (4, 4), (8, 2)]


def test_segmented_download_writes_all_ranges_and_reports_progress(tmp_path):
    payload = b"abcdefghijklmnopqrstuvwxyz"
    progress = []

    local_path, factory = _download(
        tmp_path,
        payload,
        parallel_requests=3,
        chunk_size=5,
        progress_callback=progress.append,
    )

    assert local_path.read_bytes() == payload
    assert sorted(factory.sftp.reads) == [
        (0, 5),
        (5, 5),
        (10, 5),
        (15, 5),
        (20, 5),
        (25, 1),
    ]
    assert len(factory.sftp.opened_files) == 3
    assert progress[-1] == 100.0
    assert factory.connection_context.exited is True
    assert factory.sftp_context.exited is True


def test_connect_options_include_auth_and_disable_known_hosts_when_requested(tmp_path):
    payload = b"data"
    _local_path, factory = _download(tmp_path, payload, parallel_requests=2)

    assert factory.kwargs == {
        "host": "example.com",
        "port": 2222,
        "username": "alice",
        "connect_timeout": 11,
        "client_keys": ["/home/alice/.ssh/id_ed25519"],
        "known_hosts": None,
    }


def test_password_auth_passes_password_to_asyncssh(tmp_path):
    payload = b"data"
    session = SimpleNamespace(
        host="example.com",
        user="alice",
        port=2222,
        auth_value="secret",
        proxy_jump="",
        is_ssh=lambda: True,
        uses_key_auth=lambda: False,
        uses_password_auth=lambda: True,
    )

    _local_path, factory = _download(tmp_path, payload, session=session)

    assert factory.kwargs["password"] == "secret"
    assert "client_keys" not in factory.kwargs


def test_cancelled_download_removes_partial_file(tmp_path):
    payload = b"0123456789"
    cancellation_event = threading.Event()

    def cancel_after_first_progress(_progress):
        cancellation_event.set()

    factory = FakeConnectFactory(payload)
    downloader = AsyncSSHSegmentedDownloader(
        _session(),
        AcceleratedDownloadConfig(parallel_requests=1, chunk_size_bytes=2),
        progress_callback=cancel_after_first_progress,
        cancellation_event=cancellation_event,
        connect_factory=factory,
    )
    local_path = tmp_path / "download.bin"

    with pytest.raises(AcceleratedDownloadCancelled):
        downloader.download("/srv/download.bin", local_path, expected_size=len(payload))

    assert not local_path.exists()
    assert not (tmp_path / "download.bin.part").exists()


def test_short_read_removes_partial_file_and_raises(tmp_path):
    payload = b"0123456789"
    factory = FakeConnectFactory(payload, short_read_offset=4)
    downloader = AsyncSSHSegmentedDownloader(
        _session(),
        AcceleratedDownloadConfig(parallel_requests=1, chunk_size_bytes=4),
        connect_factory=factory,
    )
    local_path = tmp_path / "download.bin"

    with pytest.raises(AcceleratedDownloadError, match="Short read"):
        downloader.download("/srv/download.bin", local_path, expected_size=len(payload))

    assert not local_path.exists()
    assert not (tmp_path / "download.bin.part").exists()


def test_short_local_write_removes_partial_file_and_raises(monkeypatch, tmp_path):
    payload = b"0123456789"
    factory = FakeConnectFactory(payload)
    downloader = AsyncSSHSegmentedDownloader(
        _session(),
        AcceleratedDownloadConfig(parallel_requests=1, chunk_size_bytes=4),
        connect_factory=factory,
    )
    local_path = tmp_path / "download.bin"
    monkeypatch.setattr(accelerated_download_module.os, "pwrite", lambda *_args: 0)

    with pytest.raises(AcceleratedDownloadError, match="Short local write"):
        downloader.download("/srv/download.bin", local_path, expected_size=len(payload))

    assert not local_path.exists()
    assert not (tmp_path / "download.bin.part").exists()
