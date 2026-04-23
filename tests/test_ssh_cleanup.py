"""Tests for SSH cleanup module — pure logic where possible."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestCleanupProcessTracking:
    """Tests for cleanup_process_tracking()."""

    def test_unregisters_running_process(self):
        from ashyterm.terminal.ssh_cleanup import cleanup_process_tracking

        class MockTracker:
            def __init__(self):
                self.unregistered = []
            def unregister_process(self, pid):
                self.unregistered.append(pid)

        class MockSpawner:
            def __init__(self):
                self.process_tracker = MockTracker()

        spawner = MockSpawner()
        info = {"process_id": 12345}

        cleanup_process_tracking(info, spawner)
        assert spawner.process_tracker.unregistered == [12345]

    def test_no_pid_no_action(self):
        from ashyterm.terminal.ssh_cleanup import cleanup_process_tracking

        class MockTracker:
            def unregister_process(self, pid):
                raise AssertionError("should not be called")

        class MockSpawner:
            process_tracker = MockTracker()

        info = {"process_id": None}
        cleanup_process_tracking(info, MockSpawner())  # Should not raise

    def test_empty_info(self):
        from ashyterm.terminal.ssh_cleanup import cleanup_process_tracking

        class MockSpawner:
            process_tracker = None  # Should not be accessed

        cleanup_process_tracking({}, MockSpawner())  # Should not raise


class TestGetTerminalName:
    """Tests for get_terminal_name_for_cleanup()."""

    def test_string_identifier(self):
        from ashyterm.terminal.ssh_cleanup import get_terminal_name_for_cleanup

        assert get_terminal_name_for_cleanup({"identifier": "my-ssh-session"}) == "my-ssh-session"

    def test_session_item_identifier(self):
        from ashyterm.terminal.ssh_cleanup import get_terminal_name_for_cleanup

        class FakeSession:
            name = "production-server"

        info = {"identifier": FakeSession()}
        assert get_terminal_name_for_cleanup(info) == "production-server"

    def test_unknown_identifier(self):
        from ashyterm.terminal.ssh_cleanup import get_terminal_name_for_cleanup

        assert get_terminal_name_for_cleanup({}) == "Unknown"

    def test_none_identifier(self):
        from ashyterm.terminal.ssh_cleanup import get_terminal_name_for_cleanup

        assert get_terminal_name_for_cleanup({"identifier": None}) == "Unknown"


class TestCleanupTerminalAttributes:
    """Tests for cleanup_terminal_attributes()."""

    def _make_logger(self):
        class Logger:
            def debug(self, msg): pass
        return Logger()

    def test_removes_tracked_attributes(self):
        from ashyterm.terminal.ssh_cleanup import cleanup_terminal_attributes

        class MockTerminal:
            def __init__(self):
                self._osc8_hovered_uri = "sftp://example.com"  # instance attr
                self._closed_by_user = True  # instance attr
                self.terminal_id = 42  # Should NOT be removed

        terminal = MockTerminal()
        cleanup_terminal_attributes(terminal, self._make_logger())

        assert not hasattr(terminal, "_osc8_hovered_uri")
        assert not hasattr(terminal, "_closed_by_user")
        assert hasattr(terminal, "terminal_id")  # Preserved

    def test_handles_missing_attributes(self):
        from ashyterm.terminal.ssh_cleanup import cleanup_terminal_attributes

        class MockTerminal:
            pass

        terminal = MockTerminal()
        cleanup_terminal_attributes(terminal, self._make_logger())  # Should not raise


class TestFinalizeTerminalCleanup:
    """Tests for finalize_terminal_cleanup()."""

    def test_unregisters_and_updates_stats(self):
        from ashyterm.terminal.ssh_cleanup import finalize_terminal_cleanup

        class MockRegistry:
            def __init__(self):
                self.unregistered = []
            def unregister_terminal(self, tid):
                self.unregistered.append(tid)
                return True

        registry = MockRegistry()
        stats = {"terminals_closed": 0}
        pending_kill_timers = {}
        command_start_times = {1: "data"}

        finalize_terminal_cleanup(
            1, "test-terminal",
            registry, pending_kill_timers, command_start_times,
            stats,
        )

        assert registry.unregistered == [1]
        assert stats["terminals_closed"] == 1
        assert 1 not in command_start_times

    def test_registry_failure_no_stats_update(self):
        from ashyterm.terminal.ssh_cleanup import finalize_terminal_cleanup

        class MockRegistry:
            def unregister_terminal(self, tid):
                return False

        registry = MockRegistry()
        stats = {"terminals_closed": 5}
        command_start_times = {1: "data"}

        finalize_terminal_cleanup(
            1, "test-terminal",
            registry, {}, command_start_times,
            stats,
        )

        assert stats["terminals_closed"] == 5  # Unchanged
