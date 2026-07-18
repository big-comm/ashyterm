"""Persistent visual attention state for terminal tabs."""

from typing import Protocol


ATTENTION_CSS_CLASS = "tab-bell"


class CssClassWidget(Protocol):
    """Widget subset required by the attention state helpers."""

    def add_css_class(self, css_class: str) -> None: ...

    def remove_css_class(self, css_class: str) -> None: ...


def mark_tab_attention(tab_widget: CssClassWidget) -> None:
    """Mark a background tab as requiring attention."""
    tab_widget.add_css_class(ATTENTION_CSS_CLASS)


def clear_tab_attention(tab_widget: CssClassWidget) -> None:
    """Clear attention after the tab has been visited."""
    tab_widget.remove_css_class(ATTENTION_CSS_CLASS)
