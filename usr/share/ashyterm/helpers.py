"""
Utilities for Ashy Terminal.

This module provides various utility functions that are used throughout the application,
now integrated with the new logging, security, and platform systems.
"""

import os
import uuid
from typing import Optional, Set, List, Dict, Any, Tuple, Union
from pathlib import Path
from gi.repository import Gtk, Gdk, GLib

# Import new utility modules
from .utils.logger import get_logger, log_error_with_context
from .utils.exceptions import (
    ValidationError, PathValidationError, SSHKeyError,
    handle_exception, create_error_from_exception, ErrorCategory, ErrorSeverity
)
from .utils.security import (
    sanitize_session_name, sanitize_folder_name, validate_ssh_key_file,
    validate_file_path, SSHKeyValidator, HostnameValidator, InputSanitizer
)
from .utils.platform import (
    get_platform_info, normalize_path, has_command, is_unix_like,
    get_ssh_directory
)
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
    logger = get_logger('ashyterm.utils')
    
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


def generate_unique_id() -> str:
    """
    Generate a unique identifier.
    
    Returns:
        A unique string identifier
    """
    return str(uuid.uuid4())


def validate_ssh_key_path(key_path: str) -> bool:
    """
    Validate if an SSH key path exists and is readable.
    
    Args:
        key_path: Path to the SSH key file
        
    Returns:
        True if the key is valid and accessible
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        validate_ssh_key_file(key_path)
        return True
    except SSHKeyError as e:
        logger.debug(f"SSH key validation failed for '{key_path}': {e.user_message}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error validating SSH key '{key_path}': {e}")
        return False


def get_default_ssh_key_paths() -> List[str]:
    """
    Get list of common SSH key paths in user's home directory.
    
    Returns:
        List of potential SSH key file paths
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        # Use platform-aware SSH directory detection
        ssh_dir = get_ssh_directory()
        
        if not ssh_dir.exists():
            logger.debug(f"SSH directory does not exist: {ssh_dir}")
            return []
        
        common_key_names = [
            "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
            "id_rsa_legacy", "github_rsa", "gitlab_rsa",
            "bitbucket_rsa", "aws_rsa", "gcp_rsa"
        ]
        
        found_keys = []
        for key_name in common_key_names:
            key_path = ssh_dir / key_name
            if key_path.exists() and key_path.is_file():
                # Validate the key before adding to list
                is_valid, error = SSHKeyValidator.validate_ssh_key_path(str(key_path))
                if is_valid:
                    found_keys.append(str(key_path))
                else:
                    logger.debug(f"Invalid SSH key found at {key_path}: {error}")
        
        logger.debug(f"Found {len(found_keys)} valid SSH keys")
        return found_keys
        
    except Exception as e:
        logger.error(f"Error searching for SSH keys: {e}")
        return []


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing or replacing invalid characters.
    
    Args:
        filename: The filename to sanitize
        
    Returns:
        A sanitized filename safe for filesystem use
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        # Use the new security module for sanitization
        sanitized = InputSanitizer.sanitize_filename(filename)
        
        if sanitized != filename:
            logger.debug(f"Filename sanitized: '{filename}' -> '{sanitized}'")
        
        return sanitized
        
    except Exception as e:
        logger.error(f"Error sanitizing filename '{filename}': {e}")
        # Fallback to basic sanitization
        if not filename:
            return _("unnamed")
        
        invalid_chars = '<>:"/\\|?*'
        sanitized = filename
        
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '_')
        
        sanitized = sanitized.strip(' .')
        
        if not sanitized:
            sanitized = _("unnamed")
        
        return sanitized


def parse_accelerator_safely(accel_string: str) -> Optional[Tuple[int, Gdk.ModifierType]]:
    """
    Safely parse a GTK accelerator string.
    
    Args:
        accel_string: Accelerator string (e.g., "<Control>t")
        
    Returns:
        Tuple of (keyval, modifiers) or None if parsing fails
    """
    logger = get_logger('ashyterm.utils')
    
    if not accel_string:
        return None
    
    try:
        # Use the recommended Gtk.accelerator_parse
        success, keyval, mods = Gtk.accelerator_parse(accel_string)
        if success and keyval != 0:
            return keyval, mods
    except (GLib.Error, ValueError, TypeError) as e:
        logger.debug(f"Failed to parse accelerator '{accel_string}': {e}")

    logger.warning(f"Could not parse accelerator string: '{accel_string}'")
    return None

def _manual_accelerator_conversion(accelerator: str) -> str:
    """
    Manual conversion of accelerator string to label.
    
    Args:
        accelerator: GTK accelerator string
        
    Returns:
        Human-readable label
    """
    if not accelerator:
        return ""
    
    # Remove angle brackets and split by modifiers
    clean_accel = accelerator.replace('<', '').replace('>', '+')
    
    # Replace common modifier names
    replacements = {
        'Control': 'Ctrl',
        'Shift': 'Shift',
        'Alt': 'Alt',
        'Super': 'Super',
        'Meta': 'Meta'
    }
    
    # Apply replacements
    result = clean_accel
    for old, new in replacements.items():
        result = result.replace(old, new)
    
    # Handle common key names
    key_replacements = {
        'plus': '+',
        'minus': '-',
        'Return': 'Enter',
        'BackSpace': 'Backspace',
        'Delete': 'Del',
        'Insert': 'Ins',
        'space': 'Space',
        'Tab': 'Tab',
        'Escape': 'Esc',
        'comma': ',',
        'period': '.',
        'slash': '/',
        'backslash': '\\',
        'semicolon': ';',
        'apostrophe': "'",
        'grave': '`',
        'bracketleft': '[',
        'bracketright': ']',
        'equal': '=',
    }
    
    # Apply key replacements to the last part (the actual key)
    parts = result.split('+')
    if parts:
        last_part = parts[-1].lower()
        for old_key, new_key in key_replacements.items():
            if last_part == old_key:
                parts[-1] = new_key
                break
        else:
            # Capitalize single letters
            if len(parts[-1]) == 1:
                parts[-1] = parts[-1].upper()
    
    return '+'.join(parts)

def accelerator_to_label(accelerator: str) -> str:
    """
    Convert GTK accelerator string to human-readable label.
    
    Args:
        accelerator: GTK accelerator string (e.g., "<Control>t", "<Control><Shift>c")
        
    Returns:
        Human-readable label (e.g., "Ctrl+T", "Ctrl+Shift+C")
    """
    if not accelerator:
        return ""
    
    try:
        # Parse the accelerator
        success, keyval, mods = Gtk.accelerator_parse(accelerator)
        
        if not success or keyval == 0:
            return accelerator  # Return original if parsing fails
        
        # Get the key name
        key_name = Gtk.accelerator_get_label(keyval, mods)
        
        if key_name:
            return key_name
        else:
            # Fallback to manual conversion
            return _manual_accelerator_conversion(accelerator)
            
    except Exception:
        # Fallback to manual conversion if GTK methods fail
        return _manual_accelerator_conversion(accelerator)


def normalize_path_safe(path: str) -> str:
    """
    Normalize a path string for consistent comparison with safety checks.
    
    Args:
        path: Path string to normalize
        
    Returns:
        Normalized path string
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        if not path:
            return ""
        
        # Use platform-aware path normalization
        normalized_path = normalize_path(path)
        
        # Convert back to string and ensure it starts with / for absolute folder paths
        path_str = str(normalized_path)
        
        if path_str != "." and not path_str.startswith("/") and not os.path.isabs(path_str):
            path_str = "/" + path_str
        
        # Handle the root case
        if path_str == "/.":
            path_str = "/"
        
        logger.debug(f"Path normalized: '{path}' -> '{path_str}'")
        return path_str
        
    except Exception as e:
        logger.error(f"Error normalizing path '{path}': {e}")
        
        # Fallback to basic normalization
        if not path:
            return ""
        
        normalized = os.path.normpath(path)
        
        if normalized != "." and not normalized.startswith("/"):
            normalized = "/" + normalized
        
        if normalized == "/.":
            normalized = "/"
        
        return normalized


def is_valid_hostname(hostname: str) -> bool:
    """
    Basic validation for hostname format using enhanced security validation.
    
    Args:
        hostname: Hostname string to validate
        
    Returns:
        True if hostname appears to be valid
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        return HostnameValidator.is_valid_hostname(hostname)
    except Exception as e:
        logger.error(f"Error validating hostname '{hostname}': {e}")
        return False


def ensure_directory_exists(directory_path: str) -> bool:
    """
    Ensure a directory exists, creating it if necessary with proper permissions.
    
    Args:
        directory_path: Path to the directory
        
    Returns:
        True if directory exists or was created successfully
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        directory = Path(directory_path)
        directory.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Directory ensured: {directory_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error ensuring directory '{directory_path}': {e}")
        return False


def find_executable(command: str) -> Optional[str]:
    """
    Find the full path to an executable command using platform-aware detection.
    
    Args:
        command: Command name to find
        
    Returns:
        Full path to executable or None if not found
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        platform_info = get_platform_info()
        
        if platform_info.has_command(command):
            cmd_path = platform_info.get_command_path(command)
            logger.debug(f"Command found: {command} -> {cmd_path}")
            return cmd_path
        
        # Fallback to shutil.which
        import shutil
        cmd_path = shutil.which(command)
        if cmd_path:
            logger.debug(f"Command found (fallback): {command} -> {cmd_path}")
        else:
            logger.debug(f"Command not found: {command}")
        
        return cmd_path
        
    except Exception as e:
        logger.error(f"Error finding executable '{command}': {e}")
        
        # Final fallback
        import shutil
        return shutil.which(command)


def is_sshpass_available() -> bool:
    """
    Check if sshpass utility is available on the system.
    
    Returns:
        True if sshpass is available
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        available = has_command('sshpass')
        logger.debug(f"sshpass availability: {available}")
        return available
    except Exception as e:
        logger.error(f"Error checking sshpass availability: {e}")
        return False


def validate_session_data(session_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate session data for security and correctness.
    
    Args:
        session_data: Session configuration dictionary
        
    Returns:
        Tuple of (is_valid, error_messages)
    """
    logger = get_logger('ashyterm.utils')
    errors = []
    
    try:
        from .utils.security import SecurityAuditor
        auditor = SecurityAuditor()
        
        # Perform security audit
        findings = auditor.audit_ssh_session(session_data)
        
        # Convert high/critical findings to errors
        for finding in findings:
            if finding['severity'] in ['high', 'critical']:
                errors.append(finding['message'])
            elif finding['severity'] == 'medium':
                logger.warning(f"Session validation warning: {finding['message']}")
        
        # Additional basic validation
        session_name = session_data.get('name', '')
        if not session_name or not session_name.strip():
            errors.append(_("Session name cannot be empty"))
        
        session_type = session_data.get('session_type', '')
        if session_type not in ['local', 'ssh']:
            errors.append(_("Invalid session type: {}").format(session_type))
        
        if session_type == 'ssh':
            host = session_data.get('host', '')
            if not host or not host.strip():
                errors.append(_("SSH host cannot be empty"))
            elif not is_valid_hostname(host.strip()):
                errors.append(_("Invalid hostname format: {}").format(host))
        
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.debug(f"Session validation passed for: {session_name}")
        else:
            logger.warning(f"Session validation failed for: {session_name}, errors: {errors}")
        
        return is_valid, errors
        
    except Exception as e:
        error_msg = _("Error during session validation: {}").format(e)
        logger.error(error_msg)
        errors.append(error_msg)
        return False, errors


def get_system_info() -> Dict[str, Any]:
    """
    Get comprehensive system information for debugging and logging.
    
    Returns:
        Dictionary with system information
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        platform_info = get_platform_info()
        
        info = {
            'platform': {
                'type': platform_info.platform_type.value,
                'system': platform_info.system_name,
                'release': platform_info.platform_release,
                'architecture': platform_info.architecture,
                'is_64bit': platform_info.is_64bit
            },
            'shell': {
                'default': platform_info.default_shell,
                'available': [shell[1] for shell in platform_info.available_shells]
            },
            'paths': {
                'home': str(platform_info.home_dir),
                'config': str(platform_info.config_dir),
                'ssh': str(platform_info.ssh_dir),
                'temp': str(platform_info.temp_dir)
            },
            'commands': {
                'ssh': platform_info.has_command('ssh'),
                'sshpass': platform_info.has_command('sshpass'),
                'git': platform_info.has_command('git')
            }
        }
        
        logger.debug("System information collected successfully")
        return info
        
    except Exception as e:
        logger.error(f"Error collecting system information: {e}")
        return {
            'platform': {'type': _('unknown')},
            'error': str(e)
        }


def setup_error_handling():
    """
    Set up global error handling for the application.
    """
    logger = get_logger('ashyterm.utils')
    
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
                exc_info=(exc_type, exc_value, exc_traceback)
            )
        
        # Set global exception handler
        sys.excepthook = handle_exception
        
        logger.info(_("Global error handling configured"))
        
    except Exception as e:
        logger.error(f"Failed to setup error handling: {e}")


def create_safe_filename_from_session(session_name: str, session_type: str = "") -> str:
    """
    Create a safe filename from session information.
    
    Args:
        session_name: Name of the session
        session_type: Type of session (optional)
        
    Returns:
        Safe filename string
    """
    logger = get_logger('ashyterm.utils')
    
    try:
        # Sanitize the session name
        safe_name = sanitize_filename(session_name)
        
        # Add session type if provided
        if session_type:
            safe_type = sanitize_filename(session_type)
            safe_name = f"{safe_name}_{safe_type}"
        
        # Ensure reasonable length
        if len(safe_name) > 100:
            safe_name = safe_name[:100]
        
        logger.debug(f"Safe filename created: '{session_name}' -> '{safe_name}'")
        return safe_name
        
    except Exception as e:
        logger.error(f"Error creating safe filename from session '{session_name}': {e}")
        return _("unknown_session")


# Legacy compatibility functions (deprecated but maintained for compatibility)
def generate_unique_name_legacy(base_name: str, existing_names: Set[str]) -> str:
    """
    Legacy version of generate_unique_name (deprecated).
    Use generate_unique_name() instead.
    """
    logger = get_logger('ashyterm.utils')
    logger.warning(_("Using deprecated generate_unique_name_legacy(). Use generate_unique_name() instead."))
    return generate_unique_name(base_name, existing_names)


# Module initialization
def initialize_utils():
    """Initialize the utils module with enhanced functionality."""
    logger = get_logger('ashyterm.utils')
    
    try:
        # Set up error handling
        setup_error_handling()
        
        # Log system information
        system_info = get_system_info()
        logger.info(_("Utils module initialized on {} platform").format(system_info['platform']['type']))
        
        # Check for required dependencies
        if not has_command('ssh'):
            logger.warning(_("SSH command not found - SSH functionality will be limited"))
        
        if not is_sshpass_available():
            logger.info(_("sshpass not available - password SSH authentication will require manual input"))
        
        logger.info(_("Utils module initialization completed"))
        
    except Exception as e:
        logger.error(f"Error during utils module initialization: {e}")


# Auto-initialize when module is imported
try:
    initialize_utils()
except Exception as e:
    # Use basic print as logger might not be available yet
    print(_("Warning: Utils module initialization failed: {}").format(e))