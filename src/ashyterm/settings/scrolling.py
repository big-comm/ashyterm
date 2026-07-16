"""Terminal scrolling mode constants and normalization."""

from typing import Any

SCROLL_MODE_AUTOMATIC = "automatic"
SCROLL_MODE_CUSTOM = "custom"
SCROLL_MODE_NATIVE = "native"
SCROLL_MODES = (
    SCROLL_MODE_AUTOMATIC,
    SCROLL_MODE_CUSTOM,
    SCROLL_MODE_NATIVE,
)


def normalize_scroll_mode(value: Any) -> str:
    """Return a supported scroll mode, defaulting safely to automatic."""
    if isinstance(value, str) and value in SCROLL_MODES:
        return value
    return SCROLL_MODE_AUTOMATIC
