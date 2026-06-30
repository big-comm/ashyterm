"""Tests for stream_buffering (pure buffering rules for the streamer)."""

import pytest

from ashyterm.terminal.stream_buffering import (
    BURST_CHUNK_MIN,
    BURST_HARD_HIT_SEED,
    HARD_BYTES_LIMIT,
    classify_burst,
    is_remainder_interactive,
    should_skip_line_highlight,
    split_partial_line,
)


# ── classify_burst ──────────────────────────────────────────


class TestClassifyBurst:
    def test_hard_limit_pins_counter_and_trips_burst(self):
        counter, is_burst = classify_burst(
            HARD_BYTES_LIMIT + 1, burst_counter=0, threshold=15
        )
        assert is_burst is True
        assert counter == BURST_HARD_HIT_SEED

    def test_small_chunk_resets_counter(self):
        counter, is_burst = classify_burst(
            BURST_CHUNK_MIN - 1, burst_counter=8, threshold=15
        )
        assert is_burst is False
        assert counter == 0

    def test_small_chunk_resets_even_when_previously_tripped(self):
        # Counter was already over threshold; small chunk should still
        # reset it so the next big chunk has to re-earn the burst.
        counter, is_burst = classify_burst(
            128, burst_counter=20, threshold=15
        )
        assert is_burst is False
        assert counter == 0

    def test_medium_chunks_increment_counter(self):
        counter = 0
        for _ in range(5):
            counter, is_burst = classify_burst(
                BURST_CHUNK_MIN + 1, burst_counter=counter, threshold=15
            )
            assert is_burst is False
        assert counter == 5

    def test_threshold_boundary_is_strict_greater_than(self):
        # counter==threshold after increment ⇒ NOT a burst yet.
        counter, is_burst = classify_burst(
            BURST_CHUNK_MIN + 1, burst_counter=14, threshold=15
        )
        assert counter == 15
        assert is_burst is False

    def test_threshold_exceeded_trips_burst(self):
        counter, is_burst = classify_burst(
            BURST_CHUNK_MIN + 1, burst_counter=15, threshold=15
        )
        assert counter == 16
        assert is_burst is True


# ── is_remainder_interactive ───────────────────────────────


class TestIsRemainderInteractive:
    @pytest.mark.parametrize(
        "text",
        [
            "user@host $",
            "root@host #",
            "test %",
            ">>",
            "prefix:",
            "Do you want to continue? [Y/n]",
            "Are you sure you want to continue connecting (yes/no/[fingerprint])?",
            "Deseja continuar? [S/n]",
            "Overwrite [y/N]",
        ],
    )
    def test_trailing_prompt_chars_count_as_interactive(self, text):
        assert is_remainder_interactive(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "user@host $ typing",
            "root@host # half-written",
            "demo % mid-line",
            "[sub]> ",
        ],
    )
    def test_prompt_with_space_counts_as_interactive(self, text):
        assert is_remainder_interactive(text) is True

    def test_ansi_escape_marks_interactive(self):
        assert is_remainder_interactive("\x1b[31mfoo") is True

    def test_osc7_escape_marks_interactive(self):
        assert is_remainder_interactive("\x1b]7;file:///home") is True

    @pytest.mark.parametrize(
        "text",
        [
            "just some output",
            "",
            "123",
            "   ",
            "line ending with no",
            "single letter n",
        ],
    )
    def test_plain_strings_are_not_interactive(self, text):
        assert is_remainder_interactive(text) is False


# ── split_partial_line ─────────────────────────────────────


class TestSplitPartialLine:
    def test_no_newline_returns_whole_chunk_no_buffer(self):
        emit, remainder = split_partial_line(
            b"partial line without newline", at_shell_prompt=False
        )
        assert emit == b"partial line without newline"
        assert remainder == b""

    def test_newline_at_end_no_split(self):
        emit, remainder = split_partial_line(
            b"complete line\n", at_shell_prompt=False
        )
        assert emit == b"complete line\n"
        assert remainder == b""

    def test_mid_chunk_newline_splits_trailing_partial(self):
        emit, remainder = split_partial_line(
            b"line one\nline two partial", at_shell_prompt=False
        )
        assert emit == b"line one\n"
        assert remainder == b"line two partial"

    def test_at_shell_prompt_inhibits_buffering(self):
        # The user is typing; buffering would swallow keystrokes.
        emit, remainder = split_partial_line(
            b"line one\nline two partial", at_shell_prompt=True
        )
        assert emit == b"line one\nline two partial"
        assert remainder == b""

    def test_interactive_remainder_inhibits_buffering(self):
        # Remainder looks like a prompt → forward it so the user sees
        # the prompt draw.
        emit, remainder = split_partial_line(
            b"earlier line\nuser@host $ ", at_shell_prompt=False
        )
        assert emit == b"earlier line\nuser@host $ "
        assert remainder == b""

    @pytest.mark.parametrize(
        "data",
        [
            (
                b"After this operation, 5554 kB of additional disk space will be "
                b"used.\nDo you want to continue? [Y/n]"
            ),
            (
                b"The authenticity of host 'demo' can't be established.\n"
                b"Are you sure you want to continue connecting "
                b"(yes/no/[fingerprint])? "
            ),
        ],
    )
    def test_confirmation_prompt_remainder_inhibits_buffering(self, data):
        emit, remainder = split_partial_line(data, at_shell_prompt=False)
        assert emit == data
        assert remainder == b""

    def test_long_remainder_skips_interactive_check(self):
        # Remainder longer than REMAINDER_DECODE_LIMIT → we don't
        # decode it, so the interactive check returns False and the
        # remainder gets buffered normally.
        big_partial = b"x" * 500
        emit, remainder = split_partial_line(
            b"earlier line\n" + big_partial, at_shell_prompt=False
        )
        assert emit == b"earlier line\n"
        assert remainder == big_partial


# ── should_skip_line_highlight ─────────────────────────────


class TestShouldSkipLineHighlight:
    def test_skip_first_is_honored_only_on_index_zero(self):
        assert should_skip_line_highlight("hi\n", index=0, skip_first=True) is True
        assert should_skip_line_highlight("hi\n", index=1, skip_first=True) is False

    def test_empty_or_whitespace_only_lines_skip(self):
        assert should_skip_line_highlight("", index=1, skip_first=False) is True
        assert should_skip_line_highlight("\n", index=1, skip_first=False) is True
        assert should_skip_line_highlight("\r", index=1, skip_first=False) is True
        assert should_skip_line_highlight("\r\n", index=1, skip_first=False) is True

    def test_osc7_line_skips(self):
        osc7 = "\x1b]7;file:///tmp\x1b\\"
        assert should_skip_line_highlight(osc7, index=2, skip_first=False) is True

    def test_plain_line_is_highlighted(self):
        assert (
            should_skip_line_highlight("ls -la\n", index=2, skip_first=False)
            is False
        )


# ── handler delegation ────────────────────────────────────


class TestHandlerDelegation:
    def test_handler_delegators_exist(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        for name in (
            "_handle_streaming_safety_limits",
            "_handle_streaming_partial_lines",
            "_is_remainder_interactive",
            "_process_streaming_line",
        ):
            assert callable(getattr(StreamingHandler, name))
