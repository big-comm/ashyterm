# ashyterm/terminal/ai_providers.py
"""Spec-table + dispatcher for OpenAI-compatible AI providers.

All providers except Gemini share an identical request shape: a
``Bearer`` token in the ``Authorization`` header, a
``{model, messages}`` JSON body, and a POST to a chat-completions
URL. The ten ``_perform_*_request`` methods that used to live on
``TerminalAiAssistant`` differed only in the URL, model default, and
error message.

This module captures that shape as :class:`OpenAICompatProvider` and
exposes :func:`perform_openai_compat` which the assistant calls once
with the provider key. The local-AI provider gets its own helper
because its URL depends on ``local_base_url`` from user config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from ..utils.translation_utils import _


# Maps the user's configured ``ai_assistant_provider`` string to the
# concrete request spec. Kept in this dict-of-dataclasses shape
# instead of an enum so tests can synthesize ad-hoc providers without
# subclassing.
@dataclass(frozen=True)
class OpenAICompatProvider:
    """Request spec for an OpenAI-compatible chat endpoint."""

    key: str  # matches the settings string ("groq", "openrouter", …)
    display_name: str  # user-facing error messages + telemetry
    url: str  # chat-completions endpoint
    default_model: str  # model used when config has no override
    missing_key_message: str  # shown via RuntimeError when key is absent


# NOTE: The local provider is handled separately because its URL is
# derived from ``local_base_url`` in user config.
_PROVIDERS: Dict[str, OpenAICompatProvider] = {
    "groq": OpenAICompatProvider(
        key="groq",
        display_name="Groq",
        url="https://api.groq.com/openai/v1/chat/completions",
        default_model="llama-3.1-8b-instant",
        missing_key_message=_("Configure the Groq API key in Preferences."),
    ),
    "openrouter": OpenAICompatProvider(
        key="openrouter",
        display_name="OpenRouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        default_model="openrouter/polaris-alpha",
        missing_key_message=_(
            "Configure the OpenRouter API key in Preferences."
        ),
    ),
    "cerebras": OpenAICompatProvider(
        key="cerebras",
        display_name="Cerebras",
        url="https://api.cerebras.ai/v1/chat/completions",
        default_model="llama-3.3-70b",
        missing_key_message=_("Configure the Cerebras API key in Preferences."),
    ),
    "github": OpenAICompatProvider(
        key="github",
        display_name="GitHub Models",
        url="https://models.inference.ai.azure.com/chat/completions",
        default_model="gpt-4o-mini",
        missing_key_message=_(
            "Configure the GitHub Personal Access Token in Preferences."
        ),
    ),
    "mistral": OpenAICompatProvider(
        key="mistral",
        display_name="Mistral",
        url="https://api.mistral.ai/v1/chat/completions",
        default_model="mistral-small-latest",
        missing_key_message=_("Configure the Mistral API key in Preferences."),
    ),
}


def get_provider(key: str) -> OpenAICompatProvider:
    """Return the spec for ``key`` or raise ``KeyError``."""
    return _PROVIDERS[key]


def _build_payload(
    *,
    model: str,
    messages: List[Dict[str, str]],
    message_builder: Callable[[List[Dict[str, str]]], List[Dict[str, str]]],
) -> Dict[str, Any]:
    """Build the ``{model, messages}`` JSON body.

    ``message_builder`` is ``ai_provider_format.build_openai_messages``
    — we take it as a parameter so this module has zero coupling to
    the response-parsing module (easier to test).
    """
    return {"model": model, "messages": message_builder(messages)}


def perform_openai_compat(
    provider_key: str,
    *,
    config: Dict[str, str],
    messages: List[Dict[str, str]],
    streaming: bool,
    message_builder: Callable[[List[Dict[str, str]]], List[Dict[str, str]]],
    request_fn: Callable[..., str],
    streaming_fn: Callable[..., str],
) -> str:
    """Issue a request to an OpenAI-compatible provider.

    ``request_fn`` and ``streaming_fn`` are the two transport
    implementations (``_openai_compat_request`` and
    ``_openai_compat_streaming`` on the assistant). Passing them in
    keeps this module transport-agnostic.

    Raises ``RuntimeError`` with the provider-specific "configure the
    key" message when no API key is configured.
    """
    provider = get_provider(provider_key)

    api_key = config.get("api_key", "").strip()
    if not api_key:
        raise RuntimeError(provider.missing_key_message)

    model = config.get("model", "").strip() or provider.default_model
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = _build_payload(
        model=model, messages=messages, message_builder=message_builder
    )

    if streaming:
        return streaming_fn(provider.url, headers, payload, provider.display_name)
    return request_fn(provider.url, headers, payload, provider.display_name)


def resolve_local_url(config: Dict[str, str]) -> str:
    """Normalize the local-AI base URL.

    Accepts ``localhost:11434`` (Ollama default — appends ``/v1``) or
    any full ``http(s)://…`` path. Trailing slashes are stripped so
    the final URL joins cleanly.
    """
    base_url = config.get(
        "local_base_url", "http://localhost:11434/v1"
    ).rstrip("/")
    if base_url.endswith(":11434"):
        base_url += "/v1"
    return f"{base_url}/chat/completions"


def build_local_headers(config: Dict[str, str]) -> Dict[str, str]:
    """Headers for a local-AI request. API key is optional."""
    headers = {"Content-Type": "application/json"}
    api_key = config.get("api_key", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
