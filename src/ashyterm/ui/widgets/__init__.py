# ashyterm/ui/widgets/__init__.py

"""Custom UI widgets for AshyTerm."""

from .ai_chat_panel import AIChatPanel
from .base_syntax_text_view import BaseSyntaxTextView
from .bash_text_view import BashTextView
from .conversation_history import ConversationHistoryPanel
from .form_widget_builder import (
    FieldConfig,
    FormWidgetBuilder,
    create_field_from_dict,
    create_field_from_form_field,
)
from .inline_context_menu import InlineContextMenu
from .regex_text_view import RegexTextView

__all__ = [
    "AIChatPanel",
    "BaseSyntaxTextView",
    "BashTextView",
    "ConversationHistoryPanel",
    "FieldConfig",
    "FormWidgetBuilder",
    "InlineContextMenu",
    "RegexTextView",
    "create_field_from_dict",
    "create_field_from_form_field",
]
