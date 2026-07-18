# ashyterm/terminal/ssh_spawn_callback.py
"""SSH spawn callback — post-spawn monitoring and startup command execution."""

from typing import Any, Optional

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


def on_spawn_callback(
    terminal: Any, pid: int, error: Optional[GLib.Error], user_data: Any,
    registry: Any, logger: Any, tab_manager: Any,
    monitor_connection_fn: Any, execute_command_fn: Any,
) -> None:
    """Called when terminal spawn completes.

    For SSH: spawn success ≠ connection success.
    Monitor process status to detect actual connection.
    """
    try:
        final_user_data = user_data[0] if isinstance(user_data, tuple) else user_data
        user_data_tuple = final_user_data.get("original_user_data")
        terminal_id, user_data = user_data_tuple

        if error:
            logger.error(f"Spawn failed for terminal {terminal_id}: {error.message}")
            registry.update_terminal_status(terminal_id, "spawn_failed")
            return

        registry.update_terminal_process(terminal_id, pid)

        # For retry/auto-reconnect: wait for process exit to determine success/failure
        # If process exits quickly (< 3s), it failed. If still running, it's connected.
        has_banner = tab_manager and tab_manager.has_error_banner(terminal)
        is_auto_reconnect = getattr(terminal, "_auto_reconnect_active", False)
        is_retry = getattr(terminal, "_retry_in_progress", False)

        if has_banner or is_auto_reconnect or is_retry:
            monitor_connection_fn(terminal, terminal_id, pid)

        # Handle execute command — use flag to ensure single execution
        if (
            isinstance(user_data, dict)
            and user_data.get("execute_command")
            and pid > 0
            and not getattr(terminal, "_startup_command_executed", False)
        ):
            terminal._startup_command_executed = True
            command_to_exec = user_data["execute_command"]
            close_after = user_data.get("close_after_execute", False)

            def exec_startup(term=terminal, cmd=command_to_exec, close=close_after):
                execute_command_fn(term, cmd, close)
                return False

            GLib.timeout_add(100, exec_startup)

    except Exception as e:
        logger.error(f"Spawn callback failed: {e}")


def execute_command_in_terminal(terminal: Any, command: str, close_after_execute: bool = False) -> bool:
    """Execute command in terminal using bracketed paste mode.

    Bracketed paste prevents shell from auto-executing pasted content.
    """
    try:
        if not terminal or not command:
            return False
        command_to_run = f"({command}); exit" if close_after_execute else command
        paste_start = "\x1b[200~"
        paste_end = "\x1b[201~"
        terminal.feed_child(
            f"{paste_start}{command_to_run}{paste_end}\n".encode("utf-8")
        )
        return True
    except Exception:
        # logger not available in pure fn — caller should log
        return False
