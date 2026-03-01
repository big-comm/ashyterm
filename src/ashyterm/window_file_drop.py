# ashyterm/window_file_drop.py
"""File drag-drop manager mixin for CommTerminalWindow."""

from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from .utils.translation_utils import _


class FileDragDropManager:
    """Mixin providing SSH file drop handling and upload coordination."""

    def _on_ssh_file_dropped(self, terminal_id, local_paths, session, ssh_target):
        """Handle files dropped on SSH terminal - open file manager and let it handle upload."""
        self.logger.info(
            f"SSH file drop: {len(local_paths)} files for terminal {terminal_id}"
        )

        terminal, page = self._get_terminal_and_page_for_drop(terminal_id)
        if not terminal or not page:
            return

        fm = self.tab_manager.file_managers.get(page)
        remote_dir = self._get_remote_dir_from_terminal(terminal)

        if self._can_upload_directly(fm):
            self._upload_files_directly(fm, local_paths, remote_dir)
        else:
            self._store_pending_drop_and_activate_fm(
                local_paths,
                remote_dir,
                session,
                ssh_target,
                terminal_id,
                page,
                terminal,
            )

    def _get_terminal_and_page_for_drop(self, terminal_id):
        """Get terminal and page for file drop operation."""
        terminal = self.terminal_manager.registry.get_terminal(terminal_id)
        if not terminal:
            self.logger.warning(f"Terminal {terminal_id} not found for file drop")
            return None, None

        page = self.tab_manager.get_page_for_terminal(terminal)
        if not page:
            self.logger.warning(f"Page not found for terminal {terminal_id}")
            return None, None

        return terminal, page

    def _get_remote_dir_from_terminal(self, terminal):
        """Try to get the current remote directory from the terminal."""
        from urllib.parse import unquote, urlparse

        try:
            uri = terminal.get_current_directory_uri()
            if uri:
                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    remote_dir = unquote(parsed_uri.path)
                    self.logger.info(f"Got remote directory from OSC7: {remote_dir}")
                    return remote_dir
        except Exception as e:
            self.logger.debug(f"Could not get terminal directory URI: {e}")
        return None

    def _can_upload_directly(self, fm) -> bool:
        """Check if file manager is ready for direct upload."""
        return (
            fm and fm._is_remote_session() and not getattr(fm, "_is_rebinding", False)
        )

    def _upload_files_directly(self, fm, local_paths, remote_dir):
        """Upload files directly via the file manager."""

        if remote_dir:
            fm.current_path = remote_dir
        paths = [Path(p) for p in local_paths]
        fm._show_upload_confirmation_dialog(paths)
        self.logger.info(f"Upload initiated for {len(paths)} files via file manager")

    def _store_pending_drop_and_activate_fm(
        self, local_paths, remote_dir, session, ssh_target, terminal_id, page, terminal
    ):
        """Store pending drop info and activate file manager."""
        self._pending_drop_files = local_paths
        self._pending_drop_remote_dir = remote_dir
        self._pending_drop_session = session
        self._pending_drop_ssh_target = ssh_target
        self._pending_drop_terminal_id = terminal_id
        self._pending_drop_page = page
        self._pending_drop_attempts = 0
        self._pending_drop_terminal = terminal

        # Ensure file manager panel is visible
        if not self.file_manager_button.get_active():
            self.file_manager_button.set_active(True)

        # After activation, force rebind to the correct terminal
        fm = self.tab_manager.file_managers.get(page)
        if fm:
            self.logger.info(
                f"Force rebinding FM to drop target terminal {terminal_id}"
            )
            fm.rebind_terminal(terminal)

        GLib.timeout_add(300, self._check_pending_drop_upload)

        # Show upload progress toast
        self.toast_overlay.add_toast(Adw.Toast(title=_("Preparing file upload\u2026")))

    def _check_pending_drop_upload(self):
        """Check if file manager is ready and process pending drop upload."""
        if not hasattr(self, "_pending_drop_files") or not self._pending_drop_files:
            return False

        self._pending_drop_attempts = getattr(self, "_pending_drop_attempts", 0) + 1
        if self._pending_drop_attempts > 30:
            self.logger.warning(
                "Timed out waiting for file manager to be ready for upload"
            )
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("File upload timed out. Please try again."))
            )
            self._clear_pending_drop()
            return False

        page = getattr(self, "_pending_drop_page", None)
        if not page:
            self.logger.warning("No page stored for pending drop")
            self._clear_pending_drop()
            return False

        fm = self.tab_manager.file_managers.get(page)
        wait_result = self._wait_for_file_manager_ready(fm)
        if wait_result is not None:
            return wait_result

        # File manager is ready - proceed with upload
        self._execute_pending_upload(fm)
        return False

    def _wait_for_file_manager_ready(self, fm):
        """Wait for file manager to be ready. Returns True to continue, False to stop, None if ready."""
        if not fm:
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: File manager not yet created"
            )
            return True

        if getattr(fm, "_is_rebinding", False):
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: File manager still rebinding"
            )
            return True

        if not fm.session_item:
            self.logger.debug(
                f"Attempt {self._pending_drop_attempts}: Session item not set"
            )
            return True

        if not fm._is_remote_session():
            self._try_rebind_if_needed(fm)
            return True

        return None  # Ready

    def _try_rebind_if_needed(self, fm):
        """Attempt rebind to correct terminal if needed."""
        if self._pending_drop_attempts % 5 != 1:
            return

        terminal = getattr(self, "_pending_drop_terminal", None)
        if not terminal:
            terminal = self.terminal_manager.registry.get_terminal(
                getattr(self, "_pending_drop_terminal_id", None)
            )
        if terminal:
            self.logger.info(
                f"Rebinding FM to terminal (attempt {self._pending_drop_attempts})"
            )
            fm.rebind_terminal(terminal)

    def _execute_pending_upload(self, fm):
        """Execute the pending upload operation."""

        self.logger.info(
            f"File manager ready after {self._pending_drop_attempts} attempts"
        )

        if self._pending_drop_remote_dir:
            fm.current_path = self._pending_drop_remote_dir

        paths = [Path(p) for p in self._pending_drop_files]
        fm._show_upload_confirmation_dialog(paths)
        self.logger.info(
            f"Pending upload initiated for {len(paths)} files via file manager"
        )
        self._clear_pending_drop()

    def _clear_pending_drop(self):
        """Clear all pending drop state."""
        self._pending_drop_files = None
        self._pending_drop_remote_dir = None
        self._pending_drop_session = None
        self._pending_drop_ssh_target = None
        self._pending_drop_terminal_id = None
        self._pending_drop_page = None
        self._pending_drop_terminal = None
        self._pending_drop_attempts = 0
