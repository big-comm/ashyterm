# ashyterm/settings/validator.py
"""Value-level validators for the settings dictionary.

The :class:`SettingsManager` stores a flat ``dict`` of settings read
from JSON. Deciding whether a given value is well-formed is pure —
it doesn't touch GTK state or the live app — so the checks live here.

The class keeps its original name (``SettingsValidator``) and public
method signatures so existing callers see no change after the move.
"""

from __future__ import annotations

from typing import Any, Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango

from ..utils.logger import get_logger
from .scrolling import SCROLL_MODES


# Boolean flags the validator enforces strictly — anything that lands
# here as a non-bool is rejected even if Python would coerce it happily.
_BOOLEAN_SETTINGS = (
    "sidebar_visible",
    "auto_hide_sidebar",
    "scroll_on_output",
    "scroll_on_keystroke",
    "osc52_clipboard_enabled",
    "mouse_autohide",
    "bell_sound",
    "file_transfer_accelerated_downloads",
    "log_to_file",
    "ai_assistant_enabled",
)


class SettingsValidator:
    """Validates settings values and overall structure."""

    def __init__(self) -> None:
        self.logger = get_logger("ashyterm.settings.validator")

    # ── scalar validators ────────────────────────────────────

    def validate_color_scheme(self, value: Any, num_schemes: int) -> bool:
        """Color scheme must be an int index into the known scheme list."""
        if not isinstance(value, int):
            return False
        return 0 <= value < num_schemes

    def validate_transparency(self, value: Any) -> bool:
        """Transparency must be a number in the 0–100 UI range."""
        if not isinstance(value, (int, float)):
            return False
        return 0 <= value <= 100

    def validate_font(self, value: Any) -> bool:
        """Font must be a non-empty Pango font description that parses."""
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            return Pango.FontDescription.from_string(value).get_family() is not None
        except Exception:
            return False

    def validate_shortcut(self, value: Any) -> bool:
        """Shortcut must be an empty string or a parseable GTK accelerator."""
        if not isinstance(value, str):
            return False
        if not value:
            return True
        try:
            success, _, _ = Gtk.accelerator_parse(value)
            return success
        except Exception as e:
            self.logger.debug(f"Shortcut validation failed for '{value}': {e}")
            return False

    def validate_terminal_scroll_mode(self, value: Any) -> bool:
        """Terminal scroll mode must select one supported event route."""
        return isinstance(value, str) and value in SCROLL_MODES

    # ── aggregate validators ─────────────────────────────────

    def validate_shortcuts(self, shortcuts: Dict[str, str]) -> List[str]:
        """Validate the ``shortcuts`` dict.

        Flags: non-dict, duplicates across actions, non-string action
        keys, and any shortcut value that fails
        :meth:`validate_shortcut`.
        """
        errors: List[str] = []
        if not isinstance(shortcuts, dict):
            errors.append("Shortcuts must be a dictionary")
            return errors
        shortcut_values = [v for v in shortcuts.values() if v]
        if len(shortcut_values) != len(set(shortcut_values)):
            errors.append("Duplicate keyboard shortcuts detected")
        for action, shortcut in shortcuts.items():
            if not isinstance(action, str):
                errors.append(f"Invalid action name: {action}")
                continue
            if not self.validate_shortcut(shortcut):
                errors.append(
                    f"Invalid shortcut for action '{action}': {shortcut}"
                )
        return errors

    def validate_settings_structure(
        self, settings: Dict[str, Any], num_schemes: int
    ) -> List[str]:
        """Check the whole settings dict and return a list of human-readable errors.

        Empty list ⇒ settings are acceptable. A non-empty list is the
        signal to kick off the repair/restore-defaults flow in the
        manager.
        """
        errors: List[str] = []
        required_keys = ["color_scheme", "font", "shortcuts"]
        for key in required_keys:
            if key not in settings:
                errors.append(f"Missing required setting: {key}")

        validators = {
            "color_scheme": lambda v: self.validate_color_scheme(v, num_schemes),
            "transparency": self.validate_transparency,
            "font": self.validate_font,
            "terminal_scroll_mode": self.validate_terminal_scroll_mode,
        }
        for key, validator in validators.items():
            if key in settings and not validator(settings[key]):
                errors.append(
                    f"Invalid value for setting '{key}': {settings[key]}"
                )

        if "shortcuts" in settings:
            errors.extend(self.validate_shortcuts(settings["shortcuts"]))

        for key in _BOOLEAN_SETTINGS:
            if key in settings and not isinstance(settings[key], bool):
                errors.append(
                    f"Setting '{key}' must be boolean, got {type(settings[key]).__name__}"
                )
        return errors
