"""Tests for shell_prompt_detect (pure detection helpers for streaming)."""

import pytest

from ashyterm.terminal.shell_prompt_detect import (
    PROMPT_TRIGGER_CHARS,
    READLINE_SEQUENCES,
    SEARCH_PROMPT_PATTERNS,
    TRADITIONAL_PROMPT_CHARS,
    extract_last_line,
    extract_line_content_and_ending,
    is_readline_redraw,
    is_valid_shell_input,
    is_valid_traditional_prompt,
    looks_like_prompt,
)


# ── exported constants ──────────────────────────────────────


class TestConstants:
    def test_trigger_chars_cover_standard_sigils(self):
        # ``$#%>❯`` is the early-exit set that skips expensive prompt
        # detection when the text doesn't contain any plausible
        # terminator. Every traditional ending char must be there.
        for c in TRADITIONAL_PROMPT_CHARS:
            assert c in PROMPT_TRIGGER_CHARS
        assert "❯" in PROMPT_TRIGGER_CHARS  # Starship default

    def test_readline_sequences_contains_cursor_and_erase(self):
        assert b"\x1b[K" in READLINE_SEQUENCES
        assert b"\x1b[D" in READLINE_SEQUENCES
        assert b"\x1b[?25l" in READLINE_SEQUENCES

    def test_search_patterns_cover_reverse_and_forward(self):
        for pat in (b"(reverse-i-search)", b"(fwd-i-search)"):
            assert pat in SEARCH_PROMPT_PATTERNS


# ── is_readline_redraw ──────────────────────────────────────


class TestIsReadlineRedraw:
    def test_carriage_return_alone_triggers(self):
        assert is_readline_redraw(b"\rfoo") is True

    def test_erase_sequences_trigger(self):
        assert is_readline_redraw(b"prefix\x1b[K tail") is True

    def test_cursor_move_sequences_trigger(self):
        assert is_readline_redraw(b"x\x1b[Dy") is True

    def test_incremental_search_triggers(self):
        assert is_readline_redraw(b"(reverse-i-search)`ls'") is True

    def test_plain_text_does_not_trigger(self):
        assert is_readline_redraw(b"just plain output\n") is False

    def test_empty_bytes_does_not_trigger(self):
        assert is_readline_redraw(b"") is False


# ── looks_like_prompt ───────────────────────────────────────


class TestLooksLikePrompt:
    @pytest.mark.parametrize(
        "text",
        [
            "user@host",     # user@host fragment
            "user:path",     # user:path fragment
            "/home/user/",   # path ending in /
            "~",             # tilde
            "bash-5.2",      # shell name
            "sh-4.4",
        ],
    )
    def test_accepts_prompt_like_strings(self, text):
        assert looks_like_prompt(text) is True

    def test_rejects_plain_output(self):
        assert looks_like_prompt("regular output") is False

    def test_rejects_empty(self):
        assert looks_like_prompt("") is False


# ── is_valid_traditional_prompt ─────────────────────────────


class TestIsValidTraditionalPrompt:
    def test_tilde_ending(self):
        assert is_valid_traditional_prompt("/home/user~") is True
        assert is_valid_traditional_prompt("~") is True

    def test_slash_ending(self):
        assert is_valid_traditional_prompt("/var/log/") is True

    def test_user_at_host(self):
        assert is_valid_traditional_prompt("alice@prod") is True

    def test_user_colon_path(self):
        assert is_valid_traditional_prompt("alice:/srv") is True

    def test_shell_name_pattern(self):
        assert is_valid_traditional_prompt("bash-5.2") is True

    def test_bare_word_rejected(self):
        # Otherwise a '#' inside prose comments would be read as a prompt.
        assert is_valid_traditional_prompt("comment") is False


# ── extract_line_content_and_ending ─────────────────────────


class TestExtractLineContentAndEnding:
    def test_empty_line(self):
        assert extract_line_content_and_ending("") == ("", "")

    def test_lf(self):
        assert extract_line_content_and_ending("foo\n") == ("foo", "\n")

    def test_crlf(self):
        assert extract_line_content_and_ending("foo\r\n") == ("foo", "\r\n")

    def test_bare_cr(self):
        assert extract_line_content_and_ending("foo\r") == ("foo", "\r")

    def test_no_terminator(self):
        assert extract_line_content_and_ending("foo") == ("foo", "")

    def test_lf_only_single_char(self):
        assert extract_line_content_and_ending("\n") == ("", "\n")

    def test_cr_only_single_char(self):
        assert extract_line_content_and_ending("\r") == ("", "\r")


# ── extract_last_line ───────────────────────────────────────


class TestExtractLastLine:
    def test_single_line_trimmed(self):
        assert extract_last_line("  hello  ") == "hello"

    def test_picks_last_newline_line(self):
        assert extract_last_line("first\nsecond\nthird") == "third"

    def test_cr_also_breaks(self):
        # Progress-bar style: "Downloading...\rFinalizing" — we want
        # Finalizing to be the "last line" as far as prompt detection
        # goes.
        assert extract_last_line("Downloading...\rFinalizing") == "Finalizing"

    def test_mixed_cr_and_lf(self):
        assert extract_last_line("prefix\ntrailing\rfinal") == "final"

    def test_empty_input(self):
        assert extract_last_line("") == ""


# ── is_valid_shell_input ────────────────────────────────────


class TestIsValidShellInput:
    def test_plain_command(self):
        assert is_valid_shell_input("ls -la") is True

    def test_rejects_non_printables(self):
        assert is_valid_shell_input("ls\x07") is False  # bell
        assert is_valid_shell_input("\tcd") is False

    def test_rejects_literal_escape(self):
        assert is_valid_shell_input("ls ^[OD") is False

    def test_rejects_echoed_arrow_keys(self):
        for seq in ("[A", "[B", "[C", "[D", "[H", "[F"):
            assert is_valid_shell_input(seq) is False

    def test_rejects_continuation_prompt(self):
        assert is_valid_shell_input(">") is False
        assert is_valid_shell_input("> ") is False

    def test_space_is_printable_enough(self):
        assert is_valid_shell_input("echo hi") is True

    def test_empty_string_is_valid(self):
        # Empty strings don't hit any reject branch — consistent with
        # pre-extraction behavior.
        assert is_valid_shell_input("") is True


# ── streaming_handler delegation ────────────────────────────


class TestStreamingHandlerDelegation:
    def test_delegators_exist(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        for name in (
            "_is_readline_redraw",
            "_looks_like_prompt",
            "_is_valid_traditional_prompt",
            "_extract_line_content_and_ending",
            "_extract_last_line",
            "_is_valid_shell_input",
        ):
            assert callable(getattr(StreamingHandler, name))

    def test_readline_sequences_class_attr_matches_module(self):
        from ashyterm.terminal._streaming_handler import StreamingHandler

        assert StreamingHandler._READLINE_SEQUENCES is READLINE_SEQUENCES
        assert StreamingHandler._SEARCH_PROMPT_PATTERNS is SEARCH_PROMPT_PATTERNS
