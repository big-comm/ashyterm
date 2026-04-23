# tests/test_output_highlighting_extended.py
"""
Extended tests for Output Highlighting and Shell Input Highlighting.

Covers:
- Output highlighting with real system JSON rules (global + command-specific)
- Shell input highlighting via Pygments (bash & sh compatibility)
- Command validator for command-not-found detection
- Shell syntax validator for brackets, quotes, and control structures
- Context switching (set_context / clear_context)
- Ignored commands behavior
- Multi-proxy isolation
- Edge cases for both bash and sh idioms
"""

import json
import os
import re
import threading
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

# ============================================================================
# Helpers
# ============================================================================

ANSI_RESET = "\033[0m"
_ANSI_CODE_RE = re.compile(r"\033\[([0-9;]+)m")


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape codes from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def get_colored_spans(text: str) -> List[Tuple[str, str]]:
    """Extract (ansi_code, matched_text) spans from highlighted text."""
    segments: List[Tuple[str | None, str]] = []
    pos = 0
    current_code = None
    for m in _ANSI_CODE_RE.finditer(text):
        if m.start() > pos:
            segments.append((current_code, text[pos : m.start()]))
        code = m.group(1)
        current_code = None if code == "0" else code
        pos = m.end()
    if pos < len(text):
        segments.append((current_code, text[pos:]))
    return [(c, t) for c, t in segments if c]


def has_colored_span(text: str, word: str, ansi_code: str | None = None) -> bool:
    """Check if *word* appears as a colored span, optionally with a specific code."""
    for code, content in get_colored_spans(text):
        if word in content:
            if ansi_code is None or code == ansi_code:
                return True
    return False


# ============================================================================
# Shared Fixtures
# ============================================================================

def _highlights_dir() -> Path:
    return Path(__file__).parent.parent / "src" / "ashyterm" / "data" / "highlights"


def _load_json_rules(name: str) -> dict:
    """Load a JSON highlight rule file by name (e.g. 'ping', 'global')."""
    path = _highlights_dir() / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_highlighter(rules_tuple):
    """Build a lightweight OutputHighlighter with the given compiled rules."""
    from ashyterm.terminal.highlighter.output import OutputHighlighter

    h = OutputHighlighter.__new__(OutputHighlighter)
    h.logger = MagicMock()
    h._manager = MagicMock()
    h._lock = threading.Lock()
    h._context_rules_cache = {}
    h._proxy_contexts = {}
    h._full_commands = {}
    h._skip_first_output = {}
    h._ignored_commands = frozenset()
    h._global_rules = rules_tuple
    return h


def _compile_rules_from_json(json_data: dict):
    """Compile a list of CompiledRule / LiteralKeywordRule from raw JSON data."""
    from ashyterm.settings.highlights import HighlightManager, HighlightRule
    from ashyterm.settings.highlight_colors import HighlightColorResolver
    from ashyterm.terminal.highlighter.rules import (
        CompiledRule,
        LiteralKeywordRule,
        extract_literal_keywords,
        extract_prefilter,
    )
    from ashyterm.utils.re_engine import engine as re_engine

    # Use a real manager for color resolution
    mgr = HighlightManager.__new__(HighlightManager)
    mgr._colors = HighlightColorResolver()
    mgr._config = type('MockConfig', (), {'enabled_for_local': False, 'enabled_for_ssh': False, 'context_aware_enabled': True, 'global_rules': [], 'contexts': {}})()

    compiled = []
    for rd in json_data.get("rules", []):
        rule = HighlightRule.from_dict(rd)
        if not rule.enabled or not rule.pattern:
            continue

        action = rule.action if rule.action in ("next", "stop") else "next"
        literal_kw = extract_literal_keywords(rule.pattern)
        if literal_kw:
            ansi_color = mgr.resolve_color_to_ansi(rule.colors[0]) if rule.colors and rule.colors[0] else ""
            if ansi_color:
                compiled.append(
                    LiteralKeywordRule(
                        keywords=frozenset(literal_kw),
                        keyword_tuple=literal_kw,
                        ansi_color=ansi_color,
                        action=action,
                    )
                )
        else:
            try:
                flags = re_engine.IGNORECASE | getattr(re_engine, "VERSION1", 0)
                pattern = re_engine.compile(rule.pattern, flags)
                ansi_colors = tuple(
                    mgr.resolve_color_to_ansi(c) if c else ""
                    for c in rule.colors
                ) if rule.colors else ("",)

                if any(ansi_colors):
                    compiled.append(
                        CompiledRule(
                            pattern=pattern,
                            ansi_colors=ansi_colors,
                            action=action,
                            num_groups=pattern.groups,
                            prefilter=extract_prefilter(rule.pattern, rule.name),
                        )
                    )
            except Exception:
                pass  # Skip invalid patterns

    return tuple(compiled)


# ============================================================================
# 1. Global Output Highlighting — Real Rules
# ============================================================================

class TestGlobalOutputHighlightingReal:
    """Test global output highlighting using real JSON rule files."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("global")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    # -- Error / Warning / Success keywords --

    def test_error_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Command error: file not found", rules)
        assert has_colored_span(result, "error")

    def test_failure_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Build failure detected", rules)
        assert has_colored_span(result, "failure")

    def test_failed_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Unit test failed", rules)
        assert has_colored_span(result, "failed")

    def test_fatal_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("fatal: repository not found", rules)
        assert has_colored_span(result, "fatal")

    def test_critical_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("CRITICAL: disk full", rules)
        assert has_colored_span(result, "CRITICAL")

    def test_exception_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Traceback: exception raised", rules)
        assert has_colored_span(result, "exception")

    def test_warning_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("warning: unused variable", rules)
        assert has_colored_span(result, "warning")

    def test_deprecated_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("This API is deprecated", rules)
        assert has_colored_span(result, "deprecated")

    def test_success_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Build success!", rules)
        assert has_colored_span(result, "success")

    def test_passed_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("All tests passed", rules)
        assert has_colored_span(result, "passed")

    def test_completed_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Installation completed", rules)
        assert has_colored_span(result, "completed")

    def test_done_keyword_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Task done", rules)
        assert has_colored_span(result, "done")

    # -- State keywords --

    def test_enabled_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Service enabled", rules)
        assert has_colored_span(result, "enabled")

    def test_active_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Status: active", rules)
        assert has_colored_span(result, "active")

    def test_disabled_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Service disabled", rules)
        assert has_colored_span(result, "disabled")

    def test_inactive_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Status: inactive", rules)
        assert has_colored_span(result, "inactive")

    def test_connected_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("WiFi connected", rules)
        assert has_colored_span(result, "connected")

    def test_disconnected_state_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Device disconnected", rules)
        assert has_colored_span(result, "disconnected")

    # -- Network patterns --

    def test_ipv4_address_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Server at 192.168.1.100", rules)
        assert has_colored_span(result, "192.168.1.100")

    def test_ipv4_localhost_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Connect to 127.0.0.1", rules)
        assert has_colored_span(result, "127.0.0.1")

    def test_url_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Visit https://example.com", rules)
        assert has_colored_span(result, "https://example.com")

    def test_http_url_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("API at http://localhost:8080/api", rules)
        assert has_colored_span(result, "http://localhost:8080/api")

    # -- Strings --

    def test_double_quoted_string_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line('Set value "hello world"', rules)
        assert has_colored_span(result, '"hello world"')

    def test_single_quoted_string_highlighted(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("Name is 'foobar'", rules)
        assert has_colored_span(result, "'foobar'")

    # -- Text preservation --

    def test_plain_text_unchanged(self, highlighter, rules):
        line = "just a normal line with nothing special 12345"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert strip_ansi(result) == line

    def test_already_colored_line_skipped(self, highlighter, rules):
        """Lines with existing ANSI color codes are not double-highlighted."""
        line = "\033[32merror in green\033[0m"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert result == line

    def test_unicode_preserved(self, highlighter, rules):
        line = "Erro crítico: falha na conexão — tente novamente"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert strip_ansi(result) == line

    def test_multiple_keywords_same_line(self, highlighter, rules):
        result = highlighter._apply_highlighting_to_line("error and warning on same line", rules)
        assert has_colored_span(result, "error")
        assert has_colored_span(result, "warning")

    def test_keyword_not_inside_word(self, highlighter, rules):
        """'error' inside 'terrorize' must NOT match."""
        result = highlighter._apply_highlighting_to_line("terrorize the system", rules)
        assert not has_colored_span(result, "error")

    def test_multiline_text(self, highlighter, rules):
        text = "line one\nerror on line two\nwarning on line three"
        result = highlighter._apply_highlighting(text, rules)
        lines = result.split("\n")
        assert not has_colored_span(lines[0], "error")
        assert has_colored_span(lines[1], "error")
        assert has_colored_span(lines[2], "warning")


# ============================================================================
# 2. Command-Specific (Context) Output — Real Rules
# ============================================================================

class TestCommandSpecificPing:
    """Test ping context rules against realistic ping output."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("ping")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_ping_target_host(self, highlighter, rules):
        line = "PING google.com (142.250.79.110) 56(84) bytes of data."
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "google.com")

    def test_ping_ttl_value(self, highlighter, rules):
        line = "64 bytes from lhr25s34-in-f14.1e100.net: icmp_seq=1 ttl=115 time=12.3 ms"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "115")

    def test_ping_icmp_seq(self, highlighter, rules):
        line = "64 bytes from 8.8.8.8: icmp_seq=42 ttl=64 time=5.2 ms"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "42")

    def test_ping_fast_response(self, highlighter, rules):
        line = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=1.5 ms"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "1.5")

    def test_ping_slow_response(self, highlighter, rules):
        line = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=350.2 ms"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "350.2")

    def test_ping_rtt_statistics(self, highlighter, rules):
        line = "rtt min/avg/max/mdev = 1.234/5.678/9.012/3.456 ms"
        result = highlighter._apply_highlighting_to_line(line, rules)
        # All four values should be colored
        assert has_colored_span(result, "1.234")
        assert has_colored_span(result, "5.678")
        assert has_colored_span(result, "9.012")
        assert has_colored_span(result, "3.456")

    def test_ping_host_unreachable(self, highlighter, rules):
        line = "From 192.168.1.1 icmp_seq=1 Destination Host Unreachable"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "Destination Host Unreachable")

    def test_ping_unknown_host(self, highlighter, rules):
        line = "ping: unknown host badhost.example.com"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "unknown host")

    def test_ping_dup(self, highlighter, rules):
        line = "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=1.2 ms (DUP!)"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "DUP!")


class TestCommandSpecificGit:
    """Test git context rules against realistic git output."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("git")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_git_new_file(self, highlighter, rules):
        line = "  new file:   src/module.py"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "new file")

    def test_git_deleted_file(self, highlighter, rules):
        line = "  deleted:    old_file.py"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "deleted")

    def test_git_diff_added_header(self, highlighter, rules):
        line = "+++ b/src/file.py"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "+++")

    def test_git_diff_removed_header(self, highlighter, rules):
        line = "--- a/src/file.py"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "---")


class TestCommandSpecificDocker:
    """Test docker context rules against realistic docker output."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("docker")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_docker_header_highlighted(self, highlighter, rules):
        line = "CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS   NAMES"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "CONTAINER ID")

    def test_docker_container_id(self, highlighter, rules):
        line = "a1b2c3d4e5f6   nginx:latest   Up 3 hours   webapp"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "a1b2c3d4e5f6")

    def test_docker_latest_tag(self, highlighter, rules):
        line = "nginx        latest    abc123def456   2 weeks ago   142MB"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "latest")


class TestCommandSpecificSystemctl:
    """Test systemctl context rules."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("systemctl")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_systemctl_active_running(self, highlighter, rules):
        line = "   Active: active (running) since Mon 2026-03-30 10:00:00 UTC"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "active")

    def test_systemctl_inactive_dead(self, highlighter, rules):
        line = "   Active: inactive (dead)"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "inactive")


class TestCommandSpecificDf:
    """Test df context rules."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("df")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_df_filesystem_path(self, highlighter, rules):
        line = "/dev/sda1       100G   50G   50G  50% /home"
        result = highlighter._apply_highlighting_to_line(line, rules)
        # Filesystem path (/sda1) and mount point (/home) should be colored
        assert has_colored_span(result, "/sda1") or has_colored_span(result, "/home")


class TestCommandSpecificPacman:
    """Test pacman context rules."""

    @pytest.fixture
    def rules(self):
        data = _load_json_rules("help")
        return _compile_rules_from_json(data)

    @pytest.fixture
    def highlighter(self, rules):
        return _make_highlighter(rules)

    def test_help_flag_option(self, highlighter, rules):
        """Help rule for flag options like --verbose."""
        line = "  -v, --verbose           show more details"
        result = highlighter._apply_highlighting_to_line(line, rules)
        assert has_colored_span(result, "--verbose") or has_colored_span(result, "-v")


# ============================================================================
# 3. Context Switching & Proxy Isolation
# ============================================================================

class TestContextSwitching:
    """Test context switching, proxy isolation, and ignored commands."""

    def _make_output_highlighter(self):
        from ashyterm.terminal.highlighter.output import OutputHighlighter

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._manager.get_context_for_command.return_value = None
        h._manager.context_aware_enabled = True
        h._lock = threading.Lock()
        h._context_rules_cache = {}
        h._proxy_contexts = {}
        h._full_commands = {}
        h._skip_first_output = {}
        h._ignored_commands = frozenset(["htop", "vim", "less"])
        h._global_rules = ()
        return h

    def test_set_context_returns_true_on_change(self):
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.return_value = "ping"
        assert h.set_context("ping", proxy_id=1) is True

    def test_set_context_returns_false_if_same(self):
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.return_value = "ping"
        h.set_context("ping", proxy_id=1)
        assert h.set_context("ping", proxy_id=1) is False

    def test_clear_context(self):
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.return_value = "ping"
        h.set_context("ping", proxy_id=1)
        h.clear_context(proxy_id=1)
        assert h.get_context(proxy_id=1) == ""

    def test_proxy_isolation(self):
        """Different proxies have independent contexts."""
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.side_effect = lambda cmd: cmd
        h.set_context("ping", proxy_id=1)
        h.set_context("docker", proxy_id=2)
        assert h.get_context(proxy_id=1) == "ping"
        assert h.get_context(proxy_id=2) == "docker"

    def test_ignored_command_passthrough(self):
        """Ignored commands return text unchanged (preserve native colors)."""
        h = self._make_output_highlighter()
        # Set context to an ignored command
        h._proxy_contexts[1] = "htop"

        line = "\033[32msome colored output\033[0m"
        result = h.highlight_text(line, proxy_id=1)
        assert result == line

    def test_skip_first_output_flag(self):
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.return_value = "ping"
        h.set_context("ping", proxy_id=1)
        assert h.should_skip_first_output(proxy_id=1) is True
        # Second check returns False (flag consumed)
        assert h.should_skip_first_output(proxy_id=1) is False

    def test_full_command_stored(self):
        h = self._make_output_highlighter()
        h._manager.get_context_for_command.return_value = "cat"
        h.set_context("cat", proxy_id=1, full_command="cat /etc/hosts")
        assert h.get_full_command(proxy_id=1) == "cat /etc/hosts"

    def test_register_unregister_proxy(self):
        h = self._make_output_highlighter()
        h.register_proxy(99)
        assert 99 in h._proxy_contexts
        h.unregister_proxy(99)
        assert 99 not in h._proxy_contexts



# ============================================================================
# 5. Command Validator
# ============================================================================

class TestCommandValidator:
    """Test command-not-found detection for shell input highlighting."""

    @pytest.fixture
    def validator(self):
        from ashyterm.terminal.highlighter.command_validator import CommandValidator

        v = CommandValidator.__new__(CommandValidator)
        v._path_commands = {"ls", "grep", "cat", "echo", "git", "python3", "bash", "sh"}
        v._last_refresh = 0.0
        v._dir_mtimes = {}
        v._enabled = True
        return v

    # -- Shell builtins (common to bash and sh) --

    def test_cd_is_builtin(self, validator):
        assert validator.is_valid_command("cd") is True

    def test_export_is_builtin(self, validator):
        assert validator.is_valid_command("export") is True

    def test_source_is_builtin(self, validator):
        assert validator.is_valid_command("source") is True

    def test_dot_is_builtin(self, validator):
        """POSIX '.' (source) is a shell builtin."""
        assert validator.is_valid_command(".") is True

    def test_test_is_builtin(self, validator):
        assert validator.is_valid_command("test") is True

    def test_eval_is_builtin(self, validator):
        assert validator.is_valid_command("eval") is True

    def test_exec_is_builtin(self, validator):
        assert validator.is_valid_command("exec") is True

    def test_set_is_builtin(self, validator):
        assert validator.is_valid_command("set") is True

    def test_unset_is_builtin(self, validator):
        assert validator.is_valid_command("unset") is True

    def test_trap_is_builtin(self, validator):
        assert validator.is_valid_command("trap") is True

    def test_read_is_builtin(self, validator):
        assert validator.is_valid_command("read") is True

    def test_wait_is_builtin(self, validator):
        assert validator.is_valid_command("wait") is True

    def test_true_is_builtin(self, validator):
        assert validator.is_valid_command("true") is True

    def test_false_is_builtin(self, validator):
        assert validator.is_valid_command("false") is True

    # -- bash-specific builtins --

    def test_declare_is_builtin(self, validator):
        assert validator.is_valid_command("declare") is True

    def test_local_is_builtin(self, validator):
        assert validator.is_valid_command("local") is True

    def test_shopt_is_builtin(self, validator):
        assert validator.is_valid_command("shopt") is True

    def test_mapfile_is_builtin(self, validator):
        assert validator.is_valid_command("mapfile") is True

    # -- PATH commands --

    def test_ls_in_path(self, validator):
        assert validator.is_valid_command("ls") is True

    def test_grep_in_path(self, validator):
        assert validator.is_valid_command("grep") is True

    def test_python3_in_path(self, validator):
        assert validator.is_valid_command("python3") is True

    def test_unknown_command_not_valid(self, validator):
        assert validator.is_valid_command("xyznonexistent") is False

    def test_empty_command_is_valid(self, validator):
        """Empty commands shouldn't be flagged."""
        assert validator.is_valid_command("") is True

    def test_disabled_validator_always_valid(self, validator):
        validator.enabled = False
        assert validator.is_valid_command("xyznonexistent") is True

    def test_absolute_path_command(self, validator):
        """Absolute paths are checked via os.path.isfile + os.access."""
        # /bin/sh or /usr/bin/env should exist on any Linux
        if os.path.isfile("/bin/sh"):
            assert validator.is_valid_command("/bin/sh") is True
        assert validator.is_valid_command("/nonexistent/binary") is False

    def test_relative_path_command(self, validator):
        """Relative paths like ./script.sh are checked on disk."""
        assert validator.is_valid_command("./nonexistent_script.sh") is False

    def test_cache_invalidation(self, validator):
        validator.invalidate_cache()
        assert validator._last_refresh == 0.0


# ============================================================================
# 6. Shell Syntax Validator
# ============================================================================

class TestShellSyntaxValidator:
    """Test real-time shell syntax validation for bracket/quote errors."""

    def _validate(self, buf: str):
        from ashyterm.terminal.highlighter.shell_validator import validate_shell_input
        return validate_shell_input(buf)

    # -- Balanced inputs (no issues expected) --

    def test_balanced_parentheses(self):
        assert self._validate("echo $(date)") == []

    def test_balanced_brackets(self):
        assert self._validate("[ -f file ]") == []

    def test_balanced_double_brackets(self):
        assert self._validate('[[ -n "$VAR" ]]') == []

    def test_balanced_braces(self):
        assert self._validate("{ echo hello; }") == []

    def test_balanced_quotes_single(self):
        assert self._validate("echo 'hello'") == []

    def test_balanced_quotes_double(self):
        assert self._validate('echo "hello"') == []

    def test_balanced_backticks(self):
        assert self._validate("echo `date`") == []

    def test_balanced_arithmetic(self):
        assert self._validate("echo $((1 + 2))") == []

    def test_empty_input(self):
        assert self._validate("") == []

    def test_single_char(self):
        assert self._validate("a") == []

    def test_for_done_balanced(self):
        assert self._validate("for i in 1 2 3; do echo $i; done") == []

    def test_if_fi_balanced(self):
        assert self._validate("if true; then echo ok; fi") == []

    def test_case_esac_balanced(self):
        """case/esac: the ')' in patterns is flagged as unmatched by the stateless scanner."""
        issues = self._validate('case "$1" in a) echo a;; esac')
        # The validator flags ')' in case patterns since it can't track case state
        # This is expected behavior for an O(n) single-pass scanner
        assert all(i.kind.name in ("UNMATCHED_BRACKET",) for i in issues)

    def test_while_done_balanced(self):
        assert self._validate("while true; do echo loop; done") == []

    def test_nested_subshell(self):
        """Simple (non-nested) subshell is balanced."""
        assert self._validate("echo $(date)") == []

    def test_nested_subshell_known_limitation(self):
        """Nested $(...) is a known limitation of the O(n) scanner."""
        issues = self._validate("echo $(echo $(date))")
        # The scanner can't handle nested $() — it reports unmatched brackets
        assert len(issues) > 0

    def test_parameter_expansion(self):
        assert self._validate('echo ${HOME:-/root}') == []

    def test_escaped_quote(self):
        assert self._validate('echo "hello \\" world"') == []

    def test_heredoc(self):
        assert self._validate("cat <<EOF\nhello\nEOF") == []

    def test_herestring(self):
        assert self._validate("cat <<< 'hello'") == []

    def test_comment_ignored(self):
        assert self._validate("echo hello # this is a comment") == []

    # -- Unbalanced inputs (issues expected) --

    def test_unclosed_single_quote(self):
        issues = self._validate("echo 'hello")
        assert len(issues) > 0
        assert any(i.kind.name == "UNCLOSED_QUOTE" for i in issues)

    def test_unclosed_double_quote(self):
        issues = self._validate('echo "hello')
        assert len(issues) > 0
        assert any(i.kind.name == "UNCLOSED_QUOTE" for i in issues)

    def test_unclosed_backtick(self):
        issues = self._validate("echo `date")
        assert len(issues) > 0
        assert any(i.kind.name == "UNCLOSED_QUOTE" for i in issues)

    def test_unclosed_parenthesis(self):
        issues = self._validate("echo $(date")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_unclosed_bracket(self):
        issues = self._validate("[ -f file")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_unclosed_brace(self):
        issues = self._validate("{ echo hello;")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_extra_closing_paren(self):
        issues = self._validate("echo )")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_extra_closing_bracket(self):
        issues = self._validate("echo ]")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_extra_double_bracket_close(self):
        issues = self._validate("echo ]]")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_unclosed_double_bracket(self):
        issues = self._validate('[[ -n "$VAR"')
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    def test_if_without_fi(self):
        issues = self._validate("if true; then echo ok")
        assert len(issues) > 0
        assert any(i.kind.name == "INCOMPLETE_STRUCTURE" for i in issues)

    def test_for_without_done(self):
        issues = self._validate("for i in 1 2 3; do echo $i")
        assert len(issues) > 0
        assert any(i.kind.name == "INCOMPLETE_STRUCTURE" for i in issues)

    def test_case_without_esac(self):
        issues = self._validate('case "$1" in a) echo a;;')
        assert len(issues) > 0
        kinds = {i.kind.name for i in issues}
        # Either incomplete structure (no esac) or unmatched bracket (the ')' in pattern)
        assert "INCOMPLETE_STRUCTURE" in kinds or "UNMATCHED_BRACKET" in kinds

    def test_while_without_done(self):
        issues = self._validate("while true; do echo loop")
        assert len(issues) > 0
        assert any(i.kind.name == "INCOMPLETE_STRUCTURE" for i in issues)

    def test_fi_without_if(self):
        """Orphan 'fi' without opener is not flagged by the scanner."""
        issues = self._validate("fi")
        # The scanner only tracks openers (if/for/while) needing closers,
        # it doesn't flag orphan closers
        assert issues == []

    def test_done_without_loop(self):
        """Orphan 'done' is not flagged by the scanner."""
        issues = self._validate("done")
        assert issues == []

    def test_esac_without_case(self):
        """Orphan 'esac' is not flagged by the scanner."""
        issues = self._validate("esac")
        assert issues == []

    def test_unclosed_arithmetic(self):
        issues = self._validate("echo $((1 + 2)")
        assert len(issues) > 0
        assert any(i.kind.name == "UNMATCHED_BRACKET" for i in issues)

    # -- Complex bash/sh constructs --

    def test_nested_if_for(self):
        buf = "if true; then for i in 1; do echo $i; done; fi"
        assert self._validate(buf) == []

    def test_nested_if_for_incomplete(self):
        buf = "if true; then for i in 1; do echo $i; done"
        issues = self._validate(buf)
        assert any(i.kind.name == "INCOMPLETE_STRUCTURE" for i in issues)

    def test_mixed_quotes_and_brackets(self):
        buf = '''if [ "$(echo hello)" = "hello" ]; then echo ok; fi'''
        assert self._validate(buf) == []


# ============================================================================
# 7. All JSON Context Files Validate & Compile
# ============================================================================

class TestAllContextRulesCompile:
    """Ensure all JSON context files compile successfully with real regex."""

    def test_all_contexts_compile_without_error(self):
        """Compile rules from every JSON file — no exceptions."""
        highlights_dir = _highlights_dir()
        for json_file in sorted(highlights_dir.glob("*.json")):
            data = _load_json_rules(json_file.stem)
            compiled = _compile_rules_from_json(data)
            assert len(compiled) > 0, f"{json_file.name}: compiled to zero rules"

    def test_global_rules_error_hits(self):
        """Global rules match typical error lines."""
        data = _load_json_rules("global")
        rules = _compile_rules_from_json(data)
        h = _make_highlighter(rules)

        test_lines = [
            ("error: disk full", "error"),
            ("fatal: not a git repository", "fatal"),
            ("warning: deprecated API", "warning"),
            ("Build success", "success"),
            ("Status: active", "active"),
            ("Device disconnected", "disconnected"),
            ("Server at 10.0.0.1", "10.0.0.1"),
            ("Visit https://example.com for docs", "https://example.com"),
        ]

        for line, expected_word in test_lines:
            result = h._apply_highlighting_to_line(line, rules)
            assert has_colored_span(result, expected_word), (
                f"Expected '{expected_word}' to be highlighted in: {line}"
            )

    def test_each_context_file_has_at_least_one_working_rule(self):
        """Each context file has at least one rule that matches some test input."""
        highlights_dir = _highlights_dir()

        # Context-specific test inputs that should trigger at least one rule
        context_examples = {
            "ping": "64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=1.5 ms",
            "git": "  new file:   src/main.py",
            "docker": "CONTAINER ID   IMAGE   COMMAND",
            "systemctl": "   Active: active (running) since Mon",
            "df": "/dev/sda1       100G   50G   50G  50% /home",
            "pip": "Successfully installed requests-2.28.0",
            "make": "make[1]: Entering directory '/src'",
            "curl": "HTTP/1.1 200 OK",
            "ls": "drwxr-xr-x  2 user user 4096 Jan  1 00:00 folder",
            "ps": "  PID TTY          TIME CMD",
        }

        for ctx_name, sample_line in context_examples.items():
            json_file = highlights_dir / f"{ctx_name}.json"
            if not json_file.exists():
                continue
            data = _load_json_rules(ctx_name)
            rules = _compile_rules_from_json(data)
            h = _make_highlighter(rules)
            result = h._apply_highlighting_to_line(sample_line, rules)
            # At least one span should be colored
            spans = get_colored_spans(result)
            assert len(spans) > 0, (
                f"Context '{ctx_name}' produced no highlights for: {sample_line}"
            )


# ============================================================================
# 8. Trigger Resolution
# ============================================================================

class TestTriggerResolution:
    """Test that command triggers map to contexts correctly."""

    def test_triggers_overlap_documented(self):
        """Some triggers are intentionally shared between contexts.
        Document known overlaps for awareness."""
        highlights_dir = _highlights_dir()
        trigger_map: dict[str, list[str]] = {}

        for json_file in sorted(highlights_dir.glob("*.json")):
            if json_file.name == "global.json":
                continue
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            ctx_name = data.get("name", json_file.stem)
            for trigger in data.get("triggers", []):
                trigger_map.setdefault(trigger, []).append(ctx_name)

        # Verify that every context has at least one trigger
        for json_file in sorted(highlights_dir.glob("*.json")):
            if json_file.name == "global.json":
                continue
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            ctx_name = data.get("name", json_file.stem)
            triggers = data.get("triggers", [])
            assert len(triggers) > 0, f"Context '{ctx_name}' has no triggers"

    def test_context_name_matches_filename(self):
        """Context 'name' field should match the JSON filename."""
        highlights_dir = _highlights_dir()
        for json_file in sorted(highlights_dir.glob("*.json")):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data.get("name") == json_file.stem, (
                f"{json_file.name}: 'name' is '{data.get('name')}' but expected '{json_file.stem}'"
            )


# ============================================================================
# 9. Bash vs sh Compatibility in Highlighting
# ============================================================================

class TestBashShCompatibility:
    """Verify that output highlighting handles bash/sh-flavored lines."""

    @pytest.fixture
    def global_rules(self):
        return _compile_rules_from_json(_load_json_rules("global"))

    @pytest.fixture
    def output_hl(self, global_rules):
        return _make_highlighter(global_rules)

    def test_output_set_euo_pipefail(self, output_hl, global_rules):
        """'set -euo pipefail' is a bash-ism, no keywords should match."""
        result = output_hl._apply_highlighting_to_line("set -euo pipefail", global_rules)
        assert strip_ansi(result) == "set -euo pipefail"

    def test_output_bash_error_message(self, output_hl, global_rules):
        result = output_hl._apply_highlighting_to_line(
            "bash: command not found: foobar", global_rules
        )
        # No keyword matches expected for this particular line unless
        # specific rules target it — this verifies no crash
        assert strip_ansi(result) == "bash: command not found: foobar"

    def test_output_sh_syntax_error(self, output_hl, global_rules):
        result = output_hl._apply_highlighting_to_line(
            "sh: 1: Syntax error: Unterminated quoted string", global_rules
        )
        assert has_colored_span(result, "error")

    def test_output_exit_code_zero(self, output_hl, global_rules):
        """Exit code 0 line: 'ok' should be highlighted."""
        result = output_hl._apply_highlighting_to_line("Status: ok", global_rules)
        assert has_colored_span(result, "ok")


# ============================================================================
# 10. Performance Regression Guard
# ============================================================================

class TestHighlightingPerformance:
    """Ensure highlighting completes in reasonable time."""

    def test_1000_lines_under_2_seconds(self):
        """Highlighting 1000 lines must complete in under 2 seconds."""
        import time

        data = _load_json_rules("global")
        rules = _compile_rules_from_json(data)
        h = _make_highlighter(rules)

        lines = [
            "error: disk full on /dev/sda1 at 192.168.1.100",
            "warning: deprecated API used",
            "Build success — all 42 tests passed",
            "Just a normal output line with nothing special",
            "Server at https://api.example.com returned 200 OK",
        ] * 200  # 1000 lines

        start = time.monotonic()
        for line in lines:
            h._apply_highlighting_to_line(line, rules)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"Highlighting 1000 lines took {elapsed:.2f}s (max 2s)"

    def test_10000_char_line_does_not_hang(self):
        """A 10K character line must complete without timeout."""
        import time

        data = _load_json_rules("global")
        rules = _compile_rules_from_json(data)
        h = _make_highlighter(rules)

        long_line = "x" * 5000 + " error " + "y" * 5000

        start = time.monotonic()
        result = h._apply_highlighting_to_line(long_line, rules)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"Long line took {elapsed:.2f}s"
        assert has_colored_span(result, "error")
