# ashyterm/app.py

import atexit
import os
import time
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .settings.config import (
    APP_ID,
    APP_TITLE,
    APP_VERSION,
    COPYRIGHT,
    DEVELOPER_NAME,
    DEVELOPER_TEAM,
    ISSUE_URL,
    WEBSITE,
)
from .settings.manager import SettingsManager
from .terminal.spawner import cleanup_spawner
from .utils.backup import AutoBackupScheduler, BackupType, get_backup_manager
from .utils.crypto import is_encryption_available
from .utils.exceptions import handle_exception
from .utils.logger import enable_debug_mode, get_logger, log_app_shutdown, log_app_start
from .utils.platform import get_platform_info
from .utils.security import create_security_auditor
from .utils.translation_utils import _

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
        self._auto_backup_scheduler = None
        self._security_auditor = None
        self.platform_info = get_platform_info()
        self._initialized = False
        self._shutting_down = False

        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)
        self.connect("command-line", self._on_command_line)
        atexit.register(self._cleanup_on_exit)

    @property
    def backup_manager(self):
        if self._backup_manager is None:
            try:
                self._backup_manager = get_backup_manager()
            except Exception as e:
                self.logger.error(f"Failed to initialize backup manager on-demand: {e}")
        return self._backup_manager

    @property
    def auto_backup_scheduler(self):
        if self._auto_backup_scheduler is None and self.backup_manager:
            self._auto_backup_scheduler = AutoBackupScheduler(self.backup_manager)
            if self.settings_manager.get("auto_backup_enabled", False):
                self._auto_backup_scheduler.enable()
            else:
                self._auto_backup_scheduler.disable()
        return self._auto_backup_scheduler

    @property
    def security_auditor(self):
        if self._security_auditor is None:
            try:
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
            self.settings_manager = SettingsManager()
            theme = self.settings_manager.get("gtk_theme", "dark")
            style_manager = Adw.StyleManager.get_default()
            if theme == "light":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif theme == "dark":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
            self.logger.info(f"Applied GTK theme: {theme}")

            if self.settings_manager.get("debug_mode", False):
                enable_debug_mode()
                self.logger.info("Debug mode enabled")

            if is_encryption_available():
                self.logger.info(
                    "Secure password storage (Secret Service API) is available."
                )
            else:
                self.logger.warning(
                    "Secure password storage is not available - passwords will not be saved."
                )

            self._initialized = True
            self.logger.info("All essential subsystems initialized successfully")
            return True
        except Exception as e:
            self.logger.critical(f"Subsystem initialization failed: {e}")
            handle_exception(
                e, "application initialization", "ashyterm.app", reraise=True
            )
            return False

    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            self.logger.info("Application startup initiated")
            log_app_start()
            if not self._initialize_subsystems():
                self.logger.critical("Failed to initialize application subsystems")
                self.quit()
                return
            self._setup_actions()
            self._setup_keyboard_shortcuts()
            GLib.idle_add(self._check_startup_backup)
            self.logger.info("Application startup completed successfully")
        except Exception as e:
            self.logger.critical(f"Application startup failed: {e}")
            self._show_startup_error(str(e))
            self.quit()

    def _check_startup_backup(self) -> bool:
        """Check if startup backup should be performed, and run it asynchronously."""
        try:
            if not self.auto_backup_scheduler or not self.backup_manager:
                return False
            if not self.settings_manager.get("auto_backup_enabled", False):
                return False
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE

            backup_files = [
                Path(p)
                for p in [SESSIONS_FILE, SETTINGS_FILE]
                if p and Path(p).exists()
            ]
            if backup_files and self.auto_backup_scheduler.should_backup():
                self.logger.info("Performing startup backup asynchronously")
                self.backup_manager.create_backup_async(
                    backup_files, BackupType.AUTOMATIC, _("Automatic startup backup")
                )
                self.auto_backup_scheduler.last_backup_time = time.time()
        except Exception as e:
            self.logger.warning(f"Startup backup check failed: {e}")
        return False

    def _setup_actions(self) -> None:
        """Set up application-level actions."""
        try:
            actions = [
                ("quit", self._on_quit_action),
                ("preferences", self._on_preferences_action),
                ("about", self._on_about_action),
                ("backup-now", self._on_backup_now_action),
                ("restore-backup", self._on_restore_backup_action),
                ("toggle-debug", self._on_toggle_debug_action),
                ("show-logs", self._on_show_logs_action),
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
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                self.set_accels_for_action("app.toggle-debug", ["<Control><Shift>d"])
                self.set_accels_for_action("app.show-logs", ["<Control><Shift>l"])
            self._update_window_shortcuts()
        except Exception as e:
            self.logger.error(f"Failed to setup keyboard shortcuts: {e}")

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
                "toggle-sidebar",
                "toggle-file-manager",
                "new-window",
                "zoom-in",
                "zoom-out",
                "zoom-reset",
                "split-horizontal",
                "split-vertical",
                "close-pane",
                "focus-pane-up",
                "focus-pane-down",
                "focus-pane-left",
                "focus-pane-right",
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
        """Handle application activation when launched without command-line arguments."""
        if not self.get_windows():
            self.logger.info("No windows found on activation, creating a new one.")
            window = self.create_new_window()
            self._present_window_and_request_focus(window)
        else:
            self._present_window_and_request_focus(self.get_active_window())

    def do_command_line(self, command_line):
        """Handle command line arguments for both initial and subsequent launches."""
        arguments = command_line.get_arguments()
        self.logger.info(f"Processing command line: {arguments}")
        self._process_and_execute_args(arguments)
        self.activate()
        return 0

    def _process_and_execute_args(self, arguments: list):
        """Parse arguments and decide what action to take."""
        working_directory, execute_command, ssh_target, close_after_execute = (
            None,
            None,
            None,
            False,
        )
        i = 1
        while i < len(arguments):
            arg = arguments[i]
            if arg in ["-w", "--working-directory"] and i + 1 < len(arguments):
                working_directory = arguments[i + 1]
                i += 1
            elif arg.startswith("--working-directory="):
                working_directory = arg.split("=", 1)[1]
            elif arg in ["-e", "--execute"] and i + 1 < len(arguments):
                execute_command = arguments[i + 1]
                i += 1
            elif arg.startswith("--execute="):
                execute_command = arg.split("=", 1)[1]
            elif arg == "--close-after-execute":
                close_after_execute = True
            elif arg == "--ssh" and i + 1 < len(arguments):
                ssh_target = arguments[i + 1]
                i += 1
            elif arg.startswith("--ssh="):
                ssh_target = arg.split("=", 1)[1]
            elif not arg.startswith("-") and working_directory is None:
                working_directory = arg
            i += 1

        behavior = self.settings_manager.get("new_instance_behavior", "new_tab")
        active_window = self.get_active_window()

        if behavior == "new_window" or not active_window:
            self.logger.info("Creating a new window for command line arguments.")
            window = self.create_new_window(
                initial_working_directory=working_directory,
                initial_execute_command=execute_command,
                close_after_execute=close_after_execute,
                initial_ssh_target=ssh_target,
            )
            self._present_window_and_request_focus(window)
        else:
            self.logger.info("Reusing existing window for a new tab.")
            self._present_window_and_request_focus(active_window)
            if ssh_target:
                active_window.create_ssh_tab(ssh_target)
            elif execute_command:
                active_window.create_execute_tab(
                    execute_command, working_directory, close_after_execute
                )
            else:
                active_window.create_local_tab(working_directory)

    def _present_window_and_request_focus(self, window: Gtk.Window):
        """Present the window and use a modal dialog hack to request focus if needed."""
        if not window.is_active() and window.tab_manager.get_tab_count() > 1:
            self.logger.info(
                "Window not focused and has multiple tabs, attempting focus hack via modal dialog."
            )
            dialog = Gtk.Dialog(transient_for=window, modal=True)
            dialog.set_default_size(1, 1)
            dialog.present()
            GLib.idle_add(dialog.destroy)
        window.present()

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
            about_dialog = Adw.AboutWindow(
                transient_for=self.get_active_window(),
                modal=True,
                application_name=APP_TITLE,
                application_icon="ashyterm",
                developer_name=DEVELOPER_NAME,
                version=APP_VERSION,
                developers=DEVELOPER_TEAM,
                copyright=COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
                website=WEBSITE,
                issue_url=ISSUE_URL,
                comments=_("A modern terminal emulator with session management"),
            )
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                debug_info = "Platform: Linux\n"
                debug_info += f"Architecture: {self.platform_info.architecture}\n"
                debug_info += f"Shell: {os.environ.get('SHELL', 'N/A')}"
                about_dialog.set_debug_info(debug_info)
            about_dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show about dialog: {e}")

    def _on_backup_now_action(self, _action, _param) -> None:
        """Handle manual backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog(
                    _("Backup Error"), _("Backup system not available")
                )
                return
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE

            backup_files = [
                Path(p)
                for p in [SESSIONS_FILE, SETTINGS_FILE]
                if p and Path(p).exists()
            ]
            if not backup_files:
                self._show_error_dialog(_("Backup Error"), _("No files to backup"))
                return
            backup_id = self.backup_manager.create_backup(
                backup_files, BackupType.MANUAL, _("Manual backup from menu")
            )
            if backup_id:
                self.logger.info(f"Manual backup created: {backup_id}")
                self._show_info_dialog(
                    _("Backup Complete"),
                    _("Backup created successfully: {}").format(backup_id),
                )
            else:
                self._show_error_dialog(_("Backup Error"), _("Failed to create backup"))
        except Exception as e:
            self.logger.error(f"Manual backup failed: {e}")
            self._show_error_dialog(_("Backup Error"), _("Backup failed: {}").format(e))

    def _on_restore_backup_action(self, _action, _param) -> None:
        """Handle restore backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog(
                    _("Restore Error"), _("Backup system not available")
                )
                return
            backups = self.backup_manager.list_backups()
            if not backups:
                self._show_info_dialog(
                    _("No Backups"), _("No backups available to restore")
                )
                return
            self._show_info_dialog(
                _("Backups Available"),
                _(
                    "{} backup(s) available. Use preferences dialog for detailed restore options."
                ).format(len(backups)),
            )
        except Exception as e:
            self.logger.error(f"Restore backup action failed: {e}")
            self._show_error_dialog(
                _("Restore Error"), _("Failed to access backups: {}").format(e)
            )

    def _on_toggle_debug_action(self, _action, _param) -> None:
        """Handle toggle debug mode action."""
        try:
            if not self.settings_manager:
                return
            new_debug = not self.settings_manager.get("debug_mode", False)
            self.settings_manager.set("debug_mode", new_debug)
            if new_debug:
                enable_debug_mode()
                self.logger.info("Debug mode enabled")
            else:
                from .utils.logger import disable_debug_mode

                disable_debug_mode()
                self.logger.info("Debug mode disabled")
            self._setup_keyboard_shortcuts()
        except Exception as e:
            self.logger.error(f"Failed to toggle debug mode: {e}")

    def _on_show_logs_action(self, _action, _param) -> None:
        """Handle show logs action."""
        try:
            from .utils.logger import get_log_info

            log_info = get_log_info()
            log_dir = log_info.get("log_dir", "Unknown")
            self._show_info_dialog(
                _("Log Information"), _("Logs are stored in: {}").format(log_dir)
            )
        except Exception as e:
            self.logger.error(f"Failed to show log info: {e}")

    def _has_active_ssh_sessions(self) -> bool:
        """Check if there are active SSH sessions across all windows."""
        try:
            for window in self.get_windows():
                if (
                    hasattr(window, "get_terminal_manager")
                    and (tm := window.get_terminal_manager())
                    and hasattr(tm, "has_active_ssh_sessions")
                ):
                    if tm.has_active_ssh_sessions():
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
            dialog = Adw.MessageDialog(
                transient_for=active_window,
                title=_("Close Application"),
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
                    dlg.close()
                except Exception as e:
                    self.logger.error(f"SSH close confirmation response failed: {e}")
                    dlg.close()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"SSH close confirmation dialog failed: {e}")
            self.quit()

    def _on_shutdown(self, app) -> None:
        """Handle application shutdown."""
        self.logger.info("Application shutdown initiated")
        cleanup_spawner()
        self._shutdown_gracefully()

    def _shutdown_gracefully(self) -> None:
        """Perform a graceful shutdown."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.logger.info("Performing graceful shutdown")
        try:
            self._perform_shutdown_backup()
            if self._main_window:
                self._main_window.destroy()
                self._main_window = None
            if self.settings_manager:
                self.settings_manager.save_settings()
            if self.auto_backup_scheduler:
                self.auto_backup_scheduler.disable()
            log_app_shutdown()
            self.logger.info("Graceful shutdown completed")
        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")

    def _perform_shutdown_backup(self) -> None:
        """Perform backup on shutdown if configured."""
        try:
            if not self.settings_manager:
                return
            if (
                not self.settings_manager.get("backup_on_exit", False)
                or not self.backup_manager
            ):
                return
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE

            backup_files = [
                Path(p)
                for p in [SESSIONS_FILE, SETTINGS_FILE]
                if p and Path(p).exists()
            ]
            if backup_files:
                self.logger.info("Performing shutdown backup")
                self.backup_manager.create_backup_async(
                    backup_files, BackupType.AUTOMATIC, _("Automatic backup on exit")
                )
        except Exception as e:
            self.logger.warning(f"Shutdown backup failed: {e}")

    def _cleanup_on_exit(self) -> None:
        """Cleanup function called on exit."""
        if not self._shutting_down:
            self.logger.info("Emergency cleanup on exit")
            self._shutdown_gracefully()

    def _show_startup_error(self, error_message: str) -> None:
        """Show startup error dialog."""
        try:
            dialog = Gtk.MessageDialog(
                text=_("Startup Error"),
                secondary_text=_("Application failed to start: {}").format(
                    error_message
                ),
            )
            dialog.add_button("_OK", Gtk.ResponseType.OK)
            dialog.run()
            dialog.destroy()
        except Exception:
            print(f"STARTUP ERROR: {error_message}")

    def _show_error_dialog(self, title: str, message: str) -> None:
        """Show error dialog to user."""
        try:
            parent = self.get_active_window()
            dialog = Adw.MessageDialog(transient_for=parent, title=title, body=message)
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")

    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show info dialog to user."""
        try:
            parent = self.get_active_window()
            dialog = Adw.MessageDialog(transient_for=parent, title=title, body=message)
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show info dialog: {e}")

    def get_settings_manager(self) -> Optional[SettingsManager]:
        return self.settings_manager

    def refresh_keyboard_shortcuts(self) -> None:
        """Refresh keyboard shortcuts after settings change."""
        try:
            self._update_window_shortcuts()
        except Exception as e:
            self.logger.error(f"Failed to refresh keyboard shortcuts: {e}")

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

    def get_main_window(self) -> Optional["CommTerminalWindow"]:
        return self._main_window

    def create_new_window(self, **kwargs) -> "CommTerminalWindow":
        """Create a new main window, passing through any initial arguments."""
        try:
            from .window import CommTerminalWindow

            init_args = {
                "initial_working_directory": kwargs.get("initial_working_directory"),
                "initial_execute_command": kwargs.get("initial_execute_command"),
                "close_after_execute": kwargs.get("close_after_execute", False),
                "initial_ssh_target": kwargs.get("initial_ssh_target"),
            }
            window = CommTerminalWindow(
                application=self, settings_manager=self.settings_manager, **init_args
            )
            self.add_window(window)
            self.logger.info(f"New window created with args: {init_args}")
            return window
        except Exception as e:
            self.logger.error(f"Failed to create new window: {e}")
            raise

    def get_backup_manager(self):
        return self.backup_manager

    def get_security_auditor(self):
        return self.security_auditor

    def is_initialized(self) -> bool:
        return self._initialized
