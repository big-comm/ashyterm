"""Tests for ai_response_parser."""


from ashyterm.terminal.ai_response_parser import (
    clean_response,
    extract_json_object,
    normalize_commands,
    parse_assistant_payload,
)


# ── clean_response ───────────────────────────────────────────


class TestCleanResponse:
    def test_strips_bare_fence(self):
        text = "```\n{\"x\": 1}\n```"
        assert clean_response(text) == '{"x": 1}'

    def test_strips_language_fence(self):
        text = '```json\n{"reply": "hi"}\n```'
        assert clean_response(text) == '{"reply": "hi"}'

    def test_no_fence_is_passthrough_after_trim(self):
        assert clean_response('  {"x": 1}  ') == '{"x": 1}'

    def test_inner_fences_are_preserved(self):
        """An inner code fence inside the JSON reply must survive — only
        the outermost wrap is removed.
        """
        raw = '```json\n{"reply": "run ```ls``` on disk"}\n```'
        out = clean_response(raw)
        assert out.startswith('{"reply":')
        assert "```ls```" in out

    def test_empty_input(self):
        assert clean_response("") == ""


# ── extract_json_object ──────────────────────────────────────


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_tolerates_surrounding_prose(self):
        raw = 'Here is the answer: {"reply": "ok", "commands": []} Done.'
        assert extract_json_object(raw) == {"reply": "ok", "commands": []}

    def test_handles_nested_objects(self):
        raw = '{"a": {"b": {"c": 1}}, "d": 2}'
        assert extract_json_object(raw) == {"a": {"b": {"c": 1}}, "d": 2}

    def test_returns_first_valid_object_even_after_garbage(self):
        raw = "junk {not-json} then {\"x\": 1} trailing"
        # The first "{not-json}" fails to parse, so the walker falls
        # through to the next opening brace and returns the valid one.
        assert extract_json_object(raw) == {"x": 1}

    def test_no_json_returns_none(self):
        assert extract_json_object("no braces here") is None
        assert extract_json_object("") is None

    def test_unbalanced_returns_none(self):
        assert extract_json_object("{open but never closes") is None


# ── normalize_commands ───────────────────────────────────────


class TestNormalizeCommands:
    def test_list_of_strings_produces_command_dicts(self):
        out = normalize_commands(["ls", "  pwd  "])
        assert out == [
            {"command": "ls", "description": ""},
            {"command": "pwd", "description": ""},
        ]

    def test_list_of_dicts_preserves_description(self):
        out = normalize_commands(
            [{"command": "ls", "description": "list files"}]
        )
        assert out == [{"command": "ls", "description": "list files"}]

    def test_accepts_cmd_alias_for_command(self):
        out = normalize_commands([{"cmd": "top"}])
        assert out == [{"command": "top", "description": ""}]

    def test_drops_entries_without_command(self):
        out = normalize_commands([{"description": "no command"}, {"cmd": "ls"}])
        assert out == [{"command": "ls", "description": ""}]

    def test_drops_empty_strings(self):
        out = normalize_commands(["", "   ", "ls"])
        assert out == [{"command": "ls", "description": ""}]

    def test_string_input_becomes_single_command(self):
        assert normalize_commands("uptime") == [
            {"command": "uptime", "description": ""}
        ]

    def test_empty_string_yields_empty_list(self):
        assert normalize_commands("") == []
        assert normalize_commands("   ") == []

    def test_none_yields_empty_list(self):
        assert normalize_commands(None) == []
        assert normalize_commands(123) == []

    def test_non_string_description_is_coerced_to_empty(self):
        out = normalize_commands([{"command": "ls", "description": 42}])
        assert out == [{"command": "ls", "description": ""}]


# ── parse_assistant_payload (happy path: JSON) ───────────────


class TestParseAssistantPayloadJson:
    def test_clean_json_reply_and_commands(self):
        raw = '{"reply": "Here you go", "commands": ["ls -la", "pwd"]}'
        reply, commands, snippets = parse_assistant_payload(raw)
        assert reply == "Here you go"
        assert commands == [
            {"command": "ls -la", "description": ""},
            {"command": "pwd", "description": ""},
        ]
        assert snippets == []

    def test_fenced_json_is_unwrapped(self):
        raw = '```json\n{"reply": "ok", "commands": ["uptime"]}\n```'
        reply, commands, _ = parse_assistant_payload(raw)
        assert reply == "ok"
        assert commands == [{"command": "uptime", "description": ""}]

    def test_prose_wrapping_json_still_parses(self):
        raw = (
            "Sure, here is the answer:\n"
            '{"reply": "done", "commands": ["ls"]} -- hope that helps!'
        )
        reply, commands, _ = parse_assistant_payload(raw)
        assert reply == "done"
        assert commands == [{"command": "ls", "description": ""}]

    def test_json_missing_commands_yields_empty_list(self):
        raw = '{"reply": "just talk"}'
        reply, commands, _ = parse_assistant_payload(raw)
        assert reply == "just talk"
        assert commands == []


# ── parse_assistant_payload (fallback: prose + code fences) ──


class TestParseAssistantPayloadProse:
    def test_prose_without_json_falls_back_to_code_fences(self):
        raw = (
            "You can check uptime with:\n"
            "```bash\n"
            "uptime\n"
            "```\n"
            "That's it."
        )
        reply, commands, _ = parse_assistant_payload(raw)
        # Reply preserves the full formatted prose.
        assert "You can check uptime" in reply
        # Single-line fenced snippets become suggested commands.
        assert commands == [
            {"command": "uptime", "description": "Suggested command"}
        ]

    def test_multiline_scripts_are_kept_in_reply_not_surfaced(self):
        raw = (
            "Here's a small script:\n"
            "```bash\n"
            "#!/bin/bash\n"
            "echo hi\n"
            "echo bye\n"
            "```\n"
        )
        reply, commands, _ = parse_assistant_payload(raw)
        # Multi-line fences are skipped on purpose — they shouldn't
        # appear as single-click command buttons.
        assert commands == []
        assert "echo hi" in reply  # still visible to the user in the reply

    def test_untagged_code_fence_is_also_picked_up(self):
        raw = "Try this:\n```\nwhoami\n```\n"
        reply, commands, _ = parse_assistant_payload(raw)
        assert commands == [
            {"command": "whoami", "description": "Suggested command"}
        ]

    def test_no_json_and_no_code_fences_yields_empty_commands(self):
        raw = "Just some advice: always back up your data."
        reply, commands, _ = parse_assistant_payload(raw)
        assert reply == raw
        assert commands == []


# ── assistant class delegation ───────────────────────────────


class TestAssistantDelegation:
    def test_assistant_clean_response_delegates(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        assert (
            TerminalAiAssistant._clean_response(
                TerminalAiAssistant.__new__(TerminalAiAssistant), "```json\nx\n```"
            )
            == "x"
        )

    def test_assistant_extract_json_delegates(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        inst = TerminalAiAssistant.__new__(TerminalAiAssistant)
        assert inst._extract_json_object('{"x": 1}') == {"x": 1}

    def test_assistant_normalize_commands_delegates(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        inst = TerminalAiAssistant.__new__(TerminalAiAssistant)
        out = inst._normalize_commands(["ls"])
        assert out == [{"command": "ls", "description": ""}]
