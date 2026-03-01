"""Helper functions for AI chat — JSON extraction, markdown parsing, pygments."""

from __future__ import annotations

import json
import re

from ....utils.logger import get_logger

logger = get_logger(__name__)

# Pre-compiled regex patterns for markdown formatting (performance optimization)
_CODE_BLOCK_PATTERN = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_BOLD_PATTERN = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_PATTERN = re.compile(r"\*([^*]+)\*")
_HEADER3_PATTERN = re.compile(r"^### (.+)$", re.MULTILINE)
_HEADER2_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)
_HEADER1_PATTERN = re.compile(r"^# (.+)$", re.MULTILINE)

# Lazy-loaded pygments module (optional dependency)
_pygments_module = None
_pygments_available = None  # None = not checked yet, True/False = result


def _get_pygments():
    """Lazy load pygments module. Returns None if not installed."""
    global _pygments_module, _pygments_available

    if _pygments_available is None:
        try:
            import pygments
            from pygments.lexers import TextLexer, get_lexer_by_name
            from pygments.util import ClassNotFound

            _pygments_module = {
                "pygments": pygments,
                "get_lexer_by_name": get_lexer_by_name,
                "TextLexer": TextLexer,
                "ClassNotFound": ClassNotFound,
            }
            _pygments_available = True
            logger.debug("Pygments loaded successfully for syntax highlighting")
        except ImportError:
            _pygments_module = None
            _pygments_available = False
            logger.debug("Pygments not available, using fallback highlighting")

    return _pygments_module


def _extract_reply_from_json(text: str) -> str:
    """Try to extract 'reply' field from JSON response text.

    Handles both complete and partial JSON responses during streaming.
    Returns ONLY the reply text, never the full JSON structure.
    Also filters out standalone JSON arrays that look like command lists.
    """
    if not text:
        return text

    if "{" not in text and "[" not in text:
        return text

    # Remove trailing command arrays
    cleaned = _strip_trailing_command_array(text)
    if cleaned != text:
        return cleaned

    # Try complete JSON first
    result = _try_parse_complete_json(text)
    if result is not None:
        return result

    # Try to find and extract JSON object
    result = _try_extract_json_object(text)
    if result is not None:
        return result

    # Try partial reply extraction
    result = _try_extract_partial_reply(text)
    if result is not None:
        return result

    # Check if it's incomplete streaming JSON
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return ""

    return text


def _strip_trailing_command_array(text: str) -> str:
    """Remove trailing JSON arrays that look like command lists."""
    stripped = text.strip()
    if not stripped.endswith("]"):
        return text

    array_start = _find_matching_bracket(stripped)
    if array_start == -1:
        return text

    potential_array = stripped[array_start:]
    try:
        parsed = json.loads(potential_array)
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            result = stripped[:array_start].rstrip("\n ]")
            return result if result else text
    except json.JSONDecodeError:
        pass
    return text


def _find_matching_bracket(text: str) -> int:
    """Find the index of the opening bracket matching the closing bracket at end."""
    bracket_count = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == "]":
            bracket_count += 1
        elif text[i] == "[":
            bracket_count -= 1
            if bracket_count == 0:
                return i
    return -1


def _try_parse_complete_json(text: str) -> str | None:
    """Try to parse text as complete JSON and extract reply."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "reply" in data:
                return data["reply"]
            return ""
    except json.JSONDecodeError:
        pass
    return None


def _try_extract_json_object(text: str) -> str | None:
    """Try to find and parse embedded JSON object."""
    start = text.find("{")
    if start == -1:
        return None

    brace_level = 0
    for end in range(start, len(text)):
        if text[end] == "{":
            brace_level += 1
        elif text[end] == "}":
            brace_level -= 1
            if brace_level == 0:
                try:
                    data = json.loads(text[start : end + 1])
                    if isinstance(data, dict) and "reply" in data:
                        return data["reply"]
                    prefix = text[:start].strip()
                    return prefix if prefix else ""
                except json.JSONDecodeError:
                    pass
                break
    return None


def _try_extract_partial_reply(text: str) -> str | None:
    """Extract reply from incomplete/streaming JSON."""
    patterns = ['"reply": "', '"reply":"', "'reply': '", "'reply':'"]
    for pattern in patterns:
        reply_start = text.find(pattern)
        if reply_start != -1:
            value_start = reply_start + len(pattern)
            quote_char = pattern[-1]
            return _parse_quoted_string(text, value_start, quote_char)
    return None


def _parse_quoted_string(text: str, start: int, quote: str) -> str:
    """Parse a quoted string handling escape sequences."""
    result = []
    i = start
    escape_map = {"n": "\n", "t": "\t", quote: quote, "\\": "\\"}

    while i < len(text):
        char = text[i]
        if char == "\\":
            if i + 1 < len(text):
                esc = text[i + 1]
                result.append(escape_map.get(esc, esc))
                i += 2
            else:
                i += 1
        elif char == quote:
            return "".join(result)
        else:
            result.append(char)
            i += 1

    return "".join(result) if result else ""


def _normalize_commands(commands: list | None) -> list[str]:
    """Normalize commands to a list of strings.

    Handles both list of strings and list of dicts with 'command' key.
    """
    if not commands:
        return []

    result = []
    for cmd in commands:
        if isinstance(cmd, str):
            result.append(cmd)
        elif isinstance(cmd, dict):
            # Extract command from dict format
            command_str = cmd.get("command", "") or cmd.get("cmd", "")
            if command_str:
                result.append(command_str)
    return result
