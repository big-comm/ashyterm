# ashyterm/terminal/highlighter/output.py
"""
Output syntax highlighter for terminal commands.

This module provides OutputHighlighter, which applies regex-based
highlighting rules to terminal output text using ANSI escape codes.
"""

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

from ...utils.re_engine import engine as re_engine

from ...settings.highlights import HighlightRule, get_highlight_manager
from ...utils.logger import get_logger
from .constants import ANSI_COLOR_PATTERN, ANSI_RESET
from .rules import (
    CompiledRule,
    LiteralKeywordRule,
    extract_literal_keywords,
    extract_prefilter,
)
if TYPE_CHECKING:
    from ...settings.highlights import HighlightManager


# Singleton instance
_output_highlighter: Optional["OutputHighlighter"] = None
_output_highlighter_lock = threading.Lock()

_REGEX_MATCH_TIMEOUT_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class _RegexRuleSpec:
    """Pure-Python snapshot safe to compile outside the GTK thread."""

    name: str
    pattern: str
    ansi_colors: tuple[str, ...]
    action: str


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
    - Per-rule iteration with PCRE2 backend
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
        self._context_compile_pending: set[tuple[int, str]] = set()
        self._rules_generation = 0
        self._disabled_rule_ids: set[int] = set()

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

        self.logger.info("Using regex module (PCRE2) for high-performance highlighting")

        self._refresh_rules()
        self._manager.connect("rules-changed", self._on_rules_changed)

    def _refresh_ignored_commands(self) -> None:
        """Refresh the cached set of ignored commands from settings."""
        try:
            from ...settings.manager import get_settings_manager

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
        with self._lock:
            self._rules_generation += 1
            self._context_rules_cache.clear()
            self._disabled_rule_ids.clear()

    def _compile_rule(
        self, rule: HighlightRule
    ) -> Optional[Union[CompiledRule, LiteralKeywordRule]]:
        """
        Compile a single highlight rule for fast matching.

        For simple keyword patterns like \\b(word1|word2)\\b, returns a
        LiteralKeywordRule which is ~10-50x faster than regex.

        For complex patterns, returns CompiledRule with:
        - Compiled regex pattern (PCRE2)
        - ANSI color tuple
        - Pre-filter function for fast skipping
        """
        prepared = self._prepare_rule(rule)
        if prepared is None:
            return None
        return self._compile_prepared_rule(prepared)

    def _prepare_rule(
        self, rule: HighlightRule
    ) -> Optional[Union[_RegexRuleSpec, LiteralKeywordRule]]:
        """Resolve settings-dependent fields before leaving the GTK thread."""
        if not rule.enabled or not rule.pattern:
            return None

        action = getattr(rule, "action", "next")
        if action not in ("next", "stop"):
            action = "next"

        literal_keywords = extract_literal_keywords(rule.pattern)
        if literal_keywords:
            ansi_color = (
                self._manager.resolve_color_to_ansi(rule.colors[0])
                if rule.colors and rule.colors[0]
                else ""
            )
            if not ansi_color:
                return None
            return LiteralKeywordRule(
                keywords=frozenset(literal_keywords),
                keyword_tuple=literal_keywords,
                ansi_color=ansi_color,
                action=action,
            )

        ansi_colors = (
            tuple(
                self._manager.resolve_color_to_ansi(color) if color else ""
                for color in rule.colors
            )
            if rule.colors
            else ("",)
        )
        if not any(ansi_colors):
            return None
        return _RegexRuleSpec(rule.name, rule.pattern, ansi_colors, action)

    def _compile_prepared_rule(
        self, prepared: Union[_RegexRuleSpec, LiteralKeywordRule]
    ) -> Optional[Union[CompiledRule, LiteralKeywordRule]]:
        """Compile a settings-free rule snapshot."""
        if isinstance(prepared, LiteralKeywordRule):
            return prepared
        try:
            flags = re_engine.IGNORECASE | getattr(re_engine, "VERSION1", 0)
            pattern = re_engine.compile(prepared.pattern, flags)
            num_groups = pattern.groups
            return CompiledRule(
                pattern=pattern,
                ansi_colors=prepared.ansi_colors,
                action=prepared.action,
                num_groups=num_groups,
                prefilter=extract_prefilter(prepared.pattern, prepared.name),
            )
        except Exception as e:
            self.logger.warning(
                f"Invalid regex pattern in rule '{prepared.name}': {e}"
            )
            return None

    def _refresh_rules(self) -> None:
        """Refresh compiled rules from the manager."""
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

        with self._lock:
            self._global_rules = tuple(compiled)

        self.logger.debug(
            f"Compiled {len(compiled)} global rules "
            f"({literal_count} literal, {regex_count} regex)"
        )

    def _precompile_common_contexts(self) -> None:
        """Pre-compile rules for commonly used contexts to avoid first-use latency."""
        _COMMON_CONTEXTS = (
            "ping",
            "docker",
            "git",
            "systemctl",
            "journalctl",
            "apt",
            "pacman",
            "pip",
            "make",
            "cmake",
        )
        for name in _COMMON_CONTEXTS:
            if self._manager.get_context_for_command(name):
                self._schedule_context_compilation(name, self._rules_generation)

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
            resolved_context: str | None
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
                        # Unknown commands use the already-compiled global rules.
                        # Keeping arbitrary command text as a context caused an
                        # unbounded cache and redundant compilation on the GTK thread.
                        resolved_context = ""

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

    def _get_context_unlocked(self, proxy_id: int = 0) -> str:
        """Get context without acquiring lock. Caller must hold self._lock."""
        return self._proxy_contexts.get(proxy_id, "")

    def get_context(self, proxy_id: int = 0) -> str:
        """Get the current context name for a specific proxy."""
        with self._lock:
            return self._get_context_unlocked(proxy_id)

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
        prepared = self._prepare_rules_for_context(context_name)
        return self._compile_prepared_rules(prepared)

    def _prepare_rules_for_context(
        self, context_name: str
    ) -> tuple[Union[_RegexRuleSpec, LiteralKeywordRule], ...]:
        """Snapshot context rules while access to managers remains on the caller."""
        rules = self._manager.get_rules_for_context(context_name)
        self.logger.debug(f"Preparing {len(rules)} rules for context '{context_name}'")
        return tuple(
            prepared
            for rule in rules
            if (prepared := self._prepare_rule(rule)) is not None
        )

    def _compile_prepared_rules(
        self,
        prepared_rules: tuple[Union[_RegexRuleSpec, LiteralKeywordRule], ...],
    ) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
        """Compile pure snapshots without consulting GTK-backed managers."""
        compiled = []
        for prepared in prepared_rules:
            rule = self._compile_prepared_rule(prepared)
            if rule:
                compiled.append(rule)
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

        with self._lock:
            cached = self._context_rules_cache.get(context)
        if cached is not None:
            return cached

        # Never hold the shared state lock while compiling regular expressions.
        context_rules = self._compile_rules_for_context(context)
        with self._lock:
            return self._context_rules_cache.setdefault(context, context_rules)

    def get_context_and_rules(
        self, proxy_id: int = 0
    ) -> tuple[str, Tuple[Union[CompiledRule, LiteralKeywordRule], ...]]:
        """Return a proxy snapshot without compiling on the caller thread."""
        with self._lock:
            context = self._proxy_contexts.get(proxy_id, "")
            if context and context.lower() in self._ignored_commands:
                return context, ()
            if not context or not self._manager.context_aware_enabled:
                return context, self._global_rules
            cached = self._context_rules_cache.get(context)
            if cached is not None:
                return context, cached
            generation = getattr(self, "_rules_generation", 0)

        self._schedule_context_compilation(context, generation)
        return context, self._global_rules

    def _schedule_context_compilation(self, context: str, generation: int) -> None:
        """Compile one context in an isolated daemon worker."""
        key = (generation, context)
        with self._lock:
            if not hasattr(self, "_context_compile_pending"):
                self._context_compile_pending = set()
            if key in self._context_compile_pending:
                return
            self._context_compile_pending.add(key)

        try:
            prepared_rules = self._prepare_rules_for_context(context)
        except Exception as exc:
            with self._lock:
                self._context_compile_pending.discard(key)
            self.logger.warning(
                f"Could not prepare highlight context '{context}': {exc}"
            )
            return

        worker = threading.Thread(
            target=self._compile_context_worker,
            args=(context, generation, prepared_rules),
            name=f"ashy-highlight-{context[:24]}",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as exc:
            with self._lock:
                self._context_compile_pending.discard(key)
            self.logger.warning(
                f"Could not start highlight compiler for '{context}': {exc}"
            )

    def _compile_context_worker(
        self,
        context: str,
        generation: int,
        prepared_rules: tuple[Union[_RegexRuleSpec, LiteralKeywordRule], ...],
    ) -> None:
        """Publish compiled rules only when their source generation is current."""
        key = (generation, context)
        try:
            compiled = self._compile_prepared_rules(prepared_rules)
            with self._lock:
                if generation == getattr(self, "_rules_generation", 0):
                    self._context_rules_cache[context] = compiled
        except Exception as exc:
            self.logger.warning(
                f"Failed to compile highlight context '{context}': {exc}"
            )
        finally:
            with self._lock:
                self._context_compile_pending.discard(key)

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

        _context, rules = self.get_context_and_rules(proxy_id)

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

        _context, rules = self.get_context_and_rules(proxy_id)

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
        - PCRE2 backend for regex rules

        Args:
            line: The line to highlight
            rules: Tuple of CompiledRule or LiteralKeywordRule objects

        Returns:
            Line with ANSI color codes applied
        """
        if not line:
            return line

        if self._line_already_highlighted(line):
            return line

        line_lower = line.lower()
        matches: List[Tuple[int, int, str]] = []

        self._collect_matches(line, line_lower, rules, matches)

        if not matches:
            return line

        return self._apply_matches_to_line(line, matches)

    def _line_already_highlighted(self, line: str) -> bool:
        """Check if line already contains ANSI color codes."""
        return "\x1b[" in line and bool(ANSI_COLOR_PATTERN.search(line))

    def _collect_matches(
        self,
        line: str,
        line_lower: str,
        rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...],
        matches: list,
    ) -> None:
        """Collect all rule matches from the line."""
        for rule in rules:
            if isinstance(rule, LiteralKeywordRule):
                should_stop = self._process_literal_rule(
                    rule, line, line_lower, matches
                )
            else:
                should_stop = self._process_compiled_rule(
                    rule, line, line_lower, matches
                )
            if should_stop:
                break

    def _process_literal_rule(
        self,
        rule: LiteralKeywordRule,
        line: str,
        line_lower: str,
        matches: list,
    ) -> bool:
        """Process a LiteralKeywordRule and return True if should stop."""
        if not any(kw in line_lower for kw in rule.keyword_tuple):
            return False

        rule_matches = rule.find_matches(line, line_lower)
        matches.extend(rule_matches)
        return bool(rule_matches) and rule.action == "stop"

    def _process_compiled_rule(
        self,
        rule: CompiledRule,
        line: str,
        line_lower: str,
        matches: list,
    ) -> bool:
        """Process a CompiledRule and return True if should stop."""
        if rule.prefilter is not None and not rule.prefilter(line_lower):
            return False

        disabled_rule_ids = getattr(self, "_disabled_rule_ids", set())
        if id(rule) in disabled_rule_ids:
            return False

        try:
            rule_matched = False
            try:
                matches_iter = rule.pattern.finditer(
                    line, timeout=_REGEX_MATCH_TIMEOUT_SECONDS
                )
            except TypeError:
                matches_iter = rule.pattern.finditer(line)
            for match in matches_iter:
                rule_matched = True
                self._extract_match_colors(match, rule, matches)

            return rule_matched and rule.action == "stop"
        except TimeoutError:
            disabled_rule_ids.add(id(rule))
            self._disabled_rule_ids = disabled_rule_ids
            if hasattr(self, "logger"):
                self.logger.warning(
                    "Highlight rule timed out and was disabled until rules reload"
                )
            return False
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.debug(f"Rule pattern matching failed: {e}")
            return False

    def _extract_match_colors(self, match, rule: CompiledRule, matches: list) -> None:
        """Extract color matches from a regex match object."""
        if rule.num_groups > 0:
            for group_idx in range(1, rule.num_groups + 1):
                start, end = match.start(group_idx), match.end(group_idx)
                if start == -1 or end == -1:
                    continue
                color_idx = min(group_idx - 1, len(rule.ansi_colors) - 1)
                ansi_color = rule.ansi_colors[color_idx]
                if ansi_color:
                    matches.append((start, end, ansi_color))
        else:
            start, end = match.start(), match.end()
            if rule.ansi_colors and rule.ansi_colors[0]:
                matches.append((start, end, rule.ansi_colors[0]))

    def _apply_matches_to_line(self, line: str, matches: list) -> str:
        """Apply collected matches to build the highlighted line."""
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

        result = []
        last_end = 0
        covered_until = 0

        for start, end, color in matches:
            if start < covered_until:
                continue

            if start > last_end:
                result.append(line[last_end:start])

            result.append(color)
            result.append(line[start:end])
            result.append(ANSI_RESET)

            last_end = end
            covered_until = end

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


def get_output_highlighter() -> OutputHighlighter:
    """Get or create the singleton OutputHighlighter instance (thread-safe)."""
    global _output_highlighter
    if _output_highlighter is None:
        with _output_highlighter_lock:
            if _output_highlighter is None:
                _output_highlighter = OutputHighlighter()
    return _output_highlighter


__all__ = ["OutputHighlighter", "get_output_highlighter"]
