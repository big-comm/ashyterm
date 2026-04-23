# ashyterm/terminal/terminal_title.py
"""Pure helpers for building the title string shown on each tab.

The terminal manager resolves a terminal's display title from a small
set of inputs: its session type, the session object, any OSC7 cwd
hint, and — for local terminals — a "manual SSH" target detected from
the running process. The dispatch/formatting rules live here so the
manager stays focused on event wiring.
"""

from __future__ import annotations

from typing import Optional

from ..sessions.models import SessionItem
from ..utils.osc7_tracker import OSC7Info


def format_duration(seconds: float) -> str:
    """Format ``seconds`` as a compact ``Nh Mm`` / ``Mm Ss`` / ``Ss`` string.

    Used in user-facing notifications about long-running commands;
    keep it short so the Adw.Banner subtitle doesn't overflow.
    """
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def ssh_title(terminal_info: dict, osc7_info: Optional[OSC7Info]) -> str:
    """Render the tab title for an SSH-typed terminal."""
    session = terminal_info.get("identifier")
    if isinstance(session, SessionItem):
        if osc7_info:
            return f"{session.name}:{osc7_info.display_path}"
        return session.name
    return "Terminal"


def local_title(
    terminal_info: dict,
    *,
    ssh_target: Optional[str],
    osc7_info: Optional[OSC7Info],
) -> str:
    """Render the tab title for a local terminal.

    Falls back through: an active "manual SSH" target → OSC7 cwd →
    the SessionItem name → the raw identifier string.
    """
    if ssh_target:
        if osc7_info:
            return f"{ssh_target}:{osc7_info.display_path}"
        return ssh_target

    if osc7_info:
        return osc7_info.display_path

    identifier = terminal_info.get("identifier")
    if isinstance(identifier, SessionItem):
        return identifier.name
    return str(identifier)


def compute_title(
    terminal_info: dict,
    *,
    ssh_target: Optional[str],
    sftp_title: str,
    osc7_info: Optional[OSC7Info],
) -> str:
    """Dispatch by terminal type to the matching renderer.

    SFTP titles are computed externally (they need the tab_manager) and
    passed in as ``sftp_title``.
    """
    terminal_type = terminal_info.get("type")

    if terminal_type == "ssh":
        return ssh_title(terminal_info, osc7_info)
    if terminal_type == "local":
        return local_title(
            terminal_info, ssh_target=ssh_target, osc7_info=osc7_info
        )
    if terminal_type == "sftp":
        return sftp_title
    return "Terminal"
