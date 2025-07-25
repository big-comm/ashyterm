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
    print(f"Warning: Could not import utility modules: {e}")
    print("Starting with basic functionality...")
    
    # Create fallback functions
    def get_logger(name=None):
        import logging
        return logging.getLogger(name or 'ashyterm')
    
    def log_app_start():
        print("Application starting...")
    
    def log_app_shutdown():
        print("Application shutting down...")
    
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
        print(f"\nReceived signal {sig}, shutting down gracefully...")
        log_app_shutdown()
        sys.exit(0)
    
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        if is_windows():
            signal.signal(signal.SIGBREAK, signal_handler)
    except Exception as e:
        print(f"Warning: Could not set up signal handlers: {e}")


def parse_command_line() -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        prog="ashyterm",
        description="Ashy Terminal - A modern terminal emulator with session management",
        epilog="For more information, visit: https://communitybig.org/"
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
        help='Enable debug mode with verbose logging'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    
    # Configuration options
    parser.add_argument(
        '--config-dir',
        type=str,
        help='Specify custom configuration directory'
    )
    
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Disable automatic backup functionality'
    )
    
    parser.add_argument(
        '--reset-config',
        action='store_true',
        help='Reset configuration to defaults'
    )
    
    # Session options
    parser.add_argument(
        '--session', '-s',
        type=str,
        help='Open specific session by name'
    )
    
    parser.add_argument(
        '--local', '-l',
        action='store_true',
        help='Open local terminal immediately'
    )
    
    # Platform options
    parser.add_argument(
        '--platform-info',
        action='store_true',
        help='Show platform information and exit'
    )
    
    parser.add_argument(
        '--check-deps',
        action='store_true',
        help='Check dependencies and exit'
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
            logger.info("✓ GTK4/Adwaita available")
        except (ImportError, ValueError) as e:
            logger.error(f"✗ GTK4/Adwaita not available: {e}")
            dependencies_ok = False
        
        # Check VTE
        try:
            gi.require_version("Vte", "3.91")
            from gi.repository import Vte
            logger.info("✓ VTE 3.91 available")
        except (ImportError, ValueError) as e:
            logger.warning(f"⚠ VTE 3.91 not available: {e}")
            logger.warning("Terminal functionality will be limited")
        
        # Check cryptography (optional)
        if is_encryption_available():
            logger.info("✓ Cryptography library available")
        else:
            logger.warning("⚠ Cryptography library not available - passwords will be stored as plain text")
        
        # Check platform-specific dependencies
        platform_info = get_platform_info()
        logger.info(f"✓ Platform: {platform_info.platform_type.value}")
        
        if platform_info.has_command('ssh'):
            logger.info("✓ SSH command available")
        else:
            logger.warning("⚠ SSH command not found - SSH functionality will be limited")
        
        if platform_info.has_command('sshpass'):
            logger.info("✓ sshpass available")
        else:
            logger.info("ℹ sshpass not available - password SSH will require manual input")
        
        return dependencies_ok
        
    except Exception as e:
        logger.error(f"Dependency check failed: {e}")
        return False


def show_platform_info():
    """Show detailed platform information."""
    try:
        from .utils.platform import get_platform_info
        from .utils.logger import get_log_info
        
        platform_info = get_platform_info()
        log_info = get_log_info()
        
        print("=== Ashy Terminal Platform Information ===")
        print()
        
        print("Platform:")
        print(f"  Type: {platform_info.platform_type.value}")
        print(f"  System: {platform_info.system_name}")
        print(f"  Release: {platform_info.platform_release}")
        print(f"  Architecture: {platform_info.architecture}")
        print(f"  64-bit: {platform_info.is_64bit}")
        print()
        
        print("Paths:")
        print(f"  Home: {platform_info.home_dir}")
        print(f"  Config: {platform_info.config_dir}")
        print(f"  SSH: {platform_info.ssh_dir}")
        print(f"  Cache: {platform_info.cache_dir}")
        print(f"  Logs: {log_info.get('log_dir', 'Unknown')}")
        print()
        
        print("Shell:")
        print(f"  Default: {platform_info.default_shell}")
        print(f"  Available: {[shell[1] for shell in platform_info.available_shells]}")
        print()
        
        print("Commands:")
        important_commands = ['ssh', 'sshpass', 'git', 'vim', 'nano']
        for cmd in important_commands:
            status = "✓" if platform_info.has_command(cmd) else "✗"
            path = platform_info.get_command_path(cmd) or "Not found"
            print(f"  {cmd}: {status} {path}")
        print()
        
        print("Encryption:")
        print(f"  Available: {'✓' if is_encryption_available() else '✗'}")
        print(f"  Initialized: {'✓' if is_encryption_available() else '✗'}")
        print()
        
    except Exception as e:
        print(f"Error showing platform info: {e}")


def reset_configuration():
    """Reset configuration to defaults."""
    try:
        logger = get_logger('ashyterm.main.reset')
        logger.info("Resetting configuration to defaults")
        
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
                logger.info(f"Backed up {config_file} to {backup_file}")
        
        if backup_files:
            print(f"Configuration reset complete. Backup files created:")
            for backup_file in backup_files:
                print(f"  {backup_file}")
        else:
            print("No configuration files found to reset.")
        
        return True
        
    except Exception as e:
        logger.error(f"Configuration reset failed: {e}")
        print(f"Error resetting configuration: {e}")
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
        print(f"Error parsing command line: {e}")
        return 1
    
    # Set up basic logging
    try:
        if args.debug:
            enable_debug_mode()
        else:
            set_console_level(LogLevel[args.log_level])
        
        logger = get_logger('ashyterm.main')
        logger.info("Ashy Terminal starting up")
        
    except Exception as e:
        print(f"Error setting up logging: {e}")
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
        logger.info("Checking system dependencies")
        if not check_dependencies():
            logger.error("Critical dependencies missing")
            print("Error: Required dependencies are missing. Use --check-deps for details.")
            return 1
        
        # Import and run the application
        logger.info("Starting application")
        log_app_start()
        
        from .app import main as app_main
        exit_code = app_main()
        
        logger.info(f"Application exited with code: {exit_code}")
        log_app_shutdown()
        
        return exit_code
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        log_app_shutdown()
        return 0
        
    except VTENotAvailableError:
        logger.critical("VTE library not available")
        print("Error: VTE library is required but not available.")
        print("Please install gir1.2-vte-2.91 package.")
        return 1
        
    except ConfigError as e:
        logger.critical(f"Configuration error: {e.user_message}")
        print(f"Configuration Error: {e.user_message}")
        return 1
        
    except AshyTerminalError as e:
        logger.critical(f"Application error: {e.user_message}")
        print(f"Error: {e.user_message}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
        
    except ImportError as e:
        logger.critical(f"Import error: {e}")
        print(f"Import Error: {e}")
        print("Make sure all required dependencies are installed.")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
        
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}")
        print(f"Unhandled Error: {e}")
        
        if args.debug:
            import traceback
            traceback.print_exc()
        else:
            print("Use --debug for detailed error information.")
        
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
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_standalone()