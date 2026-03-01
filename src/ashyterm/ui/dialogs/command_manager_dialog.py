"""Backward-compatibility re-export. Use .command_manager package directly."""

from .command_manager import CommandFormDialog, CommandManagerDialog
from .command_manager.command_button_widget import CommandButtonWidget
from .command_manager.command_editor_dialog import CommandEditorDialog

__all__ = [
    "CommandButtonWidget",
    "CommandEditorDialog",
    "CommandFormDialog",
    "CommandManagerDialog",
]
