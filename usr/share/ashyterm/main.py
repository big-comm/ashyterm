#!/usr/bin/env python3
"""
Ashy Terminal - A modern terminal emulator with session management.

Enhanced entry point with comprehensive error handling, logging, platform detection,
and command line argument processing.
"""

import sys
import argparse
import signal

# Ensure we can find the package modules
if __package__ is None:
    import pathlib
    parent_dir = pathlib.Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    __package__ = "ashyterm"

# Import translation utility
from .utils.translation_utils import _

# Import necessary components
from .app import CommTerminalApp
from .utils.logger import (
    get_logger,
    enable_debug_mode,
    set_console_level,
    LogLevel,
)
from .utils.platform import is_windows

def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown."""
    def signal_handler(sig, frame):
        print(_("\nReceived signal {}, shutting down gracefully...").format(sig))
        # The application's own shutdown handler will log this
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
        if is_windows():
            signal.signal(signal.SIGBREAK, signal_handler)
    except Exception as e:
        print(_("Warning: Could not set up signal handlers: {}").format(e))


def parse_command_line() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ashyterm",
        description=_(
            "Ashy Terminal - A modern terminal emulator with session management"
        ),
        epilog=_("For more information, visit: https://communitybig.org/"),
    )
    parser.add_argument("--version", "-v", action="version", version="%(prog)s 1.0.1")
    parser.add_argument(
        "--debug", "-d", action="store_true", help=_("Enable debug mode")
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help=_("Set logging level"),
    )
    parser.add_argument(
        '--working-directory', '-w',
        metavar='DIR',
        help=_('Set the working directory for the initial terminal')
    )
    parser.add_argument(
        'directory',
        nargs='?',  # Makes the positional argument optional
        default=None,
        help=_('Positional argument for the working directory (alternative to -w)')
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point for the application."""
    # We need to handle argument parsing before app.run() to pass the directory
    original_argv = sys.argv
    app = CommTerminalApp()
    
    # Parse arguments after creating the app instance
    args = parse_command_line()

    if args.debug:
        enable_debug_mode()
    else:
        set_console_level(LogLevel[args.log_level])

    logger = get_logger("ashyterm.main")
    setup_signal_handlers()

    try:
        logger.info("Creating application instance")
        # Determine the working directory, giving precedence to the named argument
        working_directory = args.working_directory or args.directory
        if working_directory:
            app.initial_working_directory = working_directory
            logger.info(f"Initial working directory set to: {working_directory}")
        
        logger.info("Running application")
        return app.run(original_argv)

    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
        return 0
    except Exception as e:
        logger.critical(f"A fatal error occurred: {e}", exc_info=True)
        # A simple dialog for critical startup errors
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Fatal Application Error",
            secondary_text=f"Could not start Ashy Terminal.\n\nError: {e}",
        )
        dialog.run()
        dialog.destroy()
        return 1

if __name__ == "__main__":
    sys.exit(main())