# tests/test_theme_and_transparency.py
"""
Tests for theme engine and transparency logic.

Covers: ThemeEngine parameter generation, CSS output structure,
sidebar transparency CSS, and adaptive alpha calculations.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestThemeEngineParams:
    """Tests for ThemeEngine.get_theme_params()."""

    def test_get_theme_params_defaults(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#1e1e2e", "foreground": "#cdd6f4"}
        params = ThemeEngine.get_theme_params(scheme)

        assert params["bg_color"] == "#1e1e2e"
        assert params["fg_color"] == "#cdd6f4"
        assert params["user_transparency"] == 0
        assert "terminal_transparency" not in params

    def test_get_theme_params_with_transparency(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#000000", "foreground": "#ffffff"}
        params = ThemeEngine.get_theme_params(scheme, transparency=50)

        assert params["user_transparency"] == 50

    def test_get_theme_params_no_terminal_transparency_param(self):
        """Ensure get_theme_params does NOT accept terminal_transparency."""
        from ashyterm.utils.theme_engine import ThemeEngine
        import inspect

        sig = inspect.signature(ThemeEngine.get_theme_params)
        param_names = list(sig.parameters.keys())
        assert "terminal_transparency" not in param_names

    def test_get_theme_params_headerbar_background_fallback(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme)

        # headerbar_background should fall back to bg_color when not in scheme
        assert params["header_bg_color"] == "#282828"

    def test_get_theme_params_headerbar_background_custom(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {
            "background": "#282828",
            "foreground": "#ebdbb2",
            "headerbar_background": "#3c3836",
        }
        params = ThemeEngine.get_theme_params(scheme)

        assert params["header_bg_color"] == "#3c3836"

    def test_dark_theme_detection(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        dark_scheme = {"background": "#1a1a2e", "foreground": "#e0e0e0"}
        light_scheme = {"background": "#fafafa", "foreground": "#333333"}

        dark_params = ThemeEngine.get_theme_params(dark_scheme)
        light_params = ThemeEngine.get_theme_params(light_scheme)

        assert dark_params["is_dark_theme"] is True
        assert light_params["is_dark_theme"] is False

    def test_luminance_calculation(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        # Pure black -> luminance ~0
        black = ThemeEngine.get_theme_params({"background": "#000000", "foreground": "#fff"})
        assert black["luminance"] == pytest.approx(0.0, abs=0.01)

        # Pure white -> luminance ~1
        white = ThemeEngine.get_theme_params({"background": "#ffffff", "foreground": "#000"})
        assert white["luminance"] == pytest.approx(1.0, abs=0.01)


class TestThemeEngineCSS:
    """Tests for CSS generation methods."""

    def test_root_vars_only_for_terminal_theme(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme)

        # Non-terminal theme returns empty
        css = ThemeEngine._get_root_vars_css(params, "adwaita")
        assert css == ""

        # Terminal theme returns CSS
        css = ThemeEngine._get_root_vars_css(params, "terminal")
        assert "--window-bg-color" in css
        assert "--sidebar-bg-color" in css

    def test_root_vars_uses_solid_colors(self):
        """CSS vars must use solid hex colors, not rgba with alpha."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme)
        css = ThemeEngine._get_root_vars_css(params, "terminal")

        assert "rgba(" not in css
        assert "#282828" in css

    def test_root_vars_skips_very_dark_backgrounds(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        # Very dark background (luminance < 0.05)
        scheme = {"background": "#050505", "foreground": "#ffffff"}
        params = ThemeEngine.get_theme_params(scheme)
        css = ThemeEngine._get_root_vars_css(params, "terminal")

        assert css == ""

    def test_popover_selectors_unified(self):
        """Both ashyterm-popover and sidebar-popover must have styled CSS."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme)
        css = ThemeEngine._get_root_vars_css(params, "terminal")

        assert "popover.sidebar-popover .sidebar-container" in css
        assert "popover.ashyterm-popover > contents" in css

    def test_headerbar_css_empty_when_no_transparency(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme, transparency=0)
        css = ThemeEngine._get_headerbar_css(params, "terminal")

        assert css == ""

    def test_headerbar_css_uses_color_mix(self):
        """Headerbar transparency must use color-mix, not rgba."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme, transparency=30)
        css = ThemeEngine._get_headerbar_css(params, "terminal")

        assert "color-mix" in css
        assert "70%" in css  # 100 - 30 = 70

    def test_headerbar_css_includes_searchbar_revealer(self):
        """Searchbar revealer selector must be present for GTK4 compatibility."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme, transparency=20)
        css = ThemeEngine._get_headerbar_css(params, "terminal")

        assert "searchbar > revealer > box" in css

    def test_tabs_css_only_for_terminal_theme(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme)

        assert ThemeEngine._get_tabs_css(params, "adwaita") == ""
        assert ".scrolled-tab-bar" in ThemeEngine._get_tabs_css(params, "terminal")

    def test_generate_app_css_combines_parts(self):
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {"background": "#282828", "foreground": "#ebdbb2"}
        params = ThemeEngine.get_theme_params(scheme, transparency=20)
        css = ThemeEngine.generate_app_css(params, "terminal")

        assert "--window-bg-color" in css
        assert "color-mix" in css
        assert ".scrolled-tab-bar" in css


class TestAdaptiveAlpha:
    """Tests for the adaptive alpha calculation in SettingsManager."""

    def _get_calc(self):
        """Import and return the _calculate_adaptive_alpha method."""
        from unittest.mock import MagicMock
        from ashyterm.settings.manager import SettingsManager

        # Create a minimal instance with mocked dependencies
        mgr = SettingsManager.__new__(SettingsManager)
        mgr.logger = MagicMock()
        return mgr._calculate_adaptive_alpha

    def test_zero_transparency_returns_one(self):
        calc = self._get_calc()
        alpha = calc("#282828", 0)
        assert alpha == pytest.approx(1.0, abs=0.01)

    def test_full_transparency(self):
        calc = self._get_calc()
        alpha = calc("#282828", 100)
        assert alpha < 0.1

    def test_dark_bg_more_transparent_than_light(self):
        """Dark backgrounds should get a transparency boost."""
        calc = self._get_calc()
        dark_alpha = calc("#111111", 50)
        light_alpha = calc("#eeeeee", 50)

        # Dark background gets a boost, so its alpha should be lower (more transparent)
        assert dark_alpha < light_alpha

    def test_alpha_always_in_range(self):
        calc = self._get_calc()
        for transparency in range(0, 101, 10):
            for bg in ["#000000", "#808080", "#ffffff"]:
                alpha = calc(bg, transparency)
                assert 0.0 <= alpha <= 1.0


class TestWelcomeScreenRemoved:
    """Regression tests ensuring welcome screen code is gone."""

    def test_no_first_run_tips_method(self):
        """Window class must not have _show_first_run_tips."""
        import importlib
        import ashyterm.window as win_module

        # Reload to get fresh module
        importlib.reload(win_module)
        source = open(win_module.__file__).read()
        assert "_show_first_run_tips" not in source

    def test_no_first_run_shown_setting(self):
        """No code should reference first_run_shown setting."""
        import ashyterm.window as win_module

        source = open(win_module.__file__).read()
        assert "first_run_shown" not in source
