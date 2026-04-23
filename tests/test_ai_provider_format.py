"""Tests for ai_provider_format (per-provider request/response translation)."""

import pytest

from ashyterm.terminal.ai_provider_format import (
    build_gemini_conversation,
    build_openai_messages,
    extract_gemini_content,
    extract_openai_content,
    parse_sse_line,
)


# ── build_gemini_conversation ────────────────────────────────


class TestBuildGeminiConversation:
    def test_system_is_hoisted_out_of_contents(self):
        sys, contents = build_gemini_conversation(
            [
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ]
        )
        assert sys == "Be helpful"
        assert contents == [{"role": "user", "parts": [{"text": "Hello"}]}]

    def test_only_first_system_message_becomes_instruction(self):
        sys, contents = build_gemini_conversation(
            [
                {"role": "system", "content": "First"},
                {"role": "system", "content": "Second"},
                {"role": "user", "content": "Hi"},
            ]
        )
        assert sys == "First"
        # Gemini has exactly one system_instruction slot; subsequent
        # system turns fall through and are emitted as user turns so
        # their content isn't silently lost.
        assert [c["role"] for c in contents] == ["user", "user"]
        assert contents[0]["parts"][0]["text"] == "Second"
        assert contents[1]["parts"][0]["text"] == "Hi"

    def test_assistant_role_maps_to_model(self):
        _, contents = build_gemini_conversation(
            [
                {"role": "user", "content": "Q"},
                {"role": "assistant", "content": "A"},
            ]
        )
        roles = [c["role"] for c in contents]
        assert roles == ["user", "model"]

    def test_empty_messages_yield_placeholder_user_turn(self):
        sys, contents = build_gemini_conversation([])
        assert sys == ""
        assert contents == [{"role": "user", "parts": [{"text": ""}]}]

    def test_empty_content_turns_are_skipped(self):
        _, contents = build_gemini_conversation(
            [
                {"role": "user", "content": ""},
                {"role": "user", "content": "real"},
                {"role": "user", "content": ""},
            ]
        )
        assert len(contents) == 1
        assert contents[0]["parts"][0]["text"] == "real"

    def test_unknown_role_defaults_to_user(self):
        _, contents = build_gemini_conversation(
            [{"role": "tool", "content": "payload"}]
        )
        assert contents[0]["role"] == "user"


# ── build_openai_messages ────────────────────────────────────


class TestBuildOpenaiMessages:
    def test_known_roles_survive(self):
        out = build_openai_messages(
            [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "U"},
                {"role": "assistant", "content": "A"},
            ]
        )
        assert [m["role"] for m in out] == ["system", "user", "assistant"]
        assert [m["content"] for m in out] == ["S", "U", "A"]

    def test_unknown_roles_clamped_to_user(self):
        out = build_openai_messages([{"role": "tool", "content": "x"}])
        assert out == [{"role": "user", "content": "x"}]

    def test_non_string_content_dropped(self):
        out = build_openai_messages(
            [
                {"role": "user", "content": None},
                {"role": "user", "content": 42},
                {"role": "user", "content": "keep"},
            ]
        )
        assert out == [{"role": "user", "content": "keep"}]

    def test_empty_strings_dropped(self):
        out = build_openai_messages([{"role": "user", "content": ""}])
        assert out == []


# ── extract_openai_content ───────────────────────────────────


class TestExtractOpenaiContent:
    def test_plain_content_string(self):
        data = {"choices": [{"message": {"content": "  hello  "}}]}
        assert extract_openai_content(data, "P") == "hello"

    def test_multi_part_content_list(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "line 1"},
                            {"type": "text", "text": "line 2"},
                        ]
                    }
                }
            ]
        }
        assert extract_openai_content(data, "P") == "line 1\nline 2"

    def test_no_choices_raises(self):
        with pytest.raises(RuntimeError, match="did not contain"):
            extract_openai_content({"choices": []}, "P")
        with pytest.raises(RuntimeError):
            extract_openai_content({}, "P")

    def test_empty_content_raises_with_provider_name(self):
        data = {"choices": [{"message": {"content": "   "}}]}
        with pytest.raises(RuntimeError, match="OpenAI"):
            extract_openai_content(data, "OpenAI")

    def test_non_string_non_list_content_raises(self):
        data = {"choices": [{"message": {"content": 42}}]}
        with pytest.raises(RuntimeError):
            extract_openai_content(data, "P")


# ── extract_gemini_content ───────────────────────────────────


class TestExtractGeminiContent:
    def test_single_candidate_single_part(self):
        data = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        assert extract_gemini_content(data) == "hi"

    def test_multi_parts_joined_with_newline(self):
        data = {
            "candidates": [
                {"content": {"parts": [{"text": "a"}, {"text": "b"}]}}
            ]
        }
        assert extract_gemini_content(data) == "a\nb"

    def test_no_candidates_raises(self):
        with pytest.raises(RuntimeError, match="did not contain"):
            extract_gemini_content({"candidates": []})

    def test_empty_parts_still_raises(self):
        data = {"candidates": [{"content": {"parts": []}}]}
        with pytest.raises(RuntimeError):
            extract_gemini_content(data)

    def test_parts_without_text_are_skipped(self):
        data = {
            "candidates": [
                {"content": {"parts": [{"inline_data": "x"}, {"text": "keep"}]}}
            ]
        }
        assert extract_gemini_content(data) == "keep"


# ── parse_sse_line ───────────────────────────────────────────


class TestParseSseLine:
    def test_non_data_line_is_ignored(self):
        assert parse_sse_line("event: ping") == (None, False)
        assert parse_sse_line(": keepalive") == (None, False)
        assert parse_sse_line("") == (None, False)

    def test_done_sentinel(self):
        assert parse_sse_line("data: [DONE]") == ("", True)

    def test_plain_delta(self):
        assert parse_sse_line(
            'data: {"choices": [{"delta": {"content": "hello"}}]}'
        ) == ("hello", False)

    def test_delta_with_null_content(self):
        assert parse_sse_line(
            'data: {"choices": [{"delta": {"content": null}}]}'
        ) == ("", False)

    def test_malformed_json_returns_no_chunk(self):
        assert parse_sse_line("data: not-json") == (None, False)

    def test_error_object_raises(self):
        with pytest.raises(RuntimeError, match="rate limited"):
            parse_sse_line('data: {"error": {"message": "rate limited"}}')

    def test_error_without_message_uses_fallback(self):
        with pytest.raises(RuntimeError, match="API Error"):
            parse_sse_line('data: {"error": {}}')


# ── assistant class delegation ───────────────────────────────


class TestAssistantDelegation:
    def test_delegators_exist(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        for name in (
            "_build_gemini_conversation",
            "_build_openai_messages",
            "_extract_openai_content",
            "_extract_gemini_content",
            "_parse_sse_line",
            "_parse_assistant_payload",
        ):
            assert callable(getattr(TerminalAiAssistant, name))

    def test_build_openai_delegates(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        inst = TerminalAiAssistant.__new__(TerminalAiAssistant)
        assert inst._build_openai_messages(
            [{"role": "user", "content": "hi"}]
        ) == [{"role": "user", "content": "hi"}]

    def test_parse_sse_line_delegates(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        inst = TerminalAiAssistant.__new__(TerminalAiAssistant)
        assert inst._parse_sse_line("data: [DONE]") == ("", True)
