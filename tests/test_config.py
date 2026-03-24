# tests/test_config.py
"""Tests for settings/config.py — ConfigPaths, DefaultSettings, ColorSchemes."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── ConfigPaths ──


class TestConfigPaths:
    @pytest.fixture
    def config_paths(self, tmp_path, monkeypatch):
        """Create ConfigPaths pointing to tmp_path."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

        # Mock the utility imports so ConfigPaths uses legacy path logic
        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import ConfigPaths as CP

            return CP()

    def test_config_dir_created(self, config_paths, tmp_path):
        assert config_paths.CONFIG_DIR.exists()
        assert config_paths.CONFIG_DIR == Path(tmp_path / "config" / "ashyterm")

    def test_sessions_file_path(self, config_paths):
        assert config_paths.SESSIONS_FILE.name == "sessions.json"

    def test_settings_file_path(self, config_paths):
        assert config_paths.SETTINGS_FILE.name == "settings.json"

    def test_state_file_path(self, config_paths):
        assert config_paths.STATE_FILE.name == "session_state.json"

    def test_subdirectories_created(self, config_paths):
        assert config_paths.CACHE_DIR.exists()
        assert config_paths.LOG_DIR.exists()
        assert config_paths.LAYOUT_DIR.exists()
        assert config_paths.BACKUP_DIR.exists()

    def test_cache_uses_xdg(self, config_paths, tmp_path):
        assert config_paths.CACHE_DIR == Path(tmp_path / "cache" / "ashyterm")


class TestConfigPathsFallback:
    def test_fallback_paths(self, tmp_path, monkeypatch):
        """When XDG vars are missing, use ~/.config/ and ~/.cache/."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import ConfigPaths as CP

            # Patch Path.home to use tmp_path
            with patch.object(Path, "home", return_value=tmp_path):
                cp = CP()
                assert "ashyterm" in str(cp.CONFIG_DIR)
                assert "ashyterm" in str(cp.CACHE_DIR)


# ── DefaultSettings ──


class TestDefaultSettings:
    @pytest.fixture(autouse=True)
    def _import_defaults(self):
        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import DefaultSettings as DS

            self.DS = DS

    def test_get_defaults_returns_dict(self):
        defaults = self.DS.get_defaults()
        assert isinstance(defaults, dict)

    def test_defaults_has_font(self):
        defaults = self.DS.get_defaults()
        assert "font" in defaults

    def test_defaults_has_transparency(self):
        defaults = self.DS.get_defaults()
        assert "transparency" in defaults
        assert isinstance(defaults["transparency"], (int, float))

    def test_defaults_has_scrollback(self):
        defaults = self.DS.get_defaults()
        assert "scrollback_lines" in defaults
        assert isinstance(defaults["scrollback_lines"], int)

    def test_defaults_has_color_scheme(self):
        defaults = self.DS.get_defaults()
        assert "color_scheme" in defaults


# ── ColorSchemes ──


class TestColorSchemes:
    @pytest.fixture(autouse=True)
    def _import_schemes(self):
        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import ColorSchemeMap, ColorSchemes

            self.ColorSchemes = ColorSchemes
            self.ColorSchemeMap = ColorSchemeMap

    def test_get_schemes_returns_dict(self):
        schemes = self.ColorSchemes.get_schemes()
        assert isinstance(schemes, dict)
        assert len(schemes) > 0

    def test_each_scheme_has_required_keys(self):
        schemes = self.ColorSchemes.get_schemes()
        for name, scheme in schemes.items():
            assert "foreground" in scheme, f"Missing foreground in {name}"
            assert "background" in scheme, f"Missing background in {name}"
            assert "palette" in scheme, f"Missing palette in {name}"

    def test_palette_has_16_colors(self):
        schemes = self.ColorSchemes.get_schemes()
        for name, scheme in schemes.items():
            palette = scheme["palette"]
            assert len(palette) == 16, f"{name} has {len(palette)} palette colors"

    def test_colors_are_valid_hex(self):
        import re

        hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        schemes = self.ColorSchemes.get_schemes()
        for name, scheme in schemes.items():
            assert hex_re.match(
                scheme["foreground"]
            ), f"Bad fg in {name}: {scheme['foreground']}"
            assert hex_re.match(
                scheme["background"]
            ), f"Bad bg in {name}: {scheme['background']}"
            for i, color in enumerate(scheme["palette"]):
                assert hex_re.match(
                    color
                ), f"Bad palette[{i}] in {name}: {color}"

    def test_scheme_map_returns_list(self):
        lst = self.ColorSchemeMap.get_schemes_list()
        assert isinstance(lst, list)
        assert len(lst) > 0

    def test_scheme_map_entries_are_strings(self):
        lst = self.ColorSchemeMap.get_schemes_list()
        assert all(isinstance(s, str) for s in lst)


# ── AppConstants ──


class TestAppConstants:
    def test_app_id(self):
        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import AppConstants

            assert AppConstants.APP_ID.startswith("org.")
            assert AppConstants.APP_TITLE == "Ashy Terminal"
            assert isinstance(AppConstants.APP_VERSION, str)


# ── PROMPT_TERMINATOR_PATTERN ──


class TestPromptTerminator:
    @pytest.fixture(autouse=True)
    def _import_pattern(self):
        with patch.dict(
            "sys.modules",
            {
                "ashyterm.utils.exceptions": MagicMock(
                    ConfigError=Exception, ErrorSeverity=MagicMock()
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.platform": MagicMock(
                    get_config_directory=MagicMock(return_value=None)
                ),
            },
        ):
            sys.modules.pop("ashyterm.settings.config", None)
            from ashyterm.settings.config import PROMPT_TERMINATOR_PATTERN

            self.pat = PROMPT_TERMINATOR_PATTERN

    def test_matches_dollar(self):
        assert self.pat.search("user@host:~$ ")

    def test_matches_hash(self):
        assert self.pat.search("root@host:~# ")

    def test_matches_zsh_arrow(self):
        assert self.pat.search("➜ ")

    def test_no_match_plain_text(self):
        assert self.pat.search("hello world") is None
