"""Tests for SSH session operations — pure logic, no GTK/VTE required."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ashyterm.sessions.models import SessionItem


class MockRegistry:
    """Minimal registry mock for session ops tests."""

    def __init__(self, terminals=None):
        self._terminals = terminals or {}
        self._terminal_instances = {}  # Cache terminal instances

    def get_terminals_for_session(self, session_name):
        return [
            tid for tid, info in self._terminals.items()
            if info.get("session") == session_name
        ]

    def get_terminal_info(self, terminal_id):
        return self._terminals.get(terminal_id)

    def get_terminal(self, terminal_id):
        if terminal_id not in self._terminal_instances:
            info = self._terminals.get(terminal_id)
            if info:
                self._terminal_instances[terminal_id] = MockTerminal(info)
        return self._terminal_instances.get(terminal_id)


class MockTerminal:
    """Minimal terminal mock."""

    def __init__(self, info):
        self.info = info
        self._fed = None

    def feed_child(self, data):
        self._fed = data


class MockLogger:
    """Minimal logger mock with .info()/.error()/.debug()."""
    def info(self, msg): pass
    def error(self, msg): pass
    def debug(self, msg): pass


class TestHasActiveSshSessions:
    """Tests for has_active_ssh_sessions()."""

    def test_no_terminals(self):
        from ashyterm.terminal.ssh_session_ops import has_active_ssh_sessions

        registry = MockRegistry({})
        assert has_active_ssh_sessions(registry) is False

    def test_no_ssh_terminals(self):
        from ashyterm.terminal.ssh_session_ops import has_active_ssh_sessions

        registry = MockRegistry({
            1: {"type": "local", "status": "running"},
        })
        assert has_active_ssh_sessions(registry) is False

    def test_ssh_running(self):
        from ashyterm.terminal.ssh_session_ops import has_active_ssh_sessions

        registry = MockRegistry({
            1: {"type": "ssh", "status": "running"},
        })
        assert has_active_ssh_sessions(registry) is True

    def test_ssh_disconnected_not_active(self):
        from ashyterm.terminal.ssh_session_ops import has_active_ssh_sessions

        registry = MockRegistry({
            1: {"type": "ssh", "status": "disconnected"},
        })
        assert has_active_ssh_sessions(registry) is False


class TestGetSessionConnectionStatus:
    """Tests for get_session_connection_status()."""

    def test_no_terminals(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        registry = MockRegistry({})
        result = get_session_connection_status("test", registry)

        assert result["total_terminals"] == 0
        assert result["overall_status"] == "no_terminals"

    def test_all_connected(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "connected", "session": "test", "identifier": s},
            2: {"type": "ssh", "status": "connected", "session": "test", "identifier": s},
        })
        result = get_session_connection_status("test", registry)

        assert result["total_terminals"] == 2
        assert result["status_counts"]["connected"] == 2
        assert result["overall_status"] == "all_connected"

    def test_all_disconnected(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "disconnected", "session": "test", "identifier": s},
        })
        result = get_session_connection_status("test", registry)

        assert result["overall_status"] == "all_disconnected"

    def test_partial_connection(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "connected", "session": "test", "identifier": s},
            2: {"type": "ssh", "status": "disconnected", "session": "test", "identifier": s},
        })
        result = get_session_connection_status("test", registry)

        assert result["overall_status"] == "partial"
        assert result["status_counts"]["connected"] == 1
        assert result["status_counts"]["disconnected"] == 1

    def test_connecting_status(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "connecting", "session": "test", "identifier": s},
        })
        result = get_session_connection_status("test", registry)

        assert result["overall_status"] == "connecting"

    def test_unknown_fallback(self):
        from ashyterm.terminal.ssh_session_ops import get_session_connection_status

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "weird_state", "session": "test", "identifier": s},
        })
        result = get_session_connection_status("test", registry)

        assert result["overall_status"] == "unknown"
        assert result["status_counts"]["other"] == 1


class TestDisconnectAllForSession:
    """Tests for disconnect_all_for_session()."""

    def test_disconnects_running_sessions(self):
        from ashyterm.terminal.ssh_session_ops import disconnect_all_for_session

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "running", "session": "test", "identifier": s},
        })

        cancel_calls = []

        def cancel_auto_reconnect(terminal):
            cancel_calls.append(terminal)

        count = disconnect_all_for_session("test", registry, cancel_auto_reconnect, MockLogger())

        assert count == 1
        assert len(cancel_calls) == 1
        assert registry.get_terminal(1)._fed == b"exit\n"

    def test_skips_nonexistent_terminals(self):
        from ashyterm.terminal.ssh_session_ops import disconnect_all_for_session

        registry = MockRegistry({})
        count = disconnect_all_for_session("test", registry, lambda t: None, MockLogger())
        assert count == 0


class TestReconnectAllForSession:
    """Tests for reconnect_all_for_session()."""

    def test_reconnects_disconnected(self):
        from ashyterm.terminal.ssh_session_ops import reconnect_all_for_session

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "disconnected", "session": "test", "identifier": s},
        })

        respawn_calls = []

        def respawn_fn(terminal, terminal_id, session):
            respawn_calls.append((terminal_id, session.name))

        count = reconnect_all_for_session("test", registry, respawn_fn, MockLogger())

        assert count == 1
        assert respawn_calls[0] == (1, "test")

    def test_skips_running_terminals(self):
        from ashyterm.terminal.ssh_session_ops import reconnect_all_for_session

        s = SessionItem(name="test")
        registry = MockRegistry({
            1: {"type": "ssh", "status": "running", "session": "test", "identifier": s},
        })

        count = reconnect_all_for_session("test", registry, lambda *a: None, MockLogger())
        assert count == 0

    def test_skips_non_session_items(self):
        from ashyterm.terminal.ssh_session_ops import reconnect_all_for_session

        registry = MockRegistry({
            1: {"type": "ssh", "status": "disconnected", "session": "test", "identifier": "not-a-session"},
        })

        count = reconnect_all_for_session("test", registry, lambda *a: None, MockLogger())
        assert count == 0
