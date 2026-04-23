# tests/test_process_tracker_snapshot.py
"""Tests for ProcessTracker.snapshot() introduced to support CWD polling."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_snapshot_returns_copy():
    """snapshot() must not expose the internal dict."""
    from ashyterm.terminal.process_tracker import ProcessTracker

    tracker = ProcessTracker()
    tracker.register_process(123, {"name": "x", "type": "local", "terminal": None})
    snap = tracker.snapshot()
    assert snap[123]["name"] == "x"

    # Mutating the snapshot must not affect the tracker
    snap[999] = {"bogus": True}
    snap_again = tracker.snapshot()
    assert 999 not in snap_again


def test_snapshot_thread_safe_sequential():
    """snapshot() returns coherent state after concurrent writes.

    Each writer registers a disjoint pid range and joins before we read,
    so we don't rely on timing for correctness.
    """
    import threading

    from ashyterm.terminal.process_tracker import ProcessTracker

    tracker = ProcessTracker()

    def writer(start: int, count: int):
        for i in range(start, start + count):
            tracker.register_process(i, {"name": f"p{i}", "type": "local"})

    threads = [
        threading.Thread(target=writer, args=(base, 25), daemon=True)
        for base in (0, 100, 200, 300)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    snap = tracker.snapshot()
    assert len(snap) == 100
    for pid, info in snap.items():
        assert info["name"] == f"p{pid}"
        assert info["type"] == "local"


def test_snapshot_empty():
    from ashyterm.terminal.process_tracker import ProcessTracker

    assert ProcessTracker().snapshot() == {}
