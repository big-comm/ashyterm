# ashyterm/terminal/ssh_lifecycle.py
"""SSH lifecycle management mixin: error handling, auto-reconnect, connection monitoring."""

import os
import signal
from typing import Any, Dict, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, GObject, Vte

from ..sessions.models import SessionItem
from ..utils.logger import log_terminal_event
from ..utils.translation_utils import _
from .registry import TerminalState


class SSHLifecycleMixin:
    """Mixin providing SSH error handling, auto-reconnect, and connection monitoring."""

    def _cancel_pending_kill_timer(self, terminal_id: int) -> None:
        """Cancels any pending kill timer for the terminal."""
        if terminal_id in self._pending_kill_timers:
            GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _analyze_exit_status(
        self, terminal: Vte.Terminal, terminal_info: dict, child_status: int
    ) -> dict:
        """Analyzes the exit status and returns exit information."""
        # os imported at module level

        if os.WIFEXITED(child_status):
            decoded_exit_code = os.WEXITSTATUS(child_status)
        elif os.WIFSIGNALED(child_status):
            decoded_exit_code = 128 + os.WTERMSIG(child_status)
        else:
            decoded_exit_code = child_status

        user_terminated_codes = {130, 137, 143}  # SIGINT, SIGKILL, SIGTERM
        is_user_terminated = decoded_exit_code in user_terminated_codes
        closed_by_user = getattr(terminal, "_closed_by_user", False)
        is_ssh = terminal_info.get("type") in ["ssh", "sftp"]

        ssh_failed = (
            is_ssh
            and child_status != 0
            and not closed_by_user
            and not is_user_terminated
        )

        return {
            "decoded_exit_code": decoded_exit_code,
            "is_user_terminated": is_user_terminated,
            "closed_by_user": closed_by_user,
            "is_ssh": is_ssh,
            "ssh_failed": ssh_failed,
        }

    def _handle_ssh_failure(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        terminal_name: str,
        identifier: Union[str, SessionItem],
        child_status: int,
    ) -> None:
        """Handles SSH connection failure."""
        self.lifecycle_manager.transition_state(terminal_id, TerminalState.SPAWN_FAILED)
        self.logger.warning(
            f"SSH failed for '{terminal_name}' (status: {child_status})"
        )

        auto_reconnect_active = getattr(terminal, "_auto_reconnect_active", False)
        is_auth_error = self._check_ssh_auth_error(terminal, child_status)

        if is_auth_error and auto_reconnect_active:
            self.cancel_auto_reconnect(terminal)
            terminal.feed(
                b"\r\n\x1b[31m[Auth error - auto-reconnect stopped]\x1b[0m\r\n"
            )

        if auto_reconnect_active and not is_auth_error:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        else:
            GLib.idle_add(
                self._show_ssh_connection_error_dialog,
                terminal_name,
                identifier,
                terminal,
                terminal_id,
                child_status,
            )

    def _handle_normal_exit(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        terminal_name: str,
        identifier: Union[str, SessionItem],
        child_status: int,
        exit_info: dict,
    ) -> None:
        """Handles normal or user-initiated terminal exit."""
        if exit_info["is_ssh"] and self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        if exit_info["is_user_terminated"]:
            self.logger.info(
                f"Terminal '{terminal_name}' terminated by user signal "
                f"(exit code: {exit_info['decoded_exit_code']})"
            )

        if not self.lifecycle_manager.transition_state(
            terminal_id, TerminalState.EXITED
        ):
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        self.logger.info(f"Terminal '{terminal_name}' exited (status: {child_status})")
        log_terminal_event("exited", terminal_name, f"status {child_status}")
        GLib.idle_add(
            self._cleanup_terminal_ui,
            terminal,
            terminal_id,
            child_status,
            identifier,
        )

    def _on_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        """Handle terminal child process exit."""
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            return

        try:
            self._cleanup_connection_monitor(terminal)
            terminal._retry_in_progress = False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )
            self._cancel_pending_kill_timer(terminal_id)

            exit_info = self._analyze_exit_status(terminal, terminal_info, child_status)

            if exit_info["ssh_failed"]:
                self._handle_ssh_failure(
                    terminal, terminal_id, terminal_name, identifier, child_status
                )
            else:
                self._handle_normal_exit(
                    terminal,
                    terminal_id,
                    terminal_name,
                    identifier,
                    child_status,
                    exit_info,
                )

        except Exception as e:
            self.logger.error(f"Child exit handling failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _check_ssh_auth_error(self, terminal: Vte.Terminal, child_status: int) -> bool:
        """Check if SSH failure is due to authentication error."""
        # os imported at module level

        # Decode exit code
        if os.WIFEXITED(child_status):
            exit_code = os.WEXITSTATUS(child_status)
        else:
            exit_code = child_status

        # Exit codes 5, 6 are common SSH auth failure codes
        if exit_code in (5, 6):
            return True

        # Check terminal text for auth patterns
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 20)
            result = terminal.get_text_range_format(
                0,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and len(result) > 0 and result[0]:
                text_lower = result[0].lower()
                auth_patterns = [
                    "permission denied",
                    "authentication failed",
                    "incorrect password",
                    "invalid password",
                    "too many authentication failures",
                ]
                for pattern in auth_patterns:
                    if pattern in text_lower:
                        return True
        except Exception:
            pass

        return False

    def _is_terminal_valid_for_error_dialog(
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int
    ) -> bool:
        """Checks if terminal is valid to show error dialog."""
        try:
            if terminal is None or not terminal.get_realized():
                self.logger.debug(
                    f"Skipping error dialog - terminal not realized for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return False
            if terminal.get_parent() is None:
                self.logger.debug(
                    f"Skipping error dialog - terminal orphaned for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return False
        except Exception as e:
            self.logger.debug(f"Terminal widget check failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return False
        return True

    def _should_skip_error_banner(
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int
    ) -> bool:
        """Determines if error banner should be skipped."""
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.debug(
                f"Skipping error banner - retry in progress for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return True

        if self.tab_manager and self.tab_manager.has_error_banner(terminal):
            self.logger.debug(
                f"Skipping error banner - banner already showing for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return True

        return False

    def _decode_exit_code(self, child_status: int) -> int:
        """Decodes the wait status to get actual exit code."""
        # os imported at module level

        if os.WIFEXITED(child_status):
            return os.WEXITSTATUS(child_status)
        elif os.WIFSIGNALED(child_status):
            return 128 + os.WTERMSIG(child_status)
        return child_status

    def _extract_terminal_text(self, terminal: Vte.Terminal) -> Optional[str]:
        """Extracts recent text from terminal for error analysis."""
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 50)

            result = terminal.get_text_range_format(
                Vte.Format.TEXT,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and len(result) > 0 and result[0]:
                return result[0]
        except Exception as text_err:
            self.logger.debug(f"Could not extract terminal text: {text_err}")
        return None

    def _analyze_ssh_error(self, exit_code: int, terminal_text: Optional[str]) -> dict:
        """Analyzes SSH error and returns error info dict."""
        from ..ui.ssh_dialogs import get_error_info

        error_type, _, error_description = get_error_info(exit_code, terminal_text)

        auth_error_types = (
            "auth_failed",
            "auth_multi_failed",
            "key_rejected",
            "key_format_error",
            "key_permissions",
        )
        host_key_error_types = ("host_key_failed", "host_key_changed")

        return {
            "error_type": error_type,
            "error_description": error_description,
            "is_auth_error": error_type in auth_error_types,
            "is_host_key_error": error_type in host_key_error_types,
        }

    def _show_error_banner(
        self,
        terminal: Vte.Terminal,
        session_name: str,
        error_info: dict,
        session: Optional[SessionItem],
        terminal_id: int,
    ) -> None:
        """Shows the error banner in the tab manager."""
        if self.tab_manager:
            banner_shown = self.tab_manager.show_error_banner_for_terminal(
                terminal=terminal,
                session_name=session_name,
                error_message=error_info["error_description"],
                session=session,
                is_auth_error=error_info["is_auth_error"],
                is_host_key_error=error_info["is_host_key_error"],
            )

            if banner_shown:
                self.logger.info(
                    f"Showed inline error banner for '{session_name}' "
                    f"(auth_error={error_info['is_auth_error']})"
                )
            else:
                self.logger.warning(
                    f"Could not show inline banner for '{session_name}'"
                )

        self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _show_ssh_connection_error_dialog(
        self, session_name, identifier, terminal, terminal_id, child_status
    ):
        """
        Show SSH connection error using non-blocking inline banner.

        Uses an inline banner above the terminal instead of a modal dialog,
        allowing users to continue using other tabs while deciding how to handle
        the connection failure.

        Returns False for GLib.idle_add callback compatibility.
        """
        if not self._is_terminal_valid_for_error_dialog(
            terminal, session_name, terminal_id
        ):
            return False

        if self._should_skip_error_banner(terminal, session_name, terminal_id):
            return False

        try:
            exit_code = self._decode_exit_code(child_status)
            self.logger.debug(
                f"SSH error: raw status={child_status}, decoded exit_code={exit_code}"
            )

            terminal_text = self._extract_terminal_text(terminal)
            error_info = self._analyze_ssh_error(exit_code, terminal_text)
            session = identifier if isinstance(identifier, SessionItem) else None

            self._show_error_banner(
                terminal, session_name, error_info, session, terminal_id
            )

        except Exception as e:
            self.logger.error(f"Failed to show SSH error: {e}")
            import traceback

            self.logger.debug(traceback.format_exc())
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

        return False

    def _retry_ssh_connection_with_timeout(
        self, session: SessionItem, timeout: int
    ) -> bool:
        """
        Retry SSH connection with extended timeout.
        Creates a new tab (single retry mode).
        """
        try:
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            if self.tab_manager:
                self.tab_manager.create_ssh_tab(session)

            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retried SSH connection to '{session.name}' with {timeout}s timeout"
            )
        except Exception as e:
            self.logger.error(f"Failed to retry SSH connection: {e}")

        return False

    def start_auto_reconnect(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        duration_mins: int,
        interval_secs: int,
        timeout_secs: int,
    ) -> None:
        """
        Start automatic reconnection attempts for a failed SSH terminal.

        This keeps the same terminal tab and re-spawns SSH sessions in it.
        Progress is displayed inline in the terminal itself.
        """
        import time
        from datetime import datetime

        # Store auto-reconnect state on the terminal
        terminal._auto_reconnect_active = True
        terminal._auto_reconnect_cancelled = False
        terminal._auto_reconnect_timer_id = None

        end_time = time.time() + (duration_mins * 60)
        max_attempts = (duration_mins * 60) // interval_secs

        state = {
            "attempt": 0,
        }

        def get_timestamp() -> str:
            """Get current timestamp string."""
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def display_status(message: str, is_error: bool = False) -> None:
            """Display status message in the terminal with timestamp."""
            if not terminal.get_realized():
                return
            color = "\x1b[33m" if not is_error else "\x1b[31m"  # Yellow or Red
            reset = "\x1b[0m"
            dim = "\x1b[2m"
            timestamp = get_timestamp()
            terminal.feed(
                f"\r\n{dim}[{timestamp}]{reset} {color}[Auto-Reconnect] {message}{reset}\r\n".encode(
                    "utf-8"
                )
            )

        def show_connection_options() -> None:
            """Show connection error dialog with options when auto-reconnect exhausted."""
            terminal._auto_reconnect_active = False
            terminal._auto_reconnect_timer_id = None

            # Show the connection error dialog to give user options
            GLib.idle_add(
                self._show_ssh_connection_error_dialog,
                session.name,
                session,
                terminal,
                terminal_id,
                1,  # Non-zero status to indicate failure
            )

        def attempt_reconnect() -> bool:
            """Attempt a single reconnection.

            Returns False to stop GLib.timeout_add repetition (required by GTK).
            """
            # Clear timer reference since we're executing
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
                    n=state["attempt"],
                    max=max_attempts,
                    mins=remaining_mins,
                    secs=remaining_secs,
                )
            )

            # Re-spawn SSH in the same terminal
            try:
                original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
                self.settings_manager.set(
                    "ssh_connect_timeout", timeout_secs, save_immediately=False
                )

                # Re-spawn the SSH session in the existing terminal
                self._respawn_ssh_in_terminal(terminal, terminal_id, session)

                # Restore timeout
                GLib.timeout_add(
                    1000,
                    lambda: (
                        self.settings_manager.set(
                            "ssh_connect_timeout",
                            original_timeout,
                            save_immediately=False,
                        )
                        or False
                    ),
                )

            except Exception as e:
                self.logger.error(f"Auto-reconnect spawn error: {e}")
                display_status(
                    _("Spawn error: {error}").format(error=str(e)), is_error=True
                )

            # Schedule next attempt (the child-exited handler will check auto_reconnect state)
            if now + interval_secs < end_time:
                timer_id = GLib.timeout_add_seconds(interval_secs, attempt_reconnect)
                terminal._auto_reconnect_timer_id = timer_id
            else:
                display_status(_("Maximum attempts reached."), is_error=True)
                display_status(_("Showing connection options..."))
                show_connection_options()

            return False  # Don't repeat this call

        # Display initial message
        display_status(
            _(
                "Starting auto-reconnect: {attempts} attempts over {mins} minute(s), every {secs}s"
            ).format(
                attempts=max_attempts,
                mins=duration_mins,
                secs=interval_secs,
            )
        )
        display_status(_("Close this tab to cancel."))

        # Start first attempt after a short delay
        timer_id = GLib.timeout_add(500, attempt_reconnect)
        terminal._auto_reconnect_timer_id = timer_id

    def _respawn_ssh_in_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
    ) -> None:
        """
        Re-spawn an SSH session in an existing terminal.
        This is used for auto-reconnect to avoid creating new tabs.
        """
        try:
            # Update registry to show we're spawning
            self.registry.update_terminal_status(terminal_id, "spawning")

            # Check if we should use highlighted SSH
            highlight_manager = self._get_highlight_manager()
            output_highlighting_enabled = highlight_manager.enabled_for_ssh
            if session.output_highlighting is not None:
                output_highlighting_enabled = session.output_highlighting

            should_spawn_highlighted = output_highlighting_enabled

            user_data_for_spawn = (terminal_id, session)

            if should_spawn_highlighted:
                proxy = self.spawner.spawn_highlighted_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    terminal_id=terminal_id,
                )
                if proxy:
                    self._highlight_proxies[terminal_id] = proxy
                else:
                    # Fallback to standard
                    self.spawner.spawn_ssh_session(
                        terminal,
                        session,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                    )
            else:
                self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                )

            self.logger.info(f"Re-spawned SSH session in terminal {terminal_id}")

        except Exception as e:
            self.logger.error(f"Failed to re-spawn SSH: {e}")
            raise

    def _retry_ssh_in_same_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        timeout: int = 30,
    ) -> bool:
        """
        Retry SSH connection in the same terminal (single retry mode).

        Unlike auto-reconnect, this does a single retry with extended timeout
        and shows the connection attempt in the same terminal.

        Args:
            terminal: The terminal to retry in.
            terminal_id: Terminal ID.
            session: Session to connect.
            timeout: Connection timeout in seconds.

        Returns:
            True if retry was initiated, False otherwise.
        """
        # Prevent multiple simultaneous retries
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.warning(f"Retry already in progress for terminal {terminal_id}")
            return False

        try:
            # Mark retry in progress - will be cleared by _on_child_exited or _on_connection_success
            terminal._retry_in_progress = True

            # Display retry message
            terminal.feed(
                f"\r\n\x1b[33m[Retry] Attempting reconnection with {timeout}s timeout...\x1b[0m\r\n".encode(
                    "utf-8"
                )
            )

            # Set temporary timeout
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            # Re-spawn in the same terminal
            self._respawn_ssh_in_terminal(terminal, terminal_id, session)

            # Restore original timeout after a delay
            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retrying SSH connection to '{session.name}' with {timeout}s timeout in same terminal"
            )
            return True

        except Exception as e:
            terminal._retry_in_progress = False
            self.logger.error(f"Failed to retry SSH in same terminal: {e}")
            terminal.feed(f"\r\n\x1b[31m[Retry] Failed: {e}\x1b[0m\r\n".encode("utf-8"))
            return False

    def cancel_auto_reconnect(self, terminal: Vte.Terminal) -> None:
        """Cancel auto-reconnect for a terminal, including any pending timers."""
        terminal._auto_reconnect_cancelled = True
        terminal._auto_reconnect_active = False

        # Cancel pending timer if exists
        timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
        if timer_id is not None:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            terminal._auto_reconnect_timer_id = None

        self.logger.info(
            f"Auto-reconnect cancelled for terminal {getattr(terminal, 'terminal_id', 'N/A')}"
        )

    def is_auto_reconnect_active(self, terminal: Vte.Terminal) -> bool:
        """Check if auto-reconnect is active for a terminal."""
        return getattr(terminal, "_auto_reconnect_active", False)

    def _on_eof(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        self._on_child_exited(terminal, 0, identifier, terminal_id)

    def _cleanup_terminal_ui(
        self, terminal: Vte.Terminal, terminal_id: int, child_status: int, identifier
    ) -> bool:
        """Cleanup terminal UI after process exit.

        Returns False for GLib.idle_add callback compatibility.
        """
        # Safety check: Don't cleanup if auto-reconnect is active
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP_UI] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return False

        try:
            if self.terminal_exit_handler:
                self.terminal_exit_handler(terminal, child_status, identifier)
            if self.tab_manager:
                self.tab_manager._on_terminal_process_exited(
                    terminal, child_status, identifier
                )
            else:
                self._cleanup_terminal(terminal, terminal_id)
        except Exception as e:
            self.logger.error(f"Terminal UI cleanup failed: {e}")
        finally:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        return False

    def _cleanup_process_tracking(self, terminal_info: dict) -> None:
        """Cleans up process tracking for the terminal."""
        pid = terminal_info.get("process_id")
        if pid:
            self.spawner.process_tracker.unregister_process(pid)

    def _get_terminal_name_for_cleanup(self, terminal_info: dict) -> str:
        """Gets the terminal name for logging during cleanup."""
        identifier = terminal_info.get("identifier", "Unknown")
        if isinstance(identifier, str):
            return identifier
        return getattr(identifier, "name", "Unknown")

    def _cleanup_terminal_tracking(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Cleans up OSC7 and SSH tracking for the terminal."""
        self.osc7_tracker.untrack_terminal(terminal)
        self.manual_ssh_tracker.untrack(terminal_id)

    def _cleanup_terminal_handlers(self, terminal: Vte.Terminal) -> None:
        """Disconnects signal handlers and removes controllers."""
        if hasattr(terminal, "ashy_handler_ids"):
            for handler_id in terminal.ashy_handler_ids:
                if GObject.signal_handler_is_connected(terminal, handler_id):
                    terminal.disconnect(handler_id)
            terminal.ashy_handler_ids.clear()

        if hasattr(terminal, "ashy_controllers"):
            for controller in terminal.ashy_controllers:
                terminal.remove_controller(controller)
            terminal.ashy_controllers.clear()

    def _cleanup_terminal_attributes(self, terminal: Vte.Terminal) -> None:
        """Cleans up custom attributes from the terminal."""
        attrs_to_delete = ["_osc8_hovered_uri", "_closed_by_user"]
        for attr in attrs_to_delete:
            if hasattr(terminal, attr):
                try:
                    delattr(terminal, attr)
                except Exception as e:
                    self.logger.debug(f"Could not delete {attr} attr: {e}")

    def _finalize_terminal_cleanup(self, terminal_id: int, terminal_name: str) -> None:
        """Finalizes terminal cleanup by unregistering and updating stats."""
        if self.registry.unregister_terminal(terminal_id):
            self._stats["terminals_closed"] += 1
            log_terminal_event("removed", terminal_name, "terminal resources cleaned")

        if terminal_id in self._pending_kill_timers:
            GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return

        with self._cleanup_lock:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                return

            self._cleanup_highlight_proxy(terminal_id)
            self._cleanup_process_tracking(terminal_info)
            terminal_name = self._get_terminal_name_for_cleanup(terminal_info)

            self.logger.info(
                f"Cleaning up resources for terminal '{terminal_name}' (ID: {terminal_id})"
            )

            self._cleanup_terminal_tracking(terminal, terminal_id)
            self._cleanup_terminal_handlers(terminal)
            self._cleanup_terminal_attributes(terminal)
            self._finalize_terminal_cleanup(terminal_id, terminal_name)

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any,
    ) -> None:
        """
        Called when terminal spawn completes.

        For SSH: spawn success just means process started.
        We monitor process status to detect actual connection success.
        """
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            user_data_tuple = final_user_data.get("original_user_data")
            terminal_id, user_data = user_data_tuple

            if error:
                self.logger.error(
                    f"Spawn failed for terminal {terminal_id}: {error.message}"
                )
                self.registry.update_terminal_status(terminal_id, "spawn_failed")
                return

            self.registry.update_terminal_process(terminal_id, pid)

            # For retry/auto-reconnect: wait for process exit to determine success/failure
            # If process exits quickly (< 3s), it failed. If still running, it's connected.
            has_banner = self.tab_manager and self.tab_manager.has_error_banner(
                terminal
            )
            is_auto_reconnect = getattr(terminal, "_auto_reconnect_active", False)
            is_retry = getattr(terminal, "_retry_in_progress", False)

            if has_banner or is_auto_reconnect or is_retry:
                self._monitor_connection_status(terminal, terminal_id, pid)

            # Handle execute command - use a flag to ensure single execution
            if (
                isinstance(user_data, dict)
                and user_data.get("execute_command")
                and pid > 0
                and not getattr(terminal, "_startup_command_executed", False)
            ):
                terminal._startup_command_executed = True
                command_to_exec = user_data["execute_command"]
                close_after = user_data.get("close_after_execute", False)

                def exec_startup_command(
                    term=terminal, cmd=command_to_exec, close=close_after
                ):
                    self._execute_command_in_terminal(term, cmd, close)
                    return False  # Remove from timeout queue

                GLib.timeout_add(100, exec_startup_command)

        except Exception as e:
            self.logger.error(f"Spawn callback failed: {e}")

    def _is_process_alive(self, pid: int) -> bool:
        """Checks if a process is still running."""
        # os imported at module level

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _check_terminal_connection_status(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> Optional[bool]:
        """
        Checks terminal text for connection status.
        Returns True to continue checking, False to stop, None for timeout fallback.
        """
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 5)

            result = terminal.get_text_range_format(
                0,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and result[0]:
                recent_text = result[0].lower().strip()

                if "[auto-reconnect]" in recent_text:
                    if terminal._connect_check_count < 10:
                        return True

                has_recent_error = self._has_connection_error(recent_text)
                has_prompt = self._has_shell_prompt(recent_text)

                if has_prompt and not has_recent_error:
                    self.logger.info(f"SSH connected for terminal {terminal_id}")
                    self._on_connection_success(terminal)
                    return False

        except Exception:
            pass

        return None

    def _has_connection_error(self, text: str) -> bool:
        """Checks if text contains SSH connection error patterns."""
        error_patterns = [
            "no route to host",
            "connection refused",
            "connection timed out",
            "permission denied",
            "authentication failed",
            "host key verification failed",
            "broken pipe",
        ]
        return any(p in text for p in error_patterns)

    def _has_shell_prompt(self, text: str) -> bool:
        """Checks if text contains shell prompt indicators."""
        success_patterns = [
            "$",
            "#",
            "❯",
            "➜",
            "›",
            "last login:",
            "welcome to",
        ]
        return any(p in text for p in success_patterns)

    def _monitor_connection_status(
        self, terminal: Vte.Terminal, terminal_id: int, pid: int
    ) -> None:
        """
        Monitor SSH connection status after spawn.

        SSH connection is considered successful when:
        1. Process is still running after initial connect phase
        2. Terminal shows shell prompt in recent lines (not error messages)
        """
        terminal._monitoring_pid = pid
        terminal._connect_check_count = 0
        terminal._last_line_count = 0

        def check_connection():
            """Periodically check if SSH is truly connected."""
            if getattr(terminal, "_monitoring_pid", None) != pid:
                return False

            terminal._connect_check_count = (
                getattr(terminal, "_connect_check_count", 0) + 1
            )

            if not self._is_process_alive(pid):
                self._cleanup_connection_monitor(terminal)
                return False

            connection_result = self._check_terminal_connection_status(
                terminal, terminal_id
            )
            if connection_result is not None:
                return connection_result

            if terminal._connect_check_count < 10:
                return True

            self.logger.info(
                f"SSH appears connected for terminal {terminal_id} (timeout)"
            )
            self._on_connection_success(terminal)
            return False

        GLib.timeout_add(1000, check_connection)

    def _cleanup_connection_monitor(self, terminal: Vte.Terminal) -> None:
        """Clean up connection monitoring state."""
        for attr in ["_monitoring_pid", "_connect_check_count", "_last_line_count"]:
            if hasattr(terminal, attr):
                delattr(terminal, attr)

    def _on_connection_success(self, terminal: Vte.Terminal) -> None:
        """Handle successful SSH connection."""
        self._cleanup_connection_monitor(terminal)

        # Hide error banner
        if self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        # Stop auto-reconnect
        if getattr(terminal, "_auto_reconnect_active", False):
            terminal._auto_reconnect_active = False
            timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
            if timer_id:
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
                terminal._auto_reconnect_timer_id = None

        # Clear retry flag
        terminal._retry_in_progress = False

    def _execute_command_in_terminal(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool = False
    ) -> bool:
        """Execute a command in the terminal.

        Uses bracketed paste mode to prevent the terminal from
        auto-executing pasted content in supported shells.
        """
        try:
            if not terminal or not command:
                return False
            command_to_run = f"({command}); exit" if close_after_execute else command
            # Wrap with bracketed paste escape sequences so the shell treats
            # the text as pasted input rather than typed commands.
            paste_start = "\x1b[200~"
            paste_end = "\x1b[201~"
            terminal.feed_child(
                f"{paste_start}{command_to_run}{paste_end}\n".encode("utf-8")
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to execute command '{command}': {e}")
            return False

    def _ensure_process_terminated(
        self, pid: int, terminal_name: str, terminal_id: int
    ) -> bool:
        try:
            self._pending_kill_timers.pop(terminal_id, None)
            os.kill(pid, 0)
            self.logger.warning(
                f"Process {pid} ('{terminal_name}') did not exit gracefully. Sending SIGKILL."
            )
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            self.logger.error(f"Error during final check for PID {pid}: {e}")
        return False

    def remove_terminal(
        self, terminal: Vte.Terminal, force_kill_group: bool = False
    ) -> bool:
        # Cancel auto-reconnect FIRST before any other cleanup
        # This ensures we stop reconnection attempts immediately when closing
        if self.is_auto_reconnect_active(terminal):
            self.cancel_auto_reconnect(terminal)

        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False
            info = self.registry.get_terminal_info(terminal_id)
            if not info:
                return False

            identifier = info.get("identifier", "Unknown")

            # Mark terminal as closed by user
            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception:
                pass

            # If terminal already exited or spawn failed, just do UI cleanup
            if info.get("status") in [
                TerminalState.EXITED.value,
                TerminalState.SPAWN_FAILED.value,
            ]:
                # Need full UI cleanup to close the tab
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            pid = info.get("process_id")
            if not pid or pid == -1:
                # No process to kill, just do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            terminal_name = (
                identifier.name
                if isinstance(identifier, SessionItem)
                else str(identifier)
            )

            try:
                target_id = os.getpgid(pid) if force_kill_group else pid
                os.kill(target_id, signal.SIGHUP)
            except (ProcessLookupError, PermissionError) as e:
                self.logger.warning(
                    f"Could not send signal to PID {pid}, likely already exited: {e}"
                )
                # Process already exited, do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_terminated, pid, terminal_name, terminal_id
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            return True

    def has_active_ssh_sessions(self) -> bool:
        for info in self.registry._terminals.values():
            if info.get("type") == "ssh" and info.get("status") == "running":
                return True
        return False

    def reconnect_all_for_session(self, session_name: str) -> int:
        """
        Reconnect all disconnected terminals for a given session.

        Args:
            session_name: Name of the session to reconnect terminals for.

        Returns:
            Number of terminals where reconnection was initiated.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        reconnected = 0

        for terminal_id in terminal_ids:
            if self._try_reconnect_terminal(terminal_id, session_name):
                reconnected += 1

        return reconnected

    def _try_reconnect_terminal(self, terminal_id: int, session_name: str) -> bool:
        """Try to reconnect a single disconnected terminal.

        Returns True if reconnection was initiated.
        """
        info = self.registry.get_terminal_info(terminal_id)
        if not info or info.get("status") != "disconnected":
            return False

        session = info.get("identifier")
        if not isinstance(session, SessionItem):
            return False

        terminal = self.registry.get_terminal(terminal_id)
        if not terminal:
            return False

        try:
            self._respawn_ssh_in_terminal(terminal, terminal_id, session)
            self.logger.info(
                f"Initiated reconnection for terminal {terminal_id} "
                f"(session: {session_name})"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to reconnect terminal {terminal_id}: {e}")
            return False

    def disconnect_all_for_session(self, session_name: str) -> int:
        """
        Gracefully disconnect all terminals for a session.

        This cancels any auto-reconnect and sends exit command to SSH.

        Args:
            session_name: Name of the session to disconnect.

        Returns:
            Number of terminals disconnected.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        disconnected = 0

        for terminal_id in terminal_ids:
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                # Cancel any active auto-reconnect
                self.cancel_auto_reconnect(terminal)

                # Send exit command to terminate SSH session gracefully
                try:
                    terminal.feed_child(b"exit\n")
                    disconnected += 1
                    self.logger.info(
                        f"Sent disconnect to terminal {terminal_id} "
                        f"(session: {session_name})"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to disconnect terminal {terminal_id}: {e}"
                    )

        return disconnected

    def get_session_connection_status(self, session_name: str) -> Dict[str, Any]:
        """
        Get aggregated connection status for all terminals of a session.

        Args:
            session_name: Name of the session.

        Returns:
            Dictionary with connection status summary.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)

        status_counts = {
            "connected": 0,
            "disconnected": 0,
            "connecting": 0,
            "reconnecting": 0,
            "other": 0,
        }

        for terminal_id in terminal_ids:
            info = self.registry.get_terminal_info(terminal_id)
            if info:
                status = info.get("status", "unknown")
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts["other"] += 1

        total = len(terminal_ids)

        # Determine overall status
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
