import argparse
import os
import signal
import sys

# Performance optimization: Use OpenGL renderer instead of Vulkan
# GTK4's Vulkan renderer has worse startup performance than the GL renderer
# See: https://wiki.archlinux.org/title/GTK#GTK_4_applications_are_slow
# This must be set BEFORE importing GTK/GLib
if "GSK_RENDERER" not in os.environ:
    os.environ["GSK_RENDERER"] = "gl"

if __package__ is None:
    import pathlib

    parent_dir = pathlib.Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    __package__ = "ashyterm"

from .utils.logger import (
    enable_debug_mode,
    get_logger,
    set_console_log_level,
)
from .utils.translation_utils import _


def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown on Linux."""

    def signal_handler(sig, frame):
        print(_("\nReceived signal {}, shutting down gracefully...").format(sig))
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            app = Gtk.Application.get_default()
            if app:
                app.quit()
            else:
                sys.exit(0)
        except Exception:
            sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except Exception as e:
        print(_("Warning: Could not set up signal handlers: {}").format(e))


def main() -> int:
    """Main entry point for the application."""
    # Use a separate parser to handle debug/log flags before the main app starts
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--debug", "-d", action="store_true")
    pre_parser.add_argument("--log-level")
    pre_args, remaining_argv = pre_parser.parse_known_args()

    # Apply pre-launch log settings if provided
    if pre_args.debug:
        enable_debug_mode()
    elif pre_args.log_level:
        try:
            set_console_log_level(pre_args.log_level)
        except KeyError:
            print(f"Warning: Invalid log level '{pre_args.log_level}' provided.")

    logger = get_logger("ashyterm.main")

    # Tries to set the process title and logs failures
    try:
        import setproctitle

        setproctitle.setproctitle("ashyterm")
        logger.info("Process title set to 'ashyterm'.")
    except Exception as e:
        logger.error(f"Failed to set process title: {e}", exc_info=True)

    # Main parser for the application's command-line interface
    parser = argparse.ArgumentParser(
        prog="ashyterm",
        description=_(
            "Ashy Terminal - A modern terminal emulator with session management"
        ),
        epilog=_("For more information, visit: https://communitybig.org/"),
    )

    # Custom version action to load APP_VERSION lazily (avoids loading GTK for --version)
    class LazyVersionAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            from .settings.config import APP_VERSION

            print(f"ashyterm {APP_VERSION}")
            parser.exit()

    parser.add_argument(
        "--version",
        "-v",
        nargs=0,
        action=LazyVersionAction,
        help=_("Show version and exit"),
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help=_("Enable debug mode")
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=_("Set logging level"),
    )
    parser.add_argument(
        "--working-directory",
        "-w",
        metavar="DIR",
        help=_("Set the working directory for the initial terminal"),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help=_("Working directory (positional argument)"),
    )
    parser.add_argument(
        "--execute",
        "-e",
        "-x",
        metavar="COMMAND",
        help=_("Execute command in the terminal"),
    )
    parser.add_argument(
        "--close-after-execute",
        action="store_true",
        help=_("Close terminal after executing command (only with --execute)"),
    )
    parser.add_argument(
        "--ssh",
        metavar="[USER@]HOST[:PORT][:/PATH]",
        help=_("Connect to SSH host with optional user, port, and remote path"),
    )
    parser.add_argument(
        "--convert-to-ssh", help=_("Convert KIO/GVFS URI path to SSH format")
    )
    # NOVO: Adicionado o argumento --new-window
    parser.add_argument(
        "--new-window", action="store_true", help=_("Force opening a new window")
    )

    try:
        parser.parse_known_args()
    except SystemExit:
        return 0

    setup_signal_handlers()

    try:
        logger.info("Creating application instance")

        # Lazy import: only load the heavy GTK/Adw/VTE modules when actually running
        from .app import CommTerminalApp

        app = CommTerminalApp()
        return app.run(sys.argv)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
        return 0
    except Exception as e:
        logger.critical(f"A fatal error occurred: {e}", exc_info=True)
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        # GTK4 uses GtkAlertDialog instead of GtkMessageDialog.run() (GTK3 pattern)
        alert = Gtk.AlertDialog()
        alert.set_message(_("Fatal Application Error"))
        alert.set_detail(_("Could not start Ashy Terminal.\n\nError: {}").format(e))
        alert.set_modal(True)
        alert.show(None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
