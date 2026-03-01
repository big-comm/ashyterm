# ashyterm/terminal/url_handler.py
"""URL detection, hyperlink handling, and command detection mixin."""

import re
import subprocess
from typing import Optional
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gtk, Vte

from ..helpers import is_valid_url
from ..settings.config import PROMPT_TERMINATOR_PATTERN

# Pre-compiled pattern for ANSI escape sequences used in command detection
_ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b\].*?\x07|\[+\??(?:\d*[;]?)*[ABCDEFGHJKPSTfmnsuhl]"
)


class URLHandlerMixin:
    """Mixin providing URL detection, hyperlink handling, and command detection."""

    # Shell keywords that should be skipped (not actual commands)
    _SHELL_KEYWORDS = frozenset(
        {
            "if",
            "then",
            "else",
            "elif",
            "fi",
            "for",
            "do",
            "done",
            "while",
            "until",
            "case",
            "esac",
            "select",
            "in",
            "function",
            "{",
            "}",
            "[[",
            "]]",
            "(",
            ")",
        }
    )

    # Prefix commands that should be skipped to find the real command
    _PREFIX_COMMANDS = frozenset(
        {
            "sudo",
            "time",
            "env",
            "nice",
            "nohup",
            "strace",
            "ltrace",
            "doas",
            "pkexec",
            "command",
            "builtin",
            "exec",
        }
    )

    # Glued keywords that can appear merged with commands
    _GLUED_KEYWORDS = frozenset(
        {
            "then",
            "else",
            "elif",
            "fi",
            "do",
            "done",
            "esac",
            "in",
        }
    )

    # --- Hyperlink Setup ---

    def _connect_hyperlink_handler(self, terminal: Vte.Terminal) -> None:
        """Connects the hyperlink hover handler to the terminal."""
        if not hasattr(terminal, "connect"):
            return

        handler_id = terminal.connect(
            "hyperlink-hover-uri-changed", self._on_hyperlink_hover_changed
        )
        if not hasattr(terminal, "ashy_handler_ids"):
            terminal.ashy_handler_ids = []
        terminal.ashy_handler_ids.append(handler_id)

    def _add_url_regex_patterns(self, terminal: Vte.Terminal) -> int:
        """Adds URL regex patterns to terminal. Returns count of patterns added."""
        if not hasattr(terminal, "match_add_regex") or not hasattr(Vte, "Regex"):
            return 0

        self.logger.debug("Using Vte.Regex for URL pattern matching")

        url_patterns = [
            r"https?://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
            r"ftp://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        ]

        vte_flags = 1024
        patterns_added = 0

        for pattern in url_patterns:
            if self._add_single_url_pattern(terminal, pattern, vte_flags):
                patterns_added += 1

        return patterns_added

    def _add_single_url_pattern(
        self, terminal: Vte.Terminal, pattern: str, vte_flags: int
    ) -> bool:
        """Adds a single URL pattern to terminal. Returns True on success."""
        try:
            regex = Vte.Regex.new_for_match(pattern, -1, vte_flags)
            if regex:
                tag = terminal.match_add_regex(regex, 0)
                if hasattr(terminal, "match_set_cursor_name"):
                    terminal.match_set_cursor_name(tag, "pointer")
                return True
        except Exception as e:
            self.logger.warning(f"Vte.Regex pattern '{pattern}' failed: {e}")
        return False

    def _setup_url_patterns(self, terminal: Vte.Terminal) -> None:
        try:
            terminal.set_allow_hyperlink(True)
            self._connect_hyperlink_handler(terminal)
            patterns_added = self._add_url_regex_patterns(terminal)

            if patterns_added > 0:
                self.logger.info(
                    f"URL pattern detection configured ({patterns_added} patterns)"
                )
            else:
                self.logger.error(
                    "Failed to configure URL patterns - URL clicking disabled"
                )

        except Exception as e:
            self.logger.error(f"Failed to setup URL patterns: {e}")

    # --- Hyperlink Hover ---

    def _on_hyperlink_hover_changed(self, terminal, uri, _bbox):
        try:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is not None:
                if uri:
                    terminal._osc8_hovered_uri = uri
                    self.logger.debug(
                        f"OSC8 hyperlink hovered in terminal {terminal_id}: {uri}"
                    )
                else:
                    if hasattr(terminal, "_osc8_hovered_uri"):
                        delattr(terminal, "_osc8_hovered_uri")
                    self.logger.debug(
                        f"OSC8 hyperlink hover cleared in terminal {terminal_id}"
                    )
        except Exception as e:
            self.logger.error(f"OSC8 hyperlink hover handling failed: {e}")

    # --- Click & URL Detection ---

    def _on_terminal_clicked(self, gesture, _n_press, x, y, terminal, terminal_id):
        try:
            modifiers = gesture.get_current_event_state()
            ctrl_pressed = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)

            if ctrl_pressed:
                url_to_open = self._get_url_at_position(terminal, x, y)
                if url_to_open:
                    success = self._open_hyperlink(url_to_open)
                    if success:
                        self.logger.info(f"URL opened from Ctrl+click: {url_to_open}")
                        return Gdk.EVENT_STOP

            terminal.grab_focus()
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)

            return Gdk.EVENT_PROPAGATE

        except Exception as e:
            self.logger.error(
                f"Terminal click handling failed for terminal {terminal_id}: {e}"
            )
            return Gdk.EVENT_PROPAGATE

    def _get_osc8_hovered_uri(self, terminal: Vte.Terminal) -> Optional[str]:
        """Gets OSC8 hovered URI from terminal if available."""
        if hasattr(terminal, "_osc8_hovered_uri") and terminal._osc8_hovered_uri:
            return terminal._osc8_hovered_uri
        return None

    def _get_hyperlink_hover_uri(self, terminal: Vte.Terminal) -> Optional[str]:
        """Gets VTE hyperlink hover URI from terminal if available."""
        if not hasattr(terminal, "get_hyperlink_hover_uri"):
            return None
        try:
            hover_uri = terminal.get_hyperlink_hover_uri()
            if hover_uri:
                return hover_uri
        except Exception as e:
            self.logger.debug(f"VTE hyperlink detection failed: {e}")
        return None

    def _get_url_from_regex_match(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> Optional[str]:
        """Gets URL at position using regex match check."""
        if not hasattr(terminal, "match_check"):
            return None

        try:
            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()

            if char_width <= 0 or char_height <= 0:
                return None

            col = int(x / char_width)
            row = int(y / char_height)
            match_result = terminal.match_check(col, row)

            if match_result and len(match_result) >= 2:
                matched_text = match_result[0]
                if matched_text and is_valid_url(matched_text):
                    return matched_text
        except Exception as e:
            self.logger.debug(f"Regex match check failed: {e}")

        return None

    def _get_url_at_position(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> Optional[str]:
        try:
            url = self._get_osc8_hovered_uri(terminal)
            if url:
                return url

            url = self._get_hyperlink_hover_uri(terminal)
            if url:
                return url

            return self._get_url_from_regex_match(terminal, x, y)

        except Exception as e:
            self.logger.error(f"URL detection at position failed: {e}")
            return None

    def _open_hyperlink(self, uri: str) -> bool:
        try:
            if not uri or not uri.strip():
                self.logger.warning("Empty or invalid URI provided")
                return False

            uri = uri.strip()

            if (
                "@" in uri
                and not uri.startswith(("http://", "https://", "ftp://", "mailto:"))
                and "." in uri.split("@")[-1]
            ):
                uri = f"mailto:{uri}"

            try:
                parsed = urlparse(uri)
                if not parsed.scheme:
                    self.logger.warning(f"URI missing scheme: {uri}")
                    return False
            except Exception as e:
                self.logger.warning(f"Invalid URI format: {uri} - {e}")
                return False

            self.logger.info(f"Opening hyperlink: {uri}")

            subprocess.run(["xdg-open", uri], check=True, timeout=10)
            return True

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout opening hyperlink: {uri}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to open hyperlink '{uri}': {e}")
            return False

    # --- Command Detection (Key Press Screen Scraping) ---

    def _on_terminal_key_pressed_for_detection(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> bool:
        """Handle Enter key press to detect command execution via screen scraping."""
        try:
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return Gdk.EVENT_PROPAGATE

            if state & (
                Gdk.ModifierType.SHIFT_MASK
                | Gdk.ModifierType.CONTROL_MASK
                | Gdk.ModifierType.ALT_MASK
            ):
                return Gdk.EVENT_PROPAGATE

            _, row = terminal.get_cursor_position()

            try:
                col_count = terminal.get_column_count()
                text_result = terminal.get_text_range_format(
                    Vte.Format.TEXT, row, 0, row, col_count
                )

                if isinstance(text_result, tuple) and len(text_result) >= 1:
                    line_text = text_result[0] if text_result[0] else ""
                else:
                    line_text = ""

            except Exception as e:
                self.logger.debug(
                    f"get_text_range_format failed for terminal {terminal_id}: {e}"
                )
                return Gdk.EVENT_PROPAGATE

            line_text = line_text.strip() if line_text else ""
            if line_text:
                self._analyze_command_from_line(line_text, terminal, terminal_id)

        except Exception as e:
            self.logger.error(
                f"Key press detection failed for terminal {terminal_id}: {e}"
            )

        return Gdk.EVENT_PROPAGATE

    def _analyze_command_from_line(
        self, line: str, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Analyze a terminal line to extract and set the command context."""
        try:
            if not line:
                return

            clean_line = _ANSI_ESCAPE_PATTERN.sub("", line)
            command_part = self._extract_command_from_line(clean_line)

            if not command_part:
                return

            command_part = self._strip_glued_keywords(command_part)
            program_name = self._detect_program_name(command_part)

            if not program_name:
                return

            self._set_terminal_context(
                terminal_id, program_name, command_part, clean_line
            )

        except Exception as e:
            self.logger.error(
                f"Command analysis failed for terminal {terminal_id}: {e}"
            )

    def _extract_command_from_line(self, clean_line: str) -> str:
        """Extract command portion after the shell prompt."""
        matches = list(PROMPT_TERMINATOR_PATTERN.finditer(clean_line))
        if matches:
            last_match = matches[-1]
            return clean_line[last_match.end() :].strip()
        return clean_line.strip()

    def _strip_glued_keywords(self, command_part: str) -> str:
        """Remove glued shell keywords from command start."""
        command_lower = command_part.lower()
        for kw in self._GLUED_KEYWORDS:
            if command_lower.startswith(kw) and len(command_lower) > len(kw):
                if command_lower[len(kw)].isalpha():
                    return command_part[len(kw) :]
        return command_part

    def _detect_program_name(self, command_part: str) -> Optional[str]:
        """Detect the program name from command part."""
        from ..settings.manager import get_settings_manager

        last_command_part = self._get_last_pipeline_segment(command_part)
        tokens = last_command_part.split() if last_command_part else []

        settings_manager = get_settings_manager()
        highlight_manager = self._get_highlight_manager()

        ignored_commands = set(settings_manager.get("ignored_highlight_commands", []))
        known_triggers = highlight_manager.get_all_triggers()

        return self._find_program_in_tokens(tokens, ignored_commands, known_triggers)

    def _get_last_pipeline_segment(self, command_part: str) -> str:
        """Get the last segment of a pipeline command."""
        parts: list[str] = []
        current: list[str] = []
        for char in command_part:
            if char in "|;&":
                if current:
                    parts.append("".join(current).strip())
                    current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current).strip())

        for part in reversed(parts):
            if part:
                return part
        return command_part

    def _find_program_in_tokens(
        self,
        tokens: list[str],
        ignored_commands: set[str],
        known_triggers: set[str],
    ) -> Optional[str]:
        """Find the program name from command tokens."""
        fallback = None

        for token in tokens:
            clean_token = self._clean_command_token(token)
            if not clean_token:
                continue

            clean_lower = clean_token.lower()

            if clean_lower in self._PREFIX_COMMANDS:
                continue
            if clean_lower in self._SHELL_KEYWORDS:
                continue

            clean_token = self._extract_from_glued_keyword(clean_token, clean_lower)
            clean_lower = clean_token.lower()

            if clean_lower in self._SHELL_KEYWORDS:
                continue

            if clean_lower in ignored_commands or clean_lower in known_triggers:
                return clean_token

            if fallback is None:
                fallback = clean_token

        return fallback

    def _clean_command_token(self, token: str) -> str:
        """Clean a token by removing flags, paths, and dots."""
        if token.startswith("-"):
            return ""
        if "=" in token and "/" not in token:
            return ""

        clean = token
        if "/" in clean:
            clean = clean.split("/")[-1]
        return clean.lstrip(".")

    def _extract_from_glued_keyword(self, token: str, token_lower: str) -> str:
        """Extract command from keyword-glued token."""
        for kw in self._SHELL_KEYWORDS:
            if token_lower.startswith(kw) and len(token_lower) > len(kw):
                remainder = token[len(kw) :]
                if remainder and remainder[0].isalpha():
                    return remainder
        return token

    def _set_terminal_context(
        self,
        terminal_id: int,
        program_name: str,
        command_part: str,
        clean_line: str,
    ) -> None:
        """Set the syntax highlighting context for the terminal."""
        from .manager import _get_output_highlighter

        tokens = command_part.lower().split()
        is_help = self._is_help_command(tokens)

        highlighter = _get_output_highlighter()
        if is_help:
            highlighter.set_context("help", terminal_id, full_command=command_part)
            self.logger.debug(
                f"Terminal {terminal_id}: help context for: {clean_line[:50]}..."
            )
        else:
            highlighter.set_context(
                program_name, terminal_id, full_command=command_part
            )
            self.logger.debug(
                f"Terminal {terminal_id}: detected '{program_name}' from: {clean_line[:50]}..."
            )

    def _is_help_command(self, tokens: list[str]) -> bool:
        """Check if command is a help command."""
        if "--help" in tokens or "-h" in tokens:
            return True
        return bool(tokens and tokens[0] in ("help", "man"))
