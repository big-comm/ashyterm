# ashyterm/core/tasks.py
"""Singleton task manager — one IO pool + one CPU pool, graceful shutdown.

Call sites::

    AsyncTaskManager.get().submit_io(fetch_data, url)
    AsyncTaskManager.get().submit_cpu(process_text, content)
"""

import multiprocessing
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, Set

from ..utils.logger import get_logger


class AsyncTaskManager:
    """Thread-safe singleton owning the IO (4 workers) and CPU (<=4) pools."""

    _instance: Optional["AsyncTaskManager"] = None
    _lock = threading.Lock()

    IO_POOL_SIZE = 4
    CPU_POOL_SIZE = min(4, max(1, multiprocessing.cpu_count()))

    def __init__(self):
        self.logger = get_logger("ashyterm.core.tasks")
        self._io_executor: Optional[ThreadPoolExecutor] = None
        self._cpu_executor: Optional[ThreadPoolExecutor] = None
        self._active_futures: Set[Future] = set()
        self._futures_lock = threading.Lock()
        self._is_shutdown = False

        self._initialize_pools()
        self.logger.info(
            f"AsyncTaskManager initialized (IO workers: {self.IO_POOL_SIZE}, "
            f"CPU workers: {self.CPU_POOL_SIZE})"
        )

    @classmethod
    def get(cls) -> "AsyncTaskManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tear down the singleton. Cancels pending futures — test-only."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown(wait=False)
                cls._instance = None

    def _initialize_pools(self) -> None:
        self._io_executor = ThreadPoolExecutor(
            max_workers=self.IO_POOL_SIZE, thread_name_prefix="ashy-io"
        )
        self._cpu_executor = ThreadPoolExecutor(
            max_workers=self.CPU_POOL_SIZE, thread_name_prefix="ashy-cpu"
        )

    def _track_future(self, future: Future) -> None:
        with self._futures_lock:
            self._active_futures.add(future)
        future.add_done_callback(self._remove_future)

    def _remove_future(self, future: Future) -> None:
        with self._futures_lock:
            self._active_futures.discard(future)

    def submit_io(self, fn: Callable, *args: Any, **kwargs: Any) -> Optional[Future]:
        """Queue an IO-bound task (files, network, SSH, subprocess)."""
        if self._is_shutdown or self._io_executor is None:
            self.logger.warning("IO task submitted after shutdown, ignoring")
            return None

        try:
            future = self._io_executor.submit(fn, *args, **kwargs)
            self._track_future(future)
            return future
        except RuntimeError as e:
            self.logger.error(f"Failed to submit IO task: {e}")
            return None

    def submit_cpu(self, fn: Callable, *args: Any, **kwargs: Any) -> Optional[Future]:
        """Queue a CPU-bound task (regex, parsing, highlighting)."""
        if self._is_shutdown or self._cpu_executor is None:
            self.logger.warning("CPU task submitted after shutdown, ignoring")
            return None

        try:
            future = self._cpu_executor.submit(fn, *args, **kwargs)
            self._track_future(future)
            return future
        except RuntimeError as e:
            self.logger.error(f"Failed to submit CPU task: {e}")
            return None

    def shutdown(self, wait: bool = False) -> None:
        """Shut down both pools. ``wait=False`` cancels pending futures."""
        if self._is_shutdown:
            return

        self._is_shutdown = True
        self.logger.info(f"Shutting down AsyncTaskManager (wait={wait})")

        if not wait:
            with self._futures_lock:
                active_futures = tuple(self._active_futures)
            cancelled_count = sum(future.cancel() for future in active_futures)
            if cancelled_count > 0:
                self.logger.info(f"Cancelled {cancelled_count} pending tasks")

        if self._io_executor is not None:
            self._io_executor.shutdown(wait=wait, cancel_futures=not wait)
            self._io_executor = None

        if self._cpu_executor is not None:
            self._cpu_executor.shutdown(wait=wait, cancel_futures=not wait)
            self._cpu_executor = None

        self.logger.info("AsyncTaskManager shutdown complete")

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown


def submit_io(fn: Callable, *args: Any, **kwargs: Any) -> Optional[Future]:
    return AsyncTaskManager.get().submit_io(fn, *args, **kwargs)


def submit_cpu(fn: Callable, *args: Any, **kwargs: Any) -> Optional[Future]:
    return AsyncTaskManager.get().submit_cpu(fn, *args, **kwargs)
