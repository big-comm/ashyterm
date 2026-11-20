"""Local tabs whose shell lives in AshyTerm's PTY daemon, so it outlives the
window and can be joined from the phone. The tab itself runs a client of
that session; the shell's own pid is kept on the terminal for the cwd and
"is something running" checks."""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from ..utils.flatpak_host import is_flatpak
from ..utils.logger import get_logger
from . import wire
from .attach import ensure_daemon, package_root

logger = get_logger("ashyterm.ptyd")

# Runs the attach client from this copy of the package (installed or source).
_ATTACH = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ashyterm.ptyd.attach import main; main()"


def persistent_shell_command(command: List[str], env: Dict[str, str], working_dir: str, terminal: Any) -> List[str]:
    """Start `command` in the daemon and return the argv that joins it.

    Under Flatpak the shell runs on the host through flatpak-spawn, which the
    daemon can't own: the command is returned as is. When the daemon can't
    start, the tab gets a plain shell and the failure is logged.
    """
    if is_flatpak():
        return command
    kept = getattr(terminal, "_ptyd_reattach", None)
    if kept:
        return _join(terminal, kept["id"], kept["pid"])
    try:
        ensure_daemon()
        answer = wire.ask({
            "op": "new", "argv": command, "env": env, "cwd": working_dir,
            "rows": terminal.get_row_count(), "cols": terminal.get_column_count(),
        })
    except (OSError, RuntimeError, ValueError) as error:
        logger.warning(f"Terminal daemon unavailable, this tab won't be kept open: {error}")
        return command
    return _join(terminal, answer["id"], answer["pid"])


def _join(terminal: Any, session: str, pid: int) -> List[str]:
    terminal._ptyd_session = session
    terminal._ptyd_shell_pid = pid
    return [sys.executable, "-c", _ATTACH, package_root(), session]


def kept_sessions() -> List[Dict[str, Any]]:
    """Sessions nobody is attached to: what a window closed earlier left running."""
    if is_flatpak():
        return []
    try:
        return [s for s in wire.list_sessions() if not s["clients"]]
    except (OSError, RuntimeError) as error:
        logger.warning(f"Could not list kept terminal sessions: {error}")
        return []


def shell_pid(terminal: Any, fallback: int) -> int:
    """The shell's pid: the daemon's, for a kept-open tab."""
    return getattr(terminal, "_ptyd_shell_pid", None) or fallback


def end_session(terminal: Any) -> None:
    """The person closed this tab: end its shell, not just the client."""
    session = getattr(terminal, "_ptyd_session", None)
    if not session:
        return
    try:
        wire.ask({"op": "close", "id": session})
    except (OSError, RuntimeError) as error:
        logger.warning(f"Could not end terminal session {session}: {error}")
