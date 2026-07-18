# ashyterm/cli_parser.py

"""CLI argument parsing for Ashy Terminal."""

from typing import TYPE_CHECKING, Optional

from gi.repository import GLib, Gtk
from typing import Any

if TYPE_CHECKING:
    from .app import CommTerminalApp


class CliArgParser:
    """Parse CLI arguments into structured dict. Depends on CommTerminalApp."""

    def __init__(self, app: "CommTerminalApp"):
        self._app = app
        self.logger = app.logger

    def handle_working_directory_arg(
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

    def handle_ssh_arg(
        self, arg: str, arguments: list, i: int, result: dict
    ) -> tuple:
        """Handle SSH argument variants. Returns (consumed, new_index)."""
        if arg == "--ssh" and i + 1 < len(arguments):
            result["ssh_target"] = arguments[i + 1]
            return True, i + 2
        if arg.startswith("--ssh="):
            result["ssh_target"] = arg.split("=", 1)[1]
            return True, i + 1
        return False, i

    def handle_execution_arg(
        self, arg: str, arguments: list, i: int, result: dict
    ) -> tuple:
        """Returns ``(consumed, new_index, stop_parsing)``."""
        # -e / -x / --execute: everything after becomes the command verbatim.
        if arg in ["-e", "-x", "--execute"]:
            return True, i + 1, True

        if arg.startswith("--execute="):
            result["execute_command"] = arg.split("=", 1)[1]
            return True, i + 1, False

        if arg == "--close-after-execute":
            result["close_after_execute"] = True
            return True, i + 1, False

        if arg == "--new-window":
            result["force_new_window"] = True
            return True, i + 1, False

        return False, i, False

    def handle_generic_arg(self, arg: str, result: dict, i: int) -> int:
        """Adopt the first positional as working_directory; warn on extras."""
        if not arg.startswith("-"):
            if result["working_directory"] is None:
                result["working_directory"] = arg
            else:
                self.logger.warning(
                    f"Ignoring extra positional argument '{arg}' "
                    f"(working directory already set to {result['working_directory']!r})."
                )

        return i + 1

    def parse_command_line_args(self, arguments: list) -> dict:
        """Parse command line arguments into a structured dictionary."""
        result: dict[str, str | bool | None] = {
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

            consumed, i = self.handle_working_directory_arg(arg, arguments, i, result)
            if consumed:
                continue

            consumed, i = self.handle_ssh_arg(arg, arguments, i, result)
            if consumed:
                continue

            consumed, i, stop = self.handle_execution_arg(arg, arguments, i, result)
            if stop:
                execute_index = i
                break
            if consumed:
                continue

            i = self.handle_generic_arg(arg, result, i)

        # Everything after ``-e/-x/--execute`` forms the command.
        if execute_index is not None and execute_index < len(arguments):
            remaining = arguments[execute_index:]
            if remaining:
                result["execute_command"] = " ".join(remaining)

        return result

    def create_tab_in_window(
        self,
        window: Any,
        ssh_target: Optional[str],
        execute_command: Optional[str],
        working_directory: Optional[str],
        close_after_execute: bool,
    ) -> None:
        """Create appropriate tab type in existing window."""
        if ssh_target:
            window.create_ssh_tab(ssh_target)
        elif execute_command:
            window.create_execute_tab(
                execute_command, working_directory, close_after_execute
            )
        else:
            window.create_local_tab(working_directory)

    def process_and_execute_args(self, arguments: list) -> None:
        """Parse arguments and decide what action to take."""
        args = self.parse_command_line_args(arguments)
        if self._app.settings_manager is None:
            raise RuntimeError(
                "settings_manager must be initialized before processing CLI args"
            )
        behavior = self._app.settings_manager.get("new_instance_behavior", "new_tab")
        windows = self._app.get_windows()
        target_window = windows[0] if windows else None

        has_explicit_command = (
            args["ssh_target"] or args["execute_command"] or args["working_directory"]
        )

        if args["force_new_window"] or behavior == "new_window" or not target_window:
            self.logger.info("Creating a new window for command line arguments.")
            window = self._app.create_new_window(
                initial_working_directory=args["working_directory"],
                initial_execute_command=args["execute_command"],
                close_after_execute=args["close_after_execute"],
                initial_ssh_target=args["ssh_target"],
            )
            self.present_window_and_request_focus(window)
        elif behavior == "focus_existing" and not has_explicit_command:
            self.logger.info("Focusing existing window without creating new tab.")
            self.present_window_and_request_focus(target_window)
        else:
            self.logger.info("Reusing existing window for a new tab.")
            self.present_window_and_request_focus(target_window)
            self.create_tab_in_window(
                target_window,
                args["ssh_target"],
                args["execute_command"],
                args["working_directory"],
                args["close_after_execute"],
            )

    def present_window_and_request_focus(self, window: Gtk.Window) -> None:
        """Present window; apply modal hack for focus if needed (KDE/Wayland)."""
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
