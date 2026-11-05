"""Tests for the SSH ControlMaster socket path."""

import os
from types import SimpleNamespace

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.ssh_spawn_mixin import SSHSpawnMixin

UNIX_SOCKET_LIMIT = 108
SSH_TEMP_SUFFIX = 17  # ssh appends ".XXXXXXXXXXXXXXXX" while creating it


class _Spawner(SSHSpawnMixin):
    def __init__(self, cache_dir):
        self.platform_info = SimpleNamespace(cache_dir=cache_dir)


def _session(**kwargs) -> SessionItem:
    base = dict(
        name="s",
        session_type="ssh",
        host="example.com",
        user="alice",
        port=22,
        auth_type="key",
        auth_value="/home/alice/.ssh/id",
    )
    base.update(kwargs)
    return SessionItem(**base)


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(d))
    return d


def test_uses_runtime_dir(tmp_path, runtime_dir):
    path = _Spawner(tmp_path / "cache")._get_ssh_control_path(_session())
    assert os.path.dirname(path) == str(runtime_dir / "ashyterm")
    assert os.path.basename(path).startswith("ssh_control_")
    assert (os.stat(runtime_dir / "ashyterm").st_mode & 0o777) == 0o700


def test_falls_back_to_cache_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    cache = tmp_path / "cache"
    path = _Spawner(cache)._get_ssh_control_path(_session())
    assert os.path.dirname(path) == str(cache)
    assert cache.is_dir()


def test_name_length_does_not_grow_with_host_or_user(tmp_path, runtime_dir):
    spawner = _Spawner(tmp_path / "cache")
    short = spawner._get_ssh_control_path(_session(host="a.io", user="a"))
    long = spawner._get_ssh_control_path(
        _session(host="h" * 200 + ".example.com", user="u" * 64, port=2222)
    )
    expected = len("ssh_control_") + 16
    assert len(os.path.basename(short)) == expected
    assert len(os.path.basename(long)) == expected
    # Under Flatpak XDG_RUNTIME_DIR is /run/user/<uid>: well within the limit.
    flatpak_path = "/run/user/1000/ashyterm/" + os.path.basename(long)
    assert len(flatpak_path) + SSH_TEMP_SUFFIX < UNIX_SOCKET_LIMIT


def test_path_is_stable_and_distinct(tmp_path, runtime_dir):
    spawner = _Spawner(tmp_path / "cache")
    a = spawner._get_ssh_control_path(_session())
    assert a == spawner._get_ssh_control_path(_session())
    assert a != spawner._get_ssh_control_path(_session(port=2222))
    assert a != spawner._get_ssh_control_path(_session(user="bob"))
    assert a != spawner._get_ssh_control_path(_session(host="other.org"))
