# ashyterm/terminal/ssh_connection_monitor.py
"""SSH connection monitoring — post-spawn health checks."""

from typing import Optional

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from .ssh_error_analyzer import has_connection_error, has_shell_prompt
from ..utils.logger import log_swallowed_exception


def is_process_alive(pid: int) -> bool:
    """Check if process is still running."""
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def check_terminal_connection_status(
    terminal, terminal_id: int, logger
) -> Optional[bool]:
    """Check terminal text for connection status.

    Returns:
        True → continue checking, False → stop (resolved), None → timeout/fallback.
    """
    try:
        col_count = terminal.get_column_count()
        row_count = terminal.get_row_count()
        start_row = max(0, row_count - 5)

        result = terminal.get_text_range_format(
            0, start_row, 0, row_count - 1, col_count - 1,
        )
        if result and result[0]:
            recent_text = result[0].lower().strip()

            # Auto-reconnect still in progress — keep checking
            if "[auto-reconnect]" in recent_text:
                if getattr(terminal, "_connect_check_count", 0) < 10:
                    return True

            err = has_connection_error(recent_text)
            prompt = has_shell_prompt(recent_text)

            if prompt and not err:
                logger.info(f"SSH connected for terminal {terminal_id}")
                return False  # Resolved: connected

    except Exception as exc:
        log_swallowed_exception(exc)

    return None  # Still unknown — continue monitoring


def monitor_connection_status(
    terminal, terminal_id: int, pid: int, logger, on_success
) -> None:
    """Start periodic SSH connection health check via GLib.timeout.

    SSH considered successful when:
    1. Process still running after initial connect phase
    2. Terminal shows shell prompt (not error messages)
    """
    terminal._monitoring_pid = pid
    terminal._connect_check_count = 0
    terminal._last_line_count = 0

    def check_connection():
        if getattr(terminal, "_monitoring_pid", None) != pid:
            return False

        terminal._connect_check_count = (
            getattr(terminal, "_connect_check_count", 0) + 1
        )

        if not is_process_alive(pid):
            cleanup_connection_monitor(terminal)
            return False

        result = check_terminal_connection_status(terminal, terminal_id, logger)
        if result is not None:
            return result

        # Timeout fallback: assume connected after 10 checks (~10s)
        if terminal._connect_check_count < 10:
            return True

        logger.info(f"SSH appears connected for terminal {terminal_id} (timeout)")
        on_success(terminal)
        return False

    GLib.timeout_add(1000, check_connection)


def cleanup_connection_monitor(terminal) -> None:
    """Remove connection monitoring state from terminal."""
    for attr in ["_monitoring_pid", "_connect_check_count", "_last_line_count"]:
        if hasattr(terminal, attr):
            delattr(terminal, attr)


def on_connection_success(terminal, tab_manager=None, cancel_auto_reconnect=None) -> None:
    """Handle successful SSH connection: hide banner, stop monitors, clear flags."""
    cleanup_connection_monitor(terminal)

    # Hide error banner
    if tab_manager:
        tab_manager.hide_error_banner_for_terminal(terminal)

    # Stop auto-reconnect if active
    if getattr(terminal, "_auto_reconnect_active", False):
        terminal._auto_reconnect_active = False
        timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
        if timer_id:
            try:
                GLib.source_remove(timer_id)
            except Exception as exc:
                log_swallowed_exception(exc)
            terminal._auto_reconnect_timer_id = None

    # Clear retry flag
    terminal._retry_in_progress = False
