#!/usr/bin/env python3
"""
Ashy Terminal - A modern terminal emulator with session management.

Enhanced entry point with comprehensive error handling, logging, platform detection,
and command line argument processing.
"""

import sys
import os
import argparse
import signal
from pathlib import Path
from typing import List, Optional

# Ensure we can find the package modules
if __package__ is None:
    # Add parent directory to path for relative imports
    import pathlib
    parent_dir = pathlib.Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    
    # Set the package name for relative imports
    __package__ = "ashyterm"

# Import translation utility
from .utils.translation_utils import _

# Import new utility systems first
# Initialize utilities availability flags
UTILS_AVAILABLE = False
get_logger = None
log_app_start = None
log_app_shutdown = None
enable_debug_mode = None
set_console_level = None
LogLevel = None

try:
    from .utils.logger import (
        get_logger, log_app_start, log_app_shutdown, 
        enable_debug_mode, set_console_level, LogLevel
    )
    from .utils.exceptions import (
        AshyTerminalError, VTENotAvailableError, ConfigError,
        handle_exception, ErrorCategory, ErrorSeverity
    )
    from .utils.platform import get_platform_info, is_windows, get_config_directory
    from .utils.crypto import is_encryption_available
    UTILS_AVAILABLE = True
except ImportError as e:
    # Fallback logging if utility modules aren't available
    print(_("Warning: Could not import utility modules: {}").format(e))
    print(_("Starting with basic functionality..."))
    
    # Create fallback functions
    def get_logger(name=None):
        import logging
        return logging.getLogger(name or 'ashyterm')
    
    def log_app_start():
        print(_("Application starting..."))
    
    def log_app_shutdown():
        print(_("Application shutting down..."))
    
    def enable_debug_mode():
        import logging
        logging.basicConfig(level=logging.DEBUG)
    
    def set_console_level(level):
        pass  # No-op fallback
    
    class LogLevel:
        DEBUG = 'DEBUG'
        INFO = 'INFO'
        WARNING = 'WARNING'
        ERROR = 'ERROR'
        CRITICAL = 'CRITICAL'


def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown."""
    def signal_handler(sig, frame):
        print(_("\nReceived signal {}, shutting down gracefully...").format(sig))
        log_app_shutdown()
        sys.exit(0)
    
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        if is_windows():
            signal.signal(signal.SIGBREAK, signal_handler)
    except Exception as e:
        print(_("Warning: Could not set up signal handlers: {}").format(e))


def parse_command_line() -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        prog="ashyterm",
        description=_("Ashy Terminal - A modern terminal emulator with session management"),
        epilog=_("For more information, visit: https://communitybig.org/")
    )
    
    # Version information
    parser.add_argument(
        '--version', '-v',
        action='version',
        version='%(prog)s 1.0.1'
    )
    
    # Debug options
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help=_('Enable debug mode with verbose logging')
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help=_('Set logging level (default: INFO)')
    )
    
    # Configuration options
    parser.add_argument(
        '--config-dir',
        type=str,
        help=_('Specify custom configuration directory')
    )
    
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help=_('Disable automatic backup functionality')
    )
    
    parser.add_argument(
        '--reset-config',
        action='store_true',
        help=_('Reset configuration to defaults')
    )
    
    # Session options
    parser.add_argument(
        '--session', '-s',
        type=str,
        help=_('Open specific session by name')
    )
    
    parser.add_argument(
        '--local', '-l',
        action='store_true',
        help=_('Open local terminal immediately')
    )
    
    # Platform options
    parser.add_argument(
        '--platform-info',
        action='store_true',
        help=_('Show platform information and exit')
    )
    
    parser.add_argument(
        '--check-deps',
        action='store_true',
        help=_('Check dependencies and exit')
    )
    
    return parser.parse_args()


def check_dependencies() -> bool:
    """
    Check system dependencies and return status.
    
    Returns:
        True if all required dependencies are available
    """
    logger = get_logger('ashyterm.main.deps')
    dependencies_ok = True
    
    try:
        # Check GTK4/Adwaita
        import gi
        try:
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Gtk, Adw
            logger.info(_("✓ GTK4/Adwaita available"))
        except (ImportError, ValueError) as e:
            logger.error(_("✗ GTK4/Adwaita not available: {}").format(e))
            dependencies_ok = False
        
        # Check VTE
        try:
            gi.require_version("Vte", "3.91")
            from gi.repository import Vte
            logger.info(_("✓ VTE 3.91 available"))
        except (ImportError, ValueError) as e:
            logger.warning(_("⚠ VTE 3.91 not available: {}").format(e))
            logger.warning(_("Terminal functionality will be limited"))
        
        # Check cryptography (optional)
        if is_encryption_available():
            logger.info(_("✓ Cryptography library available"))
        else:
            logger.warning(_("⚠ Cryptography library not available - passwords will be stored as plain text"))
        
        # Check platform-specific dependencies
        platform_info = get_platform_info()
        logger.info(_("✓ Platform: {}").format(platform_info.platform_type.value))
        
        if platform_info.has_command('ssh'):
            logger.info(_("✓ SSH command available"))
        else:
            logger.warning(_("⚠ SSH command not found - SSH functionality will be limited"))
        
        if platform_info.has_command('sshpass'):
            logger.info(_("✓ sshpass available"))
        else:
            logger.info(_("ℹ sshpass not available - password SSH will require manual input"))
        
        return dependencies_ok
        
    except Exception as e:
        logger.error(_("Dependency check failed: {}").format(e))
        return False


def show_platform_info():
    """Show detailed platform information."""
    try:
        from .utils.platform import get_platform_info
        from .utils.logger import get_log_info
        
        platform_info = get_platform_info()
        log_info = get_log_info()
        
        print(_("=== Ashy Terminal Platform Information ==="))
        print()
        
        print(_("Platform:"))
        print(_("  Type: {}").format(platform_info.platform_type.value))
        print(_("  System: {}").format(platform_info.system_name))
        print(_("  Release: {}").format(platform_info.platform_release))
        print(_("  Architecture: {}").format(platform_info.architecture))
        print(_("  64-bit: {}").format(platform_info.is_64bit))
        print()
        
        print(_("Paths:"))
        print(_("  Home: {}").format(platform_info.home_dir))
        print(_("  Config: {}").format(platform_info.config_dir))
        print(_("  SSH: {}").format(platform_info.ssh_dir))
        print(_("  Cache: {}").format(platform_info.cache_dir))
        print(_("  Logs: {}").format(log_info.get('log_dir', 'Unknown')))
        print()
        
        print(_("Shell:"))
        print(_("  Default: {}").format(platform_info.default_shell))
        print(_("  Available: {}").format([shell[1] for shell in platform_info.available_shells]))
        print()
        
        print(_("Commands:"))
        important_commands = ['ssh', 'sshpass', 'git', 'vim', 'nano']
        for cmd in important_commands:
            status = "✓" if platform_info.has_command(cmd) else "✗"
            path = platform_info.get_command_path(cmd) or _("Not found")
            print(f"  {cmd}: {status} {path}")
        print()
        
        print(_("Encryption:"))
        print(_("  Available: {}").format('✓' if is_encryption_available() else '✗'))
        print(_("  Initialized: {}").format('✓' if is_encryption_available() else '✗'))
        print()
        
    except Exception as e:
        print(_("Error showing platform info: {}").format(e))


def reset_configuration():
    """Reset configuration to defaults."""
    try:
        logger = get_logger('ashyterm.main.reset')
        logger.info(_("Resetting configuration to defaults"))
        
        config_dir = get_config_directory()
        
        # List files to reset
        config_files = [
            config_dir / "settings.json",
            config_dir / "sessions.json",
        ]
        
        backup_files = []
        for config_file in config_files:
            if config_file.exists():
                backup_file = config_file.with_suffix(f"{config_file.suffix}.backup")
                config_file.rename(backup_file)
                backup_files.append(backup_file)
                logger.info(_("Backed up {} to {}").format(config_file, backup_file))
        
        if backup_files:
            print(_("Configuration reset complete. Backup files created:"))
            for backup_file in backup_files:
                print(f"  {backup_file}")
        else:
            print(_("No configuration files found to reset."))
        
        return True
        
    except Exception as e:
        logger.error(_("Configuration reset failed: {}").format(e))
        print(_("Error resetting configuration: {}").format(e))
        return False


def main() -> int:
    """
    Enhanced main entry point for the application.
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Parse command line arguments first
    try:
        args = parse_command_line()
    except SystemExit as e:
        return e.code
    except Exception as e:
        print(_("Error parsing command line: {}").format(e))
        return 1
    
    # Set up basic logging
    try:
        if args.debug:
            enable_debug_mode()
        else:
            set_console_level(LogLevel[args.log_level])
        
        logger = get_logger('ashyterm.main')
        logger.info(_("Ashy Terminal starting up"))
        
    except Exception as e:
        print(_("Error setting up logging: {}").format(e))
        return 1
    
    # Set up signal handlers
    setup_signal_handlers()
    
    try:
        # Handle special commands first
        if args.platform_info:
            show_platform_info()
            return 0
        
        if args.check_deps:
            success = check_dependencies()
            return 0 if success else 1
        
        if args.reset_config:
            success = reset_configuration()
            return 0 if success else 1
        
        # Check dependencies before starting GUI
        logger.info(_("Checking system dependencies"))
        if not check_dependencies():
            logger.error(_("Critical dependencies missing"))
            print(_("Error: Required dependencies are missing. Use --check-deps for details."))
            return 1
        
        # Import and run the application
        logger.info(_("Starting application"))
        log_app_start()
        
        from .app import main as app_main
        exit_code = app_main()
        
        logger.info(_("Application exited with code: {}").format(exit_code))
        log_app_shutdown()
        
        return exit_code
        
    except KeyboardInterrupt:
        logger.info(_("Application interrupted by user"))
        log_app_shutdown()
        return 0
        
    except VTENotAvailableError:
        logger.critical(_("VTE library not available"))
        print(_("Error: VTE library is required but not available."))
        print(_("Please install gir1.2-vte-2.91 package."))
        return 1
        
    except ConfigError as e:
        logger.critical(_("Configuration error: {}").format(e.user_message))
        print(_("Configuration Error: {}").format(e.user_message))
        return 1
        
    except AshyTerminalError as e:
        logger.critical(_("Application error: {}").format(e.user_message))
        print(_("Error: {}").format(e.user_message))
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
        
    except ImportError as e:
        logger.critical(_("Import error: {}").format(e))
        print(_("Import Error: {}").format(e))
        print(_("Make sure all required dependencies are installed."))
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
        
    except Exception as e:
        logger.critical(_("Unhandled exception: {}").format(e))
        print(_("Unhandled Error: {}").format(e))
        
        if args.debug:
            import traceback
            traceback.print_exc()
        else:
            print(_("Use --debug for detailed error information."))
        
        return 1


def run_standalone():
    """
    Run the application in standalone mode.
    This function is used when the script is executed directly.
    """
    try:
        exit_code = main()
        sys.exit(exit_code)
    except SystemExit:
        # Allow normal sys.exit() calls to pass through
        raise
    except Exception as e:
        print(_("Fatal error: {}").format(e))
        sys.exit(1)


if __name__ == "__main__":
    run_standalone()
