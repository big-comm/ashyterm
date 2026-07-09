# ashyterm/terminal/ssh_cleanup.py
"""SSH terminal cleanup pipeline — orderly resource deallocation."""


import gi
from typing import Any

gi.require_version("GObject", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject

from ..utils.logger import log_terminal_event


def cleanup_terminal_ui(
    terminal: Any, terminal_id: int, child_status: int, identifier: Any,
    is_auto_reconnect_fn: Any, terminal_exit_handler: Any, tab_manager: Any,
    cleanup_fn: Any, unmark_closing_fn: Any, logger: Any,
) -> bool:
    """Cleanup terminal UI after process exit. Runs on GLib.idle_add.

    Returns False for GLib callback compatibility.
    """
    if is_auto_reconnect_fn(terminal):
        logger.warning(
            f"[CLEANUP_UI] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
        )
        return False

    try:
        if terminal_exit_handler:
            terminal_exit_handler(terminal, child_status, identifier)
        if tab_manager:
            tab_manager._on_terminal_process_exited(
                terminal, child_status, identifier,
            )
        else:
            cleanup_fn(terminal, terminal_id)
    except Exception as e:
        logger.error(f"Terminal UI cleanup failed: {e}")
    finally:
        unmark_closing_fn(terminal_id)
    return False


def cleanup_process_tracking(terminal_info: dict, spawner: Any) -> None:
    """Remove process from spawner tracker."""
    pid = terminal_info.get("process_id")
    if pid:
        spawner.process_tracker.unregister_process(pid)


def cleanup_terminal_tracking(terminal: Any, terminal_id: int, osc7_tracker: Any, manual_ssh_tracker: Any) -> None:
    """Remove OSC7 and SSH tracking for terminal."""
    osc7_tracker.untrack_terminal(terminal)
    manual_ssh_tracker.untrack(terminal_id)


def cleanup_terminal_handlers(terminal: Any) -> None:
    """Disconnect signal handlers and remove event controllers."""
    if hasattr(terminal, "ashy_handler_ids"):
        for handler_id in terminal.ashy_handler_ids:
            if GObject.signal_handler_is_connected(terminal, handler_id):
                terminal.disconnect(handler_id)
        terminal.ashy_handler_ids.clear()

    if hasattr(terminal, "ashy_controllers"):
        for controller in terminal.ashy_controllers:
            terminal.remove_controller(controller)
        terminal.ashy_controllers.clear()


def cleanup_terminal_attributes(terminal: Any, logger: Any) -> None:
    """Delete custom terminal attributes."""
    for attr in ["_osc8_hovered_uri", "_closed_by_user"]:
        if hasattr(terminal, attr):
            try:
                delattr(terminal, attr)
            except Exception as e:
                logger.debug(f"Could not delete {attr} attr: {e}")


def finalize_terminal_cleanup(
    terminal_id: int, terminal_name: str,
    registry: Any, pending_kill_timers: Any, command_start_times: Any,
    stats: dict,
) -> None:
    """Final step: unregister terminal, update stats, clear timers."""
    if registry.unregister_terminal(terminal_id):
        stats["terminals_closed"] += 1
        log_terminal_event("removed", terminal_name, "terminal resources cleaned")

    if terminal_id in pending_kill_timers:
        GLib.source_remove(pending_kill_timers.pop(terminal_id))

    command_start_times.pop(terminal_id, None)


def cleanup_terminal(
    terminal: Any, terminal_id: int,
    is_auto_reconnect_fn: Any, registry: Any, spawner: Any,
    osc7_tracker: Any, manual_ssh_tracker: Any,
    pending_kill_timers: Any, command_start_times: Any,
    stats: dict, cleanup_lock: Any, logger: Any,
    cleanup_highlight_proxy_fn: Any=None,
) -> None:
    """Full cleanup pipeline for a terminal.

    Guards against cleaning while auto-reconnect is active.
    """
    if is_auto_reconnect_fn(terminal):
        logger.warning(
            f"[CLEANUP] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
        )
        return

    with cleanup_lock:
        terminal_info = registry.get_terminal_info(terminal_id)
        if not terminal_info:
            return

        if cleanup_highlight_proxy_fn:
            cleanup_highlight_proxy_fn(terminal_id)

        cleanup_process_tracking(terminal_info, spawner)
        terminal_name = _get_terminal_name(terminal_info)

        logger.info(
            f"Cleaning up resources for terminal '{terminal_name}' (ID: {terminal_id})"
        )

        cleanup_terminal_tracking(terminal, terminal_id, osc7_tracker, manual_ssh_tracker)
        cleanup_terminal_handlers(terminal)
        cleanup_terminal_attributes(terminal, logger)
        finalize_terminal_cleanup(
            terminal_id, terminal_name,
            registry, pending_kill_timers, command_start_times, stats,
        )


def _get_terminal_name(terminal_info: dict) -> str:
    identifier = terminal_info.get("identifier", "Unknown")
    if isinstance(identifier, str):
        return identifier
    return getattr(identifier, "name", "Unknown")


def get_terminal_name_for_cleanup(terminal_info: dict) -> str:
    """Public alias for logging."""
    return _get_terminal_name(terminal_info)
