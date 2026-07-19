"""Regression tests for asynchronous task lifecycle locking."""

import threading
from concurrent.futures import Future
from unittest.mock import MagicMock

from ashyterm.core.tasks import AsyncTaskManager


def _bare_task_manager() -> AsyncTaskManager:
    manager = AsyncTaskManager.__new__(AsyncTaskManager)
    manager.logger = MagicMock()
    manager._io_executor = None
    manager._cpu_executor = None
    manager._active_futures = set()
    manager._futures_lock = threading.Lock()
    manager._is_shutdown = False
    return manager


def _run_with_deadlock_guard(target: object) -> None:
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive(), "task lifecycle operation deadlocked"


def test_track_already_completed_future_does_not_deadlock() -> None:
    manager = _bare_task_manager()
    future: Future[None] = Future()
    future.set_result(None)

    _run_with_deadlock_guard(lambda: manager._track_future(future))

    assert future not in manager._active_futures


def test_shutdown_cancels_pending_future_without_deadlock() -> None:
    manager = _bare_task_manager()
    future: Future[None] = Future()
    manager._track_future(future)

    _run_with_deadlock_guard(lambda: manager.shutdown(wait=False))

    assert future.cancelled()
    assert future not in manager._active_futures
