"""Settings module for Ashy Terminal."""

from .config import *
from .manager import SettingsManager

__all__ = [
    "SettingsManager",
    # Config constants
    "APP_ID", "APP_TITLE", "APP_VERSION", "DEVELOPER_NAME", "DEVELOPER_TEAM",
    "COPYRIGHT", "WEBSITE", "ISSUE_URL", "CONFIG_DIR", "SESSIONS_FILE", 
    "SETTINGS_FILE", "SSH_CONNECT_TIMEOUT", "DEFAULT_SETTINGS", 
    "COLOR_SCHEMES", "COLOR_SCHEME_MAP", "VTE_AVAILABLE"
]