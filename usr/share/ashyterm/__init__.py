"""
Ashy Terminal - A modern terminal emulator with session management.

This package provides a comprehensive GTK4/Adwaita-based terminal emulator with advanced features.
"""

import warnings

# Import translation utility
from .utils.translation_utils import _

# Package metadata
__version__ = "1.0.1"
__version_info__ = (1, 0, 1)
__author__ = "BigCommunity Team"
__email__ = "contact@communitybig.org"
__license__ = "MIT"
__copyright__ = "© 2024 BigCommunity"
__url__ = "https://communitybig.org/"
__description__ = _("A modern terminal emulator with session management")

# Core availability flags
VTE_AVAILABLE = False
try:
    import gi

    gi.require_version("Vte", "3.91")
    from gi.repository import Vte

    VTE_AVAILABLE = True
except (ImportError, ValueError):
    warnings.warn(
        _("VTE 3.91 not found. Terminal functionality will be limited."),
        ImportWarning,
        stacklevel=2,
    )

# Export key components and error classes for easier access
__all__ = [
    "__version__",
    "VTE_AVAILABLE",
    "initialize_application",
    "cleanup_application",
    "AshyTerminalError",
    "InitializationError",
]

# Import functions and classes for the public API
from .app import CommTerminalApp
from .window import CommTerminalWindow
from .utils.exceptions import AshyTerminalError


class InitializationError(Exception):
    """Raised when package initialization fails."""

    pass


def initialize_application():
    """Placeholder for a more complex initialization if needed in the future."""
    pass

def cleanup_application():
    """Placeholder for cleanup logic."""
    pass