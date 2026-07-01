# ashyterm/filemanager/accelerated_download.py
"""AsyncSSH segmented SFTP downloads."""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..sessions.models import SessionItem


class AcceleratedDownloadUnavailable(Exception):
    """Raised when the accelerated path cannot support this session."""


class AcceleratedDownloadCancelled(Exception):
    """Raised when the user cancels the accelerated transfer."""


class AcceleratedDownloadError(Exception):
    """Raised when the accelerated transfer fails after it starts."""


@dataclass(frozen=True)
class AcceleratedDownloadConfig:
    """Runtime limits for segmented downloads."""

    parallel_requests: int = 6
    chunk_size_bytes: int = 4 * 1024 * 1024
    connect_timeout: int = 30
    strict_host_key_checking: str = "accept-new"


def build_download_chunks(file_size: int, chunk_size_bytes: int) -> list[tuple[int, int]]:
    """Return ``(offset, length)`` chunks covering ``file_size`` exactly."""
    if file_size < 0:
        raise ValueError("file_size must be non-negative")
    if chunk_size_bytes <= 0:
        raise ValueError("chunk_size_bytes must be positive")
    return [
        (offset, min(chunk_size_bytes, file_size - offset))
        for offset in range(0, file_size, chunk_size_bytes)
    ]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if close:
        await _maybe_await(close())
    wait_closed = getattr(resource, "wait_closed", None)
    if wait_closed:
        await _maybe_await(wait_closed())


class AsyncSSHSegmentedDownloader:
    """Download one remote file with concurrent SFTP range reads."""

    def __init__(
        self,
        session: SessionItem,
        config: AcceleratedDownloadConfig,
        *,
        progress_callback: Optional[Callable[[float], None]] = None,
        cancellation_event: Optional[threading.Event] = None,
        connect_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.session = session
        self.config = config
        self.progress_callback = progress_callback
        self.cancellation_event = cancellation_event
        self._connect_factory = connect_factory
        self._async_exit_handlers: dict[int, Callable[..., Any]] = {}

    def download(
        self, remote_path: str, local_path: Path, expected_size: int = 0
    ) -> None:
        """Run the segmented download synchronously."""
        asyncio.run(self._download_async(remote_path, local_path, expected_size))

    async def _download_async(
        self, remote_path: str, local_path: Path, expected_size: int
    ) -> None:
        self._validate_session()
        if not hasattr(os, "pwrite"):
            raise AcceleratedDownloadUnavailable("os.pwrite is required")

        asyncssh = self._import_asyncssh()
        connect_factory = self._connect_factory or asyncssh.connect
        connect_kwargs = self._build_connect_kwargs(asyncssh)

        connection = await self._open_resource(connect_factory(**connect_kwargs))
        try:
            sftp = await self._open_resource(connection.start_sftp_client())
            try:
                attrs = await _maybe_await(sftp.stat(remote_path))
                remote_size = self._get_remote_size(attrs, expected_size)
                await self._download_with_sftp(sftp, remote_path, local_path, remote_size)
                self._preserve_mtime(local_path, attrs)
            finally:
                await self._close_opened_resource(sftp)
        finally:
            await self._close_opened_resource(connection)

    def _validate_session(self) -> None:
        if not self.session or not self.session.is_ssh():
            raise AcceleratedDownloadUnavailable("session is not SSH")
        if not self.session.host:
            raise AcceleratedDownloadUnavailable("session has no host")
        if self.session.proxy_jump:
            raise AcceleratedDownloadUnavailable("ProxyJump is not supported")
        if self.session.uses_password_auth() and not self.session.auth_value:
            raise AcceleratedDownloadUnavailable("password auth needs a stored password")

    def _import_asyncssh(self) -> Any:
        if self._connect_factory:
            return None
        try:
            import asyncssh

            return asyncssh
        except ImportError as exc:
            raise AcceleratedDownloadUnavailable("asyncssh is not installed") from exc

    def _build_connect_kwargs(self, asyncssh: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.session.host,
            "port": self.session.port or 22,
            "username": self.session.user or None,
            "connect_timeout": self.config.connect_timeout,
        }
        if self.session.uses_key_auth() and self.session.auth_value:
            kwargs["client_keys"] = [self.session.auth_value]
        if self.session.uses_password_auth() and self.session.auth_value:
            kwargs["password"] = self.session.auth_value
        if self.config.strict_host_key_checking == "no":
            kwargs["known_hosts"] = None
        return kwargs

    async def _open_resource(self, resource_factory_result: Any) -> Any:
        resource = await _maybe_await(resource_factory_result)
        enter = getattr(resource, "__aenter__", None)
        if enter:
            opened = await enter()
            self._async_exit_handlers[id(opened)] = resource.__aexit__
            return opened
        return resource

    async def _close_opened_resource(self, resource: Any) -> None:
        exit_func = self._async_exit_handlers.pop(id(resource), None)
        if exit_func:
            await exit_func(None, None, None)
            return
        await _close_resource(resource)

    def _get_remote_size(self, attrs: Any, expected_size: int) -> int:
        size = getattr(attrs, "size", None)
        if isinstance(size, int) and size >= 0:
            return size
        if expected_size > 0:
            return expected_size
        raise AcceleratedDownloadUnavailable("remote file size is unavailable")

    async def _download_with_sftp(
        self, sftp: Any, remote_path: str, local_path: Path, remote_size: int
    ) -> None:
        part_path = local_path.with_name(f"{local_path.name}.part")
        chunks = build_download_chunks(remote_size, self.config.chunk_size_bytes)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(part_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        completed = False
        try:
            os.ftruncate(fd, remote_size)
            if remote_size == 0:
                self._emit_progress(100.0)
            else:
                await self._run_chunk_workers(sftp, remote_path, fd, chunks, remote_size)
            completed = True
        finally:
            os.close(fd)
            if not completed:
                part_path.unlink(missing_ok=True)
        os.replace(part_path, local_path)

    async def _run_chunk_workers(
        self,
        sftp: Any,
        remote_path: str,
        local_fd: int,
        chunks: Iterable[tuple[int, int]],
        remote_size: int,
    ) -> None:
        queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
        for chunk in chunks:
            queue.put_nowait(chunk)

        transferred = 0
        progress_lock = asyncio.Lock()

        async def record_progress(byte_count: int) -> None:
            nonlocal transferred
            async with progress_lock:
                transferred += byte_count
                self._emit_progress(min(100.0, transferred * 100.0 / remote_size))

        async def worker() -> None:
            remote_file = await self._open_remote_file(sftp, remote_path)
            try:
                while True:
                    self._raise_if_cancelled()
                    try:
                        offset, length = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    data = await self._read_remote_range(remote_file, offset, length)
                    if len(data) != length:
                        raise AcceleratedDownloadError(
                            f"Short read at offset {offset}: expected {length}, got {len(data)}"
                        )
                    self._write_local_range(local_fd, data, offset)
                    await record_progress(length)
            finally:
                await self._close_opened_resource(remote_file)

        worker_count = max(1, min(self.config.parallel_requests, queue.qsize()))
        tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _open_remote_file(self, sftp: Any, remote_path: str) -> Any:
        return await self._open_resource(sftp.open(remote_path, "rb"))

    async def _read_remote_range(
        self, remote_file: Any, offset: int, length: int
    ) -> bytes:
        self._raise_if_cancelled()
        try:
            data = await _maybe_await(remote_file.read(length, offset))
        except TypeError:
            data = await _maybe_await(remote_file.read(length, offset=offset))
        self._raise_if_cancelled()
        if isinstance(data, memoryview):
            return data.tobytes()
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, bytes):
            return data
        raise AcceleratedDownloadError(f"Unexpected SFTP read type: {type(data).__name__}")

    def _raise_if_cancelled(self) -> None:
        if self.cancellation_event and self.cancellation_event.is_set():
            raise AcceleratedDownloadCancelled("download cancelled")

    def _emit_progress(self, progress: float) -> None:
        if self.progress_callback:
            self.progress_callback(progress)

    def _write_local_range(self, local_fd: int, data: bytes, offset: int) -> None:
        written = 0
        buffer = memoryview(data)
        while written < len(buffer):
            byte_count = os.pwrite(local_fd, buffer[written:], offset + written)
            if byte_count <= 0:
                raise AcceleratedDownloadError(
                    f"Short local write at offset {offset + written}"
                )
            written += byte_count

    def _preserve_mtime(self, local_path: Path, attrs: Any) -> None:
        mtime = getattr(attrs, "mtime", None)
        if isinstance(mtime, (int, float)) and mtime > 0:
            os.utime(local_path, (mtime, mtime))
