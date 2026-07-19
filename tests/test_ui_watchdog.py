"""Tests for GTK main-loop stall diagnostics."""

from unittest.mock import MagicMock

from ashyterm.core.ui_watchdog import UiWatchdog


def test_stall_is_reported_once_until_next_heartbeat(tmp_path, monkeypatch):
    watchdog = UiWatchdog(tmp_path, stall_timeout_seconds=5.0)
    dump_report = MagicMock()
    monkeypatch.setattr(watchdog, "_dump_stall_report", dump_report)
    monkeypatch.setattr("ashyterm.core.ui_watchdog.time.monotonic", lambda: 12.0)
    watchdog._active = True
    watchdog._last_heartbeat = 0.0

    watchdog._check_for_stall(6.0)
    watchdog._check_for_stall(7.0)
    watchdog._record_heartbeat()
    watchdog._check_for_stall(18.0)

    assert dump_report.call_count == 2


def test_stall_report_contains_context_and_stacks(tmp_path, monkeypatch):
    watchdog = UiWatchdog(tmp_path)
    monkeypatch.setattr(
        watchdog,
        "_format_native_wait_states",
        lambda: "Native thread wait states:\n  tid=1 name=ashyterm wchan=futex\n",
    )

    watchdog._dump_stall_report(7.25)

    report_path = watchdog.report_path
    assert report_path is not None
    report = report_path.read_text(encoding="utf-8")
    assert "AshyTerm UI stall detected" in report
    assert "stalled_for_seconds=7.250" in report
    assert "wchan=futex" in report
    assert "Python thread stacks:" in report
    assert "test_stall_report_contains_context_and_stacks" in report


def test_start_and_stop_manage_glib_source(tmp_path, monkeypatch):
    timeout_add = MagicMock(return_value=73)
    source_remove = MagicMock()
    worker = MagicMock()
    thread_factory = MagicMock(return_value=worker)
    monkeypatch.setattr("ashyterm.core.ui_watchdog.GLib.timeout_add", timeout_add)
    monkeypatch.setattr("ashyterm.core.ui_watchdog.GLib.source_remove", source_remove)
    monkeypatch.setattr("ashyterm.core.ui_watchdog.threading.Thread", thread_factory)

    watchdog = UiWatchdog(tmp_path)

    assert watchdog.start() is True
    watchdog.stop()

    timeout_add.assert_called_once()
    worker.start.assert_called_once_with()
    source_remove.assert_called_once_with(73)
    worker.join.assert_called_once_with(timeout=1.0)


def test_start_failure_cleans_up_without_raising(tmp_path, monkeypatch):
    source_remove = MagicMock()
    worker = MagicMock()
    worker.start.side_effect = RuntimeError("thread unavailable")
    worker.join.side_effect = RuntimeError("thread not started")
    monkeypatch.setattr(
        "ashyterm.core.ui_watchdog.GLib.timeout_add", lambda *_args: 91
    )
    monkeypatch.setattr("ashyterm.core.ui_watchdog.GLib.source_remove", source_remove)
    monkeypatch.setattr(
        "ashyterm.core.ui_watchdog.threading.Thread", lambda **_kwargs: worker
    )

    watchdog = UiWatchdog(tmp_path)

    assert watchdog.start() is False
    source_remove.assert_called_once_with(91)
