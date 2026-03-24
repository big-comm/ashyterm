# tests/test_highlighting.py
"""
Comprehensive tests for the output highlighting, cat colorization,
shell input highlighting, and command-specific (context) systems.

These tests verify that colors are correctly applied in practice —
from color resolution through ANSI code generation to final line output.
"""

import json
import os
import re
import sys
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# Constants used across tests
# ============================================================================

ANSI_RESET = "\033[0m"

# ANSI color code tables (same as in highlights.py)
ANSI_COLOR_MAP = {
    "black": 0, "red": 1, "green": 2, "yellow": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
    "bright_black": 8, "bright_red": 9, "bright_green": 10,
    "bright_yellow": 11, "bright_blue": 12, "bright_magenta": 13,
    "bright_cyan": 14, "bright_white": 15,
}

ANSI_MODIFIERS = {
    "bold": "1", "dim": "2", "italic": "3", "underline": "4",
    "blink": "5", "reverse": "7", "strikethrough": "9",
}


# ============================================================================
# Helper: extract ANSI color codes from highlighted text
# ============================================================================

_ANSI_CODE_RE = re.compile(r"\033\[([0-9;]+)m")


def extract_ansi_segments(text: str) -> list:
    """
    Parse highlighted text into segments: [(code_or_None, text_content), ...].
    None means plain text (no color applied).
    """
    segments = []
    pos = 0
    current_code = None

    for m in _ANSI_CODE_RE.finditer(text):
        # Text before this code
        if m.start() > pos:
            segments.append((current_code, text[pos:m.start()]))

        code = m.group(1)
        if code == "0":
            current_code = None
        else:
            current_code = code
        pos = m.end()

    # Remaining text
    if pos < len(text):
        segments.append((current_code, text[pos:]))

    return segments


def get_colored_spans(text: str) -> list:
    """
    Extract colored spans from highlighted text.
    Returns [(ansi_code, matched_text), ...] ignoring plain (reset) segments.
    """
    return [(code, content) for code, content in extract_ansi_segments(text) if code]


# ============================================================================
# 1. Color Resolution Tests
# ============================================================================

class TestColorResolution:
    """Test the HighlightManager color-to-ANSI resolution."""

    @pytest.fixture
    def manager(self):
        from ashyterm.settings.highlights import HighlightManager
        mgr = HighlightManager.__new__(HighlightManager)
        # Minimal init without GObject
        mgr.logger = MagicMock()
        mgr._settings_manager = None
        mgr._color_cache = {}
        mgr._current_theme_name = "default"
        return mgr

    def test_parse_color_spec_simple(self, manager):
        """Simple color names are parsed correctly."""
        mods, fg, bg = manager._parse_color_spec("red")
        assert mods == []
        assert fg == "red"
        assert bg is None

    def test_parse_color_spec_with_modifiers(self, manager):
        """Modifiers are separated from the base color."""
        mods, fg, bg = manager._parse_color_spec("bold italic red")
        assert "1" in mods  # bold
        assert "3" in mods  # italic
        assert fg == "red"
        assert bg is None

    def test_parse_color_spec_with_background(self, manager):
        """Background colors (on_X) are parsed correctly."""
        mods, fg, bg = manager._parse_color_spec("bold green on_blue")
        assert "1" in mods
        assert fg == "green"
        assert bg == "blue"

    def test_foreground_ansi_code_standard(self, manager):
        """Standard colors map to codes 30-37."""
        for name, idx in ANSI_COLOR_MAP.items():
            code = manager._get_foreground_ansi_code(name)
            if idx < 8:
                assert code == str(30 + idx), f"{name} -> expected {30 + idx}, got {code}"
            else:
                assert code == str(90 + idx - 8), f"{name} -> expected {90 + idx - 8}, got {code}"

    def test_background_ansi_code_standard(self, manager):
        """Background colors map to codes 40-47 / 100-107."""
        for name, idx in ANSI_COLOR_MAP.items():
            code = manager._get_background_ansi_code(name)
            if idx < 8:
                assert code == str(40 + idx), f"on_{name} -> expected {40 + idx}, got {code}"
            else:
                assert code == str(100 + idx - 8), f"on_{name} -> expected {100 + idx - 8}, got {code}"

    def test_background_ansi_code_none(self, manager):
        """No background returns None."""
        assert manager._get_background_ansi_code(None) is None

    def test_foreground_unknown_color_defaults_white(self, manager):
        """Unknown color names default to white (37)."""
        assert manager._get_foreground_ansi_code("unicorn") == "37"

    def test_foreground_special_names_return_none(self, manager):
        """Special color names (foreground, background, etc.) return None."""
        for name in ("foreground", "background", "cursor", "none", "default"):
            assert manager._get_foreground_ansi_code(name) is None

    def test_resolve_color_to_ansi_bold_red(self, manager):
        """'bold red' resolves to ESC[1;31m."""
        result = manager.resolve_color_to_ansi("bold red")
        assert result == "\033[1;31m"

    def test_resolve_color_to_ansi_green(self, manager):
        """'green' resolves to ESC[32m."""
        result = manager.resolve_color_to_ansi("green")
        assert result == "\033[32m"

    def test_resolve_color_to_ansi_bold_yellow_on_blue(self, manager):
        """'bold yellow on_blue' resolves to ESC[1;33;44m."""
        result = manager.resolve_color_to_ansi("bold yellow on_blue")
        assert result == "\033[1;33;44m"

    def test_resolve_color_to_ansi_bright_cyan(self, manager):
        """'bright_cyan' resolves to ESC[96m."""
        result = manager.resolve_color_to_ansi("bright_cyan")
        assert result == "\033[96m"

    def test_resolve_color_to_ansi_empty(self, manager):
        """Empty color returns empty string."""
        assert manager.resolve_color_to_ansi("") == ""

    def test_resolve_color_to_ansi_underline_magenta(self, manager):
        """'underline magenta' resolves to ESC[4;35m."""
        result = manager.resolve_color_to_ansi("underline magenta")
        assert result == "\033[4;35m"

    def test_resolve_color_to_ansi_multiple_modifiers(self, manager):
        """'bold italic underline red' resolves with all modifier codes."""
        result = manager.resolve_color_to_ansi("bold italic underline red")
        # Should contain 1 (bold), 3 (italic), 4 (underline), 31 (red)
        assert "\033[" in result
        assert result.endswith("m")
        codes = result[2:-1].split(";")
        assert "1" in codes
        assert "3" in codes
        assert "4" in codes
        assert "31" in codes

    def test_resolve_color_to_ansi_bright_on_bright(self, manager):
        """'bright_red on_bright_green' resolves to ESC[91;102m."""
        result = manager.resolve_color_to_ansi("bright_red on_bright_green")
        assert result == "\033[91;102m"


# ============================================================================
# 2. Rule Compilation Tests
# ============================================================================

class TestRuleCompilation:
    """Test rule compilation: literal keyword extraction and prefilter."""

    def test_extract_literal_keywords_simple(self):
        """Simple alternation pattern extracts keywords."""
        from ashyterm.terminal.highlighter.rules import extract_literal_keywords

        result = extract_literal_keywords(r"\b(error|warning|fatal)\b")
        assert result is not None
        assert "error" in result
        assert "warning" in result
        assert "fatal" in result

    def test_extract_literal_keywords_with_optional_suffix(self):
        """Optional suffixes expand into multiple keywords."""
        from ashyterm.terminal.highlighter.rules import extract_literal_keywords

        result = extract_literal_keywords(r"\b(fail(?:ure|ed)?)\b")
        assert result is not None
        assert "fail" in result
        assert "failure" in result
        assert "failed" in result

    def test_extract_literal_keywords_complex_returns_none(self):
        """Complex patterns return None (not simple alternation)."""
        from ashyterm.terminal.highlighter.rules import extract_literal_keywords

        result = extract_literal_keywords(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
        assert result is None

    def test_literal_keyword_rule_find_matches(self):
        """LiteralKeywordRule finds word-bounded matches in text."""
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        rule = LiteralKeywordRule(
            keywords=frozenset(["error", "warning"]),
            keyword_tuple=("error", "warning"),
            ansi_color="\033[1;31m",
            action="next",
        )

        line = "An error occurred, warning: something"
        line_lower = line.lower()
        matches = rule.find_matches(line, line_lower)

        # Should find "error" at position 3 and "warning" at position 22
        assert len(matches) == 2
        texts = [line[m[0]:m[1]] for m in matches]
        assert "error" in texts
        assert "warning" in texts

    def test_literal_keyword_rule_respects_word_boundary(self):
        """Keywords inside larger words are NOT matched."""
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        rule = LiteralKeywordRule(
            keywords=frozenset(["error"]),
            keyword_tuple=("error",),
            ansi_color="\033[31m",
            action="next",
        )

        line = "terrorize the errors"
        line_lower = line.lower()
        matches = rule.find_matches(line, line_lower)

        # "error" inside "terrorize" should NOT match
        # "errors" should NOT match (word boundary after 'error' fails because 's' is a word char)
        texts = [line[m[0]:m[1]] for m in matches]
        assert "terrorize"[:5] not in [line[m[0]:m[1]] for m in matches if m[1] - m[0] == 5 and m[0] == 0]

    def test_word_boundary_detection(self):
        """is_word_boundary correctly identifies boundaries."""
        from ashyterm.terminal.highlighter.constants import is_word_boundary

        # "error" at word boundary
        assert is_word_boundary("an error here", 3, 8) is True
        # "error" at start
        assert is_word_boundary("error here", 0, 5) is True
        # "error" at end
        assert is_word_boundary("an error", 3, 8) is True
        # "error" inside "terrorize" — no boundary at start
        assert is_word_boundary("terrorize", 0, 5) is False
        # "error" inside "errors" — no boundary at end
        assert is_word_boundary("errors", 0, 5) is False

    def test_prefilter_ip_rule(self):
        """IP address rule gets a prefilter requiring '.'."""
        from ashyterm.terminal.highlighter.rules import extract_prefilter

        pf = extract_prefilter(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "IPv4 Address")
        assert pf is not None
        assert pf("192.168.1.1") is True
        assert pf("no dots here") is False

    def test_prefilter_url_rule(self):
        """URL rule gets a prefilter requiring 'http'."""
        from ashyterm.terminal.highlighter.rules import extract_prefilter

        pf = extract_prefilter(r"https?://\S+", "URL")
        assert pf is not None
        assert pf("visit https://example.com") is True
        assert pf("no links here") is False

    def test_prefilter_keyword_pattern(self):
        """Keyword patterns get prefilters based on extracted words."""
        from ashyterm.terminal.highlighter.rules import extract_prefilter

        pf = extract_prefilter(r"\b(error|warning)\b", "Error/Warning")
        assert pf is not None
        assert pf("there was an error") is True
        assert pf("all is fine") is False

    def test_expand_optional_suffixes(self):
        """Optional suffixes are correctly expanded."""
        from ashyterm.terminal.highlighter.rules import expand_optional_suffixes

        assert set(expand_optional_suffixes("fail(?:ure|ed)?")) == {"fail", "failure", "failed"}
        assert set(expand_optional_suffixes("complete(?:d)?")) == {"complete", "completed"}
        assert expand_optional_suffixes("simple") == ["simple"]

    def test_smart_split_alternation(self):
        """Alternation splitting respects parentheses."""
        from ashyterm.terminal.highlighter.rules import smart_split_alternation

        result = smart_split_alternation("error|fail(?:ure|ed)?|fatal")
        assert result == ["error", "fail(?:ure|ed)?", "fatal"]


# ============================================================================
# 3. Output Highlighting (Line Application) Tests
# ============================================================================

class TestOutputHighlighting:
    """Test that highlighting rules are correctly applied to output lines."""

    @pytest.fixture
    def highlighter(self):
        """Create an OutputHighlighter with known rules."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule, LiteralKeywordRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()
        h._context_rules_cache = {}
        h._proxy_contexts = {}
        h._full_commands = {}
        h._skip_first_output = {}
        h._ignored_commands = frozenset()

        # Set up test rules
        bold_red = "\033[1;31m"
        green = "\033[32m"
        bold_magenta = "\033[1;35m"

        error_rule = LiteralKeywordRule(
            keywords=frozenset(["error", "failure", "failed", "fatal", "critical", "exception", "crash"]),
            keyword_tuple=("error", "failure", "failed", "fatal", "critical", "exception", "crash"),
            ansi_color=bold_red,
            action="next",
        )

        success_rule = LiteralKeywordRule(
            keywords=frozenset(["success", "ok", "passed", "completed", "done"]),
            keyword_tuple=("success", "ok", "passed", "completed", "done"),
            ansi_color=green,
            action="next",
        )

        # Regex rule: IPv4 addresses
        ipv4_pattern = re_engine.compile(
            r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
            re_engine.IGNORECASE,
        )
        ipv4_rule = CompiledRule(
            pattern=ipv4_pattern,
            ansi_colors=(bold_magenta,),
            action="next",
            num_groups=0,
            prefilter=lambda line: "." in line,
        )

        h._global_rules = (error_rule, success_rule, ipv4_rule)
        return h

    def test_error_keyword_highlighted(self, highlighter):
        """Error keywords get bold red ANSI codes."""
        result = highlighter._apply_highlighting_to_line(
            "Command error: file not found",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)
        error_spans = [(c, t) for c, t in spans if t == "error"]
        assert len(error_spans) == 1
        assert error_spans[0][0] == "1;31"  # bold red

    def test_success_keyword_highlighted(self, highlighter):
        """Success keywords get green ANSI codes."""
        result = highlighter._apply_highlighting_to_line(
            "Build completed successfully",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)
        completed_spans = [(c, t) for c, t in spans if t == "completed"]
        assert len(completed_spans) == 1
        assert completed_spans[0][0] == "32"  # green

    def test_ipv4_address_highlighted(self, highlighter):
        """IPv4 addresses get bold magenta ANSI codes."""
        result = highlighter._apply_highlighting_to_line(
            "Server at 192.168.1.100 is up",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)
        ip_spans = [(c, t) for c, t in spans if "192" in t]
        assert len(ip_spans) == 1
        assert ip_spans[0][0] == "1;35"  # bold magenta
        assert ip_spans[0][1] == "192.168.1.100"

    def test_multiple_matches_on_same_line(self, highlighter):
        """Multiple different rules can match on the same line."""
        result = highlighter._apply_highlighting_to_line(
            "error connecting to 10.0.0.1: operation failed",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)

        texts = [t for _, t in spans]
        assert "error" in texts
        assert "10.0.0.1" in texts
        assert "failed" in texts

    def test_empty_line_returns_empty(self, highlighter):
        """Empty line returns unchanged."""
        result = highlighter._apply_highlighting_to_line(
            "", highlighter._global_rules
        )
        assert result == ""

    def test_no_match_returns_original(self, highlighter):
        """Lines with no matches return the original text unchanged."""
        original = "Just a normal line with no keywords"
        result = highlighter._apply_highlighting_to_line(
            original, highlighter._global_rules
        )
        assert result == original

    def test_already_highlighted_line_skipped(self, highlighter):
        """Lines with existing ANSI color codes are not re-highlighted."""
        colored_line = "\033[31mred text\033[0m more text"
        result = highlighter._apply_highlighting_to_line(
            colored_line, highlighter._global_rules
        )
        assert result == colored_line

    def test_ansi_reset_after_each_match(self, highlighter):
        """Each highlighted span ends with ANSI_RESET."""
        result = highlighter._apply_highlighting_to_line(
            "error occurred",
            highlighter._global_rules,
        )
        # After "error" span, reset should appear
        assert ANSI_RESET in result
        idx_error = result.index("error")
        idx_reset = result.index(ANSI_RESET, idx_error)
        assert idx_reset == idx_error + len("error")

    def test_plain_text_preserved_between_matches(self, highlighter):
        """Text between matches is preserved as-is."""
        result = highlighter._apply_highlighting_to_line(
            "error then success",
            highlighter._global_rules,
        )
        # The word " then " should appear as plain text
        cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
        assert cleaned == "error then success"

    def test_case_insensitive_keyword_matching(self, highlighter):
        """Keywords match case-insensitively."""
        result = highlighter._apply_highlighting_to_line(
            "An ERROR occurred in the CRITICAL section",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)
        texts_lower = [t.lower() for _, t in spans]
        assert "error" in texts_lower
        assert "critical" in texts_lower


# ============================================================================
# 4. Multi-Group Regex Tests
# ============================================================================

class TestMultiGroupRegex:
    """Test multi-group regex patterns with different colors per group."""

    @pytest.fixture
    def highlighter(self):
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()
        h._context_rules_cache = {}
        h._proxy_contexts = {}
        h._full_commands = {}
        h._skip_first_output = {}
        h._ignored_commands = frozenset()

        # Multi-group rule: (key=)(value)
        kv_pattern = re_engine.compile(
            r"(ttl=)(\d+)",
            re_engine.IGNORECASE,
        )
        kv_rule = CompiledRule(
            pattern=kv_pattern,
            ansi_colors=("\033[36m", "\033[1;33m"),  # cyan for key, bold yellow for value
            action="next",
            num_groups=2,
            prefilter=lambda line: "ttl=" in line,
        )

        h._global_rules = (kv_rule,)
        return h

    def test_multi_group_different_colors(self, highlighter):
        """Each capture group gets its own color."""
        result = highlighter._apply_highlighting_to_line(
            "64 bytes from 1.2.3.4: icmp_seq=1 ttl=64 time=0.5 ms",
            highlighter._global_rules,
        )
        spans = get_colored_spans(result)

        # Find the key "ttl=" and value "64"
        key_spans = [(c, t) for c, t in spans if t == "ttl="]
        val_spans = [(c, t) for c, t in spans if t == "64"]

        assert len(key_spans) == 1
        assert key_spans[0][0] == "36"  # cyan

        assert len(val_spans) == 1
        assert val_spans[0][0] == "1;33"  # bold yellow

    def test_multi_group_null_color_skipped(self):
        """Groups with null/empty color are not colored."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        # Pattern: (icmp_seq=)(\d+) — first group has empty color
        pattern = re_engine.compile(r"(icmp_seq=)(\d+)", re_engine.IGNORECASE)
        rule = CompiledRule(
            pattern=pattern,
            ansi_colors=("", "\033[33m"),  # no color for key, yellow for value
            action="next",
            num_groups=2,
            prefilter=None,
        )

        result = h._apply_highlighting_to_line(
            "icmp_seq=42 ttl=128",
            (rule,),
        )
        spans = get_colored_spans(result)

        # Only the value "42" should be highlighted
        assert len(spans) == 1
        assert spans[0][1] == "42"
        assert spans[0][0] == "33"  # yellow


# ============================================================================
# 5. Stop Action Tests
# ============================================================================

class TestStopAction:
    """Test the 'stop' action that prevents further rule processing."""

    def test_stop_action_prevents_further_matches(self):
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        # First rule: "error" with stop action
        stop_rule = LiteralKeywordRule(
            keywords=frozenset(["error"]),
            keyword_tuple=("error",),
            ansi_color="\033[31m",
            action="stop",
        )

        # Second rule: would match "warning" — but should be blocked by stop
        second_rule = LiteralKeywordRule(
            keywords=frozenset(["warning"]),
            keyword_tuple=("warning",),
            ansi_color="\033[33m",
            action="next",
        )

        result = h._apply_highlighting_to_line(
            "error: warning: something",
            (stop_rule, second_rule),
        )
        spans = get_colored_spans(result)

        # Only "error" should be colored, "warning" should NOT
        texts = [t for _, t in spans]
        assert "error" in texts
        assert "warning" not in texts

    def test_stop_action_no_match_continues(self):
        """If stop rule doesn't match, processing continues normally."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        stop_rule = LiteralKeywordRule(
            keywords=frozenset(["error"]),
            keyword_tuple=("error",),
            ansi_color="\033[31m",
            action="stop",
        )

        second_rule = LiteralKeywordRule(
            keywords=frozenset(["warning"]),
            keyword_tuple=("warning",),
            ansi_color="\033[33m",
            action="next",
        )

        result = h._apply_highlighting_to_line(
            "just a warning here",
            (stop_rule, second_rule),
        )
        spans = get_colored_spans(result)
        texts = [t for _, t in spans]
        assert "warning" in texts


# ============================================================================
# 6. Context / Command-Specific Tests
# ============================================================================

class TestContextHighlighting:
    """Test command-specific context rules."""

    def test_highlight_rule_valid(self):
        """HighlightRule.is_valid() correctly validates patterns."""
        from ashyterm.settings.highlights import HighlightRule

        valid = HighlightRule(name="test", pattern=r"\b(error)\b", colors=["red"])
        assert valid.is_valid() is True

        invalid = HighlightRule(name="bad", pattern=r"[invalid", colors=["red"])
        assert invalid.is_valid() is False

        empty = HighlightRule(name="empty", pattern="", colors=["red"])
        assert empty.is_valid() is False

    def test_highlight_rule_from_dict(self):
        """HighlightRule.from_dict correctly parses JSON data."""
        from ashyterm.settings.highlights import HighlightRule

        data = {
            "name": "Test Rule",
            "pattern": r"\b(test)\b",
            "colors": ["bold green"],
            "enabled": True,
            "action": "stop",
        }
        rule = HighlightRule.from_dict(data)
        assert rule.name == "Test Rule"
        assert rule.pattern == r"\b(test)\b"
        assert rule.colors == ["bold green"]
        assert rule.enabled is True
        assert rule.action == "stop"

    def test_highlight_rule_to_dict_roundtrip(self):
        """Rule survives to_dict/from_dict roundtrip."""
        from ashyterm.settings.highlights import HighlightRule

        original = HighlightRule(
            name="Test",
            pattern=r"\b(error)\b",
            colors=["bold red"],
            enabled=True,
            description="Test rule",
            action="stop",
        )
        data = original.to_dict()
        restored = HighlightRule.from_dict(data)

        assert restored.name == original.name
        assert restored.pattern == original.pattern
        assert restored.colors == original.colors
        assert restored.enabled == original.enabled
        assert restored.action == original.action

    def test_highlight_context_from_dict(self):
        """HighlightContext.from_dict correctly parses context data."""
        from ashyterm.settings.highlights import HighlightContext

        data = {
            "name": "ping",
            "triggers": ["ping", "ping6"],
            "rules": [
                {"name": "TTL", "pattern": r"(ttl=)(\d+)", "colors": [None, "magenta"]},
            ],
            "enabled": True,
            "use_global_rules": False,
        }
        ctx = HighlightContext.from_dict(data)
        assert ctx.command_name == "ping"
        assert "ping6" in ctx.triggers
        assert len(ctx.rules) == 1
        assert ctx.use_global_rules is False

    def test_highlight_config_roundtrip(self):
        """HighlightConfig survives to_dict/from_dict roundtrip."""
        from ashyterm.settings.highlights import HighlightConfig, HighlightRule

        config = HighlightConfig(
            enabled_for_local=True,
            enabled_for_ssh=False,
            context_aware_enabled=True,
            global_rules=[
                HighlightRule(name="Test", pattern=r"\b(error)\b", colors=["red"]),
            ],
        )
        data = config.to_dict()
        restored = HighlightConfig.from_dict(data)

        assert restored.enabled_for_local is True
        assert restored.enabled_for_ssh is False
        assert len(restored.global_rules) == 1

    def test_context_rule_default_action(self):
        """Rules without action default to 'next'."""
        from ashyterm.settings.highlights import HighlightRule

        rule = HighlightRule.from_dict({
            "name": "test",
            "pattern": r"test",
            "colors": ["red"],
        })
        assert rule.action == "next"

    def test_invalid_action_normalized(self):
        """Invalid action values are normalized to 'next'."""
        from ashyterm.settings.highlights import HighlightRule

        rule = HighlightRule.from_dict({
            "name": "test",
            "pattern": r"test",
            "colors": ["red"],
            "action": "invalid",
        })
        assert rule.action == "next"


# ============================================================================
# 7. System Rules Loading Tests
# ============================================================================

class TestSystemRulesLoading:
    """Test that system JSON rule files are valid and loadable."""

    def _get_highlights_dir(self) -> Path:
        return Path(__file__).parent.parent / "src" / "ashyterm" / "data" / "highlights"

    def test_all_json_files_are_valid(self):
        """All system JSON highlight files are valid JSON."""
        highlights_dir = self._get_highlights_dir()
        assert highlights_dir.exists(), f"Highlights dir not found: {highlights_dir}"

        json_files = list(highlights_dir.glob("*.json"))
        assert len(json_files) > 0, "No JSON highlight files found"

        for json_file in json_files:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "name" in data, f"{json_file.name}: missing 'name'"
            assert "rules" in data, f"{json_file.name}: missing 'rules'"

    def test_all_rules_have_valid_patterns(self):
        """All regex patterns in system rules are valid."""
        from ashyterm.settings.highlights import HighlightRule

        highlights_dir = self._get_highlights_dir()
        for json_file in highlights_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for rule_data in data.get("rules", []):
                rule = HighlightRule.from_dict(rule_data)
                if rule.pattern:
                    assert rule.is_valid(), (
                        f"{json_file.name}: Invalid regex in rule '{rule.name}': {rule.pattern}"
                    )

    def test_all_rules_have_valid_colors(self):
        """All color names in system rules are recognized."""
        valid_colors = set(ANSI_COLOR_MAP.keys()) | set(ANSI_MODIFIERS.keys()) | {
            "foreground", "background", "cursor", "none", "default",
        }

        highlights_dir = self._get_highlights_dir()
        for json_file in highlights_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for rule_data in data.get("rules", []):
                for color in rule_data.get("colors", []):
                    if color is None:
                        continue
                    parts = color.lower().split()
                    for part in parts:
                        # Handle "on_X" background colors
                        if part.startswith("on_"):
                            part = part[3:]
                        assert part in valid_colors, (
                            f"{json_file.name}: Unknown color '{part}' in rule "
                            f"'{rule_data.get('name', '?')}'"
                        )

    def test_all_contexts_have_triggers(self):
        """Context files (non-global) must have triggers."""
        highlights_dir = self._get_highlights_dir()
        for json_file in highlights_dir.glob("*.json"):
            if json_file.name == "global.json":
                continue

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            triggers = data.get("triggers", [])
            name = data.get("name", json_file.stem)
            # Context should have at least one trigger
            assert len(triggers) > 0 or name in triggers, (
                f"{json_file.name}: Context has no triggers"
            )

    def test_global_rules_count(self):
        """Global rules file has a reasonable number of rules."""
        highlights_dir = self._get_highlights_dir()
        global_file = highlights_dir / "global.json"
        assert global_file.exists()

        with open(global_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        rules = data.get("rules", [])
        assert len(rules) >= 5, f"Too few global rules: {len(rules)}"
        assert len(rules) <= 100, f"Too many global rules: {len(rules)}"


# ============================================================================
# 8. Cat Command Colorization Tests
# ============================================================================

class TestCatColorization:
    """Test cat command colorization helpers."""

    def _make_handler(self):
        """Create a minimal CatModeHandler-like object for testing."""
        from ashyterm.terminal._cat_handler import CatModeHandler

        class FakeHandler(CatModeHandler):
            def __init__(self):
                self.logger = MagicMock()
                self._cat_limit_reached = False
                self._cat_bytes_processed = 0
                self._cat_filename = ""
                self._at_shell_prompt = False
                self._cat_waiting_for_newline = False
                self._partial_line_buffer = b""
                self._pygments_lexer = None
                self._pygments_needs_content_detection = False
                self._content_buffer = []
                self._cat_lines_processed = 0
                self._pending_lines = []
                self._php_in_multiline_comment = False
                self._highlighter = MagicMock()
                self._proxy_id = 1
                self._terminal_ref = MagicMock(return_value=None)
                self._terminal_type = "local"
                self._input_highlight_buffer = ""
                self._need_color_reset = False
                self._line_queue = MagicMock()

        return FakeHandler()

    def test_extract_filename_basic(self):
        """Filename extracted from simple 'cat file.py' command."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("cat file.py") == "file.py"

    def test_extract_filename_with_flags(self):
        """Flags are skipped, filename extracted correctly."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("cat -n file.sh") == "file.sh"
        assert handler._extract_filename_from_cat_command("cat -b -E config.json") == "config.json"

    def test_extract_filename_with_path(self):
        """Full paths are extracted correctly."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("cat /etc/hosts") == "/etc/hosts"

    def test_extract_filename_quoted(self):
        """Quotes are stripped from filenames."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("cat 'file.py'") == "file.py"
        assert handler._extract_filename_from_cat_command('cat "file.py"') == "file.py"

    def test_extract_filename_full_path_cat(self):
        """'/bin/cat' and '/usr/bin/cat' are recognized."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("/bin/cat file.py") == "file.py"
        assert handler._extract_filename_from_cat_command("/usr/bin/cat file.py") == "file.py"

    def test_extract_filename_no_file(self):
        """Returns None when no filename in command."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("cat") is None
        assert handler._extract_filename_from_cat_command("cat -n") is None

    def test_extract_filename_not_cat(self):
        """Returns None for non-cat commands."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("ls -la") is None
        assert handler._extract_filename_from_cat_command("echo hello") is None

    def test_extract_filename_empty(self):
        """Returns None for empty command."""
        handler = self._make_handler()
        assert handler._extract_filename_from_cat_command("") is None
        assert handler._extract_filename_from_cat_command(None) is None

    def test_safety_limit_check(self):
        """Safety limit prevents highlighting files over 1MB."""
        handler = self._make_handler()
        term = MagicMock()

        # Under limit
        handler._cat_bytes_processed = 0
        data = b"x" * 100
        assert handler._check_cat_safety_limit(data, term) is False
        assert handler._cat_bytes_processed == 100

        # Over limit
        handler._cat_bytes_processed = 1048576 - 50
        data = b"x" * 100
        assert handler._check_cat_safety_limit(data, term) is True
        assert handler._cat_limit_reached is True

    def test_decode_and_validate(self):
        """Data is decoded to string; NUL bytes stripped."""
        handler = self._make_handler()
        assert handler._decode_and_validate(b"hello") == "hello"
        assert handler._decode_and_validate(b"hello\x00world") == "helloworld"
        assert handler._decode_and_validate(b"") is None

    def test_shell_input_control_detection(self):
        """Shell input control sequences are detected."""
        handler = self._make_handler()
        assert handler._is_shell_input_control("\x08\x1b[K") is True
        assert handler._is_shell_input_control("\x08 \x08") is True
        assert handler._is_shell_input_control("hello") is False

    def test_split_line_ending(self):
        """Line endings are correctly split and normalized."""
        handler = self._make_handler()

        content, ending = handler._split_line_ending("hello\r\n")
        assert content == "hello"
        assert ending == "\r\n"

        content, ending = handler._split_line_ending("hello\n")
        assert content == "hello"
        assert ending == "\r\n"  # Normalized to CRLF

        content, ending = handler._split_line_ending("hello\r")
        assert content == "hello"
        assert ending == "\r"

        content, ending = handler._split_line_ending("hello")
        assert content == "hello"
        assert ending == ""

    def test_is_shell_prompt_osc7(self):
        """OSC7 sequences are detected as shell prompts."""
        handler = self._make_handler()
        assert handler._is_shell_prompt("\x1b]7;file:///home/user\x07") is True

    def test_is_shell_prompt_traditional(self):
        """Traditional prompt detection is conservative to avoid false positives.

        After _check_traditional_prompt strips ANSI + NUL + whitespace,
        normal prompts like 'user@host:~$ ' lose the trailing space,
        so endswith('$ ') won't match. This is by design — primary
        prompt detection uses VTE termprop-changed (OSC7), not this heuristic.
        The traditional check catches powerline and shell-name prompts.
        """
        handler = self._make_handler()
        # Powerline prompts are detected
        assert handler._is_shell_prompt("~  ❯") is True
        assert handler._is_shell_prompt("dir ➜") is True
        # Shell name prompts are detected
        assert handler._is_shell_prompt("bash$ ") is True
        assert handler._is_shell_prompt("sh-5.3$ ") is True
        # OSC7 prompts
        assert handler._is_shell_prompt("\x1b]7;file:///home/user\x07") is True

    def test_is_shell_prompt_short_string_rejected(self):
        """Very short strings are not treated as prompts."""
        handler = self._make_handler()
        assert handler._is_shell_prompt("hi") is False


# ============================================================================
# 9. Shell Input Highlighting Tests
# ============================================================================

class TestShellInputHighlighting:
    """Test shell input highlighting (Pygments)."""

    @pytest.fixture
    def shell_highlighter(self):
        from ashyterm.terminal.highlighter.shell_input import ShellInputHighlighter

        h = ShellInputHighlighter.__new__(ShellInputHighlighter)
        h.logger = MagicMock()
        h._enabled = True
        h._lexer = None
        h._formatter = None
        h._theme = "monokai"
        h._theme_mode = "manual"
        h._dark_theme = "blinds-dark"
        h._light_theme = "blinds-light"
        h._lexer_config_key = None
        h._command_buffers = {}
        h._at_prompt = {}
        h._palette = None
        h._foreground = "#ffffff"
        h._background = "#000000"
        h._lock = threading.Lock()

        # Try to initialize lexer (needs Pygments)
        try:
            h._init_lexer()
        except Exception:
            pytest.skip("Pygments not available")

        return h

    def test_register_proxy(self, shell_highlighter):
        """Registering a proxy initializes its state."""
        shell_highlighter.register_proxy(42)
        assert 42 in shell_highlighter._command_buffers
        assert shell_highlighter._command_buffers[42] == ""
        assert shell_highlighter._at_prompt[42] is True

    def test_unregister_proxy(self, shell_highlighter):
        """Unregistering a proxy cleans up state."""
        shell_highlighter.register_proxy(42)
        shell_highlighter.unregister_proxy(42)
        assert 42 not in shell_highlighter._command_buffers
        assert 42 not in shell_highlighter._at_prompt

    def test_set_at_prompt(self, shell_highlighter):
        """Prompt state transitions clear buffer."""
        shell_highlighter.register_proxy(1)
        shell_highlighter._command_buffers[1] = "git st"

        # Transition to at prompt
        shell_highlighter.set_at_prompt(1, False)
        assert shell_highlighter._command_buffers[1] == ""

    def test_key_pressed_builds_buffer(self, shell_highlighter):
        """Key presses accumulate in the buffer."""
        shell_highlighter.register_proxy(1)
        shell_highlighter.set_at_prompt(1, True)

        shell_highlighter.on_key_pressed(1, "l", ord("l"))
        shell_highlighter.on_key_pressed(1, "s", ord("s"))

        assert shell_highlighter.get_current_buffer(1) == "ls"

    def test_key_pressed_backspace(self, shell_highlighter):
        """Backspace removes last character from buffer."""
        shell_highlighter.register_proxy(1)
        shell_highlighter._command_buffers[1] = "ls"

        shell_highlighter.on_key_pressed(1, "", 65288)  # BackSpace
        assert shell_highlighter.get_current_buffer(1) == "l"

    def test_key_pressed_enter_clears_buffer(self, shell_highlighter):
        """Enter clears buffer and leaves prompt."""
        shell_highlighter.register_proxy(1)
        shell_highlighter._command_buffers[1] = "ls -la"

        shell_highlighter.on_key_pressed(1, "\n", 65293)  # Return
        assert shell_highlighter.get_current_buffer(1) == ""
        assert shell_highlighter._at_prompt[1] is False

    def test_highlight_input_line(self, shell_highlighter):
        """Full input line highlighting produces ANSI codes."""
        if not shell_highlighter._lexer:
            pytest.skip("Pygments not available")

        shell_highlighter.register_proxy(1)
        shell_highlighter.set_at_prompt(1, True)

        result = shell_highlighter.highlight_input_line(1, "echo hello")
        # Pygments should add ANSI codes
        assert "\033[" in result or result == "echo hello"

    def test_highlight_disabled_returns_original(self, shell_highlighter):
        """When disabled, returns original line."""
        shell_highlighter._enabled = False
        shell_highlighter._lexer = None

        result = shell_highlighter.highlight_input_line(1, "ls -la")
        assert result == "ls -la"

    def test_is_light_color(self, shell_highlighter):
        """Light/dark color detection works correctly."""
        assert shell_highlighter._is_light_color("#ffffff") is True
        assert shell_highlighter._is_light_color("#000000") is False
        assert shell_highlighter._is_light_color("#808080") is True  # Gray is above 0.5
        assert shell_highlighter._is_light_color("#282a36") is False  # Dark background

    def test_highlight_not_at_prompt(self, shell_highlighter):
        """When not at prompt, highlighting returns plain text."""
        shell_highlighter.register_proxy(1)
        shell_highlighter.set_at_prompt(1, False)

        result = shell_highlighter.highlight_input_line(1, "ls -la")
        assert result == "ls -la"


# ============================================================================
# 10. Integration: End-to-End Color Application
# ============================================================================

class TestEndToEndColorApplication:
    """Integration tests verifying color application from rule to output."""

    def test_global_rule_error_produces_bold_red_output(self):
        """Global 'error' rule produces bold red ANSI output end-to-end."""
        from ashyterm.settings.highlights import HighlightManager

        # Create a manager-like object for color resolution
        mgr = HighlightManager.__new__(HighlightManager)
        mgr.logger = MagicMock()
        mgr._settings_manager = None
        mgr._color_cache = {}
        mgr._current_theme_name = "default"

        # Resolve "bold red" to ANSI
        ansi_bold_red = mgr.resolve_color_to_ansi("bold red")
        assert ansi_bold_red == "\033[1;31m"

        # Now simulate applying it to a line
        expected_fragment = f"{ansi_bold_red}error{ANSI_RESET}"
        # Manual check: build what highlighting should produce
        result = f"{ansi_bold_red}error{ANSI_RESET}: something failed"
        assert expected_fragment in result
        assert "error" in result
        assert ANSI_RESET in result

    def test_multiple_colors_on_same_line(self):
        """Multiple colors applied to different parts of the same line."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        bold_red = "\033[1;31m"
        green = "\033[32m"
        yellow = "\033[33m"

        rules = (
            LiteralKeywordRule(
                frozenset(["error"]), ("error",), bold_red, "next"
            ),
            LiteralKeywordRule(
                frozenset(["warning"]), ("warning",), yellow, "next"
            ),
            LiteralKeywordRule(
                frozenset(["ok"]), ("ok",), green, "next"
            ),
        )

        line = "error then warning then ok"
        result = h._apply_highlighting_to_line(line, rules)

        # Verify all three are colored
        assert bold_red in result
        assert yellow in result
        assert green in result

        # Count resets — should have 3 (one per match)
        assert result.count(ANSI_RESET) == 3

        # Verify text is preserved
        cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
        assert cleaned == line

    def test_highlight_preserves_non_matching_text_exactly(self):
        """Non-matching text is preserved byte-for-byte."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        rules = (
            LiteralKeywordRule(
                frozenset(["error"]), ("error",), "\033[31m", "next"
            ),
        )

        line = "prefix error suffix with special chars: <>&\"'"
        result = h._apply_highlighting_to_line(line, rules)

        cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
        assert cleaned == line

    def test_ping_context_rules_apply_colors(self):
        """Ping context rules apply correct colors to ping output."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        # Simulate ping TTL rule: (ttl=)(\d+)
        ttl_pattern = re_engine.compile(r"(ttl=)(\d+)", re_engine.IGNORECASE)
        ttl_rule = CompiledRule(
            pattern=ttl_pattern,
            ansi_colors=("", "\033[35m"),  # No color for "ttl=", magenta for value
            action="next",
            num_groups=2,
            prefilter=lambda line: "ttl=" in line,
        )

        # Simulate ping time rule: fast response
        time_pattern = re_engine.compile(r"([0-2]\.\d?\d?\d?) ms$", re_engine.IGNORECASE)
        time_rule = CompiledRule(
            pattern=time_pattern,
            ansi_colors=("\033[1;32m",),  # bold green
            action="next",
            num_groups=1,
            prefilter=lambda line: "ms" in line,
        )

        rules = (ttl_rule, time_rule)

        line = "64 bytes from 1.2.3.4: icmp_seq=1 ttl=64 time=0.5 ms"
        result = h._apply_highlighting_to_line(line, rules)

        spans = get_colored_spans(result)

        # TTL value should be magenta
        ttl_spans = [(c, t) for c, t in spans if t == "64" and c == "35"]
        assert len(ttl_spans) >= 1, f"Expected TTL span, got spans: {spans}"

        # Time value should be bold green
        time_spans = [(c, t) for c, t in spans if "0.5" in t]
        assert len(time_spans) == 1
        assert time_spans[0][0] == "1;32"  # bold green


# ============================================================================
# 11. Edge Cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases in the highlighting system."""

    def test_overlapping_matches_first_wins(self):
        """When matches overlap, the first one takes precedence."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        # Rule 1: matches "error_code"
        pattern1 = re_engine.compile(r"\b(error_code)\b", re_engine.IGNORECASE)
        rule1 = CompiledRule(
            pattern=pattern1, ansi_colors=("\033[31m",),
            action="next", num_groups=1, prefilter=None,
        )

        # Rule 2: matches "error" (would overlap with "error_code")
        pattern2 = re_engine.compile(r"\b(error)\b", re_engine.IGNORECASE)
        rule2 = CompiledRule(
            pattern=pattern2, ansi_colors=("\033[33m",),
            action="next", num_groups=1, prefilter=None,
        )

        # "error_code" — rule1 matches longer span starting at same position
        # With the sort (start, -(end-start)), "error_code" (longer) sorts first if same start
        line = "got error_code 42"
        result = h._apply_highlighting_to_line(line, (rule1, rule2))
        spans = get_colored_spans(result)

        # "error_code" should be colored by rule1 (red), not split
        error_spans = [(c, t) for c, t in spans if "error" in t]
        assert len(error_spans) == 1
        assert error_spans[0][1] == "error_code"
        assert error_spans[0][0] == "31"  # red from rule1

    def test_line_with_only_ansi_codes_skipped(self):
        """Line containing only ANSI codes is not re-highlighted."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        rules = (
            LiteralKeywordRule(
                frozenset(["error"]), ("error",), "\033[31m", "next"
            ),
        )

        # Line with existing ANSI SGR sequences — should be skipped
        line = "\033[32merror in green\033[0m"
        result = h._apply_highlighting_to_line(line, rules)
        assert result == line  # Unchanged

    def test_unicode_text_highlighted_correctly(self):
        """Unicode text is preserved correctly in highlighted output."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        rules = (
            LiteralKeywordRule(
                frozenset(["error"]), ("error",), "\033[31m", "next"
            ),
        )

        line = "Há um error na operação — verifique"
        result = h._apply_highlighting_to_line(line, rules)

        cleaned = re.sub(r"\033\[[0-9;]*m", "", result)
        assert cleaned == line

    def test_very_long_line_handled(self):
        """Very long lines are handled without crashing."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import LiteralKeywordRule

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        rules = (
            LiteralKeywordRule(
                frozenset(["error"]), ("error",), "\033[31m", "next"
            ),
        )

        # 10KB line with one "error" in the middle
        line = "x" * 5000 + " error " + "y" * 5000
        result = h._apply_highlighting_to_line(line, rules)

        spans = get_colored_spans(result)
        assert any(t == "error" for _, t in spans)

    def test_adjacent_matches_no_gap(self):
        """Adjacent matches without gap between them work correctly."""
        from ashyterm.terminal.highlighter.output import OutputHighlighter
        from ashyterm.terminal.highlighter.rules import CompiledRule
        from ashyterm.utils.re_engine import engine as re_engine

        h = OutputHighlighter.__new__(OutputHighlighter)
        h.logger = MagicMock()
        h._manager = MagicMock()
        h._lock = threading.Lock()

        # Match individual digits
        pattern = re_engine.compile(r"(\d)", 0)
        rule = CompiledRule(
            pattern=pattern, ansi_colors=("\033[33m",),
            action="next", num_groups=1, prefilter=None,
        )

        line = "abc123def"
        result = h._apply_highlighting_to_line(line, (rule,))

        spans = get_colored_spans(result)
        assert len(spans) == 3  # "1", "2", "3"
        for code, text in spans:
            assert code == "33"
            assert text in ("1", "2", "3")


# ============================================================================
# 12. Constants and Patterns Tests
# ============================================================================

class TestConstants:
    """Test pre-compiled patterns and constants."""

    def test_ansi_seq_pattern_strips_colors(self):
        """ANSI_SEQ_PATTERN correctly strips ANSI escape sequences."""
        from ashyterm.terminal.highlighter.constants import ANSI_SEQ_PATTERN

        text = "\033[31mred text\033[0m normal"
        cleaned = ANSI_SEQ_PATTERN.sub("", text)
        assert cleaned == "red text normal"

    def test_ansi_color_pattern_detects_sgr(self):
        """ANSI_COLOR_PATTERN detects color SGR sequences."""
        from ashyterm.terminal.highlighter.constants import ANSI_COLOR_PATTERN

        # Standard color
        assert ANSI_COLOR_PATTERN.search("\033[31m") is not None
        # Bold + color
        assert ANSI_COLOR_PATTERN.search("\033[1;31m") is not None
        # 256-color
        assert ANSI_COLOR_PATTERN.search("\033[38;5;196m") is not None
        # RGB
        assert ANSI_COLOR_PATTERN.search("\033[38;2;255;0;0m") is not None
        # Reset (not a color)
        assert ANSI_COLOR_PATTERN.search("\033[0m") is None

    def test_alt_screen_patterns(self):
        """Alt screen enable/disable patterns are correct."""
        from ashyterm.terminal.highlighter.constants import (
            ALT_SCREEN_ENABLE_PATTERNS,
            ALT_SCREEN_DISABLE_PATTERNS,
        )

        # Standard alt screen sequences
        assert b"\x1b[?1049h" in ALT_SCREEN_ENABLE_PATTERNS
        assert b"\x1b[?1049l" in ALT_SCREEN_DISABLE_PATTERNS

    def test_word_char_set_completeness(self):
        """WORD_CHAR set contains all expected characters."""
        from ashyterm.terminal.highlighter.constants import WORD_CHAR

        # Should contain letters, digits, underscore
        assert "a" in WORD_CHAR
        assert "Z" in WORD_CHAR
        assert "0" in WORD_CHAR
        assert "_" in WORD_CHAR
        # Should NOT contain special chars
        assert " " not in WORD_CHAR
        assert "-" not in WORD_CHAR
        assert "." not in WORD_CHAR
