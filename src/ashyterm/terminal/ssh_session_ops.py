# ashyterm/terminal/ssh_session_ops.py
"""SSH session operations — batch management of terminals per session."""

from typing import Any, Dict

import gi

gi.require_version("GLib", "2.0")

from ..sessions.models import SessionItem


def has_active_ssh_sessions(registry: Any) -> bool:
    """Check if any SSH terminals are currently running."""
    for info in registry._terminals.values():
        if info.get("type") == "ssh" and info.get("status") == "running":
            return True
    return False


def reconnect_all_for_session(
    session_name: str, registry: Any, respawn_fn: Any, logger: Any,
) -> int:
    """Reconnect all disconnected terminals for a session.

    Returns number of terminals where reconnection was initiated.
    """
    terminal_ids = registry.get_terminals_for_session(session_name)
    reconnected = 0

    for terminal_id in terminal_ids:
        info = registry.get_terminal_info(terminal_id)
        if not info or info.get("status") != "disconnected":
            continue

        session = info.get("identifier")
        if not isinstance(session, SessionItem):
            continue

        terminal = registry.get_terminal(terminal_id)
        if not terminal:
            continue

        try:
            respawn_fn(terminal, terminal_id, session)
            logger.info(
                f"Initiated reconnection for terminal {terminal_id} "
                f"(session: {session_name})"
            )
            reconnected += 1
        except Exception as e:
            logger.error(f"Failed to reconnect terminal {terminal_id}: {e}")

    return reconnected


def disconnect_all_for_session(
    session_name: str, registry: Any, cancel_auto_reconnect_fn: Any, logger: Any,
) -> int:
    """Gracefully disconnect all terminals for a session.

    Cancels auto-reconnect and sends exit command to SSH.
    Returns number of terminals disconnected.
    """
    terminal_ids = registry.get_terminals_for_session(session_name)
    disconnected = 0

    for terminal_id in terminal_ids:
        terminal = registry.get_terminal(terminal_id)
        if not terminal:
            continue

        cancel_auto_reconnect_fn(terminal)

        try:
            terminal.feed_child(b"exit\n")
            disconnected += 1
            logger.info(
                f"Sent disconnect to terminal {terminal_id} "
                f"(session: {session_name})"
            )
        except Exception as e:
            logger.error(f"Failed to disconnect terminal {terminal_id}: {e}")

    return disconnected


def get_session_connection_status(session_name: str, registry: Any) -> Dict[str, Any]:
    """Get aggregated connection status for all terminals of a session.

    Returns dict with:
        - total_terminals: count
        - status_counts: {connected, disconnected, connecting, reconnecting, other}
        - overall_status: all_connected | all_disconnected | partial | connecting | no_terminals | unknown
    """
    terminal_ids = registry.get_terminals_for_session(session_name)

    status_counts = {
        "connected": 0,
        "disconnected": 0,
        "connecting": 0,
        "reconnecting": 0,
        "other": 0,
    }

    for terminal_id in terminal_ids:
        info = registry.get_terminal_info(terminal_id)
        if info:
            status = info.get("status", "unknown")
            if status in status_counts:
                status_counts[status] += 1
            else:
                status_counts["other"] += 1

    total = len(terminal_ids)

    if total == 0:
        overall = "no_terminals"
    elif status_counts["connected"] == total:
        overall = "all_connected"
    elif status_counts["disconnected"] == total:
        overall = "all_disconnected"
    elif status_counts["connected"] > 0:
        overall = "partial"
    elif status_counts["connecting"] > 0 or status_counts["reconnecting"] > 0:
        overall = "connecting"
    else:
        overall = "unknown"

    return {
        "total_terminals": total,
        "status_counts": status_counts,
        "overall_status": overall,
    }
