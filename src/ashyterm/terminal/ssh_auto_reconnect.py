# ashyterm/terminal/ssh_auto_reconnect.py
"""SSH auto-reconnect logic — timed retry loop with terminal feedback."""

import time
from datetime import datetime
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..sessions.models import SessionItem
from ..utils.translation_utils import _
from ..utils.logger import log_swallowed_exception


def start_auto_reconnect(
    terminal: Any, terminal_id: int, session: SessionItem,
    duration_mins: int, interval_secs: int, timeout_secs: int,
    settings_manager: Any, respawn_fn: Any, show_error_dialog_fn: Any, logger: Any,
) -> None:
    """Start automatic reconnection attempts for a failed SSH terminal.

    Keeps same terminal tab, re-spawns SSH sessions in it.
    Progress displayed inline in terminal.
    """
    terminal._auto_reconnect_active = True
    terminal._auto_reconnect_cancelled = False
    terminal._auto_reconnect_timer_id = None

    end_time = time.time() + (duration_mins * 60)
    max_attempts = (duration_mins * 60) // interval_secs
    state: dict[str, Any] = {"attempt": 0}

    def get_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def display_status(message: str, is_error: bool = False) -> None:
        if not terminal.get_realized():
            return
        color = "\x1b[33m" if not is_error else "\x1b[31m"
        reset = "\x1b[0m"
        dim = "\x1b[2m"
        terminal.feed(
            f"\r\n{dim}[{get_timestamp()}]{reset} {color}[Auto-Reconnect] {message}{reset}\r\n".encode("utf-8")
        )

    def show_connection_options() -> None:
        terminal._auto_reconnect_active = False
        terminal._auto_reconnect_timer_id = None
        GLib.idle_add(
            show_error_dialog_fn,
            session.name, session, terminal, terminal_id, 1,
        )

    def attempt_reconnect() -> bool:
        terminal._auto_reconnect_timer_id = None

        if getattr(terminal, "_auto_reconnect_cancelled", False):
            display_status(_("Cancelled by user."))
            terminal._auto_reconnect_active = False
            return False

        now = time.time()
        if now >= end_time:
            display_status(_("Time limit reached. Giving up."), is_error=True)
            display_status(_("Showing connection options..."))
            show_connection_options()
            return False

        state["attempt"] += 1
        remaining = int(end_time - now)
        remaining_mins = remaining // 60
        remaining_secs = remaining % 60

        display_status(
            _("Attempt {n}/{max} - Time remaining: {mins}m {secs}s").format(
                n=state["attempt"], max=max_attempts,
                mins=remaining_mins, secs=remaining_secs,
            )
        )

        try:
            original_timeout = settings_manager.get("ssh_connect_timeout", 30)
            settings_manager.set("ssh_connect_timeout", timeout_secs, save_immediately=False)

            respawn_fn(terminal, terminal_id, session)

            GLib.timeout_add(
                1000,
                lambda: (
                    settings_manager.set(
                        "ssh_connect_timeout", original_timeout, save_immediately=False,
                    )
                    or False
                ),
            )
        except Exception as e:
            logger.error(f"Auto-reconnect spawn error: {e}")
            display_status(_("Spawn error: {error}").format(error=str(e)), is_error=True)

        if now + interval_secs < end_time:
            timer_id = GLib.timeout_add_seconds(interval_secs, attempt_reconnect)
            terminal._auto_reconnect_timer_id = timer_id
        else:
            display_status(_("Maximum attempts reached."), is_error=True)
            display_status(_("Showing connection options..."))
            show_connection_options()

        return False  # Stop GLib repetition

    # Initial message
    display_status(
        _("Starting auto-reconnect: {attempts} attempts over {mins} minute(s), every {secs}s").format(
            attempts=max_attempts, mins=duration_mins, secs=interval_secs,
        )
    )
    display_status(_("Close this tab to cancel."))

    # First attempt after short delay
    timer_id = GLib.timeout_add(500, attempt_reconnect)
    terminal._auto_reconnect_timer_id = timer_id


def cancel_auto_reconnect(terminal: Any, logger: Any) -> None:
    """Cancel auto-reconnect, including pending timers."""
    terminal._auto_reconnect_cancelled = True
    terminal._auto_reconnect_active = False

    timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
    if timer_id is not None:
        try:
            GLib.source_remove(timer_id)
        except Exception as exc:
            log_swallowed_exception(exc)
        terminal._auto_reconnect_timer_id = None

    logger.info(
        f"Auto-reconnect cancelled for terminal {getattr(terminal, 'terminal_id', 'N/A')}"
    )


def is_auto_reconnect_active(terminal: Any) -> bool:
    """Check if auto-reconnect is active for a terminal."""
    return getattr(terminal, "_auto_reconnect_active", False)


def retry_ssh_in_same_terminal(
    terminal: Any, terminal_id: int, session: SessionItem,
    timeout: int, settings_manager: Any, respawn_fn: Any, logger: Any,
) -> bool:
    """Single SSH retry with extended timeout in same terminal.

    Unlike auto-reconnect: one attempt only, shows progress inline.
    """
    if getattr(terminal, "_retry_in_progress", False):
        logger.warning(f"Retry already in progress for terminal {terminal_id}")
        return False

    try:
        terminal._retry_in_progress = True

        terminal.feed(
            f"\r\n\x1b[33m[Retry] Attempting reconnection with {timeout}s timeout...\x1b[0m\r\n".encode("utf-8")
        )

        original_timeout = settings_manager.get("ssh_connect_timeout", 30)
        settings_manager.set("ssh_connect_timeout", timeout, save_immediately=False)

        respawn_fn(terminal, terminal_id, session)

        GLib.timeout_add(
            1000,
            lambda: (
                settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False,
                )
                or False
            ),
        )

        logger.info(
            f"Retrying SSH connection to '{session.name}' with {timeout}s timeout in same terminal"
        )
        return True

    except Exception as e:
        terminal._retry_in_progress = False
        logger.error(f"Failed to retry SSH in same terminal: {e}")
        terminal.feed(f"\r\n\x1b[31m[Retry] Failed: {e}\x1b[0m\r\n".encode("utf-8"))
        return False
