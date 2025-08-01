"""
Utility modules for Ashy Terminal.

This package provides comprehensive utility systems for logging, security,
platform compatibility, backup, encryption, and exception handling.
"""

# Import translation utility
from .translation_utils import _

# Package metadata
__version__ = "1.0.1"

# Export main utilities for easy access
__all__ = [
    # Core systems
    "get_logger",
    "get_platform_info", 
    "create_security_auditor",
    "get_backup_manager",
    "is_encryption_available",
    
    # Exception classes
    "AshyTerminalError",
    "ConfigError",
    "ValidationError",
    "handle_exception",
]

# Import and re-export commonly used functions
try:
    from .logger import get_logger
    from .platform import get_platform_info
    from .security import create_security_auditor
    from .backup import get_backup_manager
    from .crypto import is_encryption_available
    from .exceptions import (
        AshyTerminalError, ConfigError, ValidationError, handle_exception
    )
except ImportError:
    # Fallback if some modules aren't available
    pass


def generate_unique_name(base_name: str = "Item", existing_names: list = None) -> str:
    """
    Generate a unique name by appending a number if necessary.
    
    Args:
        base_name: Base name to use
        existing_names: List of existing names to avoid conflicts
        
    Returns:
        Unique name string
    """
    if existing_names is None:
        existing_names = []
    
    # Convert to set for faster lookup
    existing_set = set(existing_names)
    
    # If base name is not in use, return it
    if base_name not in existing_set:
        return base_name
    
    # Try appending numbers until we find a unique name
    counter = 1
    while True:
        candidate = f"{base_name} {counter}"
        if candidate not in existing_set:
            return candidate
        counter += 1


def sanitize_name(name: str, max_length: int = 128) -> str:
    """
    Sanitize a name for safe use in filenames and UI.
    
    Args:
        name: Name to sanitize
        max_length: Maximum length for the name
        
    Returns:
        Sanitized name string
    """
    if not name:
        return _("unnamed")
    
    # Remove forbidden characters
    forbidden_chars = '<>:"/\\|?*\0'
    sanitized = name
    
    for char in forbidden_chars:
        sanitized = sanitized.replace(char, '_')
    
    # Remove control characters
    sanitized = ''.join(char for char in sanitized if ord(char) >= 32)
    
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')
    
    # Ensure not empty
    if not sanitized:
        sanitized = _("unnamed")
    
    # Limit length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip()
    
    return sanitized


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
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        
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


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted size string (e.g., "1.5 MB", "2.3 KB")
    """
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def format_duration(seconds: float) -> str:
    """
    Format duration in human-readable format.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted duration string (e.g., "2m 30s", "1h 15m")
    """
    if seconds < 0:
        return "0s"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        if minutes > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{hours}h"
    elif minutes > 0:
        if secs > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{minutes}m"
    else:
        return f"{secs}s"


# Add the new functions to __all__
__all__.extend([
    "generate_unique_name",
    "sanitize_name",
    "accelerator_to_label",
    "format_file_size",
    "format_duration"
])
