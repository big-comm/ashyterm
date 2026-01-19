# tests/test_refactored_functions.py
"""
Tests for refactored functions to ensure CC reduction didn't break functionality.

These tests verify the helper functions extracted during cognitive complexity reduction.
"""

import os
import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestColorsModule:
    """Tests for refactored color parsing functions."""

    def test_parse_color_part_modifier(self):
        """Test parsing a modifier part."""
        from ashyterm.ui.colors import ANSI_MODIFIERS, _parse_color_part

        modifiers = []
        bg_holder = []
        result = _parse_color_part("bold", modifiers, bg_holder)

        assert result is None  # Modifiers don't return a base color
        assert len(modifiers) == 1
        assert modifiers[0] == ANSI_MODIFIERS["bold"]

    def test_parse_color_part_background(self):
        """Test parsing a background color part."""
        from ashyterm.ui.colors import _parse_color_part

        modifiers = []
        bg_holder = []
        result = _parse_color_part("on_red", modifiers, bg_holder)

        assert result is None
        assert len(bg_holder) == 1
        assert bg_holder[0] == "41"  # Red background code

    def test_parse_color_part_foreground(self):
        """Test parsing a foreground color part."""
        from ashyterm.ui.colors import _parse_color_part

        modifiers = []
        bg_holder = []
        result = _parse_color_part("blue", modifiers, bg_holder)

        assert result == "blue"  # Returns base color
        assert len(modifiers) == 0
        assert len(bg_holder) == 0

    def test_get_fg_code_standard_color(self):
        """Test getting foreground code for standard colors."""
        from ashyterm.ui.colors import _get_fg_code

        assert _get_fg_code("red") == "31"
        assert _get_fg_code("green") == "32"
        assert _get_fg_code("blue") == "34"

    def test_get_fg_code_bright_color(self):
        """Test getting foreground code for bright colors."""
        from ashyterm.ui.colors import _get_fg_code

        assert _get_fg_code("bright_red") == "91"
        assert _get_fg_code("bright_green") == "92"

    def test_get_fg_code_special_names(self):
        """Test that special names return empty string."""
        from ashyterm.ui.colors import _get_fg_code

        assert _get_fg_code("foreground") == ""
        assert _get_fg_code("background") == ""
        assert _get_fg_code("cursor") == ""
        assert _get_fg_code("none") == ""
        assert _get_fg_code("default") == ""

    def test_resolve_color_to_ansi_code_simple(self):
        """Test resolving simple color to ANSI."""
        from ashyterm.ui.colors import resolve_color_to_ansi_code

        result = resolve_color_to_ansi_code("red")
        assert result == "\033[31m"

    def test_resolve_color_to_ansi_code_with_modifier(self):
        """Test resolving color with modifier."""
        from ashyterm.ui.colors import resolve_color_to_ansi_code

        result = resolve_color_to_ansi_code("bold red")
        assert "1" in result  # Bold code
        assert "31" in result  # Red code

    def test_resolve_color_to_ansi_code_with_background(self):
        """Test resolving color with background."""
        from ashyterm.ui.colors import resolve_color_to_ansi_code

        result = resolve_color_to_ansi_code("red on_green")
        assert "31" in result  # Red foreground
        assert "42" in result  # Green background

    def test_resolve_color_to_ansi_code_empty(self):
        """Test empty string returns empty."""
        from ashyterm.ui.colors import resolve_color_to_ansi_code

        assert resolve_color_to_ansi_code("") == ""


class TestShellEchoModule:
    """Tests for refactored shell echo functions."""

    def test_is_csi_sequence_complete_simple(self):
        """Test CSI sequence completion check."""
        from ashyterm.utils.shell_echo import _is_csi_sequence_complete

        # Complete CSI sequence (e.g., cursor movement)
        # The function expects bytes and the start index of ESC
        data = b"\x1b[1;2H"
        assert _is_csi_sequence_complete(data, 0) is True
        # Incomplete - missing final byte
        data = b"\x1b[1;2"
        assert _is_csi_sequence_complete(data, 0) is False

    def test_is_osc_sequence_complete_with_bel(self):
        """Test OSC sequence completion with BEL."""
        from ashyterm.utils.shell_echo import _is_osc_sequence_complete

        # OSC terminated with BEL
        data = b"\x1b]0;title\x07"
        assert _is_osc_sequence_complete(data, 0) is True

    def test_is_osc_sequence_complete_with_st(self):
        """Test OSC sequence completion with ST."""
        from ashyterm.utils.shell_echo import _is_osc_sequence_complete

        # OSC terminated with ST (ESC \)
        data = b"\x1b]0;title\x1b\\"
        assert _is_osc_sequence_complete(data, 0) is True

    def test_is_osc_sequence_incomplete(self):
        """Test incomplete OSC sequence."""
        from ashyterm.utils.shell_echo import _is_osc_sequence_complete

        # Incomplete - no terminator
        data = b"\x1b]0;title"
        assert _is_osc_sequence_complete(data, 0) is False

    def test_split_incomplete_escape_suffix_complete(self):
        """Test splitting complete escape sequence."""
        from ashyterm.utils.shell_echo import split_incomplete_escape_suffix

        # The function returns bytes, not strings
        data = b"hello\x1b[32mworld"
        complete, incomplete = split_incomplete_escape_suffix(data)
        assert complete == data
        assert incomplete == b""

    def test_split_incomplete_escape_suffix_incomplete(self):
        """Test splitting incomplete escape sequence."""
        from ashyterm.utils.shell_echo import split_incomplete_escape_suffix

        data = b"hello\x1b[32"
        complete, incomplete = split_incomplete_escape_suffix(data)
        assert complete == b"hello"
        assert incomplete == b"\x1b[32"


class TestPlatformModule:
    """Tests for refactored platform utility functions."""

    def test_validate_existing_directory_is_dir(self, tmp_path):
        """Test validation passes for existing directory."""
        from ashyterm.utils.platform import _validate_existing_directory

        # tmp_path is a pytest fixture that creates a temp directory
        assert _validate_existing_directory(tmp_path) is True

    def test_validate_existing_directory_is_file(self, tmp_path):
        """Test validation fails for file instead of directory."""
        from ashyterm.utils.platform import ConfigError, _validate_existing_directory

        # Create a file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        with pytest.raises(ConfigError):
            _validate_existing_directory(test_file)


class TestSessionModels:
    """Tests for refactored SessionItem validation methods."""

    def test_validate_basic_fields_with_name(self):
        """Test basic field validation with valid name."""
        from ashyterm.sessions.models import SessionItem

        session = SessionItem(name="Test Session", session_type="local")
        errors = session._validate_basic_fields()
        assert len(errors) == 0

    def test_validate_basic_fields_without_name(self):
        """Test basic field validation without name.
        Note: SessionItem sanitizes empty names to a default value,
        so we test by directly setting the internal _name attribute.
        """
        from ashyterm.sessions.models import SessionItem

        session = SessionItem(name="Valid Name", session_type="local")
        session._name = ""  # Bypass sanitization to test validation
        errors = session._validate_basic_fields()
        assert len(errors) == 1

    def test_validate_ssh_fields_valid(self):
        """Test SSH field validation with valid data."""
        from ashyterm.sessions.models import SessionItem

        session = SessionItem(
            name="SSH Session",
            session_type="ssh",
            host="example.com",
            port=22,
        )
        errors = session._validate_ssh_fields()
        assert len(errors) == 0

    def test_validate_ssh_fields_invalid_port(self):
        """Test SSH field validation with invalid port."""
        from ashyterm.sessions.models import SessionItem

        session = SessionItem(
            name="SSH Session",
            session_type="ssh",
            host="example.com",
            port=99999,  # Invalid port
        )
        errors = session._validate_ssh_fields()
        assert len(errors) > 0

    def test_validate_sftp_directory_not_enabled(self):
        """Test SFTP validation when not enabled."""
        from ashyterm.sessions.models import SessionItem

        session = SessionItem(
            name="SSH Session",
            session_type="ssh",
            host="example.com",
            sftp_session_enabled=False,
        )
        errors = session._validate_sftp_directory()
        assert len(errors) == 0

    def test_get_validation_errors_complete(self):
        """Test complete validation error collection."""
        from ashyterm.sessions.models import SessionItem

        # Invalid session - no name, invalid port
        session = SessionItem(
            name="",
            session_type="ssh",
            host="",
            port=0,
        )
        errors = session.get_validation_errors()
        # Should have multiple errors
        assert len(errors) >= 2


class TestSSHConfigParser:
    """Tests for refactored SSH config parser functions."""

    def test_resolve_config_path_nonexistent(self, tmp_path):
        """Test path resolution for non-existent file."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        result = parser._resolve_config_path(tmp_path / "nonexistent")
        assert result is None

    def test_resolve_config_path_is_directory(self, tmp_path):
        """Test path resolution for directory."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        result = parser._resolve_config_path(tmp_path)  # tmp_path is a directory
        assert result is None

    def test_resolve_config_path_valid_file(self, tmp_path):
        """Test path resolution for valid file."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        config_file = tmp_path / "config"
        config_file.write_text("# SSH config")

        parser = SSHConfigParser()
        result = parser._resolve_config_path(config_file)
        assert result is not None
        assert result.exists()

    def test_process_config_line_host(self):
        """Test processing Host directive."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        patterns, options, stop = parser._process_config_line(
            "host", ["server1"], [], {}, Path("/")
        )
        assert patterns == ["server1"]
        assert options == {}
        assert stop is False

    def test_process_config_line_match_stops(self):
        """Test that Match directive stops processing."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        _, _, stop = parser._process_config_line(
            "match", ["all"], ["server1"], {"user": "admin"}, Path("/")
        )
        assert stop is True

    def test_process_config_line_option(self):
        """Test processing regular option."""
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        current_patterns = ["server1"]
        current_options = {}
        patterns, options, stop = parser._process_config_line(
            "user", ["admin"], current_patterns, current_options, Path("/")
        )
        assert patterns == current_patterns
        assert options.get("user") == "admin"
        assert stop is False


class TestThemeEngine:
    """Tests for ThemeEngine CSS generation."""

    def test_get_theme_params_dark(self):
        """Test theme params for dark background."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {
            "background": "#000000",
            "foreground": "#ffffff",
            "headerbar_background": "#000000",
        }

        params = ThemeEngine.get_theme_params(scheme)

        assert params["bg_color"] == "#000000"
        assert params["fg_color"] == "#ffffff"
        assert params["is_dark_theme"] is True
        assert params["luminance"] < 0.5

    def test_get_theme_params_light(self):
        """Test theme params for light background."""
        from ashyterm.utils.theme_engine import ThemeEngine

        scheme = {
            "background": "#ffffff",
            "foreground": "#000000",
        }

        params = ThemeEngine.get_theme_params(scheme)

        assert params["bg_color"] == "#ffffff"
        assert params["fg_color"] == "#000000"
        assert params["is_dark_theme"] is False
        assert params["luminance"] > 0.5

    def test_get_root_vars_css_terminal_theme(self):
        """Test root vars CSS generation for 'terminal' theme."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {
            "bg_color": "#1a1a1a",
            "fg_color": "#ffffff",
            "header_bg_color": "#1a1a1a",
            "luminance": 0.1,
        }

        css = ThemeEngine._get_root_vars_css(params, "terminal")

        assert ":root" in css
        assert "--window-bg-color: #1a1a1a" in css
        assert "--window-fg-color: #ffffff" in css

    def test_get_root_vars_css_other_theme(self):
        """Test root vars CSS is empty for non-terminal themes."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {
            "bg_color": "#1a1a1a",
            "fg_color": "#ffffff",
            "header_bg_color": "#1a1a1a",
            "luminance": 0.1,
        }

        css = ThemeEngine._get_root_vars_css(params, "Adwaita")
        assert css == ""

    def test_get_root_vars_css_low_luminance(self):
        """Test root vars CSS is empty for very low luminance."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {
            "bg_color": "#010101",
            "fg_color": "#ffffff",
            "header_bg_color": "#010101",
            "luminance": 0.01,
        }

        css = ThemeEngine._get_root_vars_css(params, "terminal")
        assert css == ""

    def test_get_headerbar_css_with_transparency(self):
        """Test headerbar CSS generation with transparency."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {
            "header_bg_color": "#1a1a1a",
            "user_transparency": 50,
        }

        # Mocking Adw would be complex, but for "terminal" theme we don't need it
        css = ThemeEngine._get_headerbar_css(params, "terminal")

        assert "background-color" in css
        assert "color-mix" in css
        assert "transparent" in css

    def test_get_headerbar_css_without_transparency(self):
        """Test headerbar CSS generation without transparency."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {"user_transparency": 0}

        css = ThemeEngine._get_headerbar_css(params, "terminal")
        assert css == ""

    def test_get_tabs_css_terminal_theme(self):
        """Test tabs CSS for terminal theme."""
        from ashyterm.utils.theme_engine import ThemeEngine

        params = {"fg_color": "#00ff00"}

        css = ThemeEngine._get_tabs_css(params, "terminal")
        assert ".scrolled-tab-bar" in css
        assert "color-mix" in css


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
