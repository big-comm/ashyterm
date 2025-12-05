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
from concurrent.futures import ThreadPoolExecutor
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

        self._lock = threading.Lock()
        self._refresh_settings()

    def _refresh_settings(self) -> None:
        """Refresh settings from configuration."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            self._enabled = settings.get("shell_input_highlighting_enabled", False)
            # Use separate theme setting for shell input highlighting
            self._theme = settings.get("shell_input_pygments_theme", "monokai")

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

            try:
                style = get_style_by_name(self._theme)
            except ClassNotFound:
                style = get_style_by_name("monokai")

            self._formatter = Terminal256Formatter(style=style)
            self.logger.debug(
                f"Shell input highlighter initialized with theme: {self._theme}"
            )
        except ImportError as e:
            self.logger.warning(
                f"Pygments not available for shell input highlighting: {e}"
            )
            self._enabled = False
            self._lexer = None
            self._formatter = None

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

        Args:
            terminal: The VTE terminal widget to proxy output to.
            terminal_type: Type of terminal ("local" or "ssh").
            proxy_id: The ID to use for this proxy. Should match the terminal_id
                     from TerminalRegistry to ensure context detection works correctly.
                     If not provided, a unique ID is auto-generated (not recommended).
        """
        self.logger = get_logger("ashyterm.terminal.proxy")

        # Use provided proxy_id or generate one (for backward compatibility)
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

        # Register this proxy with the highlighters
        self._highlighter.register_proxy(self._proxy_id)
        self._shell_input_highlighter.register_proxy(self._proxy_id)

        # NOTE: Command detection is now handled by VTE screen scraping
        # in terminal/manager.py via Enter key interception.
        # The highlighter context is set directly by the manager.

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

        # Multi-threaded highlighting support
        # Thread pool for CPU-bound highlighting work
        self._highlight_executor: Optional[ThreadPoolExecutor] = None
        # Sequence counter for ordered output delivery
        self._sequence_counter = 0
        self._pending_outputs: Dict[int, bytes] = {}
        self._next_sequence_to_feed = 0
        self._output_lock = threading.Lock()

        # Line queue for true streaming (single queue, single consumer)
        # Using deque for O(1) popleft performance
        self._line_queue: deque = deque()
        self._queue_processing = False  # Flag to track if consumer is active

        # Pygments state for cat command highlighting
        self._cat_filename: Optional[str] = None

        # Shell input highlighting state
        self._input_highlight_buffer = ""  # Current command being typed
        # Start with True since terminal starts at shell prompt
        # Will be set to False when command is executed (Enter pressed)
        # Will be set to True again when OSC7 prompt is detected
        self._at_shell_prompt = True

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

            # Initialize thread pool for multi-threaded highlighting
            # Use 4 workers for better parallelism on multi-core systems
            self._highlight_executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="highlight"
            )
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

        # 1. Stop reading immediately.
        self._cleanup_io_watch()

        # 2. Shutdown thread pool
        if self._highlight_executor is not None:
            try:
                self._highlight_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._highlight_executor = None

        # Clear pending outputs and line queue
        with self._output_lock:
            self._pending_outputs.clear()
        self._line_queue.clear()
        self._queue_processing = False

        # Clear cat state
        self._cat_filename = None

        # Clear shell input state
        self._input_highlight_buffer = ""
        self._at_shell_prompt = False

        # Unregister this proxy from the highlighters
        # NOTE: Command detection is now handled by manager.py via VTE screen scraping
        self._highlighter.unregister_proxy(self._proxy_id)
        self._shell_input_highlighter.unregister_proxy(self._proxy_id)

        # If triggered by widget destruction, we are done.
        # Touching the widget, signals, or FDs is unsafe now.
        if from_destroy or self._widget_destroyed:
            self._terminal_ref = None
            return

        with self._lock:
            term = self._terminal_ref()

            # 2. Disconnect signals from the terminal widget
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

            # 3. IMPORTANT: We do NOT call set_pty(None).
            # Since we used new_foreign_sync, VTE owns the PTY object.
            # We simply abandon our reference to the FD and let VTE/GTK cleanup naturally.
            # Trying to force unref here causes the race condition with local shells.

            # 4. Clear FD references (do NOT close them, VTE owns them)
            self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None

            self._terminal_ref = None

    def _update_alt_screen_state(self, data: bytes) -> None:
        for pattern in ALT_SCREEN_ENABLE_PATTERNS:
            if pattern in data:
                self._is_alt_screen = True
                return

        for pattern in ALT_SCREEN_DISABLE_PATTERNS:
            if pattern in data:
                self._is_alt_screen = False
                return

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
            # 3. Try read - use 2KB buffer for very responsive streaming
            # Smaller chunks = more frequent visual updates
            data = os.read(fd, 4096)
            if not data:
                return True  # Empty read, keep waiting

            # 4. Verify widget is alive before feeding
            term = self._terminal
            if term is None:
                self._io_watch_id = None
                return False

            # Try to detect if C++ object is valid
            try:
                if not term.get_realized():
                    # Widget isn't realized, likely closing or hidden. Safe to bail.
                    return False
            except Exception:
                # Access failed, object likely dead
                self._widget_destroyed = True
                return False

            # 5. Processing logic
            self._update_alt_screen_state(data)

            # NOTE: Command detection is now handled by VTE screen scraping
            # in terminal/manager.py via Enter key interception.
            # This approach works reliably for SSH sessions and Docker containers.

            try:
                if self._is_alt_screen:
                    term.feed(data)
                elif (
                    self._highlighter.is_enabled_for_type(self._terminal_type)
                    or self._shell_input_highlighter.enabled
                ):
                    # Process through highlighting pipeline if either:
                    # - Output highlighting is enabled for this terminal type
                    # - Shell input highlighting is enabled (for live command typing)

                    # Get context with lock, then release before processing to avoid deadlock
                    with self._highlighter._lock:
                        context = self._highlighter._proxy_contexts.get(
                            self._proxy_id, ""
                        )
                        is_ignored = (
                            context
                            and context.lower() in self._highlighter._ignored_commands
                        )

                    # Check for cat syntax highlighting (always use Pygments for cat)
                    is_cat_context = context and context.lower() == "cat"

                    if is_cat_context:
                        # Process cat output through Pygments for syntax highlighting
                        self._process_cat_output(data, term)
                    elif is_ignored:
                        # Ignored command - pass raw data without highlighting
                        # BUT still check for prompt detection and shell input highlighting
                        if self._shell_input_highlighter.enabled:
                            text = data.decode("utf-8", errors="replace")
                            # Log data for debugging (same as _process_data_streaming)
                            text_preview = (
                                repr(text[:100]) if len(text) > 100 else repr(text)
                            )
                            has_osc7 = "\x1b]7;" in text or "\033]7;" in text
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Data received [IGNORED ctx='{context}'] (len={len(text)}, has_osc7={has_osc7}): {text_preview}"
                            )

                            prompt_detected = self._check_and_update_prompt_state(text)
                            if prompt_detected:
                                self.logger.debug(
                                    f"Proxy {self._proxy_id}: Prompt detected in ignored context '{context}', at_shell_prompt is now {self._at_shell_prompt}"
                                )

                            # If at shell prompt, try shell input highlighting even in ignored context
                            # This handles the case where user is typing a NEW command after an ignored command finished
                            if self._at_shell_prompt:
                                self.logger.debug(
                                    f"Proxy {self._proxy_id}: At prompt in ignored context, trying shell input highlighting"
                                )
                                highlighted = self._apply_shell_input_highlighting(
                                    text, term
                                )
                                if highlighted is not None:
                                    return True  # Shell input highlighting handled the data
                        term.feed(data)
                    else:
                        # Stream data line-by-line for responsive highlighting
                        self._process_data_streaming(data, term)
                else:
                    term.feed(data)
            except Exception:
                # Feed failed? Stop everything.
                self._widget_destroyed = True
                self._io_watch_id = None
                return False

            return True

        except OSError:
            # EIO (Input/output error) happens frequently when closing local shell
            self._io_watch_id = None
            return False
        except Exception as e:
            self.logger.error(f"PTY read error: {e}")
            return True

    def _process_cat_output(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Process cat output through Pygments for syntax highlighting.

        Uses queue-based streaming with immediate first batch processing.
        Lines are queued and processed in batches, yielding to GTK between batches.

        Args:
            data: Raw bytes from PTY
            term: VTE terminal to feed highlighted output to
        """
        try:
            text = data.decode("utf-8", errors="replace")
            if not text:
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

                # Check if we need content-based detection
                import os.path

                _, ext = os.path.splitext(new_filename)
                self._pygments_needs_content_detection = not ext and new_filename

            # Initialize cat queue if needed
            if not hasattr(self, "_cat_queue"):
                from collections import deque

                self._cat_queue = deque()
                self._cat_queue_processing = False

            # Process each line and add to queue
            lines = text.splitlines(keepends=True)
            skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

            for i, line in enumerate(lines):
                # Skip echoed command
                if skip_first and i == 0:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                content, ending = self._split_line_ending(line)

                # Check for shell prompt (command finished)
                lines_done = getattr(self, "_cat_lines_processed", 0)
                if lines_done > 0 and self._is_shell_prompt(content):
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    self._cat_queue.append(b"__PROMPT_DETECTED__")  # Marker
                    continue

                # Skip ANSI control sequences
                if content.startswith("\x1b") and not content.startswith("#!"):
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Highlight content
                if content.strip():
                    highlighted = self._highlight_line_with_pygments(
                        content, self._cat_filename
                    )

                    # Check if we have a lexer
                    current_lexer = getattr(self, "_pygments_lexer", None)

                    if current_lexer is not None:
                        # Flush pending lines first
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
                        # No lexer yet - buffer
                        pending = getattr(self, "_pending_lines", [])
                        pending.append((content, ending))
                        self._pending_lines = pending

                    self._cat_lines_processed = lines_done + 1
                else:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))

            # Process first batch IMMEDIATELY for responsive display
            # Then schedule idle callback for remaining batches
            if self._cat_queue and not self._cat_queue_processing:
                self._process_cat_queue_batch(term, immediate=True)

                # Schedule next batches if queue still has content
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
        if "\x1b]7;" in line or "\033]7;" in line:
            # Verify it's a proper OSC7 sequence with file://
            if "file://" in line:
                return True

        # Also check for OSC0 (title setting), often sent with prompt
        # Format: \x1b]0; (ESC ] 0 ;) - sets window title
        if "\x1b]0;" in line or "\033]0;" in line:
            return True

        # Fallback: Traditional prompt detection
        # Strip ANSI color codes to get clean text
        import re

        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        clean_line = clean_line.strip()

        # Check if line ends with common prompt terminators WITH trailing space
        # This is the most reliable pattern: "user@host:path$ " or "# "
        if (
            clean_line.endswith("$ ")
            or clean_line.endswith("# ")
            or clean_line.endswith("% ")
        ):
            return True

        # Check prompt terminators WITHOUT trailing space, but require @
        # This handles prompts like "user@host:path$" without space
        # The @ requirement helps avoid false positives from file content
        if clean_line.endswith("$") or clean_line.endswith("%"):
            if "@" in clean_line:
                return True

        # For # without space, be VERY strict - require user@host pattern
        # to avoid matching shell comments
        if clean_line.endswith("#") and "@" in clean_line:
            # Extra check: should have pattern like "user@host" before #
            import re

            if re.search(r"\w+@\w+.*#$", clean_line):
                return True

        # PowerLine prompts: only match specific Unicode characters at end
        # Do NOT match > alone as it's too common in file content
        stripped = clean_line.rstrip()
        if stripped and stripped[-1] in ("➜", "❯", "»"):
            return True

        # For > character, require it to be preceded by a space or prompt-like pattern
        # This avoids matching HTML tags, URLs, etc.
        if stripped and stripped.endswith("> "):
            # Only if preceded by typical prompt structure (path, git branch, etc.)
            if any(c in stripped for c in ["~", "/"]):
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

    def _get_pygments_theme(self) -> str:
        """Get the configured Pygments theme from settings."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            return settings.get("pygments_theme", "monokai").lower()
        except Exception:
            return "monokai"

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

            # Detect lexer if we don't have one
            if current_lexer is None:
                lexer_found = False

                # Try filename-based detection first
                if filename and not needs_content_detection:
                    try:
                        self._pygments_lexer = get_lexer_for_filename(filename)
                        lexer_found = True
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

            # Get or create formatter
            formatter = getattr(self, "_pygments_formatter", None)
            if formatter is None:
                theme = self._get_pygments_theme()
                try:
                    style = get_style_by_name(theme)
                except ClassNotFound:
                    style = get_style_by_name("monokai")
                self._pygments_formatter = Terminal256Formatter(style=style)
                formatter = self._pygments_formatter

            # Highlight
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

        for _ in range(batch_size):
            if not queue:
                break
            try:
                line_data = queue.popleft()

                # Check for prompt marker
                if line_data == b"__PROMPT_DETECTED__":
                    prompt_detected = True
                    break

                lines_to_feed.append(line_data)
            except IndexError:
                break

        # Feed batch to terminal
        if lines_to_feed:
            term.feed(b"".join(lines_to_feed))

        # Handle prompt detection - clear context
        if prompt_detected:
            # Flush remaining pending lines
            pending = getattr(self, "_pending_lines", [])
            for pending_content, pending_ending in pending:
                term.feed(
                    (pending_content + pending_ending).encode("utf-8", errors="replace")
                )
            self._pending_lines = []

            self._highlighter.clear_context(self._proxy_id)
            self._reset_cat_state()

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
        Apply highlighting with TRUE line-by-line streaming.

        Uses a single queue + single consumer pattern to ensure lines
        are always processed in order, even when multiple data chunks arrive.

        Producer: This method adds highlighted lines to the queue
        Consumer: _process_line_queue processes one line, yields to GTK, repeats

        Also handles shell input highlighting when at a shell prompt and no
        command context is active.
        """
        try:
            # Decode to text
            text = data.decode("utf-8", errors="replace")

            if not text:
                return

            # Debug: log all incoming data to trace flow
            if self._shell_input_highlighter.enabled:
                # Log short version for single chars, full for longer
                text_preview = repr(text[:100]) if len(text) > 100 else repr(text)
                has_osc7 = "\x1b]7;" in text or "\033]7;" in text
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Data received (len={len(text)}, has_osc7={has_osc7}): {text_preview}"
                )

            # Get rules with single lock acquisition
            rules = None
            with self._highlighter._lock:
                context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                rules = self._highlighter._get_active_rules(context)

            # Check for shell prompt detection (OSC7 or traditional prompt)
            # This signals that we're back at the prompt and can highlight input
            prompt_detected = self._check_and_update_prompt_state(text)
            if prompt_detected:
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Prompt detected, at_shell_prompt is now {self._at_shell_prompt}"
                )

            # If no rules and shell input highlighting is disabled, feed raw data directly
            if not rules and not self._shell_input_highlighter.enabled:
                term.feed(data)
                return

            # If at shell prompt and shell input highlighting is enabled, try it first
            # This happens when typing characters before Enter is pressed
            if self._at_shell_prompt and self._shell_input_highlighter.enabled:
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Attempting shell input highlighting, at_prompt={self._at_shell_prompt}, text_repr={repr(text[:50])}"
                )
                highlighted_data = self._apply_shell_input_highlighting(text, term)
                if highlighted_data is not None:
                    return  # Shell input highlighting handled the data
            elif self._shell_input_highlighter.enabled and not self._at_shell_prompt:
                # Only log if we have single printable char (user typing)
                clean = text.lstrip("\x00")
                if len(clean) == 1 and clean.isprintable():
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: Not at prompt, skipping shell input highlighting for: {repr(clean)}"
                    )

            # If no rules but shell input highlighting didn't apply, feed raw
            if not rules:
                # Debug: log if text contains OSC7 to see if we're missing prompt detection
                if "\x1b]7;" in text or "\033]7;" in text:
                    self.logger.debug(
                        f"Proxy {self._proxy_id}: OSC7 present in raw feed (rules=None): {repr(text[:80])}"
                    )
                term.feed(data)
                return

            # Split into lines preserving endings
            lines = text.splitlines(keepends=True)
            highlight_line = self._highlighter._apply_highlighting_to_line

            # Check if we should skip the first line (echoed command after Enter)
            skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

            # Process all lines and add to queue
            for i, line in enumerate(lines):
                # Skip highlighting for the first complete line (echoed command)
                # The first line is the command that was typed, not output to highlight
                if skip_first and i == 0:
                    # Feed the first line without highlighting
                    self._line_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Fast path for empty lines
                if not line or line in ("\n", "\r", "\r\n"):
                    self._line_queue.append(line.encode("utf-8"))
                    continue

                # Extract content and ending
                if line[-1] == "\n":
                    if len(line) > 1 and line[-2] == "\r":
                        content, ending = line[:-2], "\r\n"
                    else:
                        content, ending = line[:-1], "\n"
                elif line[-1] == "\r":
                    content, ending = line[:-1], "\r"
                else:
                    content, ending = line, ""

                # Highlight
                if content:
                    highlighted = highlight_line(content, rules) + ending
                else:
                    highlighted = ending

                self._line_queue.append(highlighted.encode("utf-8", errors="replace"))

            # Start consumer if not already running
            if not self._queue_processing:
                self._queue_processing = True
                self._process_line_queue(term)

        except Exception:
            # Fallback: feed raw data on any error
            term.feed(data)

    def _check_and_update_prompt_state(self, text: str) -> bool:
        """
        Check if text contains a shell prompt and update state.

        Returns True if a shell prompt was detected.
        """
        import re

        # Check for OSC7 FIRST (most reliable indicator of being at NEW prompt)
        # This takes priority over continuation prompt detection
        if "\x1b]7;" in text or "\033]7;" in text:
            if "file://" in text:
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                self._input_highlight_buffer = ""  # Clear buffer for new command
                self._prev_shell_input_token_type = None  # Reset token tracking
                self._prev_shell_input_token_len = 0
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Shell prompt detected (OSC7)"
                )
                return True

        # Check for continuation prompt ("> " at start of line or after escape sequences)
        # This indicates we're still in a multi-line command
        # Match continuation prompt with optional escape sequences before it
        # Pattern handles: "> ", "\x1b[?2004h> ", "\x1b[32m> ", etc.
        # Strip the text of escape sequences AND NULL bytes first, then check for just "> "
        stripped_text = re.sub(r"\x1b\[[^a-zA-Z]*[a-zA-Z]|\x1b\].*?\x07", "", text)
        stripped_text = stripped_text.replace("\x00", "")  # Also strip NULL bytes
        if stripped_text.strip() == ">":
            # Continuation prompt - keep at_prompt True but DON'T clear buffer
            self._at_shell_prompt = True
            self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self.logger.debug(
                f"Proxy {self._proxy_id}: Continuation prompt detected, keeping buffer"
            )
            return True

        # Check each line for traditional prompt patterns ($ # %)
        for line in text.split("\n"):
            if self._is_shell_prompt(line):
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                self._input_highlight_buffer = ""  # Clear buffer for new command
                self._prev_shell_input_token_type = None  # Reset token tracking
                self._prev_shell_input_token_len = 0
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Shell prompt detected (traditional)"
                )
                return True

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

        # Don't highlight control sequences
        if text.startswith("\x1b"):
            return None

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

        # Handle backspace (delete chars from buffer)
        # Count all backspace characters and remove that many from buffer
        backspace_count = text.count("\x7f") + text.count("\x08")
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
                    f"Proxy {self._proxy_id}: Removed {chars_to_remove} chars from buffer, now: '{self._input_highlight_buffer}'"
                )
            return None  # Let terminal handle the backspace display

        # Only process printable characters
        if not text or not all(c.isprintable() or c == " " for c in text):
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
            from pygments.formatters import Terminal256Formatter
            from pygments.styles import get_style_by_name
            from pygments.util import ClassNotFound

            # Get or create lexer/formatter
            lexer = getattr(self, "_shell_input_lexer", None)
            formatter = getattr(self, "_shell_input_formatter", None)

            if lexer is None:
                self._shell_input_lexer = BashLexer()
                lexer = self._shell_input_lexer

            if formatter is None:
                theme = self._shell_input_highlighter._theme
                try:
                    style = get_style_by_name(theme)
                except ClassNotFound:
                    style = get_style_by_name("monokai")
                self._shell_input_formatter = Terminal256Formatter(style=style)
                formatter = self._shell_input_formatter

            # Tokenize to find the color of the last character
            # We use lex() to get token types and then map to colors
            from pygments import lex

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
            custom_ansi_color = (
                None  # For tokens we want to color independently of style
            )

            # Get the current line being typed (last line of buffer)
            current_line = self._input_highlight_buffer.split("\n")[-1].strip()

            # Check if this is an option (starts with - or --)
            if actual_token_value and (
                actual_token_value.startswith("--")
                or (actual_token_value.startswith("-") and len(actual_token_value) > 1)
            ):
                # Options: use cyan color (117) - distinct from commands (green 84)
                custom_ansi_color = "\x1b[38;5;117m"  # Cyan like Token.Keyword.Type
                enhanced_token_type = Token.Name.Attribute  # For tracking purposes
                self.logger.debug(
                    f"Proxy {self._proxy_id}: Enhanced as option: {actual_token_value}"
                )

            # Check if this is the first word on the line (command position)
            # A command is the first word, or word after pipe |, semicolon ;, &&, ||
            elif actual_token_type in (Token.Text, Token.Name):
                if actual_token_value:
                    # Find position of current token in the line
                    words_before = current_line.rsplit(actual_token_value, 1)[
                        0
                    ].rstrip()
                    # If nothing before, or ends with control character, it's a command
                    if not words_before or words_before.endswith((
                        "|",
                        ";",
                        "&&",
                        "||",
                        "(",
                        "`",
                        "$(",
                    )):
                        enhanced_token_type = (
                            Token.Name.Function
                        )  # Commands as functions (green)
                        self.logger.debug(
                            f"Proxy {self._proxy_id}: Enhanced as command: {actual_token_value}"
                        )

            # Check if we need to do retroactive highlighting
            # This happens when the token type changes and includes previous characters
            # e.g., 'i' is Token.Text, but 'if' is Token.Keyword - we need to go back and recolor 'i'
            prev_token_type = getattr(self, "_prev_shell_input_token_type", None)

            # Store current state for next comparison (using enhanced type)
            self._prev_shell_input_token_type = enhanced_token_type
            self._prev_shell_input_token_len = (
                len(actual_token_value) if actual_token_value else 0
            )

            # Get the ANSI code for this token type from style_string
            # style_string is a dict mapping "Token.Type.Name" -> (start_ansi, end_ansi)
            if hasattr(formatter, "style_string"):
                # Use custom ANSI color if set (for options, etc.)
                if custom_ansi_color:
                    ansi_start = custom_ansi_color
                    ansi_end = "\x1b[39m"  # Reset foreground color
                    style_codes = (ansi_start, ansi_end)
                    token_str = str(enhanced_token_type)
                else:
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
                        # We need to rewrite the entire token except the char we just typed
                        chars_to_rewrite = len(actual_token_value) - len(text)
                        if chars_to_rewrite > 0:
                            self.logger.debug(
                                f"Proxy {self._proxy_id}: Retroactive recolor: going back {chars_to_rewrite} chars"
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
