"""Tests for stream_escapes (byte-level escape-sequence helpers)."""

import pytest

from ashyterm.terminal.stream_escapes import (
    AltScreenTransition,
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    apply_backspaces,
    contains_bracketed_paste_end,
    contains_bracketed_paste_start,
    count_backspaces,
    detect_alt_screen_transition,
)


# ── bracketed paste markers ────────────────────────────────


class TestBracketedPaste:
    def test_start_marker_detected(self):
        assert contains_bracketed_paste_start(b"\x1b[200~hello") is True

    def test_end_marker_detected(self):
        assert contains_bracketed_paste_end(b"world\x1b[201~") is True

    def test_absent_markers(self):
        assert contains_bracketed_paste_start(b"plain output") is False
        assert contains_bracketed_paste_end(b"plain output") is False

    def test_markers_are_the_canonical_bytes(self):
        assert BRACKETED_PASTE_START == b"\x1b[200~"
        assert BRACKETED_PASTE_END == b"\x1b[201~"


# ── detect_alt_screen_transition ───────────────────────────


class TestDetectAltScreenTransition:
    def test_enter_from_normal(self):
        assert (
            detect_alt_screen_transition(b"\x1b[?1049h", currently_alt=False)
            is AltScreenTransition.ENTERED
        )

    def test_legacy_47_enter(self):
        assert (
            detect_alt_screen_transition(b"\x1b[?47h", currently_alt=False)
            is AltScreenTransition.ENTERED
        )

    def test_1047_enter_matches(self):
        assert (
            detect_alt_screen_transition(b"\x1b[?1047h", currently_alt=False)
            is AltScreenTransition.ENTERED
        )

    def test_exit_when_in_alt(self):
        assert (
            detect_alt_screen_transition(b"\x1b[?1049l", currently_alt=True)
            is AltScreenTransition.EXITED
        )

    def test_no_change_when_enter_while_already_alt(self):
        # Already in alt screen; enable sequence is a no-op.
        assert (
            detect_alt_screen_transition(b"\x1b[?1049h", currently_alt=True)
            is AltScreenTransition.NO_CHANGE
        )

    def test_no_change_when_exit_while_not_alt(self):
        assert (
            detect_alt_screen_transition(b"\x1b[?1049l", currently_alt=False)
            is AltScreenTransition.NO_CHANGE
        )

    def test_plain_data_returns_no_change(self):
        assert (
            detect_alt_screen_transition(b"output\n", currently_alt=False)
            is AltScreenTransition.NO_CHANGE
        )

    def test_both_enable_and_disable_while_alt_yields_toggled_ended(self):
        # We enter alt briefly then exit in the same chunk: net effect
        # is "back to normal".
        data = b"\x1b[?1049h something \x1b[?1049l"
        assert (
            detect_alt_screen_transition(data, currently_alt=True)
            is AltScreenTransition.EXITED
        )

    def test_both_from_normal_yields_entered(self):
        # From normal mode, enable+disable in same chunk: caller should
        # treat as ENTERED (data between markers was alt-screen) since
        # we can't model sub-chunk transitions.
        data = b"\x1b[?1049h X \x1b[?1049l"
        out = detect_alt_screen_transition(data, currently_alt=False)
        # ``entered`` fires (currently_alt=False), ``exited`` does not
        # (currently_alt=False), so the result is ENTERED.
        assert out is AltScreenTransition.ENTERED


# ── count_backspaces ───────────────────────────────────────


class TestCountBackspaces:
    def test_empty_data_returns_zero(self):
        assert count_backspaces(b"") == 0

    def test_single_del_char(self):
        assert count_backspaces(b"\x7f") == 1

    def test_single_bs_char(self):
        assert count_backspaces(b"\x08") == 1

    def test_mix_of_bs_and_del(self):
        assert count_backspaces(b"\x08\x7f\x08") == 3

    def test_bs_space_bs_combo_counts_once(self):
        assert count_backspaces(b"\x08 \x08") == 1

    def test_two_combos_count_twice(self):
        assert count_backspaces(b"\x08 \x08\x08 \x08") == 2

    def test_combo_not_double_counted_with_raw_bytes(self):
        # The combo's inner bytes (one \x08) must not also be counted.
        assert count_backspaces(b"\x08 \x08") == 1

    def test_plain_text_yields_zero(self):
        assert count_backspaces(b"ls -la") == 0

    def test_mixed_combo_and_raw(self):
        # One combo (1) + one bare BS (1) + one DEL (1) = 3.
        assert count_backspaces(b"\x08 \x08\x08\x7f") == 3


# ── apply_backspaces ───────────────────────────────────────


class TestApplyBackspaces:
    def test_zero_count_is_passthrough(self):
        assert apply_backspaces("hello", 0) == "hello"

    def test_negative_count_is_passthrough(self):
        assert apply_backspaces("hello", -2) == "hello"

    def test_empty_buffer_is_passthrough(self):
        assert apply_backspaces("", 5) == ""

    def test_removes_last_chars(self):
        assert apply_backspaces("hello", 2) == "hel"

    def test_exact_length_empties_buffer(self):
        assert apply_backspaces("hi", 2) == ""

    def test_excess_is_clamped_to_buffer_length(self):
        # Shell emits more backspaces than we have: don't crash.
        assert apply_backspaces("hi", 100) == ""


# ── delegation ─────────────────────────────────────────────


class TestDelegation:
    def test_highlighter_proxy_still_exposes_helpers(self):
        from ashyterm.terminal._highlighter_impl import (
            HighlightedTerminalProxy,
        )

        for name in (
            "_update_alt_screen_state",
            "_handle_backspace_in_buffer",
        ):
            assert callable(getattr(HighlightedTerminalProxy, name))
