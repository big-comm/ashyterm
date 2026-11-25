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


def join(path, session_id, rows, cols, owner):
    sock = wire.connect(path)
    sock.sendall(wire.encode_line({"op": "attach", "id": session_id, "rows": rows, "cols": cols, "owner": owner}))
    answer, rest = wire.read_line(sock)
    assert answer["ok"]
    return sock, rest


def frames_until(sock, rest, needle, frames, timeout=5.0):
    """Frames (kind, payload) until output holds `needle`."""
    seen, output = [], bytearray()
    sock.settimeout(timeout)
    chunk, deadline = rest, time.monotonic() + timeout
    while time.monotonic() < deadline:
        for kind, payload in frames.feed(chunk):
            seen.append((kind, payload))
            if kind == wire.OUTPUT:
                output += payload
        if needle in output:
            break
        chunk = sock.recv(65536)
    return seen, bytes(output)


def test_tab_keeps_its_size_while_a_phone_looks(daemon):
    session = new_shell(daemon)
    tab, _ = join(daemon, session, 40, 160, owner=True)
    phone, rest = join(daemon, session, 20, 45, owner=False)
    frames = wire.FrameBuffer()
    # The phone is told the tab's size before the replay, and its typing and
    # resizing leave the size alone.
    phone.sendall(wire.frame(wire.RESIZE, wire.resize_payload(20, 45)))
    phone.sendall(wire.frame(wire.INPUT, b"echo size-$(stty size | tr ' ' x)\n"))
    seen, output = frames_until(phone, rest, b"size-40x160", frames)
    assert b"size-40x160" in output
    kinds = [k for k, _ in seen]
    assert kinds.index(wire.SIZE) < kinds.index(wire.OUTPUT)
    assert wire.parse_resize(seen[kinds.index(wire.SIZE)][1]) == (40, 160)

    listed = wire.list_sessions(daemon)
    assert (listed[0]["clients"], listed[0]["owners"]) == (2, 1)

    # The tab leaves: the phone's size counts, and it is told so.
    tab.close()
    phone.sendall(wire.frame(wire.INPUT, b"echo now-$(stty size | tr ' ' x)\n"))
    seen, output = frames_until(phone, b"", b"now-20x45", frames)
    assert b"now-20x45" in output
    assert (wire.SIZE, wire.resize_payload(20, 45)) in seen
    phone.close()


def test_upgrade_keeps_sessions_and_clients(daemon):
    from ashyterm.ptyd.daemon import CODE

    session = new_shell(daemon)
    tab, rest = join(daemon, session, 30, 100, owner=True)
    frames = wire.FrameBuffer()
    tab.sendall(wire.frame(wire.INPUT, b"echo before-$((1+1))\n"))
    _, output = frames_until(tab, rest, b"before-2", frames)
    assert b"before-2" in output
    pid = wire.ask({"op": "version"}, daemon)
    assert pid["code"] == CODE

    # The daemon execs itself: same session, and the attached tab keeps working.
    wire.ask({"op": "upgrade"}, daemon)
    for _ in range(100):
        try:
            if wire.ask({"op": "version"}, daemon)["code"] == CODE:
                break
        except (OSError, RuntimeError):
            pass
        time.sleep(0.05)
    tab.sendall(wire.frame(wire.INPUT, b"echo after-$((2+3)) $(stty size | tr ' ' x)\n"))
    _, output = frames_until(tab, b"", b"after-5 30x100", frames)
    assert b"after-5 30x100" in output
    listed = wire.list_sessions(daemon)
    assert [(s["id"], s["owners"]) for s in listed] == [(session, 1)]
    assert not list(daemon.parent.glob("ptyd-handover-*")), "the handover file is removed"
    # A session the new code took over still ends properly, with the shell's exit code.
    tab.sendall(wire.frame(wire.INPUT, b"exit 7\n"))
    tab.settimeout(5)
    seen, chunk = [], b""
    while wire.EXIT not in [k for k, _ in seen]:
        chunk = tab.recv(65536)
        assert chunk, "connection closed before the exit code"
        seen += list(frames.feed(chunk))
    assert dict(seen)[wire.EXIT] == b"7"
    tab.close()


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
