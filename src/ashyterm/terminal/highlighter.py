# ashyterm/terminal/highlighter.py
"""
Terminal output highlighter that applies ANSI color codes based on regex patterns.

Features:
- Multi-group regex: Different capture groups can have different colors
- Theme-aware: Uses logical color names resolved via active theme palette
- Context-aware: Applies command-specific rules based on foreground process
- High-performance: Uses PCRE2 backend with smart pre-filtering
- Cat/file highlighting: Syntax highlighting for file output using Pygments
- Help output highlighting: Colorizes --help and man page output
- Shell input highlighting: Live syntax coloring of typed commands using Pygments

Performance Architecture:
- Per-rule iteration with compiled patterns (faster than megex for <50 rules)
- Fast pre-filtering skips regex when line cannot possibly match
- PCRE2 backend (regex module) when available for ~50% faster matching
- Early termination on "stop" action rules
"""

import fcntl
import os
import pty
import signal
import struct
import termios
import threading
import weakref
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

import gi

gi.require_version("Vte", "3.91")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Vte

# Use regex module (PCRE2 backend) for 50%+ faster matching
# Falls back to standard re if not available
try:
    import regex as re_engine

    USING_PCRE2 = True
except ImportError:
    import re as re_engine

    USING_PCRE2 = False

# Standard re is still needed for ANSI pattern (compatibility)
import re

from ..settings.highlights import HighlightRule, get_highlight_manager
from ..utils.logger import get_logger


@dataclass(slots=True)
class CompiledRule:
    """
    A compiled highlight rule optimized for fast matching.

    Uses __slots__ and dataclass for minimal memory overhead.
    Pre-filter function enables skipping expensive regex when line cannot match.
    """

    pattern: Any  # Compiled regex pattern
    ansi_colors: Tuple[str, ...]  # Tuple for faster iteration than list
    action: str  # "next" or "stop"
    num_groups: int
    prefilter: Optional[Callable[[str], bool]]  # Returns True if regex should run


# NOTE: Command detection via byte-stream analysis has been replaced by
# VTE screen scraping on Enter key press in terminal/manager.py.
# The command_detector module is no longer used here to avoid conflicts
# and improve performance. Context is now set directly by the manager.

if TYPE_CHECKING:
    from ..settings.highlights import HighlightManager


ANSI_RESET = "\033[0m"

ALT_SCREEN_ENABLE_PATTERNS = [
    b"\x1b[?1049h",
    b"\x1b[?47h",
    b"\x1b[?1047h",
]

ALT_SCREEN_DISABLE_PATTERNS = [
    b"\x1b[?1049l",
    b"\x1b[?47l",
    b"\x1b[?1047l",
]


# Pre-compiled pattern for extracting keywords from alternation patterns
_KEYWORD_PATTERN = re.compile(r"^\\b\(([a-zA-Z|?:()]+)\)\\b$")

# Pattern to check if a character is a word boundary character
_WORD_CHAR = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    """
    Check if a match at [start:end] has word boundaries.

    A word boundary exists when:
    - start == 0 or text[start-1] is not a word character
    - end == len(text) or text[end] is not a word character
    """
    # Check start boundary
    if start > 0 and text[start - 1] in _WORD_CHAR:
        return False
    # Check end boundary
    if end < len(text) and text[end] in _WORD_CHAR:
        return False
    return True


def _smart_split_alternation(inner: str) -> List[str]:
    """
    Split a regex alternation pattern on | characters that are not inside parentheses.

    Example: "error|fail(?:ure|ed)?|fatal" -> ["error", "fail(?:ure|ed)?", "fatal"]
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


def _expand_optional_suffixes(part: str) -> List[str]:
    """
    Expand a pattern with optional suffixes into all possible keywords.

    Examples:
        "fail(?:ure|ed)?" -> ["fail", "failure", "failed"]
        "complete(?:d)?" -> ["complete", "completed"]
        "warn(?:ing)?" -> ["warn", "warning"]
        "enable(?:d)?" -> ["enable", "enabled"]
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


def _extract_literal_keywords(pattern: str) -> Optional[Tuple[str, ...]]:
    """
    Extract literal keywords from a word-boundary alternation pattern.

    Patterns like \\b(error|fail(?:ure|ed)?|fatal)\\b become
    ('error', 'fail', 'failure', 'failed', 'fatal').

    Returns None if pattern is not a simple keyword alternation.

    Handles optional suffixes by expanding them into separate keywords:
    - fail(?:ure|ed)? -> fail, failure, failed
    - complete(?:d)? -> complete, completed
    """
    match = _KEYWORD_PATTERN.match(pattern)
    if not match:
        return None

    inner = match.group(1)

    # Split on | that's not inside parentheses
    parts = _smart_split_alternation(inner)

    keywords = []
    for part in parts:
        # Expand optional suffixes into multiple keywords
        expanded = _expand_optional_suffixes(part)
        keywords.extend(expanded)

    if not keywords:
        return None

    return tuple(keywords)


@dataclass(slots=True)
class LiteralKeywordRule:
    """
    Optimized rule for simple word-boundary keyword patterns.

    Instead of regex, uses:
    - Set lookup for O(1) keyword detection
    - Manual word boundary validation (much faster than regex)
    - Direct string scanning with str.find()

    This provides ~10-50x speedup over regex for keyword patterns.
    """

    keywords: frozenset  # Frozen set of lowercase keywords for O(1) lookup
    keyword_tuple: Tuple[str, ...]  # Tuple of keywords for iteration
    ansi_color: str  # Single ANSI color (keyword rules use one color)
    action: str  # "next" or "stop"

    def find_matches(self, line: str, line_lower: str) -> List[Tuple[int, int, str]]:
        """
        Find all keyword matches in the line with word boundaries.

        Args:
            line: Original line (for boundary checks)
            line_lower: Lowercase version for matching

        Returns:
            List of (start, end, ansi_color) tuples
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
                if _is_word_boundary(line_lower, pos, end):
                    matches.append((pos, end, self.ansi_color))

                start = pos + 1

        return matches


def _extract_prefilter(pattern: str, rule_name: str) -> Optional[Callable[[str], bool]]:
    """
    Create a fast pre-filter function for a rule pattern.

    Pre-filters are simple string checks that run before the regex.
    If the pre-filter returns False, the regex is skipped entirely.
    This provides massive speedup for lines that cannot match.

    Returns None if no efficient pre-filter can be created.
    """
    # Extract keywords from word-boundary alternation patterns like \b(word1|word2)\b
    match = _KEYWORD_PATTERN.match(pattern)
    if match:
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

    # Pattern-specific pre-filters based on required characters
    rule_lower = rule_name.lower()

    # IPv4: requires dots and digits
    if "ipv4" in rule_lower or ("ip" in rule_lower and "v6" not in rule_lower):
        return lambda line: "." in line

    # IPv6: requires colons
    if "ipv6" in rule_lower:
        return lambda line: ":" in line

    # MAC address: requires colons or hyphens
    if "mac" in rule_lower and "address" in rule_lower:
        return lambda line: ":" in line or "-" in line

    # UUID/GUID: requires hyphens
    if "uuid" in rule_lower or "guid" in rule_lower:
        return lambda line: "-" in line

    # URLs: requires http
    if "url" in rule_lower or "http" in rule_lower:
        return lambda line: "http" in line

    # Email: requires @
    if "email" in rule_lower:
        return lambda line: "@" in line

    # Date (ISO): requires hyphens and digits
    if "date" in rule_lower:
        return lambda line: "-" in line

    # Quoted strings: requires quotes
    if "quote" in rule_lower or "string" in rule_lower:
        return lambda line: '"' in line or "'" in line

    return None


class OutputHighlighter:
    """
    Applies syntax highlighting to terminal output using ANSI escape codes.

    Supports:
    - Multi-group regex: colors list maps to capture groups
    - Theme-aware colors: resolves logical names via HighlightManager
    - Context-aware highlighting based on foreground process

    This is a singleton that supports multiple terminal proxies, each with
    their own context tracking.

    Performance Architecture:
    - Per-rule iteration with PCRE2 backend when available
    - Fast pre-filtering skips rules that cannot match
    - Tuples instead of lists for faster iteration
    - Early termination on "stop" action
    - Early return for ignored commands (native coloring tools)
    """

    def __init__(self):
        self.logger = get_logger("ashyterm.terminal.highlighter")
        self._manager: "HighlightManager" = get_highlight_manager()
        self._lock = threading.Lock()

        # Cache for compiled rules per context
        # Key: context_name, Value: Tuple of CompiledRule or LiteralKeywordRule
        self._context_rules_cache: Dict[
            str, Tuple[Union[CompiledRule, LiteralKeywordRule], ...]
        ] = {}

        # Global compiled rules (tuple for faster iteration)
        self._global_rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...] = ()

        # Per-proxy context tracking: proxy_id -> context_name
        self._proxy_contexts: Dict[int, str] = {}

        # Per-proxy full command tracking: proxy_id -> full command line
        # Used for Pygments highlighting to extract filenames from cat commands
        self._full_commands: Dict[int, str] = {}

        # Per-proxy flag to skip first output after context is set
        # This prevents the echoed command line from being highlighted
        # Key: proxy_id, Value: True if should skip first output
        self._skip_first_output: Dict[int, bool] = {}

        # Cached set of ignored commands (tools with native coloring)
        self._ignored_commands: frozenset = frozenset()
        self._refresh_ignored_commands()

        # Log which regex engine we're using
        if USING_PCRE2:
            self.logger.info(
                "Using regex module (PCRE2) for high-performance highlighting"
            )
        else:
            self.logger.info(
                "Using standard re module (install 'regex' for better performance)"
            )

        self._refresh_rules()
        self._manager.connect("rules-changed", self._on_rules_changed)

    def _refresh_ignored_commands(self) -> None:
        """Refresh the cached set of ignored commands from settings."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            ignored_list = settings.get("ignored_highlight_commands", [])
            self._ignored_commands = frozenset(cmd.lower() for cmd in ignored_list)
            self.logger.debug(
                f"Refreshed ignored commands: {len(self._ignored_commands)} commands"
            )
        except Exception as e:
            self.logger.warning(f"Failed to refresh ignored commands: {e}")
            self._ignored_commands = frozenset()

    def refresh_ignored_commands(self) -> None:
        """Public method to refresh ignored commands (called when settings change)."""
        with self._lock:
            self._refresh_ignored_commands()

    def register_proxy(self, proxy_id: int) -> None:
        """Register a proxy with the highlighter."""
        with self._lock:
            self._proxy_contexts[proxy_id] = ""
            self.logger.debug(f"Registered proxy {proxy_id}")

    def unregister_proxy(self, proxy_id: int) -> None:
        """Unregister a proxy from the highlighter."""
        with self._lock:
            if proxy_id in self._proxy_contexts:
                del self._proxy_contexts[proxy_id]
            if proxy_id in self._full_commands:
                del self._full_commands[proxy_id]
            if proxy_id in self._skip_first_output:
                del self._skip_first_output[proxy_id]
            self.logger.debug(f"Unregistered proxy {proxy_id}")

    def _on_rules_changed(self, manager) -> None:
        self._refresh_rules()
        # Clear context cache when rules change
        with self._lock:
            self._context_rules_cache.clear()

    def _compile_rule(
        self, rule: HighlightRule
    ) -> Optional[Union[CompiledRule, LiteralKeywordRule]]:
        """
        Compile a single highlight rule for fast matching.

        For simple keyword patterns like \\b(word1|word2)\\b, returns a
        LiteralKeywordRule which is ~10-50x faster than regex.

        For complex patterns, returns CompiledRule with:
        - Compiled regex pattern (PCRE2 when available)
        - ANSI color tuple
        - Pre-filter function for fast skipping
        """
        if not rule.enabled or not rule.pattern:
            return None

        # Get action (default: "next")
        action = getattr(rule, "action", "next")
        if action not in ("next", "stop"):
            action = "next"

        # Check if this is a simple keyword pattern that can use optimized matching
        literal_keywords = _extract_literal_keywords(rule.pattern)
        if literal_keywords:
            # Use optimized literal keyword matching (no regex!)
            # Resolve first color only (keyword rules use single color)
            if rule.colors:
                ansi_color = self._manager.resolve_color_to_ansi(rule.colors[0])
            else:
                ansi_color = ""

            if not ansi_color:
                return None

            return LiteralKeywordRule(
                keywords=frozenset(literal_keywords),
                keyword_tuple=literal_keywords,
                ansi_color=ansi_color,
                action=action,
            )

        # Fall back to regex for complex patterns
        try:
            # Compile with regex engine (PCRE2 when available)
            flags = re_engine.IGNORECASE
            if USING_PCRE2:
                flags |= re_engine.VERSION1  # Use faster VERSION1 mode

            pattern = re_engine.compile(rule.pattern, flags)
            num_groups = pattern.groups

            # Resolve colors to ANSI sequences (tuple for faster iteration)
            ansi_colors = (
                tuple(
                    self._manager.resolve_color_to_ansi(c) if c else ""
                    for c in rule.colors
                )
                if rule.colors
                else ("",)
            )

            if not any(ansi_colors):
                return None

            # Create pre-filter for fast skipping
            prefilter = _extract_prefilter(rule.pattern, rule.name)

            return CompiledRule(
                pattern=pattern,
                ansi_colors=ansi_colors,
                action=action,
                num_groups=num_groups,
                prefilter=prefilter,
            )

        except Exception as e:
            self.logger.warning(f"Invalid regex pattern in rule '{rule.name}': {e}")
            return None

    def _refresh_rules(self) -> None:
        """Refresh compiled rules from the manager."""
        with self._lock:
            rules_list = self._manager.rules
            self.logger.debug(f"Refreshing global rules: {len(rules_list)} total")

            compiled = []
            literal_count = 0
            regex_count = 0

            for rule in rules_list:
                cr = self._compile_rule(rule)
                if cr:
                    compiled.append(cr)
                    if isinstance(cr, LiteralKeywordRule):
                        literal_count += 1
                    else:
                        regex_count += 1

            # Convert to tuple for faster iteration
            self._global_rules = tuple(compiled)

            self.logger.debug(
                f"Compiled {len(self._global_rules)} global rules "
                f"({literal_count} literal, {regex_count} regex, PCRE2: {USING_PCRE2})"
            )

    def set_context(
        self, command_name: str, proxy_id: int = 0, full_command: str = ""
    ) -> bool:
        """
        Set the active context for highlighting for a specific proxy.

        This switches the highlighter to use command-specific rules in
        addition to global rules when the given command is detected.

        The command name is resolved via HighlightManager's trigger map,
        which maps aliases like "python3" -> "python" based on the
        "triggers" arrays defined in the JSON highlight files.

        Args:
            command_name: The command name (e.g., "ping", "docker", "python3").
                          Empty string or None resets to global rules only.
            proxy_id: The ID of the proxy to set context for.
            full_command: The full command line including arguments (for Pygments file highlighting).

        Returns:
            True if context changed, False if it was already set.
        """
        with self._lock:
            # Normalize empty/None to empty string
            if not command_name:
                resolved_context = ""
            else:
                # Check if command is in ignored list FIRST
                # If so, store the command name directly so it can be checked later
                if command_name.lower() in self._ignored_commands:
                    resolved_context = command_name.lower()
                else:
                    # Resolve command to canonical context name using trigger map
                    # This handles aliases like python3 -> python, pip3 -> pip, etc.
                    resolved_context = self._manager.get_context_for_command(
                        command_name
                    )
                    if not resolved_context:
                        # Command not in any context's triggers - use command name as-is
                        # This allows the ignored command check to work
                        resolved_context = command_name.lower()

            # Get current context for this proxy
            current_context = self._proxy_contexts.get(proxy_id, "")
            current_full_command = self._full_commands.get(proxy_id, "")

            # Always update full command if provided (needed for cat to get correct filename)
            if full_command:
                self._full_commands[proxy_id] = full_command
            elif proxy_id in self._full_commands:
                del self._full_commands[proxy_id]

            # Check if context actually changed
            if current_context == resolved_context:
                # Even if context didn't change, we may have updated full_command
                if full_command and full_command != current_full_command:
                    self.logger.debug(
                        f"Full command updated for proxy {proxy_id}: '{full_command[:50]}...'"
                    )
                    # Set skip flag since this is a new command execution
                    self._skip_first_output[proxy_id] = True
                return False

            self._proxy_contexts[proxy_id] = resolved_context

            # Set the skip flag to prevent highlighting the echoed command line
            # This flag will be consumed by the first data processing after Enter
            self._skip_first_output[proxy_id] = True

            if resolved_context:
                self.logger.debug(
                    f"Context changed for proxy {proxy_id}: '{current_context}' -> '{resolved_context}' (from '{command_name}')"
                )
            else:
                self.logger.debug(
                    f"Context cleared for proxy {proxy_id} (command '{command_name}' has no context)"
                )
            return True

    def get_full_command(self, proxy_id: int = 0) -> str:
        """Get the full command line for a specific proxy (for Pygments file highlighting)."""
        with self._lock:
            return self._full_commands.get(proxy_id, "")

    def get_context(self, proxy_id: int = 0) -> str:
        """Get the current context name for a specific proxy."""
        with self._lock:
            return self._proxy_contexts.get(proxy_id, "")

    def should_skip_first_output(self, proxy_id: int = 0) -> bool:
        """
        Check if first output should be skipped for highlighting.

        This is called when processing output to check if the output
        corresponds to the echoed command line that shouldn't be highlighted.
        The flag is consumed (cleared) after being checked.

        Args:
            proxy_id: The ID of the proxy to check.

        Returns:
            True if this is the first output after Enter and should be skipped.
        """
        with self._lock:
            if self._skip_first_output.get(proxy_id, False):
                self._skip_first_output[proxy_id] = False
                return True
            return False

    def clear_context(self, proxy_id: int = 0) -> None:
        """
        Clear the context for a specific proxy.

        This is used after a command completes to prevent re-processing
        on subsequent Enter key presses.
        """
        with self._lock:
            if proxy_id in self._proxy_contexts:
                old_context = self._proxy_contexts[proxy_id]
                del self._proxy_contexts[proxy_id]
                self.logger.debug(
                    f"Cleared context for proxy {proxy_id} (was: {old_context})"
                )
            if proxy_id in self._full_commands:
                del self._full_commands[proxy_id]

    def _compile_rules_for_context(
        self, context_name: str
    ) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
        """
        Compile rules for a specific context.

        This merges global rules with context-specific rules.
        """
        rules = self._manager.get_rules_for_context(context_name)
        self.logger.debug(f"Compiling {len(rules)} rules for context '{context_name}'")

        compiled = []
        for rule in rules:
            cr = self._compile_rule(rule)
            if cr:
                compiled.append(cr)

        return tuple(compiled)

    def _get_active_rules(
        self, context: str = ""
    ) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
        """
        Get the active compiled rules based on given context.

        Uses caching to avoid recompiling rules repeatedly.

        Args:
            context: The context name to get rules for.

        Returns:
            Tuple of CompiledRule or LiteralKeywordRule objects
        """
        # If no context or context-aware disabled, use global rules
        if not context or not self._manager.context_aware_enabled:
            return self._global_rules

        # Check cache
        if context in self._context_rules_cache:
            return self._context_rules_cache[context]

        # Compile and cache rules for this context
        context_rules = self._compile_rules_for_context(context)
        self._context_rules_cache[context] = context_rules

        return context_rules

    def highlight_text(self, text: str, proxy_id: int = 0) -> str:
        """
        Apply highlighting to text for a specific proxy.

        Uses optimized per-rule iteration with pre-filtering.
        Processes text line-by-line for streaming compatibility.

        Args:
            text: The text to highlight.
            proxy_id: The ID of the proxy to get context from.
        """
        if not text:
            return text

        # Fast path: get context and rules with minimal locking
        with self._lock:
            context = self._proxy_contexts.get(proxy_id, "")

            # Early return for ignored commands (tools with native coloring)
            # This preserves their ANSI colors and saves CPU
            if context and context.lower() in self._ignored_commands:
                return text

            rules = self._get_active_rules(context)

        if not rules:
            return text

        # Process outside the lock for better concurrency
        return self._apply_highlighting(text, rules)

    def highlight_line(self, line: str, proxy_id: int = 0) -> str:
        """
        Apply highlighting to a single line (streaming API).

        This is the preferred method for streaming data - call once per line
        as data arrives instead of buffering.

        Args:
            line: Single line of text to highlight.
            proxy_id: The ID of the proxy to get context from.
        """
        if not line:
            return line

        with self._lock:
            context = self._proxy_contexts.get(proxy_id, "")

            # Early return for ignored commands (tools with native coloring)
            # This preserves their ANSI colors and saves CPU
            if context and context.lower() in self._ignored_commands:
                return line

            rules = self._get_active_rules(context)

        if not rules:
            return line

        return self._apply_highlighting_to_line(line, rules)

    def _apply_highlighting(
        self,
        text: str,
        rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...],
    ) -> str:
        """
        Apply highlighting using optimized per-rule iteration.

        Args:
            text: The text to highlight
            rules: Tuple of CompiledRule or LiteralKeywordRule objects

        Returns:
            Text with ANSI color codes applied
        """
        # Split text into lines for streaming-friendly processing
        lines = text.split("\n")
        result_lines = []

        for line in lines:
            highlighted_line = self._apply_highlighting_to_line(line, rules)
            result_lines.append(highlighted_line)

        return "\n".join(result_lines)

    def _apply_highlighting_to_line(
        self,
        line: str,
        rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...],
    ) -> str:
        """
        Apply highlighting to a single line using per-rule iteration.

        Optimizations:
        - LiteralKeywordRule: O(1) set lookup + string.find() (no regex!)
        - CompiledRule: Pre-filtering skips regex when line cannot match
        - Tuple iteration is faster than list
        - Early termination on "stop" action
        - PCRE2 backend when available for regex rules

        Args:
            line: The line to highlight
            rules: Tuple of CompiledRule or LiteralKeywordRule objects

        Returns:
            Line with ANSI color codes applied
        """
        if not line:
            return line

        # Pre-compute lowercase line for matching (O(n) once)
        line_lower = line.lower()

        # Collect all matches: (start, end, ansi_color)
        matches: List[Tuple[int, int, str]] = []
        should_stop = False

        for rule in rules:
            if should_stop:
                break

            # Handle LiteralKeywordRule (optimized path - no regex!)
            if isinstance(rule, LiteralKeywordRule):
                # Quick check: any keyword might be in line?
                # This is O(k) where k is number of keywords, but very fast
                has_potential = False
                for kw in rule.keyword_tuple:
                    if kw in line_lower:
                        has_potential = True
                        break

                if not has_potential:
                    continue

                # Find all keyword matches with word boundaries
                rule_matches = rule.find_matches(line, line_lower)
                rule_matched = bool(rule_matches)
                matches.extend(rule_matches)

                if rule_matched and rule.action == "stop":
                    should_stop = True
                continue

            # Handle CompiledRule (regex path)
            # Pre-filter: fast check if line might match
            if rule.prefilter is not None:
                if not rule.prefilter(line_lower):
                    continue  # Skip this rule - pre-filter failed

            try:
                rule_matched = False
                for match in rule.pattern.finditer(line):
                    rule_matched = True

                    if rule.num_groups > 0:
                        # Multi-group pattern: color each group separately
                        for group_idx in range(1, rule.num_groups + 1):
                            group_start = match.start(group_idx)
                            group_end = match.end(group_idx)

                            # Skip if group didn't match
                            if group_start == -1 or group_end == -1:
                                continue

                            # Get color for this group (fallback to first color)
                            color_idx = group_idx - 1
                            if color_idx < len(rule.ansi_colors):
                                ansi_color = rule.ansi_colors[color_idx]
                            else:
                                ansi_color = rule.ansi_colors[0]

                            # Skip if color is empty (intentionally no coloring)
                            if not ansi_color:
                                continue

                            matches.append((group_start, group_end, ansi_color))
                    else:
                        # No capture groups: color entire match
                        start, end = match.start(), match.end()
                        if rule.ansi_colors and rule.ansi_colors[0]:
                            matches.append((start, end, rule.ansi_colors[0]))

                # Check if we should stop processing after this rule
                if rule_matched and rule.action == "stop":
                    should_stop = True

            except Exception as e:
                # Log at debug level to help diagnose pattern issues without flooding logs
                if hasattr(self, "logger"):
                    self.logger.debug(f"Rule pattern matching failed: {e}")
                continue

        if not matches:
            return line

        # Sort by start position, then by length (longer first)
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

        result = []
        last_end = 0
        covered_until = 0

        for start, end, color in matches:
            # Skip if already covered by previous match
            if start < covered_until:
                continue

            # Add text before this match
            if start > last_end:
                result.append(line[last_end:start])

            # Add colored match
            result.append(color)
            result.append(line[start:end])
            result.append(ANSI_RESET)

            last_end = end
            covered_until = end

        # Add remaining text
        if last_end < len(line):
            result.append(line[last_end:])

        return "".join(result)

    def is_enabled_for_type(self, terminal_type: str) -> bool:
        """Check if highlighting is enabled for the given terminal type."""
        if terminal_type == "local":
            return self._manager.enabled_for_local
        elif terminal_type in ("ssh", "sftp"):
            return self._manager.enabled_for_ssh
        return False


class ShellInputHighlighter:
    """
    Applies live syntax highlighting to shell commands as they are typed.

    Uses Pygments BashLexer to tokenize and colorize shell input in real-time.
    This works at the terminal level, so it applies to any shell (bash, zsh, etc.)
    even when connecting to remote servers or Docker containers where shell
    configuration cannot be changed.

    The highlighter tracks the current command buffer and applies colors
    when characters are echoed back through the PTY.

    Architecture:
    - Detects when the terminal is at a shell prompt (via OSC7)
    - Tracks typed characters and builds a command buffer
    - When Enter is pressed, the buffer is cleared
    - Each character echo is intercepted and colorized based on its context

    Features:
    - Real-time tokenization using Pygments BashLexer
    - Theme-aware colors using the configured Pygments style
    - Handles backspace, cursor movement, and control characters
    - Properly escapes ANSI sequences in the input
    """

    def __init__(self):
        self.logger = get_logger("ashyterm.terminal.shell_input")
        self._enabled = False
        self._lexer = None
        self._formatter = None
        self._theme = "monokai"

        # Per-proxy state for input tracking
        # Key: proxy_id, Value: current command buffer string
        self._command_buffers: Dict[int, str] = {}

        # Track if we're at a shell prompt (can type commands)
        # Key: proxy_id, Value: True if at prompt
        self._at_prompt: Dict[int, bool] = {}

        # Color palette from terminal color scheme
        self._palette: Optional[List[str]] = None
        self._foreground: str = "#ffffff"

        self._lock = threading.Lock()
        self._refresh_settings()

    def _refresh_settings(self) -> None:
        """Refresh settings from configuration."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            self._enabled = settings.get("shell_input_highlighting_enabled", False)

            # Get theme mode: "auto" or "manual"
            self._theme_mode = settings.get("shell_input_theme_mode", "auto")
            # Legacy theme (used when mode is "manual")
            self._theme = settings.get("shell_input_pygments_theme", "monokai")
            # Themes for auto mode
            self._dark_theme = settings.get("shell_input_dark_theme", "blinds-dark")
            self._light_theme = settings.get("shell_input_light_theme", "blinds-light")

            # Get terminal color scheme for background detection
            gtk_theme = settings.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = settings.get_color_scheme_data()
                self._palette = scheme.get("palette", [])
                self._foreground = scheme.get("foreground", "#ffffff")
                self._background = scheme.get("background", "#000000")
            else:
                self._palette = None
                self._foreground = "#ffffff"
                self._background = "#000000"

            if self._enabled:
                self._init_lexer()
                self.logger.info("Shell input highlighting enabled")
            else:
                self._lexer = None
                self._formatter = None
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input settings: {e}")
            self._enabled = False

    def _init_lexer(self) -> None:
        """Initialize Pygments lexer and formatter."""
        try:
            from pygments.lexers import BashLexer
            from pygments.formatters import Terminal256Formatter
            from pygments.styles import get_style_by_name
            from pygments.util import ClassNotFound

            self._lexer = BashLexer()

            # Determine which theme to use based on mode
            if self._theme_mode == "auto":
                # Auto mode: select theme based on background luminance
                is_light_bg = self._is_light_color(self._background)
                selected_theme = self._light_theme if is_light_bg else self._dark_theme
                self.logger.debug(
                    f"Auto mode: bg={self._background}, light={is_light_bg}, "
                    f"using theme={selected_theme}"
                )
            else:
                # Manual mode: use the legacy single theme setting
                selected_theme = self._theme

            # Create formatter with selected theme
            try:
                style = get_style_by_name(selected_theme)
            except ClassNotFound:
                # Fallback to monokai if theme not found
                style = get_style_by_name("monokai")
                self.logger.warning(
                    f"Theme '{selected_theme}' not found, falling back to monokai"
                )

            self._formatter = Terminal256Formatter(style=style)

            self.logger.debug(
                f"Shell input highlighter initialized with theme: {selected_theme}"
            )
        except ImportError as e:
            self.logger.warning(
                f"Pygments not available for shell input highlighting: {e}"
            )
            self._enabled = False
            self._lexer = None
            self._formatter = None
        except ImportError as e:
            self.logger.warning(
                f"Pygments not available for shell input highlighting: {e}"
            )
            self._enabled = False
            self._lexer = None
            self._formatter = None

    def _create_palette_formatter(self):
        """Create a formatter that uses terminal palette colors.

        Automatically selects colors with better contrast based on whether
        the terminal background is light or dark.

        Uses Terminal256Formatter with a custom style for compatibility with
        the highlighting code that expects style_string attribute.
        """
        from pygments.formatters import Terminal256Formatter
        from pygments.style import Style
        from pygments import token

        # Determine if background is light or dark
        # If foreground is light, background is likely dark
        is_dark_bg = self._is_light_color(self._foreground)

        if is_dark_bg:
            # Dark background - use lighter/brighter ANSI colors (256 color palette)
            # Using 256-color indices that map well to standard terminal colors
            class DarkBgStyle(Style):
                styles = {
                    token.Comment: "ansibrightblack",  # Gray
                    token.Comment.Preproc: "ansicyan",
                    token.Keyword: "ansiblue",
                    token.Keyword.Type: "ansiblue",
                    token.Name.Builtin: "ansigreen",
                    token.Name.Function: "ansigreen",
                    token.Name.Variable: "ansimagenta",
                    token.String: "ansicyan",
                    token.String.Backtick: "ansicyan",
                    token.String.Double: "ansicyan",
                    token.String.Single: "ansicyan",
                    token.Number: "ansimagenta",
                    token.Operator: "ansired",
                    token.Punctuation: "ansiwhite",
                }

            return Terminal256Formatter(style=DarkBgStyle)
        else:
            # Light background - use darker ANSI colors for contrast
            class LightBgStyle(Style):
                styles = {
                    token.Comment: "#586e75",  # Solarized base01 (dark gray)
                    token.Comment.Preproc: "#2aa198",  # Solarized cyan
                    token.Keyword: "#268bd2",  # Solarized blue
                    token.Keyword.Type: "#268bd2",
                    token.Name.Builtin: "#859900",  # Solarized green
                    token.Name.Function: "#859900",
                    token.Name.Variable: "#d33682",  # Solarized magenta
                    token.String: "#2aa198",  # Solarized cyan
                    token.String.Backtick: "#2aa198",
                    token.String.Double: "#2aa198",
                    token.String.Single: "#2aa198",
                    token.Number: "#d33682",  # Solarized magenta
                    token.Operator: "#cb4b16",  # Solarized orange (darker red)
                    token.Punctuation: "#073642",  # Solarized base02 (very dark)
                }

            return Terminal256Formatter(style=LightBgStyle)

    def _is_light_color(self, hex_color: str) -> bool:
        """Determine if a color is light based on its luminance."""
        try:
            hex_val = hex_color.lstrip("#")
            r = int(hex_val[0:2], 16) / 255
            g = int(hex_val[2:4], 16) / 255
            b = int(hex_val[4:6], 16) / 255

            # Calculate relative luminance (simplified)
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return luminance > 0.5
        except (ValueError, IndexError):
            return False

    def refresh_settings(self) -> None:
        """Public method to refresh settings (called when settings change)."""
        with self._lock:
            self._refresh_settings()

    @property
    def enabled(self) -> bool:
        """Check if shell input highlighting is enabled."""
        return self._enabled and self._lexer is not None

    def register_proxy(self, proxy_id: int) -> None:
        """Register a proxy for input tracking."""
        with self._lock:
            self._command_buffers[proxy_id] = ""
            # Start with True since terminal starts at shell prompt
            self._at_prompt[proxy_id] = True

    def unregister_proxy(self, proxy_id: int) -> None:
        """Unregister a proxy."""
        with self._lock:
            self._command_buffers.pop(proxy_id, None)
            self._at_prompt.pop(proxy_id, None)

    def set_at_prompt(self, proxy_id: int, at_prompt: bool) -> None:
        """
        Set whether the terminal is at a shell prompt.

        When at a prompt, typed characters are part of a command and will
        be highlighted. When not at a prompt (e.g., running a command),
        highlighting is disabled.
        """
        with self._lock:
            old_state = self._at_prompt.get(proxy_id, False)
            self._at_prompt[proxy_id] = at_prompt

            # Clear buffer when transitioning to prompt or away from it
            if old_state != at_prompt:
                self._command_buffers[proxy_id] = ""
                if at_prompt:
                    self.logger.debug(
                        f"Proxy {proxy_id}: At shell prompt, input highlighting active"
                    )

    def is_at_prompt(self, proxy_id: int) -> bool:
        """Check if terminal is at a shell prompt."""
        with self._lock:
            return self._at_prompt.get(proxy_id, False)

    def on_key_pressed(self, proxy_id: int, char: str, keyval: int) -> None:
        """
        Handle a key press event to update the command buffer.

        Called by the terminal when a printable character is typed.
        Special keys (backspace, delete, arrows) are handled separately.
        """
        if not self.enabled:
            return

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return

            buffer = self._command_buffers.get(proxy_id, "")

            # Handle control characters
            if keyval == 65288:  # GDK_KEY_BackSpace
                self._command_buffers[proxy_id] = buffer[:-1] if buffer else ""
            elif keyval in (65293, 65421):  # GDK_KEY_Return, GDK_KEY_KP_Enter
                # Clear buffer on Enter (command submitted)
                self._command_buffers[proxy_id] = ""
                self._at_prompt[proxy_id] = False  # No longer at prompt
            elif keyval == 65507 or keyval == 65508:  # Ctrl keys (Ctrl+C, etc.)
                # Clear buffer on Ctrl+C
                if char == "\x03":  # Ctrl+C
                    self._command_buffers[proxy_id] = ""
            elif len(char) == 1 and char.isprintable():
                # Regular printable character
                self._command_buffers[proxy_id] = buffer + char

    def clear_buffer(self, proxy_id: int) -> None:
        """Clear the command buffer for a proxy."""
        with self._lock:
            self._command_buffers[proxy_id] = ""

    def get_highlighted_char(self, proxy_id: int, char: str) -> str:
        """
        Get the highlighted version of a character being echoed.

        This is called when a character is echoed back from the PTY.
        It returns the character with appropriate ANSI color codes
        based on its context in the current command buffer.

        Args:
            proxy_id: The proxy ID
            char: The character being echoed

        Returns:
            The character with ANSI color codes, or the plain character
            if highlighting is disabled or not applicable.
        """
        if not self.enabled:
            return char

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return char

            buffer = self._command_buffers.get(proxy_id, "")
            if not buffer:
                return char

            # Find position of this char in the buffer
            # This is a simplified approach - we highlight based on the full buffer
            return self._highlight_buffer_char(buffer, char)

    def _highlight_buffer_char(self, buffer: str, char: str) -> str:
        """
        Get the color for a character based on its position in the buffer.

        Tokenizes the full buffer and finds the token containing the last
        character to determine its color.
        """
        if not self._lexer or not self._formatter:
            return char

        try:
            from pygments import highlight

            # Highlight the full buffer
            highlighted = highlight(buffer, self._lexer, self._formatter)

            # For single char, just return it with color from the end of buffer
            # This is a simplified approach that colors the whole buffer
            if len(buffer) == 1:
                return highlighted.rstrip("\n")

            # Return just the character (the actual coloring happens in
            # highlight_input_line for full line redraw)
            return char

        except Exception:
            return char

    def highlight_input_line(self, proxy_id: int, line: str) -> str:
        """
        Highlight a full input line.

        This is used when redrawing the input line (e.g., after cursor
        movement or completion).

        Args:
            proxy_id: The proxy ID
            line: The command line text to highlight

        Returns:
            The line with ANSI color codes applied
        """
        if not self.enabled or not self._lexer or not self._formatter:
            return line

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return line

        try:
            from pygments import highlight

            highlighted = highlight(line, self._lexer, self._formatter)
            return highlighted.rstrip("\n")
        except Exception:
            return line

    def get_current_buffer(self, proxy_id: int) -> str:
        """Get the current command buffer for a proxy."""
        with self._lock:
            return self._command_buffers.get(proxy_id, "")


# Singleton for shell input highlighter
_shell_input_highlighter_instance: Optional[ShellInputHighlighter] = None
_shell_input_highlighter_lock = threading.Lock()


def get_shell_input_highlighter() -> ShellInputHighlighter:
    """Get the global ShellInputHighlighter singleton instance."""
    global _shell_input_highlighter_instance
    if _shell_input_highlighter_instance is None:
        with _shell_input_highlighter_lock:
            if _shell_input_highlighter_instance is None:
                _shell_input_highlighter_instance = ShellInputHighlighter()
    return _shell_input_highlighter_instance


class HighlightedTerminalProxy:
    """
    A proxy that intercepts terminal output and applies syntax highlighting.
    Robust against Local Terminal race conditions.

    Supports context-aware highlighting via the highlighter property.
    Also supports Pygments integration for cat/file and help output highlighting.
    """

    # Class-level counter for unique proxy IDs (fallback if not provided)
    _next_proxy_id = 1
    _id_lock = threading.Lock()

    def __init__(
        self,
        terminal: Vte.Terminal,
        terminal_type: str = "local",
        proxy_id: Optional[int] = None,
    ):
        """
        Initialize a highlighted terminal proxy.
        """
        self.logger = get_logger("ashyterm.terminal.proxy")

        if proxy_id is not None:
            self._proxy_id = proxy_id
        else:
            with HighlightedTerminalProxy._id_lock:
                self._proxy_id = HighlightedTerminalProxy._next_proxy_id
                HighlightedTerminalProxy._next_proxy_id += 1
            self.logger.warning(
                f"HighlightedTerminalProxy created without explicit proxy_id, "
                f"auto-generated ID {self._proxy_id}. This may cause context detection issues."
            )

        self._terminal_ref = weakref.ref(terminal)
        self._terminal_type = terminal_type
        self._highlighter = get_output_highlighter()
        self._shell_input_highlighter = get_shell_input_highlighter()

        self._highlighter.register_proxy(self._proxy_id)
        self._shell_input_highlighter.register_proxy(self._proxy_id)

        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._io_watch_id: Optional[int] = None

        self._columns_handler_id: Optional[int] = None
        self._rows_handler_id: Optional[int] = None
        self._destroy_handler_id: Optional[int] = None

        self._running = False
        self._widget_destroyed = False

        self._lock = threading.Lock()
        self._is_alt_screen = False
        self._child_pid: Optional[int] = None

        self._sequence_counter = 0
        self._pending_outputs: Dict[int, bytes] = {}
        self._next_sequence_to_feed = 0
        self._output_lock = threading.Lock()

        self._line_queue: deque = deque()
        self._queue_processing = False

        # Buffer for partial lines
        self._partial_line_buffer: bytes = b""

        # Burst detection counter
        # Tracks consecutive large chunks to detect file dumps vs commands
        self._burst_counter = 0

        # Pygments state for cat command highlighting
        self._cat_filename: Optional[str] = None
        self._cat_bytes_processed: int = 0
        self._cat_limit_reached: bool = False

        self._cat_filename: Optional[str] = None

        self._input_highlight_buffer = ""
        self._at_shell_prompt = True
        self._need_color_reset = False

        if terminal:
            self._destroy_handler_id = terminal.connect(
                "destroy", self._on_widget_destroy
            )

    @property
    def proxy_id(self) -> int:
        """Get the unique proxy ID for this instance."""
        return self._proxy_id

    @property
    def highlighter(self) -> OutputHighlighter:
        """Get the highlighter instance for context management."""
        return self._highlighter

    @property
    def shell_input_highlighter(self) -> ShellInputHighlighter:
        """Get the shell input highlighter instance."""
        return self._shell_input_highlighter

    @property
    def child_pid(self) -> Optional[int]:
        """Get the child process ID (shell PID)."""
        return self._child_pid

    @property
    def slave_fd(self) -> Optional[int]:
        """Get the slave file descriptor (for process detection)."""
        return self._slave_fd

    @property
    def _terminal(self) -> Optional[Vte.Terminal]:
        if self._widget_destroyed:
            return None
        return self._terminal_ref()

    def _on_widget_destroy(self, widget):
        """Called immediately when the GTK widget is being destroyed."""
        # Mark as destroyed IMMEDIATELY so no other thread tries to access it
        self._widget_destroyed = True
        self._running = False
        # We do NOT call stop() logic that touches the widget here.
        # We only clean up our Python-side IO watches.
        self._cleanup_io_watch()

    def create_pty(self) -> Tuple[int, int]:
        master_fd, slave_fd = pty.openpty()
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._master_fd = master_fd
        self._slave_fd = slave_fd
        self._setup_pty_attrs(slave_fd)

        return master_fd, slave_fd

    def _setup_pty_attrs(self, slave_fd: int) -> None:
        try:
            attrs = termios.tcgetattr(slave_fd)
            attrs[0] |= termios.ICRNL
            if hasattr(termios, "IUTF8"):
                attrs[0] |= termios.IUTF8
            attrs[1] |= termios.OPOST | termios.ONLCR
            attrs[3] |= (
                termios.ISIG
                | termios.ICANON
                | termios.ECHO
                | termios.ECHOE
                | termios.ECHOK
                | termios.IEXTEN
            )
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except Exception:
            pass

    def set_window_size(self, rows: int, cols: int) -> None:
        # If destroyed, do nothing.
        if rows <= 0 or cols <= 0 or self._master_fd is None or self._widget_destroyed:
            return

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            if self._child_pid:
                os.kill(self._child_pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError, Exception):
            pass

    def start(self, child_pid: int) -> bool:
        if self._running or self._widget_destroyed:
            return False

        if self._master_fd is None:
            return False

        term = self._terminal
        if term is None:
            return False

        self._child_pid = child_pid

        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None

        try:
            # VTE takes ownership of FD here.
            vte_pty = Vte.Pty.new_foreign_sync(self._master_fd)

            if vte_pty:
                term.set_pty(vte_pty)
            else:
                return False

            # Reset sequence counters
            self._sequence_counter = 0
            self._pending_outputs = {}
            self._next_sequence_to_feed = 0

            # Setup IO Watch
            self._io_watch_id = GLib.io_add_watch(
                self._master_fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self._on_pty_readable,
            )

            self._columns_handler_id = term.connect(
                "notify::columns", self._on_terminal_resize
            )
            self._rows_handler_id = term.connect(
                "notify::rows", self._on_terminal_resize
            )

            # Initial sync
            self._on_terminal_resize(term, None)

            self._running = True
            return True

        except Exception as e:
            self.logger.error(f"Failed to start highlight proxy: {e}")
            # Clean up FDs that weren't transferred to VTE on failure
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None
            self.stop()
            return False

    def _cleanup_io_watch(self):
        """Helper to safely remove the GLib IO watch."""
        with self._lock:
            if self._io_watch_id is not None:
                try:
                    GLib.source_remove(self._io_watch_id)
                except Exception:
                    pass
                self._io_watch_id = None

    def stop(self, from_destroy: bool = False) -> None:
        """
        Stops the proxy.
        """
        self._running = False

        self._cleanup_io_watch()

        with self._output_lock:
            self._pending_outputs.clear()
        self._line_queue.clear()
        self._queue_processing = False
        self._partial_line_buffer = b""
        self._burst_counter = 0

        self._cat_filename = None
        self._input_highlight_buffer = ""
        self._at_shell_prompt = False

        self._highlighter.unregister_proxy(self._proxy_id)
        self._shell_input_highlighter.unregister_proxy(self._proxy_id)

        if from_destroy or self._widget_destroyed:
            self._terminal_ref = None
            return

        with self._lock:
            term = self._terminal_ref()

            if term:
                if self._columns_handler_id:
                    try:
                        if term.handler_is_connected(self._columns_handler_id):
                            term.disconnect(self._columns_handler_id)
                    except Exception:
                        pass
                    self._columns_handler_id = None

                if self._rows_handler_id:
                    try:
                        if term.handler_is_connected(self._rows_handler_id):
                            term.disconnect(self._rows_handler_id)
                    except Exception:
                        pass
                    self._rows_handler_id = None

            self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None

            self._terminal_ref = None

    def _update_alt_screen_state(self, data: bytes) -> bool:
        """
        Check for Alternate Screen buffer switches.
        Returns True if state changed.
        """
        # Common sequences for entering/exiting alt screen (vim, fzf, htop, etc)
        # \x1b[?1049h : Enable Alt Screen
        # \x1b[?1049l : Disable Alt Screen
        # \x1b[?47h   : Enable Alt Screen (Legacy)
        # \x1b[?47l   : Disable Alt Screen (Legacy)

        changed = False

        # Check for enable patterns
        if b"\x1b[?1049h" in data or b"\x1b[?47h" in data or b"\x1b[?1047h" in data:
            if not self._is_alt_screen:
                self._is_alt_screen = True
                changed = True

        # Check for disable patterns
        # Note: We check disable AFTER enable in case both are in the same chunk (rare but possible)
        if b"\x1b[?1049l" in data or b"\x1b[?47l" in data or b"\x1b[?1047l" in data:
            if self._is_alt_screen:
                self._is_alt_screen = False
                changed = True

        return changed

    def _on_pty_readable(self, fd: int, condition: GLib.IOCondition) -> bool:
        # 1. Fail fast if stopped or destroyed
        if not self._running or self._widget_destroyed:
            self._io_watch_id = None
            return False

        # 2. Check errors BEFORE trying to read
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            self._io_watch_id = None
            return False

        try:
            # 3. Try read - use 4KB buffer (standard page size usually)
            # If we get a full buffer, it's likely bulk output.
            data = os.read(fd, 4096)
            if not data:
                return True  # Empty read, keep waiting

            # 4. Verify widget is alive before feeding
            term = self._terminal
            if term is None:
                self._io_watch_id = None
                return False

            try:
                if not term.get_realized():
                    return False
            except Exception:
                self._widget_destroyed = True
                return False

            # 5. Processing logic
            data_len = len(data)
            
            # Optimization: Skip alt screen check on small packets to save CPU
            if data_len > 10:
                self._update_alt_screen_state(data)

            try:
                if self._is_alt_screen:
                    term.feed(data)
                elif (
                    self._highlighter.is_enabled_for_type(self._terminal_type)
                    or self._shell_input_highlighter.enabled
                ):
                    # Get context with lock
                    with self._highlighter._lock:
                        context = self._highlighter._proxy_contexts.get(
                            self._proxy_id, ""
                        )
                        is_ignored = (
                            context
                            and context.lower() in self._highlighter._ignored_commands
                        )

                    # Check for cat syntax highlighting
                    is_cat_context = context and context.lower() == "cat"

                    if is_cat_context:
                        # Check if cat colorization is enabled
                        from ..settings.manager import get_settings_manager
                        settings = get_settings_manager()
                        if settings.get("cat_colorization_enabled", True):
                            self._process_cat_output(data, term)
                        else:
                            # Cat colorization disabled, feed data directly
                            term.feed(data)
                    elif is_ignored:
                        # PERFORMANCE FIX FOR IGNORED COMMANDS
                        # Only attempt prompt detection on small chunks.
                        # If we receive a large chunk (>1024 bytes), it is definitely output,
                        # not a prompt, and not user input. Skip decoding entirely.
                        if self._shell_input_highlighter.enabled and data_len < 1024:
                            text = data.decode("utf-8", errors="replace")
                            
                            # Only log if it looks like actual interaction
                            if self._at_shell_prompt:
                                has_osc7 = "\x1b]7;" in text or "\033]7;" in text
                                self.logger.debug(
                                    f"Proxy {self._proxy_id}: Data [IGNORED ctx] (len={data_len}, osc7={has_osc7})"
                                )

                            prompt_detected = self._check_and_update_prompt_state(text)
                            
                            if self._at_shell_prompt:
                                highlighted = self._apply_shell_input_highlighting(
                                    text, term
                                )
                                if highlighted is not None:
                                    return True

                        term.feed(data)
                    else:
                        # Stream data line-by-line
                        self._process_data_streaming(data, term)
                else:
                    term.feed(data)
            except Exception:
                self._widget_destroyed = True
                self._io_watch_id = None
                return False

            return True

        except OSError:
            self._io_watch_id = None
            return False
        except Exception as e:
            self.logger.error(f"PTY read error: {e}")
            return True

    def _process_cat_output(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Process cat output through Pygments for syntax highlighting.
        Includes a safety limit to prevent freezing on large files.
        """
        # Constante de limite: 1MB (1024 * 1024 bytes)
        CAT_HIGHLIGHT_LIMIT = 1048576

        try:
            data_len = len(data)

            # --- SAFETY LIMIT CHECK ---
            # Se já passamos do limite ou se este chunk vai estourar o limite
            if self._cat_limit_reached or (
                self._cat_bytes_processed + data_len > CAT_HIGHLIGHT_LIMIT
            ):
                if not self._cat_limit_reached:
                    self.logger.debug(
                        f"CAT: Limit of {CAT_HIGHLIGHT_LIMIT} bytes reached. Disabling highlighting for this stream."
                    )
                    self._cat_limit_reached = True

                # Feed raw data
                term.feed(data)

                # CRITICAL: Even in raw mode, we MUST detect when the command finishes.
                # Check for OSC7 (directory tracking) which signals prompt return.
                if b"\x1b]7;" in data or b"\033]7;" in data:
                    self.logger.debug(
                        "CAT (Raw Mode): OSC7 detected, resetting context."
                    )
                    self._highlighter.clear_context(self._proxy_id)
                    self._reset_cat_state()

                    # Reset input buffer as we are back at prompt
                    if self._input_highlight_buffer:
                        self._input_highlight_buffer = ""
                        self._prev_shell_input_token_type = None
                        self._prev_shell_input_token_len = 0
                return

            # Increment counter
            self._cat_bytes_processed += data_len

            # --- NORMAL PROCESSING ---
            text = data.decode("utf-8", errors="replace")

            # FIX: Aggressively remove NULL bytes
            text = text.replace("\x00", "")

            if not text:
                term.feed(data)
                return

            # Check for shell input control sequences (backspace/edits)
            if text in ("\x08\x1b[K", "\x08 \x08") or (
                text.startswith("\x08") and len(text) <= 5
            ):
                self.logger.debug(
                    f"CAT: Detected shell input control sequence, exiting cat mode"
                )
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
                self._input_highlight_buffer = ""
                self._prev_shell_input_token_type = None
                self._prev_shell_input_token_len = 0
                term.feed(data)
                return

            # Get filename from command
            full_command = self._highlighter.get_full_command(self._proxy_id)
            new_filename = self._extract_filename_from_cat_command(full_command) or ""

            # Reset state if filename changed
            if new_filename != self._cat_filename:
                self._cat_filename = new_filename
                self._pygments_lexer = None
                self._content_buffer = []
                self._cat_lines_processed = 0
                self._pending_lines = []
                self._php_in_multiline_comment = False

                import os.path

                _, ext = os.path.splitext(new_filename)
                self._pygments_needs_content_detection = not ext and new_filename

            # Initialize cat queue if needed
            if not hasattr(self, "_cat_queue"):
                from collections import deque

                self._cat_queue = deque()
                self._cat_queue_processing = False

            # Process lines
            lines = text.splitlines(keepends=True)
            skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

            import re

            ansi_color_pattern = re.compile(r"\x1b\[[0-9;]*m")

            for i, line in enumerate(lines):
                # Check for embedded prompt (OSC7/OSC0)
                prompt_split_idx = -1
                if "\x1b]7;" in line:
                    prompt_split_idx = line.find("\x1b]7;")
                elif "\x1b]0;" in line:
                    prompt_split_idx = line.find("\x1b]0;")

                if prompt_split_idx > 0:
                    content_part = line[:prompt_split_idx]
                    prompt_part = line[prompt_split_idx:]

                    highlighted = self._highlight_line_with_pygments(
                        content_part, self._cat_filename
                    )
                    self._cat_queue.append(
                        highlighted.encode("utf-8", errors="replace")
                    )
                    self._cat_queue.append(b"__PROMPT_DETECTED__")
                    self._cat_queue.append(
                        prompt_part.encode("utf-8", errors="replace")
                    )
                    continue

                content, ending = self._split_line_ending(line)

                if skip_first and i == 0:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Check for shell prompt
                lines_done = getattr(self, "_cat_lines_processed", 0)
                is_potential_prompt = lines_done > 0 or (
                    len(content) < 30 and "$" in content
                )

                if is_potential_prompt and self._is_shell_prompt(content):
                    self._cat_queue.append(b"__PROMPT_DETECTED__")
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Skip pure ANSI control sequences
                clean_content = re.sub(r"\x1b\[\??[0-9;]*[a-zA-Z]", "", content).strip()
                if not clean_content and (not content or content.startswith("\x1b")):
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Highlight content
                has_ansi_colors = bool(ansi_color_pattern.search(content))
                is_content = bool(content.strip())

                if is_content and not has_ansi_colors:
                    highlighted = self._highlight_line_with_pygments(
                        content, self._cat_filename
                    )

                    current_lexer = getattr(self, "_pygments_lexer", None)
                    if current_lexer is not None:
                        # Flush pending
                        pending = getattr(self, "_pending_lines", [])
                        if pending:
                            for pending_content, pending_ending in pending:
                                pending_highlighted = (
                                    self._highlight_line_with_pygments(
                                        pending_content, self._cat_filename
                                    )
                                )
                                self._cat_queue.append(
                                    (pending_highlighted + pending_ending).encode(
                                        "utf-8", errors="replace"
                                    )
                                )
                            self._pending_lines = []

                        self._cat_queue.append(
                            (highlighted + ending).encode("utf-8", errors="replace")
                        )
                    else:
                        # Buffer
                        pending = getattr(self, "_pending_lines", [])
                        pending.append((content, ending))
                        self._pending_lines = pending

                    self._cat_lines_processed = lines_done + 1
                else:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    if is_content:
                        self._cat_lines_processed = lines_done + 1

            # Process batch
            if self._cat_queue and not self._cat_queue_processing:
                self._process_cat_queue_batch(term, immediate=True)
                if self._cat_queue:
                    self._cat_queue_processing = True
                    GLib.idle_add(self._process_cat_queue, term)

        except Exception as e:
            self.logger.error(f"Cat highlighting error: {e}")
            term.feed(data)

    def _is_shell_prompt(self, line: str) -> bool:
        """
        Detect if a line contains a shell prompt (indicating command finished).

        Detection strategy:
        1. OSC7 escape sequence (most reliable) - indicates shell is ready
        2. Traditional prompt patterns - user@host:path$ or similar
        3. Simple prompt terminators at end of line (strict)

        OSC7 format: ESC ] 7 ; file://hostname/path BEL
        or: ESC ] 7 ; file://hostname/path ESC \

        Args:
            line: Line content without line ending

        Returns:
            True if line appears to contain a shell prompt
        """
        # Don't detect prompts on very short lines or empty lines
        if len(line) < 3:
            return False

        # Primary detection: OSC7 escape sequence
        # Format: \x1b]7; (ESC ] 7 ;) followed by file:// URI
        # This sequence is sent AFTER the command completes, not during echo
        #
        # IMPORTANT: Only consider this as a prompt if the line STARTS with OSC7
        # or only contains control sequences before OSC7. This prevents false positives
        # when file content and prompt are sent in the same buffer.
        osc7_pos = -1
        if "\x1b]7;" in line:
            osc7_pos = line.find("\x1b]7;")
        elif "\033]7;" in line:
            osc7_pos = line.find("\033]7;")

        if osc7_pos >= 0 and "file://" in line:
            # Check what's before the OSC7
            prefix = line[:osc7_pos]
            # Only accept as prompt if prefix is empty or only contains
            # control characters (\x00, \r, \n) and ANSI sequences
            import re

            # Remove ANSI color/control sequences
            clean_prefix = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", prefix)
            clean_prefix = re.sub(r"\x1b\][0-9]+;[^\x07\x1b]*[\x07]", "", clean_prefix)
            clean_prefix = (
                clean_prefix.replace("\x00", "").replace("\r", "").replace("\n", "")
            )

            if not clean_prefix.strip():
                self.logger.debug(
                    f"_is_shell_prompt: OSC7 detected at start/clean position in line: {line[:80]!r}"
                )
                return True
            else:
                self.logger.debug(
                    f"_is_shell_prompt: OSC7 found but has content before it ({clean_prefix[:30]!r}), ignoring"
                )
                return False

        # Also check for OSC0 (title setting), often sent with prompt
        # Format: \x1b]0; (ESC ] 0 ;) - sets window title
        # Apply same logic - only accept if at start
        osc0_pos = -1
        if "\x1b]0;" in line:
            osc0_pos = line.find("\x1b]0;")
        elif "\033]0;" in line:
            osc0_pos = line.find("\033]0;")

        if osc0_pos >= 0:
            import re

            prefix = line[:osc0_pos]
            clean_prefix = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", prefix)
            clean_prefix = re.sub(r"\x1b\][0-9]+;[^\x07\x1b]*[\x07]", "", clean_prefix)
            clean_prefix = (
                clean_prefix.replace("\x00", "").replace("\r", "").replace("\n", "")
            )

            if not clean_prefix.strip():
                self.logger.debug(
                    f"_is_shell_prompt: OSC0 detected at start/clean position in line: {line[:80]!r}"
                )
                return True
            else:
                self.logger.debug(
                    f"_is_shell_prompt: OSC0 found but has content before it, ignoring"
                )
                return False

        # Fallback: Traditional prompt detection
        # Strip ANSI escape sequences to get clean text
        import re

        # Remove various ANSI sequences:
        # - Color codes: \x1b[...m
        # - Mode changes: \x1b[?...h, \x1b[?...l
        # - Cursor/erase: \x1b[...A, \x1b[...B, etc.
        clean_line = re.sub(r"\x1b\[\??[0-9;]*[a-zA-Z]", "", line)
        # Also remove NULL bytes
        clean_line = clean_line.replace("\x00", "")
        # Only strip leading whitespace to preserve trailing space in prompts like "sh-5.3$ "
        clean_line_stripped = clean_line.strip()
        clean_line = clean_line.lstrip()

        # Check if line ends with common prompt terminators WITH trailing space
        # This is the most reliable pattern: "user@host:path$ " or "# "
        if (
            clean_line.endswith("$ ")
            or clean_line.endswith("# ")
            or clean_line.endswith("% ")
        ):
            self.logger.debug(
                f"_is_shell_prompt: Traditional prompt with space detected: {clean_line[:60]!r}"
            )
            return True

        # Check prompt terminators WITHOUT trailing space, but require @
        # This handles prompts like "user@host:path$" without space
        # The @ requirement helps avoid false positives from file content
        if clean_line_stripped.endswith("$") or clean_line_stripped.endswith("%"):
            if "@" in clean_line_stripped:
                self.logger.debug(
                    f"_is_shell_prompt: Traditional prompt with @ detected: {clean_line_stripped[:60]!r}"
                )
            return True

        # For # without space, be VERY strict - require user@host pattern
        # to avoid matching shell comments
        if clean_line_stripped.endswith("#") and "@" in clean_line_stripped:
            # Extra check: should have pattern like "user@host" before #
            import re

            if re.search(r"\w+@\w+.*#$", clean_line_stripped):
                self.logger.debug(
                    f"_is_shell_prompt: Root prompt with user@host detected: {clean_line_stripped[:60]!r}"
                )
                return True

        # PowerLine prompts: only match specific Unicode characters at end
        # Do NOT match > alone as it's too common in file content
        stripped = clean_line.rstrip()
        if stripped and stripped[-1] in ("➜", "❯", "»"):
            self.logger.debug(
                f"_is_shell_prompt: Powerline prompt detected: {clean_line[:60]!r}"
            )
            return True

        # For > character, require it to be preceded by a space or prompt-like pattern
        # This avoids matching HTML tags, URLs, etc.
        if stripped and stripped.endswith("> "):
            # Only if preceded by typical prompt structure (path, git branch, etc.)
            if any(c in stripped for c in ["~", "/"]):
                self.logger.debug(
                    f"_is_shell_prompt: > prompt with path detected: {clean_line[:60]!r}"
                )
                return True

        return False

    def _split_line_ending(self, line: str) -> tuple:
        """Split line into content and ending, normalizing to CRLF for terminal."""
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        elif line.endswith("\n"):
            return line[:-1], "\r\n"  # Normalize to CRLF
        elif line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _is_light_background(self) -> bool:
        """Check if the terminal background is light using luminance calculation."""
        try:
            terminal = self._terminal
            if terminal is None:
                return False

            # Get background color
            bg_rgba = terminal.get_color_background_for_draw()
            if bg_rgba is None:
                return False

            # Calculate luminance using standard formula
            r = bg_rgba.red
            g = bg_rgba.green
            b = bg_rgba.blue
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return luminance > 0.5
        except Exception:
            return False

    def _get_pygments_theme(self) -> str:
        """Get the configured Pygments theme from settings, with auto mode support."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            mode = settings.get("cat_theme_mode", "auto")

            if mode == "auto":
                # Auto mode: select theme based on background luminance
                if self._is_light_background():
                    return settings.get("cat_light_theme", "blinds-light").lower()
                else:
                    return settings.get("cat_dark_theme", "blinds-dark").lower()
            else:
                # Manual mode: use the single selected theme
                return settings.get("pygments_theme", "monokai").lower()
        except Exception:
            return "blinds-dark"

    def _detect_lexer_from_shebang(self, content: str):
        """
        Detect the lexer from content using Pygments' guess_lexer.

        Uses Pygments' guess_lexer to analyze the content, which internally
        handles shebang detection via lexer analyse_text() methods. This is
        more reliable than manual interpreter mapping.

        Args:
            content: Content to analyze (can be single line or multiple lines)

        Returns:
            A Pygments lexer if detected, None otherwise
        """
        if not content:
            return None

        try:
            from pygments.lexers import guess_lexer, TextLexer
            from pygments.util import ClassNotFound

            try:
                lexer = guess_lexer(content)
                # Only accept non-TextLexer results
                if not isinstance(lexer, TextLexer):
                    return lexer
            except ClassNotFound:
                pass

            return None
        except ImportError:
            return None

    def _highlight_line_with_pygments(self, line: str, filename: str) -> str:
        """
        Highlight a single line using Pygments.

        For PHP files, we track multi-line comment state manually to ensure
        lines inside /* ... */ blocks are highlighted as comments.

        Args:
            line: Single line of text (without line ending)
            filename: Filename for lexer detection

        Returns:
            Highlighted line with ANSI escape codes
        """
        try:
            from pygments import highlight
            from pygments.lexers import (
                TextLexer,
                guess_lexer,
                get_lexer_by_name,
                get_lexer_for_filename,
            )
            from pygments.formatters import Terminal256Formatter
            from pygments.util import ClassNotFound
            from pygments.styles import get_style_by_name

            # Get or create lexer
            current_lexer = getattr(self, "_pygments_lexer", None)
            needs_content_detection = getattr(
                self, "_pygments_needs_content_detection", False
            )
            is_php = filename and filename.lower().endswith(".php")

            # Detect lexer if we don't have one
            if current_lexer is None:
                lexer_found = False

                # Try filename-based detection first
                if filename and not needs_content_detection:
                    try:
                        self._pygments_lexer = get_lexer_for_filename(filename)
                        lexer_found = True

                        # For PHP, use startinline=True so code is recognized without <?php
                        if is_php:
                            from pygments.lexers import PhpLexer

                            self._pygments_lexer = PhpLexer(startinline=True)
                            # Initialize multi-line comment tracking for PHP
                            self._php_in_multiline_comment = False

                    except ClassNotFound:
                        # Unknown extension - enable content detection as fallback
                        self._pygments_needs_content_detection = True
                        needs_content_detection = True

                # Content-based detection (for files without extension OR unknown extension)
                if needs_content_detection and not lexer_found:
                    # Initialize buffer if needed
                    if not hasattr(self, "_content_buffer"):
                        self._content_buffer = []

                    # Add line to buffer (skip ANSI sequences)
                    # Strip NULL chars and control chars from terminal
                    clean = line.strip().lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08")
                    if clean and not clean.startswith("\x1b"):
                        self._content_buffer.append(clean)

                        # Try shebang detection on first line
                        if len(self._content_buffer) == 1 and clean.startswith("#!"):
                            shebang = clean.lower()
                            # Check for shell interpreters
                            is_shell = any(
                                s in shebang
                                for s in [
                                    "bash",
                                    "/sh",
                                    " sh",
                                    "zsh",
                                    "ksh",
                                    "dash",
                                    "fish",
                                ]
                            )
                            if is_shell:
                                self._pygments_lexer = get_lexer_by_name("bash")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "python" in shebang:
                                self._pygments_lexer = get_lexer_by_name("python")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "perl" in shebang:
                                self._pygments_lexer = get_lexer_by_name("perl")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "ruby" in shebang:
                                self._pygments_lexer = get_lexer_by_name("ruby")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "node" in shebang:
                                self._pygments_lexer = get_lexer_by_name("javascript")
                                self._pygments_needs_content_detection = False
                                lexer_found = True

                    # Try guess_lexer after 3+ lines
                    if not lexer_found and len(self._content_buffer) >= 3:
                        try:
                            content = "\n".join(self._content_buffer)
                            guessed = guess_lexer(content)
                            if not isinstance(guessed, TextLexer):
                                self._pygments_lexer = guessed
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                        except Exception:
                            pass

                    # Give up after 10 lines - use TextLexer (no color)
                    if not lexer_found and len(self._content_buffer) >= 10:
                        self._pygments_lexer = TextLexer()
                        self._pygments_needs_content_detection = False
                        self._content_buffer = []

                # Update current_lexer reference
                current_lexer = getattr(self, "_pygments_lexer", None)

            # Still no lexer? Return plain text
            if current_lexer is None:
                return line

            # Get or create formatter - also recreate if theme changed
            formatter = getattr(self, "_pygments_formatter", None)
            current_theme = self._get_pygments_theme()
            cached_theme = getattr(self, "_pygments_cached_theme", None)

            if formatter is None or cached_theme != current_theme:
                try:
                    style = get_style_by_name(current_theme)
                except ClassNotFound:
                    style = get_style_by_name("monokai")
                self._pygments_formatter = Terminal256Formatter(style=style)
                self._pygments_cached_theme = current_theme
                formatter = self._pygments_formatter

            # For PHP: Track multi-line comments manually
            # Pygments with startinline=True doesn't track state between lines
            is_php = filename and filename.lower().endswith(".php")
            if is_php:
                in_comment = getattr(self, "_php_in_multiline_comment", False)

                # Check if line opens or closes a multi-line comment
                # Strip the line for checking, but preserve original for highlighting
                stripped = line.strip()

                if in_comment:
                    # We're inside a comment - check if it closes
                    if "*/" in line:
                        # Comment closes on this line
                        self._php_in_multiline_comment = False
                        # Let Pygments try to highlight - it may do partial job
                    else:
                        # Still inside comment - apply comment color directly
                        # Get comment color from the style (usually gray/green)
                        comment_color = "\x1b[38;5;245m"  # Gray (monokai comment color)
                        reset = "\x1b[39m"
                        return f"{comment_color}{line}{reset}"
                else:
                    # Not in comment - check if one starts
                    if "/*" in line:
                        # Check if it also closes on this line
                        start_pos = line.find("/*")
                        end_pos = line.find("*/", start_pos + 2)
                        if end_pos == -1:
                            # Comment starts but doesn't close - track it
                            self._php_in_multiline_comment = True
                    # Also check for /** docblock
                    elif "/**" in line:
                        start_pos = line.find("/**")
                        end_pos = line.find("*/", start_pos + 3)
                        if end_pos == -1:
                            self._php_in_multiline_comment = True

            # Highlight using Pygments
            return highlight(line, current_lexer, formatter).rstrip("\n")

        except Exception as e:
            self.logger.error(f"Highlighting error: {e}")
            return line

    def _process_cat_queue_batch(
        self, term: Vte.Terminal, immediate: bool = False
    ) -> bool:
        """
        Process a batch of lines from the cat queue.

        Args:
            term: VTE terminal to feed output to
            immediate: If True, process smaller batch for immediate display

        Returns:
            True if prompt was detected (signals end of output)
        """
        queue = getattr(self, "_cat_queue", None)
        if not queue:
            return False

        # Smaller batch for immediate display, larger for background
        batch_size = 10 if immediate else 30

        lines_to_feed = []
        prompt_detected = False
        remaining_after_prompt = []

        for _ in range(batch_size):
            if not queue:
                break
            try:
                line_data = queue.popleft()

                # Check for prompt marker
                if line_data == b"__PROMPT_DETECTED__":
                    prompt_detected = True
                    self.logger.debug(
                        f"CAT QUEUE: Prompt marker found, clearing context"
                    )
                    # Don't break - continue to collect any remaining lines (prompt lines)
                    # that came in the same chunk after the marker was added
                    continue

                if prompt_detected:
                    # Lines after marker are prompt/control lines - collect them
                    remaining_after_prompt.append(line_data)
                else:
                    lines_to_feed.append(line_data)
            except IndexError:
                break

        # Feed batch to terminal
        if lines_to_feed:
            term.feed(b"".join(lines_to_feed))
            self.logger.debug(f"CAT QUEUE: Fed {len(lines_to_feed)} lines to terminal")

        # Handle prompt detection - clear context
        if prompt_detected:
            # Flush remaining pending lines (content that was buffered)
            pending = getattr(self, "_pending_lines", [])
            for pending_content, pending_ending in pending:
                term.feed(
                    (pending_content + pending_ending).encode("utf-8", errors="replace")
                )
            self._pending_lines = []

            # Feed any lines that came after the prompt marker
            # These are prompt lines (OSC7, PS1, etc.) that need to be displayed
            if remaining_after_prompt:
                term.feed(b"".join(remaining_after_prompt))
                self.logger.debug(
                    f"CAT QUEUE: Fed {len(remaining_after_prompt)} prompt lines to terminal"
                )

            # Drain any remaining lines in the queue (could be prompt data from next chunk)
            drain_lines = []
            while queue:
                try:
                    line_data = queue.popleft()
                    if line_data != b"__PROMPT_DETECTED__":
                        drain_lines.append(line_data)
                except IndexError:
                    break
            if drain_lines:
                term.feed(b"".join(drain_lines))
                self.logger.debug(
                    f"CAT QUEUE: Drained {len(drain_lines)} remaining lines to terminal"
                )

            self._highlighter.clear_context(self._proxy_id)
            self._reset_cat_state()
            # Clear shell input buffer - we're back at prompt for a new command
            self._input_highlight_buffer = ""
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0

        return prompt_detected

    def _process_cat_queue(self, term: Vte.Terminal) -> bool:
        """
        Process lines from cat queue in batches via GTK idle callback.

        Processes lines in small batches for responsive streaming.
        Uses GLib.idle_add to yield to GTK main loop between batches.

        Args:
            term: VTE terminal to feed output to

        Returns:
            False to remove from idle queue when done
        """
        if not self._running or self._widget_destroyed:
            self._cat_queue_processing = False
            return False

        try:
            queue = getattr(self, "_cat_queue", None)
            if not queue:
                self._cat_queue_processing = False
                return False

            # Process batch
            prompt_detected = self._process_cat_queue_batch(term, immediate=False)

            if prompt_detected:
                self._cat_queue_processing = False
                return False

            # Schedule next batch if queue has more
            if queue:
                return True  # Keep callback scheduled
            else:
                self._cat_queue_processing = False
                return False

        except Exception as e:
            self.logger.error(f"Cat queue processing error: {e}")
            self._cat_queue_processing = False
            return False

    def _reset_cat_state(self) -> None:
        """Reset cat/pygments state."""
        self._cat_filename = None
        self._cat_bytes_processed = 0  # Resetar contador
        self._cat_limit_reached = False  # Resetar flag
        self._pygments_lexer = None
        self._pygments_needs_content_detection = False
        self._content_buffer = []
        self._pending_lines = []
        self._cat_lines_processed = 0
        if hasattr(self, "_cat_queue"):
            self._cat_queue.clear()
        self._cat_queue_processing = False
        if hasattr(self, "_pygments_formatter"):
            delattr(self, "_pygments_formatter")

    def _extract_filename_from_cat_command(self, command: str) -> Optional[str]:
        """
        Extract the filename from a cat command for language detection.

        Args:
            command: The full cat command (e.g., "cat file.py", "cat -n file.sh")

        Returns:
            The first filename found, or None
        """
        if not command:
            return None

        # Parse the command to extract filenames
        parts = command.split()
        if not parts or parts[0].lower() not in ("cat", "/bin/cat", "/usr/bin/cat"):
            return None

        # Skip the command name and flags, find the first filename
        for part in parts[1:]:
            if part.startswith("-"):
                continue
            # This is likely a filename
            return part.strip("'\"")

        return None

    def _process_data_streaming(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Apply highlighting with Adaptive Burst Detection and Alt-Screen Bypass.
        """
        try:
            # --- 0. ALT SCREEN DETECTION (CRITICAL FOR FZF/VIM) ---
            # Check if this chunk contains a switch to/from Alt Screen.
            # If we are entering Alt Screen (fzf, vim), we must flush buffers
            # and feed raw immediately. TUI apps do not use newlines for rendering.
            state_changed = self._update_alt_screen_state(data)

            if self._is_alt_screen:
                # If we have leftover partial data from before entering alt screen, flush it now
                if self._partial_line_buffer:
                    term.feed(self._partial_line_buffer)
                    self._partial_line_buffer = b""

                # Feed the current data raw (contains the UI draw commands)
                term.feed(data)
                return

            # Combine with any partial data from previous read
            if self._partial_line_buffer:
                data = self._partial_line_buffer + data
                self._partial_line_buffer = b""

            data_len = len(data)

            # --- 1. HARD LIMIT (Safety Valve) ---
            if data_len > 65536:
                self._burst_counter = 100
                term.feed(data)
                return

            # --- 2. ADAPTIVE BURST DETECTION ---
            if data_len > 1024:
                self._burst_counter += 1
            else:
                self._burst_counter = 0

            if self._burst_counter > 15:
                # Fast check for OSC7 to keep state synchronized
                if b"\x1b]7;" in data or b"\033]7;" in data:
                    if self._input_highlight_buffer:
                        self._input_highlight_buffer = ""
                        self._prev_shell_input_token_type = None
                        self._prev_shell_input_token_len = 0

                term.feed(data)
                return

            # --- 3. PARTIAL LINE HANDLING ---
            # Only apply buffering if NOT in alt screen (already checked above)
            last_newline_pos = data.rfind(b"\n")

            if last_newline_pos != -1 and last_newline_pos < data_len - 1:
                remainder = data[last_newline_pos + 1 :]

                # Heuristic: If remainder looks like a prompt or interactive element,
                # process it immediately.
                is_interactive = False
                if len(remainder) < 100:
                    rem_str = remainder.decode("utf-8", errors="ignore").strip()
                    # Check for prompts OR cursor movement sequences often used in TUI-like CLI tools
                    if (
                        rem_str.endswith(("$", "#", "%", ">", ":"))
                        or "\x1b[" in rem_str
                    ):
                        is_interactive = True

                if not is_interactive:
                    self._partial_line_buffer = remainder
                    data = data[: last_newline_pos + 1]

            # Special case: No newline at all, but small chunk?
            # Likely interactive typing or a progress bar update. Feed immediately.
            elif last_newline_pos == -1 and data_len < 4096:
                pass  # Do not buffer, let it flow to highlighting

            # --- 4. NORMAL PROCESSING ---

            # Decode to text
            text = data.decode("utf-8", errors="replace")
            if not text:
                return

            # Interactive marker check (only on small chunks)
            if data_len < 1024:
                has_interactive_marker = False
                is_likely_user_input = False
                has_newline_marker = False

                if data_len >= 2 and data[0] == 0x00:
                    next_byte = data[1]
                    if data_len <= 15 and (b"\r" in data or b"\n" in data):
                        has_interactive_marker = True
                        has_newline_marker = True
                    elif next_byte == 0x08 or next_byte == 0x7F:
                        has_interactive_marker = True
                        is_likely_user_input = True
                    elif data_len <= 3:
                        if next_byte >= 0x20 and next_byte <= 0x7E:
                            has_interactive_marker = True
                            is_likely_user_input = True

                if has_interactive_marker:
                    if has_newline_marker:
                        if self._at_shell_prompt:
                            self._at_shell_prompt = False
                            self._shell_input_highlighter.set_at_prompt(
                                self._proxy_id, False
                            )
                    elif is_likely_user_input:
                        if not self._at_shell_prompt:
                            self._at_shell_prompt = True
                            self._shell_input_highlighter.set_at_prompt(
                                self._proxy_id, True
                            )
                            self._input_highlight_buffer = ""
                            self._prev_shell_input_token_type = None
                            self._prev_shell_input_token_len = 0
                            self._need_color_reset = True

            # Get rules
            rules = None
            with self._highlighter._lock:
                context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                rules = self._highlighter._get_active_rules(context)

            # Check for shell prompt detection
            prompt_detected = self._check_and_update_prompt_state(text)

            # Shell input highlighting (only if at prompt)
            if self._at_shell_prompt and self._shell_input_highlighter.enabled:
                highlighted_data = self._apply_shell_input_highlighting(text, term)
                if highlighted_data is not None:
                    return

            # If no rules, feed raw
            if not rules:
                term.feed(data)
                return

            # Highlighting Logic
            lines = text.splitlines(keepends=True)
            highlight_line = self._highlighter._apply_highlighting_to_line
            skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

            for i, line in enumerate(lines):
                if skip_first and i == 0:
                    self._line_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                if not line or line in ("\n", "\r", "\r\n"):
                    self._line_queue.append(line.encode("utf-8"))
                    continue

                if "\x1b]7;" in line or "\033]7;" in line:
                    self._line_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                if line[-1] == "\n":
                    if len(line) > 1 and line[-2] == "\r":
                        content, ending = line[:-2], "\r\n"
                    else:
                        content, ending = line[:-1], "\n"
                elif line[-1] == "\r":
                    content, ending = line[:-1], "\r"
                else:
                    content, ending = line, ""

                if content:
                    highlighted = highlight_line(content, rules) + ending
                else:
                    highlighted = ending

                self._line_queue.append(highlighted.encode("utf-8", errors="replace"))

            if not self._queue_processing:
                self._queue_processing = True
                self._process_line_queue(term)

        except Exception:
            term.feed(data)

    def _check_and_update_prompt_state(self, text: str) -> bool:
        """
        Check if text contains a shell prompt and update state.

        Returns True if a shell prompt was detected.

        NOTE: This function now ONLY handles:
        1. Continuation prompts ("> ") - for multi-line commands
        2. OSC7 tracking for directory changes

        Interactive mode activation is handled by the marker detection
        in _process_data_streaming when a single printable char arrives with \\x00.
        This prevents false positives from OSC7 in large output chunks.
        """
        import re

        # Track OSC7 for directory changes
        # OSC7 indicates command completed and we're back at the prompt
        # This is the right time to RESET the input buffer for the next command
        if "\x1b]7;" in text or "\033]7;" in text:
            if "file://" in text:
                self.logger.debug(
                    f"Proxy {self._proxy_id}: OSC7 detected (len={len(text)}), but not activating prompt mode"
                )
                # Reset input buffer - command has completed
                # This ensures next command starts with clean buffer
                if self._input_highlight_buffer:
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: Clearing input buffer (was {len(self._input_highlight_buffer)} chars) due to OSC7"
                    )
                    self._input_highlight_buffer = ""
                    self._prev_shell_input_token_type = None
                    self._prev_shell_input_token_len = 0

        # Check for continuation prompt ("> " at start of line or after escape sequences)
        # This indicates we're still in a multi-line command
        # Match continuation prompt with optional escape sequences before it
        # Pattern handles: "> ", "\x1b[?2004h> ", "\x1b[32m> ", etc.
        # Strip the text of escape sequences AND NULL bytes first, then check for just "> "
        stripped_text = re.sub(r"\x1b\[[^a-zA-Z]*[a-zA-Z]|\x1b\].*?\x07", "", text)
        stripped_text = stripped_text.replace("\x00", "")  # Also strip NULL bytes
        if stripped_text.strip() == ">":
            # Continuation prompt - keep at_prompt True and add newline to buffer
            # This separates the continuation line from the previous line
            self._at_shell_prompt = True
            self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            # Add newline to buffer to separate lines in multi-line command
            if (
                self._input_highlight_buffer
                and not self._input_highlight_buffer.endswith("\n")
            ):
                self._input_highlight_buffer += "\n"
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Added newline to buffer for continuation"
                )
            # Reset token tracking for new line
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0
            self.logger.debug(
                f"Proxy {self._proxy_id}: Continuation prompt detected, buffer: {repr(self._input_highlight_buffer)}"
            )
            return True

        # Detect traditional shell prompts that indicate command completion
        # This handles shells that don't send OSC7 (like sh, dash)
        # Look for patterns like "$ ", "# ", "% " at end of line (after stripping escape sequences)
        # Also detect prompts like "sh-5.3$ " or "user@host:~$ "
        if stripped_text:
            # Check if text ends with a shell prompt pattern
            prompt_patterns = ["$ ", "# ", "% "]
            for pattern in prompt_patterns:
                if stripped_text.rstrip().endswith(pattern.rstrip()):
                    # This looks like a shell prompt - clear the input buffer
                    # But only if we have content in the buffer (command was executed)
                    if self._input_highlight_buffer:
                        self.logger.debug(
                            f"Proxy {self._proxy_id}: Traditional prompt detected ('{pattern.strip()}'), clearing input buffer (was {len(self._input_highlight_buffer)} chars)"
                        )
                        self._input_highlight_buffer = ""
                        self._prev_shell_input_token_type = None
                        self._prev_shell_input_token_len = 0
                    return False  # Don't activate prompt mode here, let marker detection do it

        # NOTE: Interactive mode is activated by single-char input detection
        # in _process_data_streaming when \x00 + printable char arrives

        return False

    def _apply_shell_input_highlighting(
        self, text: str, term: Vte.Terminal
    ) -> Optional[bytes]:
        """
        Apply syntax highlighting to shell input being echoed.

        This method handles the case where we're at a shell prompt and
        characters are being echoed back as the user types.

        The approach:
        1. Track characters as they're typed (building a buffer)
        2. For each character echoed, append to buffer
        3. Re-tokenize the full buffer and apply colors
        4. Output only the newly typed character with appropriate color

        Args:
            text: The echoed text from PTY
            term: The VTE terminal

        Returns:
            bytes if handled, None if shell input highlighting didn't apply
        """
        if not self._shell_input_highlighter.enabled:
            return None

        # Strip NULL bytes that may be prepended by terminal
        text = text.lstrip("\x00")
        if not text:
            return None

        # Don't highlight control sequences or chunks containing them
        # This prevents interference with prompt colors and escape sequences
        if text.startswith("\x1b"):
            return None

        # Handle backspace FIRST (before rejecting escape sequences)
        # Backspace may come with erase sequence: \x08\x1b[K or \x7f\x1b[K
        # Or in sh/dash shell: \x08 \x08 (backspace, space overwrite, backspace)
        #
        # The pattern "\x08 \x08" is ONE logical deletion (move back, overwrite with space, move back)
        # We need to count logical deletions, not individual \x08 bytes
        #
        # Count logical backspaces:
        # - "\x08 \x08" pattern = 1 deletion (sh/dash style)
        # - "\x08\x1b[K" pattern = 1 deletion (bash style with erase to end of line)
        # - single "\x08" or "\x7f" = 1 deletion

        backspace_count = 0
        temp_text = text

        # First, count and remove "\x08 \x08" patterns (sh/dash style)
        while "\x08 \x08" in temp_text:
            backspace_count += 1
            temp_text = temp_text.replace("\x08 \x08", "", 1)

        # Then count remaining individual backspaces
        backspace_count += temp_text.count("\x7f") + temp_text.count("\x08")

        if backspace_count > 0:
            if self._input_highlight_buffer:
                # Remove up to backspace_count characters from the end
                chars_to_remove = min(
                    backspace_count, len(self._input_highlight_buffer)
                )
                self._input_highlight_buffer = (
                    self._input_highlight_buffer[:-chars_to_remove]
                    if chars_to_remove > 0
                    else self._input_highlight_buffer
                )
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Backspace: removed {chars_to_remove} chars (pattern detected), buffer now: '{self._input_highlight_buffer[-20:]}'..."
                )
            # Reset token tracking after backspace to prevent incorrect retroactive recolor
            # The next character will be tokenized fresh against the updated buffer
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0
            return None  # Let terminal handle the backspace display

        # Don't process chunks that contain escape sequences (like OSC7, colors, etc.)
        # These are command output or prompt rendering, not user input
        if "\x1b" in text or "\033" in text:
            return None

        # Don't process large chunks - user input comes one character at a time
        # Large chunks are likely command output
        # Exception: autocomplete may send small completions (e.g., "e.php " to complete "test" to "teste.php ")
        if len(text) > 10:
            return None

        # Check if we need to send a color reset before starting input highlighting
        # This happens when prompt was detected (OSC7/traditional) after command output
        # and ensures prompt colors don't leak into input highlighting
        if self._need_color_reset and text and text[0].isprintable():
            # Send SGR reset to clear any active terminal attributes
            term.feed(b"\x1b[0m")
            self._need_color_reset = False
            self.logger.debug(
                f"Proxy {self._proxy_id}: Sent color reset before input highlighting"
            )

        # Check for special characters that indicate we're leaving prompt
        # Newline means command line is being submitted OR continuation
        if "\n" in text or "\r" in text:
            # If buffer has content, the user submitted a command or is continuing multi-line
            # NOTE: For multi-line commands (if/then), continuation prompt will set _at_shell_prompt back to True
            if self._input_highlight_buffer.strip():
                # Check if this looks like a continuation (buffer has incomplete syntax)
                # Simple heuristic: if buffer ends with incomplete constructs, it's multi-line
                buffer_stripped = self._input_highlight_buffer.strip()
                incomplete_patterns = ["then", "do", "else", "|", "&&", "||", "\\", "{"]
                is_likely_multiline = any(
                    buffer_stripped.endswith(p) for p in incomplete_patterns
                )

                # Also check if this chunk contains a continuation prompt ("> ")
                # This handles multi-line commands where the second line doesn't end with an incomplete pattern
                # but the shell sends a continuation prompt in the same chunk as the newline
                stripped_text = re.sub(
                    r"\x1b\[[^a-zA-Z]*[a-zA-Z]|\x1b\].*?\x07", "", text
                )
                has_continuation_prompt = (
                    stripped_text.strip() == ">" or stripped_text.strip().endswith(">")
                )

                if is_likely_multiline or has_continuation_prompt:
                    self._input_highlight_buffer += "\n"
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: Newline added to buffer, continuing multi-line (pattern={is_likely_multiline}, continuation_prompt={has_continuation_prompt})"
                    )
                else:
                    # Command submitted - no longer at prompt until OSC7/prompt detected
                    self._at_shell_prompt = False
                    self._input_highlight_buffer = ""
                    self._prev_shell_input_token_type = None
                    self._prev_shell_input_token_len = 0
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: Command submitted, clearing buffer"
                    )
            return None

        # NOTE: Backspace handling moved to earlier in the function (before escape sequence check)

        # Only process printable characters
        if not text or not all(c.isprintable() or c == " " for c in text):
            return None

        # Filter out literal escape sequences shown by simple shells (sh/dash)
        # When ^[[D appears as text (arrow key not handled by shell), skip it
        # These patterns indicate the shell doesn't support this key
        # ^[ is the visual representation of ESC that some shells show
        # [A, [B, [C, [D are arrow keys shown literally when shell doesn't handle them
        if "^[" in text:
            self.logger.debug(
                f"Proxy {self._proxy_id}: Skipping literal escape sequence (^[): {repr(text)}"
            )
            return None
        # Check for arrow key sequences shown as literal text: [A, [B, [C, [D
        if text in ("[A", "[B", "[C", "[D", "[H", "[F"):
            self.logger.debug(
                f"Proxy {self._proxy_id}: Skipping literal arrow/nav key: {repr(text)}"
            )
            return None

        # Append to buffer (strip leading newlines if buffer was empty)
        if not self._input_highlight_buffer:
            self._input_highlight_buffer = text
        else:
            self._input_highlight_buffer += text
        self.logger.debug(
            f"Proxy {self._proxy_id}: Input buffer: '{self._input_highlight_buffer}'"
        )

        # Get highlighted version of the current buffer
        try:
            from pygments.lexers import BashLexer
            from pygments import lex

            # Always use lexer/formatter from the global singleton
            # This ensures theme changes are applied immediately
            highlighter = self._shell_input_highlighter
            lexer = highlighter._lexer
            formatter = highlighter._formatter

            # Fallback if singleton not initialized
            if lexer is None:
                lexer = BashLexer()

            # Tokenize to find the color of the last character
            # We use lex() to get token types and then map to colors
            tokens = list(lex(self._input_highlight_buffer, lexer))

            if not tokens:
                # No tokens, just output the raw text
                term.feed(text.encode("utf-8"))
                return b""

            # Pygments always adds a trailing newline token (Token.Text.Whitespace '\n')
            # So we need to find the actual token containing our typed character
            # Skip trailing whitespace-only tokens to find the real last token
            actual_token_type = None
            actual_token_value = None
            for token_type, token_value in reversed(tokens):
                # Skip pure whitespace/newline tokens at the end
                if token_value.strip():
                    actual_token_type = token_type
                    actual_token_value = token_value.rstrip(
                        "\n"
                    )  # Strip trailing newline from value
                    break
                # If the token is just a space (not newline) and we typed a space, use it
                elif token_value == " " and text == " ":
                    actual_token_type = token_type
                    actual_token_value = token_value
                    break

            if actual_token_type is None:
                # If we still didn't find anything useful, use the second-to-last token
                # (first is our content, last is the trailing newline)
                if len(tokens) >= 2:
                    actual_token_type, actual_token_value = tokens[-2]
                    actual_token_value = actual_token_value.rstrip("\n")
                else:
                    actual_token_type, actual_token_value = tokens[-1]
                    actual_token_value = actual_token_value.rstrip("\n")

            self.logger.debug(
                f"Proxy {self._proxy_id}: Actual token type: {actual_token_type}, value: {repr(actual_token_value)}"
            )

            # Enhanced token detection: improve coloring for commands and options
            # Pygments BashLexer doesn't recognize external commands or options well
            from pygments.token import Token

            enhanced_token_type = actual_token_type

            # Get the current line being typed (last line of buffer)
            current_line = self._input_highlight_buffer.split("\n")[-1].strip()

            # Define prefix commands that should be treated specially
            # These commands take other commands as arguments
            PREFIX_COMMANDS = {
                "sudo",
                "time",
                "env",
                "nice",
                "nohup",
                "strace",
                "ltrace",
                "doas",
                "pkexec",
            }
            # Commands that should be highlighted with Token.Name.Exception
            WARNING_COMMANDS = {"sudo", "doas", "pkexec", "rm", "dd"}

            # Check if this is an option (starts with - or --)
            if actual_token_value and (
                actual_token_value.startswith("--")
                or (actual_token_value.startswith("-") and len(actual_token_value) > 1)
            ):
                # Options: use Token.Name.Attribute which most themes style nicely
                # Don't set custom_ansi_color - let the formatter provide the color
                enhanced_token_type = Token.Name.Attribute
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Enhanced as option: {actual_token_value}"
                )

            # Check if this is the first word on the line (command position)
            # A command is the first word, or word after pipe |, semicolon ;, &&, ||, or prefix command
            elif actual_token_type in (Token.Text, Token.Name):
                if actual_token_value:
                    # Find position of current token in the line
                    words_before = current_line.rsplit(actual_token_value, 1)[
                        0
                    ].rstrip()
                    # Check if nothing before, or ends with control character, or follows a prefix command
                    is_command_position = not words_before or words_before.endswith((
                        "|",
                        ";",
                        "&&",
                        "||",
                        "(",
                        "`",
                        "$(",
                    ))

                    # Also check if last word was a prefix command (sudo, time, env, etc.)
                    if not is_command_position and words_before:
                        last_word = (
                            words_before.split()[-1] if words_before.split() else ""
                        )
                        if last_word in PREFIX_COMMANDS:
                            is_command_position = True

                    if is_command_position:
                        # Check if this is a warning command (sudo, rm, dd, etc.)
                        if actual_token_value in WARNING_COMMANDS:
                            # Warning commands: use Token.Name.Exception for visual distinction
                            # Most themes style this prominently (often in red/orange tones)
                            enhanced_token_type = Token.Name.Exception
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Enhanced as WARNING command: {actual_token_value}"
                            )
                        else:
                            enhanced_token_type = (
                                Token.Name.Function
                            )  # Commands as functions (green in most themes)
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Enhanced as command: {actual_token_value}"
                            )

            # Check if we need to do retroactive highlighting
            # This happens when the token type changes and includes previous characters
            # e.g., 'i' is Token.Text, but 'if' is Token.Keyword - we need to go back and recolor 'i'
            prev_token_type = getattr(self, "_prev_shell_input_token_type", None)
            prev_token_len = getattr(self, "_prev_shell_input_token_len", 0)

            # Store current state for next comparison (using enhanced type)
            self._prev_shell_input_token_type = enhanced_token_type
            self._prev_shell_input_token_len = (
                len(actual_token_value) if actual_token_value else 0
            )

            # Get the ANSI code for this token type from style_string
            # style_string is a dict mapping "Token.Type.Name" -> (start_ansi, end_ansi)
            if hasattr(formatter, "style_string"):
                # Use enhanced token type for better command/option coloring
                token_str = str(enhanced_token_type)
                style_codes = formatter.style_string.get(token_str)

                # Fallback to original Pygments token type if enhanced type has no style
                if not style_codes:
                    token_str = str(actual_token_type)
                    style_codes = formatter.style_string.get(token_str)

                self.logger.debug(
                    f"Proxy {self._proxy_id}: Token str: '{token_str}', style_codes: {style_codes}"
                )

                if style_codes:
                    ansi_start, ansi_end = style_codes
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: ANSI start: {repr(ansi_start)}, end: {repr(ansi_end)}"
                    )

                    # Check if we need retroactive highlighting
                    # This happens when:
                    # 1. Token type changed from previous char
                    # 2. Current token includes previous characters (len > 1)
                    # 3. We have a valid ANSI color to apply
                    need_retroactive = (
                        prev_token_type is not None
                        and prev_token_type != enhanced_token_type
                        and actual_token_value
                        and len(actual_token_value) > 1
                        and ansi_start
                    )

                    if need_retroactive:
                        # Calculate how many chars to go back and rewrite
                        # We need to go back to where the previous token started
                        # and rewrite with the new token
                        # Note: prev_token_len was captured BEFORE updating _prev_shell_input_token_len
                        # Use the previous token length if available, otherwise calculate
                        # This handles autocomplete where text is larger than 1 char
                        if prev_token_len > 0:
                            chars_to_rewrite = prev_token_len
                        else:
                            # Fallback: characters to rewrite is current token minus new text
                            chars_to_rewrite = len(actual_token_value) - len(text)

                        if chars_to_rewrite > 0:
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Retroactive recolor: going back {chars_to_rewrite} chars (prev_len={prev_token_len})"
                            )
                            # ANSI: move cursor back N chars, then output the full token colored
                            # \x1b[{n}D = move cursor left n positions
                            cursor_back = f"\x1b[{chars_to_rewrite}D"
                            highlighted_text = f"{cursor_back}{ansi_start}{actual_token_value}{ansi_end}"
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Feeding retroactive: {repr(highlighted_text)}"
                            )
                            term.feed(highlighted_text.encode("utf-8"))
                            return b""

                    # Normal case: just color the newly typed character
                    if ansi_start:
                        highlighted_text = f"{ansi_start}{text}{ansi_end}"
                        self.logger.debug(
                            f"Proxy {self._proxy_id}: Feeding highlighted: {repr(highlighted_text)}"
                        )
                        term.feed(highlighted_text.encode("utf-8"))
                        return b""
                else:
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: No style_codes for {token_str}"
                    )
            else:
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Formatter has no style_string attribute"
                )

            # Fallback: output raw text
            term.feed(text.encode("utf-8"))
            return b""

        except Exception as e:
            self.logger.debug(f"Shell input highlighting failed: {e}")
            return None

    def _process_line_queue(self, term: Vte.Terminal) -> bool:
        """
        Process multiple lines from queue per callback for efficiency.

        This is the SINGLE consumer for the line queue. It processes
        a batch of lines per callback, balancing responsiveness with efficiency.

        Uses deque.popleft() for O(1) performance.

        Returns False to remove from idle queue.
        """
        if not self._running or self._widget_destroyed:
            self._queue_processing = False
            return False

        try:
            if self._line_queue:
                # Process up to 10 lines per callback for efficiency
                # This reduces GTK overhead while maintaining responsiveness
                lines_to_feed = []
                for _ in range(10):
                    if self._line_queue:
                        lines_to_feed.append(self._line_queue.popleft())
                    else:
                        break

                # Feed all lines in one batch
                if lines_to_feed:
                    term.feed(b"".join(lines_to_feed))

                # Schedule next batch if queue not empty
                if self._line_queue:
                    GLib.idle_add(self._process_line_queue, term)
                else:
                    self._queue_processing = False
            else:
                self._queue_processing = False

        except Exception:
            self._queue_processing = False

        return False  # Remove this callback

    def _on_terminal_resize(self, terminal: Vte.Terminal, _pspec) -> None:
        if not self._running or self._widget_destroyed:
            return

        try:
            rows = terminal.get_row_count()
            cols = terminal.get_column_count()
            if rows > 0 and cols > 0:
                self.set_window_size(rows, cols)
        except Exception:
            pass


_highlighter_instance: Optional[OutputHighlighter] = None
_highlighter_lock = threading.Lock()


def get_output_highlighter() -> OutputHighlighter:
    """Get the global OutputHighlighter singleton instance."""
    global _highlighter_instance
    if _highlighter_instance is None:
        with _highlighter_lock:
            if _highlighter_instance is None:
                _highlighter_instance = OutputHighlighter()
    return _highlighter_instance