"""Tests for tab_close (tab close-flow helpers)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from ashyterm.terminal.tab_close import (
    any_terminal_has_foreground_process,
    build_close_confirmation_dialog,
    has_stable_running_process,
    process_terminals_for_close,
)


def _fake_terminal(tid: int = 1) -> SimpleNamespace:
    """Tiny stand-in for a Vte.Terminal carrying a terminal_id attr."""
    return SimpleNamespace(terminal_id=tid)


def _fake_manager(*, registry_returns=None, auto_reconnect=False) -> MagicMock:
    manager = MagicMock()
    manager.registry.get_terminal_info = MagicMock(
        side_effect=lambda tid: (registry_returns or {}).get(tid)
    )
    manager.is_auto_reconnect_active = MagicMock(return_value=auto_reconnect)
    manager.remove_terminal = MagicMock()
    return manager


# ── has_stable_running_process ──────────────────────────────


class TestHasStableRunningProcess:
    def test_none_is_false(self):
        assert has_stable_running_process(None) is False

    def test_empty_dict_is_false(self):
        assert has_stable_running_process({}) is False

    def test_placeholder_pid_is_false(self):
        assert (
            has_stable_running_process(
                {"process_id": -1, "status": "running"}
            )
            is False
        )

    def test_zero_pid_is_false(self):
        assert (
            has_stable_running_process(
                {"process_id": 0, "status": "running"}
            )
            is False
        )

    def test_exited_status_is_false(self):
        assert (
            has_stable_running_process(
                {"process_id": 1234, "status": "exited"}
            )
            is False
        )

    def test_running_with_real_pid_is_true(self):
        assert (
            has_stable_running_process(
                {"process_id": 1234, "status": "running"}
            )
            is True
        )


# ── any_terminal_has_foreground_process ─────────────────────


class TestAnyTerminalHasForegroundProcess:
    def test_no_psutil_returns_false(self, monkeypatch):
        # Simulate an environment without psutil installed.
        monkeypatch.setitem(__import__("sys").modules, "psutil", None)
        manager = _fake_manager(registry_returns={1: {"process_id": 99}})
        # Trigger the ImportError path: reload the module's local view.
        import importlib
        import ashyterm.terminal.tab_close as mod

        importlib.reload(mod)
        # Feed a terminal whose registry entry has a pid — and confirm
        # the helper falls through to False because psutil isn't there.
        # (Use a stub that raises ImportError on direct import.)
        with patch.dict(
            __import__("sys").modules, {"psutil": None}, clear=False
        ):
            result = mod.any_terminal_has_foreground_process(
                [_fake_terminal(1)], terminal_manager=manager
            )
        assert result is False

    def test_no_children_returns_false(self):
        manager = _fake_manager(
            registry_returns={1: {"process_id": 1234, "status": "running"}}
        )

        fake_proc = MagicMock()
        fake_proc.children = MagicMock(return_value=[])
        with patch("psutil.Process", return_value=fake_proc):
            assert (
                any_terminal_has_foreground_process(
                    [_fake_terminal(1)], terminal_manager=manager
                )
                is False
            )

    def test_child_detected_returns_true(self):
        manager = _fake_manager(
            registry_returns={1: {"process_id": 1234, "status": "running"}}
        )

        fake_proc = MagicMock()
        fake_proc.children = MagicMock(return_value=[MagicMock()])
        with patch("psutil.Process", return_value=fake_proc):
            assert (
                any_terminal_has_foreground_process(
                    [_fake_terminal(1)], terminal_manager=manager
                )
                is True
            )

    def test_no_such_process_is_handled(self):
        """psutil can race with the process exiting; we must not
        crash — treat the PID as "no child" and keep scanning.
        """
        import psutil

        manager = _fake_manager(
            registry_returns={
                1: {"process_id": 1234, "status": "running"},
                2: {"process_id": 5678, "status": "running"},
            }
        )

        def side_effect(pid):
            if pid == 1234:
                raise psutil.NoSuchProcess(pid)
            proc = MagicMock()
            proc.children = MagicMock(return_value=[MagicMock()])
            return proc

        with patch("psutil.Process", side_effect=side_effect):
            # Second PID does have a child ⇒ returns True.
            assert (
                any_terminal_has_foreground_process(
                    [_fake_terminal(1), _fake_terminal(2)],
                    terminal_manager=manager,
                )
                is True
            )

    def test_terminal_without_registry_entry_is_skipped(self):
        manager = _fake_manager(registry_returns={})
        # No psutil call needs to happen; the terminal is simply ignored.
        with patch("psutil.Process") as proc:
            result = any_terminal_has_foreground_process(
                [_fake_terminal(1)], terminal_manager=manager
            )
        assert result is False
        proc.assert_not_called()

    def test_terminal_without_id_is_skipped(self):
        manager = _fake_manager()
        terminal = SimpleNamespace()  # no terminal_id
        assert (
            any_terminal_has_foreground_process(
                [terminal], terminal_manager=manager
            )
            is False
        )


# ── process_terminals_for_close ─────────────────────────────


class TestProcessTerminalsForClose:
    def test_stable_running_triggers_wait(self):
        manager = _fake_manager(
            registry_returns={1: {"process_id": 1234, "status": "running"}}
        )
        out = process_terminals_for_close(
            [_fake_terminal(1)], terminal_manager=manager
        )
        assert out is True
        manager.remove_terminal.assert_called_once()
        # force_kill_group=True is passed unconditionally — ensures the
        # backgrounded child group is cleaned up even when we waited.
        call_kwargs = manager.remove_terminal.call_args.kwargs
        assert call_kwargs.get("force_kill_group") is True

    def test_exited_terminal_does_not_trigger_wait(self):
        manager = _fake_manager(
            registry_returns={1: {"process_id": 1234, "status": "exited"}}
        )
        out = process_terminals_for_close(
            [_fake_terminal(1)], terminal_manager=manager
        )
        assert out is False

    def test_auto_reconnect_bypasses_wait(self):
        """Even with a live child, an auto-reconnecting SSH terminal
        should not block the close path — its whole contract is that
        it may come back.
        """
        manager = _fake_manager(
            registry_returns={1: {"process_id": 1234, "status": "running"}},
            auto_reconnect=True,
        )
        out = process_terminals_for_close(
            [_fake_terminal(1)], terminal_manager=manager
        )
        assert out is False
        manager.remove_terminal.assert_called_once()

    def test_no_terminal_id_does_not_query_registry(self):
        manager = _fake_manager()
        # Terminal without terminal_id — we still call remove_terminal
        # but never hit the registry lookup.
        out = process_terminals_for_close(
            [SimpleNamespace(terminal_id=None)], terminal_manager=manager
        )
        assert out is False
        manager.registry.get_terminal_info.assert_not_called()

    def test_mixed_list_waits_when_any_terminal_is_stable(self):
        manager = _fake_manager(
            registry_returns={
                1: {"process_id": 1234, "status": "running"},
                2: {"process_id": -1, "status": "running"},
            }
        )
        out = process_terminals_for_close(
            [_fake_terminal(1), _fake_terminal(2)], terminal_manager=manager
        )
        assert out is True


# ── build_close_confirmation_dialog ─────────────────────────


class TestBuildCloseConfirmationDialog:
    def test_dialog_has_expected_responses(self):
        on_response = MagicMock()
        dialog = build_close_confirmation_dialog(
            parent=MagicMock(), on_response=on_response
        )
        # Both response buttons must be present. Adw.AlertDialog can be
        # queried with has_response.
        assert dialog.has_response("cancel") is True
        assert dialog.has_response("close") is True

    def test_default_response_is_cancel(self):
        dialog = build_close_confirmation_dialog(
            parent=MagicMock(), on_response=MagicMock()
        )
        assert dialog.get_default_response() == "cancel"

    def test_close_response_label_is_destructive_styled(self):
        # Verified indirectly via set_response_appearance being applied.
        # Adw exposes get_response_appearance in recent versions.
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        dialog = build_close_confirmation_dialog(
            parent=MagicMock(), on_response=MagicMock()
        )
        if hasattr(dialog, "get_response_appearance"):
            assert (
                dialog.get_response_appearance("close")
                == Adw.ResponseAppearance.DESTRUCTIVE
            )


# ── tabs.py delegation ──────────────────────────────────────


class TestTabsDelegation:
    def test_tabs_delegators_exist(self):
        from ashyterm.terminal.tabs import TabManager

        for name in (
            "_any_terminal_has_foreground_process",
            "_has_stable_running_process",
            "_process_terminals_for_close",
            "_confirm_close_tab",
        ):
            assert callable(getattr(TabManager, name))
