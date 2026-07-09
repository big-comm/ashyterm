"""Bounded, write-only OSC 52 clipboard support for proxied terminals."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Callable, List, Tuple

from ..utils.logger import get_logger


OSC52_PREFIX = b"\x1b]52;"
OSC52_MAX_DECODED_BYTES = 1024 * 1024
OSC52_MAX_ENCODED_BYTES = ((OSC52_MAX_DECODED_BYTES + 2) // 3) * 4

_logger = get_logger("ashyterm.terminal.osc52")


@dataclass(frozen=True)
class OSC52Request:
    """A parsed OSC 52 selection and its still-encoded payload."""

    selection: str
    payload: bytes


class OSC52StreamParser:
    """Consume OSC 52 sequences while preserving surrounding terminal output."""

    def __init__(self) -> None:
        self._pending = b""
        self._discarding_oversized = False

    def reset(self) -> None:
        """Drop buffered partial sequence state."""
        self._pending = b""
        self._discarding_oversized = False

    def feed(self, chunk: bytes) -> Tuple[bytes, List[OSC52Request]]:
        """Return visible bytes and complete OSC 52 write requests."""
        data = self._pending + chunk
        self._pending = b""
        if self._discarding_oversized:
            data = self._finish_oversized_discard(data)
            if not data:
                return b"", []

        visible = bytearray()
        requests: List[OSC52Request] = []
        cursor = 0

        while cursor < len(data):
            start = data.find(OSC52_PREFIX, cursor)
            if start < 0:
                self._preserve_partial_prefix(data[cursor:], visible)
                break

            visible.extend(data[cursor:start])
            terminator = _find_terminator(data, start + len(OSC52_PREFIX))
            if terminator is None:
                sequence = data[start:]
                if len(sequence) > OSC52_MAX_ENCODED_BYTES + 32:
                    self._discarding_oversized = True
                    _logger.warning("Discarding oversized OSC 52 sequence")
                else:
                    self._pending = sequence
                break

            terminator_start, terminator_size = terminator
            body = data[start + len(OSC52_PREFIX) : terminator_start]
            request = _parse_request(body)
            if request is not None:
                requests.append(request)
            cursor = terminator_start + terminator_size

        return bytes(visible), requests

    def _finish_oversized_discard(self, data: bytes) -> bytes:
        terminator = _find_terminator(data, 0)
        if terminator is None:
            return b""
        terminator_start, terminator_size = terminator
        self._discarding_oversized = False
        return data[terminator_start + terminator_size :]

    def _preserve_partial_prefix(self, tail: bytes, visible: bytearray) -> None:
        partial_size = _partial_prefix_size(tail)
        if partial_size:
            visible.extend(tail[:-partial_size])
            self._pending = tail[-partial_size:]
        else:
            visible.extend(tail)


def consume_osc52_output(
    parser: OSC52StreamParser,
    chunk: bytes,
    terminal: Any,
    *,
    enabled: bool | Callable[[], bool],
) -> bytes:
    """Consume OSC 52 output and apply allowed writes to GTK clipboards."""
    visible, requests = parser.feed(chunk)
    if not requests:
        return visible
    should_write = enabled() if callable(enabled) else enabled
    if should_write:
        for request in requests:
            _write_clipboard(terminal, request)
    return visible


def _parse_request(body: bytes) -> OSC52Request | None:
    separator = body.find(b";")
    if separator < 0:
        return None
    try:
        selection = body[:separator].decode("ascii")
    except UnicodeDecodeError:
        return None
    payload = body[separator + 1 :]
    if len(payload) > OSC52_MAX_ENCODED_BYTES:
        _logger.warning("Ignoring oversized OSC 52 clipboard payload")
        return None
    return OSC52Request(selection=selection, payload=payload)


def _write_clipboard(terminal: Any, request: OSC52Request) -> bool:
    if request.payload == b"?":
        _logger.info("Ignoring OSC 52 clipboard read request")
        return False
    try:
        decoded = base64.b64decode(request.payload, validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        _logger.warning("Ignoring invalid OSC 52 clipboard payload")
        return False
    if len(decoded) > OSC52_MAX_DECODED_BYTES:
        _logger.warning("Ignoring oversized OSC 52 clipboard content")
        return False

    clipboards = _select_clipboards(terminal, request.selection)
    for clipboard in clipboards:
        clipboard.set(text)
    if clipboards:
        _logger.info(f"Copied {len(decoded)} bytes to clipboard via OSC 52")
    return bool(clipboards)


def _select_clipboards(terminal: Any, selection: str) -> List[Any]:
    selection = selection or "c"
    clipboards: List[Any] = []
    if any(code in selection for code in ("c", "s")):
        clipboards.append(terminal.get_clipboard())
    if "p" in selection:
        primary = terminal.get_primary_clipboard()
        if primary not in clipboards:
            clipboards.append(primary)
    return clipboards


def _find_terminator(data: bytes, start: int) -> Tuple[int, int] | None:
    bel = data.find(b"\x07", start)
    string_terminator = data.find(b"\x1b\\", start)
    candidates = [(bel, 1), (string_terminator, 2)]
    present = [candidate for candidate in candidates if candidate[0] >= 0]
    return min(present, default=None, key=lambda item: item[0])


def _partial_prefix_size(data: bytes) -> int:
    maximum = min(len(data), len(OSC52_PREFIX) - 1)
    for size in range(maximum, 0, -1):
        if data.endswith(OSC52_PREFIX[:size]):
            return size
    return 0
