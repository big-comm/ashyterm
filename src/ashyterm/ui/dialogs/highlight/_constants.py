"""Shared constants for the highlight dialog package."""

from ...colors import (
    get_background_color_options,
    get_foreground_color_options,
    get_text_effect_options,
)
from ....settings.highlights import HighlightRule

LOGICAL_COLOR_OPTIONS = get_foreground_color_options()
TEXT_EFFECT_OPTIONS = get_text_effect_options()
BACKGROUND_COLOR_OPTIONS = get_background_color_options()


def get_rule_subtitle(rule: HighlightRule) -> str:
    """Get formatted subtitle for a rule."""
    if rule.description:
        return rule.description
    return rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
