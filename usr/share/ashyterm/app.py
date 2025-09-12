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

from gi.repository import Adw, Gio, GLib, Gtk

from .settings.config import (
    APP_ID,
    APP_TITLE,
    APP_VERSION,
    COPYRIGHT,
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
                "new-window",
                "zoom-in",
                "zoom-out",
                "zoom-reset",
                "split-horizontal",
                "split-vertical",
                "close-pane",
                "next-tab",
                "previous-tab",
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

        windows = self.get_windows()
        target_window = windows[0] if windows else None

        if behavior == "new_window" or not target_window:
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
            self._present_window_and_request_focus(target_window)
            if ssh_target:
                target_window.create_ssh_tab(ssh_target)
            elif execute_command:
                target_window.create_execute_tab(
                    execute_command, working_directory, close_after_execute
                )
            else:
                target_window.create_local_tab(working_directory)

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
        """Handles the manual backup creation flow."""
        file_dialog = Gtk.FileDialog(title=_("Save Backup As..."), modal=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        file_dialog.set_initial_name(f"ashyterm-backup-{timestamp}.7z")
        file_dialog.save(self.get_active_window(), None, self._on_backup_file_selected)

    def _on_backup_file_selected(self, dialog, result):
        """Callback after user selects a location to save the backup."""
        try:
            gio_file = dialog.save_finish(result)
            if gio_file:
                self._prompt_for_backup_password(gio_file.get_path())
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self._show_error_dialog(_("Backup Error"), e.message)

    def _prompt_for_backup_password(self, target_path: str):
        """Shows a dialog to get and confirm a password for the backup."""
        dialog = Adw.MessageDialog(
            transient_for=self.get_active_window(),
            heading=_("Set Backup Password"),
            body=_("Please enter a password to encrypt the backup file."),
            close_response="cancel",
        )
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        pass_entry = Gtk.PasswordEntry(
            placeholder_text=_("Password"), show_peek_icon=True
        )
        confirm_entry = Gtk.PasswordEntry(
            placeholder_text=_("Confirm Password"), show_peek_icon=True
        )
        content.append(pass_entry)
        content.append(confirm_entry)
        dialog.set_extra_child(content)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("backup", _("Create Backup"))
        dialog.set_default_response("backup")
        dialog.set_response_appearance("backup", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response_id):
            if response_id == "backup":
                pwd1 = pass_entry.get_text()
                pwd2 = confirm_entry.get_text()
                if not pwd1:
                    self._show_error_dialog(
                        _("Password Error"), _("Password cannot be empty."), parent=d
                    )
                    return
                if pwd1 != pwd2:
                    self._show_error_dialog(
                        _("Password Error"), _("Passwords do not match."), parent=d
                    )
                    return

                d.close()
                self._execute_backup(target_path, pwd1)
            else:
                d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _execute_backup(self, target_path: str, password: str):
        """Executes the backup process in a separate thread."""
        active_window = self.get_active_window()
        if not active_window:
            return

        toast = Adw.Toast(title=_("Creating backup..."), timeout=0)
        active_window.toast_overlay.add_toast(toast)

        def backup_thread():
            try:
                source_files = [Path(SESSIONS_FILE), Path(SETTINGS_FILE)]
                layouts_dir = Path(LAYOUT_DIR)
                self.backup_manager.create_encrypted_backup(
                    target_path,
                    password,
                    active_window.session_store,
                    source_files,
                    layouts_dir,
                )
                GLib.idle_add(
                    self._show_info_dialog,
                    _("Backup Complete"),
                    _("Backup saved successfully to:\n{}").format(target_path),
                )
            except Exception as e:
                self.logger.error(f"Manual backup failed: {e}")
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Backup Failed"),
                    _("Could not create backup: {}").format(e),
                )
            finally:
                GLib.idle_add(toast.dismiss)

        threading.Thread(target=backup_thread, daemon=True).start()

    def _on_restore_backup_action(self, _action, _param) -> None:
        """Handles the restore backup flow."""
        dialog = Adw.MessageDialog(
            transient_for=self.get_active_window(),
            heading=_("Restore from Backup?"),
            body=_(
                "Restoring from a backup will overwrite all your current sessions, settings, and layouts. This action cannot be undone.\n\n<b>The application will need to be restarted after restoring.</b>"
            ),
            body_use_markup=True,
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Choose File and Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_confirmation)
        dialog.present()

    def _on_restore_confirmation(self, dialog, response_id):
        dialog.close()
        if response_id == "restore":
            file_dialog = Gtk.FileDialog(title=_("Select Backup File"), modal=True)
            file_filter = Gtk.FileFilter()
            file_filter.add_pattern("*.7z")
            file_filter.set_name(_("Backup Files"))
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(file_filter)
            file_dialog.set_filters(filters)
            file_dialog.open(
                self.get_active_window(), None, self._on_restore_file_selected
            )

    def _on_restore_file_selected(self, dialog, result):
        """Callback after user selects a backup file to restore."""
        try:
            gio_file = dialog.open_finish(result)
            if gio_file:
                self._prompt_for_restore_password(gio_file.get_path())
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self._show_error_dialog(_("Restore Error"), e.message)

    def _prompt_for_restore_password(self, source_path: str):
        """Shows a dialog to get the password for the backup file."""
        dialog = Adw.MessageDialog(
            transient_for=self.get_active_window(),
            heading=_("Enter Backup Password"),
            body=_("Please enter the password for the selected backup file."),
            close_response="cancel",
        )
        pass_entry = Gtk.PasswordEntry(
            placeholder_text=_("Password"), show_peek_icon=True
        )
        dialog.set_extra_child(pass_entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_default_response("restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response_id):
            if response_id == "restore":
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
