# ashyterm/window_session.py
"""Window side of the session snapshot: restore on start, decide on close."""

from typing import TYPE_CHECKING, Optional

from .state.session_autosave import close_keeps_session

if TYPE_CHECKING:
    from .state.session_autosave import SessionAutosaver


class WindowSessionMixin:
    """Restores saved tabs into new windows and applies the close policy."""

    def _session_autosaver(self) -> Optional["SessionAutosaver"]:
        app = self.get_application()
        return getattr(app, "session_autosaver", None) if app else None

    def _restore_saved_session(self) -> bool:
        """Restore this window's share of the saved session; True if tabs came back."""
        autosaver = self._session_autosaver()
        if autosaver is None:
            return False

        window_state = getattr(self, "restored_window_state", None)
        if window_state is not None:
            self.restored_window_state = None
            try:
                return self.state_manager.restore_window(window_state)
            finally:
                autosaver.window_restored()

        saved_windows = autosaver.claim_restore()
        if saved_windows is None:
            return False
        restored = bool(saved_windows) and self.state_manager.restore_window(
            saved_windows[0]
        )
        for extra_state in saved_windows[1:]:
            self._open_restored_window(extra_state, autosaver)
        autosaver.finish_restore()
        return restored

    def _open_restored_window(self, window_state: dict, autosaver) -> None:
        try:
            window = self.get_application().create_new_window(
                restored_window_state=window_state
            )
            window.present()
        except Exception as error:
            autosaver.window_restored()
            self.logger.error(f"Failed to reopen a saved window: {error}")

    def _has_launch_request(self) -> bool:
        """Whether this window was opened to run something specific."""
        return bool(
            self.initial_ssh_target
            or self.initial_execute_command
            or self.initial_working_directory
        )

    def _is_last_open_window(self) -> bool:
        app = self.get_application()
        if app is None:
            return True
        return not any(
            other is not self
            and hasattr(other, "state_manager")
            and not getattr(other, "_cleanup_performed", False)
            for other in app.get_windows()
        )

    def _apply_session_close_choice(self) -> None:
        """Keep or forget the session when the last window is being closed."""
        autosaver = self._session_autosaver()
        if autosaver is None or not self._is_last_open_window():
            return
        policy = self.settings_manager.get("close_multiple_tabs_policy", "ask")
        keep = close_keeps_session(
            policy,
            self.tab_manager.get_tab_count(),
            getattr(self, "_session_close_response", None),
        )
        autosaver.close_last_window(keep)
