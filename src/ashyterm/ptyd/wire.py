"""What AshyTerm's PTY daemon and its clients say to each other.

A connection starts with one JSON request line. ``list``, ``new`` and
``close`` get one JSON answer line and end. ``attach`` gets an answer line,
then binary frames both ways: one kind byte, a 4-byte big-endian length, the
payload.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Daemon → client.
OUTPUT = 1
EXIT = 2
# The session's size, to a guest (rows, cols as in RESIZE): it draws at that size.
SIZE = 5
# Client → daemon.
INPUT = 3
RESIZE = 4

HEADER = struct.Struct(">BI")
# Largest frame either side accepts: the replay sent on attach, plus room.
MAX_FRAME = 2 * 1024 * 1024
MAX_REQUEST_LINE = 256 * 1024


class ProtocolError(Exception):
    """The other side broke the protocol (bad frame, oversized line)."""


def runtime_dir() -> Path:
    """Private per-user directory holding the daemon's socket."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "ashyterm"


def socket_path() -> Path:
    return runtime_dir() / "ptyd.sock"


def frame(kind: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_FRAME:
        raise ProtocolError(f"frame of {len(payload)} bytes is too large")
    return HEADER.pack(kind, len(payload)) + payload


def resize_payload(rows: int, cols: int) -> bytes:
    return struct.pack(">HH", rows, cols)


def parse_resize(payload: bytes) -> Tuple[int, int]:
    if len(payload) != 4:
        raise ProtocolError("resize frame must hold rows and columns")
    rows, cols = struct.unpack(">HH", payload)
    return max(1, rows), max(1, cols)


class FrameBuffer:
    """Bytes received so far, split into complete frames."""

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> Iterator[Tuple[int, bytes]]:
        self._pending += chunk
        while len(self._pending) >= HEADER.size:
            kind, length = HEADER.unpack_from(self._pending)
            if length > MAX_FRAME:
                raise ProtocolError(f"frame of {length} bytes is too large")
            end = HEADER.size + length
            if len(self._pending) < end:
                return
            payload = bytes(self._pending[HEADER.size:end])
            del self._pending[:end]
            yield kind, payload


def encode_line(message: Dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def read_line(sock: socket.socket) -> Tuple[Dict[str, Any], bytes]:
    """One JSON line from a blocking socket, and whatever came after it."""
    received = bytearray()
    while b"\n" not in received:
        chunk = sock.recv(65536)
        if not chunk:
            raise ProtocolError("connection closed before a full line")
        received += chunk
        if len(received) > MAX_REQUEST_LINE:
            raise ProtocolError("line too long")
    line, _, rest = bytes(received).partition(b"\n")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ProtocolError("a message must be a JSON object")
    return message, rest


def connect(path: Optional[Path] = None) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(path or socket_path()))
    return sock


def ask(message: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    """Send a one-shot request (``list``, ``new``, ``close``) and read the answer."""
    with connect(path) as sock:
        sock.sendall(encode_line(message))
        answer, _ = read_line(sock)
    if not answer.get("ok"):
        raise RuntimeError(answer.get("error") or "the terminal daemon refused the request")
    return answer


def list_sessions(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The daemon's sessions; none when it isn't running."""
    try:
        return ask({"op": "list"}, path)["sessions"]
    except (FileNotFoundError, ConnectionRefusedError):
        return []
