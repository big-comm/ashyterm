"""Backward-compatibility re-export. Use .highlight package directly."""

from .highlight import HighlightDialog, RuleEditDialog
from .highlight._constants import get_rule_subtitle
from .highlight.color_entry_row import ColorEntryRow
from .highlight.context_rules_dialog import ContextRulesDialog
from .highlight.small_dialogs import (
    AddIgnoredCommandDialog,
    AddTriggerDialog,
    ContextNameDialog,
)

__all__ = [
    "ColorEntryRow",
    "ContextRulesDialog",
    "HighlightDialog",
    "RuleEditDialog",
    "ContextNameDialog",
    "AddTriggerDialog",
    "AddIgnoredCommandDialog",
    "get_rule_subtitle",
]
