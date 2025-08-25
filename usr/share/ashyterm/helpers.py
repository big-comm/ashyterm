"""
Utilities for Ashy Terminal.

This module provides various utility functions that are used throughout the application,
now integrated with the new logging, security, and platform systems.
"""

from typing import Any, Dict, Optional, Set, Tuple

from gi.repository import Gdk, GLib, Gtk

# Import new utility modules
from .utils.logger import get_logger
from .utils.platform import (
    get_platform_info,
    has_command,
)
from .utils.security import sanitize_session_name
from .utils.translation_utils import _


def generate_unique_name(base_name: str, existing_names: Set[str]) -> str:
    """
    Generate a unique name by appending a number if the base name already exists.

    Args:
        base_name: The desired base name
        existing_names: Set of existing names to avoid

    Returns:
        A unique name that doesn't conflict with existing names
    """
    logger = get_logger("ashyterm.utils")

    try:
        # Sanitize the base name first
        sanitized_base = sanitize_session_name(base_name)

        if sanitized_base not in existing_names:
            return sanitized_base

        counter = 1
        while f"{sanitized_base} ({counter})" in existing_names:
            counter += 1

        unique_name = f"{sanitized_base} ({counter})"
        logger.debug(f"Generated unique name: '{base_name}' -> '{unique_name}'")
        return unique_name

    except Exception as e:
        logger.error(f"Error generating unique name for '{base_name}': {e}")
        # Fallback to basic implementation
        if base_name not in existing_names:
            return base_name

        counter = 1
        while f"{base_name} ({counter})" in existing_names:
            counter += 1

        return f"{base_name} ({counter})"


def parse_accelerator_safely(
    accel_string: str,
) -> Optional[Tuple[int, Gdk.ModifierType]]:
    """
    Safely parse a GTK accelerator string.

    Args:
        accel_string: Accelerator string (e.g., "<Control>t")

    Returns:
        Tuple of (keyval, modifiers) or None if parsing fails
    """
    logger = get_logger("ashyterm.utils")

    if not accel_string:
        return None

    try:
        # Try standard parsing method
        parsed_result = Gtk.accelerator_parse(accel_string)
        if isinstance(parsed_result, tuple) and len(parsed_result) == 2:
            keyval, mods = parsed_result
            if keyval != 0:
                return (keyval, mods)
    except (GLib.Error, ValueError, TypeError) as e:
        logger.debug(
            f"Failed to parse accelerator '{accel_string}' with standard method: {e}"
        )

    try:
        # Try alternative parsing if available
        keyval, mods = Gtk.accelerator_parse_with_keycode(accel_string, None)
        if keyval != 0:
            return (keyval, mods)
    except (GLib.Error, ValueError, TypeError, AttributeError) as e:
        logger.debug(
            f"Failed to parse accelerator '{accel_string}' with alternative method: {e}"
        )

    logger.warning(f"Could not parse accelerator string: '{accel_string}'")
    return None


def accelerator_to_label(accel_string: str) -> str:
    """
    Convert accelerator string to human-readable label.

    Args:
        accel_string: Accelerator string (e.g., "<Control>t")

    Returns:
        Human-readable label (e.g., "Ctrl+T") or escaped string if invalid
    """
    logger = get_logger("ashyterm.utils")

    if not accel_string:
        return _("None")

    parsed = parse_accelerator_safely(accel_string)
    if parsed is None:
        # Escape XML characters to prevent markup errors
        escaped = (
            accel_string.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        )
        logger.debug(f"Returning escaped accelerator string: '{escaped}'")
        return escaped

    keyval, mods = parsed
    try:
        label = Gtk.accelerator_get_label(keyval, mods)
        # Ensure the result doesn't contain XML markup
        escaped_label = (
            label.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        )
        return escaped_label
    except (GLib.Error, TypeError) as e:
        logger.debug(
            f"Failed to get accelerator label for keyval={keyval}, mods={mods}: {e}"
        )
        # Escape XML characters to prevent markup errors
        escaped = (
            accel_string.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        )
        return escaped


def is_sshpass_available() -> bool:
    """
    Check if sshpass utility is available on the system.

    Returns:
        True if sshpass is available
    """
    logger = get_logger("ashyterm.utils")

    try:
        available = has_command("sshpass")
        logger.debug(f"sshpass availability: {available}")
        return available
    except Exception as e:
        logger.error(f"Error checking sshpass availability: {e}")
        return False


def get_system_info() -> Dict[str, Any]:
    """
    Get comprehensive system information for debugging and logging.

    Returns:
        Dictionary with system information
    """
    logger = get_logger("ashyterm.utils")

    try:
        platform_info = get_platform_info()

        info = {
            "platform": {
                "type": platform_info.platform_type.value,
                "system": platform_info.system_name,
                "release": platform_info.platform_release,
                "architecture": platform_info.architecture,
                "is_64bit": platform_info.is_64bit,
            },
            "shell": {
                "default": platform_info.default_shell,
                "available": [shell[1] for shell in platform_info.available_shells],
            },
            "paths": {
                "home": str(platform_info.home_dir),
                "config": str(platform_info.config_dir),
                "ssh": str(platform_info.ssh_dir),
                "temp": str(platform_info.temp_dir),
            },
            "commands": {
                "ssh": platform_info.has_command("ssh"),
                "sshpass": platform_info.has_command("sshpass"),
                "git": platform_info.has_command("git"),
            },
        }

        logger.debug("System information collected successfully")
        return info

    except Exception as e:
        logger.error(f"Error collecting system information: {e}")
        return {"platform": {"type": _("unknown")}, "error": str(e)}


def setup_error_handling():
    """
    Set up global error handling for the application.
    """
    logger = get_logger("ashyterm.utils")

    try:
        import sys

        def handle_exception(exc_type, exc_value, exc_traceback):
            """Global exception handler."""
            if issubclass(exc_type, KeyboardInterrupt):
                # Allow keyboard interrupts to pass through
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return

            logger.critical(
                f"Uncaught exception: {exc_type.__name__}: {exc_value}",
                exc_info=(exc_type, exc_value, exc_traceback),
            )

        # Set global exception handler
        sys.excepthook = handle_exception

        logger.info(_("Global error handling configured"))

    except Exception as e:
        logger.error(f"Failed to setup error handling: {e}")


# Module initialization
def initialize_utils():
    """Initialize the utils module with enhanced functionality."""
    logger = get_logger("ashyterm.utils")

    try:
        # Set up error handling
        setup_error_handling()

        # Log system information
        system_info = get_system_info()
        logger.info(
            _("Utils module initialized on {} platform").format(
                system_info["platform"]["type"]
            )
        )

        # Check for required dependencies
        if not has_command("ssh"):
            logger.warning(
                _("SSH command not found - SSH functionality will be limited")
            )

    except Exception as e:
        logger.error(f"Error during utils module initialization: {e}")


# Auto-initialize when module is imported
try:
    initialize_utils()
except Exception as e:
    # Use basic print as logger might not be available yet
    print(_("Warning: Utils module initialization failed: {}").format(e))
