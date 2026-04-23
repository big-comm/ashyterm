"""Provider-specific request/response format translation for the AI assistant.

Each supported provider (Gemini, OpenAI-compatible) has its own wire
format for messages and its own shape for responses. The rules are
pure transformations over dicts/strings, so they live here and can be
validated independently of the network stack in ``ai_assistant``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


# ── request builders ─────────────────────────────────────────


def build_gemini_conversation(
    messages: List[Dict[str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """Split a flat message list into Gemini's (system_instruction, contents).

    Gemini expects ``system`` text out-of-band as ``system_instruction``
    and all other turns as ``contents`` entries with mapped roles
    (``assistant`` → ``model``). Empty bodies are skipped so we don't
    blow the token budget on placeholders.
    """
    system_instruction = ""
    contents: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        text = message.get("content", "")
        if not text:
            continue
        if role == "system" and not system_instruction:
            system_instruction = text
            continue
        mapped_role = "model" if role == "assistant" else "user"
        contents.append({"role": mapped_role, "parts": [{"text": text}]})
    # Gemini rejects an empty `contents` array, so seed an empty user turn.
    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})
    return system_instruction, contents


def build_openai_messages(
    messages: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Normalize messages for OpenAI-compatible providers.

    Drops messages with non-string or empty content and clamps unknown
    roles to ``user`` so the request always validates against the
    provider's schema.
    """
    formatted: List[Dict[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        role_mapped = role if role in {"system", "user", "assistant"} else "user"
        formatted.append({"role": role_mapped, "content": content})
    return formatted


# ── response extractors ──────────────────────────────────────


def extract_openai_content(
    response_data: Dict[str, Any], provider_name: str
) -> str:
    """Pull the assistant's text out of an OpenAI-compatible response.

    Handles both the plain ``content`` string and the newer multi-part
    list (``[{type: 'text', text: '...'}]``). Raises ``RuntimeError``
    so the assistant surfaces a consistent user-visible message.
    """
    choices = response_data.get("choices") or []
    if not choices:
        raise RuntimeError("The server response did not contain any suggestions.")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None

    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        )

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"{provider_name} did not return any usable content.")
    return content.strip()


def extract_gemini_content(response_data: Dict[str, Any]) -> str:
    """Pull concatenated text from a Gemini ``generateContent`` response."""
    candidates = response_data.get("candidates") or []
    if not candidates:
        raise RuntimeError("The server response did not contain any suggestions.")

    collected: List[str] = []
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not parts:
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                collected.append(part["text"])

    if collected:
        return "\n".join(collected)

    raise RuntimeError("Gemini did not return any usable content.")


# ── SSE line parsing ─────────────────────────────────────────


def parse_sse_line(line_str: str) -> Tuple[Optional[str], bool]:
    """Parse one server-sent-events line from an OpenAI-style stream.

    Returns ``(content_chunk, is_eof)``. ``content_chunk`` is:
      * ``None`` when the line is a heartbeat/keepalive (no ``data: ``
        prefix) or a malformed JSON payload we can't decode,
      * ``""`` when the payload is a terminator (``[DONE]`` or a
        ``delta`` with no content) — ``is_eof`` distinguishes the two,
      * a non-empty string for real token deltas.

    Raises ``RuntimeError`` on explicit provider errors so the caller
    can show the message immediately instead of silently draining.
    """
    if not line_str.startswith("data: "):
        return None, False

    data_str = line_str[6:]
    if data_str == "[DONE]":
        return "", True

    try:
        data = json.loads(data_str)
        if "error" in data:
            err_msg = data["error"].get("message", "API Error")
            raise RuntimeError(err_msg)
        choices = data.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content is None:
                return "", False
            return content, False
    except json.JSONDecodeError:
        pass
    return None, False
