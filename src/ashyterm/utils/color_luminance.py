# ashyterm/utils/color_luminance.py
"""Shared WCAG relative-luminance helpers.

Four places used to carry their own copy of the same formula with
slightly different input handling (hex, ``rgb(...)``, Gdk.RGBA). They
now all route through :func:`luminance_from_rgb_floats` below. The
wrappers expose convenient entry points for each input shape while
keeping the math in one spot.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# WCAG 2.x relative-luminance weights. Values are roughly CCIR 601 /
# Rec. 709 and apply to linearized sRGB. We use them on raw 0–1 sRGB
# because full gamma linearization doesn't pay off for tab-contrast UX.
_W_R: float = 0.2126
_W_G: float = 0.7152
_W_B: float = 0.0722

# Threshold for "light" background — above this, use black text;
# below, use white. Matches the legacy behavior callers all agreed on.
LIGHT_THRESHOLD: float = 0.5


_RGBA_COLOR_PATTERN = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+),?.*\)")


def luminance_from_rgb_floats(r: float, g: float, b: float) -> float:
    """Weighted sum of the three sRGB components in the 0–1 range."""
    return _W_R * r + _W_G * g + _W_B * b


def hex_to_rgb_floats(hex_color: str) -> Optional[Tuple[float, float, float]]:
    """Parse ``#rrggbb`` or ``rrggbb`` into a 0–1 triple. None on parse fail."""
    if not hex_color:
        return None
    try:
        h = hex_color.lstrip("#")
        return (
            int(h[0:2], 16) / 255.0,
            int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0,
        )
    except (ValueError, IndexError):
        return None


# Alias kept private so module-internal callers read naturally.
_hex_to_rgb_floats = hex_to_rgb_floats


def _rgba_to_rgb_floats(css_rgba: str) -> Optional[Tuple[float, float, float]]:
    """Parse ``rgb(r,g,b)`` / ``rgba(r,g,b,a)`` into a 0–1 triple."""
    if not css_rgba:
        return None
    match = _RGBA_COLOR_PATTERN.match(css_rgba)
    if not match:
        return None
    try:
        return tuple(int(c) / 255.0 for c in match.groups())  # type: ignore[return-value]
    except (ValueError, TypeError):
        return None


def is_light_hex(hex_color: str) -> bool:
    """Return True when a ``#rrggbb``-style color is perceptually light.

    Unparseable input returns False so callers default to dark-mode
    styling (white text) — the safer guess for syntax-highlighter use.
    """
    rgb = _hex_to_rgb_floats(hex_color)
    if rgb is None:
        return False
    return luminance_from_rgb_floats(*rgb) > LIGHT_THRESHOLD


def contrasting_text_for_hex(
    hex_color: str,
    *,
    on_light: str = "#000000",
    on_dark: str = "#FFFFFF",
    fallback: str = "#000000",
) -> str:
    """Pick ``on_light`` or ``on_dark`` for best contrast on ``hex_color``.

    ``fallback`` is returned when ``hex_color`` can't be parsed — kept
    configurable because callers prefer different defaults (the tab
    group chip wants black, others may want white).
    """
    rgb = _hex_to_rgb_floats(hex_color)
    if rgb is None:
        return fallback
    return on_light if luminance_from_rgb_floats(*rgb) > LIGHT_THRESHOLD else on_dark


def contrasting_text_for_rgba(
    css_rgba: str,
    *,
    on_light: str = "#000000",
    on_dark: str = "#FFFFFF",
    fallback: str = "#000000",
) -> str:
    """Same as :func:`contrasting_text_for_hex` but for ``rgb()`` / ``rgba()``."""
    rgb = _rgba_to_rgb_floats(css_rgba)
    if rgb is None:
        return fallback
    return on_light if luminance_from_rgb_floats(*rgb) > LIGHT_THRESHOLD else on_dark


def is_light_gdk_rgba(rgba) -> bool:
    """True when a ``Gdk.RGBA`` represents a perceptually light color.

    ``Gdk.RGBA`` exposes ``.red``, ``.green``, ``.blue`` already in the
    0–1 range; we skip parsing entirely.
    """
    if rgba is None:
        return False
    return (
        luminance_from_rgb_floats(rgba.red, rgba.green, rgba.blue)
        > LIGHT_THRESHOLD
    )
