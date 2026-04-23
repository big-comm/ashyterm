"""Tests for ai_chat_theme (theme color resolution + CSS builder)."""

from unittest.mock import MagicMock

import pytest

from ashyterm.ui.widgets.ai_chat.ai_chat_theme import (
    _adwaita_colors,
    _hex_to_rgb,
    _rgba_background,
    _terminal_colors,
    build_panel_css,
    resolve_theme_colors,
)


# ── hex → rgb ────────────────────────────────────────────────


class TestHexToRgb:
    @pytest.mark.parametrize(
        "hex_input,expected",
        [
            ("#000000", (0, 0, 0)),
            ("#ffffff", (255, 255, 255)),
            ("#ff8040", (255, 128, 64)),
            ("#abcdef", (0xab, 0xcd, 0xef)),
        ],
    )
    def test_known_colors(self, hex_input, expected):
        assert _hex_to_rgb(hex_input) == expected


# ── rgba_background ──────────────────────────────────────────


class TestRgbaBackground:
    def test_zero_transparency_is_plain_rgb(self):
        assert _rgba_background((10, 20, 30), 0) == "rgb(10, 20, 30)"

    def test_full_transparency_alpha_near_zero(self):
        out = _rgba_background((10, 20, 30), 100)
        assert out.startswith("rgba(10, 20, 30, ")
        alpha = float(out.rsplit(", ", 1)[-1].rstrip(")"))
        assert alpha == 0.0

    def test_low_transparency_alpha_close_to_one(self):
        # transparency 10 ⇒ alpha = 1 - (0.1 ** 1.6) ≈ 0.9749 (barely off)
        out = _rgba_background((10, 20, 30), 10)
        alpha = float(out.rsplit(", ", 1)[-1].rstrip(")"))
        assert 0.95 < alpha < 1.0

    def test_mid_transparency_bands(self):
        # Exponent 1.6 means slider 50 is still fairly solid.
        out = _rgba_background((0, 0, 0), 50)
        alpha = float(out.rsplit(", ", 1)[-1].rstrip(")"))
        assert 0.5 < alpha < 1.0


# ── adwaita colors ───────────────────────────────────────────


class TestAdwaitaColors:
    def test_returns_named_gtk_colors(self):
        c = _adwaita_colors(is_dark=True)
        assert c["rgba_bg"] == "@window_bg_color"
        assert c["bubble_user_bg"] == "@accent_bg_color"
        assert c["scroll_bg"] == "transparent"

    def test_all_required_keys_present(self):
        required = {
            "rgba_bg",
            "content_fg",
            "bubble_user_bg",
            "bubble_user_fg",
            "bubble_assistant_bg",
            "bubble_assistant_border",
            "input_bg",
            "input_border",
            "scroll_bg",
        }
        assert set(_adwaita_colors(is_dark=True).keys()) == required
        assert set(_adwaita_colors(is_dark=False).keys()) == required


# ── terminal colors ──────────────────────────────────────────


class TestTerminalColors:
    def _scheme(self, **kwargs) -> dict:
        base = {
            "background": "#202020",
            "foreground": "#eeeeee",
            "palette": ["#ff0000"] * 8,  # enough entries; index 4 will be "#ff0000"
        }
        base.update(kwargs)
        return base

    def test_uses_scheme_background_and_foreground(self):
        c = _terminal_colors(self._scheme(), is_dark=True, transparency=0)
        assert c["rgba_bg"] == "rgb(32, 32, 32)"
        assert c["content_fg"] == "#eeeeee"

    def test_transparency_switches_to_rgba(self):
        c = _terminal_colors(self._scheme(), is_dark=True, transparency=40)
        assert c["rgba_bg"].startswith("rgba(")

    def test_accent_falls_back_when_palette_short(self):
        scheme = self._scheme(palette=["#111"] * 2)
        c = _terminal_colors(scheme, is_dark=True, transparency=0)
        # Palette index 4 is unavailable ⇒ named color fallback.
        assert c["bubble_user_bg"] == "@accent_bg_color"

    def test_header_bg_fallback_is_background(self):
        c = _terminal_colors(self._scheme(), is_dark=True, transparency=0)
        assert c["bubble_assistant_bg"] == "#202020"
        assert c["input_bg"] == "#202020"

    def test_header_bg_override_respected(self):
        scheme = self._scheme(headerbar_background="#333333")
        c = _terminal_colors(scheme, is_dark=True, transparency=0)
        assert c["bubble_assistant_bg"] == "#333333"

    def test_light_theme_defaults_to_white_bg(self):
        scheme = {"palette": []}  # missing background AND foreground
        c = _terminal_colors(scheme, is_dark=False, transparency=0)
        assert c["rgba_bg"] == "rgb(255, 255, 255)"
        assert c["content_fg"] == "#000000"

    def test_scroll_bg_becomes_semitransparent_with_transparency(self):
        c = _terminal_colors(self._scheme(), is_dark=True, transparency=50)
        assert c["scroll_bg"].startswith("rgba(")
        assert "0.3" in c["scroll_bg"]

    def test_scroll_bg_is_transparent_when_opaque(self):
        c = _terminal_colors(self._scheme(), is_dark=True, transparency=0)
        assert c["scroll_bg"] == "transparent"


# ── resolve_theme_colors dispatch ────────────────────────────


class TestResolveThemeColors:
    def test_terminal_theme_path(self):
        sm = MagicMock()
        sm.get = MagicMock(return_value="terminal")
        sm.get_color_scheme_data = MagicMock(
            return_value={
                "background": "#111111",
                "foreground": "#eeeeee",
                "palette": ["#ff0000"] * 8,
            }
        )
        c = resolve_theme_colors(sm, is_dark=True, transparency=0)
        assert c["rgba_bg"] == "rgb(17, 17, 17)"
        sm.get_color_scheme_data.assert_called_once()

    def test_adwaita_theme_path_skips_scheme_lookup(self):
        sm = MagicMock()
        sm.get = MagicMock(return_value="")
        c = resolve_theme_colors(sm, is_dark=False, transparency=0)
        assert c["rgba_bg"] == "@window_bg_color"
        sm.get_color_scheme_data.assert_not_called()

    def test_unknown_gtk_theme_falls_back_to_adwaita(self):
        sm = MagicMock()
        sm.get = MagicMock(return_value="system")
        c = resolve_theme_colors(sm, is_dark=False, transparency=0)
        assert c["rgba_bg"] == "@window_bg_color"


# ── build_panel_css ──────────────────────────────────────────


class TestBuildPanelCss:
    def test_css_substitutes_every_key(self):
        c = {
            "rgba_bg": "#101010",
            "content_fg": "#fff",
            "bubble_user_bg": "#00f",
            "bubble_user_fg": "#fff",
            "bubble_assistant_bg": "#222",
            "bubble_assistant_border": "#444",
            "input_bg": "#333",
            "input_border": "#666",
            "scroll_bg": "transparent",
        }
        css = build_panel_css(c)
        for value in c.values():
            assert value in css

    def test_every_required_selector_appears(self):
        c = {k: f"@{k}" for k in (
            "rgba_bg", "content_fg", "bubble_user_bg", "bubble_user_fg",
            "bubble_assistant_bg", "bubble_assistant_border",
            "input_bg", "input_border", "scroll_bg",
        )}
        css = build_panel_css(c)
        for selector in (
            ".ai-chat-panel",
            ".ai-message-user",
            ".ai-message-assistant",
            ".ai-message-assistant.ai-message-error",
            ".ai-command-block",
            ".ai-input-box",
            ".ai-panel-header",
        ):
            assert selector in css


# ── panel delegation ─────────────────────────────────────────


class TestPanelDelegation:
    def test_panel_exposes_delegators(self):
        from ashyterm.ui.widgets.ai_chat.ai_chat_panel import AIChatPanel

        for name in ("_resolve_theme_colors", "_build_transparency_css"):
            assert callable(getattr(AIChatPanel, name))
