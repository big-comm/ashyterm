# tests/test_command_validation.py
"""
Tests for the command validation and shell syntax validation systems.

Tests:
- CommandValidator: $PATH cache, builtin detection, path commands
- ShellSyntaxValidator: bracket matching, quote detection, control structures
"""

import os
import sys
from unittest.mock import patch

import pytest

# Ensure src is in path
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from ashyterm.terminal.highlighter.command_validator import (
    CommandValidator,
    _SHELL_BUILTINS,
)
from ashyterm.terminal.highlighter.shell_validator import (
    ErrorKind,
    SyntaxIssue,
    validate_shell_input,
    get_error_indicators,
)


# ============================================================================
# CommandValidator Tests
# ============================================================================


class TestCommandValidator:
    """Tests for CommandValidator."""

    def setup_method(self):
        """Reset singleton for each test."""
        CommandValidator._instance = None

    def test_singleton_pattern(self):
        """get_instance returns the same object."""
        v1 = CommandValidator.get_instance()
        v2 = CommandValidator.get_instance()
        assert v1 is v2

    def test_builtins_are_valid(self):
        """Shell builtins should be recognized as valid commands."""
        validator = CommandValidator.get_instance()
        for builtin in ("cd", "echo", "export", "source", "test", "[", "if", "for"):
            assert validator.is_valid_command(builtin), f"Builtin '{builtin}' not recognized"

    def test_common_commands_are_valid(self):
        """Common system commands should be found in $PATH."""
        validator = CommandValidator.get_instance()
        # These should exist on any Linux system
        for cmd in ("ls", "cat", "grep", "sh"):
            assert validator.is_valid_command(cmd), f"Command '{cmd}' not found in $PATH"

    def test_nonexistent_command_is_invalid(self):
        """A clearly nonexistent command should not be valid."""
        validator = CommandValidator.get_instance()
        assert not validator.is_valid_command("this_command_definitely_does_not_exist_xyzzy_12345")

    def test_empty_command_is_valid(self):
        """Empty string should return True (don't flag)."""
        validator = CommandValidator.get_instance()
        assert validator.is_valid_command("")

    def test_disabled_validator_returns_true(self):
        """When disabled, all commands should be considered valid."""
        validator = CommandValidator.get_instance()
        validator.enabled = False
        assert validator.is_valid_command("nonexistent_command_abc")
        validator.enabled = True

    def test_absolute_path_executable(self):
        """Absolute path to an executable should be valid."""
        validator = CommandValidator.get_instance()
        assert validator.is_valid_command("/bin/sh")

    def test_absolute_path_nonexistent(self):
        """Absolute path to nonexistent file should be invalid."""
        validator = CommandValidator.get_instance()
        assert not validator.is_valid_command("/nonexistent/path/to/binary")

    def test_cache_invalidation(self):
        """invalidate_cache should force re-scan on next lookup."""
        validator = CommandValidator.get_instance()
        validator.invalidate_cache()
        assert validator._last_refresh == 0.0
        # Next lookup should trigger refresh
        validator.is_valid_command("ls")
        assert validator._last_refresh > 0.0

    def test_path_cache_contains_real_commands(self):
        """The cached command set should contain real system commands."""
        validator = CommandValidator.get_instance()
        # Force refresh
        validator._refresh_path_cache()
        assert "sh" in validator._path_commands
        assert len(validator._path_commands) > 10  # Should have many commands

    def test_shell_builtins_frozenset(self):
        """SHELL_BUILTINS should be a frozenset with expected entries."""
        assert isinstance(_SHELL_BUILTINS, frozenset)
        assert "cd" in _SHELL_BUILTINS
        assert "echo" in _SHELL_BUILTINS
        assert "exit" in _SHELL_BUILTINS
        assert "return" in _SHELL_BUILTINS
        assert "." in _SHELL_BUILTINS  # dot/source

    def test_relative_path_with_slash(self):
        """Commands with / are treated as paths."""
        validator = CommandValidator.get_instance()
        # ./nonexistent should check file existence
        assert not validator.is_valid_command("./nonexistent_binary_abc")


# ============================================================================
# ShellSyntaxValidator Tests
# ============================================================================


class TestShellSyntaxValidator:
    """Tests for shell syntax validation."""

    def test_empty_input(self):
        """Empty input returns no issues."""
        assert validate_shell_input("") == []

    def test_single_char(self):
        """Single character input returns no issues."""
        assert validate_shell_input("a") == []

    def test_valid_simple_command(self):
        """A valid simple command has no issues."""
        assert validate_shell_input("ls -la") == []

    def test_unclosed_single_quote(self):
        """Unclosed single quote is detected."""
        issues = validate_shell_input("echo 'hello")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == "'"

    def test_unclosed_double_quote(self):
        """Unclosed double quote is detected."""
        issues = validate_shell_input('echo "hello')
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == '"'

    def test_unclosed_backtick(self):
        """Unclosed backtick is detected."""
        issues = validate_shell_input("echo `date")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == "`"

    def test_matched_quotes_no_issues(self):
        """Properly matched quotes produce no issues."""
        assert validate_shell_input('echo "hello world"') == []
        assert validate_shell_input("echo 'hello world'") == []
        assert validate_shell_input("echo `date`") == []

    def test_unclosed_parenthesis(self):
        """Unclosed parenthesis is detected."""
        issues = validate_shell_input("echo $(date")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_unclosed_brace(self):
        """Unclosed brace is detected."""
        issues = validate_shell_input("echo ${HOME")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_matched_brackets_no_issues(self):
        """Properly matched brackets produce no issues."""
        assert validate_shell_input("echo $(date)") == []
        assert validate_shell_input("echo ${HOME}") == []
        assert validate_shell_input("test [ -f file ]") == []

    def test_unclosed_double_bracket(self):
        """Unclosed [[ is detected."""
        issues = validate_shell_input("[[ -f file")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET and i.token == "[[" for i in issues)

    def test_matched_double_bracket(self):
        """Matched [[ ]] produces no issues."""
        assert validate_shell_input("[[ -f file ]]") == []

    def test_orphan_closer(self):
        """Orphan closing bracket is detected."""
        issues = validate_shell_input("echo )")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET and i.token == ")" for i in issues)

    def test_escaped_quote_no_issue(self):
        """Escaped quotes should not trigger issues."""
        assert validate_shell_input('echo \\"hello') == []

    def test_if_without_fi(self):
        """'if' without 'fi' is detected as incomplete structure."""
        issues = validate_shell_input("if true; then echo yes")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE and i.token == "if" for i in issues)

    def test_if_with_fi(self):
        """Complete if/fi produces no structure issues."""
        issues = validate_shell_input("if true; then echo yes; fi")
        structure_issues = [i for i in issues if i.kind == ErrorKind.INCOMPLETE_STRUCTURE]
        assert len(structure_issues) == 0

    def test_for_without_done(self):
        """'for' without 'done' is detected."""
        issues = validate_shell_input("for i in 1 2 3; do echo $i")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE and i.token == "for" for i in issues)

    def test_for_with_done(self):
        """Complete for/done produces no structure issues."""
        issues = validate_shell_input("for i in 1 2 3; do echo $i; done")
        structure_issues = [i for i in issues if i.kind == ErrorKind.INCOMPLETE_STRUCTURE]
        assert len(structure_issues) == 0

    def test_while_without_done(self):
        """'while' without 'done' is detected."""
        issues = validate_shell_input("while true; do echo loop")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE and i.token == "while" for i in issues)

    def test_case_without_esac(self):
        """'case' without 'esac' is detected."""
        issues = validate_shell_input("case $x in a) echo a")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE and i.token == "case" for i in issues)

    def test_case_with_esac(self):
        """Complete case/esac produces no structure issues."""
        # The ) in pattern will generate bracket issue, but no structure issue
        issues = validate_shell_input("case $x in a) echo a;; esac")
        structure_issues = [i for i in issues if i.kind == ErrorKind.INCOMPLETE_STRUCTURE]
        assert len(structure_issues) == 0

    def test_arithmetic_expansion(self):
        """Matched $(( )) produces no issues."""
        assert validate_shell_input("echo $((1+2))") == []

    def test_unclosed_arithmetic(self):
        """Unclosed (( is detected."""
        issues = validate_shell_input("echo $((1+2")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_nested_quotes_in_command_sub(self):
        """Quotes inside command substitution."""
        assert validate_shell_input('echo "$(echo "hello")"') == []

    def test_comment_skipped(self):
        """Comments should be skipped in analysis."""
        assert validate_shell_input("echo hello # this is a comment") == []

    def test_get_error_indicators_entry_point(self):
        """get_error_indicators is the same as validate_shell_input."""
        buffer = "echo 'unclosed"
        assert get_error_indicators(buffer) == validate_shell_input(buffer)

    def test_pipe_commands(self):
        """Pipe separated commands should be fine."""
        assert validate_shell_input("ls | grep foo | wc -l") == []

    def test_and_or_chains(self):
        """&& and || chains should be fine."""
        assert validate_shell_input("test -f file && echo yes || echo no") == []

    def test_multiple_issues(self):
        """Multiple issues can be detected simultaneously."""
        # Unclosed $( at top level + unclosed ${ at top level
        issues = validate_shell_input("echo $(cmd ${var")
        assert len(issues) >= 2


# ============================================================================
# Integration: CommandValidator + streaming handler token enhancement
# ============================================================================


class TestCommandNotFoundIntegration:
    """Test the integration between CommandValidator and token enhancement."""

    def setup_method(self):
        CommandValidator._instance = None

    def test_valid_command_returns_true(self):
        """A valid command should pass validation."""
        validator = CommandValidator.get_instance()
        assert validator.is_valid_command("bash")

    def test_invalid_command_returns_false(self):
        """An invalid command should fail validation."""
        validator = CommandValidator.get_instance()
        assert not validator.is_valid_command("xyzzy_not_a_real_command_99999")

    def test_builtin_return_true_without_path(self):
        """Builtins should pass even if $PATH is empty."""
        validator = CommandValidator.get_instance()
        with patch.dict(os.environ, {"PATH": ""}, clear=False):
            validator._refresh_path_cache()
            assert validator.is_valid_command("cd")
            assert validator.is_valid_command("echo")
            assert validator.is_valid_command("export")

    def test_ansi_codes_are_defined(self):
        """ANSI codes for command-not-found should be importable."""
        from ashyterm.terminal._streaming_handler import (
            _COMMAND_NOT_FOUND_START,
            _COMMAND_NOT_FOUND_END,
        )
        assert "\033[4;31m" in _COMMAND_NOT_FOUND_START  # underline + red
        assert "\033[24;39m" in _COMMAND_NOT_FOUND_END  # reset underline + fg
