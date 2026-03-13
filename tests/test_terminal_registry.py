# tests/test_terminal_registry.py
"""Tests for TerminalRegistry and TerminalLifecycleManager."""

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

# Registry uses gi.repository at module level — mock before import
_mock_gi = MagicMock()
_mock_gi.require_version = MagicMock()
_mock_vte = MagicMock()
_mock_gi.repository.Vte = _mock_vte
_mock_gi.repository.GLib = MagicMock()

with patch.dict(
    "sys.modules",
    {
        "gi": _mock_gi,
        "gi.repository": _mock_gi.repository,
        "ashyterm.sessions.models": MagicMock(),
        "ashyterm.utils.logger": MagicMock(
            get_logger=MagicMock(return_value=MagicMock())
        ),
    },
):
    sys.modules.pop("ashyterm.terminal.registry", None)
    from ashyterm.terminal.registry import (
        TerminalLifecycleManager,
        TerminalRegistry,
        TerminalState,
    )


class FakeTerminal:
    """Lightweight stand-in for Vte.Terminal that cooperates with weakref."""

    pass


# ── TerminalState Enum ──


class TestTerminalState:
    def test_values(self):
        assert TerminalState.FOCUSED.value == "focused"
        assert TerminalState.EXITED.value == "exited"
        assert TerminalState.SPAWN_FAILED.value == "spawn_failed"


# ── TerminalRegistry ──


class TestTerminalRegistry:
    @pytest.fixture
    def registry(self):
        return TerminalRegistry()

    def test_register_terminal(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "Local")
        assert isinstance(tid, int)
        assert tid >= 1

    def test_sequential_ids(self, registry):
        t1, t2 = FakeTerminal(), FakeTerminal()
        id1 = registry.register_terminal(t1, "local", "A")
        id2 = registry.register_terminal(t2, "local", "B")
        assert id2 == id1 + 1

    def test_get_terminal_info(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "ssh", "my-server")
        info = registry.get_terminal_info(tid)
        assert info["type"] == "ssh"
        assert info["identifier"] == "my-server"
        assert info["status"] == "initializing"

    def test_get_terminal_info_returns_copy(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        info1 = registry.get_terminal_info(tid)
        info1["status"] = "hacked"
        info2 = registry.get_terminal_info(tid)
        assert info2["status"] == "initializing"

    def test_get_terminal(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        assert registry.get_terminal(tid) is terminal

    def test_get_terminal_id(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        assert registry.get_terminal_id(terminal) == tid

    def test_get_terminal_id_not_found(self, registry):
        other = FakeTerminal()
        assert registry.get_terminal_id(other) is None

    def test_unregister_terminal(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        assert registry.unregister_terminal(tid) is True
        assert registry.get_terminal_info(tid) == {}
        assert registry.unregister_terminal(tid) is False

    def test_get_active_terminal_count(self, registry):
        t1, t2, t3 = FakeTerminal(), FakeTerminal(), FakeTerminal()
        id1 = registry.register_terminal(t1, "local", "A")
        registry.register_terminal(t2, "local", "B")
        registry.register_terminal(t3, "local", "C")
        # All are initializing → counted as active
        assert registry.get_active_terminal_count() == 3

        registry.update_terminal_status(id1, "exited")
        assert registry.get_active_terminal_count() == 2

    def test_update_terminal_process(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        registry.update_terminal_process(tid, 12345)
        info = registry.get_terminal_info(tid)
        assert info["process_id"] == 12345
        assert info["status"] == "running"

    def test_get_terminals_by_type(self, registry):
        t1, t2, t3 = FakeTerminal(), FakeTerminal(), FakeTerminal()
        registry.register_terminal(t1, "local", "A")
        id2 = registry.register_terminal(t2, "ssh", "B")
        id3 = registry.register_terminal(t3, "ssh", "C")
        ssh_ids = registry.get_terminals_by_type("ssh")
        assert set(ssh_ids) == {id2, id3}

    def test_get_terminals_by_status(self, registry):
        t1, t2 = FakeTerminal(), FakeTerminal()
        id1 = registry.register_terminal(t1, "local", "A")
        id2 = registry.register_terminal(t2, "local", "B")
        registry.update_terminal_status(id1, "running")
        assert registry.get_terminals_by_status("running") == [id1]
        assert registry.get_terminals_by_status("initializing") == [id2]

    def test_get_all_terminal_ids(self, registry):
        t1, t2 = FakeTerminal(), FakeTerminal()
        id1 = registry.register_terminal(t1, "local", "A")
        id2 = registry.register_terminal(t2, "local", "B")
        assert set(registry.get_all_terminal_ids()) == {id1, id2}

    def test_deregister_terminal_for_move(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        info = registry.deregister_terminal_for_move(tid)
        assert info is not None
        assert info["type"] == "local"
        # Terminal should be gone
        assert registry.get_terminal_info(tid) == {}

    def test_reregister_terminal(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        info = registry.deregister_terminal_for_move(tid)

        new_term = FakeTerminal()
        registry.reregister_terminal(new_term, tid, info)
        assert registry.get_terminal(tid) is new_term
        assert registry.get_terminal_info(tid)["type"] == "local"

    def test_update_connection_status_connected(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "ssh", "S")
        registry.update_terminal_connection_status(tid, connected=True)
        info = registry.get_terminal_info(tid)
        assert info["status"] == "connected"
        assert "connected_at" in info

    def test_update_connection_status_disconnected(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "ssh", "S")
        registry.update_terminal_connection_status(
            tid, connected=False, error_message="timeout"
        )
        info = registry.get_terminal_info(tid)
        assert info["status"] == "disconnected"
        assert info["last_error"] == "timeout"

    def test_increment_reconnect_attempts(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "ssh", "S")
        assert registry.increment_reconnect_attempts(tid) == 1
        assert registry.increment_reconnect_attempts(tid) == 2
        assert registry.increment_reconnect_attempts(tid) == 3

    def test_reconnect_resets_on_connect(self, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "ssh", "S")
        registry.increment_reconnect_attempts(tid)
        registry.increment_reconnect_attempts(tid)
        registry.update_terminal_connection_status(tid, connected=True)
        info = registry.get_terminal_info(tid)
        assert info["reconnect_attempts"] == 0

    def test_thread_safety(self, registry):
        """Concurrent register/unregister should not corrupt state."""
        terminals = [FakeTerminal() for _ in range(50)]
        ids = []
        lock = threading.Lock()

        def register_batch(batch):
            for t in batch:
                tid = registry.register_terminal(t, "local", "L")
                with lock:
                    ids.append(tid)

        threads = [
            threading.Thread(target=register_batch, args=(terminals[i : i + 10],))
            for i in range(0, 50, 10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 50
        assert len(set(ids)) == 50  # All unique


# ── TerminalLifecycleManager ──


class TestTerminalLifecycleManager:
    @pytest.fixture
    def registry(self):
        return TerminalRegistry()

    @pytest.fixture
    def lifecycle(self, registry):
        return TerminalLifecycleManager(registry, MagicMock())

    def test_mark_closing_first_time(self, lifecycle):
        assert lifecycle.mark_terminal_closing(1) is True

    def test_mark_closing_already_closing(self, lifecycle):
        lifecycle.mark_terminal_closing(1)
        assert lifecycle.mark_terminal_closing(1) is False

    def test_unmark_closing(self, lifecycle):
        lifecycle.mark_terminal_closing(1)
        lifecycle.unmark_terminal_closing(1)
        assert lifecycle.mark_terminal_closing(1) is True

    def test_transition_state(self, lifecycle, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        assert lifecycle.transition_state(tid, TerminalState.FOCUSED) is True
        info = registry.get_terminal_info(tid)
        assert info["status"] == "focused"

    def test_transition_to_exited_twice_blocked(self, lifecycle, registry):
        terminal = FakeTerminal()
        tid = registry.register_terminal(terminal, "local", "L")
        assert lifecycle.transition_state(tid, TerminalState.EXITED) is True
        assert lifecycle.transition_state(tid, TerminalState.EXITED) is False

    def test_transition_nonexistent_terminal(self, lifecycle):
        assert lifecycle.transition_state(999, TerminalState.FOCUSED) is False
