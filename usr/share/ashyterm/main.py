#!/usr/bin/env python3
"""
Ashy Terminal - A modern terminal emulator with session management.

Enhanced entry point with comprehensive error handling, logging, platform detection,
and command line argument processing.
"""

import os
import sys
import argparse
import signal
from typing import Optional

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
        nargs='?',
        default=None,
        help=_('Working directory (positional argument)')
    )
    return parser.parse_args()


def _resolve_working_directory(working_dir: str) -> Optional[str]:
    """
    Resolve and validate working directory path.
    
    Args:
        working_dir: Raw working directory path
        
    Returns:
        Resolved absolute path or None if invalid
    """
    if not working_dir:
        return None
    
    try:
        # Expand user home directory and environment variables
        expanded_path = os.path.expanduser(os.path.expandvars(working_dir))
        
        # Convert to absolute path
        resolved_path = os.path.abspath(expanded_path)
        
        # Validate that directory exists and is accessible
        if os.path.isdir(resolved_path) and os.access(resolved_path, os.R_OK | os.X_OK):
            return resolved_path
        else:
            print(f"Warning: Directory '{working_dir}' is not accessible. Using default.")
            return None
            
    except Exception as e:
        print(f"Warning: Invalid working directory '{working_dir}': {e}. Using default.")
        return None


def _filter_argv_for_gtk(argv: list, working_directory: Optional[str]) -> list:
    """
    Filter command line arguments to remove custom arguments before passing to GTK.
    
    Args:
        argv: Original command line arguments
        working_directory: Working directory to exclude from filtered args
        
    Returns:
        Filtered argument list safe for GTK
    """
    filtered_argv = [argv[0]]  # Keep program name
    
    skip_next = False
    for i, arg in enumerate(argv[1:], 1):
        if skip_next:
            skip_next = False
            continue
            
        # Skip our custom arguments
        if arg in ['--debug', '-d']:
            continue
        elif arg.startswith('--log-level'):
            if '=' not in arg and i + 1 < len(argv):
                skip_next = True  # Skip next argument too
            continue
        elif arg in ['--working-directory', '-w']:
            skip_next = True  # Skip next argument (the directory)
            continue
        elif arg.startswith('--working-directory='):
            continue
        elif working_directory and arg == working_directory:
            continue  # Skip positional directory argument
        else:
            filtered_argv.append(arg)
    
    return filtered_argv


def main() -> int:
    """Main entry point for the application."""
    # Parse arguments BEFORE creating app to avoid GTK conflicts
    args = parse_command_line()

    if args.debug:
        enable_debug_mode()
    else:
        set_console_level(LogLevel[args.log_level])

    logger = get_logger("ashyterm.main")
    setup_signal_handlers()

    try:
        # Resolve working directory with proper validation
        working_directory = args.working_directory or args.directory
        resolved_working_dir = _resolve_working_directory(working_directory)
        
        if working_directory and resolved_working_dir:
            logger.info(f"Initial working directory resolved: {resolved_working_dir}")
        elif working_directory and not resolved_working_dir:
            logger.warning(f"Invalid working directory specified: {working_directory}")
        
        logger.info("Creating application instance")
        app = CommTerminalApp()
        
        # Set working directory on app BEFORE run()
        if resolved_working_dir:
            app.initial_working_directory = resolved_working_dir
            logger.info(f"Working directory set on application: {resolved_working_dir}")
        
        logger.info("Running application")
        
        # Create filtered argv without our custom arguments to avoid GTK conflicts
        filtered_argv = _filter_argv_for_gtk(sys.argv, working_directory)
        logger.debug(f"Filtered argv for GTK: {filtered_argv}")
        
        return app.run(filtered_argv)

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