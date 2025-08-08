"""
Configuration constants and settings for Ashy Terminal.

This module provides application constants, paths, default settings, and color schemes
with enhanced platform compatibility, validation, and security features.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from enum import Enum

# GTK imports with version checks
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

# VTE availability check with comprehensive error handling
try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte
    VTE_AVAILABLE = True
    VTE_VERSION = getattr(Vte, '_version', 'unknown')
except (ValueError, ImportError, AttributeError) as e:
    VTE_AVAILABLE = False
    VTE_VERSION = None
    # Store the error for potential debugging
    VTE_ERROR = str(e)

# Import platform detection early for path configuration
try:
    from ..utils.platform import get_platform_info, get_config_directory, PlatformType
    from ..utils.logger import get_logger
    from ..utils.exceptions import ConfigError, ErrorSeverity
    UTILS_AVAILABLE = True
except ImportError:
    # Fallback for cases where utils aren't available yet
    UTILS_AVAILABLE = False
    get_platform_info = None
    get_config_directory = None


class AppConstants:
    """Application metadata and identification constants."""
    
    # Core application info
    APP_ID = "org.communitybig.ashyterm"
    APP_TITLE = "Ashy Terminal"
    APP_VERSION = "1.1.0"
    APP_VERSION_TUPLE = (1, 1, 0)
    
    # Developer information
    DEVELOPER_NAME = "BigCommunity"
    DEVELOPER_TEAM = ["BigCommunity Team"]
    DEVELOPER_EMAIL = "leo4berbert@gmail.com"
    
    # Legal and support
    COPYRIGHT = "© 2025 BigCommunity"
    LICENSE = "MIT"
    WEBSITE = "https://communitybig.org/"
    DOCUMENTATION_URL = "https://forum.biglinux.com.br/t/biglinuxcommunity"
    ISSUE_URL = "https://github.com/big-comm/comm-ashyterm/issues"
    SUPPORT_URL = "https://t.me/BigLinuxCommunity"
    
    # Technical specifications
    MIN_GTK_VERSION = "4.0"
    MIN_ADWAITA_VERSION = "1.0"
    REQUIRED_VTE_VERSION = "3.91"
    MIN_PYTHON_VERSION = (3, 8)


class ConfigPaths:
    """Platform-aware configuration paths."""
    
    def __init__(self):
        """Initialize configuration paths based on platform."""
        self.logger = None
        if UTILS_AVAILABLE:
            try:
                self.logger = get_logger('ashyterm.config.paths')
                self.platform_info = get_platform_info()
                self.config_dir = get_config_directory()
            except Exception as e:
                self.logger = None
                self.platform_info = None
                self.config_dir = None
        else:
            self.platform_info = None
            self.config_dir = None
        
        self._setup_paths()
    
    def _setup_paths(self):
        """Set up all configuration paths."""
        try:
            if self.config_dir:
                # Use platform-aware config directory
                self.CONFIG_DIR = self.config_dir
            else:
                # Fallback to legacy path detection
                self.CONFIG_DIR = self._get_legacy_config_dir()
            
            # Ensure config directory exists
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            
            # Core configuration files
            self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
            self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
            self.FOLDERS_FILE = self.CONFIG_DIR / "folders.json"
            
            # Cache and temporary directories
            self.CACHE_DIR = self._get_cache_directory()
            self.TEMP_DIR = self._get_temp_directory()
            self.LOG_DIR = self.CONFIG_DIR / "logs"
            
            # Security and backup directories
            self.BACKUP_DIR = self.CONFIG_DIR / "backups"
            self.SECURE_DIR = self.CONFIG_DIR / "secure"
            
            # Create additional directories
            for directory in [self.CACHE_DIR, self.LOG_DIR, self.BACKUP_DIR, self.SECURE_DIR]:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    # Set secure permissions for sensitive directories
                    if directory.name in ['secure', 'backups']:
                        try:
                            directory.chmod(0o700)
                        except OSError:
                            pass  # Windows doesn't support chmod
                except OSError as e:
                    if self.logger:
                        self.logger.warning(f"Failed to create directory {directory}: {e}")
            
            if self.logger:
                self.logger.debug(f"Configuration paths initialized: {self.CONFIG_DIR}")
                
        except Exception as e:
            # Critical error - log and use fallback
            if self.logger:
                self.logger.error(f"Failed to initialize config paths: {e}")
            self._use_fallback_paths()
    
    def _get_legacy_config_dir(self) -> Path:
        """Get configuration directory using legacy detection."""
        # Try XDG config first
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config) / "ashyterm"
        
        # Platform-specific fallbacks
        home = Path.home()
        if sys.platform == 'win32':
            appdata = os.environ.get('APPDATA')
            if appdata:
                return Path(appdata) / "ashyterm"
            return home / "AppData" / "Roaming" / "ashyterm"
        elif sys.platform == 'darwin':
            return home / "Library" / "Application Support" / "ashyterm"
        else:
            return home / ".config" / "ashyterm"
    
    def _get_cache_directory(self) -> Path:
        """Get cache directory."""
        if self.platform_info and hasattr(self.platform_info, 'cache_dir'):
            return self.platform_info.cache_dir
        
        # Fallback
        if sys.platform == 'win32':
            localappdata = os.environ.get('LOCALAPPDATA')
            if localappdata:
                return Path(localappdata) / "ashyterm" / "cache"
            return Path.home() / "AppData" / "Local" / "ashyterm" / "cache"
        elif sys.platform == 'darwin':
            return Path.home() / "Library" / "Caches" / "ashyterm"
        else:
            xdg_cache = os.environ.get('XDG_CACHE_HOME')
            if xdg_cache:
                return Path(xdg_cache) / "ashyterm"
            return Path.home() / ".cache" / "ashyterm"
    
    def _get_temp_directory(self) -> Path:
        """Get temporary directory."""
        import tempfile
        return Path(tempfile.gettempdir()) / "ashyterm"
    
    def _use_fallback_paths(self):
        """Use minimal fallback paths if initialization fails."""
        home = Path.home()
        self.CONFIG_DIR = home / ".config" / "ashyterm"
        self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
        self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
        self.FOLDERS_FILE = self.CONFIG_DIR / "folders.json"
        self.CACHE_DIR = home / ".cache" / "ashyterm"
        self.TEMP_DIR = Path("/tmp") / "ashyterm" if sys.platform != 'win32' else home / "AppData" / "Local" / "Temp" / "ashyterm"
        self.LOG_DIR = self.CONFIG_DIR / "logs"
        self.BACKUP_DIR = self.CONFIG_DIR / "backups"
        self.SECURE_DIR = self.CONFIG_DIR / "secure"


class NetworkConstants:
    """Network-related configuration constants."""
    
    # SSH connection settings
    SSH_CONNECT_TIMEOUT = 10  # seconds
    SSH_SERVER_ALIVE_INTERVAL = 5  # seconds
    SSH_SERVER_ALIVE_COUNT_MAX = 3
    SSH_TCP_KEEP_ALIVE = True
    
    # SSH security settings
    SSH_STRICT_HOST_KEY_CHECKING = 'ask'  # ask, yes, no
    SSH_HOST_KEY_ALGORITHMS = [
        'rsa-sha2-512',
        'rsa-sha2-256',
        'ecdsa-sha2-nistp256',
        'ecdsa-sha2-nistp384',
        'ecdsa-sha2-nistp521',
        'ssh-ed25519'
    ]
    
    # Network timeouts
    DNS_TIMEOUT = 5  # seconds
    CONNECTION_RETRY_COUNT = 3
    CONNECTION_RETRY_DELAY = 2  # seconds


class SecurityConstants:
    """Security-related configuration constants."""
    
    # Password and key security
    MIN_PASSWORD_LENGTH = 8
    MAX_PASSWORD_LENGTH = 256
    KEY_FILE_MAX_SIZE = 16 * 1024  # 16KB
    
    # Session validation
    MAX_SESSION_NAME_LENGTH = 128
    MAX_HOSTNAME_LENGTH = 253
    MAX_USERNAME_LENGTH = 32
    
    # File security
    SECURE_FILE_PERMISSIONS = 0o600
    SECURE_DIR_PERMISSIONS = 0o700
    
    # Encryption settings
    ENCRYPTION_KEY_SIZE = 32  # bytes
    ENCRYPTION_ALGORITHM = 'AES-256'
    KEY_DERIVATION_ITERATIONS = 100000


class DefaultSettings:
    """Default application settings with comprehensive configuration."""
    
    @staticmethod
    def get_defaults() -> Dict[str, Any]:
        """
        Get default application settings.
        
        Returns:
            Dictionary with default settings
        """
        return {
            # Appearance settings
            "color_scheme": 0,  # Index in COLOR_SCHEME_MAP
            "transparency": 0,  # Percentage (0-100)
            "font": "Monospace 10",
            
            # UI behavior
            "sidebar_visible": True,
            "auto_hide_sidebar": False,
            "show_toolbar": True,
            "confirm_close": True,
            "confirm_delete": True,
            
            # Terminal behavior
            "auto_close_tab": True,
            "auto_close_delay": 1000,  # milliseconds
            "scroll_on_output": True,
            "scroll_on_keystroke": True,
            "mouse_autohide": True,
            "cursor_blink": True,
            "bell_sound": False,
            "font_scale": 1.0, # Zoom level (1.0 = 100%)
            
            # Session management
            "restore_sessions": True,
            "auto_save_sessions": True,
            "session_backup": True,
            "max_recent_sessions": 10,
            "default_ssh_port": 22,
            
            # Security settings
            "security_warnings": True,
            "audit_sessions": True,
            "encrypt_passwords": True,
            "secure_file_permissions": True,
            
            # Backup settings
            "auto_backup_enabled": False, # Disable bkp temporary
            "backup_on_change": True,
            "backup_interval_hours": 24,
            "backup_retention_days": 30,
            "backup_on_exit": False,
            "max_backup_count": 10,
            
            # Advanced settings
            "debug_mode": False,
            "log_level": "INFO",
            "performance_mode": False,
            "experimental_features": False,
            
            # Keyboard shortcuts - keys must match GAction names
            "shortcuts": {
                "new-local-tab": "<Control>t",
                "close-tab": "<Control>w",
                "copy": "<Control><Shift>c",
                "paste": "<Control><Shift>v",
                "select-all": "<Control><Shift>a",
                "preferences": "<Control>comma",
                "quit": "<Control>q",
                "new-window": "<Control>n",
                "toggle-sidebar": "<Control><Shift>h",
                "find": "<Control>f",
                "zoom-in": "<Control>plus",
                "zoom-out": "<Control>minus",
                "zoom-reset": "<Control>0",
                "split-horizontal": "<Control><Shift>o",
                "split-vertical": "<Control><Shift>e",
                "close-pane": "<Control><Shift>w"
            }
        }


class ColorSchemes:
    """Terminal color schemes with extended palette support."""
    
    @staticmethod
    def get_schemes() -> Dict[str, Dict[str, Any]]:
        """
        Get all available color schemes.
        
        Returns:
            Dictionary mapping scheme names to scheme data
        """
        return {
            "system_default": {
                "name": "System Default",
                "description": "Use system's default terminal colors",
                "foreground": "#ffffff",
                "background": "#000000",
                "cursor": "#ffffff",
                "palette": [
                    "#000000", "#cc0000", "#4e9a06", "#c4a000",
                    "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                    "#555753", "#ef2929", "#8ae234", "#fce94f",
                    "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
                ]
            },
            "light": {
                "name": "Light",
                "description": "Light theme suitable for bright environments",
                "foreground": "#000000",
                "background": "#ffffff",
                "cursor": "#000000",
                "palette": [
                    "#000000", "#cc0000", "#4e9a06", "#c4a000",
                    "#3465a4", "#75507b", "#06989a", "#555753",
                    "#888a85", "#ef2929", "#8ae234", "#fce94f",
                    "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
                ]
            },
            "dark": {
                "name": "Dark",
                "description": "Dark theme for comfortable viewing",
                "foreground": "#ffffff",
                "background": "#1c1c1c",
                "cursor": "#ffffff",
                "palette": [
                    "#000000", "#cc0000", "#4e9a06", "#c4a000",
                    "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                    "#555753", "#ef2929", "#8ae234", "#fce94f",
                    "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
                ]
            },
            "solarized_light": {
                "name": "Solarized Light",
                "description": "Solarized light color scheme by Ethan Schoonover",
                "foreground": "#657b83",
                "background": "#fdf6e3",
                "cursor": "#657b83",
                "palette": [
                    "#073642", "#dc322f", "#859900", "#b58900",
                    "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83",
                    "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"
                ]
            },
            "solarized_dark": {
                "name": "Solarized Dark",
                "description": "Solarized dark color scheme by Ethan Schoonover",
                "foreground": "#839496",
                "background": "#002b36",
                "cursor": "#839496",
                "palette": [
                    "#073642", "#dc322f", "#859900", "#b58900",
                    "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83",
                    "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"
                ]
            },
            "monokai": {
                "name": "Monokai",
                "description": "Popular Monokai color scheme",
                "foreground": "#f8f8f2",
                "background": "#272822",
                "cursor": "#f8f8f2",
                "palette": [
                    "#272822", "#f92672", "#a6e22e", "#f4bf75",
                    "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
                    "#75715e", "#f92672", "#a6e22e", "#f4bf75",
                    "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5"
                ]
            },
            "dracula": {
                "name": "Dracula",
                "description": "Dracula theme colors",
                "foreground": "#f8f8f2",
                "background": "#282a36",
                "cursor": "#f8f8f2",
                "palette": [
                    "#000000", "#ff5555", "#50fa7b", "#f1fa8c",
                    "#bd93f9", "#ff79c6", "#8be9fd", "#bfbfbf",
                    "#4d4d4d", "#ff6e67", "#5af78e", "#f4f99d",
                    "#caa9fa", "#ff92d0", "#9aedfe", "#e6e6e6"
                ]
            },
            "nord": {
                "name": "Nord",
                "description": "Arctic, north-bluish color scheme",
                "foreground": "#d8dee9",
                "background": "#2e3440",
                "cursor": "#d8dee9",
                "palette": [
                    "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b",
                    "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                    "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b",
                    "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"
                ]
            }
        }


class ColorSchemeMap:
    """Maps combobox indices to color scheme names."""
    
    SCHEME_ORDER = [
        "system_default",
        "light", 
        "dark",
        "solarized_light",
        "solarized_dark",
        "monokai",
        "dracula",
        "nord"
    ]
    
    @classmethod
    def get_schemes_list(cls) -> List[str]:
        """Get ordered list of color scheme names."""
        return cls.SCHEME_ORDER.copy()
    
    @classmethod
    def get_scheme_index(cls, scheme_name: str) -> int:
        """Get index for a scheme name."""
        try:
            return cls.SCHEME_ORDER.index(scheme_name)
        except ValueError:
            return 0  # Default to system_default
    
    @classmethod
    def get_scheme_name(cls, index: int) -> str:
        """Get scheme name for an index."""
        if 0 <= index < len(cls.SCHEME_ORDER):
            return cls.SCHEME_ORDER[index]
        return cls.SCHEME_ORDER[0]  # Default to system_default


# Global instances
_config_paths = None
_logger = None


def get_config_paths() -> ConfigPaths:
    """
    Get global configuration paths instance.
    
    Returns:
        ConfigPaths instance
    """
    global _config_paths
    if _config_paths is None:
        _config_paths = ConfigPaths()
    return _config_paths


def get_config_logger():
    """Get logger for config module."""
    global _logger
    if _logger is None and UTILS_AVAILABLE:
        try:
            _logger = get_logger('ashyterm.config')
        except Exception:
            _logger = None
    return _logger


def validate_vte_availability() -> bool:
    """
    Validate VTE library availability with detailed error reporting.
    
    Returns:
        True if VTE is available and functional
        
    Raises:
        ConfigError: If VTE is not available
    """
    logger = get_config_logger()
    
    if not VTE_AVAILABLE:
        error_msg = "VTE library is not available"
        if 'VTE_ERROR' in globals():
            error_msg += f": {VTE_ERROR}"
        
        if logger:
            logger.critical(error_msg)
        
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.CRITICAL,
            details={
                'vte_available': False,
                'vte_error': globals().get('VTE_ERROR', 'Unknown error'),
                'required_version': AppConstants.REQUIRED_VTE_VERSION
            },
            user_message="Terminal functionality requires VTE library. Please install gir1.2-vte-2.91"
        )
    
    # Additional VTE functionality checks
    try:
        # Test basic VTE functionality
        terminal = Vte.Terminal()
        terminal.get_font()
        
        if logger:
            logger.info(f"VTE library validated successfully (version: {VTE_VERSION})")
        
        return True
        
    except Exception as e:
        error_msg = f"VTE library test failed: {e}"
        if logger:
            logger.error(error_msg)
        
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.HIGH,
            details={'vte_test_error': str(e)},
            user_message="VTE library is installed but not functioning correctly"
        )


def validate_python_version():
    """
    Validate Python version compatibility.
    
    Raises:
        ConfigError: If Python version is incompatible
    """
    logger = get_config_logger()
    current_version = sys.version_info[:2]
    
    if current_version < AppConstants.MIN_PYTHON_VERSION:
        error_msg = f"Python {AppConstants.MIN_PYTHON_VERSION[0]}.{AppConstants.MIN_PYTHON_VERSION[1]}+ required, got {current_version[0]}.{current_version[1]}"
        
        if logger:
            logger.critical(error_msg)
        
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.CRITICAL,
            details={
                'current_version': current_version,
                'required_version': AppConstants.MIN_PYTHON_VERSION
            },
            user_message=f"This application requires Python {AppConstants.MIN_PYTHON_VERSION[0]}.{AppConstants.MIN_PYTHON_VERSION[1]} or later"
        )


def initialize_configuration():
    """
    Initialize configuration system with validation and error handling.
    
    Raises:
        ConfigError: If critical configuration initialization fails
    """
    logger = get_config_logger()
    
    try:
        if logger:
            logger.info(f"Initializing {AppConstants.APP_TITLE} v{AppConstants.APP_VERSION}")
        
        # Validate system requirements
        validate_python_version()
        
        # Initialize paths
        paths = get_config_paths()
        
        if logger:
            logger.info(f"Configuration directory: {paths.CONFIG_DIR}")
            logger.info(f"VTE available: {VTE_AVAILABLE}")
            if VTE_AVAILABLE:
                logger.info(f"VTE version: {VTE_VERSION}")
        
        # Create essential directories
        try:
            essential_dirs = [
                paths.CONFIG_DIR,
                paths.LOG_DIR,
                paths.BACKUP_DIR
            ]
            
            for directory in essential_dirs:
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    if logger:
                        logger.debug(f"Created directory: {directory}")
        
        except Exception as e:
            if logger:
                logger.error(f"Failed to create essential directories: {e}")
            # Don't raise error for directory creation failures - app can still work
        
        if logger:
            logger.info("Configuration initialization completed successfully")
            
    except ConfigError:
        raise  # Re-raise config errors
    except Exception as e:
        error_msg = f"Configuration initialization failed: {e}"
        if logger:
            logger.critical(error_msg)
        
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.CRITICAL,
            details={'initialization_error': str(e)},
            user_message="Application initialization failed"
        )


# Legacy compatibility - maintain old constants for existing code
CONFIG_DIR = None
SESSIONS_FILE = None
SETTINGS_FILE = None
SSH_CONNECT_TIMEOUT = NetworkConstants.SSH_CONNECT_TIMEOUT
DEFAULT_SETTINGS = DefaultSettings.get_defaults()
COLOR_SCHEMES = ColorSchemes.get_schemes()
COLOR_SCHEME_MAP = ColorSchemeMap.get_schemes_list()

# Export AppConstants at module level for compatibility
APP_ID = AppConstants.APP_ID
APP_TITLE = AppConstants.APP_TITLE
APP_VERSION = AppConstants.APP_VERSION
DEVELOPER_NAME = AppConstants.DEVELOPER_NAME
DEVELOPER_TEAM = AppConstants.DEVELOPER_TEAM
COPYRIGHT = AppConstants.COPYRIGHT
WEBSITE = AppConstants.WEBSITE
ISSUE_URL = AppConstants.ISSUE_URL

# Initialize legacy paths
try:
    _paths = get_config_paths()
    CONFIG_DIR = str(_paths.CONFIG_DIR)
    SESSIONS_FILE = str(_paths.SESSIONS_FILE)
    SETTINGS_FILE = str(_paths.SETTINGS_FILE)
except Exception:
    # Fallback for legacy compatibility
    CONFIG_DIR = os.path.expanduser("~/.config/ashyterm")
    SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
    SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
    
    # Create directory if it doesn't exist
    os.makedirs(CONFIG_DIR, exist_ok=True)


# Initialize configuration on module import
try:
    initialize_configuration()
except Exception as e:
    # Don't fail module import, but log the error
    print(f"WARNING: Configuration initialization failed: {e}")
