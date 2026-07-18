"""Tests for manual OpenSSH and Tailscale SSH process detection."""

from unittest.mock import MagicMock

from ashyterm.terminal.ssh_process_detection import (
    extract_ssh_target,
    find_ssh_process,
)


def test_extracts_openssh_user_and_host() -> None:
    assert extract_ssh_target(["/usr/bin/ssh", "root@100.64.0.10"]) == (
        "root@100.64.0.10"
    )


def test_extracts_openssh_alias_after_options() -> None:
    command = ["ssh", "-v", "-p", "2222", "-i", "/tmp/key", "server-alias"]
    assert extract_ssh_target(command) == "server-alias"


def test_extracts_tailscale_ssh_target() -> None:
    assert extract_ssh_target(["tailscale", "ssh", "admin@build-server"]) == (
        "admin@build-server"
    )


def test_rejects_non_ssh_tailscale_command() -> None:
    assert extract_ssh_target(["tailscale", "status"]) is None


def test_find_skips_exited_process_and_returns_tailscale() -> None:
    exited = MagicMock(cmdline=MagicMock(side_effect=RuntimeError("gone")))
    tailscale = MagicMock(
        cmdline=MagicMock(return_value=["tailscale", "ssh", "root@node"])
    )

    process, target = find_ssh_process([exited, tailscale])

    assert process is tailscale
    assert target == "root@node"
