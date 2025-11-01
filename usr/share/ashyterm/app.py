# ashyterm/app.py

import atexit
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .settings.config import (
    APP_ID,
    APP_TITLE,
    APP_VERSION,
    COPYRIGHT,
    CUSTOM_COMMANDS_FILE,
    DEVELOPER_NAME,
    DEVELOPER_TEAM,
    ISSUE_URL,
    LAYOUT_DIR,
    SESSIONS_FILE,
    SETTINGS_FILE,
)
from .settings.manager import SettingsManager
from .terminal.spawner import cleanup_spawner
from .utils.backup import get_backup_manager
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
            elif theme == "dark" or theme == "terminal":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:  # "default"
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

            self.logger.info(f"Applied initial GTK theme: {theme}")

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

    def _setup_icon_theme(self):
        """Setup custom icon theme path for bundled icons"""
        try:
            # Get the application's directory (where app.py is located)
            app_dir = os.path.dirname(os.path.abspath(__file__))
            icons_dir = os.path.join(app_dir, 'icons')
            
            # Also check system installation path
            system_icon_path = "/usr/share/ashyterm/icons"
            
            # Determine which icon path to use
            icon_paths_to_add = []
            if os.path.exists(icons_dir):
                icon_paths_to_add.append(icons_dir)
            if os.path.isdir(system_icon_path):
                icon_paths_to_add.append(system_icon_path)
            
            if not icon_paths_to_add:
                self.logger.debug("No custom icon directories found")
                return
            
            # For each valid icon path, create index.theme if it doesn't exist
            for icon_path in icon_paths_to_add:
                index_theme_path = os.path.join(icon_path, 'hicolor', 'index.theme')
                if not os.path.exists(index_theme_path):
                    try:
                        os.makedirs(os.path.dirname(index_theme_path), exist_ok=True)
                        with open(index_theme_path, 'w') as f:
                            f.write("""[Icon Theme]
Name=Hicolor
Comment=Fallback icon theme
Hidden=true
Directories=scalable/actions

[scalable/actions]
Context=Actions
Size=48
MinSize=1
MaxSize=512
Type=Scalable
""")
                        self.logger.debug(f"Created index.theme at: {index_theme_path}")
                    except Exception as e:
                        self.logger.debug(f"Could not create index.theme: {e}")
                
                # Add custom icons directory to icon theme
                icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
                icon_theme.add_search_path(icon_path)
                self.logger.info(f"Added icon search path: {icon_path}")
                    
        except Exception as e:
            self.logger.error(f"Error setting up icon theme: {e}")

    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            # Setup custom icon theme path FIRST
            self._setup_icon_theme()
            
            self.logger.info("Application startup initiated")
            log_app_start()
            if not self._initialize_subsystems():
                self.logger.critical("Failed to initialize application subsystems")
                self.quit()
                return
            self._setup_actions()
            self._setup_keyboard_shortcuts()
            self.logger.info("Application startup completed successfully")
        except Exception as e:
            self.logger.critical(f"Application startup failed: {e}")
            self._show_startup_error(str(e))
            self.quit()

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
                "show-command-guide",
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
            for action in shortcut_actions:
                shortcut_key = f"shortcut_{action.replace('-', '_')}"
                shortcut_value = self.settings_manager.get(
                    shortcut_key, self._default_shortcuts().get(action)
                )
                if shortcut_value:
                    self.set_accels_for_action(f"win.{action}", [shortcut_value])
        except Exception as e:
            self.logger.error(f"Failed to update window shortcuts: {e}")

    def _default_shortcuts(self) -> dict:
        """Default keyboard shortcuts."""
        return {
            "new-local-tab": "<Control><Shift>t",
            "close-tab": "<Control><Shift>w",
            "copy": "<Control><Shift>c",
            "paste": "<Control><Shift>v",
            "select-all": "<Control><Shift>a",
            "toggle-sidebar": "F9",
            "toggle-file-manager": "<Control><Shift>f",
            "show-command-guide": "<Control><Shift>h",
            "new-window": "<Control><Shift>n",
            "zoom-in": "<Control>plus",
            "zoom-out": "<Control>minus",
            "zoom-reset": "<Control>0",
            "split-horizontal": "<Control><Shift>d",
            "split-vertical": "<Control><Shift>e",
            "close-pane": "<Control><Shift>x",
            "next-tab": "<Control>Page_Down",
            "previous-tab": "<Control>Page_Up",
            "toggle-broadcast": "<Control><Shift>b",
            "ai-assistant": "<Control><Shift>i",
        }

    def _on_activate(self, app) -> None:
        """Handle application activation."""
        try:
            # Prioritize the project's bundled icon path over system themes
            try:
                # Get the default icon theme object
                icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
                
                # Get the current list of system search paths
                current_paths = icon_theme.get_search_path()
                
                # Define paths to the project's bundled icons
                app_dir = os.path.dirname(os.path.abspath(__file__))
                project_icon_path = os.path.join(app_dir, 'icons')
                system_icon_path = "/usr/share/ashyterm/icons"
                
                # Collect valid icon paths to prioritize
                priority_paths = []
                if os.path.isdir(project_icon_path):
                    priority_paths.append(project_icon_path)
                if os.path.isdir(system_icon_path):
                    priority_paths.append(system_icon_path)
                
                if priority_paths:
                    # Create a new list of paths with project's paths at the beginning
                    new_paths = priority_paths + current_paths
                    
                    # Set the new, prioritized search path list
                    icon_theme.set_search_path(new_paths)
                    
                    self.logger.info(f"Prioritized custom icon paths: {priority_paths}")
                else:
                    self.logger.debug("No custom icon paths found to prioritize")

            except Exception as e:
                self.logger.error(f"Could not set prioritized icon path: {e}")

            if self._main_window:
                self._main_window.present()
                return
            try:
                from .window import CommTerminalWindow

                self._main_window = CommTerminalWindow(
                    application=self, settings_manager=self.settings_manager
                )
                self.add_window(self._main_window)
                self._main_window.present()
                self.logger.info("Main window created and presented")
            except Exception as e:
                self.logger.critical(f"Failed to create main window: {e}")
                self._show_startup_error(str(e))
                self.quit()
        except Exception as e:
            self.logger.critical(f"Application activation failed: {e}")
            self.quit()

    def _on_command_line(self, app, command_line) -> int:
        """Handle command line arguments."""
        try:
            args = command_line.get_arguments()[1:]
            options = {}
            i = 0
            while i < len(args):
                arg = args[i]
                if arg == "--working-directory" or arg == "-w":
                    if i + 1 < len(args):
                        options["initial_working_directory"] = args[i + 1]
                        i += 2
                    else:
                        self.logger.error("--working-directory requires an argument")
                        return 1
                elif arg == "--execute" or arg == "-e":
                    if i + 1 < len(args):
                        options["initial_execute_command"] = args[i + 1]
                        i += 2
                    else:
                        self.logger.error("--execute requires an argument")
                        return 1
                elif arg == "--close-after-execute":
                    options["close_after_execute"] = True
                    i += 1
                elif arg == "--ssh":
                    if i + 1 < len(args):
                        options["initial_ssh_target"] = args[i + 1]
                        i += 2
                    else:
                        self.logger.error("--ssh requires an argument")
                        return 1
                elif arg == "--new-window":
                    options["_triggered_by_command_line"] = True
                    i += 1
                elif arg == "--version" or arg == "-v":
                    print(f"{APP_TITLE} {APP_VERSION}")
                    return 0
                elif arg == "--help" or arg == "-h":
                    self._print_help()
                    return 0
                else:
                    self.logger.warning(f"Unknown argument: {arg}")
                    i += 1
            self.activate()
            if options:
                if self._main_window:
                    if options.get("_triggered_by_command_line"):
                        self.create_new_window(**options)
                    elif "initial_ssh_target" in options:
                        self._main_window.create_new_ssh_tab(
                            options["initial_ssh_target"]
                        )
                    elif "initial_execute_command" in options:
                        self._main_window.create_new_local_tab(
                            working_directory=options.get("initial_working_directory"),
                            execute_command=options.get("initial_execute_command"),
                            close_after_execute=options.get("close_after_execute", False),
                        )
                    elif "initial_working_directory" in options:
                        self._main_window.create_new_local_tab(
                            working_directory=options["initial_working_directory"]
                        )
            return 0
        except Exception as e:
            self.logger.error(f"Command line handling failed: {e}")
            return 1

    def _print_help(self):
        """Print command line help."""
        help_text = f"""
{APP_TITLE} {APP_VERSION}

Usage: ashyterm [OPTIONS]

Options:
  -w, --working-directory DIR  Start terminal in specified directory
  -e, --execute COMMAND       Execute command in new tab
  --close-after-execute       Close tab after command execution
  --ssh TARGET                Open SSH connection to target
  --new-window                Open a new window
  -v, --version               Show version information
  -h, --help                  Show this help message

Examples:
  ashyterm --working-directory /home/user/projects
  ashyterm --execute "ls -la"
  ashyterm --ssh user@example.com
  ashyterm --new-window
"""
        print(help_text)

    def _on_quit_action(self, action, param) -> None:
        """Handle quit action."""
        try:
            if self._has_active_ssh_sessions():
                self._show_ssh_close_confirmation()
            else:
                self.quit()
        except Exception as e:
            self.logger.error(f"Quit action failed: {e}")

    def _on_preferences_action(self, action, param) -> None:
        """Handle preferences action."""
        try:
            if self._main_window:
                self._main_window.show_preferences_dialog()
        except Exception as e:
            self.logger.error(f"Preferences action failed: {e}")

    def _on_about_action(self, action, param) -> None:
        """Handle about action."""
        try:
            parent = self.get_active_window()
            about = Adw.AboutDialog(
                application_name=APP_TITLE,
                application_icon="ashyterm",
                version=APP_VERSION,
                developer_name=DEVELOPER_NAME,
                developers=DEVELOPER_TEAM,
                copyright=COPYRIGHT,
                issue_url=ISSUE_URL,
                license_type=Gtk.License.GPL_3_0,
            )
            about.present(parent)
        except Exception as e:
            self.logger.error(f"About dialog failed: {e}")

    def _on_backup_now_action(self, action, param) -> None:
        """Handle backup now action."""
        try:
            active_window = self.get_active_window()
            if not active_window:
                self.logger.warning("No active window for backup operation")
                return
            dialog = Gtk.FileDialog()
            dialog.set_title(_("Select Backup Location"))
            dialog.set_initial_name(
                f"ashyterm_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            )

            def on_save_finish(dialog, result):
                try:
                    file = dialog.save_finish(result)
                    if file:
                        destination = file.get_path()
                        pass_dialog = Adw.MessageDialog(
                            transient_for=active_window,
                            heading=_("Encrypt Backup"),
                            body=_(
                                "Enter a password to encrypt your backup. Keep this password safe - you'll need it to restore your data."
                            ),
                        )
                        pass_entry = Gtk.PasswordEntry()
                        pass_entry.set_show_peek_icon(True)
                        pass_dialog.set_extra_child(pass_entry)
                        pass_dialog.add_response("cancel", _("Cancel"))
                        pass_dialog.add_response("encrypt", _("Encrypt & Backup"))
                        pass_dialog.set_response_appearance(
                            "encrypt", Adw.ResponseAppearance.SUGGESTED
                        )
                        pass_dialog.set_default_response("encrypt")

                        def on_password_response(d, response):
                            if response == "encrypt":
                                password = pass_entry.get_text()
                                if password:
                                    self._execute_backup(destination, password)
                            d.close()

                        pass_dialog.connect("response", on_password_response)
                        pass_dialog.present()
                except Exception as e:
                    self.logger.error(f"Backup file selection failed: {e}")

            dialog.save(active_window, None, on_save_finish)
        except Exception as e:
            self.logger.error(f"Backup action failed: {e}")

    def _execute_backup(self, destination: str, password: str):
        """Executes the backup process in a separate thread."""
        active_window = self.get_active_window()
        toast = Adw.Toast(title=_("Creating encrypted backup..."), timeout=0)
        active_window.toast_overlay.add_toast(toast)

        def backup_thread():
            try:
                self.backup_manager.create_encrypted_backup(
                    self.platform_info.config_dir, destination, password
                )
                GLib.idle_add(
                    self._show_info_dialog,
                    _("Backup Complete"),
                    _("Your data has been backed up successfully to: {}").format(
                        destination
                    ),
                )
            except Exception as e:
                self.logger.error(f"Backup failed: {e}")
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Backup Failed"),
                    _("Could not create backup: {}").format(e),
                )
            finally:
                GLib.idle_add(toast.dismiss)

        threading.Thread(target=backup_thread, daemon=True).start()

    def _on_restore_backup_action(self, action, param) -> None:
        """Handle restore backup action."""
        try:
            active_window = self.get_active_window()
            if not active_window:
                self.logger.warning("No active window for restore operation")
                return
            file_dialog = Gtk.FileDialog()
            file_dialog.set_title(_("Select Backup File"))
            filter_zip = Gtk.FileFilter()
            filter_zip.set_name(_("Backup Files"))
            filter_zip.add_pattern("*.zip")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(filter_zip)
            file_dialog.set_filters(filters)

            def on_open_finish(dialog, result):
                try:
                    file = dialog.open_finish(result)
                    if file:
                        source_path = file.get_path()
                        self._show_restore_password_dialog(source_path)
                except Exception as e:
                    self.logger.error(f"Backup file selection failed: {e}")

            file_dialog.open(active_window, None, on_open_finish)
        except Exception as e:
            self.logger.error(f"Restore backup action failed: {e}")

    def _show_restore_password_dialog(self, source_path: str):
        """Shows password dialog for restore operation."""
        active_window = self.get_active_window()
        dialog = Adw.MessageDialog(
            transient_for=active_window,
            heading=_("Decrypt Backup"),
            body=_(
                "Enter the password used to encrypt this backup. All current data will be replaced with the backup data."
            ),
        )
        pass_entry = Gtk.PasswordEntry()
        pass_entry.set_show_peek_icon(True)
        dialog.set_extra_child(pass_entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("restore")

        def on_response(d, response):
            if response == "restore":
                password = pass_entry.get_text()
                if password:
                    self._execute_restore(source_path, password)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _execute_restore(self, source_path: str, password: str):
        """Executes the restore process in a separate thread."""
        active_window = self.get_active_window()
        toast = Adw.Toast(title=_("Restoring from backup..."), timeout=0)
        active_window.toast_overlay.add_toast(toast)

        def restore_thread():
            try:
                self.backup_manager.restore_from_encrypted_backup(
                    source_path, password, self.platform_info.config_dir
                )
                GLib.idle_add(self._show_restore_success_dialog)
            except Exception as e:
                self.logger.error(f"Restore failed: {e}")
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Restore Failed"),
                    _("Could not restore from backup: {}").format(e),
                )
            finally:
                GLib.idle_add(toast.dismiss)

        threading.Thread(target=restore_thread, daemon=True).start()

    def _show_restore_success_dialog(self):
        dialog = Adw.MessageDialog(
            transient_for=self.get_active_window(),
            heading=_("Restore Complete"),
            body=_(
                "Data has been restored successfully. Please restart Ashy Terminal for the changes to take effect."
            ),
            close_response="ok",
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()
        return False

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
            if self._main_window:
                self._main_window.destroy()
                self._main_window = None
            if self.settings_manager:
                self.settings_manager.save_settings()
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

    def _show_error_dialog(self, title: str, message: str, parent=None) -> None:
        """Show error dialog to user."""
        try:
            if parent is None:
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