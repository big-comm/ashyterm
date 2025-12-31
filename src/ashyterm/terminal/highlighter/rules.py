# ashyterm/terminal/highlighter/rules.py
"""
Rule classes and utilities for terminal highlighting.

This module contains:
- CompiledRule: Compiled regex rule with pre-filter optimization
- LiteralKeywordRule: Optimized rule for simple keyword patterns
- Helper functions for pattern extraction and pre-filter creation
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .constants import KEYWORD_PATTERN, is_word_boundary


def smart_split_alternation(inner: str) -> List[str]:
    """
    Split a regex alternation pattern on | characters that are not inside parentheses.

    Example: "error|fail(?:ure|ed)?|fatal" -> ["error", "fail(?:ure|ed)?", "fatal"]

    Args:
        inner: The inner content of an alternation pattern.

    Returns:
        List of parts split on top-level | characters.
    """
    parts = []
    current = ""
    depth = 0
    for char in inner:
        if char == "(":
            depth += 1
            current += char
        elif char == ")":
            depth -= 1
            current += char
        elif char == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def expand_optional_suffixes(part: str) -> List[str]:
    """
    Expand a pattern with optional suffixes into all possible keywords.

    Examples:
        "fail(?:ure|ed)?" -> ["fail", "failure", "failed"]
        "complete(?:d)?" -> ["complete", "completed"]
        "warn(?:ing)?" -> ["warn", "warning"]
        "enable(?:d)?" -> ["enable", "enabled"]

    Args:
        part: A single alternation part that may contain optional suffixes.

    Returns:
        List of expanded keywords.
    """
    # Match patterns like: word(?:suffix1|suffix2)?
    match = re.match(r"^([a-zA-Z]+)\(\?:([^)]+)\)\?$", part)
    if match:
        base = match.group(1).lower()
        suffixes_str = match.group(2)
        # Split suffixes on |
        suffixes = suffixes_str.split("|")
        keywords = [base]  # Base word always included
        for suffix in suffixes:
            keywords.append(base + suffix.lower())
        return keywords

    # No optional suffix, just return the cleaned base word
    clean = re.sub(r"[^a-zA-Z]", "", part)
    if clean:
        return [clean.lower()]
    return []


def extract_literal_keywords(pattern: str) -> Optional[Tuple[str, ...]]:
    """
    Extract literal keywords from a word-boundary alternation pattern.

    Patterns like \\b(error|fail(?:ure|ed)?|fatal)\\b become
    ('error', 'fail', 'failure', 'failed', 'fatal').

    Returns None if pattern is not a simple keyword alternation.

    Handles optional suffixes by expanding them into separate keywords:
    - fail(?:ure|ed)? -> fail, failure, failed
    - complete(?:d)? -> complete, completed

    Args:
        pattern: A regex pattern string.

    Returns:
        Tuple of keywords, or None if pattern is not a simple alternation.
    """
    match = KEYWORD_PATTERN.match(pattern)
    if not match:
        return None

    inner = match.group(1)

    # Split on | that's not inside parentheses
    parts = smart_split_alternation(inner)

    keywords = []
    for part in parts:
        # Expand optional suffixes into multiple keywords
        expanded = expand_optional_suffixes(part)
        keywords.extend(expanded)

    if not keywords:
        return None

    return tuple(keywords)


# Pre-filter mapping: rule keywords -> (required_chars, check_function_or_None)
# If check_function is None, use a simple lambda line: char in line
_PREFILTER_RULES: Dict[str, Tuple[str, Optional[Callable[[str], bool]]]] = {
    "ipv4": (".", None),
    "ip": (".", None),  # Matches "ip" without "v6"
    "ipv6": (":", None),
    "mac": (":", lambda line: ":" in line or "-" in line),
    "uuid": ("-", None),
    "guid": ("-", None),
    "url": ("http", lambda line: "http" in line),
    "http": ("http", lambda line: "http" in line),
    "email": ("@", None),
    "date": ("-", None),
    "quote": ('"', lambda line: '"' in line or "'" in line),
    "string": ('"', lambda line: '"' in line or "'" in line),
}


def _extract_keyword_prefilter(pattern: str) -> Optional[Callable[[str], bool]]:
    """Extract pre-filter from word-boundary alternation patterns.

    Handles patterns like \\b(word1|word2)\\b.

    Args:
        pattern: The regex pattern string.

    Returns:
        A pre-filter function if keywords found, None otherwise.
    """
    match = KEYWORD_PATTERN.match(pattern)
    if not match:
        return None

    inner = match.group(1)
    # Extract base words, removing optional suffixes like (?:ed)?
    words = set()
    for part in inner.split("|"):
        # Remove (?:...) non-capturing groups
        clean = re.sub(r"\(\?:[^)]+\)\??", "", part)
        if clean and clean.isalpha():
            words.add(clean.lower())

    if words:
        # Frozen tuple for slightly faster iteration
        keywords = tuple(words)
        return lambda line: any(kw in line for kw in keywords)

    return None


def _get_rule_based_prefilter(rule_name: str) -> Optional[Callable[[str], bool]]:
    """Get pre-filter based on rule name heuristics.

    Args:
        rule_name: The name of the rule.

    Returns:
        A pre-filter function if applicable, None otherwise.
    """
    rule_lower = rule_name.lower()

    # Check for IPv4 (but not IPv6)
    if "ipv4" in rule_lower or ("ip" in rule_lower and "v6" not in rule_lower):
        return lambda line: "." in line

    # Check each rule in the mapping
    for key, (char, func) in _PREFILTER_RULES.items():
        if key in rule_lower:
            # Special case: mac address needs both keywords
            if key == "mac" and "address" not in rule_lower:
                continue
            return func if func else (lambda c: lambda line: c in line)(char)

    return None


def extract_prefilter(pattern: str, rule_name: str) -> Optional[Callable[[str], bool]]:
    """
    Create a fast pre-filter function for a rule pattern.

    Pre-filters are simple string checks that run before the regex.
    If the pre-filter returns False, the regex is skipped entirely.
    This provides massive speedup for lines that cannot match.

    Args:
        pattern: The regex pattern string.
        rule_name: The name of the rule (used for heuristics).

    Returns:
        A pre-filter function, or None if no efficient pre-filter can be created.
    """
    # Try keyword-based pre-filter first
    keyword_filter = _extract_keyword_prefilter(pattern)
    if keyword_filter:
        return keyword_filter

    # Fall back to rule-name-based pre-filter
    return _get_rule_based_prefilter(rule_name)


@dataclass(slots=True)
class CompiledRule:
    """
    A compiled highlight rule optimized for fast matching.

    Uses __slots__ and dataclass for minimal memory overhead.
    Pre-filter function enables skipping expensive regex when line cannot match.

    Attributes:
        pattern: Compiled regex pattern (PCRE2).
        ansi_colors: Tuple of ANSI color codes for capture groups.
        action: "next" to continue processing, "stop" to halt after match.
        num_groups: Number of capture groups in the pattern.
        prefilter: Optional function that returns True if regex should run.
    """

    pattern: Any  # Compiled regex pattern
    ansi_colors: Tuple[str, ...]  # Tuple for faster iteration than list
    action: str  # "next" or "stop"
    num_groups: int
    prefilter: Optional[Callable[[str], bool]]  # Returns True if regex should run


@dataclass(slots=True)
class LiteralKeywordRule:
    """
    Optimized rule for simple word-boundary keyword patterns.

    Instead of regex, uses:
    - Set lookup for O(1) keyword detection
    - Manual word boundary validation (much faster than regex)
    - Direct string scanning with str.find()

    This provides ~10-50x speedup over regex for keyword patterns.

    Attributes:
        keywords: Frozen set of lowercase keywords for O(1) lookup.
        keyword_tuple: Tuple of keywords for iteration.
        ansi_color: Single ANSI color code for all matches.
        action: "next" to continue processing, "stop" to halt after match.
    """

    keywords: frozenset  # Frozen set of lowercase keywords for O(1) lookup
    keyword_tuple: Tuple[str, ...]  # Tuple of keywords for iteration
    ansi_color: str  # Single ANSI color (keyword rules use one color)
    action: str  # "next" or "stop"

    def find_matches(self, line: str, line_lower: str) -> List[Tuple[int, int, str]]:
        """
        Find all keyword matches in the line with word boundaries.

        Args:
            line: Original line (for boundary checks).
            line_lower: Lowercase version for matching.

        Returns:
            List of (start, end, ansi_color) tuples.
        """
        matches = []

        for keyword in self.keyword_tuple:
            kw_len = len(keyword)
            start = 0

            # Find all occurrences of this keyword
            while True:
                pos = line_lower.find(keyword, start)
                if pos == -1:
                    break

                end = pos + kw_len

                # Check word boundaries
                if is_word_boundary(line_lower, pos, end):
                    matches.append((pos, end, self.ansi_color))

                start = pos + 1

        return matches
