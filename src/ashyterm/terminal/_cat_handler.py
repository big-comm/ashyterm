# ashyterm/terminal/_cat_handler.py
"""Cat mode handler mixin: Pygments syntax highlighting for cat/file output."""

from typing import Optional

import gi

gi.require_version("Vte", "3.91")
gi.require_version("GLib", "2.0")
from ..utils.re_engine import engine as re_engine
from ..utils.shell_echo import is_echo_terminator
from gi.repository import GLib, Vte

from .highlighter.constants import (
    ANSI_COLOR_PATTERN as _ANSI_COLOR_PATTERN,
)
from .highlighter.constants import (
    ANSI_SEQ_PATTERN as _ANSI_SEQ_PATTERN,
)
from .highlighter.constants import (
    CSI_CONTROL_PATTERN as _CSI_CONTROL_PATTERN,
)
from .highlighter.constants import (
    SHELL_NAME_PROMPT_PATTERN as _SHELL_NAME_PROMPT_PATTERN,
)

_PROMPT_MARKER = b"__PROMPT_DETECTED__"


class CatModeHandler:
    """Mixin providing cat/file output syntax highlighting via Pygments."""

    # Type stubs for attributes initialized in parent class (_highlighter_impl.py)
    _cat_limit_reached: bool
    _cat_bytes_processed: int
    _cat_filename: str
    _at_shell_prompt: bool

    def _process_cat_output(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Process cat output through Pygments for syntax highlighting.
        Includes safety limit, Strict Queue Ordering, Partial Buffer Flushing,
        and Robust Echo Skipping.
        """
        from ..settings.manager import get_settings_manager

        settings = get_settings_manager()
        if not self._is_cat_colorization_enabled(settings):
            term.feed(data)
            return

        # 1. Flush standard line queue to ensure command echo order
        self._flush_queue(term)

        # 2. Clear any leftover remainder from streaming mode
        self._partial_line_buffer = b""

        try:
            # Check safety limit
            if self._check_cat_safety_limit(data, term):
                return

            text = self._decode_and_validate(data)
            if text is None:
                term.feed(data)
                return

            # Check for shell input control sequences
            if self._is_shell_input_control(text):
                self._reset_cat_context_and_buffer()
                term.feed(data)
                return

            # Setup cat filename and lexer
            self._setup_cat_filename()
            self._ensure_cat_queue_exists()

            # Check echo skipping
            if self._highlighter.should_skip_first_output(self._proxy_id):
                self._cat_waiting_for_newline = True

            # Process lines
            self._process_cat_lines(text)

            # Process batch
            self._trigger_cat_queue_processing(term)

        except Exception as e:
            self.logger.error(f"Cat highlighting error: {e}")
            term.feed(data)

    def _is_cat_colorization_enabled(self, settings) -> bool:
        """Check if cat colorization is enabled."""
        output_enabled = self._highlighter.is_enabled_for_type(self._terminal_type)
        return output_enabled and settings.get("cat_colorization_enabled", True)

    def _check_cat_safety_limit(self, data: bytes, term: Vte.Terminal) -> bool:
        """Check and handle cat safety limit. Returns True if limit exceeded."""
        CAT_HIGHLIGHT_LIMIT = 1048576  # 1MB
        data_len = len(data)

        if self._cat_limit_reached or (
            self._cat_bytes_processed + data_len > CAT_HIGHLIGHT_LIMIT
        ):
            if not self._cat_limit_reached:
                self._cat_limit_reached = True

            term.feed(data)

            # Check if shell prompt
            if self._at_shell_prompt or b"\x1b]7;" in data or b"\033]7;" in data:
                self._reset_cat_context_and_buffer()
            return True

        self._cat_bytes_processed += data_len
        return False

    def _decode_and_validate(self, data: bytes) -> str | None:
        """Decode data and validate. Returns None if invalid."""
        text = data.decode("utf-8", errors="replace").replace("\x00", "")
        return text if text else None

    def _is_shell_input_control(self, text: str) -> bool:
        """Check if text is shell input control sequence."""
        return text in ("\x08\x1b[K", "\x08 \x08") or (
            text.startswith("\x08") and len(text) <= 5
        )

    def _reset_cat_context_and_buffer(self) -> None:
        """Reset cat context and buffers."""
        self._highlighter.clear_context(self._proxy_id)
        self._reset_cat_state()
        self._reset_input_buffer()

    def _setup_cat_filename(self) -> None:
        """Setup filename for cat highlighting."""
        import os.path

        full_command = self._highlighter.get_full_command(self._proxy_id)
        new_filename = self._extract_filename_from_cat_command(full_command) or ""

        if new_filename != self._cat_filename:
            self._cat_filename = new_filename
            self._pygments_lexer = None
            self._content_buffer: list[str] = []
            self._cat_lines_processed = 0
            self._pending_lines: list[str] = []
            self._php_in_multiline_comment = False

            _, ext = os.path.splitext(new_filename)
            self._pygments_needs_content_detection = bool(not ext and new_filename)

    def _ensure_cat_queue_exists(self) -> None:
        """Ensure cat queue is initialized."""
        if not hasattr(self, "_cat_queue"):
            from collections import deque

            self._cat_queue: deque = deque()
            self._cat_queue_processing = False

    def _process_cat_lines(self, text: str) -> None:
        """Process lines of cat output."""
        lines = text.splitlines(keepends=True)

        for line in lines:
            # Handle echo skipping
            if self._cat_waiting_for_newline:
                self._cat_queue.append(line.encode("utf-8", errors="replace"))
                if is_echo_terminator(line):
                    self._cat_waiting_for_newline = False
                continue

            # Handle prompt splits and special cases
            if self._handle_cat_line_special_cases(line):
                continue

            # Process normal content line
            self._process_cat_content_line(line)

    def _handle_cat_line_special_cases(self, line: str) -> bool:
        """Handle special cases in cat output. Returns True if handled."""
        # Check for embedded prompt (OSC7/OSC0)
        for seq in ("\x1b]7;", "\x1b]0;"):
            if seq in line:
                idx = line.find(seq)
                if idx > 0:
                    self._handle_prompt_split(line[:idx], line[idx:])
                else:
                    self._cat_queue.append(_PROMPT_MARKER)
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                return True

        # Check for bracketed paste mode sequence
        bpm_idx = line.find("\x1b[?2004h")
        if bpm_idx >= 0:
            if bpm_idx > 0:
                self._handle_prompt_split(line[:bpm_idx], line[bpm_idx:])
            else:
                self._cat_queue.append(_PROMPT_MARKER)
                self._cat_queue.append(line.encode("utf-8", errors="replace"))
            return True

        return False

    def _process_cat_content_line(self, line: str) -> None:
        """Process a content line in cat output."""
        content, ending = self._split_line_ending(line)

        # Check for embedded prompt pattern
        if self._check_embedded_prompt_pattern(content, ending):
            return

        # Check for shell prompt
        lines_done = getattr(self, "_cat_lines_processed", 0)
        is_potential_prompt = lines_done > 0 or (len(content) < 30 and "$" in content)

        if is_potential_prompt and self._is_shell_prompt(content):
            self._cat_queue.append(_PROMPT_MARKER)
            self._cat_queue.append(line.encode("utf-8", errors="replace"))
            return

        # Skip pure ANSI control sequences
        clean_content = _CSI_CONTROL_PATTERN.sub("", content).strip()
        if not clean_content and (not content or content.startswith("\x1b")):
            self._cat_queue.append(line.encode("utf-8", errors="replace"))
            return

        # Highlight content
        self._highlight_and_queue_content(content, ending, line)

    def _check_embedded_prompt_pattern(self, content: str, ending: str) -> bool:
        """Check for embedded prompt pattern. Returns True if found."""
        prompt_patterns = [
            r"([a-zA-Z_][a-zA-Z0-9_-]*@[^\s:]+:[^\$]+\$\s)",
            r"(sh-\d+\.\d+\$\s)",
            r"(bash-\d+\.\d+\$\s)",
        ]
        for pattern in prompt_patterns:
            match = re_engine.search(pattern, content)
            if match and match.start() > 0:
                self._handle_prompt_split(
                    content[: match.start()], content[match.start() :] + ending
                )
                return True
        return False

    def _highlight_and_queue_content(
        self, content: str, ending: str, original_line: str
    ) -> None:
        """Highlight content and add to queue."""
        has_ansi_colors = bool(_ANSI_COLOR_PATTERN.search(content))
        is_content = bool(content.strip())
        lines_done = getattr(self, "_cat_lines_processed", 0)

        if is_content and not has_ansi_colors:
            highlighted = self._highlight_line_with_pygments(
                content, self._cat_filename
            )
            current_lexer = getattr(self, "_pygments_lexer", None)

            if current_lexer is not None:
                self._flush_pending_to_cat_queue()
                self._cat_queue.append(
                    (highlighted + ending).encode("utf-8", errors="replace")
                )
            else:
                pending = getattr(self, "_pending_lines", [])
                pending.append((content, ending))
                self._pending_lines = pending

            self._cat_lines_processed = lines_done + 1
        else:
            self._cat_queue.append(original_line.encode("utf-8", errors="replace"))
            if is_content:
                self._cat_lines_processed = lines_done + 1

    def _flush_pending_to_cat_queue(self) -> None:
        """Flush pending lines to cat queue."""
        pending = getattr(self, "_pending_lines", [])
        if pending:
            for pending_content, pending_ending in pending:
                pending_highlighted = self._highlight_line_with_pygments(
                    pending_content, self._cat_filename
                )
                self._cat_queue.append(
                    (pending_highlighted + pending_ending).encode(
                        "utf-8", errors="replace"
                    )
                )
            self._pending_lines = []

    def _trigger_cat_queue_processing(self, term: Vte.Terminal) -> None:
        """Trigger cat queue processing."""
        if self._cat_queue and not self._cat_queue_processing:
            self._process_cat_queue_batch(term, immediate=True)
            if self._cat_queue:
                self._cat_queue_processing = True
                GLib.idle_add(self._process_cat_queue, term)

    def _is_shell_prompt(self, line: str) -> bool:
        """
        Fallback prompt detection for shells without VTE shell integration.
        Primary detection uses TERMPROP_SHELL_PRECMD via termprop-changed signal.
        """
        if len(line) < 3:
            return False

        # OSC7 detection (file:// URI indicates shell ready)
        if self._check_osc_prompt(line, "\x1b]7;", "\033]7;", "file://"):
            return True

        # OSC0 (title setting, often sent with prompt)
        if self._check_osc_prompt(line, "\x1b]0;", "\033]0;"):
            return True

        # Traditional prompt patterns
        return self._check_traditional_prompt(line)

    def _check_osc_prompt(
        self, line: str, seq1: str, seq2: str, required: str | None = None
    ) -> bool:
        """Check if line contains OSC sequence indicating prompt."""
        if seq1 not in line and seq2 not in line:
            return False
        if required and required not in line:
            return False
        pos = line.find(seq1) if seq1 in line else line.find(seq2)
        prefix = _ANSI_SEQ_PATTERN.sub("", line[:pos]).replace("\x00", "").strip()
        return not prefix

    def _check_traditional_prompt(self, line: str) -> bool:
        """Check for traditional shell prompt patterns."""
        clean = _ANSI_SEQ_PATTERN.sub("", line).replace("\x00", "").strip()

        # user@host:path$ pattern with space
        if clean.endswith(("$ ", "# ", "% ")) and "@" in clean:
            return True

        # Shell name prompts: sh-5.3$, bash$
        if _SHELL_NAME_PROMPT_PATTERN.match(clean.rstrip("$#% ")):
            return True

        # Powerline prompts
        if clean and clean[-1] in ("➜", "❯", "»"):
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

            # Get or detect lexer
            current_lexer = self._get_or_detect_lexer(filename, line)

            # Still no lexer? Return plain text
            if current_lexer is None:
                return line

            # Get or create formatter
            formatter = self._get_or_create_formatter()

            # Handle PHP multi-line comments specially
            is_php = filename and filename.lower().endswith(".php")
            if is_php:
                php_result = self._handle_php_multiline_comment(line)
                if php_result is not None:
                    return php_result

            # Highlight using Pygments
            return highlight(line, current_lexer, formatter).rstrip("\n")

        except Exception as e:
            self.logger.error(f"Highlighting error: {e}")
            return line

    def _get_or_detect_lexer(self, filename: str, line: str):
        """Get existing lexer or detect one for the file."""
        current_lexer = getattr(self, "_pygments_lexer", None)
        if current_lexer is not None:
            return current_lexer

        needs_content_detection = getattr(
            self, "_pygments_needs_content_detection", False
        )

        # Try filename-based detection first
        if filename and not needs_content_detection:
            lexer = self._detect_lexer_by_filename(filename)
            if lexer:
                return lexer

        # Content-based detection
        if needs_content_detection or not filename:
            self._pygments_needs_content_detection = True
            lexer = self._detect_lexer_by_content(line)
            if lexer:
                return lexer

        return getattr(self, "_pygments_lexer", None)

    def _detect_lexer_by_filename(self, filename: str):
        """Detect lexer based on filename."""
        from pygments.lexers import PhpLexer, get_lexer_for_filename
        from pygments.util import ClassNotFound

        try:
            lexer = get_lexer_for_filename(filename)
            self._pygments_lexer = lexer

            # For PHP, use startinline=True
            if filename.lower().endswith(".php"):
                self._pygments_lexer = PhpLexer(startinline=True)
                self._php_in_multiline_comment = False

            return self._pygments_lexer
        except ClassNotFound:
            self._pygments_needs_content_detection = True
            return None

    def _detect_lexer_by_content(self, line: str):
        """Detect lexer based on file content."""
        from pygments.lexers import TextLexer

        # Initialize buffer if needed
        if not hasattr(self, "_content_buffer"):
            self._content_buffer = []

        # Add clean line to buffer
        clean = line.strip().lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08")
        if clean and not clean.startswith("\x1b"):
            self._content_buffer.append(clean)

            # Try shebang detection on first line
            if len(self._content_buffer) == 1 and clean.startswith("#!"):
                lexer = self._detect_lexer_from_shebang(clean)
                if lexer:
                    return lexer

        # Try guess_lexer after 3+ lines
        if len(self._content_buffer) >= 3:
            lexer = self._try_guess_lexer()
            if lexer:
                return lexer

        # Give up after 10 lines - use TextLexer
        if len(self._content_buffer) >= 10:
            self._pygments_lexer = TextLexer()
            self._pygments_needs_content_detection = False
            self._content_buffer = []

        return None

    def _detect_lexer_from_shebang(self, shebang_line: str):
        """Detect lexer from shebang line."""
        from pygments.lexers import get_lexer_by_name

        shebang = shebang_line.lower()
        shebang_mapping = {
            ("bash", "/sh", " sh", "zsh", "ksh", "dash", "fish"): "bash",
            ("python",): "python",
            ("perl",): "perl",
            ("ruby",): "ruby",
            ("node",): "javascript",
        }

        for patterns, lexer_name in shebang_mapping.items():
            if any(p in shebang for p in patterns):
                self._pygments_lexer = get_lexer_by_name(lexer_name)
                self._pygments_needs_content_detection = False
                return self._pygments_lexer

        return None

    def _try_guess_lexer(self):
        """Try to guess lexer from content buffer."""
        from pygments.lexers import TextLexer, guess_lexer

        try:
            content = "\n".join(self._content_buffer)
            guessed = guess_lexer(content)
            if not isinstance(guessed, TextLexer):
                self._pygments_lexer = guessed
                self._pygments_needs_content_detection = False
                return self._pygments_lexer
        except Exception as e:
            self.logger.debug(f"Lexer auto-detection failed: {e}")
        return None

    def _get_or_create_formatter(self):
        """Get or create Pygments formatter."""
        from pygments.formatters import Terminal256Formatter
        from pygments.styles import get_style_by_name
        from pygments.util import ClassNotFound

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

        return formatter

    def _handle_php_multiline_comment(self, line: str) -> str | None:
        """Handle PHP multi-line comment tracking. Returns styled line or None."""
        in_comment = getattr(self, "_php_in_multiline_comment", False)

        if in_comment:
            if "*/" in line:
                self._php_in_multiline_comment = False
                return None  # Let Pygments handle it
            # Still inside comment - apply comment color
            comment_color = "\x1b[38;5;245m"
            reset = "\x1b[39m"
            return f"{comment_color}{line}{reset}"

        # Not in comment - check if one starts
        self._check_php_comment_start(line)
        return None

    def _check_php_comment_start(self, line: str) -> None:
        """Check if a PHP multi-line comment starts on this line."""
        for pattern in ("/*", "/**"):
            if pattern in line:
                start_pos = line.find(pattern)
                end_pos = line.find("*/", start_pos + len(pattern))
                if end_pos == -1:
                    self._php_in_multiline_comment = True
                    return

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

        batch_size = 10 if immediate else 30
        lines_to_feed, prompt_detected, remaining_after_prompt = (
            self._collect_cat_batch(queue, batch_size)
        )

        # Feed batch to terminal
        if lines_to_feed:
            term.feed(b"".join(lines_to_feed))

        # Handle prompt detection - clear context
        if prompt_detected:
            self._handle_prompt_detected_in_cat(term, remaining_after_prompt, queue)

        return prompt_detected

    def _collect_cat_batch(self, queue, batch_size: int) -> tuple[list, bool, list]:
        """Collect a batch of lines from cat queue."""
        lines_to_feed = []
        prompt_detected = False
        remaining_after_prompt = []

        for _ in range(batch_size):
            if not queue:
                break
            try:
                line_data = queue.popleft()

                # Check for prompt marker
                if line_data == _PROMPT_MARKER:
                    prompt_detected = True
                    continue

                if prompt_detected:
                    remaining_after_prompt.append(line_data)
                else:
                    lines_to_feed.append(line_data)
            except IndexError:
                break

        return lines_to_feed, prompt_detected, remaining_after_prompt

    def _handle_prompt_detected_in_cat(
        self, term: Vte.Terminal, remaining_after_prompt: list, queue
    ) -> None:
        """Handle prompt detection during cat output processing."""
        # Flush remaining pending lines
        self._flush_pending_lines(term)

        # Feed lines that came after the prompt marker
        if remaining_after_prompt:
            term.feed(b"".join(remaining_after_prompt))

        # Drain any remaining lines in the queue
        self._drain_remaining_queue(term, queue)

        self._highlighter.clear_context(self._proxy_id)
        self._reset_cat_state()
        self._reset_input_buffer()

    def _flush_pending_lines(self, term: Vte.Terminal) -> None:
        """Flush pending lines to terminal."""
        pending = getattr(self, "_pending_lines", [])
        for pending_content, pending_ending in pending:
            term.feed(
                (pending_content + pending_ending).encode("utf-8", errors="replace")
            )
        self._pending_lines = []

    def _drain_remaining_queue(self, term: Vte.Terminal, queue) -> None:
        """Drain remaining lines from queue to terminal."""
        drain_lines = []
        while queue:
            try:
                line_data = queue.popleft()
                if line_data != _PROMPT_MARKER:
                    drain_lines.append(line_data)
            except IndexError:
                break
        if drain_lines:
            term.feed(b"".join(drain_lines))

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
        self._cat_filename = ""
        self._cat_bytes_processed = 0  # Resetar contador
        self._cat_limit_reached = False  # Resetar flag
        self._cat_waiting_for_newline = False
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

    def _flush_queue(self, term: Vte.Terminal) -> None:
        """
        Force flush any pending lines in the highlighting queue to the terminal.
        This ensures strict ordering before switching to raw feed.
        """
        if self._line_queue:
            # Drain the entire queue immediately
            while self._line_queue:
                try:
                    chunk = self._line_queue.popleft()
                    term.feed(chunk)
                except IndexError:
                    break
            self._queue_processing = False
