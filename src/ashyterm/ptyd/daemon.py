"""AshyTerm's PTY daemon: owns the shells, so a terminal session outlives the
window that started it and can be joined from elsewhere (another window, the
phone over SSH through ``ashyterm-attach``).

One per user, started on demand, listening on a socket only that user can
reach (the directory is 0700 and each peer's uid is checked). It exits when it
has had no session and no client for :data:`IDLE_EXIT` seconds.

After an update, AshyTerm finds the daemon's :data:`CODE` differs from its own
and asks it to ``upgrade``: the daemon writes its state (sessions, their
replay, attached clients) to a private file and execs the new code with the
same pid, PTYs and sockets, so no shell and no attached tab notices.
"""

from __future__ import annotations

import base64
import errno
import fcntl
import hashlib
import json
import os
import secrets
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import wire

# Output kept per session, replayed to whoever attaches.
REPLAY_BYTES = 1024 * 1024
# A client that falls this far behind is dropped rather than slowing the shell.
MAX_CLIENT_BACKLOG = 8 * 1024 * 1024
IDLE_EXIT = 30.0
READ_CHUNK = 65536
# This code's identity: a daemon still running older code answers another one.
CODE = hashlib.sha256(Path(__file__).read_bytes() + Path(wire.__file__).read_bytes()).hexdigest()[:16]


class Client:
    """One attached connection: what's still to be sent, and its size.

    An owner is one of AshyTerm's own tabs. Anything else (``ashyterm-attach``
    from the phone) is a guest: while an owner is attached, a guest doesn't
    change the session's size; it's told the size and draws at it.
    """

    def __init__(self, sock: socket.socket, owner: bool) -> None:
        self.sock = sock
        self.owner = owner
        self.outgoing = bytearray()
        self.frames = wire.FrameBuffer()
        self.size: Optional[tuple] = None


class Session:
    def __init__(self, argv: List[str], cwd: str, env: Dict[str, str], rows: int, cols: int, title: str) -> None:
        self.id = secrets.token_hex(4)
        self.title = title
        self.created = time.time()
        self.replay = bytearray()
        self.clients: List[Client] = []
        self.active: Optional[Client] = None
        self.size = (rows, cols)
        master, slave = os.openpty()
        set_size(master, rows, cols)

        def child_setup() -> None:
            # The daemon ignores SIGHUP; the shell must not inherit that.
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        self.proc: Optional[subprocess.Popen] = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=child_setup, close_fds=True,
        )
        self.pid = self.proc.pid
        os.close(slave)
        os.set_blocking(master, False)
        self.master = master

    @classmethod
    def restore(cls, state: Dict[str, Any]) -> "Session":
        """A session an older daemon handed over: its shell is our child already."""
        session = cls.__new__(cls)
        session.id = state["id"]
        session.title = state["title"]
        session.created = state["created"]
        session.replay = bytearray(base64.b64decode(state["replay"]))
        session.clients = []
        session.active = None
        session.size = tuple(state["size"])
        session.proc = None
        session.pid = state["pid"]
        session.master = state["master"]
        return session

    def handover(self) -> Dict[str, Any]:
        """What the next daemon needs to keep this session going."""
        return {
            "id": self.id, "title": self.title, "created": self.created, "size": list(self.size),
            "pid": self.pid, "master": self.master, "replay": base64.b64encode(bytes(self.replay)).decode(),
            "clients": [
                {
                    "fd": c.sock.fileno(), "owner": c.owner, "size": list(c.size) if c.size else None,
                    "outgoing": base64.b64encode(bytes(c.outgoing)).decode(),
                    "incoming": base64.b64encode(c.frames.pending()).decode(), "active": self.active is c,
                }
                for c in self.clients
            ],
        }

    def exit_code(self) -> int:
        if self.proc is not None:
            return self.proc.wait()
        _, status = os.waitpid(self.pid, 0)
        return os.waitstatus_to_exitcode(status)

    def remember(self, chunk: bytes) -> None:
        self.replay += chunk
        if len(self.replay) > REPLAY_BYTES:
            del self.replay[: len(self.replay) - REPLAY_BYTES]

    def cwd(self) -> str:
        try:
            return os.readlink(f"/proc/{self.pid}/cwd")
        except OSError:
            return ""

    def running(self) -> str:
        """The program in the foreground (the shell itself when idle)."""
        try:
            return Path(f"/proc/{foreground_group(self.master)}/comm").read_text().strip()
        except OSError:
            return ""

    def describe(self) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "pid": self.pid, "cwd": self.cwd(),
            "running": self.running(), "created": self.created, "clients": len(self.clients),
            "owners": sum(1 for c in self.clients if c.owner),
        }

    def hang_up(self) -> None:
        """End the session as closing a terminal does: SIGHUP to the shell and to
        whatever runs in the foreground."""
        for group in {self.pid, foreground_group(self.master)} - {0}:
            try:
                os.killpg(group, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass

    def takes_size_of(self, client: Client) -> bool:
        """Whether `client` sets the size: any client while no owner is attached, else an owner."""
        return client.owner or not any(c.owner for c in self.clients)


def foreground_group(master: int) -> int:
    try:
        return os.tcgetpgrp(master)
    except OSError:
        return 0


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


class Daemon:
    def __init__(self, path: Path, resume: Optional[Dict[str, Any]] = None) -> None:
        self.path = path
        self.selector = selectors.DefaultSelector()
        self.sessions: Dict[str, Session] = {}
        self.pending: Dict[socket.socket, bytearray] = {}
        self.idle_since = time.monotonic()
        self.upgrading = False
        if resume is None:
            self.listener = self._bind()
        else:
            self.listener = socket.socket(fileno=resume["listener"])
            self.listener.setblocking(False)
        self.selector.register(self.listener, selectors.EVENT_READ, ("listen", None))
        for state in (resume or {}).get("sessions", []):
            self._take_over(state)

    def _take_over(self, state: Dict[str, Any]) -> None:
        """Keep running a session (and its clients) an older daemon handed over."""
        session = Session.restore(state)
        self.sessions[session.id] = session
        self.selector.register(session.master, selectors.EVENT_READ, ("pty", session))
        for c in state["clients"]:
            sock = socket.socket(fileno=c["fd"])
            sock.setblocking(False)
            client = Client(sock, owner=c["owner"])
            client.size = tuple(c["size"]) if c["size"] else None
            client.outgoing += base64.b64decode(c["outgoing"])
            client.frames.restore(base64.b64decode(c["incoming"]))
            session.clients.append(client)
            if c["active"]:
                session.active = client
            events = selectors.EVENT_READ | (selectors.EVENT_WRITE if client.outgoing else 0)
            self.selector.register(sock, events, ("client", (session, client)))

    def _upgrade(self) -> None:
        """Exec this program's current code with everything open handed over."""
        for sock in list(self.pending):
            self._drop_pending(sock)
        state = {"listener": self.listener.fileno(), "sessions": [s.handover() for s in self.sessions.values()]}
        fds = [state["listener"]] + [s["master"] for s in state["sessions"]]
        fds += [c["fd"] for s in state["sessions"] for c in s["clients"]]
        for fd in fds:
            os.set_inheritable(fd, True)
        path = self.path.parent / f"ptyd-handover-{os.getpid()}.json"
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out)
        os.execv(sys.executable, [sys.executable, "-m", "ashyterm.ptyd.daemon", str(self.path), "--resume", str(path)])

    def _bind(self) -> socket.socket:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            try:
                wire.connect(self.path).close()
                raise SystemExit("ashyterm-ptyd is already running")
            except (ConnectionRefusedError, FileNotFoundError):
                self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        listener.listen(16)
        listener.setblocking(False)
        return listener

    # The loop

    def run(self) -> None:
        while not self._should_exit():
            for key, mask in self.selector.select(timeout=1.0):
                role, item = key.data
                if role == "listen":
                    self._accept()
                elif role == "request":
                    self._read_request(key.fileobj)
                elif role == "pty":
                    self._read_pty(item)
                elif mask & selectors.EVENT_READ:
                    self._read_client(*item)
                if role == "client" and mask & selectors.EVENT_WRITE:
                    self._flush(*item)
            if self.upgrading:
                self._upgrade()
        self.listener.close()
        self.path.unlink(missing_ok=True)

    def _should_exit(self) -> bool:
        busy = self.sessions or self.pending
        if busy:
            self.idle_since = time.monotonic()
        return not busy and time.monotonic() - self.idle_since > IDLE_EXIT

    def _accept(self) -> None:
        sock, _ = self.listener.accept()
        if peer_uid(sock) != os.getuid():
            sock.close()
            return
        sock.setblocking(False)
        self.pending[sock] = bytearray()
        self.selector.register(sock, selectors.EVENT_READ, ("request", None))

    # Requests

    def _read_request(self, sock: socket.socket) -> None:
        received = self.pending[sock]
        try:
            chunk = sock.recv(65536)
        except BlockingIOError:
            return
        if not chunk or len(received) + len(chunk) > wire.MAX_REQUEST_LINE:
            self._drop_pending(sock)
            return
        received += chunk
        if b"\n" not in received:
            return
        line, _, rest = bytes(received).partition(b"\n")
        del self.pending[sock]
        self.selector.unregister(sock)
        try:
            request = json.loads(line)
            self._handle(sock, request, rest)
        # A bad request (bad JSON, fields, cwd or program) must not stop the daemon.
        except (OSError, ValueError, LookupError, TypeError) as error:
            self._answer(sock, {"ok": False, "error": str(error)})

    def _drop_pending(self, sock: socket.socket) -> None:
        self.selector.unregister(sock)
        del self.pending[sock]
        sock.close()

    def _answer(self, sock: socket.socket, message: Dict[str, Any]) -> None:
        sock.setblocking(True)
        try:
            sock.sendall(wire.encode_line(message))
        finally:
            sock.close()

    def _handle(self, sock: socket.socket, request: Dict[str, Any], rest: bytes) -> None:
        op = request.get("op")
        if op == "list":
            sessions = sorted(self.sessions.values(), key=lambda s: s.created)
            self._answer(sock, {"ok": True, "sessions": [s.describe() for s in sessions]})
        elif op == "new":
            session = self._spawn(request)
            self._answer(sock, {"ok": True, "id": session.id, "pid": session.pid})
        elif op == "close":
            self._session(request).hang_up()
            self._answer(sock, {"ok": True})
        elif op == "attach":
            self._attach(sock, self._session(request), request, rest)
        elif op == "version":
            self._answer(sock, {"ok": True, "code": CODE})
        elif op == "upgrade":
            self._answer(sock, {"ok": True})
            self.upgrading = True
        else:
            raise ValueError(f"unknown request {op!r}")

    def _session(self, request: Dict[str, Any]) -> Session:
        session = self.sessions.get(str(request.get("id")))
        if session is None:
            raise LookupError("no such session")
        return session

    def _spawn(self, request: Dict[str, Any]) -> Session:
        argv = request.get("argv") or [os.environ.get("SHELL") or "/bin/bash", "-l"]
        if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
            raise ValueError("argv must be a list of strings")
        env = request.get("env") or dict(os.environ)
        if not (isinstance(env, dict) and all(isinstance(v, str) for v in env.values())):
            raise ValueError("env must map names to strings")
        cwd = request.get("cwd") or str(Path.home())
        rows, cols = int(request.get("rows") or 24), int(request.get("cols") or 80)
        session = Session(argv, cwd, env, rows, cols, str(request.get("title") or ""))
        self.sessions[session.id] = session
        self.selector.register(session.master, selectors.EVENT_READ, ("pty", session))
        return session

    def _attach(self, sock: socket.socket, session: Session, request: Dict[str, Any], rest: bytes) -> None:
        client = Client(sock, owner=bool(request.get("owner")))
        session.clients.append(client)
        client.outgoing += wire.encode_line({"ok": True, "id": session.id})
        self.selector.register(sock, selectors.EVENT_READ | selectors.EVENT_WRITE, ("client", (session, client)))
        if request.get("rows") and request.get("cols"):
            client.size = (int(request["rows"]), int(request["cols"]))
        # The size before the replay: a guest redraws the replay at the session's size.
        if session.takes_size_of(client):
            self._resize(session, client)
        else:
            client.outgoing += wire.frame(wire.SIZE, wire.resize_payload(*session.size))
        client.outgoing += wire.frame(wire.OUTPUT, bytes(session.replay))
        if rest:
            self._take_frames(session, client, rest)

    # Sessions and clients

    def _read_pty(self, session: Session) -> None:
        try:
            chunk = os.read(session.master, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            chunk = b""
        if not chunk:
            self._end(session)
            return
        session.remember(chunk)
        for client in list(session.clients):
            self._send(session, client, wire.frame(wire.OUTPUT, chunk))

    def _resize(self, session: Session, client: Client) -> None:
        """The session takes `client`'s size, and its guests are told."""
        session.active = client
        if client.size and client.size != session.size:
            session.size = client.size
            set_size(session.master, *client.size)
        for c in session.clients:
            if not c.owner:
                self._send(session, c, wire.frame(wire.SIZE, wire.resize_payload(*session.size)))

    def _end(self, session: Session) -> None:
        self.selector.unregister(session.master)
        os.close(session.master)
        code = session.exit_code()
        for client in list(session.clients):
            self._send(session, client, wire.frame(wire.EXIT, str(code).encode()))
            self._flush_blocking(client)
            self._detach(session, client)
        del self.sessions[session.id]

    def _send(self, session: Session, client: Client, data: bytes) -> None:
        client.outgoing += data
        if len(client.outgoing) > MAX_CLIENT_BACKLOG:
            self._detach(session, client)
            return
        self.selector.modify(client.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, ("client", (session, client)))

    def _flush(self, session: Session, client: Client) -> None:
        try:
            sent = client.sock.send(client.outgoing)
        except BlockingIOError:
            return
        except OSError:
            self._detach(session, client)
            return
        del client.outgoing[:sent]
        if not client.outgoing:
            self.selector.modify(client.sock, selectors.EVENT_READ, ("client", (session, client)))

    def _flush_blocking(self, client: Client) -> None:
        try:
            client.sock.setblocking(True)
            client.sock.settimeout(2)
            client.sock.sendall(client.outgoing)
        except OSError:
            pass  # The client is going anyway.

    def _read_client(self, session: Session, client: Client) -> None:
        try:
            chunk = client.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:
            self._detach(session, client)
            return
        try:
            self._take_frames(session, client, chunk)
        except wire.ProtocolError:
            self._detach(session, client)

    def _take_frames(self, session: Session, client: Client, chunk: bytes) -> None:
        for kind, payload in client.frames.feed(chunk):
            if kind == wire.INPUT:
                # Typing makes a client the one whose size counts (owners first).
                if session.active is not client and session.takes_size_of(client):
                    self._resize(session, client)
                os.write(session.master, payload)
            elif kind == wire.RESIZE:
                client.size = wire.parse_resize(payload)
                if session.takes_size_of(client):
                    self._resize(session, client)

    def _detach(self, session: Session, client: Client) -> None:
        if client not in session.clients:
            return
        session.clients.remove(client)
        try:
            self.selector.unregister(client.sock)
        except (KeyError, ValueError):
            pass
        client.sock.close()
        if session.active is client and session.clients:
            owners = [c for c in session.clients if c.owner]
            self._resize(session, (owners or session.clients)[-1])


def main() -> None:
    os.umask(0o077)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else wire.socket_path()
    resume = None
    if len(sys.argv) > 3 and sys.argv[2] == "--resume":
        # Written by this daemon's previous code just before it exec'd us.
        handover = Path(sys.argv[3])
        resume = json.loads(handover.read_text(encoding="utf-8"))
        handover.unlink()
    Daemon(path, resume).run()


if __name__ == "__main__":
    main()
