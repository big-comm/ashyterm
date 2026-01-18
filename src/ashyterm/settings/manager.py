# ashyterm/settings/manager.py
import json
import threading
import time
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte

from ..utils.exceptions import ConfigValidationError
from ..utils.logger import get_logger, log_error_with_context
from ..utils.platform import get_platform_info
from ..utils.security import (
    ensure_secure_file_permissions,
    validate_file_path,
)
from .config import (
    AppConstants,
    ColorSchemeMap,
    ColorSchemes,
    DefaultSettings,
    get_config_paths,
)


@dataclass(slots=True)
class SettingsMetadata:
    """Metadata for settings file."""

    version: str
    created_at: float
    modified_at: float
    checksum: Optional[str] = None


class SettingsValidator:
    """Validates settings values and structure."""

    def __init__(self):
        self.logger = get_logger("ashyterm.settings.validator")

    def validate_color_scheme(self, value: Any, num_schemes: int) -> bool:
        if not isinstance(value, int):
            return False
        return 0 <= value < num_schemes

    def validate_transparency(self, value: Any) -> bool:
        if not isinstance(value, (int, float)):
            return False
        return 0 <= value <= 100

    def validate_font(self, value: Any) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            return Pango.FontDescription.from_string(value).get_family() is not None
        except Exception:
            return False

    def validate_shortcut(self, value: Any) -> bool:
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

    def validate_shortcuts(self, shortcuts: Dict[str, str]) -> List[str]:
        errors = []
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
                errors.append(f"Invalid shortcut for action '{action}': {shortcut}")
        return errors

    def validate_settings_structure(
        self, settings: Dict[str, Any], num_schemes: int
    ) -> List[str]:
        errors = []
        required_keys = ["color_scheme", "font", "shortcuts"]
        for key in required_keys:
            if key not in settings:
                errors.append(f"Missing required setting: {key}")

        validators = {
            "color_scheme": lambda v: self.validate_color_scheme(v, num_schemes),
            "transparency": self.validate_transparency,
            "font": self.validate_font,
        }
        for key, validator in validators.items():
            if key in settings and not validator(settings[key]):
                errors.append(f"Invalid value for setting '{key}': {settings[key]}")

        if "shortcuts" in settings:
            errors.extend(self.validate_shortcuts(settings["shortcuts"]))
        boolean_settings = [
            "sidebar_visible",
            "auto_hide_sidebar",
            "scroll_on_output",
            "scroll_on_keystroke",
            "mouse_autohide",
            "bell_sound",
            "log_to_file",
            "ai_assistant_enabled",
        ]
        for key in boolean_settings:
            if key in settings and not isinstance(settings[key], bool):
                errors.append(
                    f"Setting '{key}' must be boolean, got {type(settings[key]).__name__}"
                )
        return errors


class SettingsManager:
    """Enhanced settings manager with comprehensive functionality."""

    _LOG_AREA = "ashyterm.settings"

    def __init__(self, settings_file: Optional[Path] = None):
        self.logger = get_logger("ashyterm.settings.manager")
        self.platform_info = get_platform_info()
        self.validator = SettingsValidator()
        self.config_paths = get_config_paths()
        self.settings_file = settings_file or self.config_paths.SETTINGS_FILE
        self.custom_schemes_file = self.config_paths.CONFIG_DIR / "custom_schemes.json"
        self._settings: Dict[str, Any] = {}
        self._defaults = DefaultSettings.get_defaults()
        self._metadata: Optional[SettingsMetadata] = None
        self._dirty = False
        self._lock = threading.RLock()
        self._change_listeners: List[Callable[[str, Any, Any], None]] = []
        self.custom_schemes: Dict[str, Any] = {}
        # Unified CSS provider for the entire application
        self._app_css_provider = Gtk.CssProvider()
        # Store window-specific transparency providers to manage lifecycle
        self._window_providers = weakref.WeakKeyDictionary()
        self._initialize()
        self.logger.info("Settings manager initialized")

    def _initialize(self):
        try:
            with self._lock:
                self._settings = self._load_settings_safe()
                self.custom_schemes = self._load_custom_schemes()
                self._validate_and_repair()
                self._merge_with_defaults()
                self._apply_log_settings()
                # If we loaded a legacy settings file or performed repairs/default merges,
                # persist the canonical wrapper format proactively.
                if self._dirty:
                    self.save_settings()
        except Exception as e:
            self.logger.error(f"Settings initialization failed: {e}")
            self._settings = self._defaults.copy()
            self.custom_schemes = {}
            self._dirty = True
            self._dirty = True

        # Initial theme application
        try:
            GLib.idle_add(self._update_app_theme_css)
        except Exception as e:
            self.logger.error(f"Failed to apply initial theme: {e}")

    def _load_custom_schemes(self) -> Dict[str, Any]:
        if not self.custom_schemes_file.exists():
            return {}
        try:
            with open(self.custom_schemes_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Validate each scheme - must be a dict with required keys
                valid_schemes = {}
                for key, value in data.items():
                    if (
                        isinstance(value, dict)
                        and "foreground" in value
                        and "background" in value
                    ):
                        valid_schemes[key] = value
                    else:
                        self.logger.warning(f"Invalid custom scheme '{key}', skipping")
                return valid_schemes
            self.logger.warning("Custom schemes file is not a valid dictionary.")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to load custom color schemes: {e}")
            return {}

    def save_custom_schemes(self):
        try:
            with open(self.custom_schemes_file, "w", encoding="utf-8") as f:
                json.dump(self.custom_schemes, f, indent=2)
            self.logger.info(f"Saved {len(self.custom_schemes)} custom schemes.")
        except Exception as e:
            self.logger.error(f"Failed to save custom color schemes: {e}")

    def _apply_log_settings(self):
        """Applies log settings to the logger system."""
        from ..utils import logger

        log_to_file = self.get("log_to_file", False)
        log_level = self.get("console_log_level", "ERROR")
        logger.set_log_to_file_enabled(log_to_file)
        logger.set_console_log_level(log_level)

    def _parse_settings_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse and extract settings from loaded data."""
        if "settings" in data and "metadata" in data:
            settings = data.get("settings", {})
            metadata = data.get("metadata", {})
            if isinstance(metadata, dict):
                self._metadata = SettingsMetadata(**metadata)
                if self._metadata.checksum and isinstance(settings, dict):
                    self._verify_settings_integrity(settings)
            else:
                self._metadata = None
            return settings

        # Legacy format handling
        self._metadata = None
        self.logger.info("Loaded legacy settings format")
        self._dirty = True
        return data

    def _validate_settings_structure(self, settings: Any) -> Dict[str, Any]:
        """Validate settings structure and return valid dict or raise ValueError."""
        if not isinstance(settings, dict):
            raise ValueError("Settings file has invalid structure")
        return settings

    def _load_settings_safe(self) -> Dict[str, Any]:
        if not self.settings_file.exists():
            self.logger.info("Settings file not found, using defaults")
            return self._defaults.copy()
        try:
            validate_file_path(str(self.settings_file))
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("Settings file must contain a JSON object at the root")

            settings = self._parse_settings_data(data)
            return self._validate_settings_structure(settings)
        except json.JSONDecodeError as e:
            self.logger.error(f"Settings file is corrupted: {e}")
            return self._defaults.copy()
        except ValueError as e:
            self.logger.error(f"Settings file has invalid structure: {e}")
            return self._defaults.copy()
        except Exception as e:
            self.logger.error(f"Failed to load settings: {e}")
            log_error_with_context(e, "settings loading", self._LOG_AREA)
            return self._defaults.copy()

    def _verify_settings_integrity(self, settings: Dict[str, Any]):
        try:
            import hashlib

            settings_json = json.dumps(settings, sort_keys=True, separators=(",", ":"))
            current_checksum = hashlib.md5(
                settings_json.encode("utf-8"), usedforsecurity=False
            ).hexdigest()
            if current_checksum != self._metadata.checksum:
                self.logger.warning(
                    "Settings checksum mismatch - file may be corrupted"
                )
        except Exception as e:
            self.logger.warning(f"Checksum verification failed: {e}")

    def _validate_and_repair(self):
        try:
            num_schemes = len(self.get_scheme_order())
            errors = self.validator.validate_settings_structure(
                self._settings, num_schemes
            )
            if errors:
                self.logger.warning(f"Settings validation failed: {errors}")
                if self._repair_settings(errors):
                    self.logger.info("Settings automatically repaired")
                    self._dirty = True
                else:
                    self.logger.warning("Could not repair all settings issues")
        except Exception as e:
            self.logger.error(f"Settings validation failed: {e}")

    def _repair_settings(self, errors: List[str]) -> bool:
        repairs_made = 0
        for error in errors:
            if self._try_repair_setting(error):
                repairs_made += 1
        return repairs_made > 0

    def _try_repair_setting(self, error: str) -> bool:
        """Attempt to repair a single validation error."""
        # Split error into type and key/detail
        if "Missing required setting:" in error:
            key = error.split(": ")[1]
            return self._restore_default(key)

        if "Invalid value for setting" in error:
            try:
                key = error.split("'")[1]
                return self._restore_default(key)
            except (IndexError, KeyError):
                pass

        if "must be boolean" in error:
            try:
                key = error.split("'")[1]
                if isinstance(self._defaults.get(key), bool):
                    return self._restore_default(key)
            except (IndexError, KeyError):
                pass

        return False

    def _restore_default(self, key: str) -> bool:
        """Restore a setting to its default value if available."""
        if key in self._defaults:
            self._settings[key] = self._defaults[key]
            return True
        return False

    def _merge_with_defaults(self):
        try:
            updated = False
            for key, default_value in self._defaults.items():
                if key not in self._settings:
                    self._settings[key] = default_value
                    updated = True
                elif isinstance(default_value, dict) and isinstance(
                    self._settings[key], dict
                ):
                    for sub_key, sub_default in default_value.items():
                        if sub_key not in self._settings[key]:
                            self._settings[key][sub_key] = sub_default
                            updated = True
            if updated:
                self._dirty = True
        except Exception as e:
            self.logger.error(f"Failed to merge with defaults: {e}")

    def save_settings(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            settings_to_save = self._settings.copy()
            self._dirty = False

        def save_task():
            with self._lock:
                try:
                    num_schemes = len(self.get_scheme_order())
                    errors = self.validator.validate_settings_structure(
                        settings_to_save, num_schemes
                    )
                    if errors:
                        self.logger.error(
                            f"Cannot save invalid settings asynchronously: {errors}"
                        )
                        self._dirty = True
                        return
                    current_time = time.time()
                    if self._metadata:
                        self._metadata.modified_at = current_time
                    else:
                        self._metadata = SettingsMetadata(
                            version=AppConstants.APP_VERSION,
                            created_at=current_time,
                            modified_at=current_time,
                        )
                    import hashlib

                    settings_json = json.dumps(
                        settings_to_save, sort_keys=True, separators=(",", ":")
                    )
                    self._metadata.checksum = hashlib.md5(
                        settings_json.encode("utf-8"), usedforsecurity=False
                    ).hexdigest()
                    save_data = {
                        "metadata": asdict(self._metadata),
                        "settings": settings_to_save,
                    }
                    self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_file = self.settings_file.with_suffix(".tmp")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(save_data, f, indent=2, ensure_ascii=False)
                    temp_file.replace(self.settings_file)
                    try:
                        ensure_secure_file_permissions(str(self.settings_file))
                    except Exception as e:
                        self.logger.warning(f"Failed to set secure permissions: {e}")
                except Exception as e:
                    self.logger.error(f"Async settings save task failed: {e}")
                    log_error_with_context(e, "async settings saving", self._LOG_AREA)
                    self._dirty = True

        threading.Thread(target=save_task, daemon=True).start()

    def set(self, key: str, value: Any, save_immediately: bool = True) -> None:
        with self._lock:
            try:
                old_value = self.get(key)
                self._validate_setting_value(key, value)
                if "." in key:
                    keys = key.split(".")
                    current = self._settings
                    for k in keys[:-1]:
                        current = current.setdefault(k, {})
                    current[keys[-1]] = value
                else:
                    self._settings[key] = value
                self._dirty = True

                if key == "console_log_level":
                    from ..utils import logger

                    logger.set_console_log_level(value)
                elif key == "log_to_file":
                    from ..utils import logger

                    logger.set_log_to_file_enabled(value)

                self._notify_change_listeners(key, old_value, value)
                self._notify_change_listeners(key, old_value, value)

                # Update theme CSS if relevant settings change
                if self._is_theme_setting(key):
                    # We need to update the theme on the main thread
                    GLib.idle_add(self._update_app_theme_css)

                if save_immediately:
                    self.save_settings()
            except Exception as e:
                self.logger.error(f"Failed to set setting '{key}': {e}")
                raise ConfigValidationError(key, value, str(e))

    def _is_theme_setting(self, key: str) -> bool:
        """Check if a setting key affects the application theme."""
        theme_keys = {
            "gtk_theme",
            "color_scheme",
            "transparency",
            "headerbar_transparency",
            "font",  # Font might affect some sizing/layout
            "cursor_shape",  # Terminal appearance
            # Add other appearance keys as needed
        }
        return key in theme_keys or key.startswith("custom_schemes")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            try:
                if "." in key:
                    keys = key.split(".")
                    value = self._settings
                    for k in keys:
                        value = value[k]
                    return value
                else:
                    return self._settings.get(key, default)
            except (KeyError, TypeError):
                return default
            except Exception as e:
                self.logger.error(f"Error getting setting '{key}': {e}")
                return default

    def _validate_setting_value(self, key: str, value: Any):
        base_key = key.split(".")[0]
        validators = {
            "color_scheme": lambda v: self.validator.validate_color_scheme(
                v, len(self.get_scheme_order())
            ),
            "transparency": self.validator.validate_transparency,
            "font": self.validator.validate_font,
        }
        if base_key in validators and not validators[base_key](value):
            raise ConfigValidationError(key, value, f"Invalid value for {base_key}")
        if key.startswith("shortcuts.") and not self.validator.validate_shortcut(value):
            raise ConfigValidationError(key, value, "Invalid keyboard shortcut")

    def _notify_change_listeners(self, key: str, old_value: Any, new_value: Any):
        for listener in self._change_listeners:
            try:
                listener(key, old_value, new_value)
            except Exception as e:
                self.logger.error(f"Change listener failed for key '{key}': {e}")

    def add_change_listener(self, listener: Callable[[str, Any, Any], None]):
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    def get_all_schemes(self) -> Dict[str, Any]:
        """Merges built-in schemes with custom schemes."""
        schemes = ColorSchemes.get_schemes().copy()
        schemes.update(self.custom_schemes)
        return schemes

    def get_scheme_order(self) -> List[str]:
        """Returns the order of built-in schemes followed by sorted custom schemes."""
        return ColorSchemeMap.get_schemes_list() + sorted(self.custom_schemes.keys())

    def get_color_scheme_name(self) -> str:
        index = self.get("color_scheme", 0)
        scheme_order = self.get_scheme_order()
        if 0 <= index < len(scheme_order):
            return scheme_order[index]
        return scheme_order[0]

    def get_color_scheme_data(self) -> Dict[str, Any]:
        scheme_name = self.get_color_scheme_name()
        all_schemes = self.get_all_schemes()
        return all_schemes.get(scheme_name, all_schemes[self.get_scheme_order()[0]])

    def _calculate_adaptive_alpha(
        self, base_color_hex: str, user_transparency: float
    ) -> float:
        """
        Calculates the final alpha value based on the base color's luminance
        to provide a perceptually more consistent transparency effect.
        """
        rgba = Gdk.RGBA()
        rgba.parse(base_color_hex)

        # Calculate perceptual luminance (Y in YIQ). This value ranges from 0.0 (black) to 1.0 (white).
        luminance = 0.299 * rgba.red + 0.587 * rgba.green + 0.114 * rgba.blue

        # Create a boost factor. Darker colors (lower luminance) get a bigger boost.
        # A boost_factor of 0.3 means black (L=0) gets a 30% boost, while white (L=1) gets 0%.
        # This value is reduced from 0.8 to make the effect more subtle and controllable.
        boost_factor = 0.3
        adjustment_factor = 1.0 + (boost_factor * (1.0 - luminance))

        # Apply the boost to the user's desired transparency
        adjusted_transparency = min(100.0, user_transparency * adjustment_factor)

        # Apply the perceptually uniform curve to the adjusted value
        final_alpha = max(0.0, min(1.0, 1.0 - (adjusted_transparency / 100.0) ** 1.6))
        return final_alpha

    def apply_terminal_settings(self, terminal, window) -> None:
        user_transparency = self.get("transparency", 0)
        style_context = window.get_style_context()

        # Handle terminal transparency
        if hasattr(window, "_transparency_css_provider"):
            style_context.remove_provider(window._transparency_css_provider)
        if user_transparency > 0:
            css_provider = Gtk.CssProvider()
            css = ".terminal-tab-view > .view { background-color: transparent; } .background { background: transparent; }"
            css_provider.load_from_data(css.encode("utf-8"))
            style_context.add_provider(
                css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            window._transparency_css_provider = css_provider

        color_scheme = self.get_color_scheme_data()
        fg_color, bg_color, cursor_color = Gdk.RGBA(), Gdk.RGBA(), Gdk.RGBA()
        fg_color.parse(color_scheme.get("foreground", "#FFFFFF"))
        bg_color.parse(color_scheme.get("background", "#000000"))

        bg_color.alpha = self._calculate_adaptive_alpha(
            color_scheme.get("background", "#000000"), user_transparency
        )

        cursor_color.parse(
            color_scheme.get("cursor", color_scheme.get("foreground", "#FFFFFF"))
        )
        palette = []
        for i, color_str in enumerate(color_scheme.get("palette", [])):
            color = Gdk.RGBA()
            if not color.parse(color_str):
                fallback_colors = [
                    "#000000",
                    "#800000",
                    "#008000",
                    "#808000",
                    "#000080",
                    "#800080",
                    "#008080",
                    "#c0c0c0",
                    "#808080",
                    "#ff0000",
                    "#00ff00",
                    "#ffff00",
                    "#0000ff",
                    "#ff00ff",
                    "#00ffff",
                    "#ffffff",
                ]
                color.parse(fallback_colors[i % len(fallback_colors)])
            palette.append(color)
        while len(palette) < 16:
            palette.append(Gdk.RGBA.new(0, 0, 0, 1))
        terminal.set_colors(fg_color, bg_color, palette[:16])
        if hasattr(terminal, "set_color_cursor"):
            terminal.set_color_cursor(cursor_color)
        font_string = self.get("font", "Monospace 10")
        try:
            terminal.set_font(Pango.FontDescription.from_string(font_string))
        except Exception as e:
            self.logger.warning(f"Invalid font '{font_string}', using default: {e}")
            terminal.set_font(Pango.FontDescription.from_string("Monospace 10"))
        try:
            terminal.set_font_scale(self.get("font_scale", 1.0))
        except Exception as e:
            self.logger.warning(f"Failed to apply font scale: {e}")

        # Smart scrolling is handled in TabManager, so we don't call set_scroll_on_output here.
        terminal.set_scroll_on_keystroke(self.get("scroll_on_keystroke", True))
        terminal.set_scroll_on_insert(self.get("scroll_on_insert", True))
        terminal.set_mouse_autohide(self.get("mouse_autohide", True))
        terminal.set_audible_bell(self.get("bell_sound", False))
        terminal.set_scrollback_lines(self.get("scrollback_lines", 10000))

        cursor_shape_map = [
            Vte.CursorShape.BLOCK,
            Vte.CursorShape.IBEAM,
            Vte.CursorShape.UNDERLINE,
        ]
        shape_index = self.get("cursor_shape", 0)
        terminal.set_cursor_shape(
            cursor_shape_map[shape_index]
            if 0 <= shape_index < len(cursor_shape_map)
            else Vte.CursorShape.BLOCK
        )
        cursor_blink_map = [
            Vte.CursorBlinkMode.SYSTEM,
            Vte.CursorBlinkMode.ON,
            Vte.CursorBlinkMode.OFF,
        ]
        cursor_blink_index = self.get("cursor_blink", 0)
        terminal.set_cursor_blink_mode(
            cursor_blink_map[cursor_blink_index]
            if 0 <= cursor_blink_index < len(cursor_blink_map)
            else Vte.CursorBlinkMode.SYSTEM
        )
        text_blink_map = [Vte.TextBlinkMode.FOCUSED, Vte.TextBlinkMode.UNFOCUSED]
        blink_index = self.get("text_blink_mode", 0)
        terminal.set_text_blink_mode(
            text_blink_map[blink_index]
            if 0 <= blink_index < len(text_blink_map)
            else Vte.TextBlinkMode.FOCUSED
        )

        terminal.set_enable_bidi(self.get("bidi_enabled", False))
        terminal.set_enable_shaping(self.get("enable_shaping", False))
        terminal.set_enable_sixel(self.get("sixel_enabled", True))
        terminal.set_allow_hyperlink(True)  # OSC8 hyperlinks always enabled
        terminal.set_word_char_exceptions(self.get("word_char_exceptions", "-_.:/~"))
        # VTE 0.76+ removed set_enable_a11y (accessibility is always enabled)
        if hasattr(terminal, "set_enable_a11y"):
            terminal.set_enable_a11y(self.get("accessibility_enabled", True))
        terminal.set_cell_height_scale(self.get("line_spacing", 1.0))
        terminal.set_bold_is_bright(self.get("bold_is_bright", True))

        backspace_map = [
            Vte.EraseBinding.AUTO,
            Vte.EraseBinding.ASCII_BACKSPACE,
            Vte.EraseBinding.ASCII_DELETE,
            Vte.EraseBinding.DELETE_SEQUENCE,
        ]
        backspace_index = self.get("backspace_binding", 0)
        terminal.set_backspace_binding(
            backspace_map[backspace_index]
            if 0 <= backspace_index < len(backspace_map)
            else Vte.EraseBinding.AUTO
        )
        delete_map = [
            Vte.EraseBinding.AUTO,
            Vte.EraseBinding.ASCII_DELETE,
            Vte.EraseBinding.DELETE_SEQUENCE,
        ]
        delete_index = self.get("delete_binding", 0)
        terminal.set_delete_binding(
            delete_map[delete_index]
            if 0 <= delete_index < len(delete_map)
            else Vte.EraseBinding.AUTO
        )
        terminal.set_cjk_ambiguous_width(self.get("cjk_ambiguous_width", 1))

    def _update_app_theme_css(self, window=None) -> None:
        """
        Generates and applies the unified application CSS using ThemeEngine.
        """
        try:
            display = Gdk.Display.get_default()
            if not display:
                return

            # Ensure provider is attached to display
            if not getattr(self, "_provider_attached", False):
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    self._app_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_USER,
                )
                self._provider_attached = True

            # Lazy-load ThemeEngine
            from ..utils.theme_engine import ThemeEngine

            scheme = self.get_color_scheme_data()
            gtk_theme = self.get("gtk_theme")
            transparency = self.get("headerbar_transparency", 0)

            params = ThemeEngine.get_theme_params(scheme, transparency)
            full_css = ThemeEngine.generate_app_css(params, gtk_theme)

            self._app_css_provider.load_from_data(full_css.encode("utf-8"))

            # Force redraw if window provided
            if window:
                window.queue_draw()

        except Exception as e:
            self.logger.error(f"Failed to update application theme CSS: {e}")
            log_error_with_context(e, "theme update", self._LOG_AREA)

    def remove_gtk_terminal_theme(self, window) -> None:
        """Removes the custom CSS provider for the terminal theme."""
        try:
            if hasattr(window, "_terminal_theme_provider"):
                Gtk.StyleContext.remove_provider_for_display(
                    Gdk.Display.get_default(), window._terminal_theme_provider
                )
                delattr(window, "_terminal_theme_provider")
                self.logger.info("Removed terminal theme provider.")

            # Clean up other legacy providers if present
            for attr in [
                "_terminal_theme_header_provider",
                "_terminal_theme_tabs_provider",
            ]:
                if hasattr(window, attr):
                    try:
                        Gtk.StyleContext.remove_provider_for_display(
                            Gdk.Display.get_default(), getattr(window, attr)
                        )
                    except Exception:
                        pass
                    delattr(window, attr)

            # Re-apply headerbar transparency to restore default appearance if needed
            if hasattr(window, "header_bar"):
                # In new system, transparency is part of unified CSS, so we just trigger update
                GLib.idle_add(self._update_app_theme_css, window)

        except Exception as e:
            self.logger.warning(f"Failed to remove GTK terminal theme: {e}")

    def get_shortcut(self, action_name: str) -> str:
        return self.get(f"shortcuts.{action_name}", "")

    def set_shortcut(self, action_name: str, shortcut: str) -> None:
        self.set(f"shortcuts.{action_name}", shortcut)

    def reset_to_defaults(self, keys: Optional[List[str]] = None) -> None:
        with self._lock:
            try:
                if keys is None:
                    self._settings = self._defaults.copy()
                    self.logger.info("All settings reset to defaults")
                else:
                    for key in keys:
                        if key in self._defaults:
                            self.set(key, self._defaults[key], save_immediately=False)
                    self.logger.info(f"Reset {len(keys)} settings to defaults")
                self._dirty = True
                self.save_settings()
            except Exception as e:
                self.logger.error(f"Failed to reset settings: {e}")
                raise

    def get_sidebar_visible(self) -> bool:
        return self.get("sidebar_visible", True)

    def set_sidebar_visible(self, visible: bool) -> None:
        self.set("sidebar_visible", visible)

    def cleanup_css_providers(self, window) -> None:
        """Remove all CSS providers associated with a window to prevent memory leaks."""
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return

            self._cleanup_window_level_providers(window, display)
            self._cleanup_transparency_provider(window, display)
            self._cleanup_headerbar_providers(window, display)

        except Exception as e:
            self.logger.warning(f"Error during CSS provider cleanup: {e}")

    def _cleanup_window_level_providers(self, window, display):
        """Clean up providers stored as window attributes."""
        provider_attrs = ["_terminal_theme_provider", "_transparency_css_provider"]
        for attr in provider_attrs:
            if hasattr(window, attr):
                self._remove_provider(display, getattr(window, attr))
                delattr(window, attr)

    def _cleanup_transparency_provider(self, window, display):
        """Clean up transparency provider from WeakKeyDictionary."""
        if window in self._window_providers:
            self._remove_provider(display, self._window_providers[window])
            del self._window_providers[window]

    def _cleanup_headerbar_providers(self, window, display):
        """Clean up headerbar providers (legacy cleanup)."""
        if hasattr(window, "header_bar") and (headerbar := window.header_bar):
            if hasattr(headerbar, "_transparency_provider"):
                self._remove_provider(display, headerbar._transparency_provider)
                delattr(headerbar, "_transparency_provider")

    def _remove_provider(self, display, provider):
        """Safely remove a provider from a display."""
        try:
            Gtk.StyleContext.remove_provider_for_display(display, provider)
        except Exception:
            pass


_settings_manager: Optional[SettingsManager] = None
_settings_lock = threading.Lock()


def get_settings_manager() -> SettingsManager:
    """Get the global settings manager instance."""
    global _settings_manager
    if _settings_manager is None:
        with _settings_lock:
            if _settings_manager is None:
                _settings_manager = SettingsManager()
    return _settings_manager
