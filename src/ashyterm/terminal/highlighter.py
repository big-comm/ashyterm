# ashyterm/terminal/highlighter.py
"""
Terminal output highlighter that applies ANSI color codes based on regex patterns.

Features:
- Multi-group regex: Different capture groups can have different colors
- Theme-aware: Uses logical color names resolved via active theme palette
- Context-aware: Applies command-specific rules based on foreground process
- High-performance: Uses PCRE2 backend with smart pre-filtering

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
_KEYWORD_PATTERN = re.compile(r'^\\b\(([a-zA-Z|?:()]+)\)\\b$')

# Pattern to check if a character is a word boundary character
_WORD_CHAR = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


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
        if char == '(':
            depth += 1
            current += char
        elif char == ')':
            depth -= 1
            current += char
        elif char == '|' and depth == 0:
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
    match = re.match(r'^([a-zA-Z]+)\(\?:([^)]+)\)\?$', part)
    if match:
        base = match.group(1).lower()
        suffixes_str = match.group(2)
        # Split suffixes on |
        suffixes = suffixes_str.split('|')
        keywords = [base]  # Base word always included
        for suffix in suffixes:
            keywords.append(base + suffix.lower())
        return keywords
    
    # No optional suffix, just return the cleaned base word
    clean = re.sub(r'[^a-zA-Z]', '', part)
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
        for part in inner.split('|'):
            # Remove (?:...) non-capturing groups
            clean = re.sub(r'\(\?:[^)]+\)\??', '', part)
            if clean and clean.isalpha():
                words.add(clean.lower())
        if words:
            # Frozen tuple for slightly faster iteration
            keywords = tuple(words)
            return lambda line: any(kw in line for kw in keywords)
    
    # Pattern-specific pre-filters based on required characters
    rule_lower = rule_name.lower()
    
    # IPv4: requires dots and digits
    if 'ipv4' in rule_lower or ('ip' in rule_lower and 'v6' not in rule_lower):
        return lambda line: '.' in line
    
    # IPv6: requires colons
    if 'ipv6' in rule_lower:
        return lambda line: ':' in line
    
    # MAC address: requires colons or hyphens
    if 'mac' in rule_lower and 'address' in rule_lower:
        return lambda line: ':' in line or '-' in line
    
    # UUID/GUID: requires hyphens
    if 'uuid' in rule_lower or 'guid' in rule_lower:
        return lambda line: '-' in line
    
    # URLs: requires http
    if 'url' in rule_lower or 'http' in rule_lower:
        return lambda line: 'http' in line
    
    # Email: requires @
    if 'email' in rule_lower:
        return lambda line: '@' in line
    
    # Date (ISO): requires hyphens and digits
    if 'date' in rule_lower:
        return lambda line: '-' in line
    
    # Quoted strings: requires quotes
    if 'quote' in rule_lower or 'string' in rule_lower:
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
        self._context_rules_cache: Dict[str, Tuple[Union[CompiledRule, LiteralKeywordRule], ...]] = {}

        # Global compiled rules (tuple for faster iteration)
        self._global_rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...] = ()

        # Per-proxy context tracking: proxy_id -> context_name
        self._proxy_contexts: Dict[int, str] = {}
        
        # Cached set of ignored commands (tools with native coloring)
        self._ignored_commands: frozenset = frozenset()
        self._refresh_ignored_commands()

        # Log which regex engine we're using
        if USING_PCRE2:
            self.logger.info("Using regex module (PCRE2) for high-performance highlighting")
        else:
            self.logger.info("Using standard re module (install 'regex' for better performance)")

        self._refresh_rules()
        self._manager.connect("rules-changed", self._on_rules_changed)
    
    def _refresh_ignored_commands(self) -> None:
        """Refresh the cached set of ignored commands from settings."""
        try:
            from ..settings.manager import get_settings_manager
            settings = get_settings_manager()
            ignored_list = settings.get("ignored_highlight_commands", [])
            self._ignored_commands = frozenset(cmd.lower() for cmd in ignored_list)
            self.logger.debug(f"Refreshed ignored commands: {len(self._ignored_commands)} commands")
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
                self.logger.debug(f"Unregistered proxy {proxy_id}")

    def _on_rules_changed(self, manager) -> None:
        self._refresh_rules()
        # Clear context cache when rules change
        with self._lock:
            self._context_rules_cache.clear()

    def _compile_rule(self, rule: HighlightRule) -> Optional[Union[CompiledRule, LiteralKeywordRule]]:
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
            ansi_colors = tuple(
                self._manager.resolve_color_to_ansi(c) if c else ""
                for c in rule.colors
            ) if rule.colors else ("",)
            
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

    def set_context(self, command_name: str, proxy_id: int = 0) -> bool:
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
                    resolved_context = self._manager.get_context_for_command(command_name)
                    if not resolved_context:
                        # Command not in any context's triggers - use command name as-is
                        # This allows the ignored command check to work
                        resolved_context = command_name.lower()

            # Get current context for this proxy
            current_context = self._proxy_contexts.get(proxy_id, "")

            # Check if context actually changed
            if current_context == resolved_context:
                return False

            self._proxy_contexts[proxy_id] = resolved_context

            if resolved_context:
                self.logger.debug(
                    f"Context changed for proxy {proxy_id}: '{current_context}' -> '{resolved_context}' (from '{command_name}')"
                )
            else:
                self.logger.debug(
                    f"Context cleared for proxy {proxy_id} (command '{command_name}' has no context)"
                )
            return True

    def get_context(self, proxy_id: int = 0) -> str:
        """Get the current context name for a specific proxy."""
        with self._lock:
            return self._proxy_contexts.get(proxy_id, "")

    def _compile_rules_for_context(self, context_name: str) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
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

    def _get_active_rules(self, context: str = "") -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
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

            except Exception:
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


class HighlightedTerminalProxy:
    """
    A proxy that intercepts terminal output and applies syntax highlighting.
    Robust against Local Terminal race conditions.

    Supports context-aware highlighting via the highlighter property.
    """

    # Class-level counter for unique proxy IDs
    _next_proxy_id = 1
    _id_lock = threading.Lock()

    def __init__(
        self,
        terminal: Vte.Terminal,
        terminal_type: str = "local",
    ):
        self.logger = get_logger("ashyterm.terminal.proxy")

        # Assign unique proxy ID
        with HighlightedTerminalProxy._id_lock:
            self._proxy_id = HighlightedTerminalProxy._next_proxy_id
            HighlightedTerminalProxy._next_proxy_id += 1

        self._terminal_ref = weakref.ref(terminal)
        self._terminal_type = terminal_type
        self._highlighter = get_output_highlighter()

        # Register this proxy with the highlighter
        self._highlighter.register_proxy(self._proxy_id)

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
            self._highlight_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="highlight")
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

        # Unregister this proxy from the highlighter
        # NOTE: Command detection is now handled by manager.py via VTE screen scraping
        self._highlighter.unregister_proxy(self._proxy_id)

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
                elif self._highlighter.is_enabled_for_type(self._terminal_type):
                    # Check if current command is in ignored list (native coloring tools)
                    # Get context with lock, then release before processing to avoid deadlock
                    with self._highlighter._lock:
                        context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                        is_ignored = context and context.lower() in self._highlighter._ignored_commands
                    
                    if is_ignored:
                        # Ignored command - pass raw data without highlighting
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

    def _process_data_streaming(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Apply highlighting with TRUE line-by-line streaming.
        
        Uses a single queue + single consumer pattern to ensure lines
        are always processed in order, even when multiple data chunks arrive.
        
        Producer: This method adds highlighted lines to the queue
        Consumer: _process_line_queue processes one line, yields to GTK, repeats
        """
        try:
            # Decode to text
            text = data.decode("utf-8", errors="replace")
            
            if not text:
                return
            
            # Get rules with single lock acquisition
            rules = None
            with self._highlighter._lock:
                context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                rules = self._highlighter._get_active_rules(context)
            
            # If no rules, feed raw data directly (fastest path)
            if not rules:
                term.feed(data)
                return
            
            # Split into lines preserving endings
            lines = text.splitlines(keepends=True)
            highlight_line = self._highlighter._apply_highlighting_to_line
            
            # Process all lines and add to queue
            for line in lines:
                # Fast path for empty lines
                if not line or line in ('\n', '\r', '\r\n'):
                    self._line_queue.append(line.encode("utf-8"))
                    continue
                
                # Extract content and ending
                if line[-1] == '\n':
                    if len(line) > 1 and line[-2] == '\r':
                        content, ending = line[:-2], '\r\n'
                    else:
                        content, ending = line[:-1], '\n'
                elif line[-1] == '\r':
                    content, ending = line[:-1], '\r'
                else:
                    content, ending = line, ''
                
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
                    term.feed(b''.join(lines_to_feed))
                
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
