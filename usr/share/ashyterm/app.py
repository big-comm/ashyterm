import sys
import signal
import atexit
from typing import Optional, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib

from .settings.config import (
    APP_ID, APP_TITLE, APP_VERSION, DEVELOPER_NAME, DEVELOPER_TEAM, 
    COPYRIGHT, WEBSITE, ISSUE_URL
)
from .settings.manager import SettingsManager

# Import new utility systems
from .utils.logger import get_logger, log_app_start, log_app_shutdown, enable_debug_mode
from .utils.exceptions import (
    AshyTerminalError, VTENotAvailableError, ConfigError,
    handle_exception, ErrorCategory, ErrorSeverity
)
from .utils.platform import (
    get_platform_info, is_windows, get_config_directory,
    get_default_shell
)
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
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        
        # Initialize logging first
        self.logger = get_logger('ashyterm.app')
        self.logger.info(f"Initializing {APP_TITLE} v{APP_VERSION}")
        
        # Core components
        self.settings_manager: Optional[SettingsManager] = None
        self._main_window: Optional["CommTerminalWindow"] = None
        self.backup_manager = None
        self.auto_backup_scheduler = None
        self.security_auditor = None
        
        # Platform information
        self.platform_info = get_platform_info()
        self.logger.info(f"Running on {self.platform_info.platform_type.value} platform")
        
        # Application state
        self._initialized = False
        self._shutting_down = False
        
        # Connect application signals
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)
        
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        # Register cleanup functions
        atexit.register(self._cleanup_on_exit)
    
    def _setup_signal_handlers(self) -> None:
        """Set up system signal handlers for graceful shutdown."""
        try:
            # Track signal calls for forced exit
            self._signal_count = 0
            self._shutdown_in_progress = False
            
            def signal_handler(sig, frame):
                self._signal_count += 1
                
                if self._signal_count == 1:
                    self.logger.info(f"Received signal {sig}, initiating graceful shutdown")
                    if not self._shutdown_in_progress:
                        self._shutdown_in_progress = True
                        # Use GLib.idle_add to ensure we're in the main thread
                        from gi.repository import GLib
                        GLib.idle_add(self._shutdown_gracefully)
                elif self._signal_count >= 2:
                    self.logger.warning(f"Received signal {sig} {self._signal_count} times, forcing immediate exit")
                    # Force immediate exit
                    import os
                    os._exit(1)
            
            # Handle SIGTERM and SIGINT
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            
            # On Windows, handle CTRL+C
            if is_windows():
                signal.signal(signal.SIGBREAK, signal_handler)
                
        except Exception as e:
            self.logger.warning(f"Could not set up signal handlers: {e}")
    
    def _initialize_subsystems(self) -> bool:
        """
        Initialize all application subsystems.
        
        Returns:
            True if initialization successful
        """
        try:
            self.logger.info("Initializing application subsystems")
            
            # Initialize settings manager
            self.settings_manager = SettingsManager()
            self.logger.debug("Settings manager initialized")
            
            # Check for debug mode
            if self.settings_manager.get("debug_mode", False):
                enable_debug_mode()
                self.logger.info("Debug mode enabled")
            
            # Initialize encryption if available
            if is_encryption_available():
                try:
                    # Try to initialize without passphrase first (for stored keys)
                    if not initialize_encryption():
                        self.logger.info("Encryption available but not initialized (may require passphrase)")
                    else:
                        self.logger.info("Encryption system initialized")
                except Exception as e:
                    self.logger.warning(f"Encryption initialization failed: {e}")
            else:
                self.logger.warning("Encryption not available - passwords will be stored as plain text")
            
            # Initialize backup system
            try:
                self.backup_manager = get_backup_manager()
                self.auto_backup_scheduler = AutoBackupScheduler(self.backup_manager)
                
                # Enable auto-backup if configured
                auto_backup_enabled = self.settings_manager.get("auto_backup_enabled", True)
                if auto_backup_enabled:
                    self.auto_backup_scheduler.enable()
                    self.logger.info("Automatic backups enabled")
                else:
                    self.auto_backup_scheduler.disable()
                
                self.logger.debug("Backup system initialized")
                
            except Exception as e:
                self.logger.error(f"Failed to initialize backup system: {e}")
                self.backup_manager = None
                self.auto_backup_scheduler = None
            
            # Initialize security auditor
            try:
                self.security_auditor = create_security_auditor()
                self.logger.debug("Security auditor initialized")
            except Exception as e:
                self.logger.warning(f"Security auditor initialization failed: {e}")
            
            # Verify VTE availability
            from .settings.config import VTE_AVAILABLE
            if not VTE_AVAILABLE:
                self.logger.critical("VTE library not available")
                raise VTENotAvailableError()
            
            self._initialized = True
            self.logger.info("All subsystems initialized successfully")
            return True
            
        except Exception as e:
            self.logger.critical(f"Subsystem initialization failed: {e}")
            handle_exception(e, "application initialization", "ashyterm.app", reraise=True)
            return False
    
    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            self.logger.info("Application startup initiated")
            log_app_start()
            
            # Initialize all subsystems
            if not self._initialize_subsystems():
                self.logger.critical("Failed to initialize application subsystems")
                self.quit()
                return
            
            # Set up actions and shortcuts
            self._setup_actions()
            self._setup_keyboard_shortcuts()
            
            # Perform startup backup check
            self._check_startup_backup()
            
            self.logger.info("Application startup completed successfully")
            
        except Exception as e:
            self.logger.critical(f"Application startup failed: {e}")
            self._show_startup_error(str(e))
            self.quit()
    
    def _check_startup_backup(self) -> None:
        """Check if startup backup should be performed."""
        try:
            if not self.auto_backup_scheduler:
                return
            
            # Get important files to backup
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if backup_files and self.auto_backup_scheduler.should_backup():
                self.logger.info("Performing startup backup")
                backup_id = self.auto_backup_scheduler.perform_auto_backup(backup_files)
                if backup_id:
                    self.logger.info(f"Startup backup completed: {backup_id}")
                
        except Exception as e:
            self.logger.warning(f"Startup backup failed: {e}")
    
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
            
            self.logger.debug("Application actions configured")
            
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")
    
    def _setup_keyboard_shortcuts(self) -> None:
        """Set up application-level keyboard shortcuts."""
        try:
            # Application shortcuts
            self.set_accels_for_action("app.quit", ["<Control>q"])
            self.set_accels_for_action("app.preferences", ["<Control>comma"])
            
            # Debug shortcuts (only if debug mode enabled)
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                self.set_accels_for_action("app.toggle-debug", ["<Control><Shift>d"])
                self.set_accels_for_action("app.show-logs", ["<Control><Shift>l"])
            
            # Window shortcuts will be set when window is created
            self._update_window_shortcuts()
            
            self.logger.debug("Keyboard shortcuts configured")
            
        except Exception as e:
            self.logger.error(f"Failed to setup keyboard shortcuts: {e}")
    
    def _update_window_shortcuts(self) -> None:
        """Update window-level keyboard shortcuts from settings."""
        try:
            if not self.settings_manager:
                return
            
            shortcut_actions = ["new-local-tab", "close-tab", "copy", "paste"]
            
            for action_name in shortcut_actions:
                shortcut = self.settings_manager.get_shortcut(action_name)
                if shortcut:
                    self.set_accels_for_action(f"win.{action_name}", [shortcut])
                else:
                    self.set_accels_for_action(f"win.{action_name}", [])
            
            self.logger.debug("Window shortcuts updated")
            
        except Exception as e:
            self.logger.error(f"Failed to update window shortcuts: {e}")
    
    def _on_activate(self, app) -> None:
        """Handle application activation."""
        try:
            self.logger.debug("Application activation requested")
            
            # Get or create main window
            window = self.get_active_window()
            if not window:
                self.logger.info("Creating main window")
                # Import here to avoid circular imports
                from .window import CommTerminalWindow
                window = CommTerminalWindow(application=self, settings_manager=self.settings_manager)
                self._main_window = window
            
            # Present the window
            window.present()
            
            # Update keyboard shortcuts
            self._update_window_shortcuts()
            
            self.logger.debug("Application activation completed")
            
        except Exception as e:
            self.logger.error(f"Application activation failed: {e}")
            self._show_error_dialog("Activation Error", f"Failed to activate application: {e}")
    
    def _on_quit_action(self, action, param) -> None:
        """Handle quit action."""
        self.logger.info("Quit action triggered")
        self._shutdown_gracefully()
    
    def _on_preferences_action(self, action, param) -> None:
        """Handle preferences action."""
        try:
            window = self.get_active_window()
            if not window:
                # Create window if none exists
                self._on_activate(self)
                window = self.get_active_window()
            
            if not window.get_visible():
                window.present()
            
            # Activate preferences action on window
            if window and hasattr(window, 'activate_action'):
                window.activate_action("preferences", None)
                
        except Exception as e:
            self.logger.error(f"Failed to open preferences: {e}")
            self._show_error_dialog("Preferences Error", f"Failed to open preferences: {e}")
    
    def _on_about_action(self, action, param) -> None:
        """Handle about action."""
        try:
            about_dialog = Adw.AboutWindow(
                transient_for=self.get_active_window(),
                modal=True,
                application_name=APP_TITLE,
                application_icon=APP_ID,
                developer_name=DEVELOPER_NAME,
                version=APP_VERSION,
                developers=DEVELOPER_TEAM,
                copyright=COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
                website=WEBSITE,
                issue_url=ISSUE_URL,
                comments="A modern terminal emulator with session management"
            )
            
            # Add platform information in debug mode
            if self.settings_manager and self.settings_manager.get("debug_mode", False):
                debug_info = f"Platform: {self.platform_info.platform_type.value}\n"
                debug_info += f"Architecture: {self.platform_info.architecture}\n"
                debug_info += f"Shell: {self.platform_info.default_shell}"
                about_dialog.set_debug_info(debug_info)
            
            about_dialog.present()
            
        except Exception as e:
            self.logger.error(f"Failed to show about dialog: {e}")
    
    def _on_backup_now_action(self, action, param) -> None:
        """Handle manual backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog("Backup Error", "Backup system not available")
                return
            
            # Get files to backup
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if not backup_files:
                self._show_error_dialog("Backup Error", "No files to backup")
                return
            
            # Create backup
            backup_id = self.backup_manager.create_backup(
                backup_files,
                BackupType.MANUAL,
                "Manual backup from menu"
            )
            
            if backup_id:
                self.logger.info(f"Manual backup created: {backup_id}")
                self._show_info_dialog("Backup Complete", f"Backup created successfully: {backup_id}")
            else:
                self._show_error_dialog("Backup Error", "Failed to create backup")
                
        except Exception as e:
            self.logger.error(f"Manual backup failed: {e}")
            self._show_error_dialog("Backup Error", f"Backup failed: {e}")
    
    def _on_restore_backup_action(self, action, param) -> None:
        """Handle restore backup action."""
        try:
            if not self.backup_manager:
                self._show_error_dialog("Restore Error", "Backup system not available")
                return
            
            # Show backup selection dialog (simplified for now)
            backups = self.backup_manager.list_backups()
            if not backups:
                self._show_info_dialog("No Backups", "No backups available to restore")
                return
            
            # For now, just show the count - a proper dialog would be implemented in UI
            backup_count = len(backups)
            self._show_info_dialog("Backups Available", 
                                 f"{backup_count} backup(s) available. Use preferences dialog for detailed restore options.")
            
        except Exception as e:
            self.logger.error(f"Restore backup action failed: {e}")
            self._show_error_dialog("Restore Error", f"Failed to access backups: {e}")
    
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
                self.logger.info("Debug mode enabled")
            else:
                from .utils.logger import disable_debug_mode
                disable_debug_mode()
                self.logger.info("Debug mode disabled")
            
            # Update keyboard shortcuts
            self._setup_keyboard_shortcuts()
            
        except Exception as e:
            self.logger.error(f"Failed to toggle debug mode: {e}")
    
    def _on_show_logs_action(self, action, param) -> None:
        """Handle show logs action."""
        try:
            from .utils.logger import get_log_info
            log_info = get_log_info()
            
            log_dir = log_info.get('log_dir', 'Unknown')
            self._show_info_dialog("Log Information", f"Logs are stored in: {log_dir}")
            
        except Exception as e:
            self.logger.error(f"Failed to show log info: {e}")
    
    def _on_shutdown(self, app) -> None:
        """Handle application shutdown."""
        self.logger.info("Application shutdown initiated")
        self._shutdown_gracefully()
    
    def _shutdown_gracefully(self) -> None:
        """Perform immediate shutdown without hanging."""
        if self._shutting_down:
            return
        
        self._shutting_down = True
        self.logger.info("IMMEDIATE SHUTDOWN - no cleanup")
        
        try:
            # Force exit immediately - don't wait for anything
            import os
            self.logger.info("Forcing immediate exit")
            os._exit(0)  # IMMEDIATE EXIT
            
        except Exception:
            # Even if logging fails, force exit
            import os
            os._exit(0)
        
        try:
            # Perform final backup if enabled
            self._perform_shutdown_backup()
            
            # Clean up main window
            if self._main_window:
                self.logger.debug("Cleaning up main window")
                self._main_window.destroy()
                self._main_window = None
            
            # Save settings
            if self.settings_manager:
                self.logger.debug("Saving settings")
                self.settings_manager.save_settings()
            
            # Clean up backup system
            if self.auto_backup_scheduler:
                self.auto_backup_scheduler.disable()
            
            log_app_shutdown()
            self.logger.info("Graceful shutdown completed")
            
        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")
        finally:
            self.quit()
    
    def _perform_shutdown_backup(self) -> None:
        """Perform backup on shutdown if configured."""
        try:
            # Skip backup on shutdown to prevent hanging
            self.logger.debug("Skipping shutdown backup to prevent hanging")
            return
            
            if not self.settings_manager:
                return
            
            backup_on_exit = self.settings_manager.get("backup_on_exit", False)
            if not backup_on_exit or not self.backup_manager:
                return
            
            # Get files to backup
            from .settings.config import SESSIONS_FILE, SETTINGS_FILE
            backup_files = []
            
            for file_path in [SESSIONS_FILE, SETTINGS_FILE]:
                if file_path and Path(file_path).exists():
                    backup_files.append(Path(file_path))
            
            if backup_files:
                self.logger.info("Performing shutdown backup")
                backup_id = self.backup_manager.create_backup(
                    backup_files,
                    BackupType.AUTOMATIC,
                    "Automatic backup on exit"
                )
                if backup_id:
                    self.logger.info(f"Shutdown backup completed: {backup_id}")
                
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
            # Use GTK directly since Adw might not be initialized
            dialog = Gtk.MessageDialog(
                text="Startup Error",
                secondary_text=f"Application failed to start: {error_message}"
            )
            dialog.add_button("_OK", Gtk.ResponseType.OK)
            dialog.run()
            dialog.destroy()
        except Exception:
            # Final fallback - print to console
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
            self.logger.error(f"Failed to show error dialog: {e}")
    
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
            self.logger.error(f"Failed to show info dialog: {e}")
    
    def get_settings_manager(self) -> Optional[SettingsManager]:
        """Get the settings manager instance."""
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
        
        # Store reference to main window
        if hasattr(window, 'is_main_window') and window.is_main_window:
            self._main_window = window
            self.logger.debug("Main window registered")
    
    def do_window_removed(self, window) -> None:
        """Handle window being removed from application."""
        Adw.Application.do_window_removed(self, window)
        
        # Clear main window reference if this was the main window
        if window == self._main_window:
            self._main_window = None
            self.logger.debug("Main window unregistered")
    
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
            self.logger.info("New window created")
            return window
        except Exception as e:
            self.logger.error(f"Failed to create new window: {e}")
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


def create_application() -> CommTerminalApp:
    """
    Create and return a new CommTerminalApp instance.
    
    Returns:
        Configured CommTerminalApp instance
    """
    logger = get_logger('ashyterm.app.factory')
    
    try:
        logger.info("Creating application instance")
        
        # Initialize Adwaita
        Adw.init()
        
        # Create application
        app = CommTerminalApp()
        
        logger.info("Application instance created successfully")
        return app
        
    except Exception as e:
        logger.critical(f"Failed to create application: {e}")
        raise


def run_application(app: CommTerminalApp, args: list = None) -> int:
    """
    Run the application with given arguments.
    
    Args:
        app: CommTerminalApp instance to run
        args: Command line arguments (default: sys.argv)
        
    Returns:
        Exit code
    """
    logger = get_logger('ashyterm.app.runner')
    
    if args is None:
        args = sys.argv
    
    try:
        logger.info(f"Starting application with args: {args}")
        exit_code = app.run(args)
        logger.info(f"Application exited with code: {exit_code}")
        return exit_code
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        return 1
    except Exception as e:
        logger.critical(f"Unhandled exception during application run: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main() -> int:
    """
    Main entry point for the application.
    
    Returns:
        Exit code
    """
    try:
        app = create_application()
        return run_application(app)
    except Exception as e:
        # Use basic print since logging might not be available
        print(f"FATAL: Failed to start application: {e}")
        import traceback
        traceback.print_exc()
        return 1