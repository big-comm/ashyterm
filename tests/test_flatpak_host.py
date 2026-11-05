"""Tests for the Flatpak host-shell helpers."""

import subprocess
from types import SimpleNamespace

import pytest

from ashyterm.utils import flatpak_host


def _stat(pid: str, start: str) -> str:
    # comm with spaces and parentheses; field 22 (starttime) = `start`
    rest = ["S"] + [str(n) for n in range(4, 53)]
    rest[19] = start
    return f"{pid} (a (weird) name) " + " ".join(rest)


@pytest.fixture
def in_flatpak(monkeypatch):
    monkeypatch.setattr(flatpak_host, "is_flatpak", lambda: True)


def _fake_run(monkeypatch, stdout="", exc=None):
    def run(args, **kwargs):
        if exc:
            raise exc
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(flatpak_host.subprocess, "run", run)


# --- _process_start_time -------------------------------------------------


def test_process_start_time_handles_comm_with_spaces_and_parens():
    assert flatpak_host._process_start_time(_stat("42", "98765")) == "98765"


# --- host_shell_command ---------------------------------------------------


def test_host_shell_command_outside_flatpak_is_unchanged(monkeypatch):
    monkeypatch.setattr(flatpak_host, "is_flatpak", lambda: False)
    cmd = ["/bin/zsh", "-l"]
    assert flatpak_host.host_shell_command(cmd, {}, "/tmp") is cmd


def test_host_shell_command_forwards_only_terminal_env(in_flatpak):
    env = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "SECRET": "x"}
    argv = flatpak_host.host_shell_command(["/bin/zsh"], env, "/work")
    assert argv[:4] == [
        "flatpak-spawn",
        "--host",
        "--watch-bus",
        "--directory=/work",
    ]
    assert "--env=TERM=xterm-256color" in argv
    assert "--env=COLORTERM=truecolor" in argv
    assert not any("SECRET" in a for a in argv)
    assert argv[-1] == "/bin/zsh"


def test_host_shell_command_records_host_pid_when_state_given(in_flatpak):
    terminal = SimpleNamespace()
    env = {"_ASHYTERM_HOST_STATE": "/cache/state"}
    argv = flatpak_host.host_shell_command(["/bin/bash"], env, "/w", terminal)
    i = argv.index("sh")
    assert argv[i : i + 5] == [
        "sh",
        "-c",
        flatpak_host._RECORD_HOST_PROCESS,
        "ashyterm-host",
        "/cache/state",
    ]
    assert argv[-1] == "/bin/bash"
    assert terminal._flatpak_host_state == "/cache/state"


# --- host_terminal_has_children -------------------------------------------


@pytest.fixture
def terminal(tmp_path):
    state = tmp_path / "host-process"
    state.write_text(f"1234\n{_stat('1234', '500')}\n")
    return SimpleNamespace(_flatpak_host_state=str(state))


def test_no_children_returns_false(monkeypatch, terminal):
    _fake_run(monkeypatch, stdout=f"{_stat('1234', '500')}\n\n")
    assert flatpak_host.host_terminal_has_children(terminal) is False


def test_children_returns_true(monkeypatch, terminal):
    _fake_run(monkeypatch, stdout=f"{_stat('1234', '500')}\n5678\n")
    assert flatpak_host.host_terminal_has_children(terminal) is True


def test_reused_pid_returns_true(monkeypatch, terminal):
    # Same PID, different start time: not our shell anymore.
    _fake_run(monkeypatch, stdout=f"{_stat('1234', '999')}\n\n")
    assert flatpak_host.host_terminal_has_children(terminal) is True


@pytest.mark.parametrize(
    "exc",
    [subprocess.TimeoutExpired("flatpak-spawn", 2), OSError("boom")],
)
def test_host_errors_ask_for_confirmation(monkeypatch, terminal, exc):
    _fake_run(monkeypatch, exc=exc)
    assert flatpak_host.host_terminal_has_children(terminal) is True


def test_missing_state_asks_for_confirmation():
    assert flatpak_host.host_terminal_has_children(SimpleNamespace()) is True


def test_invalid_pid_asks_for_confirmation(tmp_path):
    state = tmp_path / "host-process"
    state.write_text("abc\nstat\n")
    t = SimpleNamespace(_flatpak_host_state=str(state))
    assert flatpak_host.host_terminal_has_children(t) is True


# --- get_host_shell -------------------------------------------------------


@pytest.fixture
def fresh_shell_cache():
    flatpak_host.get_host_shell.cache_clear()
    yield
    flatpak_host.get_host_shell.cache_clear()


def test_get_host_shell_returns_host_shell(monkeypatch, fresh_shell_cache):
    _fake_run(monkeypatch, stdout="/run/current-system/sw/bin/zsh")
    assert flatpak_host.get_host_shell() == "/run/current-system/sw/bin/zsh"


@pytest.mark.parametrize(
    "stdout,exc",
    [
        ("zsh", None),
        ("", OSError("flatpak-spawn missing")),
        ("", subprocess.TimeoutExpired("flatpak-spawn", 10)),
        ("", subprocess.CalledProcessError(1, "flatpak-spawn")),
    ],
)
def test_get_host_shell_falls_back_to_sh(
    monkeypatch, fresh_shell_cache, stdout, exc
):
    _fake_run(monkeypatch, stdout=stdout, exc=exc)
    assert flatpak_host.get_host_shell() == "/bin/sh"
