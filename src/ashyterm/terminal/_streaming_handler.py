# ashyterm/terminal/_streaming_handler.py
"""Streaming data handler mixin: prompt detection, shell input highlighting, readline handling."""

from typing import Optional

import gi

gi.require_version("Vte", "3.91")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Vte

from .highlighter.constants import (
    ALL_ESCAPE_SEQ_PATTERN as _ALL_ESCAPE_SEQ_PATTERN,
)
from .highlighter.constants import (
    SHELL_NAME_PROMPT_PATTERN as _SHELL_NAME_PROMPT_PATTERN,
)

# Sentinel for "continue normal processing" in shell input handling
_CONTINUE_PROCESSING = object()


class StreamingHandler:
    """Mixin providing streaming data processing, prompt detection, and shell input highlighting."""

    # Type stubs for attributes initialized in parent class (_highlighter_impl.py)
    _partial_line_buffer: bytes
    _at_shell_prompt: bool
    _input_highlight_buffer: str
    _queue_processing: bool

    def _process_data_streaming(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Apply highlighting with Adaptive Burst Detection, Alt-Screen Bypass,
        Bracketed Paste Bypass, Strict Ordering, and Robust Split-Escape Safety.
        """
        try:
            # Early exits and special modes
            if self._handle_streaming_early_exits(data, term):
                return

            # Combine with partial data
            data = self._combine_streaming_partial_data(data)
            data_len = len(data)

            # Safety limits
            if self._handle_streaming_safety_limits(data, data_len, term):
                return

            # Handle partial lines
            data = self._handle_streaming_partial_lines(data, data_len)

            # Normal processing
            text = data.decode("utf-8", errors="replace")
            if not text:
                return

            # Process the text
            self._process_streaming_text(data, text, term)

        except Exception:
            self._flush_queue(term)
            term.feed(data)

    def _handle_streaming_early_exits(self, data: bytes, term: Vte.Terminal) -> bool:
        """Handle early exit conditions for streaming. Returns True if should exit."""
        # Output highlighting disabled
        if not self._highlighter.is_enabled_for_type(self._terminal_type):
            self._line_queue.clear()
            if self._partial_line_buffer:
                term.feed(self._partial_line_buffer)
                self._partial_line_buffer = b""
            term.feed(data)
            return True

        # Bracketed paste detection
        if b"\x1b[200~" in data:
            self._in_bracketed_paste = True
            self._flush_queue(term)
            if self._partial_line_buffer:
                term.feed(self._partial_line_buffer)
                self._partial_line_buffer = b""

        if self._in_bracketed_paste:
            term.feed(data)
            if b"\x1b[201~" in data:
                self._in_bracketed_paste = False
                self._reset_input_buffer()
                self._suppress_shell_input_highlighting = True
            return True

        # Prompt redraw / cursor movement bypass
        if self._should_bypass_for_readline(data, term):
            return True

        # Alt screen detection
        self._update_alt_screen_state(data)
        if self._is_alt_screen:
            self._flush_queue(term)
            if self._partial_line_buffer:
                term.feed(self._partial_line_buffer)
                self._partial_line_buffer = b""
            term.feed(data)
            return True

        return False

    def _should_bypass_for_readline(self, data: bytes, term: Vte.Terminal) -> bool:
        """Check if we should bypass highlighting for readline operations."""
        if not self._at_shell_prompt or len(data) >= 1048576:
            return False

        is_enter_marker = (data[0] == 0x00 if len(data) >= 1 else False) and (
            b"\r\n" in data or b"\n" in data
        )

        is_readline_redraw = self._is_readline_redraw(data)
        is_large_line = len(data) > 200 and not is_enter_marker and b"\n" not in data

        if not is_enter_marker and (is_readline_redraw or is_large_line):
            self._flush_queue(term)
            self._partial_line_buffer = b""

            if b"\x08" in data or b"\x7f" in data:
                self._handle_backspace_in_buffer(data)

            term.feed(data)
            return True

        return False

    # Readline escape sequences that indicate cursor/line editing redraw
    _READLINE_SEQUENCES = frozenset(
        (
            b"\x1b[D",  # Cursor left
            b"\x1b[C",  # Cursor right
            b"\x1b[K",  # Erase to end of line
            b"\x1b[0K",  # Erase to end of line (explicit)
            b"\x1b[1D",  # Move cursor left 1
            b"\x1b[1C",  # Move cursor right 1
            b"\x1b[A",  # Cursor up
            b"\x1b[B",  # Cursor down
            b"\x1b[J",  # Erase display
            b"\x1b[0J",  # Erase display (explicit)
            b"\x1b[H",  # Cursor home
            b"\x1b[?25l",  # Hide cursor
            b"\x1b[?25h",  # Show cursor
            b"\x1b[P",  # Delete char
            b"\x1b[@",  # Insert char
        )
    )

    _SEARCH_PROMPT_PATTERNS = (
        b"(reverse-i-search)",
        b"(i-search)",
        b"(bck-i-search)",
        b"(fwd-i-search)",
        b"(failed ",
    )

    def _is_readline_redraw(self, data: bytes) -> bool:
        """Check if data contains readline redraw sequences."""
        if b"\r" in data:
            return True
        return any(seq in data for seq in self._READLINE_SEQUENCES) or any(
            pat in data for pat in self._SEARCH_PROMPT_PATTERNS
        )

    def _combine_streaming_partial_data(self, data: bytes) -> bytes:
        """Combine current data with partial buffer."""
        if self._partial_line_buffer:
            data = self._partial_line_buffer + data
            self._partial_line_buffer = b""
        return data

    def _handle_streaming_safety_limits(
        self, data: bytes, data_len: int, term: Vte.Terminal
    ) -> bool:
        """Handle safety limits. Returns True if should exit."""
        # Hard limit (1MB)
        if data_len > 1048576:
            self._burst_counter = 100
            self._flush_queue(term)
            term.feed(data)
            return True

        # Adaptive burst detection
        if data_len > 1024:
            self._burst_counter += 1
        else:
            self._burst_counter = 0

        if self._burst_counter > getattr(self, "_burst_threshold", 15):
            if self._at_shell_prompt or b"\x1b]7;" in data or b"\033]7;" in data:
                self._reset_input_buffer()
            self._flush_queue(term)
            term.feed(data)
            return True

        return False

    def _handle_streaming_partial_lines(self, data: bytes, data_len: int) -> bytes:
        """Handle partial line buffering."""
        last_newline_pos = data.rfind(b"\n")

        if last_newline_pos != -1 and last_newline_pos < data_len - 1:
            remainder = data[last_newline_pos + 1 :]

            is_interactive = False
            if len(remainder) < 200:
                rem_str = remainder.decode("utf-8", errors="ignore")
                is_interactive = self._is_remainder_interactive(rem_str)

            if not is_interactive and not self._at_shell_prompt:
                self._partial_line_buffer = remainder
                data = data[: last_newline_pos + 1]

        return data

    def _process_streaming_text(
        self, data: bytes, text: str, term: Vte.Terminal
    ) -> None:
        """Process streaming text with highlighting."""
        # Early primary prompt detection
        self._detect_early_prompt_in_text(text)

        # Process interactive markers
        chunk_is_likely_user_input = self._process_interactive_markers(data, text, term)
        if chunk_is_likely_user_input is None:
            return  # Early return was triggered

        # Handle prompt-related escape sequences
        self._handle_prompt_escape_sequences(data, text, term)

        # Strip NUL markers
        data, text = self._strip_nul_markers(data, text)

        # Handle backspace at prompt
        if self._handle_prompt_backspace(data, term):
            return

        # Get highlighting rules
        context, rules = self._get_streaming_context_and_rules()

        # Check for shell prompt
        self._check_and_update_prompt_state(text)

        # Shell input highlighting
        if self._should_apply_shell_input_highlighting(chunk_is_likely_user_input):
            self._flush_queue(term)
            highlighted_data = self._apply_shell_input_highlighting(text, term)
            if highlighted_data is not None:
                return

        # Handle prompt interactions
        if self._handle_prompt_interactions(data, text, term, context, rules):
            return

        # Apply output highlighting
        self._apply_streaming_highlighting(data, text, term, rules)

    def _detect_early_prompt_in_text(self, text: str) -> None:
        """Detect prompt in text and reset buffer if found."""
        if not (self._input_highlight_buffer and self._at_shell_prompt):
            return

        stripped = _ALL_ESCAPE_SEQ_PATTERN.sub("", text).replace("\x00", "").strip()
        if not (stripped.endswith("$") or stripped.endswith("#")):
            return

        prompt_candidate = stripped[:-1].rstrip()
        if "\n" in prompt_candidate:
            prompt_candidate = prompt_candidate.split("\n")[-1].strip()

        if prompt_candidate and self._looks_like_prompt(prompt_candidate):
            self._reset_input_buffer()
            self._need_color_reset = True

    def _looks_like_prompt(self, text: str) -> bool:
        """Check if text looks like a shell prompt."""
        return bool(
            _SHELL_NAME_PROMPT_PATTERN.match(text)
            or "@" in text
            or ":" in text
            or text.endswith("~")
            or text.endswith("/")
        )

    def _process_interactive_markers(
        self, data: bytes, text: str, term: Vte.Terminal
    ) -> bool | None:
        """Process interactive markers. Returns user_input flag or None for early return."""
        chunk_is_likely_user_input = False

        if len(data) >= 1024:
            return chunk_is_likely_user_input

        has_marker, is_user_input, is_newline = self._detect_interactive_marker(data)

        # Handle backspace early return
        if has_marker and is_user_input and len(data) > 1 and data[1] in (0x08, 0x7F):
            if self._at_shell_prompt:
                self._handle_backspace_in_buffer(data)
                clean_data = data.replace(b"\x00", b"")
                if clean_data:
                    term.feed(clean_data)
                return None

        if has_marker:
            if is_newline:
                self._handle_interactive_newline(text)
            elif is_user_input:
                chunk_is_likely_user_input = True
                self._activate_shell_prompt_mode()

        return chunk_is_likely_user_input

    def _handle_interactive_newline(self, text: str) -> None:
        """Handle newline in interactive mode."""
        if self._at_shell_prompt:
            is_in_unclosed_block = self._is_in_unclosed_multiline_block(
                self._input_highlight_buffer
            )
            stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
            has_continuation_prompt = ">" in stripped_text.strip()

            if is_in_unclosed_block or has_continuation_prompt:
                if not self._input_highlight_buffer.endswith("\n"):
                    self._input_highlight_buffer += "\n"
            else:
                self._at_shell_prompt = False
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, False)

        self._suppress_shell_input_highlighting = False

    def _activate_shell_prompt_mode(self) -> None:
        """Activate shell prompt mode."""
        if not self._at_shell_prompt:
            self._at_shell_prompt = True
            self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self._reset_input_buffer()
            self._need_color_reset = True
            self._suppress_shell_input_highlighting = False

    def _handle_prompt_escape_sequences(
        self, data: bytes, text: str, _term: Vte.Terminal
    ) -> None:
        """Handle escape sequences at prompt."""
        if not self._at_shell_prompt or not (b"\x1b" in data or b"\r" in data):
            return

        is_backspace = b"\x08" in data or b"\x7f" in data

        if is_backspace and self._input_highlight_buffer:
            self._handle_backspace_in_buffer(data)
        elif self._input_highlight_buffer:
            self._handle_non_backspace_at_prompt(data, text)

    def _handle_non_backspace_at_prompt(self, data: bytes, text: str) -> None:
        """Handle non-backspace escape sequences at prompt."""
        is_possible_newline = b"\r\n" in data or (b"\r" in data and b"\n" in data)

        stripped = _ALL_ESCAPE_SEQ_PATTERN.sub("", text).strip()
        has_primary_prompt = self._check_for_primary_prompt(stripped)
        is_in_multiline = self._is_in_unclosed_multiline_block(
            self._input_highlight_buffer
        )

        if has_primary_prompt:
            self._reset_input_buffer()
            self._need_color_reset = True
        elif not is_possible_newline and not is_in_multiline:
            self._suppress_shell_input_highlighting = True
            self._reset_input_buffer()
            self._need_color_reset = True

    def _check_for_primary_prompt(self, stripped: str) -> bool:
        """Check if stripped text contains primary prompt."""
        if not (stripped.endswith("$") or stripped.endswith("#")):
            return False

        prompt_part = stripped[:-1].strip()
        return bool(
            _SHELL_NAME_PROMPT_PATTERN.match(prompt_part)
            or "@" in prompt_part
            or ":" in prompt_part
        )

    def _strip_nul_markers(self, data: bytes, text: str) -> tuple[bytes, str]:
        """Strip NUL markers from data and text."""
        if b"\x00" in data:
            data = data.replace(b"\x00", b"")
            text = text.replace("\x00", "")
        return data, text

    def _handle_prompt_backspace(self, data: bytes, term: Vte.Terminal) -> bool:
        """Handle backspace at prompt. Returns True if handled."""
        if not self._at_shell_prompt:
            return False

        if b"\x08" in data or b"\x7f" in data:
            chars_removed = self._handle_backspace_in_buffer(data)
            if chars_removed > 0:
                term.feed(data)
                return True

        return False

    def _get_streaming_context_and_rules(self) -> tuple:
        """Get context and rules for streaming highlighting."""
        with self._highlighter._lock:
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            rules = self._highlighter._get_active_rules(context)
        return context, rules

    def _should_apply_shell_input_highlighting(
        self, chunk_is_likely_user_input: bool
    ) -> bool:
        """Check if shell input highlighting should be applied."""
        return (
            self._at_shell_prompt
            and self._shell_input_highlighter.enabled
            and chunk_is_likely_user_input
            and not self._suppress_shell_input_highlighting
        )

    def _handle_prompt_interactions(
        self, data: bytes, text: str, term: Vte.Terminal, context: str, rules
    ) -> bool:
        """Handle prompt interactions. Returns True if should exit."""
        if not self._at_shell_prompt:
            return False

        if "\n" not in text:
            self._flush_queue(term)
            term.feed(data)
            return True

        # Handle newline at prompt
        if self._input_highlight_buffer.strip():
            return self._handle_prompt_newline_with_buffer(data, text, term)
        else:
            return self._handle_prompt_newline_empty_buffer(data, term, context, rules)

    def _handle_prompt_newline_with_buffer(
        self, data: bytes, text: str, term: Vte.Terminal
    ) -> bool:
        """Handle newline at prompt with content in buffer."""
        is_in_unclosed_block = self._is_in_unclosed_multiline_block(
            self._input_highlight_buffer
        )
        stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
        has_continuation_prompt = (
            stripped_text.strip() == ">" or stripped_text.strip().endswith(">")
        )

        if is_in_unclosed_block or has_continuation_prompt:
            if not self._input_highlight_buffer.endswith("\n"):
                self._input_highlight_buffer += "\n"
            self._flush_queue(term)
            term.feed(data)
            return True
        else:
            self._at_shell_prompt = False
            self._reset_input_buffer()
            return False  # Continue to output highlighting

    def _handle_prompt_newline_empty_buffer(
        self, data: bytes, term: Vte.Terminal, context: str, rules
    ) -> bool:
        """Handle newline at prompt with empty buffer."""
        # Re-obtain context (may have been updated)
        with self._highlighter._lock:
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            if context:
                rules = self._highlighter._get_active_rules(context)

        if context and rules:
            self._at_shell_prompt = False
            self._reset_input_buffer()
            return False  # Continue to output highlighting
        else:
            self._flush_queue(term)
            term.feed(data)
            return True

    def _apply_streaming_highlighting(
        self, data: bytes, text: str, term: Vte.Terminal, rules
    ) -> None:
        """Apply output highlighting to streaming data."""
        output_enabled = self._highlighter.is_enabled_for_type(self._terminal_type)
        if not rules or not output_enabled:
            self._flush_queue(term)
            term.feed(data)
            return

        # Highlight lines
        self._highlight_streaming_lines(text, rules)

        if not self._queue_processing:
            self._queue_processing = True
            self._process_line_queue(term)

    def _highlight_streaming_lines(self, text: str, rules) -> None:
        """Highlight lines and add to queue."""
        lines = text.splitlines(keepends=True)
        highlight_line = self._highlighter._apply_highlighting_to_line
        skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

        for i, line in enumerate(lines):
            encoded = self._process_streaming_line(
                line, i, skip_first, highlight_line, rules
            )
            self._line_queue.append(encoded)

    def _process_streaming_line(
        self, line: str, index: int, skip_first: bool, highlight_line, rules
    ) -> bytes:
        """Process a single line for streaming highlighting."""
        # Skip first line if needed
        if skip_first and index == 0:
            return line.encode("utf-8", errors="replace")

        # Empty or whitespace-only lines
        if not line or line in ("\n", "\r", "\r\n"):
            return line.encode("utf-8")

        # OSC7 sequences
        if "\x1b]7;" in line or "\033]7;" in line:
            return line.encode("utf-8", errors="replace")

        # Apply highlighting
        content, ending = self._extract_line_content_and_ending(line)
        if content:
            highlighted = highlight_line(content, rules) + ending
        else:
            highlighted = ending

        return highlighted.encode("utf-8", errors="replace")

    def _extract_line_content_and_ending(self, line: str) -> tuple[str, str]:
        """Extract content and ending from a line."""
        if line[-1] == "\n":
            if len(line) > 1 and line[-2] == "\r":
                return line[:-2], "\r\n"
            return line[:-1], "\n"
        elif line[-1] == "\r":
            return line[:-1], "\r"
        return line, ""

    def _reset_input_buffer(self) -> None:
        """Reset shell input highlighting buffer state."""
        if self._input_highlight_buffer:
            self._input_highlight_buffer = ""
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

    def _check_and_update_prompt_state(self, text: str) -> bool:
        """
        Check if text contains a shell prompt (primary or continuation).
        Primary prompt detection is handled by termprop-changed signal,
        but we also detect prompts directly for shells without termprop support.

        Returns True if a prompt was detected.
        """
        # Early exit: if no potential prompt characters, skip expensive processing
        if not any(c in text for c in "$#%>❯"):
            return False

        stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text).replace("\x00", "")
        stripped_clean = stripped_text.strip()

        # Check for continuation prompt ("> ")
        if stripped_clean == ">":
            return self._set_continuation_prompt_state()

        # Get the last line only - prompts are single lines
        last_line = self._extract_last_line(stripped_clean)

        # Sanity check: prompts are short (typically < 100 chars)
        if len(last_line) > 100:
            return False

        return self._check_prompt_ending(last_line)

    def _set_continuation_prompt_state(self) -> bool:
        """Set prompt state for continuation prompt (>)."""
        self._at_shell_prompt = True
        self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
        if self._input_highlight_buffer and not self._input_highlight_buffer.endswith(
            "\n"
        ):
            self._input_highlight_buffer += "\n"
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0
        return True

    def _extract_last_line(self, text: str) -> str:
        """Extract last line from text, handling both \\n and \\r."""
        last_line = text.rsplit("\n", 1)[-1].strip()
        return last_line.rsplit("\r", 1)[-1].strip()

    def _check_prompt_ending(self, last_line: str) -> bool:
        """Check if last_line ends with a prompt character."""
        # Check for modern prompts (Starship, Oh-My-Zsh, Powerlevel10k, etc.)
        fancy_prompt_chars = ("❯", "➜", "λ", "›")
        traditional_prompt_chars = ("#", "%")

        for char in fancy_prompt_chars + traditional_prompt_chars:
            if last_line.endswith(char):
                if char in fancy_prompt_chars:
                    return self._set_prompt_detected_state()
                # For traditional prompts, verify it looks like a shell prompt
                if self._is_valid_traditional_prompt(last_line[:-1].strip()):
                    return self._set_prompt_detected_state()

        return False

    def _is_valid_traditional_prompt(self, prompt_part: str) -> bool:
        """Check if prompt_part looks like a valid traditional shell prompt."""
        # Match shell name patterns: sh-5.3, bash, etc.
        if _SHELL_NAME_PROMPT_PATTERN.match(prompt_part):
            return True
        # Match paths like ~ or /home/user or user@host:~
        return (
            prompt_part.endswith("~")
            or prompt_part.endswith("/")
            or "@" in prompt_part
            or ":" in prompt_part
        )

    def _set_prompt_detected_state(self) -> bool:
        """Update state when a prompt is detected."""
        self._at_shell_prompt = True
        self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
        self._reset_input_buffer()
        self._need_color_reset = True
        # Clear highlighting context when returning to prompt
        self._highlighter.clear_context(self._proxy_id)
        self._reset_cat_state()
        return True

    def _apply_shell_input_highlighting(
        self, text: str, term: Vte.Terminal
    ) -> Optional[bytes]:
        """
        Apply syntax highlighting to shell input being echoed.

        This method handles the case where we're at a shell prompt and
        characters are being echoed back as the user types.

        Args:
            text: The echoed text from PTY
            term: The VTE terminal

        Returns:
            bytes if handled, None if shell input highlighting didn't apply
        """
        if not self._shell_input_highlighter.enabled:
            return None

        # Pre-process and validate text
        text = text.lstrip("\x00")
        if not text or text.startswith("\x1b"):
            return None

        # Handle special input types (returns b"" if handled, None to continue)
        result = self._handle_shell_input_special_cases(text, term)
        if result is not _CONTINUE_PROCESSING:
            return result  # type: ignore[return-value]  # Special case was detected

        # Validate text for highlighting
        if not self._is_valid_shell_input(text):
            return None

        # Handle color reset if needed
        self._handle_color_reset_if_needed(text, term)

        # Append to buffer
        self._append_to_input_buffer(text)

        # Apply highlighting
        return self._highlight_shell_input(text, term)

    def _handle_shell_input_special_cases(
        self, text: str, _term: Vte.Terminal
    ) -> Optional[bytes] | object:
        """Handle special cases in shell input. Returns result, None to skip, or sentinel to continue."""
        # Handle backspace
        if "\x08" in text or "\x7f" in text:
            data = text.encode("utf-8", errors="replace")
            if self._handle_backspace_in_buffer(data) > 0:
                return None

        # Handle newline
        if "\n" in text:
            self._handle_newline_in_shell_input(text)
            return None

        # Escape sequences after newline handling
        if "\x1b" in text or "\033" in text:
            return None

        # Large chunks are likely command output
        if len(text) > 10:
            return None

        # Readline redraw
        if "\r" in text:
            self._reset_input_buffer()
            self._need_color_reset = True
            return None

        return _CONTINUE_PROCESSING

    def _handle_newline_in_shell_input(self, text: str) -> None:
        """Handle newline in shell input."""
        if self._input_highlight_buffer.strip():
            is_in_unclosed_block = self._is_in_unclosed_multiline_block(
                self._input_highlight_buffer
            )
            stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
            has_continuation_prompt = (
                stripped_text.strip() == ">" or stripped_text.strip().endswith(">")
            )

            if is_in_unclosed_block or has_continuation_prompt:
                self._input_highlight_buffer += "\n"
            else:
                self._at_shell_prompt = False
                self._reset_input_buffer()
        return None

    def _is_valid_shell_input(self, text: str) -> bool:
        """Check if text is valid for shell input highlighting."""
        # Only printable characters
        if not all(c.isprintable() or c == " " for c in text):
            return False

        # Filter out literal escape sequences
        if "^[" in text:
            return False

        # Arrow keys shown as literal text
        if text in ("[A", "[B", "[C", "[D", "[H", "[F"):
            return False

        # Continuation prompt
        text_stripped = text.strip()
        if text_stripped in (">", "> "):
            return False

        return True

    def _handle_color_reset_if_needed(self, text: str, term: Vte.Terminal) -> None:
        """Send color reset if needed before input highlighting."""
        if self._need_color_reset and text and text[0].isprintable():
            term.feed(b"\x1b[0m")
            self._need_color_reset = False

    def _append_to_input_buffer(self, text: str) -> None:
        """Append text to input highlight buffer."""
        if not self._input_highlight_buffer:
            self._input_highlight_buffer = text
        else:
            self._input_highlight_buffer += text

    def _highlight_shell_input(self, text: str, term: Vte.Terminal) -> Optional[bytes]:
        """Apply highlighting to shell input."""
        try:
            from pygments import lex
            from pygments.lexers import BashLexer

            highlighter = self._shell_input_highlighter
            lexer = highlighter._lexer or BashLexer()
            formatter = highlighter._formatter

            tokens = list(lex(self._input_highlight_buffer, lexer))
            if not tokens:
                term.feed(text.encode("utf-8"))
                return b""

            # Find actual token for the typed character
            actual_token_type, actual_token_value = self._find_actual_token(
                tokens, text
            )

            # Enhance token type for better highlighting
            enhanced_token_type = self._enhance_token_type(
                actual_token_type, actual_token_value
            )

            # Apply highlighting
            return self._apply_token_highlighting(
                text,
                term,
                enhanced_token_type,
                actual_token_type,
                actual_token_value,
                formatter,
            )

        except Exception as e:
            self.logger.debug(f"Shell input highlighting failed: {e}")
            return None

    def _find_actual_token(self, tokens: list, text: str) -> tuple:
        """Find the actual token for the typed character."""
        actual_token_type = None
        actual_token_value = None

        for token_type, token_value in reversed(tokens):
            if token_value.strip():
                actual_token_type = token_type
                actual_token_value = token_value.rstrip("\n")
                break
            elif token_value == " " and text == " ":
                actual_token_type = token_type
                actual_token_value = token_value
                break

        if actual_token_type is None:
            if len(tokens) >= 2:
                actual_token_type, actual_token_value = tokens[-2]
            else:
                actual_token_type, actual_token_value = tokens[-1]
            actual_token_value = actual_token_value.rstrip("\n")

        return actual_token_type, actual_token_value

    def _enhance_token_type(self, token_type, token_value):
        """Enhance token type for better command/option highlighting."""
        from pygments.token import Token

        if not token_value:
            return token_type

        # Check for options
        if token_value.startswith("--") or (
            token_value.startswith("-") and len(token_value) > 1
        ):
            return Token.Name.Attribute

        # Check for command position
        if token_type in (Token.Text, Token.Name):
            if self._is_command_position(token_value):
                WARNING_COMMANDS = {"sudo", "doas", "pkexec", "rm", "dd"}
                if token_value in WARNING_COMMANDS:
                    return Token.Name.Exception
                return Token.Name.Function

        return token_type

    def _is_command_position(self, token_value: str) -> bool:
        """Check if token is in command position."""
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
        current_line = self._input_highlight_buffer.split("\n")[-1].strip()
        words_before = current_line.rsplit(token_value, 1)[0].rstrip()

        is_command_position = not words_before or words_before.endswith(
            (
                "|",
                ";",
                "&&",
                "||",
                "(",
                "`",
                "$(",
            )
        )

        if not is_command_position and words_before:
            last_word = words_before.split()[-1] if words_before.split() else ""
            if last_word in PREFIX_COMMANDS:
                is_command_position = True

        return is_command_position

    def _apply_token_highlighting(
        self,
        text: str,
        term: Vte.Terminal,
        enhanced_token_type,
        actual_token_type,
        actual_token_value,
        formatter,
    ) -> Optional[bytes]:
        """Apply token highlighting to the terminal.

        Returns:
            b"" if highlighting was applied successfully
            None if no highlighting was applied (raw text was fed)
        """
        # Check for retroactive recolor
        prev_token_type = self._prev_shell_input_token_type
        prev_token_len = self._prev_shell_input_token_len
        current_token_len = len(actual_token_value) if actual_token_value else 0

        should_retroactive_recolor = (
            prev_token_type is not None
            and enhanced_token_type != prev_token_type
            and current_token_len > 1
            and current_token_len == prev_token_len + 1
            and not self._suppress_shell_input_highlighting
        )

        self._prev_shell_input_token_type = enhanced_token_type
        self._prev_shell_input_token_len = current_token_len

        # Get style codes
        if not hasattr(formatter, "style_string"):
            term.feed(text.encode("utf-8"))
            return None  # No highlighting applied

        style_codes = self._get_style_codes(
            formatter, enhanced_token_type, actual_token_type
        )
        if not style_codes:
            term.feed(text.encode("utf-8"))
            return None  # No highlighting applied

        ansi_start, ansi_end = style_codes
        if not ansi_start:
            term.feed(text.encode("utf-8"))
            return None  # No highlighting applied

        # Apply retroactive recolor if needed
        if should_retroactive_recolor and actual_token_value:
            self._apply_retroactive_recolor(
                term,
                actual_token_value,
                current_token_len,
                prev_token_len,
                ansi_start,
                ansi_end,
            )
            return b""  # Highlighting applied

        # Normal highlighting
        highlighted_text = f"{ansi_start}{text}{ansi_end}"
        term.feed(highlighted_text.encode("utf-8"))
        return b""  # Highlighting applied

    def _get_style_codes(
        self, formatter, enhanced_token_type, actual_token_type
    ) -> tuple:
        """Get style codes for token type."""
        token_str = str(enhanced_token_type)
        style_codes = formatter.style_string.get(token_str)

        if not style_codes:
            token_str = str(actual_token_type)
            style_codes = formatter.style_string.get(token_str)

        return style_codes

    def _apply_retroactive_recolor(
        self,
        term: Vte.Terminal,
        actual_token_value: str,
        current_token_len: int,
        prev_token_len: int,
        ansi_start: str,
        ansi_end: str,
    ) -> None:
        """Apply retroactive recoloring when token type changes."""
        self.logger.debug(
            f"[RETROACTIVE] Recoloring: token={repr(actual_token_value)}, "
            f"len={current_token_len}, prev_len={prev_token_len}"
        )

        chars_to_recolor = current_token_len - 1
        if chars_to_recolor > 0:
            cursor_back = f"\x1b[{chars_to_recolor}D"
            highlighted_text = (
                f"{cursor_back}{ansi_start}{actual_token_value}{ansi_end}"
            )
            term.feed(highlighted_text.encode("utf-8"))
        else:
            term.feed(f"{ansi_start}{actual_token_value}{ansi_end}".encode("utf-8"))

    def _process_line_queue(self, term: Vte.Terminal) -> bool:
        """
        Process multiple lines from queue per callback for efficiency.

        This is the SINGLE consumer for the line queue. It processes
        a batch of lines per callback, balancing responsiveness with efficiency.

        Uses deque.popleft() for O(1) performance.

        Returns:
            bool: GTK callback convention - GLib.SOURCE_REMOVE removes callback.
                  This method always returns SOURCE_REMOVE because it
                  re-schedules itself via idle_add if more work is pending.
        """
        if not self._running or self._widget_destroyed:
            self._queue_processing = False
            return GLib.SOURCE_REMOVE

        self._process_queue_batch(term)
        return GLib.SOURCE_REMOVE  # Evaluates to False  # Remove this callback

    def _process_queue_batch(self, term: Vte.Terminal) -> None:
        """Process a batch of lines from the queue."""
        try:
            if not self._line_queue:
                self._queue_processing = False
                return

            # Process up to 10 lines per callback for efficiency
            # This reduces GTK overhead while maintaining responsiveness
            lines_to_feed = self._collect_lines_batch()

            # Feed all lines in one batch
            if lines_to_feed:
                term.feed(b"".join(lines_to_feed))

            # Schedule next batch if queue not empty
            if self._line_queue:
                GLib.idle_add(self._process_line_queue, term)
            else:
                self._queue_processing = False

        except Exception:
            self._queue_processing = False

    def _collect_lines_batch(self) -> list:
        """Collect up to 10 lines from the queue."""
        lines = []
        for _ in range(10):
            if self._line_queue:
                lines.append(self._line_queue.popleft())
            else:
                break
        return lines  # Remove this callback

    def _is_remainder_interactive(self, rem_str: str) -> bool:
        """Check if remainder looks like interactive prompt content."""
        stripped = rem_str.strip()
        # Check prompt endings
        if stripped.endswith(("$", "#", "%", ">", ":")):
            return True
        # Check prompt with space (current input)
        if any(t in rem_str for t in ("$ ", "# ", "% ", "> ")):
            return True
        # Check for escape sequences (prompt styling, OSC7)
        if "\x1b[" in rem_str or "\x1b]7;" in rem_str or "\033]7;" in rem_str:
            return True
        return False
