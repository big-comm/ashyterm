# tests/conftest.py
"""
Pytest configuration for Ashy Terminal tests.

This module configures the test environment, including path setup
and mock configurations for GTK-dependent modules.
"""

import atexit
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock

# Sandbox HOME / XDG_CONFIG_HOME / XDG_CACHE_HOME to a throwaway directory
# BEFORE any ashyterm module is imported. Several ashyterm modules read
# these at import time (ConfigPaths, PlatformInfo) and cache the result
# in module-level singletons — if the real user config dir ever leaks in,
# tests that exercise public methods on those singletons will write to
# ~/.config/ashyterm and pollute the real user state.
_test_home = tempfile.mkdtemp(prefix="ashyterm-tests-")
os.environ["HOME"] = _test_home
os.environ["XDG_CONFIG_HOME"] = os.path.join(_test_home, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_test_home, "cache")
os.environ.pop("XDG_DATA_HOME", None)
os.makedirs(os.environ["XDG_CONFIG_HOME"], exist_ok=True)
os.makedirs(os.environ["XDG_CACHE_HOME"], exist_ok=True)
atexit.register(lambda: shutil.rmtree(_test_home, ignore_errors=True))

# Add src directory to Python path - MUST be first to override system packages
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path in sys.path:
    sys.path.remove(src_path)
sys.path.insert(0, src_path)

# Mock GTK/GObject imports before any ashyterm imports
# This allows testing non-GTK code without requiring a display


def mock_gi_module():
    """Create a mock gi module to prevent GTK import errors."""
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()

    # Mock repository classes
    mock_repository = MagicMock()
    mock_repository.Gtk = MagicMock()
    mock_repository.Gdk = MagicMock()
    mock_repository.Adw = MagicMock()
    mock_repository.Vte = MagicMock()
    mock_repository.Gio = MagicMock()
    mock_repository.GLib = MagicMock()
    mock_repository.GObject = MagicMock()
    mock_repository.Pango = MagicMock()

    mock_gi.repository = mock_repository

    return mock_gi


# Only mock gi if not in a GTK environment
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    sys.modules["gi"] = mock_gi_module()
    sys.modules["gi.repository"] = sys.modules["gi"].repository


import pytest


@pytest.fixture(autouse=True)
def _reset_ashyterm_singletons():
    """Clear module-level singletons before each test so state cannot
    survive across tests via cached ConfigPaths / PlatformInfo /
    CommandButtonManager instances."""
    try:
        import ashyterm.settings.config as _cfg

        _cfg._config_paths = None
    except ImportError:
        pass
    try:
        import ashyterm.utils.platform as _plat

        _plat._platform_info = None
    except ImportError:
        pass
    try:
        from ashyterm.data.command_manager_models import CommandButtonManager

        CommandButtonManager._instance = None
    except ImportError:
        pass
    yield
