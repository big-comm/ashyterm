# tests/test_highlighting_untested.py
"""
Tests for previously untested highlighting components:

1. shell_validator — bracket/quote/control-structure validation
2. command_validator — command existence checking ($PATH, builtins)
3. OutputHighlighter lifecycle — set_context, clear_context, compile_rule, highlight_line
4. HighlightManager — trigger map, context resolution, layered loading
5. StreamingHandler helpers — enhance_token_type, is_command_position
6. HighlightedTerminalProxy helpers — backspace, interactive marker, multiline block
"""

import os
import stat
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ============================================================================
# 1. Shell Validator Tests
# ============================================================================


class TestShellValidator:
    """Test shell_validator.validate_shell_input and supporting functions."""

    def _validate(self, buf):
        from ashyterm.terminal.highlighter.shell_validator import validate_shell_input

        return validate_shell_input(buf)

    def _kinds(self, buf):
        return [issue.kind for issue in self._validate(buf)]

    def _tokens(self, buf):
        return [issue.token for issue in self._validate(buf)]

    # --- Empty / trivial ---

    def test_empty_string(self):
        assert self._validate("") == []

    def test_single_char(self):
        assert self._validate("x") == []

    def test_simple_command(self):
        assert self._validate("ls -la") == []

    # --- Balanced constructs ---

    def test_balanced_parens(self):
        assert self._validate("echo (hello)") == []

    def test_balanced_braces(self):
        assert self._validate("echo { a; b; }") == []

    def test_balanced_brackets(self):
        assert self._validate("echo [test]") == []

    def test_balanced_double_brackets(self):
        assert self._validate("[[ -f file ]]") == []

    def test_balanced_double_parens(self):
        assert self._validate("(( x + 1 ))") == []

    def test_balanced_dollar_paren(self):
        assert self._validate("echo $(whoami)") == []

    def test_balanced_dollar_brace(self):
        assert self._validate("echo ${HOME}") == []

    def test_balanced_dollar_double_paren(self):
        assert self._validate("echo $(( 1 + 2 ))") == []

    def test_balanced_single_quotes(self):
        assert self._validate("echo 'hello world'") == []

    def test_balanced_double_quotes(self):
        assert self._validate('echo "hello world"') == []

    def test_balanced_backticks(self):
        assert self._validate("echo `date`") == []

    # --- Unmatched brackets ---

    def test_unclosed_paren(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo (hello")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNMATCHED_BRACKET
        assert issues[0].token == "("

    def test_unclosed_brace(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo { a; b")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNMATCHED_BRACKET
        assert issues[0].token == "{"

    def test_unclosed_bracket(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo [test")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNMATCHED_BRACKET

    def test_extra_closing_paren(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo hello)")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNMATCHED_BRACKET
        assert issues[0].token == ")"

    def test_extra_closing_double_bracket(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo ]]")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_extra_closing_double_paren(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo ))")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    # --- Unclosed quotes ---

    def test_unclosed_single_quote(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo 'hello")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == "'"

    def test_unclosed_double_quote(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate('echo "hello')
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == '"'

    def test_unclosed_backtick(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo `date")
        assert len(issues) == 1
        assert issues[0].kind == ErrorKind.UNCLOSED_QUOTE
        assert issues[0].token == "`"

    # --- Escaped characters inside quotes ---

    def test_escaped_double_quote_inside_quotes(self):
        """Escaped double-quote inside double-quotes is valid."""
        assert self._validate(r'echo "hello \"world\""') == []

    def test_escaped_char_skipped(self):
        """Backslash-escaped chars are skipped by scanner."""
        assert self._validate(r"echo \(not a bracket") == []

    # --- Nested constructs ---

    def test_nested_dollar_paren_in_double_quotes(self):
        assert self._validate('echo "$(whoami)"') == []

    def test_nested_dollar_brace_in_double_quotes(self):
        assert self._validate('echo "${HOME}/bin"') == []

    def test_mismatched_nesting(self):
        """Paren closed with brace is a mismatch."""
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo (hello}")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    # --- Control structures ---

    def test_if_without_fi(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("if true; then echo yes")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)
        assert any(i.token == "if" for i in issues)

    def test_if_with_fi(self):
        issues = self._validate("if true; then echo yes; fi")
        assert not any(i.token == "if" for i in issues)

    def test_case_without_esac(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("case $x in a) echo a;;")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)

    def test_case_with_esac(self):
        issues = self._validate("case $x in a) echo a;; esac")
        assert not any(i.token == "case" for i in issues)

    def test_for_without_done(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("for i in 1 2 3; do echo $i")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)

    def test_for_with_done(self):
        issues = self._validate("for i in 1 2 3; do echo $i; done")
        assert not any(i.token == "for" for i in issues)

    def test_while_without_done(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("while true; do echo yes")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)

    def test_orphan_fi(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo hello; fi")
        assert any(
            i.kind == ErrorKind.UNMATCHED_BRACKET and i.token == "fi" for i in issues
        )

    def test_orphan_done(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo hello; done")
        assert any(
            i.kind == ErrorKind.UNMATCHED_BRACKET and i.token == "done" for i in issues
        )

    # --- Comments ---

    def test_comment_skipped(self):
        """Comments are properly skipped during scanning."""
        assert self._validate("echo hello # this is (a comment") == []

    # --- get_error_indicators entry point ---

    def test_get_error_indicators(self):
        from ashyterm.terminal.highlighter.shell_validator import get_error_indicators

        issues = get_error_indicators("echo 'unclosed")
        assert len(issues) == 1

    # --- SyntaxIssue positions ---

    def test_issue_positions(self):
        issues = self._validate("echo (hello")
        assert issues[0].start == 5
        assert issues[0].end == 6


# ============================================================================
# 2. Command Validator Tests
# ============================================================================


class TestCommandValidator:
    """Test command_validator.CommandValidator."""

    @pytest.fixture
    def validator(self):
        from ashyterm.terminal.highlighter.command_validator import CommandValidator

        v = CommandValidator()
        v._enabled = True
        return v

    # --- Builtins ---

    def test_builtins_are_valid(self, validator):
        """All shell builtins are recognized as valid."""
        for builtin in (
            "cd",
            "echo",
            "export",
            "exit",
            "set",
            "unset",
            "alias",
            "bg",
            "fg",
            "jobs",
            "kill",
            "read",
            "test",
            "true",
            "false",
            "source",
            ".",
            ":",
            "[",
        ):
            assert validator.is_valid_command(builtin), (
                f"Builtin '{builtin}' not recognized"
            )

    def test_empty_command_returns_true(self, validator):
        """Empty command is not flagged as invalid."""
        assert validator.is_valid_command("") is True

    def test_disabled_always_returns_true(self, validator):
        """When disabled, any command returns True."""
        validator.enabled = False
        assert validator.is_valid_command("nonexistent_cmd_xyz_42") is True

    # --- PATH-based lookup ---

    def test_ls_is_valid(self, validator):
        """'ls' should be found in $PATH on any Linux system."""
        assert validator.is_valid_command("ls") is True

    def test_cat_is_valid(self, validator):
        assert validator.is_valid_command("cat") is True

    def test_nonexistent_command_is_invalid(self, validator):
        """A clearly nonexistent command returns False."""
        assert validator.is_valid_command("zzz_nonexistent_cmd_42") is False

    # --- Absolute/relative paths ---

    def test_absolute_path_to_existing_exe(self, validator):
        """Absolute path to an executable returns True."""
        assert validator.is_valid_command("/bin/sh") is True

    def test_absolute_path_to_nonexistent(self, validator):
        assert validator.is_valid_command("/nonexistent/path/to/cmd") is False

    def test_absolute_path_to_non_executable(self, validator):
        """A file that exists but is not executable returns False."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not executable")
            path = f.name
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # rw, no x
            assert validator.is_valid_command(path) is False
        finally:
            os.unlink(path)

    # --- Cache ---

    def test_invalidate_cache(self, validator):
        """Cache invalidation sets timestamp to 0."""
        validator.invalidate_cache()
        assert validator._last_refresh == 0.0

    def test_cache_ttl_triggers_refresh(self, validator):
        """Stale cache triggers refresh on next lookup."""
        validator._last_refresh = 0.0
        # This should trigger a refresh internally
        validator.is_valid_command("ls")
        assert validator._last_refresh > 0.0

    # --- Enabled property ---

    def test_enabled_property(self, validator):
        validator.enabled = False
        assert validator.enabled is False
        validator.enabled = True
        assert validator.enabled is True

    # --- Singleton ---

    def test_singleton(self):
        from ashyterm.terminal.highlighter.command_validator import CommandValidator

        a = CommandValidator.get_instance()
        b = CommandValidator.get_instance()
        assert a is b


# ============================================================================
# 3. OutputHighlighter Lifecycle Tests
# ============================================================================


class TestOutputHighlighterLifecycle:
    """Test OutputHighlighter proxy management and context handling."""

    @pytest.fixture
    def highlighter(self):
        """Create a minimal OutputHighlighter for lifecycle tests."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._lock = threading.Lock()
        h._proxy_contexts = {}
        h._full_commands = {}
        h._skip_first_output = {}
        h._ignored_commands = frozenset(["ls", "grep"])
        h._context_rules_cache = {}
        h._global_rules = ()

        # Mock manager
        h._manager = MagicMock()
        h._manager.context_aware_enabled = True
        h._manager.enabled_for_local = True
        h._manager.enabled_for_ssh = False
        h._manager.get_context_for_command = MagicMock(
            side_effect=lambda cmd: {
                "ping": "ping",
                "ping6": "ping",
                "docker": "docker",
            }.get(cmd.lower())
        )
        h._manager.rules = []
        return h

    # --- register / unregister ---

    def test_register_proxy(self, highlighter):
        highlighter.register_proxy(10)
        assert 10 in highlighter._proxy_contexts
        assert highlighter._proxy_contexts[10] == ""

    def test_unregister_proxy(self, highlighter):
        highlighter.register_proxy(10)
        highlighter._full_commands[10] = "cat file.py"
        highlighter._skip_first_output[10] = True
        highlighter.unregister_proxy(10)
        assert 10 not in highlighter._proxy_contexts
        assert 10 not in highlighter._full_commands
        assert 10 not in highlighter._skip_first_output

    def test_unregister_nonexistent_proxy(self, highlighter):
        """Unregistering a non-existent proxy doesn't crash."""
        highlighter.unregister_proxy(999)  # No error

    # --- set_context ---

    def test_set_context_known_command(self, highlighter):
        highlighter.register_proxy(1)
        changed = highlighter.set_context("ping", proxy_id=1)
        assert changed is True
        assert highlighter.get_context(1) == "ping"

    def test_set_context_alias_resolved(self, highlighter):
        """Command aliases are resolved via trigger map."""
        highlighter.register_proxy(1)
        highlighter.set_context("ping6", proxy_id=1)
        assert highlighter.get_context(1) == "ping"

    def test_set_context_unknown_command_uses_global_context(self, highlighter):
        """Unknown commands reuse global rules without growing the cache."""
        highlighter.register_proxy(1)
        highlighter.set_context("mycommand", proxy_id=1)
        assert highlighter.get_context(1) == ""

    def test_set_context_empty_resets(self, highlighter):
        highlighter.register_proxy(1)
        highlighter.set_context("ping", proxy_id=1)
        changed = highlighter.set_context("", proxy_id=1)
        assert changed is True
        assert highlighter.get_context(1) == ""

    def test_set_context_same_returns_false(self, highlighter):
        highlighter.register_proxy(1)
        highlighter.set_context("ping", proxy_id=1)
        changed = highlighter.set_context("ping", proxy_id=1)
        assert changed is False

    def test_set_context_ignored_command(self, highlighter):
        """Ignored commands store the command name for bypass checks."""
        highlighter.register_proxy(1)
        highlighter.set_context("ls", proxy_id=1)
        assert highlighter.get_context(1) == "ls"

    def test_set_context_stores_full_command(self, highlighter):
        highlighter.register_proxy(1)
        highlighter.set_context("cat", proxy_id=1, full_command="cat file.py")
        assert highlighter.get_full_command(1) == "cat file.py"

    def test_set_context_sets_skip_flag(self, highlighter):
        """Setting context marks first output to be skipped."""
        highlighter.register_proxy(1)
        highlighter.set_context("ping", proxy_id=1)
        assert highlighter._skip_first_output.get(1) is True

    # --- should_skip_first_output ---

    def test_skip_consumed_on_read(self, highlighter):
        highlighter.register_proxy(1)
        highlighter.set_context("ping", proxy_id=1)
        assert highlighter.should_skip_first_output(1) is True
        assert highlighter.should_skip_first_output(1) is False  # consumed

    def test_skip_default_false(self, highlighter):
        highlighter.register_proxy(1)
        assert highlighter.should_skip_first_output(1) is False

    # --- clear_context ---

    def test_clear_context(self, highlighter):
        highlighter.register_proxy(1)
        highlighter.set_context("ping", proxy_id=1, full_command="ping 8.8.8.8")
        highlighter.clear_context(1)
        assert 1 not in highlighter._proxy_contexts
        assert 1 not in highlighter._full_commands

    def test_clear_context_nonexistent(self, highlighter):
        """Clearing non-existent proxy doesn't crash."""
        highlighter.clear_context(999)

    # --- is_enabled_for_type ---

    def test_enabled_for_local(self, highlighter):
        assert highlighter.is_enabled_for_type("local") is True

    def test_not_enabled_for_ssh(self, highlighter):
        assert highlighter.is_enabled_for_type("ssh") is False

    def test_unknown_type_not_enabled(self, highlighter):
        assert highlighter.is_enabled_for_type("unknown") is False

    # --- highlight_line with ignored commands ---

    def test_highlight_line_ignored_command_passthrough(self, highlighter):
        """Lines from ignored commands pass through unchanged."""
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        highlighter.register_proxy(1)
        highlighter._proxy_contexts[1] = "ls"
        highlighter._global_rules = (
            LiteralKeywordRule(frozenset(["error"]), ("error",), "\033[31m", "next"),
        )

        result = highlighter.highlight_line("error in output", proxy_id=1)
        assert result == "error in output"  # No highlighting applied

    def test_highlight_line_no_rules_passthrough(self, highlighter):
        """No rules means text passes through unchanged."""
        highlighter.register_proxy(1)
        result = highlighter.highlight_line("some text", proxy_id=1)
        assert result == "some text"

    def test_highlight_line_empty(self, highlighter):
        assert highlighter.highlight_line("") == ""

    # --- _get_active_rules ---

    def test_get_active_rules_no_context(self, highlighter):
        """No context returns global rules."""
        rules = highlighter._get_active_rules("")
        assert rules is highlighter._global_rules

    def test_get_active_rules_caches_context(self, highlighter):
        """Context rules are cached after first compilation."""
        highlighter._manager.get_rules_for_context.return_value = []
        rules1 = highlighter._get_active_rules("ping")
        rules2 = highlighter._get_active_rules("ping")
        assert rules1 is rules2  # Same object = cached


# ============================================================================
# 4. OutputHighlighter._compile_rule Tests
# ============================================================================


class TestCompileRule:
    """Test OutputHighlighter._compile_rule with real HighlightRules."""

    @pytest.fixture
    def highlighter(self):
        from ashyterm.terminal.highlighter.output import OutputHighlighter

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._lock = threading.Lock()

        # Real manager-like mock with resolve_color_to_ansi
        h._manager = MagicMock()
        h._manager.resolve_color_to_ansi = MagicMock(
            side_effect=lambda c: (
                "\033[31m"
                if c == "bold red"
                else (
                    "\033[32m"
                    if c == "green"
                    else ("\033[33m" if c == "yellow" else "")
                )
            )
        )
        return h

    def test_compile_keyword_rule(self, highlighter):
        """Simple keyword pattern compiles to LiteralKeywordRule."""
        from ashyterm.settings.highlights import HighlightRule
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        rule = HighlightRule(
            name="Errors",
            pattern=r"\b(error|fatal)\b",
            colors=["bold red"],
        )
        compiled = highlighter._compile_rule(rule)
        assert isinstance(compiled, LiteralKeywordRule)
        assert "error" in compiled.keywords
        assert "fatal" in compiled.keywords
        assert compiled.ansi_color == "\033[31m"

    def test_compile_regex_rule(self, highlighter):
        """Complex pattern compiles to CompiledRule."""
        from ashyterm.settings.highlights import HighlightRule
        from ashyterm.terminal.highlighter.rules import CompiledRule

        rule = HighlightRule(
            name="IPv4",
            pattern=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
            colors=["bold red"],
        )
        compiled = highlighter._compile_rule(rule)
        assert isinstance(compiled, CompiledRule)
        assert compiled.ansi_colors == ("\033[31m",)

    def test_compile_disabled_rule_returns_none(self, highlighter):
        from ashyterm.settings.highlights import HighlightRule

        rule = HighlightRule(
            name="Test", pattern=r"\btest\b", colors=["bold red"], enabled=False
        )
        assert highlighter._compile_rule(rule) is None

    def test_compile_empty_pattern_returns_none(self, highlighter):
        from ashyterm.settings.highlights import HighlightRule

        rule = HighlightRule(name="Test", pattern="", colors=["bold red"])
        assert highlighter._compile_rule(rule) is None

    def test_compile_invalid_pattern_returns_none(self, highlighter):
        from ashyterm.settings.highlights import HighlightRule

        rule = HighlightRule(name="Test", pattern=r"[invalid", colors=["bold red"])
        assert highlighter._compile_rule(rule) is None

    def test_compile_no_color_returns_none(self, highlighter):
        """Rule with empty color resolution returns None."""
        from ashyterm.settings.highlights import HighlightRule

        highlighter._manager.resolve_color_to_ansi = MagicMock(return_value="")
        rule = HighlightRule(name="Test", pattern=r"\b(test)\b", colors=[""])
        assert highlighter._compile_rule(rule) is None

    def test_compile_stop_action(self, highlighter):
        from ashyterm.settings.highlights import HighlightRule
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        rule = HighlightRule(
            name="Stop", pattern=r"\b(error)\b", colors=["bold red"], action="stop"
        )
        compiled = highlighter._compile_rule(rule)
        assert isinstance(compiled, LiteralKeywordRule)
        assert compiled.action == "stop"

    def test_compile_multi_group_rule(self, highlighter):
        from ashyterm.settings.highlights import HighlightRule
        from ashyterm.terminal.highlighter.rules import CompiledRule

        rule = HighlightRule(
            name="KV", pattern=r"(key=)(value)", colors=["bold red", "green"]
        )
        compiled = highlighter._compile_rule(rule)
        assert isinstance(compiled, CompiledRule)
        assert compiled.num_groups == 2
        assert compiled.ansi_colors == ("\033[31m", "\033[32m")


# ============================================================================
# 5. HighlightManager Trigger Map & Context Resolution
# ============================================================================


class TestHighlightManagerTriggers:
    """Test HighlightManager trigger map and context resolution."""

    @pytest.fixture
    def manager(self):
        from ashyterm.settings.highlights import (
            HighlightConfig,
            HighlightContext,
            HighlightManager,
            HighlightRule,
        )

        mgr = HighlightManager.__new__(HighlightManager)
        mgr.logger = MagicMock()
        mgr._lock = threading.RLock()
        mgr._settings_manager = None
        mgr._color_cache = {}
        mgr._current_theme_name = "default"
        mgr._trigger_map = {}

        mgr._config = HighlightConfig(
            enabled_for_local=True,
            enabled_for_ssh=True,
            context_aware_enabled=True,
            global_rules=[
                HighlightRule(
                    name="Error", pattern=r"\b(error)\b", colors=["bold red"]
                ),
            ],
            contexts={
                "ping": HighlightContext(
                    command_name="ping",
                    triggers=["ping", "ping6"],
                    rules=[
                        HighlightRule(
                            name="TTL", pattern=r"ttl=\d+", colors=["magenta"]
                        )
                    ],
                    enabled=True,
                    use_global_rules=False,
                ),
                "docker": HighlightContext(
                    command_name="docker",
                    triggers=["docker", "podman"],
                    rules=[
                        HighlightRule(
                            name="Container",
                            pattern=r"\b[a-f0-9]{12}\b",
                            colors=["cyan"],
                        )
                    ],
                    enabled=True,
                    use_global_rules=True,
                ),
                "disabled_ctx": HighlightContext(
                    command_name="disabled_ctx",
                    triggers=["disabled_cmd"],
                    rules=[HighlightRule(name="X", pattern=r"x", colors=["red"])],
                    enabled=False,
                ),
            },
        )

        mgr._build_trigger_map()
        return mgr

    def test_trigger_map_built(self, manager):
        assert manager._trigger_map["ping"] == "ping"
        assert manager._trigger_map["ping6"] == "ping"
        assert manager._trigger_map["docker"] == "docker"
        assert manager._trigger_map["podman"] == "docker"

    def test_get_context_for_command_found(self, manager):
        assert manager.get_context_for_command("ping") == "ping"
        assert manager.get_context_for_command("ping6") == "ping"
        assert manager.get_context_for_command("podman") == "docker"

    def test_get_context_for_command_not_found(self, manager):
        assert manager.get_context_for_command("unknown_cmd") is None

    def test_get_context_for_command_case_insensitive(self, manager):
        assert manager.get_context_for_command("PING") == "ping"
        assert manager.get_context_for_command("Docker") == "docker"

    def test_get_all_triggers(self, manager):
        triggers = manager.get_all_triggers()
        assert "ping" in triggers
        assert "ping6" in triggers
        assert "docker" in triggers
        assert "podman" in triggers
        assert "disabled_cmd" in triggers  # Trigger map includes disabled

    # --- get_rules_for_context ---

    def test_rules_for_context_only(self, manager):
        """Context with use_global_rules=False returns only context rules."""
        rules = manager.get_rules_for_context("ping")
        assert len(rules) == 1
        assert rules[0].name == "TTL"

    def test_rules_for_context_with_global(self, manager):
        """Context with use_global_rules=True returns global + context rules."""
        rules = manager.get_rules_for_context("docker")
        names = [r.name for r in rules]
        assert "Error" in names  # global
        assert "Container" in names  # context

    def test_rules_for_unknown_context_returns_global(self, manager):
        rules = manager.get_rules_for_context("unknown")
        assert len(rules) == 1
        assert rules[0].name == "Error"

    def test_rules_for_disabled_context_returns_global(self, manager):
        """Disabled context falls through to global rules."""
        rules = manager.get_rules_for_context("disabled_ctx")
        assert len(rules) == 1
        assert rules[0].name == "Error"

    def test_rules_for_empty_context_returns_global(self, manager):
        rules = manager.get_rules_for_context("")
        assert len(rules) == 1
        assert rules[0].name == "Error"

    # --- Property accessors ---

    def test_enabled_for_local_property(self, manager):
        assert manager.enabled_for_local is True
        manager.enabled_for_local = False
        assert manager.enabled_for_local is False

    def test_enabled_for_ssh_property(self, manager):
        assert manager.enabled_for_ssh is True

    def test_context_aware_enabled_property(self, manager):
        assert manager.context_aware_enabled is True
        manager.context_aware_enabled = False
        assert manager.context_aware_enabled is False

    def test_rules_returns_copy(self, manager):
        rules = manager.rules
        rules.clear()
        assert len(manager.rules) == 1  # Original not affected

    def test_contexts_returns_copy(self, manager):
        ctxs = manager.contexts
        ctxs.clear()
        assert len(manager.contexts) == 3  # Original not affected


# ============================================================================
# 6. HighlightManager Layered Loading Tests
# ============================================================================


class TestHighlightManagerLoading:
    """Test layered config loading (system + user)."""

    def test_system_highlights_path_exists(self):
        """System highlights directory exists and contains JSON files."""
        path = Path(__file__).parent.parent / "src" / "ashyterm" / "data" / "highlights"
        assert path.exists()
        json_files = list(path.glob("*.json"))
        assert len(json_files) > 0

    def test_load_context_from_file(self):
        """Individual JSON files are loadable as HighlightContext."""
        from ashyterm.settings.highlights import HighlightManager

        mgr = HighlightManager.__new__(HighlightManager)
        mgr.logger = MagicMock()

        path = (
            Path(__file__).parent.parent
            / "src"
            / "ashyterm"
            / "data"
            / "highlights"
            / "ping.json"
        )
        if path.exists():
            ctx = mgr._load_context_from_file(path)
            assert ctx is not None
            assert ctx.command_name == "ping"
            assert len(ctx.triggers) > 0
            assert len(ctx.rules) > 0

    def test_load_context_from_invalid_json(self):
        """Invalid JSON returns None."""
        from ashyterm.settings.highlights import HighlightManager

        mgr = HighlightManager.__new__(HighlightManager)
        mgr.logger = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{invalid json")
            path = Path(f.name)
        try:
            ctx = mgr._load_context_from_file(path)
            assert ctx is None
        finally:
            path.unlink()


# ============================================================================
# 7. HighlightedTerminalProxy Helper Tests
# ============================================================================


class TestProxyHelpers:
    """Test HighlightedTerminalProxy helper methods without GTK."""

    @pytest.fixture
    def proxy(self):
        """Create a minimal proxy-like object for testing helper methods."""
        from ashyterm.terminal._highlighter_impl import HighlightedTerminalProxy

        # We can't instantiate HighlightedTerminalProxy without VTE,
        # so we test its methods directly on a mock-like object.
        class FakeProxy:
            def __init__(self):
                self._input_highlight_buffer = ""
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                self._at_shell_prompt = True

            # Borrow methods from HighlightedTerminalProxy
            _handle_backspace_in_buffer = (
                HighlightedTerminalProxy._handle_backspace_in_buffer
            )
            _detect_interactive_marker = (
                HighlightedTerminalProxy._detect_interactive_marker
            )
            _is_in_unclosed_multiline_block = (
                HighlightedTerminalProxy._is_in_unclosed_multiline_block
            )
            _has_keyword = HighlightedTerminalProxy._has_keyword
            _has_unclosed_braces = HighlightedTerminalProxy._has_unclosed_braces
            _ends_with_continuation = HighlightedTerminalProxy._ends_with_continuation
            _check_block_openings = HighlightedTerminalProxy._check_block_openings
            _has_incomplete_escape = HighlightedTerminalProxy._has_incomplete_escape
            _check_escape_sequence_complete = (
                HighlightedTerminalProxy._check_escape_sequence_complete
            )
            _is_csi_incomplete = HighlightedTerminalProxy._is_csi_incomplete
            _is_osc_incomplete = HighlightedTerminalProxy._is_osc_incomplete

        return FakeProxy()

    # --- _handle_backspace_in_buffer ---

    def test_backspace_removes_last_char(self, proxy):
        proxy._input_highlight_buffer = "hello"
        removed = proxy._handle_backspace_in_buffer(b"\x08")
        assert removed == 1
        assert proxy._input_highlight_buffer == "hell"

    def test_backspace_del_char(self, proxy):
        proxy._input_highlight_buffer = "hello"
        removed = proxy._handle_backspace_in_buffer(b"\x7f")
        assert removed == 1
        assert proxy._input_highlight_buffer == "hell"

    def test_backspace_sh_style(self, proxy):
        """sh/dash sends \\x08 \\x08 for each erase."""
        proxy._input_highlight_buffer = "hello"
        removed = proxy._handle_backspace_in_buffer(b"\x08 \x08")
        assert removed == 1
        assert proxy._input_highlight_buffer == "hell"

    def test_backspace_multiple(self, proxy):
        proxy._input_highlight_buffer = "hello"
        removed = proxy._handle_backspace_in_buffer(b"\x08\x08\x08")
        assert removed == 3
        assert proxy._input_highlight_buffer == "he"

    def test_backspace_empty_buffer(self, proxy):
        proxy._input_highlight_buffer = ""
        removed = proxy._handle_backspace_in_buffer(b"\x08")
        assert removed == 0

    def test_backspace_more_than_buffer(self, proxy):
        proxy._input_highlight_buffer = "hi"
        removed = proxy._handle_backspace_in_buffer(b"\x08\x08\x08\x08\x08")
        assert removed == 2
        assert proxy._input_highlight_buffer == ""

    def test_backspace_resets_token_tracking(self, proxy):
        proxy._input_highlight_buffer = "hello"
        proxy._prev_shell_input_token_type = "SomeType"
        proxy._prev_shell_input_token_len = 5
        proxy._handle_backspace_in_buffer(b"\x08")
        assert proxy._prev_shell_input_token_type is None
        assert proxy._prev_shell_input_token_len == 0

    def test_no_backspace_no_change(self, proxy):
        proxy._input_highlight_buffer = "hello"
        removed = proxy._handle_backspace_in_buffer(b"abc")
        assert removed == 0
        assert proxy._input_highlight_buffer == "hello"

    # --- _detect_interactive_marker ---

    def test_interactive_marker_printable_char(self, proxy):
        """NUL + printable char = user input."""
        has, is_input, is_nl = proxy._detect_interactive_marker(b"\x00a")
        assert has is True
        assert is_input is True
        assert is_nl is False

    def test_interactive_marker_backspace(self, proxy):
        has, is_input, is_nl = proxy._detect_interactive_marker(b"\x00\x08")
        assert has is True
        assert is_input is True
        assert is_nl is False

    def test_interactive_marker_delete(self, proxy):
        has, is_input, is_nl = proxy._detect_interactive_marker(b"\x00\x7f")
        assert has is True
        assert is_input is True

    def test_interactive_marker_newline(self, proxy):
        has, is_input, is_nl = proxy._detect_interactive_marker(b"\x00\r\n")
        assert has is True
        assert is_input is False
        assert is_nl is True

    def test_interactive_marker_no_nul(self, proxy):
        has, is_input, is_nl = proxy._detect_interactive_marker(b"hello")
        assert has is False

    def test_interactive_marker_too_short(self, proxy):
        has, is_input, is_nl = proxy._detect_interactive_marker(b"\x00")
        assert has is False

    def test_interactive_marker_large_data(self, proxy):
        """Large data never has marker (min 2 bytes required)."""
        has, _, _ = proxy._detect_interactive_marker(b"x" * 100)
        assert has is False

    # --- _is_in_unclosed_multiline_block ---

    def test_multiline_empty(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("") is False

    def test_multiline_simple_command(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("ls -la") is False

    def test_multiline_if_then_without_fi(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("if true; then echo yes") is True

    def test_multiline_if_then_with_fi(self, proxy):
        assert (
            proxy._is_in_unclosed_multiline_block("if true; then echo yes; fi") is False
        )

    def test_multiline_for_do_without_done(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("for i in 1 2; do echo $i") is True

    def test_multiline_for_do_with_done(self, proxy):
        assert (
            proxy._is_in_unclosed_multiline_block("for i in 1 2; do echo $i; done")
            is False
        )

    def test_multiline_while_do_without_done(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("while true; do echo x") is True

    def test_multiline_unclosed_braces(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("echo { a; b") is True

    def test_multiline_pipe_continuation(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("ls |") is True

    def test_multiline_and_continuation(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("cmd1 &&") is True

    def test_multiline_or_continuation(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("cmd1 ||") is True

    def test_multiline_backslash_continuation(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("echo hello \\") is True

    def test_multiline_brace_continuation(self, proxy):
        assert proxy._is_in_unclosed_multiline_block("func() {") is True

    def test_multiline_else_continuation(self, proxy):
        assert (
            proxy._is_in_unclosed_multiline_block("if true; then echo a\nelse") is True
        )

    # --- _has_incomplete_escape ---

    def test_incomplete_csi(self, proxy):
        """ESC [ without terminator is incomplete."""
        assert proxy._has_incomplete_escape(b"text\x1b[") is True

    def test_complete_csi(self, proxy):
        """ESC [ with terminator is complete."""
        assert proxy._has_incomplete_escape(b"text\x1b[31m") is False

    def test_incomplete_osc(self, proxy):
        """ESC ] without BEL terminator is incomplete."""
        assert proxy._has_incomplete_escape(b"text\x1b]7;something") is True

    def test_complete_osc(self, proxy):
        """ESC ] with BEL terminator is complete."""
        assert proxy._has_incomplete_escape(b"text\x1b]7;something\x07") is False

    def test_no_escape(self, proxy):
        assert proxy._has_incomplete_escape(b"plain text") is False

    def test_lone_escape(self, proxy):
        """Lone ESC at end is incomplete."""
        assert proxy._has_incomplete_escape(b"text\x1b") is True

    def test_incomplete_charset(self, proxy):
        """ESC ( without final byte is incomplete."""
        assert proxy._has_incomplete_escape(b"text\x1b(") is True

    def test_complete_charset(self, proxy):
        """ESC ( B is complete."""
        assert proxy._has_incomplete_escape(b"text\x1b(B") is False


# ============================================================================
# 8. StreamingHandler Helper Tests
# ============================================================================


class TestStreamingHandlerHelpers:
    """Test StreamingHandler mixin helper methods."""

    @pytest.fixture
    def handler(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        class FakeHandler(StreamingHandler):
            def __init__(self):
                self._partial_line_buffer = b""
                self._at_shell_prompt = True
                self._input_highlight_buffer = ""
                self._queue_processing = False
                self._shell_input_highlighter = MagicMock()
                self._shell_input_highlighter.enabled = True
                self._highlighter = MagicMock()
                self._proxy_id = 1
                self._suppress_shell_input_highlighting = False
                self._need_color_reset = False
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                self.logger = MagicMock()

        return FakeHandler()

    def test_is_valid_shell_input_printable(self, handler):
        assert handler._is_valid_shell_input("echo") is True

    def test_is_valid_shell_input_escape_literal(self, handler):
        assert handler._is_valid_shell_input("^[") is False

    def test_is_valid_shell_input_arrow_keys(self, handler):
        assert handler._is_valid_shell_input("[A") is False
        assert handler._is_valid_shell_input("[B") is False
        assert handler._is_valid_shell_input("[C") is False
        assert handler._is_valid_shell_input("[D") is False

    def test_is_valid_shell_input_continuation_prompt(self, handler):
        assert handler._is_valid_shell_input(">") is False
        assert handler._is_valid_shell_input("> ") is False

    def test_is_valid_shell_input_control_chars(self, handler):
        assert handler._is_valid_shell_input("\x01") is False

    def test_is_readline_redraw_carriage_return(self, handler):
        assert handler._is_readline_redraw(b"\r") is True

    def test_is_readline_redraw_cursor_move(self, handler):
        assert handler._is_readline_redraw(b"\x1b[D") is True
        assert handler._is_readline_redraw(b"\x1b[C") is True

    def test_is_readline_redraw_erase(self, handler):
        assert handler._is_readline_redraw(b"\x1b[K") is True

    def test_is_readline_redraw_search(self, handler):
        assert handler._is_readline_redraw(b"(reverse-i-search)") is True

    def test_is_readline_redraw_plain_text(self, handler):
        assert handler._is_readline_redraw(b"hello") is False

    def test_should_apply_shell_input_highlighting(self, handler):
        """All conditions must be True for shell input highlighting."""
        assert handler._should_apply_shell_input_highlighting(True) is True

    def test_should_not_apply_when_not_at_prompt(self, handler):
        handler._at_shell_prompt = False
        assert handler._should_apply_shell_input_highlighting(True) is False

    def test_should_not_apply_when_not_user_input(self, handler):
        assert handler._should_apply_shell_input_highlighting(False) is False

    def test_should_not_apply_when_suppressed(self, handler):
        handler._suppress_shell_input_highlighting = True
        assert handler._should_apply_shell_input_highlighting(True) is False

    def test_should_not_apply_when_disabled(self, handler):
        handler._shell_input_highlighter.enabled = False
        assert handler._should_apply_shell_input_highlighting(True) is False

    def test_extract_line_content_and_ending_lf(self, handler):
        content, ending = handler._extract_line_content_and_ending("hello\n")
        assert content == "hello"
        assert ending == "\n"

    def test_extract_line_content_and_ending_crlf(self, handler):
        content, ending = handler._extract_line_content_and_ending("hello\r\n")
        assert content == "hello"
        assert ending == "\r\n"

    def test_extract_line_content_and_ending_cr(self, handler):
        content, ending = handler._extract_line_content_and_ending("hello\r")
        assert content == "hello"
        assert ending == "\r"

    def test_extract_line_content_and_ending_no_ending(self, handler):
        content, ending = handler._extract_line_content_and_ending("hello")
        assert content == "hello"
        assert ending == ""

    def test_looks_like_prompt(self, handler):
        assert handler._looks_like_prompt("bash-5.2") is True
        assert handler._looks_like_prompt("user@host") is True
        assert handler._looks_like_prompt("~") is True
        assert handler._looks_like_prompt("random text") is False

    def test_reset_input_buffer(self, handler):
        handler._input_highlight_buffer = "some data"
        handler._prev_shell_input_token_type = "Token.Text"
        handler._prev_shell_input_token_len = 5
        handler._reset_input_buffer()
        assert handler._input_highlight_buffer == ""
        assert handler._prev_shell_input_token_type is None
        assert handler._prev_shell_input_token_len == 0

    def test_append_to_input_buffer_empty(self, handler):
        handler._input_highlight_buffer = ""
        handler._append_to_input_buffer("ls")
        assert handler._input_highlight_buffer == "ls"

    def test_append_to_input_buffer_existing(self, handler):
        handler._input_highlight_buffer = "ls"
        handler._append_to_input_buffer(" -la")
        assert handler._input_highlight_buffer == "ls -la"


# ============================================================================
# 9. _enhance_token_type and _is_command_position Tests
# ============================================================================


class TestTokenEnhancement:
    """Test token type enhancement in StreamingHandler."""

    @pytest.fixture
    def handler(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        class FakeHandler(StreamingHandler):
            def __init__(self):
                self._partial_line_buffer = b""
                self._at_shell_prompt = True
                self._input_highlight_buffer = ""
                self._queue_processing = False
                self._shell_input_highlighter = MagicMock()
                self._highlighter = MagicMock()
                self._proxy_id = 1
                self._suppress_shell_input_highlighting = False
                self._need_color_reset = False
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                self.logger = MagicMock()

        return FakeHandler()

    def test_option_long_flag(self, handler):
        from pygments.token import Token

        result = handler._enhance_token_type(Token.Text, "--verbose")
        assert result == Token.Name.Attribute

    def test_option_short_flag(self, handler):
        from pygments.token import Token

        result = handler._enhance_token_type(Token.Text, "-l")
        assert result == Token.Name.Attribute

    def test_single_dash_in_command_position(self, handler):
        """Single dash in command position is treated as unknown command."""
        from ashyterm.terminal._streaming_handler import _COMMAND_NOT_FOUND
        from pygments.token import Token

        handler._input_highlight_buffer = "-"
        result = handler._enhance_token_type(Token.Text, "-")
        assert result is _COMMAND_NOT_FOUND

    def test_command_position_detected(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "ls"
        result = handler._enhance_token_type(Token.Text, "ls")
        assert result == Token.Name.Function

    def test_warning_command_sudo(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "sudo"
        result = handler._enhance_token_type(Token.Text, "sudo")
        assert result == Token.Name.Exception

    def test_warning_command_rm(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "rm"
        result = handler._enhance_token_type(Token.Text, "rm")
        assert result == Token.Name.Exception

    def test_command_after_pipe(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "cat file | grep"
        result = handler._enhance_token_type(Token.Text, "grep")
        assert result == Token.Name.Function

    def test_command_after_semicolon(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "cd /tmp; ls"
        result = handler._enhance_token_type(Token.Text, "ls")
        assert result == Token.Name.Function

    def test_command_after_and(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "make && make install"
        handler._enhance_token_type(Token.Name, "install")
        # "install" comes after "make" (which is not a prefix command), so
        # it's NOT in command position: "make install" is make's argument
        # Actually "make &&" puts "make" as a command, but "install" comes after "make" not "&&"
        # Let's check what _is_command_position says
        result_cp = handler._is_command_position("install")
        # "make && make install" -> words_before = "make && make" -> ends with normal word, not operator
        # So "install" is NOT in command position — it's an argument to "make"
        assert result_cp is False

    def test_command_after_prefix_sudo(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "sudo apt"
        result = handler._enhance_token_type(Token.Text, "apt")
        assert result == Token.Name.Function

    def test_is_command_position_first_word(self, handler):
        handler._input_highlight_buffer = "git"
        assert handler._is_command_position("git") is True

    def test_is_command_position_after_pipe(self, handler):
        handler._input_highlight_buffer = "ls | grep"
        assert handler._is_command_position("grep") is True

    def test_is_command_position_after_subshell(self, handler):
        handler._input_highlight_buffer = "echo $(date"
        assert handler._is_command_position("date") is True

    def test_is_command_position_argument(self, handler):
        """Arguments to commands are NOT in command position."""
        handler._input_highlight_buffer = "echo hello"
        assert handler._is_command_position("hello") is False

    def test_command_not_found(self, handler):
        """Non-existent command gets _COMMAND_NOT_FOUND token type."""
        from ashyterm.terminal._streaming_handler import _COMMAND_NOT_FOUND

        handler._input_highlight_buffer = "zzz_nonexistent_42"

        from pygments.token import Token

        result = handler._enhance_token_type(Token.Text, "zzz_nonexistent_42")
        assert result is _COMMAND_NOT_FOUND

    def test_empty_token_value(self, handler):
        from pygments.token import Token

        result = handler._enhance_token_type(Token.Text, "")
        assert result == Token.Text

    def test_non_text_token_passthrough(self, handler):
        """Non-Text/Name tokens pass through unchanged."""
        from pygments.token import Token

        result = handler._enhance_token_type(Token.Keyword, "if")
        assert result == Token.Keyword


# ============================================================================
# 10. Shell Validator Edge Cases
# ============================================================================


class TestShellValidatorEdgeCases:
    """Advanced edge cases for shell_validator."""

    def _validate(self, buf):
        from ashyterm.terminal.highlighter.shell_validator import validate_shell_input

        return validate_shell_input(buf)

    def test_nested_brackets_valid(self):
        assert self._validate("echo $(echo $((1+2)))") == []

    def test_deeply_nested_quotes_and_brackets(self):
        """Complex nesting with quotes and brackets."""
        issues = self._validate("echo \"$(echo 'hello')\"")
        assert issues == []

    def test_heredoc_style_ignored(self):
        """Simple multiword without heredoc doesn't crash."""
        issues = self._validate("cat << EOF\nhello\nEOF")
        # We don't expect perfect heredoc handling, just no crash
        assert isinstance(issues, list)

    def test_multiple_unclosed_quotes(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo 'a \"b")
        # Single quote unclosed first, then scanner doesn't reach double quote
        assert any(i.kind == ErrorKind.UNCLOSED_QUOTE for i in issues)

    def test_dollar_brace_unclosed(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo ${VAR")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_dollar_paren_unclosed(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo $(cmd")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_arithmetic_unclosed(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("echo $((1 + 2")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_double_bracket_unclosed(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("[[ -f file")
        assert any(i.kind == ErrorKind.UNMATCHED_BRACKET for i in issues)

    def test_select_without_done(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("select opt in a b c; do echo $opt")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)

    def test_until_without_done(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind

        issues = self._validate("until false; do echo yes")
        assert any(i.kind == ErrorKind.INCOMPLETE_STRUCTURE for i in issues)

    def test_multiple_issues(self):
        """Multiple issues detected in one buffer."""
        issues = self._validate("if true; then echo (unclosed")
        # Should report both unclosed paren and incomplete if
        assert len(issues) >= 2

    def test_comment_at_line_start(self):
        """Comment at very start of input."""
        assert self._validate("# this is a comment") == []

    def test_comment_after_semicolon(self):
        assert self._validate("echo hello; # comment (with parens") == []

    def test_word_extraction_skips_quoted(self):
        """Control keywords inside quotes are not counted."""
        # "if" inside quotes should not be treated as control structure
        issues = self._validate('echo "if then for while"')
        assert not any(i.token in ("if", "for", "while") for i in issues)


# ============================================================================
# 11. SyntaxIssue Dataclass Tests
# ============================================================================


class TestSyntaxIssue:
    """Test SyntaxIssue dataclass properties."""

    def test_frozen(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind, SyntaxIssue

        issue = SyntaxIssue(start=0, end=1, kind=ErrorKind.UNCLOSED_QUOTE, token="'")
        with pytest.raises(AttributeError):
            issue.start = 5  # frozen=True

    def test_slots(self):
        from ashyterm.terminal.highlighter.shell_validator import SyntaxIssue

        assert hasattr(SyntaxIssue, "__slots__")

    def test_equality(self):
        from ashyterm.terminal.highlighter.shell_validator import ErrorKind, SyntaxIssue

        a = SyntaxIssue(0, 1, ErrorKind.UNCLOSED_QUOTE, "'")
        b = SyntaxIssue(0, 1, ErrorKind.UNCLOSED_QUOTE, "'")
        assert a == b


# ============================================================================
# 12. Heredoc Support Tests (Improvement 4)
# ============================================================================


class TestShellValidatorHeredoc:
    """Test heredoc handling in shell_validator."""

    def _validate(self, buf):
        from ashyterm.terminal.highlighter.shell_validator import validate_shell_input

        return validate_shell_input(buf)

    def test_heredoc_with_closing_delimiter(self):
        """Content inside heredoc should not trigger bracket errors."""
        buf = "cat << EOF\n(unmatched bracket inside heredoc\nEOF"
        issues = self._validate(buf)
        assert not any(i.token == "(" for i in issues)

    def test_heredoc_without_closing_delimiter(self):
        """Heredoc without closing delimiter doesn't crash."""
        buf = "cat << EOF\nsome content"
        issues = self._validate(buf)
        assert isinstance(issues, list)

    def test_here_string(self):
        """Here-string (<<<) should not cause issues."""
        assert self._validate("cat <<< 'hello (world'") == []

    def test_heredoc_with_quoted_delimiter(self):
        """Quoted delimiter in heredoc."""
        buf = "cat << 'EOF'\n(content)\nEOF"
        issues = self._validate(buf)
        assert not any(i.token == "(" for i in issues)

    def test_heredoc_with_dash(self):
        """Tab-stripping heredoc <<-."""
        buf = "cat <<-EOF\n\tcontent (here\nEOF"
        issues = self._validate(buf)
        assert not any(i.token == "(" for i in issues)


# ============================================================================
# 13. CommandValidator mtime Cache Tests (Improvement 3)
# ============================================================================


class TestCommandValidatorMtimeCache:
    """Test the mtime-aware PATH cache."""

    @pytest.fixture
    def validator(self):
        from ashyterm.terminal.highlighter.command_validator import CommandValidator

        v = CommandValidator()
        v._enabled = True
        return v

    def test_second_refresh_skips_when_unchanged(self, validator):
        """After initial refresh, second refresh skips scan if mtime unchanged."""
        old_commands = validator._path_commands.copy()
        old_mtimes = validator._dir_mtimes.copy()

        validator._last_refresh = 0.0  # Force refresh
        validator._refresh_path_cache()

        # Should still have same commands (no change)
        assert validator._path_commands == old_commands
        assert validator._dir_mtimes == old_mtimes

    def test_dir_mtime_tracked(self, validator):
        """PATH directories have their mtimes recorded."""
        path_var = os.environ.get("PATH", "")
        dirs = [d for d in path_var.split(os.pathsep) if d and os.path.isdir(d)]
        # At least some dirs should be tracked
        assert len(validator._dir_mtimes) > 0
        for d in dirs[:3]:
            if d in validator._dir_mtimes:
                assert validator._dir_mtimes[d] > 0

    def test_new_dir_in_path_triggers_rescan(self, validator):
        """Adding a new dir to PATH triggers full rescan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = tmpdir + os.pathsep + old_path
            try:
                validator._last_refresh = 0.0
                validator._refresh_path_cache()
                assert tmpdir in validator._dir_mtimes
            finally:
                os.environ["PATH"] = old_path


# ============================================================================
# 14. Thread-safe Singleton Tests (Improvement 5/6)
# ============================================================================


class TestSingletonThreadSafety:
    """Test thread-safe singleton construction."""

    def test_output_highlighter_singleton_lock_exists(self):
        from ashyterm.terminal.highlighter import output

        assert hasattr(output, "_output_highlighter_lock")

    def test_command_validator_singleton_lock_exists(self):
        from ashyterm.terminal.highlighter.command_validator import CommandValidator

        assert hasattr(CommandValidator, "_instance_lock")

    def test_shell_input_highlighter_singleton_lock_exists(self):
        from ashyterm.terminal.highlighter import shell_input

        assert hasattr(shell_input, "_shell_input_highlighter_lock")


# ============================================================================
# 15. Buffer Limit Tests (Improvement 6)
# ============================================================================


class TestBufferLimit:
    """Test input buffer limit in StreamingHandler."""

    @pytest.fixture
    def handler(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        class FakeHandler(StreamingHandler):
            def __init__(self):
                self._input_highlight_buffer = ""
                self._partial_line_buffer = b""
                self._at_shell_prompt = True
                self._queue_processing = False
                self._shell_input_highlighter = MagicMock()
                self._shell_input_highlighter.enabled = True
                self._highlighter = MagicMock()
                self._proxy_id = 1
                self._suppress_shell_input_highlighting = False
                self._need_color_reset = False
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                self.logger = MagicMock()

        return FakeHandler()

    def test_buffer_cap_at_4096(self, handler):
        """Buffer is capped at 4096 chars."""
        handler._input_highlight_buffer = ""
        handler._append_to_input_buffer("x" * 5000)
        assert len(handler._input_highlight_buffer) == 4096

    def test_buffer_truncates_from_front(self, handler):
        """When capped, the end of the buffer is preserved."""
        handler._input_highlight_buffer = ""
        handler._append_to_input_buffer("A" * 3000)
        handler._append_to_input_buffer("B" * 2000)
        assert len(handler._input_highlight_buffer) == 4096
        assert handler._input_highlight_buffer.endswith("B" * 2000)

    def test_buffer_under_limit_not_truncated(self, handler):
        handler._input_highlight_buffer = ""
        handler._append_to_input_buffer("hello")
        assert handler._input_highlight_buffer == "hello"


# ============================================================================
# 16. ShellInputHighlighter Tests (Improvement 9)
# ============================================================================


class TestShellInputHighlighter:
    """Test ShellInputHighlighter auto-theme detection.

    Proxy/buffer/prompt methods were removed with the legacy API;
    the streaming handler tracks that state itself now.
    """

    @pytest.fixture
    def highlighter(self):
        from ashyterm.terminal.highlighter.shell_input import ShellInputHighlighter

        h = ShellInputHighlighter.__new__(ShellInputHighlighter)
        h.logger = MagicMock()
        h._enabled = True
        h._lexer = MagicMock()
        h._formatter = MagicMock()
        h._theme = "monokai"
        h._lexer_config_key = None
        h._palette = None
        h._foreground = "#ffffff"
        h._lock = threading.Lock()
        return h

    def test_is_light_color_white(self, highlighter):
        assert highlighter._is_light_color("#ffffff") is True

    def test_is_light_color_black(self, highlighter):
        assert highlighter._is_light_color("#000000") is False

    def test_is_light_color_midgray(self, highlighter):
        # R=128, G=128, B=128 → luminance ~0.502
        assert highlighter._is_light_color("#808080") is True

    def test_is_light_color_dark_gray(self, highlighter):
        assert highlighter._is_light_color("#333333") is False

    def test_is_light_color_invalid(self, highlighter):
        assert highlighter._is_light_color("invalid") is False
        assert highlighter._is_light_color("#xx") is False


# ============================================================================
# 17. CatModeHandler Helper Tests (Improvement 9)
# ============================================================================


class TestCatModeHandlerHelpers:
    """Test CatModeHandler helper methods."""

    @pytest.fixture
    def handler(self):
        from ashyterm.terminal._cat_handler import CatModeHandler

        class FakeCatHandler(CatModeHandler):
            def __init__(self):
                self._cat_limit_reached = False
                self._cat_bytes_processed = 0
                self._cat_filename = ""
                self._at_shell_prompt = False
                self._highlighter = MagicMock()
                self._highlighter.get_full_command.return_value = "cat test.py"
                self._proxy_id = 1
                self.logger = MagicMock()

            _extract_filename_from_cat_command = (
                CatModeHandler._extract_filename_from_cat_command
            )

        return FakeCatHandler()

    def test_extract_filename_simple(self, handler):
        assert handler._extract_filename_from_cat_command("cat file.py") == "file.py"

    def test_extract_filename_with_flags(self, handler):
        assert handler._extract_filename_from_cat_command("cat -n file.sh") == "file.sh"

    def test_extract_filename_with_multiple_flags(self, handler):
        assert (
            handler._extract_filename_from_cat_command("cat -n -E file.txt")
            == "file.txt"
        )

    def test_extract_filename_absolute_path(self, handler):
        assert (
            handler._extract_filename_from_cat_command("/bin/cat file.py") == "file.py"
        )

    def test_extract_filename_usr_bin_path(self, handler):
        assert (
            handler._extract_filename_from_cat_command("/usr/bin/cat file.py")
            == "file.py"
        )

    def test_extract_filename_quoted_no_spaces(self, handler):
        """Quotes are stripped from individual args."""
        assert handler._extract_filename_from_cat_command("cat 'file.py'") == "file.py"

    def test_extract_filename_no_file(self, handler):
        assert handler._extract_filename_from_cat_command("cat -n") is None

    def test_extract_filename_empty(self, handler):
        assert handler._extract_filename_from_cat_command("") is None

    def test_extract_filename_not_cat(self, handler):
        assert handler._extract_filename_from_cat_command("echo file.py") is None

    def test_extract_filename_only_flags(self, handler):
        assert handler._extract_filename_from_cat_command("cat -n -e -v") is None


# ============================================================================
# 18. OutputHighlighter _get_context_unlocked Tests (Improvement 1)
# ============================================================================


class TestGetContextUnlocked:
    """Test _get_context_unlocked internal method."""

    @pytest.fixture
    def highlighter(self):
        from ashyterm.terminal.highlighter.output import OutputHighlighter

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._lock = threading.Lock()
        h._proxy_contexts = {1: "ping", 2: "docker"}
        h._full_commands = {}
        h._skip_first_output = {}
        h._ignored_commands = frozenset()
        h._context_rules_cache = {}
        h._global_rules = ()
        h._manager = MagicMock()
        h._manager.context_aware_enabled = True
        h._manager.get_context_for_command = MagicMock(return_value=None)
        return h

    def test_unlocked_returns_context(self, highlighter):
        assert highlighter._get_context_unlocked(1) == "ping"

    def test_unlocked_returns_empty_for_unknown(self, highlighter):
        assert highlighter._get_context_unlocked(99) == ""

    def test_locked_version_matches_unlocked(self, highlighter):
        """get_context and _get_context_unlocked return same result."""
        assert highlighter.get_context(1) == highlighter._get_context_unlocked(1)

    def test_highlight_line_no_deadlock(self, highlighter):
        """highlight_line uses _get_context_unlocked, so no deadlock with Lock()."""
        result = highlighter.highlight_line("test text", proxy_id=1)
        assert result == "test text"  # No rules, passes through


# ============================================================================
# 19. Builtin Command & Variable Assignment Fix Tests
# ============================================================================


class TestBuiltinAndAssignmentFixes:
    """Test incremental fixes to _enhance_token_type."""

    @pytest.fixture
    def handler(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        class FakeHandler(StreamingHandler):
            def __init__(self):
                self._input_highlight_buffer = ""
                self._partial_line_buffer = b""
                self._at_shell_prompt = True
                self._queue_processing = False
                self._shell_input_highlighter = MagicMock()
                self._shell_input_highlighter.enabled = True
                self._highlighter = MagicMock()
                self._proxy_id = 1
                self._suppress_shell_input_highlighting = False
                self._need_color_reset = False
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                self.logger = MagicMock()

        return FakeHandler()

    # --- Builtin promotion to Function ---

    def test_builtin_echo_becomes_function(self, handler):
        """Token.Name.Builtin (echo) should map to Token.Name.Function."""
        from pygments.token import Token

        handler._input_highlight_buffer = "echo hello"
        result = handler._enhance_token_type(Token.Name.Builtin, "echo")
        assert result == Token.Name.Function

    def test_builtin_cd_becomes_function(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "cd /tmp"
        result = handler._enhance_token_type(Token.Name.Builtin, "cd")
        assert result == Token.Name.Function

    def test_builtin_export_becomes_function(self, handler):
        from pygments.token import Token

        handler._input_highlight_buffer = "export VAR=10"
        result = handler._enhance_token_type(Token.Name.Builtin, "export")
        assert result == Token.Name.Function

    def test_builtin_pseudo_becomes_function(self, handler):
        """Token.Name.Builtin.Pseudo should also be promoted."""
        from pygments.token import Token

        handler._input_highlight_buffer = "true"
        result = handler._enhance_token_type(Token.Name.Builtin.Pseudo, "true")
        assert result == Token.Name.Function

    # --- Variable assignment detection ---

    def test_variable_assignment_not_command_not_found(self, handler):
        """VAR= in buffer should not trigger command-not-found."""
        from ashyterm.terminal._streaming_handler import _COMMAND_NOT_FOUND

        handler._input_highlight_buffer = "bruno=10"
        from pygments.token import Token

        result = handler._enhance_token_type(Token.Text, "bruno")
        assert result is not _COMMAND_NOT_FOUND

    def test_variable_assignment_returns_original_type(self, handler):
        """VAR= should return original token type (not Function or Error)."""
        from pygments.token import Token

        handler._input_highlight_buffer = "MY_VAR=hello"
        result = handler._enhance_token_type(Token.Text, "MY_VAR")
        assert result == Token.Text

    def test_regular_command_still_validated(self, handler):
        """Non-assignment tokens at command position still get validated."""
        from pygments.token import Token

        handler._input_highlight_buffer = "ls"
        result = handler._enhance_token_type(Token.Text, "ls")
        assert result == Token.Name.Function  # ls is valid

    def test_command_after_pipe_still_works(self, handler):
        """Commands after pipe should still be validated."""
        from pygments.token import Token

        handler._input_highlight_buffer = "echo a | grep test"
        result = handler._enhance_token_type(Token.Text, "grep")
        assert result == Token.Name.Function

    def test_warning_commands_unaffected(self, handler):
        """Warning commands like rm/sudo should still be flagged."""
        from pygments.token import Token

        handler._input_highlight_buffer = "rm -rf"
        result = handler._enhance_token_type(Token.Text, "rm")
        assert result == Token.Name.Exception
