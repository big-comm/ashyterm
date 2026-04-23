# ashyterm/core/signals.py
"""Singleton GObject event bus for cross-component signals.

Emit::

    AppSignals.get().emit("session-created", session_item)

Listen::

    AppSignals.get().connect("session-created", handler)
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject


class AppSignals(GObject.Object):
    """Application-wide event bus. Singleton, GObject-based."""

    __gsignals__ = {
        # Session signals
        "session-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "session-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "session-deleted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Folder signals
        "folder-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "folder-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "folder-deleted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # UI update requests
        "request-tree-refresh": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "request-session-select": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Settings signals
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "color-scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # Terminal signals
        "terminal-created": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "terminal-closed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "terminal-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    _instance = None

    def __init__(self):
        super().__init__()

    @classmethod
    def get(cls) -> "AppSignals":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton. Disconnects every handler — test-only."""
        cls._instance = None


def get_app_signals() -> AppSignals:
    return AppSignals.get()
