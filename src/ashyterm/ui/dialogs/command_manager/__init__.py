"""Command manager dialog package — split from monolithic command_manager_dialog.py."""

from .command_form_dialog import CommandFormDialog
from .command_manager_dialog import CommandManagerDialog

__all__ = ["CommandFormDialog", "CommandManagerDialog"]
