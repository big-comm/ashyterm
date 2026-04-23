"""Pure parsers for AI assistant responses.

The assistant expects each reply to be a JSON object with ``reply`` and
``commands`` fields. In practice models sometimes wrap that JSON in
markdown fences, include extra prose around it, or emit commands as
bare strings. These helpers smooth over those variations without
reaching into ``TerminalAiAssistant`` state.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def clean_response(raw_content: str) -> str:
    """Strip a leading ```\\n or ```json\\n fence plus the trailing ```.

    Reasoning: some providers wrap their JSON in code fences even when
    instructed not to. We only strip one layer — inner fences inside
    the JSON ``reply`` field are user-visible markdown and must stay.
    """
    clean = raw_content.strip()

    if clean.startswith("```"):
        first_newline = clean.find("\n")
        if first_newline != -1:
            clean = clean[first_newline + 1 :]
        if clean.endswith("```"):
            clean = clean[:-3]

    return clean.strip()


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Return the first balanced JSON object found in ``text``.

    Walks the string brace-by-brace so a leading/trailing prose can be
    tolerated. Returns ``None`` if no object parses cleanly.
    """
    start = text.find("{")
    while start != -1:
        brace_level = 0
        for end in range(start, len(text)):
            char = text[end]
            if char == "{":
                brace_level += 1
            elif char == "}":
                brace_level -= 1
                if brace_level == 0:
                    candidate = text[start : end + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def normalize_commands(value: Any) -> List[Dict[str, str]]:
    """Coerce a raw ``commands`` value into ``[{command, description}, …]``.

    Accepts:
    * ``list[str]`` — every non-empty entry becomes ``{command, ""}``.
    * ``list[dict]`` — entries with ``command`` (or ``cmd``) survive,
      ``description`` is preserved when present.
    * ``str`` — treated as a single-command list.

    Anything else (or empty after trimming) yields an empty list.
    """
    commands: List[Dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                commands.append({"command": item.strip(), "description": ""})
            elif isinstance(item, dict):
                candidate = item.get("command") or item.get("cmd")
                description = item.get("description") or ""
                if isinstance(candidate, str) and candidate.strip():
                    commands.append(
                        {
                            "command": candidate.strip(),
                            "description": description.strip()
                            if isinstance(description, str)
                            else "",
                        }
                    )
    elif isinstance(value, str) and value.strip():
        commands.append({"command": value.strip(), "description": ""})
    return commands


# Regex fallback used when the provider ignored the "raw JSON" rule and
# emitted prose with fenced code blocks instead. We harvest every
# bash/sh/zsh block and treat it as a separate suggested command.
_CODE_BLOCK_PATTERN = re.compile(r"```(?:bash|sh|zsh)?\n(.*?)```", re.DOTALL)


def parse_assistant_payload(
    content: str,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
    """Turn a raw assistant string into ``(reply_text, commands, snippets)``.

    Flow:
      1. Strip the outer code fence if any.
      2. Try ``json.loads`` on the cleaned content. If that fails, try
         :func:`extract_json_object` in case the model added prose around
         the JSON object.
      3. On success: pull ``reply`` and ``commands`` through the
         normalizer.
      4. On failure: fall back to treating ``content`` as prose and
         harvesting commands from fenced code blocks.
    """
    clean_content = clean_response(content)

    reply_text = ""
    commands: List[Dict[str, str]] = []
    code_snippets: List[Dict[str, str]] = []
    payload: Optional[Any] = None

    try:
        payload = json.loads(clean_content)
    except json.JSONDecodeError:
        payload = extract_json_object(clean_content)

    if isinstance(payload, dict):
        reply_text = payload.get("reply", "")
        commands = normalize_commands(payload.get("commands", []))
    else:
        # Keep the original content for the reply so markdown/formatting
        # survives; harvest single-line fenced snippets as commands (we
        # deliberately skip multi-line scripts — those are shown in the
        # reply text for the user to read before running).
        reply_text = content
        for match in _CODE_BLOCK_PATTERN.findall(content):
            cmd_str = match.strip()
            if cmd_str and len(cmd_str.splitlines()) == 1:
                commands.append(
                    {"command": cmd_str, "description": "Suggested command"}
                )

    return reply_text, commands, code_snippets
