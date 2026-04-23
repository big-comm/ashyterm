"""Snapshot tests for bash syntax-highlight markup generation.

The fixture ``snapshots.json`` records the exact output of
``get_bash_pango_markup`` for a curated set of inputs. Any future change
in the highlighter — new patterns, reordering, palette changes — breaks
the matching snapshot, forcing a conscious review/regeneration step.

The Rust port's markup generator (whatever its output format) inherits
this table. Same inputs, frozen expected outputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ashyterm.utils.syntax_utils import get_bash_pango_markup


SNAPSHOT_FILE = Path(__file__).parent / "fixtures" / "bash_markup" / "snapshots.json"


def _load_snapshots():
    with SNAPSHOT_FILE.open() as f:
        return json.load(f)


@pytest.mark.parametrize(
    "case_name",
    sorted(_load_snapshots().keys()),
)
def test_bash_markup_matches_snapshot(case_name: str):
    case = _load_snapshots()[case_name]
    actual = get_bash_pango_markup(case["input"])
    expected = case["expected_markup"]
    assert actual == expected, (
        f"\nCase:     {case_name}\n"
        f"Input:    {case['input']!r}\n"
        f"Expected: {expected!r}\n"
        f"Actual:   {actual!r}"
    )


def test_snapshot_file_has_enough_cases():
    assert len(_load_snapshots()) >= 20


class TestMarkupInvariants:
    """Properties that must hold regardless of pattern changes."""

    def test_empty_input_empty_output(self):
        assert get_bash_pango_markup("") == ""

    def test_output_length_at_least_input_length(self):
        """Markup can only grow the string (wrapping spans around
        tokens). It never shrinks legitimate characters away."""
        for text in ["ls", "echo hello", "find / -name x", "a b c d e"]:
            out = get_bash_pango_markup(text)
            # Extract text content by stripping all <span...> and </span>.
            import re
            stripped = re.sub(r"<[^>]+>", "", out)
            # After GLib.markup_escape_text, &lt;/&gt;/&amp; may appear
            # in the output. For plain ASCII with no metachars, stripped
            # matches the input.
            if not any(ch in text for ch in "<>&"):
                assert stripped == text

    def test_known_command_gets_colored(self):
        """'ls' at the start should be wrapped in a span — colour of
        the 'command' role from the palette."""
        out = get_bash_pango_markup("ls /etc")
        assert "<span" in out
        assert ">ls</span>" in out

    def test_custom_palette_is_respected(self):
        """Explicit 16-color palette overrides defaults. Just prove the
        colour from the palette appears somewhere in the output."""
        palette = [
            "#000000", "#111111", "#2a2a2a", "#3b3b3b",
            "#444444", "#555555", "#666666", "#777777",
            "#888888", "#999999", "#aaaaaa", "#bbbbbb",
            "#cccccc", "#dddddd", "#eeeeee", "#ffffff",
        ]
        out = get_bash_pango_markup("ls", palette=palette)
        # palette[2] is the 'command' color.
        assert "#2a2a2a" in out

    def test_dangerous_pango_chars_are_escaped(self):
        """User input must not inject markup — literal < > & are escaped
        before pattern substitution kicks in."""
        out = get_bash_pango_markup("<script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;" in out or "&amp;lt;" in out
