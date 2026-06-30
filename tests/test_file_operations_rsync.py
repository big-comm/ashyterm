"""Tests for FileOperations rsync command helpers."""

from unittest.mock import MagicMock

from ashyterm.filemanager import operations as operations_module
from ashyterm.filemanager.operations import FileOperations
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


def test_remove_sftp_batch_file_is_best_effort(tmp_path):
    batch_file = tmp_path / "transfer.sftp"
    batch_file.write_text("quit\n", encoding="utf-8")
    ops = FileOperations(_session())

    ops._remove_sftp_batch_file(str(batch_file))
    ops._remove_sftp_batch_file(str(batch_file))
    ops._remove_sftp_batch_file(None)

    assert not batch_file.exists()
