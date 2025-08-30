# ashyterm/helpers.py

from typing import Set

from gi.repository import Gtk

from .utils.logger import get_logger
from .utils.security import sanitize_session_name


def generate_unique_name(base_name: str, existing_names: Set[str]) -> str:
    """
    Generate a unique name by appending a number if the base name already exists.

    Args:
        base_name: The desired base name
        existing_names: Set of existing names to avoid

    Returns:
        A unique name that doesn't conflict with existing names
    """
    logger = get_logger("ashyterm.helpers")
    try:
        sanitized_base = sanitize_session_name(base_name)
        if sanitized_base not in existing_names:
            return sanitized_base
        counter = 1
        while f"{sanitized_base} ({counter})" in existing_names:
            counter += 1
        return f"{sanitized_base} ({counter})"
    except Exception as e:
        logger.error(f"Error generating unique name for '{base_name}': {e}")
        # Fallback to a simpler logic in case of unexpected errors
        if base_name not in existing_names:
            return base_name
        counter = 1
        while f"{base_name} ({counter})" in existing_names:
            counter += 1
        return f"{base_name} ({counter})"


def accelerator_to_label(accelerator: str) -> str:
    """Convert GTK accelerator string to a human-readable label."""
    if not accelerator:
        return ""

    def _manual_conversion(accel_str: str) -> str:
        """Manual conversion for robustness."""
        clean_accel = accel_str.replace("<", "").replace(">", "+")
        replacements = {
            "Control": "Ctrl",
            "Shift": "Shift",
            "Alt": "Alt",
            "Super": "Super",
            "Meta": "Meta",
        }
        result = clean_accel
        for old, new in replacements.items():
            result = result.replace(old, new)

        key_replacements = {
            "plus": "+",
            "minus": "-",
            "Return": "Enter",
            "BackSpace": "Backspace",
            "Delete": "Del",
            "Insert": "Ins",
            "space": "Space",
            "Tab": "Tab",
            "Escape": "Esc",
            "comma": ",",
            "period": ".",
            "slash": "/",
            "backslash": "\\",
            "semicolon": ";",
            "apostrophe": "'",
            "grave": "`",
            "bracketleft": "[",
            "bracketright": "]",
            "equal": "=",
        }
        parts = result.split("+")
        if parts:
            last_part = parts[-1].lower()
            for old_key, new_key in key_replacements.items():
                if last_part == old_key:
                    parts[-1] = new_key
                    break
            else:
                if len(parts[-1]) == 1:
                    parts[-1] = parts[-1].upper()
        return "+".join(parts)

    try:
        success, keyval, mods = Gtk.accelerator_parse(accelerator)
        if not success or keyval == 0:
            return _manual_conversion(accelerator)

        key_name = Gtk.accelerator_get_label(keyval, mods)
        return key_name if key_name else _manual_conversion(accelerator)
    except Exception:
        return _manual_conversion(accelerator)
