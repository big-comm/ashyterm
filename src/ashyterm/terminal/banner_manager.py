# ashyterm/terminal/banner_manager.py
"""SSH error banner management delegate for TabManager.

Handles showing, hiding, and acting on error banners displayed
above terminal panes (retry, reconnect, edit session, fix host key).
"""

import subprocess
from typing import TYPE_CHECKING, Optional

from gi.repository import Adw, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .tabs import TabManager


class BannerManager:
    """Manages SSH error banners for terminal panes."""

    def __init__(self, tab_manager: "TabManager") -> None:
        self.tm = tab_manager
        self.logger = get_logger("ashyterm.tabs.banner")

    # -- pane lookup ----------------------------------------------------------

    def find_terminal_pane_recursive(
        self, widget, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Recursively searches for the ToolbarView containing the terminal."""
        if widget is None:
            return None

        if isinstance(widget, Adw.ToolbarView):
            if getattr(widget, "terminal", None) == terminal_to_find:
                return widget

        found = self._search_paned_children(widget, terminal_to_find)
        if found:
            return found

        return self._search_single_child(widget, terminal_to_find)

    def find_pane_for_terminal(
        self, page: Adw.ViewStackPage, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Finds the Adw.ToolbarView pane that contains a specific terminal."""
        return self.find_terminal_pane_recursive(page.get_child(), terminal_to_find)

    # -- banner lifecycle -----------------------------------------------------

    def show_error_banner_for_terminal(
        self,
        terminal: Vte.Terminal,
        session_name: str,
        error_message: str = "",
        session: Optional[SessionItem] = None,
        is_auth_error: bool = False,
        is_host_key_error: bool = False,
    ) -> bool:
        """Show a non-blocking error banner above the terminal."""
        from ..ui.widgets.ssh_error_banner import BannerAction, SSHErrorBanner

        page = self.tm.get_page_for_terminal(terminal)
        if not page:
            self.logger.warning("Cannot show banner - terminal has no page")
            return False

        terminal_id = getattr(terminal, "terminal_id", None)

        existing_result = self._check_existing_banner(terminal, terminal_id)
        if existing_result is not None:
            return existing_result

        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            self.logger.warning(
                f"Cannot show banner - terminal parent is not ScrolledWindow: {type(scrolled_window)}"
            )
            return False

        container = scrolled_window.get_parent()
        if not container:
            self.logger.warning("Cannot show banner - scrolled window has no parent")
            return False

        banner = SSHErrorBanner(
            session_name=session_name,
            error_message=error_message,
            session=session,
            terminal_id=terminal_id,
            is_auth_error=is_auth_error,
            is_host_key_error=is_host_key_error,
        )

        def on_banner_action(action: BannerAction, tid: int, config: dict):
            self._handle_banner_action(action, terminal, session, tid, config)

        banner.set_action_callback(on_banner_action)
        banner.connect(
            "dismissed", lambda b: self.hide_error_banner_for_terminal(terminal)
        )

        banner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        banner_box.set_vexpand(True)
        banner_box.set_hexpand(True)
        banner_box.add_css_class("ssh-error-banner-container")
        banner_box.append(banner)

        if not self._insert_banner_in_container(
            container, scrolled_window, banner_box, banner, terminal
        ):
            return False

        terminal._error_banner = banner
        terminal._banner_box = banner_box

        self.logger.info(
            f"Showed error banner for terminal {terminal_id}: {session_name}"
        )
        return True

    def hide_error_banner_for_terminal(self, terminal: Vte.Terminal) -> bool:
        """Hide and remove error banner from a terminal."""
        terminal_id = getattr(terminal, "terminal_id", None)

        if not hasattr(terminal, "_error_banner") or not terminal._error_banner:
            return False

        banner = terminal._error_banner
        banner_box = getattr(terminal, "_banner_box", None)

        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            terminal._error_banner = None
            terminal._banner_box = None
            return False

        if banner_box:
            container = banner_box.get_parent()
            banner_box.remove(banner)
            banner_box.remove(scrolled_window)

            if isinstance(container, Adw.Bin):
                container.set_child(None)
                container.set_child(scrolled_window)
            elif isinstance(container, Adw.ToolbarView):
                container.set_content(None)
                container.set_content(scrolled_window)
        else:
            parent = banner.get_parent()
            if parent:
                parent.remove(banner)

        terminal._error_banner = None
        terminal._banner_box = None

        self.logger.info(f"Removed error banner for terminal {terminal_id}")
        return True

    def has_error_banner(self, terminal: Vte.Terminal) -> bool:
        """Check if terminal has an active error banner."""
        return hasattr(terminal, "_error_banner") and terminal._error_banner is not None

    # -- banner action handling -----------------------------------------------

    def _handle_banner_action(
        self,
        action,
        terminal: Vte.Terminal,
        session: Optional[SessionItem],
        terminal_id: int,
        config: dict,
    ) -> None:
        """Handle action from error banner."""
        from ..ui.widgets.ssh_error_banner import BannerAction

        self.logger.info(f"Banner action: {action} for terminal {terminal_id}")

        if action == BannerAction.RETRY:
            if session:
                self.hide_error_banner_for_terminal(terminal)
                timeout = config.get("timeout", 30)
                GLib.idle_add(
                    self.tm.terminal_manager._retry_ssh_in_same_terminal,
                    terminal,
                    terminal_id,
                    session,
                    timeout,
                )

        elif action == BannerAction.AUTO_RECONNECT:
            if session:
                self.hide_error_banner_for_terminal(terminal)
                duration = config.get("duration_mins", 5)
                interval = config.get("interval_secs", 10)
                timeout = config.get("timeout_secs", 30)

                self.tm.terminal_manager.lifecycle_manager.unmark_terminal_closing(
                    terminal_id
                )
                self.tm.terminal_manager.start_auto_reconnect(
                    terminal, terminal_id, session, duration, interval, timeout
                )

        elif action == BannerAction.CLOSE:
            self.hide_error_banner_for_terminal(terminal)
            info = self.tm.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else session
            self.tm.terminal_manager._cleanup_terminal_ui(
                terminal, terminal_id, 1, identifier
            )

        elif action == BannerAction.EDIT_SESSION:
            if session:
                self.hide_error_banner_for_terminal(terminal)
                self._open_session_edit_dialog(session, terminal, terminal_id)

        elif action == BannerAction.FIX_HOST_KEY and session:
            self.hide_error_banner_for_terminal(terminal)
            self._fix_host_key_and_retry(session, terminal, terminal_id)

    def _open_session_edit_dialog(
        self,
        session: SessionItem,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> None:
        """Open the session edit dialog for fixing credentials."""
        from ..ui.dialogs import SessionEditDialog

        parent_window = self.tm.terminal_manager.parent_window
        if not parent_window:
            self.logger.warning("Cannot open session edit dialog - no parent window")
            return

        session_store = parent_window.session_store
        position = -1
        for i, s in enumerate(session_store):
            if s.name == session.name:
                position = i
                break

        def on_dialog_closed(dialog):
            self.logger.info(
                f"Session edit dialog closed, retrying connection for {session.name}"
            )
            GLib.idle_add(
                self.tm.terminal_manager._retry_ssh_in_same_terminal,
                terminal,
                terminal_id,
                session,
                30,
            )

        dialog = SessionEditDialog(
            parent_window,
            session,
            session_store,
            position,
            parent_window.folder_store,
            settings_manager=parent_window.settings_manager,
        )
        dialog.connect("close-request", lambda d: on_dialog_closed(d) or False)
        dialog.present()

    def _fix_host_key_and_retry(
        self,
        session: SessionItem,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> None:
        """Fix SSH host key verification error and retry connection."""
        host = session.host
        port = session.port or 22

        try:
            subprocess.run(
                ["ssh-keygen", "-R", host],
                capture_output=True,
                timeout=5,
            )

            if port != 22:
                subprocess.run(
                    ["ssh-keygen", "-R", f"[{host}]:{port}"],
                    capture_output=True,
                    timeout=5,
                )

            terminal.feed(
                f"\r\n\x1b[32m[Host Key] Removed old key for {host}\x1b[0m\r\n".encode(
                    "utf-8"
                )
            )
            terminal.feed(b"\x1b[33m[Host Key] Reconnecting...\x1b[0m\r\n")

            self.logger.info(f"Removed host key for {host} and retrying connection")

            GLib.idle_add(
                self.tm.terminal_manager._retry_ssh_in_same_terminal,
                terminal,
                terminal_id,
                session,
                30,
            )

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout removing host key for {host}")
            terminal.feed(b"\r\n\x1b[31m[Host Key] Timeout removing key\x1b[0m\r\n")
        except Exception as e:
            self.logger.error(f"Failed to remove host key for {host}: {e}")
            terminal.feed(
                f"\r\n\x1b[31m[Host Key] Failed: {e}\x1b[0m\r\n".encode("utf-8")
            )

    # -- internal helpers -----------------------------------------------------

    def _search_paned_children(
        self, widget, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Searches for terminal in Paned widget children."""
        if not isinstance(widget, Gtk.Paned):
            return None

        for child_getter in [widget.get_start_child, widget.get_end_child]:
            child = child_getter()
            if child:
                found = self.find_terminal_pane_recursive(child, terminal_to_find)
                if found:
                    return found
        return None

    def _search_single_child(
        self, widget, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Searches for terminal in single-child container."""
        if not hasattr(widget, "get_child"):
            return None
        child = widget.get_child()
        if child:
            return self.find_terminal_pane_recursive(child, terminal_to_find)
        return None

    def _check_existing_banner(
        self, terminal: Vte.Terminal, terminal_id
    ) -> Optional[bool]:
        """Check if terminal already has a valid banner."""
        existing_banner = getattr(terminal, "_error_banner", None)
        if existing_banner is None:
            return None

        try:
            if existing_banner.get_parent() is not None:
                self.logger.debug(
                    f"Banner already exists and is valid for terminal {terminal_id}"
                )
                return True
        except Exception:
            pass

        terminal._error_banner = None
        terminal._banner_box = None
        return None

    def _insert_banner_in_container(
        self,
        container,
        scrolled_window: Gtk.ScrolledWindow,
        banner_box: Gtk.Box,
        banner,
        terminal: Vte.Terminal,
    ) -> bool:
        """Insert the banner box into the appropriate container type."""
        if isinstance(container, Adw.Bin):
            container.set_child(None)
            banner_box.append(scrolled_window)
            container.set_child(banner_box)
            return True

        if isinstance(container, Adw.ToolbarView):
            container.set_content(None)
            banner_box.append(scrolled_window)
            container.set_content(banner_box)
            return True

        if isinstance(container, Gtk.Box):
            existing_box = getattr(terminal, "_banner_box", None)
            if existing_box is container:
                container.prepend(banner)
                terminal._error_banner = banner
                return True
            container.remove(scrolled_window)
            banner_box.append(scrolled_window)
            container.append(banner_box)
            return True

        self.logger.warning(
            f"Cannot show banner - unsupported container type: {type(container)}"
        )
        return False
