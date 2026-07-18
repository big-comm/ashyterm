"""Tests for FileOperations rsync command helpers."""

import threading
from unittest.mock import MagicMock

import pytest

from ashyterm.filemanager import operations as operations_module
from ashyterm.filemanager.accelerated_download import AcceleratedDownloadUnavailable
from ashyterm.filemanager.operations import FileOperations, _format_exception_for_log
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


def test_rsync_ssh_command_uses_session_ssh_options():
    session = _session()
    spawner = MagicMock()
    spawner._get_base_ssh_options.return_value = {"ControlPath": "/tmp/cp"}
    spawner.command_builder.build_remote_command.return_value = [
        "/usr/bin/ssh",
        "-o",
        "ControlPath=/tmp/cp",
        "-o",
        "BatchMode=yes",
        "-i",
        "/home/alice/.ssh/id_ed25519",
        "-p",
        "2222",
        "example.com",
    ]
    ops = FileOperations(session)

    ssh_cmd = ops._build_rsync_ssh_command(spawner, session)

    spawner.command_builder.build_remote_command.assert_called_once_with(
        "ssh",
        hostname="example.com",
        port=2222,
        key_file="/home/alice/.ssh/id_ed25519",
        options={"ControlPath": "/tmp/cp", "BatchMode": "yes"},
    )
    assert ssh_cmd == (
        "/usr/bin/ssh -o ControlPath=/tmp/cp -o BatchMode=yes "
        "-i /home/alice/.ssh/id_ed25519 -p 2222"
    )


def test_rsync_remote_target_omits_empty_user():
    ops = FileOperations(_session())

    assert ops._get_rsync_remote_target(_session(user="alice")) == "alice@example.com"
    assert ops._get_rsync_remote_target(_session(user="")) == "example.com"


def test_rsync_compression_auto_skips_media_files():
    ops = FileOperations(_session())

    flags = ops._get_rsync_archive_flags(
        "/srv/movie.mkv", "/home/alice/movie.mkv", False, "auto"
    )

    assert flags == "-av"


def test_rsync_compression_auto_keeps_directory_compression():
    ops = FileOperations(_session())

    flags = ops._get_rsync_archive_flags("/srv/project", "/tmp/project", True, "auto")

    assert flags == "-avz"


def test_rsync_compression_modes_override_auto_detection():
    ops = FileOperations(_session())

    assert (
        ops._get_rsync_archive_flags(
            "/srv/movie.mkv", "/tmp/movie.mkv", False, "always"
        )
        == "-avz"
    )
    assert (
        ops._get_rsync_archive_flags(
            "/srv/readme.txt", "/tmp/readme.txt", False, "never"
        )
        == "-av"
    )


def test_accelerated_download_selection_requires_large_ssh_item():
    ops = FileOperations(_session())
    ops._get_accelerated_download_settings = MagicMock(
        return_value=(True, 6, 1024, 30, "accept-new")
    )

    assert ops._should_use_accelerated_download(_session(), False, 2048) is True
    assert ops._should_use_accelerated_download(_session(), False, 512) is False
    assert ops._should_use_accelerated_download(_session(), True, 2048) is True
    assert ops._should_use_accelerated_download(_session(), True, 512) is False
    assert (
        ops._should_use_accelerated_download(
            _session(proxy_jump="alice@bastion.example.com"), False, 2048
        )
        is False
    )
    assert (
        ops._should_use_accelerated_download(
            _session(session_type="local"), False, 2048
        )
        is False
    )


def test_start_download_uses_accelerated_path_when_eligible(tmp_path):
    ops = FileOperations(_session())
    ops._should_use_accelerated_download = MagicMock(return_value=True)
    ops._start_accelerated_download_with_fallback = MagicMock()
    ops._transfer_with_progress = MagicMock()

    ops.start_download_with_progress(
        "transfer-1", _session(), "/srv/movie.mkv", tmp_path / "movie.mkv", False, 2048
    )

    ops._start_accelerated_download_with_fallback.assert_called_once()
    ops._transfer_with_progress.assert_not_called()


def test_accelerated_directory_download_calls_recursive_downloader(
    monkeypatch, tmp_path
):
    class ImmediateThread:
        def __init__(self, target, daemon):
            self._target = target
            self.daemon = daemon

        def start(self):
            self._target()

    class DirectoryDownloader:
        called = {}

        def __init__(self, *_args, **_kwargs):
            pass

        def download(self, *_args, **_kwargs):
            raise AssertionError("single-file downloader should not run")

        def download_directory(self, remote_path, local_path, *, min_segment_size):
            self.called.update(
                {
                    "remote_path": remote_path,
                    "local_path": local_path,
                    "min_segment_size": min_segment_size,
                }
            )

    ops = FileOperations(_session())
    ops._get_accelerated_download_settings = MagicMock(
        return_value=(True, 6, 1024, 30, "accept-new")
    )
    ops._transfer_with_progress = MagicMock()
    monkeypatch.setattr(operations_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        operations_module, "AsyncSSHSegmentedDownloader", DirectoryDownloader
    )

    ops._start_accelerated_download_with_fallback(
        "transfer-1",
        _session(),
        "/srv/media",
        tmp_path / "media",
        True,
        4096,
        cancellation_event=threading.Event(),
    )

    assert DirectoryDownloader.called == {
        "remote_path": "/srv/media",
        "local_path": tmp_path / "media",
        "min_segment_size": 1024,
    }
    ops._transfer_with_progress.assert_not_called()


def test_accelerated_download_unavailable_falls_back_to_existing_transfer(
    monkeypatch, tmp_path
):
    class ImmediateThread:
        def __init__(self, target, daemon):
            self._target = target
            self.daemon = daemon

        def start(self):
            self._target()

    class UnavailableDownloader:
        def __init__(self, *_args, **_kwargs):
            pass

        def download(self, *_args, **_kwargs):
            raise AcceleratedDownloadUnavailable("not available")

    ops = FileOperations(_session())
    ops._get_accelerated_download_settings = MagicMock(
        return_value=(True, 6, 1024, 30, "accept-new")
    )
    ops._transfer_with_progress = MagicMock()
    monkeypatch.setattr(operations_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        operations_module, "AsyncSSHSegmentedDownloader", UnavailableDownloader
    )

    ops._start_accelerated_download_with_fallback(
        "transfer-1",
        _session(),
        "/srv/movie.mkv",
        tmp_path / "movie.mkv",
        False,
        2048,
        cancellation_event=threading.Event(),
    )

    ops._transfer_with_progress.assert_called_once()


def test_sftp_fallback_command_uses_session_ssh_options():
    session = _session()
    spawner = MagicMock()
    spawner._get_base_ssh_options.return_value = {"ControlPath": "/tmp/cp"}
    spawner.command_builder.build_remote_command.return_value = [
        "/usr/bin/sftp",
        "-o",
        "ControlPath=/tmp/cp",
        "-o",
        "BatchMode=yes",
        "-i",
        "/home/alice/.ssh/id_ed25519",
        "-P",
        "2222",
        "alice@example.com",
    ]
    ops = FileOperations(session)

    cmd = ops._build_sftp_command(spawner, session)

    spawner.command_builder.build_remote_command.assert_called_once_with(
        "sftp",
        hostname="example.com",
        username="alice",
        port=2222,
        key_file="/home/alice/.ssh/id_ed25519",
        options={"ControlPath": "/tmp/cp", "BatchMode": "yes"},
    )
    assert cmd[-1] == "alice@example.com"


def test_transfer_completion_scheduler_allows_missing_callback(monkeypatch):
    idle_add = MagicMock()
    monkeypatch.setattr(operations_module.GLib, "idle_add", idle_add)
    ops = FileOperations(_session())

    ops._schedule_transfer_completion(None, "transfer-1", True, "done")

    idle_add.assert_not_called()


def test_transfer_completion_scheduler_uses_idle_add(monkeypatch):
    idle_add = MagicMock()
    monkeypatch.setattr(operations_module.GLib, "idle_add", idle_add)
    callback = MagicMock()
    ops = FileOperations(_session())

    ops._schedule_transfer_completion(callback, "transfer-1", False, "failed")

    idle_add.assert_called_once_with(callback, "transfer-1", False, "failed")


def test_transfer_error_parser_summarizes_remote_io_errors():
    ops = FileOperations(_session())

    message = ops._parse_transfer_error(
        'rsync: [sender] send_files failed to open "/remote/movie.mkv": Input/output error (5)\n'
        "rsync error: some files/attrs were not transferred"
    )

    assert "input/output error" in message.lower()
    assert "/remote/movie.mkv" in message


def test_exception_formatter_keeps_type_for_generic_messages():
    assert _format_exception_for_log(Exception("Failure")) == "Exception: Failure"
    assert _format_exception_for_log(RuntimeError()) == "RuntimeError"


def test_remove_sftp_batch_file_is_best_effort(tmp_path):
    batch_file = tmp_path / "transfer.sftp"
    batch_file.write_text("quit\n", encoding="utf-8")
    ops = FileOperations(_session())

    ops._remove_sftp_batch_file(str(batch_file))
    ops._remove_sftp_batch_file(str(batch_file))
    ops._remove_sftp_batch_file(None)

    assert not batch_file.exists()


def test_sftp_batch_quotes_spaces_quotes_and_backslashes():
    ops = FileOperations(_session())

    batch = ops._build_sftp_batch(
        '/tmp/source "copy"\\file.txt',
        "/remote/target folder/file.txt",
        is_directory=False,
        is_download=False,
    )

    assert batch == (
        'put -r "/tmp/source \\"copy\\"\\\\file.txt" '
        '"/remote/target folder/file.txt"\nquit\n'
    )


@pytest.mark.parametrize("unsafe_character", ["\n", "\r", "\0"])
def test_sftp_batch_rejects_command_separators(unsafe_character):
    ops = FileOperations(_session())

    with pytest.raises(ValueError):
        ops._build_sftp_batch(
            f"/tmp/source{unsafe_character}put /etc/passwd /tmp/leak",
            "/remote/target",
            is_directory=False,
            is_download=False,
        )
