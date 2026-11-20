"""AshyTerm's PTY daemon on a real PTY: a session outlives its client, a new
client gets the replay, and input and close reach the shell."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from ashyterm.ptyd import wire

SRC = str(Path(__file__).resolve().parent.parent / "src")


@pytest.fixture
def daemon():
    # A Unix socket path holds ~108 bytes; pytest's tmp_path can be longer.
    short = Path(tempfile.mkdtemp(prefix="ptyd-", dir="/tmp"))
    path = short / "ptyd.sock"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ashyterm.ptyd.daemon", str(path)],
        env={**os.environ, "PYTHONPATH": SRC},
    )
    for _ in range(100):
        if path.exists():
            break
        time.sleep(0.05)
    yield path
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(short, ignore_errors=True)


def attach(path, session_id):
    sock = wire.connect(path)
    sock.sendall(wire.encode_line({"op": "attach", "id": session_id, "rows": 24, "cols": 80}))
    answer, rest = wire.read_line(sock)
    assert answer["ok"]
    return sock, rest


def read_until(sock, rest, needle, timeout=5.0):
    """Output frames until `needle` shows up; returns (output, exit code or None)."""
    frames = wire.FrameBuffer()
    output, code = bytearray(), None
    sock.settimeout(timeout)
    chunk = rest
    deadline = time.monotonic() + timeout
    while needle not in output and code is None and time.monotonic() < deadline:
        for kind, payload in frames.feed(chunk):
            if kind == wire.OUTPUT:
                output += payload
            elif kind == wire.EXIT:
                code = int(payload)
        if needle in output or code is not None:
            break
        chunk = sock.recv(65536)
    return bytes(output), code


def new_shell(path):
    env = {"PATH": os.environ["PATH"], "PS1": "$ ", "TERM": "dumb", "HOME": os.environ["HOME"]}
    return wire.ask({"op": "new", "argv": ["/bin/sh"], "env": env, "cwd": "/"}, path)["id"]


def test_session_outlives_client_and_replays(daemon):
    session = new_shell(daemon)
    sock, rest = attach(daemon, session)
    sock.sendall(wire.frame(wire.INPUT, b"echo first-$((1+1))\n"))
    output, _ = read_until(sock, rest, b"first-2")
    assert b"first-2" in output
    sock.close()

    listed = wire.list_sessions(daemon)
    assert [s["id"] for s in listed] == [session]
    assert listed[0]["cwd"] == "/"

    again, rest = attach(daemon, session)
    replay, _ = read_until(again, rest, b"first-2")
    assert b"first-2" in replay, "a new client sees what was on screen"
    again.close()


def test_close_ends_session_and_tells_clients(daemon):
    session = new_shell(daemon)
    sock, rest = attach(daemon, session)
    wire.ask({"op": "close", "id": session}, daemon)
    _, code = read_until(sock, rest, b"\0never\0")
    assert code is not None
    assert wire.list_sessions(daemon) == []


def test_unknown_session_and_bad_requests_refused(daemon):
    with pytest.raises(RuntimeError, match="no such session"):
        wire.ask({"op": "close", "id": "nope"}, daemon)
    with pytest.raises(RuntimeError, match="argv"):
        wire.ask({"op": "new", "argv": "rm -rf"}, daemon)
    with socket.socket(socket.AF_UNIX) as raw:
        raw.connect(str(daemon))
        raw.sendall(b"not json\n")
        answer, _ = wire.read_line(raw)
    assert not answer["ok"]
    assert wire.list_sessions(daemon) == []


def test_frames_reject_oversized_length():
    buffer = wire.FrameBuffer()
    with pytest.raises(wire.ProtocolError):
        list(buffer.feed(wire.HEADER.pack(wire.OUTPUT, wire.MAX_FRAME + 1)))
