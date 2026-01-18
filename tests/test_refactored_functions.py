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
        patterns, options, stop = parser._process_config_line(
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


class TestSettingsManagerTheme:
    """Tests for refactored SettingsManager theme CSS generation functions."""

    def _create_mock_manager(self, transparency=0):
        """Create a mock SettingsManager with required attributes."""
        import threading

        from ashyterm.settings.manager import SettingsManager

        manager = SettingsManager.__new__(SettingsManager)
        manager._settings = {"headerbar_transparency": transparency}
        manager._lock = threading.RLock()
        return manager

    def test_get_theme_params_dark(self):
        """Test theme params for dark background."""
        manager = self._create_mock_manager(transparency=0)

        scheme = {
            "background": "#000000",
            "foreground": "#ffffff",
            "headerbar_background": "#000000",
        }

        params = manager._get_theme_params(scheme)

        assert params["bg_color"] == "#000000"
        assert params["fg_color"] == "#ffffff"
        assert params["is_dark_theme"] is True
        assert params["luminance"] < 0.5

    def test_get_theme_params_light(self):
        """Test theme params for light background."""
        manager = self._create_mock_manager(transparency=0)

        scheme = {
            "background": "#ffffff",
            "foreground": "#000000",
        }

        params = manager._get_theme_params(scheme)

        assert params["bg_color"] == "#ffffff"
        assert params["fg_color"] == "#000000"
        assert params["is_dark_theme"] is False
        assert params["luminance"] > 0.5

    def test_get_headerbar_css_with_transparency(self):
        """Test headerbar CSS generation with transparency."""
        manager = self._create_mock_manager()

        params = {
            "fg_color": "#ffffff",
            "header_bg_color": "#1a1a1a",
            "user_transparency": 50,
        }

        css = manager._get_headerbar_css(params)

        # With transparency, background-color should NOT be set
        assert "background-color" not in css
        assert "color: #ffffff" in css

    def test_get_headerbar_css_without_transparency(self):
        """Test headerbar CSS generation without transparency."""
        manager = self._create_mock_manager()

        params = {
            "fg_color": "#ffffff",
            "header_bg_color": "#1a1a1a",
            "user_transparency": 0,
        }

        css = manager._get_headerbar_css(params)

        # Without transparency, background-color SHOULD be set
        assert "background-color: #1a1a1a" in css
        assert "color: #ffffff" in css

    def test_get_tabs_css_structure(self):
        """Test tabs CSS contains expected selectors."""
        manager = self._create_mock_manager()

        params = {
            "fg_color": "#00ff00",
            "header_bg_color": "#1a1a1a",
            "user_transparency": 0,
        }

        css = manager._get_tabs_css(params)

        assert ".scrolled-tab-bar" in css
        assert "color: #00ff00" in css

    def test_get_sidebar_css_with_low_luminance(self):
        """Test sidebar CSS for very dark theme (luminance < 0.05)."""
        manager = self._create_mock_manager()

        params = {
            "bg_color": "#030303",  # Very dark
            "fg_color": "#ffffff",
            "hover_alpha": "10%",
            "selected_alpha": "15%",
            "luminance": 0.01,  # Below 0.05 threshold
        }

        css = manager._get_sidebar_css(params)

        # With low luminance, background should NOT be applied
        assert ".sidebar-frame" in css

    def test_get_tooltip_css(self):
        """Test tooltip CSS generation."""
        manager = self._create_mock_manager()

        params = {
            "bg_color": "#2a2a2a",
            "fg_color": "#eeeeee",
        }

        css = manager._get_tooltip_css(params)

        assert "tooltip" in css
        assert "background-color: #2a2a2a" in css
        assert "color: #eeeeee" in css

    def test_get_misc_css(self):
        """Test miscellaneous CSS (SSH error banner, separators)."""
        manager = self._create_mock_manager()

        params = {"fg_color": "#ffffff"}

        css = manager._get_misc_css(params)

        assert ".ssh-error-banner" in css
        assert "separator" in css
        assert "paned-separator" in css

    def test_get_dialog_css(self):
        """Test dialog CSS generation."""
        manager = self._create_mock_manager()

        params = {
            "bg_color": "#1a1a1a",
            "fg_color": "#ffffff",
            "luminance": 0.1,  # Above 0.05, should apply bg
        }

        css = manager._get_dialog_css(params)

        assert ".terminal-dialog" in css
        assert ".terminal-find-bar" in css

    def test_get_filemanager_css(self):
        """Test file manager CSS generation."""
        manager = self._create_mock_manager()

        params = {
            "bg_color": "#1a1a1a",
            "fg_color": "#ffffff",
            "hover_alpha": "10%",
            "selected_alpha": "15%",
        }

        css = manager._get_filemanager_css(params)

        assert ".file-manager-view" in css
        assert "listview > row:hover" in css
        assert "listview > row:selected" in css


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
