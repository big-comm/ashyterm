# ashyterm/terminal/ssh_lifecycle.py
"""SSH lifecycle management mixin — thin orchestrator delegating to specialized modules.

External dependencies (injected by host class):
    self.lifecycle_manager, self.registry, self.logger, self.tab_manager,
    self.settings_manager, self.spawner, self.osc7_tracker, self.manual_ssh_tracker,
    self.terminal_exit_handler, self._cleanup_lock, self._pending_kill_timers,
    self._stats, self._command_start_times, self._highlight_proxies
"""

import os
import signal
from typing import Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

from ..sessions.models import SessionItem
from ..utils.logger import log_terminal_event
from .registry import TerminalState

# Delegate modules
from .ssh_error_analyzer import (
    analyze_exit_status as _analyze_exit_status,
    analyze_ssh_error as _analyze_ssh_error,
    check_ssh_auth_error as _check_ssh_auth_error,
    extract_terminal_text as _extract_terminal_text,
    decode_exit_code as _decode_exit_code,
)
from .ssh_connection_monitor import (
    monitor_connection_status as _monitor_connection_status,
    cleanup_connection_monitor as _cleanup_connection_monitor,
    on_connection_success as _on_connection_success,
)
from .ssh_auto_reconnect import (
    start_auto_reconnect as _start_auto_reconnect,
    cancel_auto_reconnect as _cancel_auto_reconnect,
    is_auto_reconnect_active as _is_auto_reconnect_active,
    retry_ssh_in_same_terminal as _retry_ssh_in_same_terminal,
)
from .ssh_cleanup import (
    cleanup_terminal_ui as _cleanup_terminal_ui,
    cleanup_terminal as _cleanup_terminal,
)
from .ssh_spawn_callback import (
    on_spawn_callback as _on_spawn_callback,
    execute_command_in_terminal as _execute_command_in_terminal,
)
from .ssh_session_ops import (
    has_active_ssh_sessions as _has_active_ssh_sessions,
    reconnect_all_for_session as _reconnect_all_for_session,
    disconnect_all_for_session as _disconnect_all_for_session,
    get_session_connection_status as _get_session_connection_status,
)
from ..utils.logger import log_swallowed_exception


class SSHLifecycleMixin:
    """Mixin providing SSH error handling, auto-reconnect, and connection monitoring.

    Delegates to specialized modules:
    - ssh_error_analyzer: exit codes, auth detection, text analysis
    - ssh_connection_monitor: post-spawn health checks
    - ssh_auto_reconnect: timed retry loop
    - ssh_cleanup: resource deallocation pipeline
    - ssh_spawn_callback: post-spawn monitoring + startup commands
    - ssh_session_ops: batch session management
    """

    # ── Exit Handling ─────────────────────────────────────────────

    def _cancel_pending_kill_timer(self, terminal_id: int) -> None:
        if terminal_id in self._pending_kill_timers:
            GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _handle_ssh_failure(
        self, terminal: Vte.Terminal, terminal_id: int,
        terminal_name: str, identifier: Union[str, SessionItem],
        child_status: int,
    ) -> None:
        self.lifecycle_manager.transition_state(terminal_id, TerminalState.SPAWN_FAILED)
        self.logger.warning(
            f"SSH failed for '{terminal_name}' (status: {child_status})"
        )

        auto_reconnect_active = _is_auto_reconnect_active(terminal)
        is_auth_error = _check_ssh_auth_error(child_status, terminal)

        if is_auth_error and auto_reconnect_active:
            _cancel_auto_reconnect(terminal, self.logger)
            terminal.feed(
                b"\r\n\x1b[31m[Auth error - auto-reconnect stopped]\x1b[0m\r\n"
            )

        if auto_reconnect_active and not is_auth_error:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        else:
            GLib.idle_add(
                self._show_ssh_connection_error_dialog,
                terminal_name, identifier, terminal, terminal_id, child_status,
            )

    def _handle_normal_exit(
        self, terminal: Vte.Terminal, terminal_id: int,
        terminal_name: str, identifier: Union[str, SessionItem],
        child_status: int, exit_info: dict,
    ) -> None:
        if exit_info["is_ssh"] and self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        if exit_info["is_user_terminated"]:
            self.logger.info(
                f"Terminal '{terminal_name}' terminated by user signal "
                f"(exit code: {exit_info['decoded_exit_code']})"
            )

        if not self.lifecycle_manager.transition_state(terminal_id, TerminalState.EXITED):
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        self.logger.info(f"Terminal '{terminal_name}' exited (status: {child_status})")
        log_terminal_event("exited", terminal_name, f"status {child_status}")
        GLib.idle_add(
            self._cleanup_terminal_ui, terminal, terminal_id, child_status, identifier,
        )

    def _on_child_exited(
        self, terminal: Vte.Terminal, child_status: int,
        identifier: Union[str, SessionItem], terminal_id: int,
    ) -> None:
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            return

        try:
            _cleanup_connection_monitor(terminal)
            terminal._retry_in_progress = False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )
            self._cancel_pending_kill_timer(terminal_id)

            exit_info = _analyze_exit_status(terminal_info, child_status)

            if exit_info["ssh_failed"]:
                self._handle_ssh_failure(
                    terminal, terminal_id, terminal_name, identifier, child_status,
                )
            else:
                self._handle_normal_exit(
                    terminal, terminal_id, terminal_name,
                    identifier, child_status, exit_info,
                )
        except Exception as e:
            self.logger.error(f"Child exit handling failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    # ── Error Dialog / Banner ─────────────────────────────────────

    def _is_terminal_valid_for_error_dialog(
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int,
    ) -> bool:
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
        self, terminal: Vte.Terminal, session_name: str, terminal_id: int,
    ) -> bool:
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

    def _show_error_banner(
        self, terminal: Vte.Terminal, session_name: str,
        error_info: dict, session: SessionItem, terminal_id: int,
    ) -> None:
        if self.tab_manager:
            banner_shown = self.tab_manager.show_error_banner_for_terminal(
                terminal=terminal, session_name=session_name,
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
                self.logger.warning(f"Could not show inline banner for '{session_name}'")

        self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _show_ssh_connection_error_dialog(
        self, session_name, identifier, terminal, terminal_id, child_status,
    ):
        """Show SSH connection error via inline banner (non-blocking)."""
        if not self._is_terminal_valid_for_error_dialog(terminal, session_name, terminal_id):
            return False

        if self._should_skip_error_banner(terminal, session_name, terminal_id):
            return False

        try:
            exit_code = _decode_exit_code(child_status)
            self.logger.debug(
                f"SSH error: raw status={child_status}, decoded exit_code={exit_code}"
            )

            terminal_text = _extract_terminal_text(terminal)
            error_info = _analyze_ssh_error(exit_code, terminal_text)
            session = identifier if isinstance(identifier, SessionItem) else None

            self._show_error_banner(terminal, session_name, error_info, session, terminal_id)

        except Exception as e:
            self.logger.error(f"Failed to show SSH error: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

        return False

    # ── Auto-Reconnect ────────────────────────────────────────────

    def _respawn_ssh_in_terminal(self, terminal, terminal_id: int, session: SessionItem) -> None:
        """Re-spawn SSH session in existing terminal (for auto-reconnect)."""
        try:
            self.registry.update_terminal_status(terminal_id, "spawning")

            highlight_manager = self._get_highlight_manager()
            output_highlighting_enabled = highlight_manager.enabled_for_ssh
            if session.output_highlighting is not None:
                output_highlighting_enabled = session.output_highlighting

            user_data_for_spawn = (terminal_id, session)

            if output_highlighting_enabled:
                proxy = self.spawner.spawn_highlighted_ssh_session(
                    terminal, session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn, terminal_id=terminal_id,
                )
                if proxy:
                    self._highlight_proxies[terminal_id] = proxy
                else:
                    self.spawner.spawn_ssh_session(
                        terminal, session,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                    )
            else:
                self.spawner.spawn_ssh_session(
                    terminal, session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                )

            self.logger.info(f"Re-spawned SSH session in terminal {terminal_id}")
        except Exception as e:
            self.logger.error(f"Failed to re-spawn SSH: {e}")
            raise

    def start_auto_reconnect(
        self, terminal: Vte.Terminal, terminal_id: int, session: SessionItem,
        duration_mins: int, interval_secs: int, timeout_secs: int,
    ) -> None:
        _start_auto_reconnect(
            terminal, terminal_id, session,
            duration_mins, interval_secs, timeout_secs,
            self.settings_manager, self._respawn_ssh_in_terminal,
            self._show_ssh_connection_error_dialog, self.logger,
        )

    def cancel_auto_reconnect(self, terminal: Vte.Terminal) -> None:
        _cancel_auto_reconnect(terminal, self.logger)

    def is_auto_reconnect_active(self, terminal: Vte.Terminal) -> bool:
        return _is_auto_reconnect_active(terminal)

    def _retry_ssh_in_same_terminal(
        self, terminal, terminal_id: int, session: SessionItem, timeout: int = 30,
    ) -> bool:
        return _retry_ssh_in_same_terminal(
            terminal, terminal_id, session, timeout,
            self.settings_manager, self._respawn_ssh_in_terminal, self.logger,
        )

    # ── Cleanup Pipeline ──────────────────────────────────────────

    def _cleanup_terminal_ui(
        self, terminal: Vte.Terminal, terminal_id: int,
        child_status: int, identifier,
    ) -> bool:
        return _cleanup_terminal_ui(
            terminal, terminal_id, child_status, identifier,
            self.is_auto_reconnect_active, self.terminal_exit_handler,
            self.tab_manager, self._cleanup_terminal,
            self.lifecycle_manager.unmark_terminal_closing, self.logger,
        )

    def _cleanup_highlight_proxy(self, terminal_id: int) -> None:
        proxy = self._highlight_proxies.pop(terminal_id, None)
        if proxy and hasattr(proxy, "cleanup"):
            try:
                proxy.cleanup()
            except Exception as e:
                self.logger.debug(f"Highlight proxy cleanup failed: {e}")

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
        _cleanup_terminal(
            terminal, terminal_id,
            self.is_auto_reconnect_active, self.registry, self.spawner,
            self.osc7_tracker, self.manual_ssh_tracker,
            self._pending_kill_timers, self._command_start_times,
            self._stats, self._cleanup_lock, self.logger,
            self._cleanup_highlight_proxy,
        )

    # ── Spawn Callback + Command Execution ────────────────────────

    def _on_spawn_callback(
        self, terminal, pid: int, error, user_data,
    ) -> None:
        _on_spawn_callback(
            terminal, pid, error, user_data,
            self.registry, self.logger, self.tab_manager,
            self._monitor_connection_status, self._execute_command_in_terminal,
        )

    def _monitor_connection_status(self, terminal, terminal_id: int, pid: int) -> None:
        _monitor_connection_status(
            terminal, terminal_id, pid, self.logger, _on_connection_success,
        )

    def _execute_command_in_terminal(
        self, terminal, command: str, close_after_execute: bool = False,
    ) -> bool:
        return _execute_command_in_terminal(terminal, command, close_after_execute)

    # ── EOF Handler ───────────────────────────────────────────────

    def _on_eof(self, terminal, identifier, terminal_id: int) -> None:
        self._on_child_exited(terminal, 0, identifier, terminal_id)

    # ── Session Operations ────────────────────────────────────────

    def has_active_ssh_sessions(self) -> bool:
        return _has_active_ssh_sessions(self.registry)

    def reconnect_all_for_session(self, session_name: str) -> int:
        return _reconnect_all_for_session(
            session_name, self.registry, self._respawn_ssh_in_terminal, self.logger,
        )

    def disconnect_all_for_session(self, session_name: str) -> int:
        return _disconnect_all_for_session(
            session_name, self.registry, self.cancel_auto_reconnect, self.logger,
        )

    def get_session_connection_status(self, session_name: str) -> dict:
        return _get_session_connection_status(session_name, self.registry)

    # ── Process Termination ───────────────────────────────────────

    def _ensure_process_termination(self, pid: int, terminal_name: str, terminal_id: int) -> bool:
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

    def remove_terminal(self, terminal: Vte.Terminal, force_kill_group: bool = False) -> bool:
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

            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception as exc:
                log_swallowed_exception(exc)

            if info.get("status") in [
                TerminalState.EXITED.value, TerminalState.SPAWN_FAILED.value,
            ]:
                GLib.idle_add(
                    self._cleanup_terminal_ui, terminal, terminal_id, 0, identifier,
                )
                return True

            pid = info.get("process_id")
            if not pid or pid == -1:
                GLib.idle_add(
                    self._cleanup_terminal_ui, terminal, terminal_id, 0, identifier,
                )
                return True

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else str(identifier)
            )

            try:
                target_id = os.getpgid(pid) if force_kill_group else pid
                os.kill(target_id, signal.SIGHUP)
            except (ProcessLookupError, PermissionError) as e:
                self.logger.warning(
                    f"Could not send signal to PID {pid}, likely already exited: {e}"
                )
                GLib.idle_add(
                    self._cleanup_terminal_ui, terminal, terminal_id, 0, identifier,
                )
                return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_termination, pid, terminal_name, terminal_id,
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            return True
