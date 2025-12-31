# tests/conftest.py
"""
Pytest configuration for Ashy Terminal tests.

This module configures the test environment, including path setup
and mock configurations for GTK-dependent modules.
"""

import os
import sys
from unittest.mock import MagicMock

# Add src directory to Python path
src_path = os.path.join(os.path.dirname(__file__), "..", "src")
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
