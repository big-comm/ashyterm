"""UI module for Ashy Terminal."""

# Import translation utility
from ..utils.translation_utils import _

from .dialogs import SessionEditDialog, FolderEditDialog, PreferencesDialog
from .menus import (
    MainApplicationMenu,
    create_session_menu,
    create_folder_menu,
    create_root_menu,
    create_terminal_menu,
)

__all__ = [
    # Dialogs
    "SessionEditDialog",
    "FolderEditDialog",
    "PreferencesDialog",
    # Menus
    "MainApplicationMenu",
    "create_session_menu",
    "create_folder_menu",
    "create_root_menu",
    "create_terminal_menu",
]