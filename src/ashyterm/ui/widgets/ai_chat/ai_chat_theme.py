# ashyterm/ui/widgets/ai_chat/ai_chat_theme.py
"""Theme-color resolver + CSS builder for the AI chat panel.

Two theme paths are supported:

* **Adwaita** — Named GTK accent/window colors; the panel blends into
  the surrounding toolkit theme.
* **Terminal** — Colors sampled from the user's active VTE color
  scheme so the chat bubbles stay coherent with what's in the terminal
  beneath them. Transparency is honored by dialing down alpha on the
  background.

Both paths produce the same dict shape so the CSS builder can emit a
single template regardless of source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from ...settings.manager import SettingsManager  # noqa: F401


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert ``#rrggbb`` to an integer triple; caller promises a valid hex."""
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def _rgba_background(rgb: tuple[int, int, int], transparency: int) -> str:
    """Format ``rgb(...)`` or ``rgba(...)`` depending on transparency.

    ``transparency`` is on a 0–100 UI scale. The ``**1.6`` exponent is
    the existing curve — it keeps low slider values close to opaque and
    accelerates toward full transparency as the user approaches 100.
    """
    r, g, b = rgb
    if transparency > 0:
        alpha = max(0.0, min(1.0, 1.0 - (transparency / 100.0) ** 1.6))
        return f"rgba({r}, {g}, {b}, {alpha})"
    return f"rgb({r}, {g}, {b})"


def _adwaita_colors(is_dark: bool) -> Dict[str, str]:
    """Return the Adwaita variant. Dark and light currently map to the
    same named colors — the GTK stylesheet resolves them at render time.
    """
    return {
        "rgba_bg": "@window_bg_color",
        "content_fg": "@window_fg_color",
        "bubble_user_bg": "@accent_bg_color",
        "bubble_user_fg": "@accent_fg_color",
        "bubble_assistant_bg": "@popover_bg_color",
        "bubble_assistant_border": "@borders",
        "input_bg": "@popover_bg_color",
        "input_border": "@borders",
        "scroll_bg": "transparent",
    }


def _terminal_colors(
    scheme: Dict[str, Any],
    is_dark: bool,
    transparency: int,
) -> Dict[str, str]:
    """Return the Terminal variant from a raw VTE color scheme dict.

    Expects keys ``background``/``foreground``/``palette`` / optional
    ``headerbar_background``. Falls back to black/white when a key is
    missing so we always produce a renderable CSS.
    """
    base = scheme.get("background", "#000000" if is_dark else "#ffffff")
    fg = scheme.get("foreground", "#ffffff" if is_dark else "#000000")
    header_bg = scheme.get("headerbar_background", base)
    palette = scheme.get("palette", [])
    accent = palette[4] if len(palette) > 4 else "@accent_bg_color"

    rgb = _hex_to_rgb(base)
    rgba_bg = _rgba_background(rgb, transparency)
    r, g, b = rgb

    # Contrast metric isn't actually used to pick the bubble text color
    # in the legacy code — both branches pick "@view_fg_color". We keep
    # the same behavior but compute the luminance so callers can extend
    # it later without re-reading the palette.
    if accent.startswith("#") and len(accent) >= 7:
        ar, ag, ab = _hex_to_rgb(accent)
    else:
        ar = ag = ab = 128
    _lum = (0.299 * ar + 0.587 * ag + 0.114 * ab) / 255

    return {
        "rgba_bg": rgba_bg,
        "content_fg": fg,
        "bubble_user_bg": accent,
        "bubble_user_fg": "@view_fg_color",
        "bubble_assistant_bg": header_bg,
        "bubble_assistant_border": f"color-mix(in srgb, {fg} 10%, transparent)",
        "input_bg": header_bg,
        "input_border": f"color-mix(in srgb, {fg} 10%, transparent)",
        "scroll_bg": (
            f"rgba({r}, {g}, {b}, 0.3)" if transparency > 0 else "transparent"
        ),
    }


def resolve_theme_colors(
    settings_manager, *, is_dark: bool, transparency: int
) -> Dict[str, str]:
    """Dispatch to the matching variant based on ``settings_manager`` state."""
    gtk_theme = settings_manager.get("gtk_theme", "")
    if gtk_theme == "terminal":
        scheme = settings_manager.get_color_scheme_data()
        return _terminal_colors(scheme, is_dark, transparency)
    return _adwaita_colors(is_dark)


def build_panel_css(c: Dict[str, str]) -> str:
    """Emit the chat-panel CSS string from a resolved color dict."""
    return f"""
            .ai-chat-panel {{
                background-color: {c["rgba_bg"]};
                color: {c["content_fg"]};
            }}
            .ai-chat-panel scrolledwindow {{
                background-color: {c["scroll_bg"]};
            }}
            .ai-message-user {{
                background-color: {c["bubble_user_bg"]};
                background-image: linear-gradient(135deg, {c["bubble_user_bg"]}, shade({c["bubble_user_bg"]}, 0.92));
                color: {c["bubble_user_fg"]};
                border-radius: 16px 16px 4px 16px;
                padding: 10px 14px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
            }}
            .ai-message-assistant {{
                background-color: {c["bubble_assistant_bg"]};
                color: {c["content_fg"]};
                border: 1px solid {c["bubble_assistant_border"]};
                border-radius: 16px 16px 16px 4px;
                padding: 10px 14px;
                box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
            }}
            .ai-message-assistant.ai-message-error {{
                border-color: rgba(255, 60, 60, 0.8);
                background-color: rgba(255, 60, 60, 0.1);
            }}
            .ai-command-block {{
                background-color: @popover_bg_color;
                color: @window_fg_color;
                border: 1px solid @borders;
                border-radius: 10px;
                padding: 12px 14px;
                transition: all 200ms ease;
            }}
            .ai-command-block:hover {{
                background-color: @card_bg_color;
                border-color: alpha(@accent_color, 0.4);
                box-shadow: 0 2px 8px alpha(@accent_color, 0.1);
            }}
            .ai-command-text {{
                color: @window_fg_color;
            }}
            .ai-input-box {{
                background-color: {c["input_bg"]};
                color: {c["content_fg"]};
                border: 1px solid {c["input_border"]};
                border-radius: 14px;
                padding: 6px 10px;
                transition: border-color 200ms ease, box-shadow 200ms ease;
            }}
            .ai-input-box:focus-within {{
                border-color: @accent_color;
                box-shadow: 0 0 0 2px alpha(@accent_color, 0.2);
            }}
            .ai-input-textview {{
                background-color: transparent;
                color: {c["content_fg"]};
                padding: 4px;
                min-height: 24px;
            }}
            .ai-input-textview text {{
                background-color: transparent;
                color: {c["content_fg"]};
            }}
            .ai-panel-header {{
                background-color: {c["input_bg"]};
                color: {c["content_fg"]};
            }}
            .ai-panel-header .title {{
                color: {c["content_fg"]};
            }}
            .ai-panel-header button {{
                color: {c["content_fg"]};
            }}
            .ai-panel-header button image {{
                color: {c["content_fg"]};
            }}
            """
