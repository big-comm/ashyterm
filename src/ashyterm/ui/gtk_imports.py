# ashyterm/ui/gtk_imports.py
"""
Centralized GTK4/Adwaita imports for UI components.

This module provides a single import point for commonly used GTK widgets
and utilities, reducing code duplication across UI files.

Usage:
    from ..ui.gtk_imports import Adw, Gdk, GLib, Gtk, Pango
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

__all__ = ["Adw", "Gdk", "GLib", "Gtk", "Pango", "gi"]
