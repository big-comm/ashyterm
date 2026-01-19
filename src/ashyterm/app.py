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
    COPYRIGHT,
    DEVELOPER_NAME,
    DEVELOPER_TEAM,
    ISSUE_URL,
)
from .settings.manager import SettingsManager, get_settings_manager

# Lazy import: from .terminal.spawner import cleanup_spawner  # Only needed at shutdown
from .utils.exceptions import handle_exception
from .utils.logger import enable_debug_mode, get_logger, log_app_shutdown, log_app_start
from .utils.translation_utils import _

# Lazy imports for startup performance - these are used infrequently
# from .utils.platform import get_platform_info  # Loaded on first access via property
# from .utils.security import create_security_auditor  # Loaded on first access via property

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
        self._platform_info = None  # Lazy loaded via property
        self._initialized = False
        self._shutting_down = False

        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)
        self.connect("command-line", self._on_command_line)
        atexit.register(self._cleanup_on_exit)

    @property
    def platform_info(self):
        """Lazy-load platform info on first access."""
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
                enable_debug_mode()
                self.logger.info("Debug mode enabled")

            # Defer crypto availability check to 1 second after startup
            # The check is just for logging, not critical for app operation
            GLib.timeout_add(1000, self._log_crypto_status)

            self._initialized = True
            self.logger.info("All essential subsystems initialized successfully")
            return True
        except Exception as e:
            self.logger.critical(f"Subsystem initialization failed: {e}")
            handle_exception(
                e, "application initialization", "ashyterm.app", reraise=True
            )
            return False

    def _log_crypto_status(self) -> bool:
        """Log crypto status after startup (deferred to avoid blocking startup)."""
        from .utils.crypto import is_encryption_available

        if is_encryption_available():
            self.logger.info(
                "Secure password storage (Secret Service API) is available."
            )
        else:
            self.logger.warning(
                "Secure password storage is not available - passwords will not be saved."
            )
        return False  # Don't repeat idle callback

    def _on_startup(self, app) -> None:
        """Handle application startup."""
        try:
            self.logger.info("Application startup initiated")
            log_app_start()
            if not self._initialize_subsystems():
                self.logger.critical("Failed to initialize application subsystems")
                self.quit()
                return

            # Configure icon strategy after settings are available, but still
            # before any UI is built.
            self._configure_icon_theme()

            self._setup_actions()
            self._setup_keyboard_shortcuts()
            self.logger.info("Application startup completed successfully")
        except Exception as e:
            self.logger.critical(f"Application startup failed: {e}")
            self._show_startup_error(str(e))
            self.quit()

    def _configure_icon_theme(self) -> None:
        """Configure icon theme strategy based on settings.

        When 'ashy' strategy is selected, icons are loaded directly from
        bundled SVG files. When 'system' is selected, icons come from the
        GTK IconTheme (follows desktop theme).

        Note: FileManager uses MIME-type icons from system theme regardless
        of this setting (see filemanager/manager.py).
        """
        try:
            icon_strategy = "ashy"
            if self.settings_manager:
                icon_strategy = self.settings_manager.get("icon_theme_strategy", "ashy")

            # Configure icons module based on strategy
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
        """Handle application activation when launched without command-line arguments."""
        # Skip if we already presented the window during command-line processing
        if getattr(self, "_window_already_presented", False):
            self._window_already_presented = False
            return

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
        # Mark that we've already presented window to avoid duplicate present() in _on_activate
        self._window_already_presented = True
        self.activate()
        return 0

    def _handle_working_directory_arg(
        self, arg: str, arguments: list, i: int, result: dict
    ) -> tuple:
        """Handle working directory argument variants. Returns (consumed, new_index)."""
        if arg in ["-w", "--working-directory"] and i + 1 < len(arguments):
            result["working_directory"] = arguments[i + 1]
            return True, i + 2
        if arg.startswith("--working-directory="):
            result["working_directory"] = arg.split("=", 1)[1]
            return True, i + 1
        return False, i

    def _handle_ssh_arg(self, arg: str, arguments: list, i: int, result: dict) -> tuple:
        """Handle SSH argument variants. Returns (consumed, new_index)."""
        if arg == "--ssh" and i + 1 < len(arguments):
            result["ssh_target"] = arguments[i + 1]
            return True, i + 2
        if arg.startswith("--ssh="):
            result["ssh_target"] = arg.split("=", 1)[1]
            return True, i + 1
        return False, i

    def _handle_execution_arg(
        self, arg: str, arguments: list, i: int, result: dict
    ) -> tuple:
        """Handle execution related arguments. Returns (consumed, new_index, stop_parsing)."""
        # 1. Stop parsing markers
        if arg in ["-e", "-x", "--execute"]:
            return True, i + 1, True

        # 2. Key-value style
        if arg.startswith("--execute="):
            result["execute_command"] = arg.split("=", 1)[1]
            return True, i + 1, False

        # 3. Flags
        if arg == "--close-after-execute":
            result["close_after_execute"] = True
            return True, i + 1, False

        if arg == "--new-window":
            result["force_new_window"] = True
            return True, i + 1, False

        return False, i, False

    def _handle_generic_arg(self, arg: str, result: dict, i: int) -> int:
        """Handle positional arguments or unknown flags."""
        # Check working directory and SSH handlers first (legacy integration)
        # These are now merged or called sequentially in the main loop

        # Positional working directory (if not already set)
        if not arg.startswith("-") and result["working_directory"] is None:
            result["working_directory"] = arg

        return i + 1

    def _parse_command_line_args(self, arguments: list) -> dict:
        """Parse command line arguments into a structured dictionary."""
        result = {
            "working_directory": None,
            "execute_command": None,
            "ssh_target": None,
            "close_after_execute": False,
            "force_new_window": False,
        }

        i = 1
        execute_index = None
        while i < len(arguments):
            arg = arguments[i]

            # Try specialized handlers
            consumed, i = self._handle_working_directory_arg(arg, arguments, i, result)
            if consumed:
                continue

            consumed, i = self._handle_ssh_arg(arg, arguments, i, result)
            if consumed:
                continue

            # Try execution handlers
            consumed, i, stop = self._handle_execution_arg(arg, arguments, i, result)
            if stop:
                execute_index = i
                break
            if consumed:
                continue

            # Handle flags and generic arguments
            i = self._handle_generic_arg(arg, result, i)

        # Capture remaining arguments as execute command
        if execute_index is not None and execute_index < len(arguments):
            remaining = arguments[execute_index:]
            if remaining:
                result["execute_command"] = " ".join(remaining)

        return result

    def _create_tab_in_window(
        self,
        window,
        ssh_target,
        execute_command,
        working_directory,
        close_after_execute,
    ):
        """Create appropriate tab type in existing window."""
        if ssh_target:
            window.create_ssh_tab(ssh_target)
        elif execute_command:
            window.create_execute_tab(
                execute_command, working_directory, close_after_execute
            )
        else:
            window.create_local_tab(working_directory)

    def _process_and_execute_args(self, arguments: list):
        """Parse arguments and decide what action to take."""
        args = self._parse_command_line_args(arguments)

        behavior = self.settings_manager.get("new_instance_behavior", "new_tab")
        windows = self.get_windows()
        target_window = windows[0] if windows else None

        has_explicit_command = (
            args["ssh_target"] or args["execute_command"] or args["working_directory"]
        )

        if args["force_new_window"] or behavior == "new_window" or not target_window:
            self.logger.info("Creating a new window for command line arguments.")
            window = self.create_new_window(
                initial_working_directory=args["working_directory"],
                initial_execute_command=args["execute_command"],
                close_after_execute=args["close_after_execute"],
                initial_ssh_target=args["ssh_target"],
            )
            self._present_window_and_request_focus(window)
        elif behavior == "focus_existing" and not has_explicit_command:
            self.logger.info("Focusing existing window without creating new tab.")
            self._present_window_and_request_focus(target_window)
        else:
            self.logger.info("Reusing existing window for a new tab.")
            self._present_window_and_request_focus(target_window)
            self._create_tab_in_window(
                target_window,
                args["ssh_target"],
                args["execute_command"],
                args["working_directory"],
                args["close_after_execute"],
            )

    def _present_window_and_request_focus(self, window: Gtk.Window):
        """Present the window and use a modal dialog hack to request focus if needed.

        The hack is deferred to run at low priority, allowing the main window
        to render and become interactive first. This improves perceived startup time.

        NOTE: This hack is required for KDE Plasma/Wayland to properly focus the window
        when opening a new tab from an external program.
        """
        window.present()

        def check_and_apply_hack():
            if not window.is_active():
                self.logger.info(
                    "Window not active after present(), applying modal window hack."
                )
                hack_window = Gtk.Window(transient_for=window, modal=True)

                hack_window.set_default_size(1, 1)
                hack_window.set_decorated(False)

                hack_window.present()
                GLib.idle_add(hack_window.destroy)

            return GLib.SOURCE_REMOVE

        # Run at low priority so it doesn't block the initial render
        GLib.idle_add(check_and_apply_hack, priority=GLib.PRIORITY_LOW)

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

    @property
    def backup_handler(self):
        """Lazy-load backup handler on first access."""
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
        # Lazy import - only needed at shutdown
        from .core.tasks import AsyncTaskManager
        from .terminal.spawner import cleanup_spawner

        cleanup_spawner()

        # Shutdown global task manager to terminate all background threads
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
            dialog = Adw.MessageDialog(
                transient_for=self.get_active_window(),
                heading=_("Startup Error"),
                body=_("Application failed to start: {}").format(error_message),
            )
            dialog.add_response("ok", "OK")
            dialog.present()
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
