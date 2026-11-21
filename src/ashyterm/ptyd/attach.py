"""``ashyterm-attach``: join a terminal session AshyTerm keeps open, from any
terminal (the phone over SSH), or start one.

    ashyterm-attach --list [--json]   sessions open on this computer
    ashyterm-attach ID                 join session ID
    ashyterm-attach new                start a session and join it

Closing the connection leaves the session running. While one of AshyTerm's
own tabs shows the session, its size counts: this terminal is asked to
take it (ESC [ 8 ; rows ; cols t), so both show the same lines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Optional

from . import wire

START_TIMEOUT = 5.0


def ensure_daemon() -> None:
    """Start the daemon unless it already answers."""
    try:
        wire.connect().close()
        return
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    path = wire.socket_path()
    pythonpath = os.pathsep.join(filter(None, [package_root(), os.environ.get("PYTHONPATH")]))
    daemon = [sys.executable, "-m", "ashyterm.ptyd.daemon", str(path)]
    # A user service, not a child: logind kills a login session's processes
    # when it ends (KillUserProcesses=yes on BigLinux), which would take every
    # shell with it when the SSH session that started the daemon closes.
    unit = "ashyterm-ptyd-" + hashlib.sha256(str(path).encode()).hexdigest()[:8]
    started = shutil.which("systemd-run") and subprocess.run(
        ["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}", f"--setenv=PYTHONPATH={pythonpath}", *daemon],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10,
    ).returncode == 0
    if not started:
        subprocess.Popen(
            daemon,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env={**os.environ, "PYTHONPATH": pythonpath},
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        try:
            wire.connect().close()
            return
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.05)
    raise RuntimeError("the terminal daemon did not start")


def package_root() -> str:
    """Directory holding the ``ashyterm`` package, so the daemon runs this copy."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def terminal_size() -> tuple:
    size = shutil.get_terminal_size((80, 24))
    return size.lines, size.columns


def print_sessions(as_json: bool) -> None:
    sessions = wire.list_sessions()
    if as_json:
        print(json.dumps(sessions))
        return
    for s in sessions:
        print(f"{s['id']}  {s['title'] or '-'}  {s['cwd']}")


def new_session() -> str:
    ensure_daemon()
    rows, cols = terminal_size()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SSH_")}
    env["TERM"] = "xterm-256color"
    # A command run over SSH carries no locale; take the system's.
    if "LANG" not in env and (lang := system_lang()):
        env["LANG"] = lang
    return wire.ask({"op": "new", "cwd": os.path.expanduser("~"), "env": env, "rows": rows, "cols": cols})["id"]


def system_lang() -> str:
    """LANG from /etc/locale.conf; empty when it sets none."""
    try:
        lines = Path("/etc/locale.conf").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        name, _, value = line.partition("=")
        if name.strip() == "LANG":
            return value.strip().strip('"')
    return ""


def attach(session_id: str, owner: bool = False) -> int:
    """Bridge this terminal to the session until it ends or we're cut off."""
    rows, cols = terminal_size()
    sock = wire.connect()
    sock.sendall(wire.encode_line({"op": "attach", "id": session_id, "rows": rows, "cols": cols, "owner": owner}))
    answer, rest = wire.read_line(sock)
    if not answer.get("ok"):
        print(answer.get("error") or "could not join the session", file=sys.stderr)
        return 1
    stdin = sys.stdin.fileno()
    saved = termios.tcgetattr(stdin) if os.isatty(stdin) else None
    if saved:
        tty.setraw(stdin)
    try:
        return pump(sock, stdin, rest)
    finally:
        if saved:
            termios.tcsetattr(stdin, termios.TCSADRAIN, saved)


def pump(sock: socket.socket, stdin: int, rest: bytes) -> int:
    resized = []
    signal.signal(signal.SIGWINCH, lambda *_: resized.append(True))
    frames = wire.FrameBuffer()
    selector = selectors.DefaultSelector()
    selector.register(stdin, selectors.EVENT_READ)
    selector.register(sock, selectors.EVENT_READ)
    exit_code: Optional[int] = None
    pending = rest
    while exit_code is None:
        if resized:
            resized.clear()
            sock.sendall(wire.frame(wire.RESIZE, wire.resize_payload(*terminal_size())))
        if pending:
            exit_code = write_frames(frames, pending)
            pending = b""
            continue
        try:
            events = selector.select()
        except InterruptedError:
            continue
        for key, _ in events:
            if key.fileobj == stdin:
                data = os.read(stdin, 65536)
                if not data:
                    return 0
                sock.sendall(wire.frame(wire.INPUT, data))
            else:
                pending = sock.recv(65536)
                if not pending:
                    return 0
    return exit_code


def write_frames(frames: wire.FrameBuffer, chunk: bytes) -> Optional[int]:
    for kind, payload in frames.feed(chunk):
        if kind == wire.OUTPUT:
            os.write(sys.stdout.fileno(), payload)
        elif kind == wire.SIZE:
            rows, cols = wire.parse_resize(payload)
            os.write(sys.stdout.fileno(), f"\x1b[8;{rows};{cols}t".encode())
        elif kind == wire.EXIT:
            return int(payload or b"0")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="ashyterm-attach", description=__doc__.splitlines()[0])
    parser.add_argument("session", nargs="?", help="session id, or 'new'")
    parser.add_argument("--list", action="store_true", help="list the open sessions")
    parser.add_argument("--json", action="store_true", help="with --list: print JSON")
    parser.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list or not args.session:
        print_sessions(args.json)
        return
    session_id = new_session() if args.session == "new" else args.session
    sys.exit(attach(session_id, args.owner))


if __name__ == "__main__":
    main()
