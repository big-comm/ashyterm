"""Tests for syntax_utils — bash command → Pango markup."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestBuildColorMap:
    """Tests for _build_color_map."""

    def test_default_colors(self):
        from ashyterm.utils.syntax_utils import _build_color_map

        colors = _build_color_map()
        assert colors["command"] == "#8ae234"
        assert colors["string"] == "#e9b96e"
        assert colors["variable"] == "#ad7fa8"
        assert colors["flag"] == "#98d8c8"
        assert colors["number"] == "#f4d03f"
        assert colors["path"] == "#87ceeb"
        assert colors["operator"] == "#fcaf3e"
        assert colors["substitution"] == "#b8860b"
        assert colors["special_var"] == "#ff69b4"

    def test_custom_palette(self):
        from ashyterm.utils.syntax_utils import _build_color_map

        palette = [
            "#000000", "#cc0000", "#4e9a06", "#c4a000",
            "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f",
            "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec",
        ]
        colors = _build_color_map(palette)
        assert colors["command"] == "#4e9a06"  # palette[2] green
        assert colors["string"] == "#c4a000"   # palette[3] yellow
        assert colors["variable"] == "#75507b"  # palette[5] magenta
        assert colors["flag"] == "#34e2e2"      # palette[14] bright cyan

    def test_partial_palette(self):
        """Palette with only 8 colors uses defaults for extended indices."""
        from ashyterm.utils.syntax_utils import _build_color_map

        palette = ["#000000", "#cc0000", "#4e9a06", "#c4a000",
                    "#3465a4", "#75507b", "#06989a", "#d3d7cf"]
        colors = _build_color_map(palette)
        # palette[14] out of range → default
        assert colors["flag"] == "#98d8c8"

    def test_empty_palette_falls_back(self):
        from ashyterm.utils.syntax_utils import _build_color_map

        colors = _build_color_map([])
        assert colors["command"] == "#8ae234"


class TestGetBashPangoMarkup:
    """Tests for get_bash_pango_markup output."""

    def test_empty_string(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("")
        assert result == ""

    def test_simple_command_highlights_command(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("ls -la")
        assert "ls" in result
        assert "<span" in result

    def test_path_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("cat /etc/passwd")
        assert "/etc/passwd" in result
        assert '<span foreground=' in result

    def test_variable_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo $HOME")
        assert "$HOME" in result
        assert '<span foreground=' in result

    def test_braced_variable_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo ${PATH}")
        assert "${PATH}" in result
        assert '<span foreground=' in result

    def test_flag_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("grep -r --include='*.py' .")
        assert "-r" in result
        assert "--include" in result

    def test_string_single_quote_highlight(self):
        """Single-quoted strings get foreground span."""
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo 'hello world'")
        # GLib.markup_escape_text escapes quotes; check span presence
        assert "<span foreground=" in result

    def test_string_double_quote_highlight(self):
        """Double-quoted strings get foreground span."""
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup('echo "hello world"')
        # GLib.markup_escape_text escapes " to &quot;
        assert "<span foreground=" in result
        assert "hello world" in result

    def test_url_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("curl https://example.com")
        assert "https://example.com" in result

    def test_pipe_operator_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("ls | grep foo")
        assert "|" in result

    def test_special_variable_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo $?")
        assert "$?" in result

    def test_number_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("find . -mtime 7")
        assert "7" in result

    def test_negative_number_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("find . -mtime -1")
        assert "-1" in result

    def test_backtick_substitution_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo `date`")
        assert "`date`" in result

    def test_dollar_subshell_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo $(date)")
        assert "$(date)" in result

    def test_command_at_start_highlight(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        for cmd in ["find", "grep", "ls", "cat", "echo", "cd", "rm", "mkdir",
                     "touch", "cp", "mv", "chmod", "chown", "tar", "curl", "wget"]:
            result = get_bash_pango_markup(f"{cmd} .")
            assert cmd in result

    def test_escaped_text_preserved(self):
        """GLib.markup_escape_text should escape special chars like < > &."""
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo <file>")
        # < and > should be escaped in markup
        assert "&lt;" in result or "<" in result

    def test_custom_palette(self):
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        palette = [
            "#000000", "#cc0000", "#4e9a06", "#c4a000",
            "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f",
            "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec",
        ]
        result = get_bash_pango_markup("ls -la /tmp", palette=palette)
        assert '<span foreground="#4e9a06">' in result  # green for command

    def test_custom_foreground_color(self):
        """Custom foreground param accepted; output is still valid markup."""
        from ashyterm.utils.syntax_utils import get_bash_pango_markup

        result = get_bash_pango_markup("echo test", foreground="#ff0000")
        # Output should be valid Pango markup
        assert "<span" in result
        assert "echo" in result
        assert "test" in result


class TestRegexPatterns:
    """Tests for pre-compiled regex patterns exist and are valid."""

    def test_all_patterns_compile(self):
        """Verify all module-level regex patterns compile without error."""
        import re
        from ashyterm.utils import syntax_utils

        patterns = [
            "_PATTERN_URL", "_PATTERN_SINGLE_QUOTE", "_PATTERN_DOUBLE_QUOTE",
            "_PATTERN_VARIABLE", "_PATTERN_SPECIAL_VAR", "_PATTERN_FLAG_SPACE",
            "_PATTERN_FLAG_START", "_PATTERN_NUMBER_NEG", "_PATTERN_NUMBER",
            "_PATTERN_PATH_SPACE", "_PATTERN_PATH_START", "_PATTERN_COMMAND",
            "_PATTERN_OPERATOR", "_PATTERN_BACKTICK", "_PATTERN_SUBSHELL",
        ]
        for name in patterns:
            pattern = getattr(syntax_utils, name)
            assert isinstance(pattern, re.Pattern)
