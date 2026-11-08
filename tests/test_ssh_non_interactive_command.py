"""Tests for SSHSpawnMixin._build_non_interactive_ssh_command."""

from types import SimpleNamespace

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal import ssh_spawn_mixin
from ashyterm.terminal.ssh_spawn_mixin import SSHSpawnMixin


class _FakeBuilder:
    def __init__(self):
        self.kwargs = None

    def build_remote_command(self, program, **kwargs):
        self.kwargs = kwargs
        return [program, "target"]


class _Spawner(SSHSpawnMixin):
    def __init__(self, cache_dir):
        self.platform_info = SimpleNamespace(cache_dir=cache_dir)
        self.settings_manager = SimpleNamespace(get=lambda key, default=None: default)
        self.command_builder = _FakeBuilder()

    def _get_strict_host_key_checking(self):
        return "accept-new"

    def _create_sshpass_file(self, password):
        return "/run/pass-file"


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
def spawner(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(ssh_spawn_mixin, "has_command", lambda name: True)
    return _Spawner(tmp_path / "cache")


@pytest.fixture
def password_session(monkeypatch):
    # The password normally comes from the keyring; stub the getter.
    monkeypatch.setattr(SessionItem, "auth_value", property(lambda self: "s3cret"))
    return _session(auth_type="password", auth_value="")


def test_key_auth_keeps_batchmode_and_shared_control_path(spawner):
    session = _session()
    cmd, env = spawner._build_non_interactive_ssh_command(session, ["uptime"])
    opts = spawner.command_builder.kwargs["options"]
    assert opts["BatchMode"] == "yes"
    assert opts["ControlPath"] == spawner._get_ssh_control_path(session)
    assert opts["ControlPersist"] == "600"
    assert cmd == ["ssh", "target", "uptime"]
    assert env is None


def test_password_auth_disables_batchmode_and_uses_sshpass_file(
    spawner, password_session
):
    cmd, env = spawner._build_non_interactive_ssh_command(
        password_session, ["uptime"]
    )
    opts = spawner.command_builder.kwargs["options"]
    assert opts["BatchMode"] == "no"
    assert cmd[:3] == ["sshpass", "-f", "/run/pass-file"]
    assert env == {"_ASHYTERM_SSHPASS_FILE": "/run/pass-file"}
    assert "s3cret" not in " ".join(cmd)


def test_password_auth_without_sshpass_raises(
    spawner, password_session, monkeypatch
):
    monkeypatch.setattr(ssh_spawn_mixin, "has_command", lambda name: name != "sshpass")
    with pytest.raises(ssh_spawn_mixin.SSHConnectionError):
        spawner._build_non_interactive_ssh_command(password_session, ["uptime"])


def test_remote_command_is_shell_quoted(spawner):
    cmd, _ = spawner._build_non_interactive_ssh_command(
        _session(), ["ls", "-la", "my dir"]
    )
    assert cmd[-1] == "ls -la 'my dir'"
