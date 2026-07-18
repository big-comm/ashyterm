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
    scanners = (
        _scan_escaped_character,
        _scan_heredoc,
        _scan_single_quote,
        _scan_double_quote,
        _scan_backtick_quote,
        _scan_compound_opener,
        _scan_compound_closer,
        _scan_simple_opener,
        _scan_simple_closer,
        _scan_comment,
    )
    index = 0
    while index < len(buffer):
        for scanner in scanners:
            next_index = scanner(buffer, index, bracket_stack, issues)
            if next_index is not None:
                index = next_index
                break
        else:
            index += 1

    for opener, pos in bracket_stack:
        end = pos + len(opener)
        issues.append(SyntaxIssue(pos, end, ErrorKind.UNMATCHED_BRACKET, opener))


def _scan_escaped_character(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    _issues: List[SyntaxIssue],
) -> int | None:
    if buffer[index] == "\\" and index + 1 < len(buffer):
        return index + 2
    return None


def _scan_heredoc(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    _issues: List[SyntaxIssue],
) -> int | None:
    if not buffer.startswith("<<", index):
        return None
    if buffer.startswith("<<<", index):
        line_end = buffer.find("\n", index + 3)
        return len(buffer) if line_end == -1 else line_end
    cursor, delimiter = _parse_heredoc_delimiter(buffer, index + 2)
    if not delimiter:
        return cursor
    return _find_heredoc_end(buffer, cursor, delimiter)


def _parse_heredoc_delimiter(buffer: str, cursor: int) -> Tuple[int, str]:
    if cursor < len(buffer) and buffer[cursor] == "-":
        cursor += 1
    while cursor < len(buffer) and buffer[cursor] in (" ", "\t"):
        cursor += 1
    delimiter_start = cursor
    quote_char = ""
    if cursor < len(buffer) and buffer[cursor] in ("'", '"', "\\"):
        quote_char = buffer[cursor]
        cursor += 1
    stops = {" ", "\t", "\n", ";"}
    if quote_char:
        stops.add(quote_char)
    while cursor < len(buffer) and buffer[cursor] not in stops:
        cursor += 1
    delimiter = buffer[delimiter_start:cursor].strip("'\"\\")
    if quote_char and cursor < len(buffer) and buffer[cursor] == quote_char:
        cursor += 1
    return cursor, delimiter


def _find_heredoc_end(buffer: str, cursor: int, delimiter: str) -> int:
    while cursor < len(buffer):
        newline = buffer.find("\n", cursor)
        if newline == -1:
            return len(buffer)
        line_start = newline + 1
        line_end = buffer.find("\n", line_start)
        if line_end == -1:
            line_end = len(buffer)
        if buffer[line_start:line_end].strip() == delimiter:
            return line_end
        cursor = line_start
    return cursor


def _scan_single_quote(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    issues: List[SyntaxIssue],
) -> int | None:
    if buffer[index] != "'":
        return None
    closing = buffer.find("'", index + 1)
    if closing == -1:
        issues.append(SyntaxIssue(index, index + 1, ErrorKind.UNCLOSED_QUOTE, "'"))
        return len(buffer)
    return closing + 1


def _scan_escaped_quote(
    buffer: str, index: int, quote: str, issues: List[SyntaxIssue]
) -> int | None:
    if buffer[index] != quote:
        return None
    cursor = index + 1
    while cursor < len(buffer):
        if buffer[cursor] == "\\" and cursor + 1 < len(buffer):
            cursor += 2
            continue
        if buffer[cursor] == quote:
            return cursor + 1
        cursor += 1
    issues.append(SyntaxIssue(index, index + 1, ErrorKind.UNCLOSED_QUOTE, quote))
    return len(buffer)


def _scan_double_quote(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    issues: List[SyntaxIssue],
) -> int | None:
    return _scan_escaped_quote(buffer, index, '"', issues)


def _scan_backtick_quote(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    issues: List[SyntaxIssue],
) -> int | None:
    return _scan_escaped_quote(buffer, index, "`", issues)


def _scan_compound_opener(
    buffer: str,
    index: int,
    stack: List[Tuple[str, int]],
    _issues: List[SyntaxIssue],
) -> int | None:
    for opener in ("$((", "$(", "${", "[[", "(("):
        if buffer.startswith(opener, index):
            stack.append((opener, index))
            return index + len(opener)
    return None


def _scan_compound_closer(
    buffer: str,
    index: int,
    stack: List[Tuple[str, int]],
    issues: List[SyntaxIssue],
) -> int | None:
    closers = (("]]", ("[[",)), ("))", ("((", "$((")))
    for closer, expected in closers:
        if buffer.startswith(closer, index):
            _close_bracket(stack, expected, closer, index, issues)
            return index + len(closer)
    return None


def _close_bracket(
    stack: List[Tuple[str, int]],
    expected: tuple[str, ...],
    closer: str,
    index: int,
    issues: List[SyntaxIssue],
) -> None:
    if stack and stack[-1][0] in expected:
        stack.pop()
        return
    issues.append(
        SyntaxIssue(index, index + len(closer), ErrorKind.UNMATCHED_BRACKET, closer)
    )


def _scan_simple_opener(
    buffer: str,
    index: int,
    stack: List[Tuple[str, int]],
    _issues: List[SyntaxIssue],
) -> int | None:
    character = buffer[index]
    if character not in _BRACKET_PAIRS:
        return None
    stack.append((character, index))
    return index + 1


def _scan_simple_closer(
    buffer: str,
    index: int,
    stack: List[Tuple[str, int]],
    issues: List[SyntaxIssue],
) -> int | None:
    closer = buffer[index]
    if closer not in _BRACKET_CLOSERS:
        return None
    expected = {
        ")": ("(", "$("),
        "}": ("{", "${"),
        "]": ("[",),
    }[closer]
    _close_bracket(stack, expected, closer, index, issues)
    return index + 1


def _is_comment_start(buffer: str, index: int) -> bool:
    return buffer[index] == "#" and (
        index == 0 or buffer[index - 1] in (" ", "\t", ";", "\n", "(")
    )


def _scan_comment(
    buffer: str,
    index: int,
    _stack: List[Tuple[str, int]],
    _issues: List[SyntaxIssue],
) -> int | None:
    if not _is_comment_start(buffer, index):
        return None
    line_end = buffer.find("\n", index)
    return len(buffer) if line_end == -1 else line_end


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
    scanners = (
        _skip_shell_whitespace,
        _skip_shell_quoted_text,
        _skip_shell_comment,
        _collect_shell_word,
    )
    index = 0
    while index < len(buffer):
        for scanner in scanners:
            next_index = scanner(buffer, index, words)
            if next_index is not None:
                index = next_index
                break
        else:
            index += 1

    return words


def _skip_shell_whitespace(
    buffer: str, index: int, _words: List[Tuple[str, int]]
) -> int | None:
    if buffer[index] in (" ", "\t", "\n"):
        return index + 1
    return None


def _skip_shell_quoted_text(
    buffer: str, index: int, _words: List[Tuple[str, int]]
) -> int | None:
    quote = buffer[index]
    if quote not in ("'", '"'):
        return None
    cursor = index + 1
    while cursor < len(buffer):
        if quote == '"' and buffer[cursor] == "\\" and cursor + 1 < len(buffer):
            cursor += 2
            continue
        if buffer[cursor] == quote:
            return cursor + 1
        cursor += 1
    return cursor


def _skip_shell_comment(
    buffer: str, index: int, _words: List[Tuple[str, int]]
) -> int | None:
    if not _is_comment_start(buffer, index):
        return None
    line_end = buffer.find("\n", index)
    return len(buffer) if line_end == -1 else line_end


def _collect_shell_word(
    buffer: str, index: int, words: List[Tuple[str, int]]
) -> int | None:
    if not (buffer[index].isalpha() or buffer[index] == "_"):
        return None
    cursor = index + 1
    while cursor < len(buffer) and (buffer[cursor].isalnum() or buffer[cursor] == "_"):
        cursor += 1
    words.append((buffer[index:cursor], index))
    return cursor


# ANSI codes for syntax error indication
SYNTAX_ERROR_UNDERLINE = "\033[4;31m"
SYNTAX_ERROR_RESET = "\033[24;39m"


def get_error_indicators(buffer: str) -> List[SyntaxIssue]:
    """
    Get syntax error indicators for the current input buffer.

    This is the main entry point for the syntax validation system.
    """
    return validate_shell_input(buffer)
