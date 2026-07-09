"""Tests for live manual SSH target tracking."""

from unittest.mock import MagicMock

from ashyterm.terminal.registry import ManualSSHTracker


def test_same_child_count_still_updates_changed_ssh_target() -> None:
    ssh_process = MagicMock(cmdline=MagicMock(return_value=["ssh", "root@new-node"]))
    parent_process = MagicMock()
    parent_process.children.return_value = [ssh_process]
    psutil_module = MagicMock()
    psutil_module.Process.return_value = parent_process
    tracker = ManualSSHTracker(MagicMock(), MagicMock())
    tracker._update_ssh_state = MagicMock()
    tracker._last_child_count[4] = 1
    state = {
        "in_ssh": True,
        "ssh_target": "root@old-node",
        "terminal_ref": MagicMock(),
    }

    tracker._check_ssh_state(4, state, 123, psutil_module)

    tracker._update_ssh_state.assert_called_once_with(4, state, "root@new-node", True)


def test_same_nonzero_child_count_detects_new_ssh_session() -> None:
    ssh_process = MagicMock(
        cmdline=MagicMock(return_value=["tailscale", "ssh", "root@node"])
    )
    parent_process = MagicMock()
    parent_process.children.return_value = [ssh_process]
    psutil_module = MagicMock()
    psutil_module.Process.return_value = parent_process
    tracker = ManualSSHTracker(MagicMock(), MagicMock())
    tracker._update_ssh_state = MagicMock()
    tracker._last_child_count[4] = 1
    state = {
        "in_ssh": False,
        "ssh_target": None,
        "terminal_ref": MagicMock(),
    }

    tracker._check_ssh_state(4, state, 123, psutil_module)

    tracker._update_ssh_state.assert_called_once_with(4, state, "root@node", True)
