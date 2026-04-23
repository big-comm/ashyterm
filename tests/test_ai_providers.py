"""Tests for ai_providers (OpenAI-compat spec table + dispatcher)."""

from unittest.mock import MagicMock

import pytest

from ashyterm.terminal.ai_providers import (
    OpenAICompatProvider,
    build_local_headers,
    get_provider,
    perform_openai_compat,
    resolve_local_url,
)


# ── provider table ──────────────────────────────────────────


class TestProviderTable:
    @pytest.mark.parametrize(
        "key",
        ["groq", "openrouter", "cerebras", "github", "mistral"],
    )
    def test_every_supported_key_resolves(self, key):
        assert isinstance(get_provider(key), OpenAICompatProvider)
        assert get_provider(key).key == key

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            get_provider("unknown-service")

    def test_every_provider_has_url_and_default_model(self):
        for key in ("groq", "openrouter", "cerebras", "github", "mistral"):
            spec = get_provider(key)
            assert spec.url.startswith("https://")
            assert spec.default_model  # non-empty
            assert spec.display_name
            assert spec.missing_key_message


# ── perform_openai_compat ──────────────────────────────────


def _builder(messages):
    """Identity message builder for tests."""
    return messages


def _capture():
    """Pair of Mocks that record their call kwargs."""
    req = MagicMock(return_value="non-stream-ok")
    stream = MagicMock(return_value="stream-ok")
    return req, stream


class TestPerformOpenaiCompat:
    def test_missing_key_raises_provider_specific_message(self):
        req, stream = _capture()
        with pytest.raises(RuntimeError) as exc:
            perform_openai_compat(
                "groq",
                config={},  # no api_key
                messages=[],
                streaming=False,
                message_builder=_builder,
                request_fn=req,
                streaming_fn=stream,
            )
        # Error message comes from the provider spec; we don't assert
        # on translation, but the Groq name must be in the English base.
        assert "Groq" in str(exc.value) or "API key" in str(exc.value)
        req.assert_not_called()
        stream.assert_not_called()

    def test_streaming_flag_picks_streaming_fn(self):
        req, stream = _capture()
        out = perform_openai_compat(
            "openrouter",
            config={"api_key": "abc"},
            messages=[{"role": "user", "content": "hi"}],
            streaming=True,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        assert out == "stream-ok"
        req.assert_not_called()
        stream.assert_called_once()

    def test_non_streaming_picks_request_fn(self):
        req, stream = _capture()
        out = perform_openai_compat(
            "mistral",
            config={"api_key": "xyz"},
            messages=[],
            streaming=False,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        assert out == "non-stream-ok"
        stream.assert_not_called()
        req.assert_called_once()

    def test_default_model_used_when_config_omits_it(self):
        req, stream = _capture()
        perform_openai_compat(
            "groq",
            config={"api_key": "key"},
            messages=[],
            streaming=False,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        # Transport receives (url, headers, payload, display_name).
        payload = req.call_args[0][2]
        assert payload["model"] == get_provider("groq").default_model

    def test_config_model_overrides_default(self):
        req, stream = _capture()
        perform_openai_compat(
            "groq",
            config={"api_key": "k", "model": "llama-3.2-90b"},
            messages=[],
            streaming=False,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        payload = req.call_args[0][2]
        assert payload["model"] == "llama-3.2-90b"

    def test_bearer_header_contains_api_key(self):
        req, stream = _capture()
        perform_openai_compat(
            "cerebras",
            config={"api_key": "sk-abc"},
            messages=[],
            streaming=False,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        headers = req.call_args[0][1]
        assert headers["Authorization"] == "Bearer sk-abc"
        assert headers["Content-Type"] == "application/json"

    def test_url_matches_provider_spec(self):
        req, stream = _capture()
        perform_openai_compat(
            "github",
            config={"api_key": "ghp_abc"},
            messages=[],
            streaming=True,
            message_builder=_builder,
            request_fn=req,
            streaming_fn=stream,
        )
        url = stream.call_args[0][0]
        assert url == get_provider("github").url

    def test_message_builder_is_invoked_once(self):
        req, stream = _capture()
        captured: list = []

        def spy(msgs):
            captured.append(msgs)
            return msgs

        perform_openai_compat(
            "groq",
            config={"api_key": "k"},
            messages=[{"role": "user", "content": "hi"}],
            streaming=False,
            message_builder=spy,
            request_fn=req,
            streaming_fn=stream,
        )
        assert len(captured) == 1

    def test_whitespace_api_key_treated_as_missing(self):
        req, stream = _capture()
        with pytest.raises(RuntimeError):
            perform_openai_compat(
                "groq",
                config={"api_key": "   "},
                messages=[],
                streaming=False,
                message_builder=_builder,
                request_fn=req,
                streaming_fn=stream,
            )


# ── local-AI helpers ───────────────────────────────────────


class TestResolveLocalUrl:
    def test_ollama_default_gets_v1_suffix(self):
        assert (
            resolve_local_url({"local_base_url": "http://localhost:11434"})
            == "http://localhost:11434/v1/chat/completions"
        )

    def test_base_url_already_has_v1(self):
        assert (
            resolve_local_url({"local_base_url": "http://localhost:11434/v1"})
            == "http://localhost:11434/v1/chat/completions"
        )

    def test_trailing_slashes_are_stripped(self):
        assert (
            resolve_local_url({"local_base_url": "http://localhost:8080/v1/"})
            == "http://localhost:8080/v1/chat/completions"
        )

    def test_empty_config_falls_back_to_default(self):
        out = resolve_local_url({})
        assert out == "http://localhost:11434/v1/chat/completions"


class TestBuildLocalHeaders:
    def test_no_api_key_emits_only_content_type(self):
        headers = build_local_headers({})
        assert headers == {"Content-Type": "application/json"}

    def test_api_key_adds_bearer(self):
        headers = build_local_headers({"api_key": "local-key"})
        assert headers["Authorization"] == "Bearer local-key"
        assert headers["Content-Type"] == "application/json"

    def test_whitespace_api_key_is_ignored(self):
        # Matches the same convention as the hosted dispatcher — spaces
        # alone don't count as a key.
        headers = build_local_headers({"api_key": "   "})
        assert "Authorization" not in headers


# ── assistant integration ──────────────────────────────────


class TestAssistantDelegation:
    def test_assistant_has_compat_provider_method(self):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        assert callable(TerminalAiAssistant._perform_openai_compat_provider)

    @pytest.mark.parametrize(
        "method",
        [
            "_perform_groq_request",
            "_perform_groq_streaming_request",
            "_perform_openrouter_request",
            "_perform_openrouter_streaming_request",
            "_perform_cerebras_request",
            "_perform_cerebras_streaming_request",
            "_perform_github_request",
            "_perform_github_streaming_request",
            "_perform_mistral_request",
            "_perform_mistral_streaming_request",
            "_perform_local_request",
            "_perform_local_streaming_request",
        ],
    )
    def test_legacy_method_names_still_exist(self, method):
        from ashyterm.terminal.ai_assistant import TerminalAiAssistant

        assert callable(getattr(TerminalAiAssistant, method))
