# ashyterm/terminal/terminal_drag_drop.py
"""File drag-and-drop handlers for SFTP and SSH terminals.

Two shapes:

* **SFTP** — files dropped on an SFTP session are injected into the
  terminal as ``put -r "<path>"`` commands, one per file. The
  session is already in SFTP so this is a shell-level feed, not an
  external upload.
* **SSH** — files dropped on an SSH terminal need the file-manager's
  upload dialog. We emit a signal with the paths + session metadata
  and let the window open the dialog on the main thread.

Helpers live here so the transport details (``Gdk.FileList``,
``terminal.feed_child``) don't clutter ``TerminalManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .manager import TerminalManager


_logger = get_logger("ashyterm.terminal.drag_drop")


def extract_dropped_paths(value: Any) -> List[str]:
    """Pull local file paths out of a ``Gdk.FileList`` drop payload.

    Files without a local path (``file.get_path()`` returns None —
    e.g. remote ``file://`` URIs that GIO hasn't mounted locally)
    are skipped so the caller can't accidentally feed garbage into
    the terminal.
    """
    paths: List[str] = []
    try:
        files = value.get_files() or []
    except Exception:
        return paths
    for file in files:
        path = file.get_path()
        if path:
            paths.append(path)
    return paths


def is_terminal_ssh_like(
    *,
    session: Any,
    ssh_target: Optional[str],
) -> bool:
    """Return True when ``terminal`` is an SSH session or a manually-SSHed local."""
    if ssh_target:
        return True
    return isinstance(session, SessionItem) and session.is_ssh()


def setup_sftp_drop(
    terminal: Vte.Terminal, *, on_drop
) -> None:
    """Install an SFTP-style file drop controller on ``terminal``."""
    drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
    drop_target.connect("drop", on_drop, terminal)
    terminal.add_controller(drop_target)


def setup_ssh_drop(
    terminal: Vte.Terminal, *, terminal_id: int, on_drop
) -> None:
    """Install an SSH-style file drop controller on ``terminal``.

    Terminal id is passed to the callback so the manager can look up
    session metadata for the dropped files.
    """
    drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
    drop_target.connect("drop", on_drop, terminal, terminal_id)
    terminal.add_controller(drop_target)


def handle_sftp_drop(
    value: Any, terminal: Vte.Terminal
) -> bool:
    """Feed each dropped path into ``terminal`` as a ``put -r`` command.

    Returns False if we hit a parse error (any exception), True
    otherwise. Matches the ``drop`` signal return contract.
    """
    try:
        for path in extract_dropped_paths(value):
            command_to_send = f'put -r "{path}"\n'
            _logger.info(
                f"File dropped on SFTP terminal. Sending command: "
                f"{command_to_send.strip()}"
            )
            terminal.feed_child(command_to_send.encode("utf-8"))
        return True
    except Exception as e:
        _logger.error(f"Error handling file drop for SFTP: {e}")
        return False


def handle_ssh_drop(
    manager: "TerminalManager",
    value: Any,
    terminal_id: int,
) -> bool:
    """Forward an SSH drop through ``manager`` to the window's upload flow.

    Returns True if the drop was accepted for processing (a signal
    was scheduled), False if we bailed (no terminal info, not an SSH
    session, no local paths).
    """
    try:
        local_paths = extract_dropped_paths(value)
        if not local_paths:
            return False

        info = manager.registry.get_terminal_info(terminal_id)
        if not info:
            _logger.warning(f"No terminal info for ID {terminal_id}")
            return False

        session = info.get("identifier")
        ssh_target = manager.manual_ssh_tracker.get_ssh_target(terminal_id)

        if not is_terminal_ssh_like(session=session, ssh_target=ssh_target):
            _logger.info("Drop target is not an SSH session, ignoring")
            return False

        _logger.info(
            f"Files dropped on SSH terminal. Requesting upload dialog for "
            f"{len(local_paths)} files."
        )

        # Defer to the main loop so the drop signal can unwind before
        # we pop a dialog.
        GLib.idle_add(
            emit_ssh_file_drop_signal,
            manager,
            terminal_id,
            local_paths,
            session,
            ssh_target,
        )
        return True

    except Exception as e:
        _logger.error(f"Error handling file drop for SSH: {e}")
        return False


def emit_ssh_file_drop_signal(
    manager: "TerminalManager",
    terminal_id: int,
    local_paths: List[str],
    session: Any,
    ssh_target: Optional[str],
) -> bool:
    """Notify the window about a pending SSH upload.

    Stores the payload on ``manager._pending_ssh_upload`` so any flow
    that polls for it can pick it up, *and* invokes the explicit
    callback set via ``manager.set_ssh_file_drop_callback`` if one is
    registered. Returns False so it drops off the idle queue cleanly.
    """
    manager._pending_ssh_upload = {
        "terminal_id": terminal_id,
        "local_paths": local_paths,
        "session": session,
        "ssh_target": ssh_target,
    }
    callback = getattr(manager, "_ssh_file_drop_callback", None)
    if callback is not None:
        callback(terminal_id, local_paths, session, ssh_target)
    return False
