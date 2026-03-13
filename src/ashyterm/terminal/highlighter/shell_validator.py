# ashyterm/terminal/highlighter/shell_validator.py
"""
Shell syntax validator for live input highlighting.

Detects unbalanced brackets/quotes and incomplete control structures
while the user types, providing visual feedback through ANSI underline
markers on the offending characters.

Design:
- Stateless per call: validate(buffer) returns a list of error spans
- Fast: O(n) single-pass scanner, no subprocess or eval
- Safe: never executes user input
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Tuple


class ErrorKind(Enum):
    """Kind of syntax error detected."""

    UNMATCHED_BRACKET = auto()
    UNCLOSED_QUOTE = auto()
    INCOMPLETE_STRUCTURE = auto()


@dataclass(slots=True, frozen=True)
class SyntaxIssue:
    """A detected syntax issue in the input buffer."""

    start: int
    end: int
    kind: ErrorKind
    token: str


# Bracket pairs (opener -> closer)
_BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
}

_BRACKET_CLOSERS = set(_BRACKET_PAIRS.values())

# Control structure openers -> expected closers
_CONTROL_PAIRS = {
    "if": "fi",
    "then": None,
    "elif": None,
    "else": None,
    "case": "esac",
    "for": "done",
    "while": "done",
    "until": "done",
    "select": "done",
    "do": None,
}

# Words that close control structures
_CONTROL_CLOSERS = {"fi", "esac", "done"}


def validate_shell_input(buffer: str) -> List[SyntaxIssue]:
    """
    Validate shell input buffer for syntax issues.

    Returns a list of SyntaxIssue objects describing unbalanced brackets,
    unclosed quotes, and incomplete control structures.
    """
    if not buffer or len(buffer) < 2:
        return []

    issues: List[SyntaxIssue] = []

    # Phase 1: Scan for bracket and quote issues
    _scan_brackets_and_quotes(buffer, issues)

    # Phase 2: Check control structures (only on multi-word input)
    if " " in buffer or "\n" in buffer:
        _scan_control_structures(buffer, issues)

    return issues


def _scan_brackets_and_quotes(buffer: str, issues: List[SyntaxIssue]) -> None:
    """Scan for unbalanced brackets and unclosed quotes."""
    bracket_stack: List[Tuple[str, int]] = []
    i = 0
    length = len(buffer)

    while i < length:
        ch = buffer[i]

        # Skip escaped characters
        if ch == "\\" and i + 1 < length:
            i += 2
            continue

        # Handle single quotes
        if ch == "'":
            start = i
            i += 1
            while i < length and buffer[i] != "'":
                i += 1
            if i >= length:
                issues.append(
                    SyntaxIssue(start, start + 1, ErrorKind.UNCLOSED_QUOTE, "'")
                )
            else:
                i += 1
            continue

        # Handle double quotes
        if ch == '"':
            start = i
            i += 1
            while i < length:
                if buffer[i] == "\\" and i + 1 < length:
                    i += 2
                    continue
                if buffer[i] == '"':
                    break
                i += 1
            if i >= length:
                issues.append(
                    SyntaxIssue(start, start + 1, ErrorKind.UNCLOSED_QUOTE, '"')
                )
            else:
                i += 1
            continue

        # Handle backtick quotes
        if ch == "`":
            start = i
            i += 1
            while i < length and buffer[i] != "`":
                if buffer[i] == "\\" and i + 1 < length:
                    i += 2
                    continue
                i += 1
            if i >= length:
                issues.append(
                    SyntaxIssue(start, start + 1, ErrorKind.UNCLOSED_QUOTE, "`")
                )
            else:
                i += 1
            continue

        # Handle $(( arithmetic expansion
        if (
            ch == "$"
            and i + 2 < length
            and buffer[i + 1] == "("
            and buffer[i + 2] == "("
        ):
            bracket_stack.append(("$((", i))
            i += 3
            continue

        # Handle $( command substitution
        if ch == "$" and i + 1 < length and buffer[i + 1] == "(":
            bracket_stack.append(("$(", i))
            i += 2
            continue

        # Handle ${ parameter expansion
        if ch == "$" and i + 1 < length and buffer[i + 1] == "{":
            bracket_stack.append(("${", i))
            i += 2
            continue

        # Handle [[ double bracket
        if ch == "[" and i + 1 < length and buffer[i + 1] == "[":
            bracket_stack.append(("[[", i))
            i += 2
            continue

        # Handle ]] double bracket closer
        if ch == "]" and i + 1 < length and buffer[i + 1] == "]":
            if bracket_stack and bracket_stack[-1][0] == "[[":
                bracket_stack.pop()
            else:
                issues.append(
                    SyntaxIssue(i, i + 2, ErrorKind.UNMATCHED_BRACKET, "]]")
                )
            i += 2
            continue

        # Handle (( arithmetic
        if ch == "(" and i + 1 < length and buffer[i + 1] == "(":
            bracket_stack.append(("((", i))
            i += 2
            continue

        # Handle )) arithmetic closer
        if ch == ")" and i + 1 < length and buffer[i + 1] == ")":
            if bracket_stack and bracket_stack[-1][0] in ("((", "$(("):
                bracket_stack.pop()
            else:
                issues.append(
                    SyntaxIssue(i, i + 2, ErrorKind.UNMATCHED_BRACKET, "))")
                )
            i += 2
            continue

        # Simple bracket openers
        if ch in _BRACKET_PAIRS:
            bracket_stack.append((ch, i))
            i += 1
            continue

        # Simple bracket closers
        if ch in _BRACKET_CLOSERS:
            if bracket_stack:
                top_char = bracket_stack[-1][0]
                if (
                    (ch == ")" and top_char in ("(", "$("))
                    or (ch == "}" and top_char in ("{", "${"))
                    or (ch == "]" and top_char == "[")
                ):
                    bracket_stack.pop()
                else:
                    issues.append(
                        SyntaxIssue(i, i + 1, ErrorKind.UNMATCHED_BRACKET, ch)
                    )
            else:
                issues.append(
                    SyntaxIssue(i, i + 1, ErrorKind.UNMATCHED_BRACKET, ch)
                )
            i += 1
            continue

        # Handle # comments
        if ch == "#":
            if i == 0 or buffer[i - 1] in (" ", "\t", ";", "\n", "("):
                while i < length and buffer[i] != "\n":
                    i += 1
                continue

        i += 1

    # Remaining unclosed brackets
    for opener, pos in bracket_stack:
        end = pos + len(opener)
        issues.append(SyntaxIssue(pos, end, ErrorKind.UNMATCHED_BRACKET, opener))


def _scan_control_structures(buffer: str, issues: List[SyntaxIssue]) -> None:
    """Scan for incomplete control structures (if without fi, etc.)."""
    words = _extract_shell_words(buffer)
    structure_stack: List[Tuple[str, int]] = []

    for word, pos in words:
        word_lower = word.lower()

        if word_lower in ("if", "case"):
            structure_stack.append((word_lower, pos))
        elif word_lower in ("for", "while", "until", "select"):
            structure_stack.append((word_lower, pos))
        elif word_lower == "fi":
            _close_structure(structure_stack, "if", word, pos, issues)
        elif word_lower == "esac":
            _close_structure(structure_stack, "case", word, pos, issues)
        elif word_lower == "done":
            _close_structure(
                structure_stack,
                ("for", "while", "until", "select"),
                word,
                pos,
                issues,
            )

    # Remaining unclosed structures
    for keyword, pos in structure_stack:
        issues.append(
            SyntaxIssue(
                pos, pos + len(keyword), ErrorKind.INCOMPLETE_STRUCTURE, keyword
            )
        )


def _close_structure(
    stack: List[Tuple[str, int]],
    expected_opener: str | tuple,
    closer_word: str,
    closer_pos: int,
    issues: List[SyntaxIssue],
) -> None:
    """Try to close a structure, report mismatch if not found."""
    if isinstance(expected_opener, str):
        expected_opener = (expected_opener,)

    for idx in range(len(stack) - 1, -1, -1):
        if stack[idx][0] in expected_opener:
            stack.pop(idx)
            return

    issues.append(
        SyntaxIssue(
            closer_pos,
            closer_pos + len(closer_word),
            ErrorKind.UNMATCHED_BRACKET,
            closer_word,
        )
    )


def _extract_shell_words(buffer: str) -> List[Tuple[str, int]]:
    """Extract word tokens from buffer, skipping quoted regions."""
    words: List[Tuple[str, int]] = []
    i = 0
    length = len(buffer)

    while i < length:
        ch = buffer[i]

        # Skip whitespace
        if ch in (" ", "\t", "\n"):
            i += 1
            continue

        # Skip single-quoted strings
        if ch == "'":
            i += 1
            while i < length and buffer[i] != "'":
                i += 1
            if i < length:
                i += 1
            continue

        # Skip double-quoted strings
        if ch == '"':
            i += 1
            while i < length:
                if buffer[i] == "\\" and i + 1 < length:
                    i += 2
                    continue
                if buffer[i] == '"':
                    break
                i += 1
            if i < length:
                i += 1
            continue

        # Skip comments
        if ch == "#" and (i == 0 or buffer[i - 1] in (" ", "\t", ";", "\n", "(")):
            while i < length and buffer[i] != "\n":
                i += 1
            continue

        # Collect a word
        if ch.isalpha() or ch == "_":
            start = i
            while i < length and (buffer[i].isalnum() or buffer[i] == "_"):
                i += 1
            word = buffer[start:i]
            words.append((word, start))
            continue

        # Skip operators and other characters
        i += 1

    return words


# ANSI codes for syntax error indication
SYNTAX_ERROR_UNDERLINE = "\033[4;31m"
SYNTAX_ERROR_RESET = "\033[24;39m"


def get_error_indicators(buffer: str) -> List[SyntaxIssue]:
    """
    Get syntax error indicators for the current input buffer.

    This is the main entry point for the syntax validation system.
    """
    return validate_shell_input(buffer)
