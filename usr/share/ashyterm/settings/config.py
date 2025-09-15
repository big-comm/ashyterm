# ashyterm/settings/config.py

import os
from pathlib import Path
from typing import Any, Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

try:
    from ..utils.exceptions import ConfigError, ErrorSeverity
    from ..utils.logger import get_logger
    from ..utils.platform import get_config_directory

    UTILS_AVAILABLE = True
except ImportError:
    UTILS_AVAILABLE = False
    get_config_directory = None


class AppConstants:
    """Application metadata and identification constants."""

    APP_ID = "org.communitybig.ashyterm"
    APP_TITLE = "Ashy Terminal"
    APP_VERSION = "1.1.0"
    DEVELOPER_NAME = "BigCommunity"
    DEVELOPER_TEAM = ["BigCommunity Team"]
    COPYRIGHT = "© 2025 BigCommunity"
    WEBSITE = "https://communitybig.org/"
    ISSUE_URL = "https://github.com/big-comm/comm-ashyterm/issues"


class ConfigPaths:
    """Platform-aware configuration paths for Linux."""

    def __init__(self):
        self.logger = get_logger("ashyterm.config.paths") if UTILS_AVAILABLE else None
        self._setup_paths()

    def _setup_paths(self):
        try:
            if UTILS_AVAILABLE and (config_dir := get_config_directory()):
                self.CONFIG_DIR = config_dir
            else:
                self.CONFIG_DIR = self._get_legacy_config_dir()

            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
            self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
            self.STATE_FILE = self.CONFIG_DIR / "session_state.json"
            self.LAYOUT_DIR = self.CONFIG_DIR / "layouts"
            self.CUSTOM_COMMANDS_FILE = self.CONFIG_DIR / "custom_commands.json"
            self.CACHE_DIR = self._get_cache_directory()
            self.LOG_DIR = self.CONFIG_DIR / "logs"
            self.BACKUP_DIR = (
                self.CONFIG_DIR / "backups"
            )  # Directory for manual backups

            for directory in [
                self.CACHE_DIR,
                self.LOG_DIR,
                self.LAYOUT_DIR,
                self.BACKUP_DIR,
            ]:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    if self.logger:
                        self.logger.warning(
                            f"Failed to create directory {directory}: {e}"
                        )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to initialize config paths: {e}")
            self._use_fallback_paths()

    def _get_legacy_config_dir(self) -> Path:
        if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
            return Path(xdg_config) / "ashyterm"
        return Path.home() / ".config" / "ashyterm"

    def _get_cache_directory(self) -> Path:
        if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
            return Path(xdg_cache) / "ashyterm"
        return Path.home() / ".cache" / "ashyterm"

    def _use_fallback_paths(self):
        home = Path.home()
        self.CONFIG_DIR = home / ".config" / "ashyterm"
        self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
        self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
        self.STATE_FILE = self.CONFIG_DIR / "session_state.json"
        self.LAYOUT_DIR = self.CONFIG_DIR / "layouts"
        self.CUSTOM_COMMANDS_FILE = self.CONFIG_DIR / "custom_commands.json"
        self.CACHE_DIR = home / ".cache" / "ashyterm"
        self.LOG_DIR = self.CONFIG_DIR / "logs"
        self.BACKUP_DIR = self.CONFIG_DIR / "backups"


class DefaultSettings:
    """Default application settings."""

    @staticmethod
    def get_defaults() -> Dict[str, Any]:
        return {
            # General Appearance
            "gtk_theme": "default",
            "color_scheme": 5,
            "transparency": 12,
            "headerbar_transparency": 10,
            "font": "Monospace 12",
            "line_spacing": 1.0,
            "bold_is_bright": True,
            "tab_alignment": "center",
            # Window State
            "window_width": 1200,
            "window_height": 700,
            "window_maximized": False,
            "remember_window_state": True,
            # Behavior
            "sidebar_visible": False,
            "auto_hide_sidebar": True,
            "sidebar_width": 300,  # Default sidebar width in pixels
            "scroll_on_output": True,  # Enables smart scrolling
            "scroll_on_keystroke": True,
            "scroll_on_insert": True,  # Scroll to bottom on paste
            "mouse_autohide": True,
            "cursor_blink": 0,
            "new_instance_behavior": "new_tab",
            "use_login_shell": False,
            "session_restore_policy": "never",
            # VTE Features
            "scrollback_lines": 10000,
            "mouse_scroll_sensitivity": 30.0,
            "touchpad_scroll_sensitivity": 30.0,
            "cursor_shape": 0,
            "bidi_enabled": False,
            "enable_shaping": False,  # For Arabic text shaping
            "sixel_enabled": True,
            "text_blink_mode": 0,
            "accessibility_enabled": True,
            # Compatibility & Advanced
            "backspace_binding": 0,
            "delete_binding": 0,
            "cjk_ambiguous_width": 1,
            "word_char_exceptions": "-_.:/~",  # For word selection on double-click
            # Logging Settings
            "log_to_file": False,
            "console_log_level": "ERROR",
            # Remote Editing
            "use_system_tmp_for_edit": False,
            "clear_remote_edit_files_on_exit": False,
            # Search Settings
            "search_case_sensitive": False,
            "search_use_regex": False,
            # Shortcuts
            "shortcuts": {
                "new-local-tab": "<Control><Shift>t",
                "close-tab": "<Control><Shift>w",
                "copy": "<Control><Shift>c",
                "paste": "<Control><Shift>v",
                "select-all": "<Control><Shift>a",
                "preferences": "<Control><Shift>comma",
                "quit": "<Control><Shift>q",
                "new-window": "<Control><Shift>n",
                "toggle-sidebar": "<Control><Shift>h",
                "show-command-guide": "<Control><Shift>p",
                "zoom-in": "<Control>plus",
                "zoom-out": "<Control>minus",
                "zoom-reset": "<Control>0",
                "split-horizontal": "<Control>parenleft",
                "split-vertical": "<Control>parenright",
                "close-pane": "<Control><Shift>k",
                "next-tab": "<Alt>Page_Down",
                "previous-tab": "<Alt>Page_Up",
                "toggle-file-manager": "<Control><Shift>e",
            },
        }


class ColorSchemes:
    """Terminal color schemes."""

    @staticmethod
    def get_schemes() -> Dict[str, Dict[str, Any]]:
        return {
            "system_default": {
                "name": "System Default",
                "foreground": "#ffffff",
                "background": "#000000",
                "cursor": "#ffffff",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#d3d7cf",
                    "#555753",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "light": {
                "name": "Light",
                "foreground": "#000000",
                "background": "#ffffff",
                "cursor": "#000000",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#555753",
                    "#888a85",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "dark": {
                "name": "Dark",
                "foreground": "#ffffff",
                "background": "#1c1c1c",
                "cursor": "#ffffff",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#d3d7cf",
                    "#555753",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "solarized_light": {
                "name": "Solarized Light",
                "foreground": "#657b83",
                "background": "#fdf6e3",
                "cursor": "#657b83",
                "palette": [
                    "#073642",
                    "#dc322f",
                    "#859900",
                    "#b58900",
                    "#268bd2",
                    "#d33682",
                    "#2aa198",
                    "#eee8d5",
                    "#002b36",
                    "#cb4b16",
                    "#586e75",
                    "#657b83",
                    "#839496",
                    "#6c71c4",
                    "#93a1a1",
                    "#fdf6e3",
                ],
            },
            "solarized_dark": {
                "name": "Solarized Dark",
                "foreground": "#839496",
                "background": "#002b36",
                "cursor": "#839496",
                "palette": [
                    "#073642",
                    "#dc322f",
                    "#859900",
                    "#b58900",
                    "#268bd2",
                    "#d33682",
                    "#2aa198",
                    "#eee8d5",
                    "#002b36",
                    "#cb4b16",
                    "#586e75",
                    "#657b83",
                    "#839496",
                    "#6c71c4",
                    "#93a1a1",
                    "#fdf6e3",
                ],
            },
            "monokai": {
                "name": "Monokai",
                "foreground": "#f8f8f2",
                "background": "#272822",
                "cursor": "#f8f8f2",
                "palette": [
                    "#272822",
                    "#f92672",
                    "#a6e22e",
                    "#f4bf75",
                    "#66d9ef",
                    "#ae81ff",
                    "#a1efe4",
                    "#f8f8f2",
                    "#75715e",
                    "#f92672",
                    "#a6e22e",
                    "#f4bf75",
                    "#66d9ef",
                    "#ae81ff",
                    "#a1efe4",
                    "#f9f8f5",
                ],
            },
            "dracula": {
                "name": "Dracula",
                "foreground": "#f8f8f2",
                "background": "#282a36",
                "cursor": "#f8f8f2",
                "palette": [
                    "#000000",
                    "#ff5555",
                    "#50fa7b",
                    "#f1fa8c",
                    "#bd93f9",
                    "#ff79c6",
                    "#8be9fd",
                    "#bfbfbf",
                    "#4d4d4d",
                    "#ff6e67",
                    "#5af78e",
                    "#f4f99d",
                    "#caa9fa",
                    "#ff92d0",
                    "#9aedfe",
                    "#e6e6e6",
                ],
            },
            "nord": {
                "name": "Nord",
                "foreground": "#d8dee9",
                "background": "#2e3440",
                "cursor": "#d8dee9",
                "palette": [
                    "#3b4252",
                    "#bf616a",
                    "#a3be8c",
                    "#ebcb8b",
                    "#81a1c1",
                    "#b48ead",
                    "#88c0d0",
                    "#e5e9f0",
                    "#4c566a",
                    "#bf616a",
                    "#a3be8c",
                    "#ebcb8b",
                    "#81a1c1",
                    "#b48ead",
                    "#8fbcbb",
                    "#eceff4",
                ],
            },
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
        "nord",
    ]

    @classmethod
    def get_schemes_list(cls) -> List[str]:
        return cls.SCHEME_ORDER.copy()


_config_paths = None


def get_config_paths() -> ConfigPaths:
    """Get global configuration paths instance."""
    global _config_paths
    if _config_paths is None:
        _config_paths = ConfigPaths()
    return _config_paths


def initialize_configuration():
    """Initialize configuration system with validation."""
    logger = get_logger("ashyterm.config") if UTILS_AVAILABLE else None
    try:
        if logger:
            logger.info(
                f"Initializing {AppConstants.APP_TITLE} v{AppConstants.APP_VERSION}"
            )
        paths = get_config_paths()
        if logger:
            logger.info(f"Configuration directory: {paths.CONFIG_DIR}")
    except Exception as e:
        error_msg = f"Configuration initialization failed: {e}"
        if logger:
            logger.critical(error_msg)
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.CRITICAL,
            user_message="Application initialization failed",
        )


try:
    initialize_configuration()
except Exception as e:
    print(f"WARNING: Configuration initialization failed: {e}")

APP_ID = AppConstants.APP_ID
APP_TITLE = AppConstants.APP_TITLE
APP_VERSION = AppConstants.APP_VERSION
DEVELOPER_NAME = AppConstants.DEVELOPER_NAME
DEVELOPER_TEAM = AppConstants.DEVELOPER_TEAM
COPYRIGHT = AppConstants.COPYRIGHT
WEBSITE = AppConstants.WEBSITE
ISSUE_URL = AppConstants.ISSUE_URL

try:
    _paths = get_config_paths()
    CONFIG_DIR = str(_paths.CONFIG_DIR)
    SESSIONS_FILE = str(_paths.SESSIONS_FILE)
    SETTINGS_FILE = str(_paths.SETTINGS_FILE)
    STATE_FILE = str(_paths.STATE_FILE)
    LAYOUT_DIR = str(_paths.LAYOUT_DIR)
    CUSTOM_COMMANDS_FILE = str(_paths.CUSTOM_COMMANDS_FILE)
    BACKUP_DIR = str(_paths.BACKUP_DIR)
except Exception:
    CONFIG_DIR = os.path.expanduser("~/.config/ashyterm")
    SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
    SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
    STATE_FILE = os.path.join(CONFIG_DIR, "session_state.json")
    LAYOUT_DIR = os.path.join(CONFIG_DIR, "layouts")
    CUSTOM_COMMANDS_FILE = os.path.join(CONFIG_DIR, "custom_commands.json")
    BACKUP_DIR = os.path.join(CONFIG_DIR, "backups")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
