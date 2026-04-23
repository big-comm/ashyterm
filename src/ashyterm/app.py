# ashyterm/app.py

import atexit
import os
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from .settings.config import (
    APP_ID,
    APP_TITLE,
    APP_VERSION,
)
from .settings.manager import SettingsManager, get_settings_manager
from .utils.logger import get_logger
from .utils.translation_utils import _
from .cli_parser import CliArgParser

if TYPE_CHECKING:
    from .window import CommTerminalWindow


class CommTerminalApp(Adw.Application):
    """Main application class for Ashy Terminal."""

    def __init__(self):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        GLib.set_prgname(APP_ID)
        self.logger = get_logger("ashyterm.app")
        self.logger.info(f"Initializing {APP_TITLE} v{APP_VERSION}")

        self.settings_manager: Optional[SettingsManager] = None
        self._main_window: Optional["CommTerminalWindow"] = None
        self._backup_manager = None
        self._security_auditor = None
        self._platform_info = None
        self._initialized = False
        self._shutting_down = False
        self._arg_parser = CliArgParser(self)

        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)
        self.connect("command-line", self._on_command_line)
        atexit.register(self._cleanup_on_exit)

    @property
    def platform_info(self):
        if self._platform_info is None:
            from .utils.platform import get_platform_info

            self._platform_info = get_platform_info()
        return self._platform_info

    @property
    def backup_manager(self):
        if self._backup_manager is None:
            try:
                from .utils.backup import get_backup_manager

                self._backup_manager = get_backup_manager()
            except Exception as e:
                self.logger.error(f"Failed to initialize backup manager on-demand: {e}")
        return self._backup_manager

    @property
    def security_auditor(self):
        if self._security_auditor is None:
            try:
                from .utils.security import create_security_auditor

                self._security_auditor = create_security_auditor()
            except Exception as e:
                self.logger.warning(
                    f"Security auditor initialization on-demand failed: {e}"
                )
        return self._security_auditor

    def _initialize_subsystems(self) -> bool:
        """Initialize all application subsystems."""
        try:
            self.logger.info("Initializing application subsystems")
            self.settings_manager = get_settings_manager()
            theme = self.settings_manager.get("gtk_theme", "dark")
            style_manager = Adw.StyleManager.get_default()

            if theme == "light":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif theme == "dark" or theme == "terminal":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:  # "default"
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

            self.logger.info(f"Applied initial GTK theme: {theme}")

            if self.settings_manager.get("debug_mode", False):
                from .utils.logger import enable_debug_mode
                enable_debug_mode()
                self.logger.info("Debug mode enabled")

            # Crypto check is informational only; defer so startup isn't blocked.
            GLib.timeout_add(1000, self._log_crypto_status)

            self._initialized = True
            self.logger.info("All essential subsystems initialized successfully")
            return True
        except Exception as e:
            self.logger.critical(f"Subsystem initialization failed: {e}")
            from .utils.exceptions import handle_exception
            handle_exception(
                e, "application initialization", "ashyterm.app", reraise=True
            )
            return False

    def _log_crypto_status(self) -> bool:
        from .utils.crypto import is_encryption_available

        if is_encryption_available():
            self.logger.info(
                "Secure password storage (Secret Service API) is available."
            )
        else:
            self.logger.warning(
                "Secure password storage is not available - passwords will not be saved."
            )
        return False

    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            self.logger.info("Application startup initiated")
            from .utils.logger import log_app_start
            log_app_start()
            if not self._initialize_subsystems():
                self.logger.critical("Failed to initialize application subsystems")
                self.quit()
                return

            # Must run after settings load, before any UI is built.
            self._configure_icon_theme()

            self._setup_actions()
            self._setup_keyboard_shortcuts()
            self.logger.info("Application startup completed successfully")
        except Exception as e:
            self.logger.critical(f"Application startup failed: {e}")
            self._show_startup_error(str(e))
            self.quit()

    def _configure_icon_theme(self) -> None:
        """Apply ``icon_theme_strategy`` (ashy=bundled SVGs vs system theme).

        FileManager MIME icons always follow the system theme regardless.
        """
        try:
            icon_strategy = "ashy"
            if self.settings_manager:
                icon_strategy = self.settings_manager.get("icon_theme_strategy", "ashy")

            from .utils import icons

            icons._use_bundled_icons = icon_strategy == "ashy"

            self.logger.info(
                f"Icon strategy: {'Ashy (bundled SVGs)' if icon_strategy == 'ashy' else 'System icons'}"
            )

        except Exception as e:
            self.logger.warning(f"Failed to configure icon theme: {e}")

    def _setup_actions(self) -> None:
        """Set up application-level actions."""
        try:
            actions = [
                ("quit", self._on_quit_action),
                ("preferences", self._on_preferences_action),
                ("about", self._on_about_action),
                ("backup-now", self._on_backup_now_action),
                ("restore-backup", self._on_restore_backup_action),
            ]
            for action_name, callback in actions:
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", callback)
                self.add_action(action)
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")

    def _setup_keyboard_shortcuts(self) -> None:
        """Set up application-level keyboard shortcuts."""
        try:
            self.set_accels_for_action("app.quit", ["<Control><Shift>q"])
            self.set_accels_for_action("app.preferences", ["<Control><Shift>comma"])
            self.set_accels_for_action("win.command-palette", ["<Control><Shift>p"])
            self._update_window_shortcuts()
            # React to live shortcut changes in Preferences without restart.
            if self.settings_manager is not None:
                self.settings_manager.add_change_listener(
                    self._on_setting_changed_for_shortcuts
                )
        except Exception as e:
            self.logger.error(f"Failed to setup keyboard shortcuts: {e}")

    def _on_setting_changed_for_shortcuts(
        self, key: str, _old_value, _new_value
    ) -> None:
        """Reload window-level accelerators whenever a shortcut setting moves."""
        if key.startswith("shortcuts.") or key == "shortcuts":
            try:
                self._update_window_shortcuts()
            except Exception as e:
                self.logger.debug(f"Shortcut refresh failed after '{key}' change: {e}")

    def _update_window_shortcuts(self) -> None:
        """Update window-level keyboard shortcuts from settings."""
        try:
            if not self.settings_manager:
                return
            shortcut_actions = [
                "new-local-tab",
                "close-tab",
                "copy",
                "paste",
                "select-all",
                "clear-session",
                "toggle-sidebar",
                "toggle-file-manager",
                "toggle-search",
                "show-command-manager",
                "new-window",
                "zoom-in",
                "zoom-out",
                "zoom-reset",
                "split-horizontal",
                "split-vertical",
                "close-pane",
                "next-tab",
                "previous-tab",
                "toggle-broadcast",
                "ai-assistant",
            ]
            for action_name in shortcut_actions:
                shortcut = self.settings_manager.get_shortcut(action_name)
                accels = [shortcut] if shortcut else []
                if action_name == "zoom-in":
                    accels.append("<Control>equal")
                self.set_accels_for_action(f"win.{action_name}", accels)
        except Exception as e:
            self.logger.error(f"Failed to update window shortcuts: {e}")

    def _on_activate(self, app) -> None:
        """Activation entry point. do_command_line already presented a window."""
        if getattr(self, "_window_already_presented", False):
            self._window_already_presented = False
            return

        if not self.get_windows():
            self.logger.info("No windows found on activation, creating a new one.")
            window = self.create_new_window()
            self._arg_parser.present_window_and_request_focus(window)
        else:
            self._arg_parser.present_window_and_request_focus(
                self.get_active_window()
            )

    def do_command_line(self, command_line):
        """Dispatch CLI args (both first launch and subsequent remote calls)."""
        arguments = command_line.get_arguments()
        self.logger.info(f"Processing command line: {arguments}")
        self._arg_parser.process_and_execute_args(arguments)
        # Flag so _on_activate doesn't re-present the window.
        self._window_already_presented = True
        self.activate()
        return 0

    def _on_command_line(self, app, command_line):
        return self.do_command_line(command_line)

    def _on_quit_action(self, _action, _param) -> None:
        """Handle quit action with SSH session confirmation."""
        try:
            if self._has_active_ssh_sessions():
                self._show_ssh_close_confirmation()
            else:
                self.logger.info("Quit action triggered - no SSH sessions")
                self.quit()
        except Exception as e:
            self.logger.error(f"Quit action failed: {e}")
            self.quit()

    def _on_preferences_action(self, _action, _param) -> None:
        """Handle preferences action."""
        try:
            window = self.get_active_window()
            if not window:
                self._on_activate(self)
                window = self.get_active_window()
            if not window.get_visible():
                window.present()
            if window and hasattr(window, "activate_action"):
                window.activate_action("preferences", None)
        except Exception as e:
            self.logger.error(f"Failed to open preferences: {e}")
            self._show_error_dialog(
                _("Preferences Error"), _("Failed to open preferences: {}").format(e)
            )

    def _on_about_action(self, _action, _param) -> None:
        """Handle about action."""
        try:
            from .settings.config import (
                COPYRIGHT, DEVELOPER_NAME, DEVELOPER_TEAM, ISSUE_URL,
            )
            about_dialog = Adw.AboutDialog(
                application_name=APP_TITLE,
                application_icon="ashyterm",
                developer_name=DEVELOPER_NAME,
                version=APP_VERSION,
                developers=DEVELOPER_TEAM,
                copyright=COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
                issue_url=ISSUE_URL,
                comments=_("A modern terminal emulator with session management"),
            )
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                debug_info = "Platform: Linux\n"
                debug_info += f"Architecture: {self.platform_info.architecture}\n"
                debug_info += f"Shell: {os.environ.get('SHELL', 'N/A')}"
                about_dialog.set_debug_info(debug_info)
            about_dialog.present(self.get_active_window())
        except Exception as e:
            self.logger.error(f"Failed to show about dialog: {e}")

    @property
    def backup_handler(self):
        if not hasattr(self, "_backup_handler"):
            from .ui.dialogs.backup_dialog import BackupRestoreHandler

            self._backup_handler = BackupRestoreHandler(self)
        return self._backup_handler

    def _on_backup_now_action(self, _action, _param) -> None:
        """Handles the manual backup creation flow."""
        window = self.get_active_window()
        if not window:
            self.logger.warning("No active window found for backup action")
            return
        self.backup_handler.start_backup_flow(window)

    def _on_restore_backup_action(self, _action, _param) -> None:
        """Handles the restore backup flow."""
        window = self.get_active_window()
        if not window:
            self.logger.warning("No active window found for restore action")
            return
        self.backup_handler.start_restore_flow(window)

    def _has_active_ssh_sessions(self) -> bool:
        """Check if there are active SSH sessions across all windows."""
        try:
            for window in self.get_windows():
                if (
                    hasattr(window, "get_terminal_manager")
                    and (tm := window.get_terminal_manager())
                    and hasattr(tm, "has_active_ssh_sessions")
                    and tm.has_active_ssh_sessions()
                ):
                    return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to check SSH sessions: {e}")
            return False

    def _show_ssh_close_confirmation(self) -> None:
        """Show confirmation dialog for active SSH sessions."""
        try:
            active_window = self.get_active_window()
            if not active_window:
                self.quit()
                return
            dialog = Adw.AlertDialog(
                heading=_("Close Application"),
                body=_(
                    "There are active SSH connections. Closing will disconnect all sessions.\n\nAre you sure you want to close the application?"
                ),
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close All"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")

            def on_response(dlg, response_id):
                try:
                    if response_id == "close":
                        self.logger.info("User confirmed quit with active SSH sessions")
                        self.quit()
                except Exception as e:
                    self.logger.error(f"SSH close confirmation response failed: {e}")

            dialog.connect("response", on_response)
            dialog.present(active_window)
        except Exception as e:
            self.logger.error(f"SSH close confirmation dialog failed: {e}")
            self.quit()

    def _on_shutdown(self, app) -> None:
        self.logger.info("Application shutdown initiated")
        from .core.tasks import AsyncTaskManager
        from .terminal.spawner import cleanup_spawner

        cleanup_spawner()

        try:
            AsyncTaskManager.get().shutdown(wait=False)
        except Exception as e:
            self.logger.error(f"Error shutting down AsyncTaskManager: {e}")

        self._shutdown_gracefully()

    def _shutdown_gracefully(self) -> None:
        """Perform a graceful shutdown."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.logger.info("Performing graceful shutdown")
        try:
            if self._main_window:
                self._main_window.destroy()
                self._main_window = None
            if self.settings_manager:
                self.settings_manager.save_settings()
            from .utils.logger import log_app_shutdown
            log_app_shutdown()
            self.logger.info("Graceful shutdown completed")
        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")

    def _cleanup_on_exit(self) -> None:
        """Cleanup function called on exit."""
        if not self._shutting_down:
            self.logger.info("Emergency cleanup on exit")
            self._shutdown_gracefully()

    def _show_startup_error(self, error_message: str) -> None:
        """Show startup error dialog."""
        try:
            dialog = Adw.AlertDialog(
                heading=_("Startup Error"),
                body=_("Application failed to start: {}").format(error_message),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self.get_active_window())
        except Exception:
            print(f"STARTUP ERROR: {error_message}")

    def _show_error_dialog(self, title: str, message: str, parent=None) -> None:
        """Show error dialog to user."""
        try:
            if parent is None:
                parent = self.get_active_window()
            dialog = Adw.AlertDialog(heading=title, body=message)
            dialog.add_response("ok", _("OK"))
            dialog.present(parent)
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")

    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show info dialog to user."""
        try:
            parent = self.get_active_window()
            dialog = Adw.AlertDialog(heading=title, body=message)
            dialog.add_response("ok", _("OK"))
            dialog.present(parent)
        except Exception as e:
            self.logger.error(f"Failed to show info dialog: {e}")

    def get_settings_manager(self) -> Optional[SettingsManager]:
        return self.settings_manager

    def do_window_added(self, window) -> None:
        """Handle window being added to application."""
        Adw.Application.do_window_added(self, window)
        if hasattr(window, "is_main_window") and window.is_main_window:
            self._main_window = window

    def do_window_removed(self, window) -> None:
        """Handle window being removed from application."""
        Adw.Application.do_window_removed(self, window)
        if window == self._main_window:
            self._main_window = None

    def create_new_window(self, **kwargs) -> "CommTerminalWindow":
        """Create a new main window, passing through any initial arguments."""
        try:
            from .window import CommTerminalWindow

            init_args = {
                "initial_working_directory": kwargs.get("initial_working_directory"),
                "initial_execute_command": kwargs.get("initial_execute_command"),
                "close_after_execute": kwargs.get("close_after_execute", False),
                "initial_ssh_target": kwargs.get("initial_ssh_target"),
                "_is_for_detached_tab": kwargs.get("_is_for_detached_tab", False),
                "_triggered_by_command_line": kwargs.get(
                    "_triggered_by_command_line", False
                ),
                "detached_terminals_data": kwargs.get("detached_terminals_data"),
                "detached_file_manager": kwargs.get("detached_file_manager"),
            }
            if self.settings_manager is None:
                raise RuntimeError(
                    "settings_manager must be initialized before creating a window"
                )
            window = CommTerminalWindow(
                application=self, settings_manager=self.settings_manager, **init_args
            )
            self.add_window(window)
            self.logger.info(f"New window created with args: {init_args}")
            return window
        except Exception as e:
            self.logger.error(f"Failed to create new window: {e}")
            raise
