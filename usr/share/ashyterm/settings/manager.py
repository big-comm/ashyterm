"""
Enhanced settings manager for Ashy Terminal.

This module provides comprehensive settings management with validation, security,
backup, migration, and platform-aware configuration handling.
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Callable
from dataclasses import dataclass, asdict

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gdk, Pango, GLib

# VTE import with availability check
try:
    from gi.repository import Vte
    VTE_AVAILABLE = True
except ImportError:
    VTE_AVAILABLE = False

# Import configuration constants
from .config import (
    AppConstants, DefaultSettings, ColorSchemes, ColorSchemeMap,
    get_config_paths, VTE_AVAILABLE as CONFIG_VTE_AVAILABLE,
    NetworkConstants, SecurityConstants
)

# Import new utility systems
from ..utils.logger import get_logger, log_error_with_context
from ..utils.exceptions import (
    ConfigError, ConfigValidationError, ConfigMissingError,
    StorageError, StorageReadError, StorageWriteError,
    handle_exception, ErrorCategory, ErrorSeverity
)
from ..utils.security import (
    validate_file_path, ensure_secure_file_permissions,
    InputSanitizer, create_security_auditor
)
from ..utils.backup import get_backup_manager, BackupType
from ..utils.platform import get_platform_info, normalize_path
from ..utils.crypto import (
    is_encryption_available, get_secure_storage,
    encrypt_password, decrypt_password
)


@dataclass
class SettingsMetadata:
    """Metadata for settings file."""
    version: str
    created_at: float
    modified_at: float
    platform: str
    checksum: Optional[str] = None
    backup_id: Optional[str] = None


class SettingsValidator:
    """Validates settings values and structure."""
    
    def __init__(self):
        self.logger = get_logger('ashyterm.settings.validator')
        self.security_auditor = None
        
        try:
            self.security_auditor = create_security_auditor()
        except Exception as e:
            self.logger.warning(f"Security auditor not available: {e}")
    
    def validate_color_scheme(self, value: Any) -> bool:
        """Validate color scheme index."""
        if not isinstance(value, int):
            return False
        
        schemes = ColorSchemeMap.get_schemes_list()
        return 0 <= value < len(schemes)
    
    def validate_transparency(self, value: Any) -> bool:
        """Validate transparency percentage."""
        if not isinstance(value, (int, float)):
            return False
        return 0 <= value <= 100
    
    def validate_font(self, value: Any) -> bool:
        """Validate font string."""
        if not isinstance(value, str) or not value.strip():
            return False
        
        try:
            # Test if font description is valid
            font_desc = Pango.FontDescription.from_string(value)
            return font_desc.get_family() is not None
        except Exception:
            return False
    
    def validate_shortcut(self, value: Any) -> bool:
        """Validate keyboard shortcut string."""
        if not isinstance(value, str):
            return False
        
        if not value:  # Empty shortcut is allowed
            return True
        
        try:
            # Try to parse the accelerator - GTK4 compatibility
            from gi.repository import Gtk
            
            # GTK4 accelerator_parse returns a different structure
            success, keyval, mods = Gtk.accelerator_parse(value)
            
            # Check if parsing was successful and keyval is valid
            return success and keyval != 0
            
        except Exception as e:
            self.logger.debug(f"Shortcut validation failed for '{value}': {e}")
            return False
    
    def validate_shortcuts(self, shortcuts: Dict[str, str]) -> List[str]:
        """
        Validate all shortcuts and return list of errors.
        
        Args:
            shortcuts: Dictionary of action->shortcut mappings
            
        Returns:
            List of validation error messages
        """
        errors = []
        
        if not isinstance(shortcuts, dict):
            errors.append("Shortcuts must be a dictionary")
            return errors
        
        # Check for duplicate shortcuts
        shortcut_values = [v for v in shortcuts.values() if v]
        if len(shortcut_values) != len(set(shortcut_values)):
            errors.append("Duplicate keyboard shortcuts detected")
        
        # Validate individual shortcuts
        for action, shortcut in shortcuts.items():
            if not isinstance(action, str):
                errors.append(f"Invalid action name: {action}")
                continue
            
            if not self.validate_shortcut(shortcut):
                errors.append(f"Invalid shortcut for action '{action}': {shortcut}")
        
        return errors
    
    def validate_settings_structure(self, settings: Dict[str, Any]) -> List[str]:
        """
        Validate complete settings structure.
        
        Args:
            settings: Settings dictionary to validate
            
        Returns:
            List of validation error messages
        """
        errors = []
        defaults = DefaultSettings.get_defaults()
        
        # Check for required keys
        required_keys = ['color_scheme', 'font', 'shortcuts']
        for key in required_keys:
            if key not in settings:
                errors.append(f"Missing required setting: {key}")
        
        # Validate individual settings
        validators = {
            'color_scheme': self.validate_color_scheme,
            'transparency': self.validate_transparency,
            'font': self.validate_font,
        }
        
        for key, validator in validators.items():
            if key in settings and not validator(settings[key]):
                errors.append(f"Invalid value for setting '{key}': {settings[key]}")
        
        # Validate shortcuts
        if 'shortcuts' in settings:
            shortcut_errors = self.validate_shortcuts(settings['shortcuts'])
            errors.extend(shortcut_errors)
        
        # Validate boolean settings
        boolean_settings = [
            'sidebar_visible', 'auto_hide_sidebar', 'show_toolbar',
            'confirm_close', 'confirm_delete', 'auto_close_tab',
            'scroll_on_output', 'scroll_on_keystroke', 'mouse_autohide',
            'cursor_blink', 'bell_sound', 'restore_sessions',
            'auto_save_sessions', 'session_backup', 'security_warnings',
            'audit_sessions', 'encrypt_passwords', 'secure_file_permissions',
            'auto_backup_enabled', 'backup_on_exit', 'debug_mode',
            'performance_mode', 'experimental_features'
        ]
        
        for key in boolean_settings:
            if key in settings and not isinstance(settings[key], bool):
                errors.append(f"Setting '{key}' must be boolean, got {type(settings[key]).__name__}")
        
        # Validate numeric settings
        numeric_settings = {
            'auto_close_delay': (0, 60000),  # 0-60 seconds
            'max_recent_sessions': (1, 100),
            'backup_interval_hours': (1, 168),  # 1 hour to 1 week
            'backup_retention_days': (1, 365),
            'max_backup_count': (1, 100)
        }
        
        for key, (min_val, max_val) in numeric_settings.items():
            if key in settings:
                value = settings[key]
                if not isinstance(value, (int, float)):
                    errors.append(f"Setting '{key}' must be numeric, got {type(value).__name__}")
                elif not (min_val <= value <= max_val):
                    errors.append(f"Setting '{key}' must be between {min_val} and {max_val}, got {value}")
        
        return errors


class SettingsManager:
    """
    Enhanced settings manager with comprehensive functionality.
    
    Features:
    - Validation and error handling
    - Automatic backup and recovery
    - Security and encryption
    - Platform-aware configuration
    - Migration support
    - Thread safety
    """
    
    def __init__(self, settings_file: Optional[Path] = None):
        """
        Initialize settings manager.
        
        Args:
            settings_file: Custom settings file path (optional)
        """
        self.logger = get_logger('ashyterm.settings.manager')
        self.platform_info = get_platform_info()
        self.validator = SettingsValidator()
        
        # Configuration
        self.config_paths = get_config_paths()
        self.settings_file = settings_file or self.config_paths.SETTINGS_FILE
        
        # State management
        self._settings: Dict[str, Any] = {}
        self._defaults = DefaultSettings.get_defaults()
        self._metadata: Optional[SettingsMetadata] = None
        self._last_save_time = 0
        self._dirty = False
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Backup system
        self.backup_manager = None
        try:
            self.backup_manager = get_backup_manager()
        except Exception as e:
            self.logger.warning(f"Backup manager not available: {e}")
        
        # Security
        self.secure_storage = None
        if is_encryption_available():
            try:
                self.secure_storage = get_secure_storage()
            except Exception as e:
                self.logger.warning(f"Secure storage not available: {e}")
        
        # Change listeners
        self._change_listeners: List[Callable[[str, Any, Any], None]] = []
        
        # Load settings
        self._initialize()
        
        self.logger.info("Settings manager initialized")
    
    def _initialize(self):
        """Initialize settings manager with loading and validation."""
        try:
            with self._lock:
                # Load settings from file
                self._settings = self._load_settings_safe()
                
                # Validate settings
                self._validate_and_repair()
                
                # Ensure all default keys exist
                self._merge_with_defaults()
                
                # Create backup on first load
                self._create_initialization_backup()
                
                self.logger.debug("Settings initialization completed")
                
        except Exception as e:
            self.logger.error(f"Settings initialization failed: {e}")
            # Use defaults as fallback
            self._settings = self._defaults.copy()
            self._dirty = True
    
    def _load_settings_safe(self) -> Dict[str, Any]:
        """
        Safely load settings from file with error recovery.
        
        Returns:
            Loaded settings dictionary
        """
        if not self.settings_file.exists():
            self.logger.info("Settings file not found, using defaults")
            return self._defaults.copy()
        
        try:
            # Validate file path for security
            validate_file_path(str(self.settings_file))
            
            # Read settings file
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Check if this is a new format with metadata
            if 'settings' in data and 'metadata' in data:
                # New format with metadata
                settings = data['settings']
                metadata_dict = data['metadata']
                self._metadata = SettingsMetadata(**metadata_dict)
                
                # Verify checksum if available
                if self._metadata.checksum:
                    self._verify_settings_integrity(settings)
                
                self.logger.debug(f"Loaded settings with metadata (version: {self._metadata.version})")
            else:
                # Legacy format - just settings
                settings = data
                self._metadata = None
                self.logger.info("Loaded legacy settings format")
            
            return settings
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Settings file is corrupted: {e}")
            return self._recover_from_backup()
        except Exception as e:
            self.logger.error(f"Failed to load settings: {e}")
            log_error_with_context(e, "settings loading", "ashyterm.settings")
            return self._recover_from_backup()
    
    def _verify_settings_integrity(self, settings: Dict[str, Any]):
        """Verify settings integrity using checksum."""
        try:
            import hashlib
            settings_json = json.dumps(settings, sort_keys=True, separators=(',', ':'))
            current_checksum = hashlib.md5(settings_json.encode('utf-8')).hexdigest()
            
            if current_checksum != self._metadata.checksum:
                self.logger.warning("Settings checksum mismatch - file may be corrupted")
                # Don't raise error, just log warning
                
        except Exception as e:
            self.logger.warning(f"Checksum verification failed: {e}")
    
    def _recover_from_backup(self) -> Dict[str, Any]:
        """Attempt to recover settings from backup."""
        if not self.backup_manager:
            self.logger.warning("No backup manager available for recovery")
            return self._defaults.copy()
        
        try:
            # Get recent backups
            backups = self.backup_manager.list_backups()
            settings_backups = [
                (backup_id, metadata) for backup_id, metadata in backups
                if 'settings' in metadata.description.lower()
            ]
            
            if not settings_backups:
                self.logger.warning("No settings backups found")
                return self._defaults.copy()
            
            # Try to restore from most recent backup
            backup_id, metadata = settings_backups[0]
            self.logger.info(f"Attempting to restore settings from backup: {backup_id}")
            
            # Restore to temporary location
            temp_restore_dir = self.config_paths.TEMP_DIR / "settings_recovery"
            success = self.backup_manager.restore_backup(backup_id, temp_restore_dir)
            
            if success:
                # Try to load restored settings
                restored_settings_file = temp_restore_dir / "settings.json"
                if restored_settings_file.exists():
                    with open(restored_settings_file, 'r', encoding='utf-8') as f:
                        restored_data = json.load(f)
                    
                    # Handle both new and legacy formats
                    if 'settings' in restored_data:
                        restored_settings = restored_data['settings']
                    else:
                        restored_settings = restored_data
                    
                    self.logger.info("Settings successfully restored from backup")
                    return restored_settings
            
        except Exception as e:
            self.logger.error(f"Settings recovery from backup failed: {e}")
        
        self.logger.warning("Using default settings as fallback")
        return self._defaults.copy()
    
    def _validate_and_repair(self):
        """Validate loaded settings and repair issues."""
        try:
            errors = self.validator.validate_settings_structure(self._settings)
            
            if errors:
                self.logger.warning(f"Settings validation failed: {errors}")
                
                # Attempt to repair
                repaired = self._repair_settings(errors)
                if repaired:
                    self.logger.info("Settings automatically repaired")
                    self._dirty = True
                else:
                    self.logger.warning("Could not repair all settings issues")
            else:
                self.logger.debug("Settings validation passed")
                
        except Exception as e:
            self.logger.error(f"Settings validation failed: {e}")
    
    def _repair_settings(self, errors: List[str]) -> bool:
        """
        Attempt to repair settings based on validation errors.
        
        Args:
            errors: List of validation error messages
            
        Returns:
            True if all repairs were successful
        """
        repairs_made = 0
        
        # Repair missing required settings
        for error in errors:
            if "Missing required setting:" in error:
                key = error.split(": ")[1]
                if key in self._defaults:
                    self._settings[key] = self._defaults[key]
                    repairs_made += 1
                    self.logger.debug(f"Repaired missing setting: {key}")
        
        # Repair invalid values
        for error in errors:
            if "Invalid value for setting" in error:
                try:
                    key = error.split("'")[1]
                    if key in self._defaults:
                        self._settings[key] = self._defaults[key]
                        repairs_made += 1
                        self.logger.debug(f"Repaired invalid setting: {key}")
                except (IndexError, KeyError):
                    continue
        
        # Repair boolean settings
        for error in errors:
            if "must be boolean" in error:
                try:
                    key = error.split("'")[1]
                    if key in self._defaults and isinstance(self._defaults[key], bool):
                        self._settings[key] = self._defaults[key]
                        repairs_made += 1
                        self.logger.debug(f"Repaired boolean setting: {key}")
                except (IndexError, KeyError):
                    continue
        
        return repairs_made > 0
    
    def _merge_with_defaults(self):
        """Merge settings with defaults to ensure all keys exist."""
        try:
            updated = False
            
            for key, default_value in self._defaults.items():
                if key not in self._settings:
                    self._settings[key] = default_value
                    updated = True
                    self.logger.debug(f"Added missing default setting: {key}")
                elif isinstance(default_value, dict) and isinstance(self._settings[key], dict):
                    # Handle nested dictionaries (like shortcuts)
                    for sub_key, sub_default in default_value.items():
                        if sub_key not in self._settings[key]:
                            self._settings[key][sub_key] = sub_default
                            updated = True
                            self.logger.debug(f"Added missing default subsetting: {key}.{sub_key}")
            
            if updated:
                self._dirty = True
                
        except Exception as e:
            self.logger.error(f"Failed to merge with defaults: {e}")
    
    def _create_initialization_backup(self):
        """Create backup during initialization."""
        if not self.backup_manager or not self.settings_file.exists():
            return
        
        try:
            backup_id = self.backup_manager.create_backup(
                [self.settings_file],
                BackupType.AUTOMATIC,
                "Settings initialization backup"
            )
            
            if backup_id:
                self.logger.debug(f"Created initialization backup: {backup_id}")
                
        except Exception as e:
            self.logger.warning(f"Failed to create initialization backup: {e}")
    
    def save_settings(self, force: bool = False) -> bool:
        """
        Save settings to file with backup and validation.
        
        Args:
            force: Force save even if settings haven't changed
            
        Returns:
            True if save was successful
        """
        with self._lock:
            if not self._dirty and not force:
                self.logger.debug("Settings not dirty, skipping save")
                return True
            
            try:
                # Validate before saving
                errors = self.validator.validate_settings_structure(self._settings)
                if errors:
                    self.logger.error(f"Cannot save invalid settings: {errors}")
                    raise ConfigValidationError("settings", self._settings, "Validation failed: " + "; ".join(errors))
                
                # Create backup before saving
                self._create_pre_save_backup()
                
                # Prepare data for saving
                current_time = time.time()
                
                # Update metadata
                if self._metadata:
                    self._metadata.modified_at = current_time
                    self._metadata.platform = self.platform_info.platform_type.value
                else:
                    self._metadata = SettingsMetadata(
                        version=AppConstants.APP_VERSION,
                        created_at=current_time,
                        modified_at=current_time,
                        platform=self.platform_info.platform_type.value
                    )
                
                # Calculate checksum
                settings_json = json.dumps(self._settings, sort_keys=True, separators=(',', ':'))
                import hashlib
                self._metadata.checksum = hashlib.md5(settings_json.encode('utf-8')).hexdigest()
                
                # Prepare data to save
                save_data = {
                    'metadata': asdict(self._metadata),
                    'settings': self._settings
                }
                
                # Ensure directory exists
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                
                # Write to temporary file first
                temp_file = self.settings_file.with_suffix('.tmp')
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(save_data, f, indent=2, ensure_ascii=False)
                
                # Atomic move to final location
                temp_file.replace(self.settings_file)
                
                # Set secure permissions
                try:
                    ensure_secure_file_permissions(str(self.settings_file))
                except Exception as e:
                    self.logger.warning(f"Failed to set secure permissions: {e}")
                
                # Update state
                self._last_save_time = current_time
                self._dirty = False
                
                self.logger.debug("Settings saved successfully")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to save settings: {e}")
                log_error_with_context(e, "settings saving", "ashyterm.settings")
                raise StorageWriteError(str(self.settings_file), str(e))
    
    def _create_pre_save_backup(self):
        """Create backup before saving settings."""
        if not self.backup_manager or not self.settings_file.exists():
            return
        
        try:
            backup_id = self.backup_manager.create_backup(
                [self.settings_file],
                BackupType.AUTOMATIC,
                "Pre-save settings backup"
            )
            
            if backup_id:
                self.logger.debug(f"Created pre-save backup: {backup_id}")
                if self._metadata:
                    self._metadata.backup_id = backup_id
                    
        except Exception as e:
            self.logger.warning(f"Failed to create pre-save backup: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value with type safety.
        
        Args:
            key: Setting key
            default: Default value if key not found
            
        Returns:
            Setting value or default
        """
        with self._lock:
            try:
                # Support nested keys (e.g., "shortcuts.copy")
                if '.' in key:
                    keys = key.split('.')
                    value = self._settings
                    for k in keys:
                        if isinstance(value, dict) and k in value:
                            value = value[k]
                        else:
                            return default
                    return value
                else:
                    return self._settings.get(key, default)
                    
            except Exception as e:
                self.logger.error(f"Error getting setting '{key}': {e}")
                return default
    
    def set(self, key: str, value: Any, save_immediately: bool = True) -> None:
        """
        Set a setting value with validation and change notification.
        
        Args:
            key: Setting key
            value: Setting value
            save_immediately: Whether to save immediately
        """
        with self._lock:
            try:
                # Get old value for change notification
                old_value = self.get(key)
                
                # Validate value
                self._validate_setting_value(key, value)
                
                # Support nested keys
                if '.' in key:
                    keys = key.split('.')
                    current = self._settings
                    for k in keys[:-1]:
                        if k not in current or not isinstance(current[k], dict):
                            current[k] = {}
                        current = current[k]
                    current[keys[-1]] = value
                else:
                    self._settings[key] = value
                
                # Mark as dirty
                self._dirty = True
                
                # Notify listeners
                self._notify_change_listeners(key, old_value, value)
                
                # Save if requested
                if save_immediately:
                    self.save_settings()
                
                self.logger.debug(f"Setting updated: {key} = {value}")
                
            except Exception as e:
                self.logger.error(f"Failed to set setting '{key}': {e}")
                raise ConfigValidationError(key, value, str(e))
    
    def _validate_setting_value(self, key: str, value: Any):
        """Validate a specific setting value."""
        # Extract base key for nested settings
        base_key = key.split('.')[0]
        
        # Specific validations
        validators = {
            'color_scheme': self.validator.validate_color_scheme,
            'transparency': self.validator.validate_transparency,
            'font': self.validator.validate_font,
        }
        
        if base_key in validators:
            if not validators[base_key](value):
                raise ConfigValidationError(key, value, f"Invalid value for {base_key}")
        
        # Validate shortcuts
        if key.startswith('shortcuts.'):
            if not self.validator.validate_shortcut(value):
                raise ConfigValidationError(key, value, "Invalid keyboard shortcut")
    
    def _notify_change_listeners(self, key: str, old_value: Any, new_value: Any):
        """Notify registered change listeners."""
        for listener in self._change_listeners:
            try:
                listener(key, old_value, new_value)
            except Exception as e:
                self.logger.error(f"Change listener failed for key '{key}': {e}")
    
    def add_change_listener(self, listener: Callable[[str, Any, Any], None]):
        """Add a change listener callback."""
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)
    
    def remove_change_listener(self, listener: Callable[[str, Any, Any], None]):
        """Remove a change listener callback."""
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)
    
    def get_color_scheme_name(self) -> str:
        """Get the current color scheme name."""
        index = self.get("color_scheme", 0)
        return ColorSchemeMap.get_scheme_name(index)
    
    def get_color_scheme_data(self) -> Dict[str, Any]:
        """Get the current color scheme data."""
        scheme_name = self.get_color_scheme_name()
        schemes = ColorSchemes.get_schemes()
        return schemes.get(scheme_name, schemes[ColorSchemeMap.get_schemes_list()[0]])
    
    def set_color_scheme(self, scheme_name: str) -> None:
        """Set color scheme by name."""
        index = ColorSchemeMap.get_scheme_index(scheme_name)
        self.set("color_scheme", index)
    
    def apply_terminal_settings(self, terminal) -> None:
        """
        Apply current settings to a terminal widget with enhanced error handling.
        
        Args:
            terminal: Vte.Terminal widget to configure
        """
        if not CONFIG_VTE_AVAILABLE or not VTE_AVAILABLE:
            self.logger.warning("VTE not available, cannot apply terminal settings")
            return
        
        try:
            # Get color scheme
            color_scheme = self.get_color_scheme_data()
            
            # Parse foreground and background colors
            fg_color = Gdk.RGBA()
            bg_color = Gdk.RGBA()
            cursor_color = Gdk.RGBA()
            
            # Parse colors with fallbacks
            if not fg_color.parse(color_scheme.get("foreground", "#FFFFFF")):
                fg_color.parse("#FFFFFF")
                self.logger.warning("Invalid foreground color, using white")
            
            if not bg_color.parse(color_scheme.get("background", "#000000")):
                bg_color.parse("#000000")
                self.logger.warning("Invalid background color, using black")
            
            # Parse cursor color (fallback to foreground)
            cursor_color_str = color_scheme.get("cursor", color_scheme.get("foreground", "#FFFFFF"))
            if not cursor_color.parse(cursor_color_str):
                cursor_color.parse("#FFFFFF")
            
            # Apply transparency
            transparency = self.get("transparency", 0)
            if transparency > 0:
                bg_color.alpha = max(0.0, min(1.0, 1.0 - (transparency / 100.0)))
            
            # Parse palette colors
            palette = []
            palette_src = color_scheme.get("palette", [])
            
            for i, color_str in enumerate(palette_src):
                color = Gdk.RGBA()
                if not color.parse(color_str):
                    # Use fallback color
                    fallback_colors = [
                        "#000000", "#800000", "#008000", "#808000",
                        "#000080", "#800080", "#008080", "#c0c0c0",
                        "#808080", "#ff0000", "#00ff00", "#ffff00",
                        "#0000ff", "#ff00ff", "#00ffff", "#ffffff"
                    ]
                    color.parse(fallback_colors[i % len(fallback_colors)])
                    self.logger.warning(f"Invalid palette color {i}, using fallback")
                
                palette.append(color)
            
            # Ensure we have exactly 16 colors
            while len(palette) < 16:
                fallback_color = Gdk.RGBA()
                fallback_color.parse("#000000")
                palette.append(fallback_color)
            
            # Apply colors to terminal
            terminal.set_colors(fg_color, bg_color, palette[:16])
            
            # Set cursor color if supported
            try:
                if hasattr(terminal, 'set_color_cursor'):
                    terminal.set_color_cursor(cursor_color)
            except Exception as e:
                self.logger.debug(f"Could not set cursor color: {e}")
            
            # Apply font
            font_string = self.get("font", "Monospace 10")
            try:
                font_desc = Pango.FontDescription.from_string(font_string)
                terminal.set_font(font_desc)
            except Exception as e:
                self.logger.warning(f"Invalid font '{font_string}', using default: {e}")
                fallback_font = Pango.FontDescription.from_string("Monospace 10")
                terminal.set_font(fallback_font)
            
            # Apply behavior settings
            try:
                terminal.set_scroll_on_output(self.get("scroll_on_output", True))
                terminal.set_scroll_on_keystroke(self.get("scroll_on_keystroke", True))
                terminal.set_mouse_autohide(self.get("mouse_autohide", True))
                
                # Cursor blink
                blink_mode = Vte.CursorBlinkMode.ON if self.get("cursor_blink", True) else Vte.CursorBlinkMode.OFF
                terminal.set_cursor_blink_mode(blink_mode)
                
                # Bell
                terminal.set_audible_bell(self.get("bell_sound", False))
                
            except Exception as e:
                self.logger.warning(f"Failed to apply some terminal behaviors: {e}")
            
            self.logger.debug("Terminal settings applied successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to apply terminal settings: {e}")
            log_error_with_context(e, "terminal settings application", "ashyterm.settings")
    
    def update_all_terminals(self, terminals: List) -> None:
        """
        Apply current settings to all terminal widgets.
        
        Args:
            terminals: List of Vte.Terminal widgets
        """
        try:
            updated_count = 0
            
            for terminal in terminals:
                try:
                    if terminal and terminal.get_realized():
                        self.apply_terminal_settings(terminal)
                        updated_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to update terminal: {e}")
            
            self.logger.info(f"Updated {updated_count}/{len(terminals)} terminals")
            
        except Exception as e:
            self.logger.error(f"Failed to update terminals: {e}")
    
    def get_shortcut(self, action_name: str) -> str:
        """
        Get keyboard shortcut for an action.
        
        Args:
            action_name: Name of the action
            
        Returns:
            Shortcut string or empty string if not set
        """
        return self.get(f"shortcuts.{action_name}", "")
    
    def set_shortcut(self, action_name: str, shortcut: str) -> None:
        """
        Set keyboard shortcut for an action.
        
        Args:
            action_name: Name of the action
            shortcut: Shortcut string (e.g., "<Control>t")
        """
        self.set(f"shortcuts.{action_name}", shortcut)
    
    def get_all_shortcuts(self) -> Dict[str, str]:
        """Get all keyboard shortcuts."""
        return self.get("shortcuts", {}).copy()
    
    def reset_to_defaults(self, keys: Optional[List[str]] = None) -> None:
        """
        Reset settings to defaults.
        
        Args:
            keys: Specific keys to reset (None for all)
        """
        with self._lock:
            try:
                if keys is None:
                    # Reset all settings
                    self._settings = self._defaults.copy()
                    self.logger.info("All settings reset to defaults")
                else:
                    # Reset specific keys
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
        """Get sidebar visibility setting."""
        return self.get("sidebar_visible", True)
    
    def set_sidebar_visible(self, visible: bool) -> None:
        """Set sidebar visibility setting."""
        self.set("sidebar_visible", visible)
    
    def export_settings(self, export_path: Path) -> bool:
        """
        Export settings to file.
        
        Args:
            export_path: Path to export to
            
        Returns:
            True if export successful
        """
        try:
            with self._lock:
                export_data = {
                    'metadata': {
                        'version': AppConstants.APP_VERSION,
                        'exported_at': time.time(),
                        'platform': self.platform_info.platform_type.value,
                        'export_type': 'settings'
                    },
                    'settings': self._settings.copy()
                }
                
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)
                
                self.logger.info(f"Settings exported to {export_path}")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to export settings: {e}")
            return False
    
    def import_settings(self, import_path: Path, merge: bool = True) -> bool:
        """
        Import settings from file.
        
        Args:
            import_path: Path to import from
            merge: Whether to merge with existing settings
            
        Returns:
            True if import successful
        """
        try:
            with self._lock:
                with open(import_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Extract settings from export format
                if 'settings' in data:
                    imported_settings = data['settings']
                else:
                    imported_settings = data
                
                # Validate imported settings
                errors = self.validator.validate_settings_structure(imported_settings)
                if errors:
                    self.logger.error(f"Invalid imported settings: {errors}")
                    return False
                
                # Apply settings
                if merge:
                    # Merge with existing
                    for key, value in imported_settings.items():
                        self.set(key, value, save_immediately=False)
                else:
                    # Replace all
                    self._settings = imported_settings.copy()
                    self._merge_with_defaults()
                
                self._dirty = True
                self.save_settings()
                
                self.logger.info(f"Settings imported from {import_path}")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to import settings: {e}")
            return False
    
    def get_metadata(self) -> Optional[SettingsMetadata]:
        """Get settings metadata."""
        return self._metadata
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get settings manager statistics."""
        with self._lock:
            return {
                'settings_count': len(self._settings),
                'last_save_time': self._last_save_time,
                'is_dirty': self._dirty,
                'has_metadata': self._metadata is not None,
                'backup_available': self.backup_manager is not None,
                'secure_storage_available': self.secure_storage is not None,
                'settings_file_exists': self.settings_file.exists(),
                'settings_file_size': self.settings_file.stat().st_size if self.settings_file.exists() else 0
            }


# Global settings manager instance
_settings_manager: Optional[SettingsManager] = None
_settings_lock = threading.Lock()


def get_settings_manager() -> SettingsManager:
    """
    Get the global settings manager instance.
    
    Returns:
        SettingsManager instance
    """
    global _settings_manager
    if _settings_manager is None:
        with _settings_lock:
            if _settings_manager is None:
                _settings_manager = SettingsManager()
    return _settings_manager


def reset_settings_manager():
    """Reset the global settings manager (for testing)."""
    global _settings_manager
    with _settings_lock:
        _settings_manager = None