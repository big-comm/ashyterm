import atexit
from typing import Optional, TYPE_CHECKING
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib

# Import translation utility
from .utils.translation_utils import _

from .settings.config import (
    APP_ID, APP_TITLE, APP_VERSION, DEVELOPER_NAME, DEVELOPER_TEAM, 
    COPYRIGHT, WEBSITE, ISSUE_URL
)
from .settings.manager import SettingsManager
from .terminal.spawner import cleanup_spawner

# Import new utility systems
from .utils.logger import get_logger, log_app_start, log_app_shutdown, enable_debug_mode
from .utils.exceptions import VTENotAvailableError, handle_exception
from .utils.platform import get_platform_info
from .utils.backup import get_backup_manager, AutoBackupScheduler, BackupType
from pathlib import Path
from .utils.crypto import is_encryption_available, initialize_encryption
from .utils.security import create_security_auditor

if TYPE_CHECKING:
    from .window import CommTerminalWindow


class CommTerminalApp(Adw.Application):
    """Main application class for Ashy Terminal with enhanced functionality."""
    
    def __init__(self):
        """Initialize the application with comprehensive setup."""
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        GLib.set_prgname(APP_ID)
        self.logger = get_logger('ashyterm.app')
        self.logger.info(_("Initializing {} v{}").format(APP_TITLE, APP_VERSION))

        self.settings_manager: Optional[SettingsManager] = None
        self._main_window: Optional["CommTerminalWindow"] = None
        self.backup_manager = None
        self.auto_backup_scheduler = None
        self.security_auditor = None

        self.platform_info = get_platform_info()
        self.logger.info(_("Running on {} platform").format(self.platform_info.platform_type.value))

        self._initialized = False
        self._shutting_down = False

        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

        atexit.register(self._cleanup_on_exit)

    def _initialize_subsystems(self) -> bool:
        """
        Initialize all application subsystems.
        
        Returns:
            True if initialization successful
        """
        try:
            self.logger.info(_("Initializing application subsystems"))
            
            self.settings_manager = SettingsManager()
            self.logger.debug(_("Settings manager initialized"))
            
            if self.settings_manager.get("debug_mode", False):
                enable_debug_mode()
                self.logger.info(_("Debug mode enabled"))
            
            if is_encryption_available():
                try:
                    if not initialize_encryption():
                        self.logger.info(_("Encryption available but not initialized (may require passphrase)"))
                    else:
                        self.logger.info(_("Encryption system initialized"))
                except Exception as e:
                    self.logger.warning(_("Encryption initialization failed: {}").format(e))
            else:
                self.logger.warning(_("Encryption not available - passwords will be stored as plain text"))
            
            try:
                self.backup_manager = get_backup_manager()
                self.auto_backup_scheduler = AutoBackupScheduler(self.backup_manager)
                
                auto_backup_enabled = self.settings_manager.get("auto_backup_enabled", False)
                if auto_backup_enabled:
                    self.auto_backup_scheduler.enable()
                    self.logger.info(_("Automatic backups enabled"))
                else:
                    self.auto_backup_scheduler.disable()
                
                self.logger.debug(_("Backup system initialized"))
                
            except Exception as e:
                self.logger.error(_("Failed to initialize backup system: {}").format(e))
                self.backup_manager = None
                self.auto_backup_scheduler = None
            
            try:
                self.security_auditor = create_security_auditor()
                self.logger.debug(_("Security auditor initialized"))
            except Exception as e:
                self.logger.warning(_("Security auditor initialization failed: {}").format(e))
            
            from .settings.config import VTE_AVAILABLE
            if not VTE_AVAILABLE:
                self.logger.critical(_("VTE library not available"))
                raise VTENotAvailableError()
            
            self._initialized = True
            self.logger.info(_("All subsystems initialized successfully"))
            return True
            
        except Exception as e:
            self.logger.critical(_("Subsystem initialization failed: {}").format(e))
            handle_exception(e, _("application initialization"), "ashyterm.app", reraise=True)
            return False
    
    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            self.logger.info(_("Application startup initiated"))
            log_app_start()
            
            if not self._initialize_subsystems():
                self.logger.critical(_("Failed to initialize application subsystems"))
                self.quit()
                return
            
            self._setup_actions()
            self._setup_keyboard_shortcuts()
            
            self._check_startup_backup()
            
            self.logger.info(_("Application startup completed successfully"))
            
        except Exception as e:
            self.logger.critical(_("Application startup failed: {}").format(e))
            self._show_startup_error(str(e))
            self.quit()
    
    def _check_startup_backup(self) -> None:
        """Check if startup backup should be performed, and run it asynchronously."""
        try:
            if not self.auto_backup_scheduler or not self.backup_manager:
                return

            if not self.settings_manager.get("auto_backup_enabled", False):
                self.logger.debug(_("Automatic startup backup is disabled in settings."))
                return
            
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if backup_files and self.auto_backup_scheduler.should_backup():
                self.logger.info(_("Performing startup backup asynchronously"))
                
                self.backup_manager.create_backup_async(
                    backup_files,
                    BackupType.AUTOMATIC,
                    _("Automatic startup backup")
                )
                
                self.auto_backup_scheduler.last_backup_time = time.time()
                
        except Exception as e:
            self.logger.warning(_("Startup backup check failed: {}").format(e))
    
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
            
            self.logger.debug(_("Application actions configured"))
            
        except Exception as e:
            self.logger.error(_("Failed to setup actions: {}").format(e))
    
    def _setup_keyboard_shortcuts(self) -> None:
        """Set up application-level keyboard shortcuts."""
        try:
            self.set_accels_for_action("app.quit", ["<Control>q"])
            self.set_accels_for_action("app.preferences", ["<Control>comma"])
            
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                self.set_accels_for_action("app.toggle-debug", ["<Control><Shift>d"])
                self.set_accels_for_action("app.show-logs", ["<Control><Shift>l"])
            
            self._update_window_shortcuts()
            
            self.logger.debug("Keyboard shortcuts configured")
            
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
                "new-window",
                "zoom-in",
                "zoom-out",
                "zoom-reset",
                "split-horizontal",
                "split-vertical",
                "close-pane"
            ]
            
            for action_name in shortcut_actions:
                shortcut = self.settings_manager.get_shortcut(action_name)
                accels = [shortcut] if shortcut else []

                if action_name == "zoom-in":
                    accels.append("<Control>equal")

                self.set_accels_for_action(f"win.{action_name}", accels)
            
            self.logger.debug("Window shortcuts updated")
            
        except Exception as e:
            self.logger.error(f"Failed to update window shortcuts: {e}")
    
    def _on_activate(self, app) -> None:
        """Handle application activation."""
        try:
            self.logger.debug(_("Application activation requested"))
            
            window = self.get_active_window()
            if not window:
                self.logger.info(_("Creating main window"))
                from .window import CommTerminalWindow
                window = CommTerminalWindow(application=self, settings_manager=self.settings_manager)
                self._main_window = window
            
            window.present()
            
            self._update_window_shortcuts()
            
            self.logger.debug(_("Application activation completed"))
            
        except Exception as e:
            self.logger.error(_("Application activation failed: {}").format(e))
            self._show_error_dialog(_("Activation Error"), _("Failed to activate application: {}").format(e))
    
    def _on_quit_action(self, action, param) -> None:
        """Handle quit action with SSH session confirmation."""
        try:
            if self._has_active_ssh_sessions():
                self._show_ssh_close_confirmation()
            else:
                self.logger.info(_("Quit action triggered - no SSH sessions"))
                self.quit()
        except Exception as e:
            self.logger.error(_("Quit action failed: {}").format(e))
            self.quit()
    
    def _on_preferences_action(self, action, param) -> None:
        """Handle preferences action."""
        try:
            window = self.get_active_window()
            if not window:
                self._on_activate(self)
                window = self.get_active_window()
            
            if not window.get_visible():
                window.present()
            
            if window and hasattr(window, 'activate_action'):
                window.activate_action("preferences", None)
                
        except Exception as e:
            self.logger.error(_("Failed to open preferences: {}").format(e))
            self._show_error_dialog(_("Preferences Error"), _("Failed to open preferences: {}").format(e))
    
    def _on_about_action(self, action, param) -> None:
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
                comments=_("A modern terminal emulator with session management")
            )
            
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                debug_info = _("Platform: {}\n").format(self.platform_info.platform_type.value)
                debug_info += _("Architecture: {}\n").format(self.platform_info.architecture)
                debug_info += _("Shell: {}").format(self.platform_info.default_shell)
                about_dialog.set_debug_info(debug_info)
            
            about_dialog.present()
            
        except Exception as e:
            self.logger.error(_("Failed to show about dialog: {}").format(e))
    
    def _on_backup_now_action(self, action, param) -> None:
        """Handle manual backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog(_("Backup Error"), _("Backup system not available"))
                return
            
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if not backup_files:
                self._show_error_dialog(_("Backup Error"), _("No files to backup"))
                return
            
            backup_id = self.backup_manager.create_backup(
                backup_files,
                BackupType.MANUAL,
                _("Manual backup from menu")
            )
            
            if backup_id:
                self.logger.info(_("Manual backup created: {}").format(backup_id))
                self._show_info_dialog(_("Backup Complete"), _("Backup created successfully: {}").format(backup_id))
            else:
                self._show_error_dialog(_("Backup Error"), _("Failed to create backup"))
                
        except Exception as e:
            self.logger.error(_("Manual backup failed: {}").format(e))
            self._show_error_dialog(_("Backup Error"), _("Backup failed: {}").format(e))
    
    def _on_restore_backup_action(self, action, param) -> None:
        """Handle restore backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog(_("Restore Error"), _("Backup system not available"))
                return
            
            backups = self.backup_manager.list_backups()
            if not backups:
                self._show_info_dialog(_("No Backups"), _("No backups available to restore"))
                return
            
            backup_count = len(backups)
            self._show_info_dialog(_("Backups Available"), 
                                 _("{} backup(s) available. Use preferences dialog for detailed restore options.").format(backup_count))
            
        except Exception as e:
            self.logger.error(_("Restore backup action failed: {}").format(e))
            self._show_error_dialog(_("Restore Error"), _("Failed to access backups: {}").format(e))
    
    def _on_toggle_debug_action(self, action, param) -> None:
        """Handle toggle debug mode action."""
        try:
            if not self.settings_manager:
                return
            
            current_debug = self.settings_manager.get("debug_mode", False)
            new_debug = not current_debug
            
            self.settings_manager.set("debug_mode", new_debug)
            
            if new_debug:
                enable_debug_mode()
                self.logger.info(_("Debug mode enabled"))
            else:
                from .utils.logger import disable_debug_mode
                disable_debug_mode()
                self.logger.info(_("Debug mode disabled"))
            
            self._setup_keyboard_shortcuts()
            
        except Exception as e:
            self.logger.error(_("Failed to toggle debug mode: {}").format(e))
    
    def _on_show_logs_action(self, action, param) -> None:
        """Handle show logs action."""
        try:
            from .utils.logger import get_log_info
            log_info = get_log_info()
            
            log_dir = log_info.get('log_dir', 'Unknown')
            self._show_info_dialog(_("Log Information"), _("Logs are stored in: {}").format(log_dir))
            
        except Exception as e:
            self.logger.error(_("Failed to show log info: {}").format(e))
            
    def _has_active_ssh_sessions(self) -> bool:
        """Check if there are active SSH sessions across all windows."""
        try:
            for window in self.get_windows():
                if hasattr(window, 'get_terminal_manager'):
                    terminal_manager = window.get_terminal_manager()
                    if hasattr(terminal_manager, 'has_active_ssh_sessions'):
                        if terminal_manager.has_active_ssh_sessions():
                            return True
            return False
        except Exception as e:
            self.logger.error(_("Failed to check SSH sessions: {}").format(e))
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
                body=_("There are active SSH connections. Closing will disconnect all sessions.\n\nAre you sure you want to close the application?")
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close All"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "close":
                        self.logger.info(_("User confirmed quit with active SSH sessions"))
                        self.quit()
                    else:
                        self.logger.debug(_("User cancelled quit"))
                    dlg.close()
                except Exception as e:
                    self.logger.error(_("SSH close confirmation response failed: {}").format(e))
                    dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(_("SSH close confirmation dialog failed: {}").format(e))
            self.quit()
    
    def _on_shutdown(self, app) -> None:
        """Handle application shutdown."""
        self.logger.info(_("Application shutdown initiated"))
        cleanup_spawner()
        self._shutdown_gracefully()
    
    def _shutdown_gracefully(self) -> None:
        """Perform a graceful shutdown."""
        if self._shutting_down:
            return
        
        self._shutting_down = True
        self.logger.info(_("Performing graceful shutdown"))
        
        try:
            self._perform_shutdown_backup()
            
            if self._main_window:
                self.logger.debug(_("Cleaning up main window"))
                self._main_window.destroy()
                self._main_window = None
            
            if self.settings_manager:
                self.logger.debug(_("Saving settings"))
                self.settings_manager.save_settings()
            
            if self.auto_backup_scheduler:
                self.auto_backup_scheduler.disable()
            
            log_app_shutdown()
            self.logger.info(_("Graceful shutdown completed"))
            
        except Exception as e:
            self.logger.error(_("Error during graceful shutdown: {}").format(e))
    
    def _perform_shutdown_backup(self) -> None:
        """Perform backup on shutdown if configured."""
        try:
            if not self.settings_manager:
                return
            
            backup_on_exit = self.settings_manager.get("backup_on_exit", False)
            if not backup_on_exit or not self.backup_manager:
                return
            
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if backup_files:
                self.logger.info(_("Performing shutdown backup"))
                self.backup_manager.create_backup_async(
                    backup_files,
                    BackupType.AUTOMATIC,
                    _("Automatic backup on exit")
                )
                
        except Exception as e:
            self.logger.warning(_("Shutdown backup failed: {}").format(e))
    
    def _cleanup_on_exit(self) -> None:
        """Cleanup function called on exit."""
        if not self._shutting_down:
            self.logger.info(_("Emergency cleanup on exit"))
            self._shutdown_gracefully()
    
    def _show_startup_error(self, error_message: str) -> None:
        """Show startup error dialog."""
        try:
            dialog = Gtk.MessageDialog(
                text=_("Startup Error"),
                secondary_text=_("Application failed to start: {}").format(error_message)
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
            dialog = Adw.MessageDialog(
                transient_for=parent,
                title=title,
                body=message
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(_("Failed to show error dialog: {}").format(e))
    
    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show info dialog to user."""
        try:
            parent = self.get_active_window()
            dialog = Adw.MessageDialog(
                transient_for=parent,
                title=title,
                body=message
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception as e:
            self.logger.error(_("Failed to show info dialog: {}").format(e))
    
    def get_settings_manager(self) -> Optional[SettingsManager]:
        """Get the settings manager instance."""
        return self.settings_manager
    
    def refresh_keyboard_shortcuts(self) -> None:
        """Refresh keyboard shortcuts after settings change."""
        try:
            self._update_window_shortcuts()
        except Exception as e:
            self.logger.error(_("Failed to refresh keyboard shortcuts: {}").format(e))
    
    def do_window_added(self, window) -> None:
        """Handle window being added to application."""
        Adw.Application.do_window_added(self, window)
        
        if hasattr(window, 'is_main_window') and window.is_main_window:
            self._main_window = window
            self.logger.debug(_("Main window registered"))
    
    def do_window_removed(self, window) -> None:
        """Handle window being removed from application."""
        Adw.Application.do_window_removed(self, window)
        
        if window == self._main_window:
            self._main_window = None
            self.logger.debug(_("Main window unregistered"))
    
    def get_main_window(self) -> Optional["CommTerminalWindow"]:
        """Get the main window instance."""
        return self._main_window
    
    def create_new_window(self) -> "CommTerminalWindow":
        """
        Create a new main window.
        
        Returns:
            New CommTerminalWindow instance
        """
        try:
            from .window import CommTerminalWindow
            window = CommTerminalWindow(application=self, settings_manager=self.settings_manager)
            self.add_window(window)
            
            self.logger.info(_("New window created successfully"))
            return window
            
        except Exception as e:
            self.logger.error(_("Failed to create new window: {}").format(e))
            raise
    
    def get_backup_manager(self):
        """Get the backup manager instance."""
        return self.backup_manager
    
    def get_security_auditor(self):
        """Get the security auditor instance."""
        return self.security_auditor
    
    def is_initialized(self) -> bool:
        """Check if application is fully initialized."""
        return self._initialized