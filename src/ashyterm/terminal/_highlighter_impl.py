# ashyterm/terminal/_highlighter_impl.py
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
- PCRE2 backend (regex module) for ~50% faster matching
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
from typing import TYPE_CHECKING, Dict, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
gi.require_version("GLib", "2.0")
# Use regex module (PCRE2 backend) for ~50% faster matching
import regex as re_engine
from gi.repository import GLib, Vte

from ..utils.logger import get_logger
from ..utils.shell_echo import is_echo_terminator
from .highlighter.constants import (
    ALL_ESCAPE_SEQ_PATTERN as _ALL_ESCAPE_SEQ_PATTERN,
)
from .highlighter.constants import (
    ANSI_COLOR_PATTERN as _ANSI_COLOR_PATTERN,
)

# Import constants and rules from highlighter package
from .highlighter.constants import (
    ANSI_SEQ_PATTERN as _ANSI_SEQ_PATTERN,
)
from .highlighter.constants import (
    CSI_CONTROL_PATTERN as _CSI_CONTROL_PATTERN,
)
from .highlighter.constants import (
    SGR_RESET_LINE_PATTERN as _SGR_RESET_LINE_PATTERN,
)
from .highlighter.constants import (
    SHELL_NAME_PROMPT_PATTERN as _SHELL_NAME_PROMPT_PATTERN,
)

if TYPE_CHECKING:
    from ..sessions.models import SessionItem  # noqa: F401

# Import OutputHighlighter from its own module
from .highlighter.output import OutputHighlighter, get_output_highlighter

# Import ShellInputHighlighter from its own module
from .highlighter.shell_input import ShellInputHighlighter, get_shell_input_highlighter

# Sentinel marker for prompt detection in CAT queue
_PROMPT_MARKER = b"__PROMPT_DETECTED__"


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

        # Bracketed Paste State
        self._in_bracketed_paste = False

        # Pygments state for cat command highlighting
        self._cat_filename: Optional[str] = None
        self._cat_bytes_processed: int = 0
        self._cat_limit_reached: bool = False
        self._cat_waiting_for_newline: bool = False

        self._input_highlight_buffer = ""
        # Start as False; will be set True when shell prompt is detected via termprop
        self._at_shell_prompt = False
        self._need_color_reset = False
        # When True, do not apply per-character shell input highlighting.
        # This is used to avoid interfering with readline redisplay/cursor movement
        # after paste or navigation keys, which can cause visible artifacts.
        self._suppress_shell_input_highlighting = False
        # Track previous token type for retroactive recoloring
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

        if terminal:
            self._destroy_handler_id = terminal.connect(
                "destroy", self._on_widget_destroy
            )
            # Use VTE's native shell integration for prompt detection
            self._termprop_handler_id = terminal.connect(
                "termprop-changed", self._on_termprop_changed
            )

    def _on_termprop_changed(self, terminal: Vte.Terminal, prop: str) -> None:
        """Handle VTE termprop changes for shell integration."""
        if prop == Vte.TERMPROP_SHELL_PRECMD:
            # Shell is about to display prompt - command finished
            if not self._at_shell_prompt:
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self._reset_input_buffer()
            self._need_color_reset = True
            self._suppress_shell_input_highlighting = False
            # Don't clear cat context immediately - wait for content to finish
            # The context will be cleared when prompt is detected in _process_cat_output
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            if context.lower() != "cat":
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
        elif prop == Vte.TERMPROP_SHELL_PREEXEC:
            # Shell is about to execute command
            if self._at_shell_prompt:
                self._at_shell_prompt = False
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, False)
            self._reset_input_buffer()
        elif prop == Vte.TERMPROP_SHELL_POSTEXEC:
            # Command finished executing - reset highlighting state
            # Don't clear cat context immediately - content may still be arriving
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            if context.lower() != "cat":
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
            self._reset_input_buffer()
        elif prop == Vte.TERMPROP_CURRENT_DIRECTORY_URI:
            # OSC7 - directory change signals shell ready (fallback for shells without precmd)
            if not self._at_shell_prompt:
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self._reset_input_buffer()

    def _has_incomplete_escape(self, data: bytes) -> bool:
        """Check if data ends with an incomplete escape sequence."""
        last_esc = data.rfind(b"\x1b")
        if last_esc == -1:
            return False

        data_len = len(data)
        pos = last_esc + 1
        if pos >= data_len:
            return True

        second = data[pos]
        return self._check_escape_sequence_complete(data, pos, data_len, second)

    def _check_escape_sequence_complete(
        self, data: bytes, pos: int, data_len: int, second: int
    ) -> bool:
        """Checks if an escape sequence is incomplete."""
        if second == 0x5B:  # '[' - CSI
            return self._is_csi_incomplete(data, pos + 1, data_len)
        if second == 0x5D:  # ']' - OSC
            return self._is_osc_incomplete(data, pos + 1, data_len)
        if second in (0x28, 0x29):  # G0/G1 charset
            return pos + 1 >= data_len
        return False

    def _is_csi_incomplete(self, data: bytes, start: int, data_len: int) -> bool:
        """Checks if CSI sequence is incomplete."""
        for i in range(start, data_len):
            if 0x40 <= data[i] <= 0x7E:
                return False
        return True

    def _is_osc_incomplete(self, data: bytes, start: int, data_len: int) -> bool:
        """Checks if OSC sequence is incomplete."""
        for i in range(start, data_len):
            if data[i] == 0x07:
                return False
            if data[i] == 0x1B and i + 1 < data_len and data[i + 1] == 0x5C:
                return False
        return True  # Simple escape, complete

    def _is_in_unclosed_multiline_block(self, buffer: str) -> bool:
        """
        Check if the buffer contains an unclosed multi-line block.

        Returns True if we detect:
        - if/then without fi
        - for/do without done
        - while/do without done
        - unclosed braces
        - line ending with continuation indicators (|, &&, ||, \\) - NOT 'then'/'do' if closed
        """
        if not buffer:
            return False

        buffer_stripped = buffer.strip()
        if not buffer_stripped:
            return False

        then_open, do_open = self._check_block_openings(buffer_stripped)
        if then_open or do_open:
            return True

        if self._has_unclosed_braces(buffer_stripped):
            return True

        return self._ends_with_continuation(buffer_stripped, then_open, do_open)

    def _check_block_openings(self, buffer_stripped: str) -> tuple[bool, bool]:
        """Check for unclosed if/then and for/do blocks."""
        has_then = self._has_keyword(buffer_stripped, "then")
        has_fi = self._has_keyword(buffer_stripped, "fi")
        has_do = self._has_keyword(buffer_stripped, "do")
        has_done = self._has_keyword(buffer_stripped, "done")

        then_block_open = has_then and not has_fi
        do_block_open = has_do and not has_done
        return then_block_open, do_block_open

    def _has_keyword(self, buffer_stripped: str, keyword: str) -> bool:
        """Check if buffer contains a shell keyword."""
        return (
            f" {keyword}" in buffer_stripped
            or buffer_stripped.startswith(keyword)
            or f"\n{keyword}" in buffer_stripped
            or buffer_stripped.endswith(keyword)
        )

    def _has_unclosed_braces(self, buffer_stripped: str) -> bool:
        """Check if buffer has more opening braces than closing."""
        return buffer_stripped.count("{") > buffer_stripped.count("}")

    def _ends_with_continuation(
        self, buffer_stripped: str, then_open: bool, do_open: bool
    ) -> bool:
        """Check if the last line ends with a continuation indicator."""
        lines = buffer_stripped.split("\n")
        if not lines:
            return False

        last_line = lines[-1].strip()
        if not last_line:
            return False

        # Operators that always indicate continuation
        if last_line.endswith(("|", "&&", "||", "\\", "{")):
            return True

        # Context-dependent continuations
        if last_line.endswith("then") and then_open:
            return True
        if last_line.endswith("do") and do_open:
            return True
        if last_line.endswith("else"):
            return True

        return False

    def _detect_interactive_marker(self, data: bytes) -> tuple[bool, bool, bool]:
        """
        Detect interactive marker in data (NUL prefix from PTY).

        Returns: (has_marker, is_user_input, is_newline)
        """
        data_len = len(data)
        if data_len < 2 or data[0] != 0x00:
            return (False, False, False)

        next_byte = data[1]
        # Check for newline - can be longer due to escape sequences like bracketed paste mode
        # The data often contains sequences like \x00\r\n\x1b[?2004l\r\x1b[?2004h>
        if b"\r\n" in data or (b"\r" in data and data_len <= 3):
            return (True, False, True)  # Newline marker
        elif next_byte in (0x08, 0x7F):
            return (True, True, False)  # Backspace
        elif data_len <= 3 and 0x20 <= next_byte <= 0x7E:
            return (True, True, False)  # Printable char
        return (False, False, False)

    def _handle_prompt_split(
        self, content: str, prompt: str, add_newline: bool = True
    ) -> None:
        """
        Handle splitting of content that contains an embedded prompt.
        Highlights content and queues prompt marker.
        """
        clean = _CSI_CONTROL_PATTERN.sub("", content)
        clean = _SGR_RESET_LINE_PATTERN.sub("", clean).strip()
        if clean:
            highlighted = self._highlight_line_with_pygments(clean, self._cat_filename)
            self._cat_queue.append(highlighted.encode("utf-8", errors="replace"))
            if add_newline:
                self._cat_queue.append(b"\r\n")
        self._cat_queue.append(_PROMPT_MARKER)
        self._cat_queue.append(prompt.encode("utf-8", errors="replace"))

    def _handle_backspace_in_buffer(self, data: bytes) -> int:
        """
        Handle backspace characters in input data by updating the highlight buffer.

        Counts backspace characters (\x08 and \x7f) and removes that many characters
        from the input highlight buffer. Also handles shell-style \x08 \x08 patterns.

        Args:
            data: The byte data that may contain backspace characters.

        Returns:
            The number of characters actually removed from the buffer.
        """
        if not self._input_highlight_buffer:
            return 0

        # Count backspaces - handle \x08 \x08 patterns (sh/dash style)
        temp_data = data
        backspace_count = 0

        # Count \x08 \x08 patterns first (count as 1 each)
        while b"\x08 \x08" in temp_data:
            backspace_count += 1
            temp_data = temp_data.replace(b"\x08 \x08", b"", 1)

        # Count remaining individual backspaces
        backspace_count += temp_data.count(b"\x7f") + temp_data.count(b"\x08")

        if backspace_count > 0:
            chars_to_remove = min(backspace_count, len(self._input_highlight_buffer))
            if chars_to_remove > 0:
                self._input_highlight_buffer = self._input_highlight_buffer[
                    :-chars_to_remove
                ]
            # Reset token tracking after backspace
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0
            return chars_to_remove

        return 0

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
        except OSError:
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
        self._close_slave_fd_if_open()

        try:
            if not self._setup_vte_pty(term, child_pid):
                return False

            self._reset_sequence_counters()
            self._setup_io_watch()
            self._running = True
            return True

        except Exception as e:
            self.logger.error(f"Failed to start highlight proxy: {e}")
            self._cleanup_fds_on_failure()
            self.stop()
            return False

    def _close_slave_fd_if_open(self) -> None:
        """Closes the slave FD if it's open."""
        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None

    def _setup_vte_pty(self, term: Vte.Terminal, child_pid: int) -> bool:
        """Sets up VTE PTY with the master FD."""
        vte_pty = Vte.Pty.new_foreign_sync(self._master_fd)
        if not vte_pty:
            return False

        term.set_pty(vte_pty)
        term.watch_child(child_pid)
        return True

    def _reset_sequence_counters(self) -> None:
        """Resets the sequence counters for output ordering."""
        self._sequence_counter = 0
        self._pending_outputs = {}
        self._next_sequence_to_feed = 0

    def _setup_io_watch(self) -> None:
        """Sets up the IO watch for PTY reading."""
        self._io_watch_id = GLib.io_add_watch(
            self._master_fd,
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_pty_readable,
        )

    def _cleanup_fds_on_failure(self) -> None:
        """Cleans up FDs on startup failure."""
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
        self._in_bracketed_paste = False

        self._cat_filename = None
        self._input_highlight_buffer = ""
        self._at_shell_prompt = False
        self._suppress_shell_input_highlighting = False
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

        self._highlighter.unregister_proxy(self._proxy_id)
        self._shell_input_highlighter.unregister_proxy(self._proxy_id)

        if from_destroy or self._widget_destroyed:
            self._terminal_ref = None
            return

        with self._lock:
            # Reference terminal to ensure it's not garbage collected during cleanup
            _ = self._terminal_ref()

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
            # 3. Try read - use 4KB buffer
            data = os.read(fd, 4096)
            if not data:
                return True  # Empty read, keep waiting

            # 4. Verify widget is alive before feeding
            term = self._terminal
            if not self._verify_terminal_valid(term):
                return False

            # 5. Handle partial escape sequences from previous read
            data = self._combine_with_partial_buffer(data)

            # 6. Check for incomplete escape sequence and buffer if needed
            if self._has_incomplete_escape(data):
                self._partial_line_buffer = data
                return True  # Wait for next chunk

            # 7. Process data
            return self._process_pty_data(data, term)

        except OSError:
            self._io_watch_id = None
            return False
        except Exception as e:
            self.logger.error(f"PTY read error: {e}")
            return True

    def _verify_terminal_valid(self, term: Vte.Terminal | None) -> bool:
        """Verify terminal widget is still valid and usable."""
        if term is None:
            self._io_watch_id = None
            return False

        try:
            if self._widget_destroyed:
                self._io_watch_id = None
                return False
            if not term.get_realized():
                return False
            if term.get_parent() is None:
                self._widget_destroyed = True
                self._io_watch_id = None
                return False
        except Exception:
            self._widget_destroyed = True
            self._io_watch_id = None
            return False

        return True

    def _combine_with_partial_buffer(self, data: bytes) -> bytes:
        """Combine new data with any partial data from previous read."""
        if self._partial_line_buffer:
            data = self._partial_line_buffer + data
            self._partial_line_buffer = b""
        return data

    def _process_pty_data(self, data: bytes, term: Vte.Terminal) -> bool:
        """Process PTY data with appropriate highlighting."""
        data_len = len(data)

        # Update alt screen state for larger packets
        if data_len > 10:
            self._update_alt_screen_state(data)

        try:
            if self._is_alt_screen:
                term.feed(data)
            else:
                self._process_normal_screen_data(data, term)
        except Exception:
            self._widget_destroyed = True
            self._io_watch_id = None
            return False

        return True

    def _process_normal_screen_data(self, data: bytes, term: Vte.Terminal) -> None:
        """Process data when not in alt screen mode."""
        from ..settings.manager import get_settings_manager

        settings = get_settings_manager()
        highlight_config = self._get_highlight_config(settings)

        if not highlight_config["any_enabled"]:
            term.feed(data)
            return

        # Get current context
        context, is_ignored = self._get_context_state()

        # Route to appropriate handler
        self._route_data_processing(data, term, context, is_ignored, highlight_config)

    def _get_highlight_config(self, settings) -> dict:
        """Get highlight configuration flags."""
        output_enabled = self._highlighter.is_enabled_for_type(self._terminal_type)
        cat_enabled = output_enabled and settings.get("cat_colorization_enabled", True)
        shell_enabled = output_enabled and self._shell_input_highlighter.enabled

        return {
            "output_enabled": output_enabled,
            "cat_enabled": cat_enabled,
            "shell_enabled": shell_enabled,
            "any_enabled": output_enabled or cat_enabled or shell_enabled,
        }

    def _get_context_state(self) -> tuple[str, bool]:
        """Get current context and ignored state."""
        with self._highlighter._lock:
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            is_ignored = (
                context and context.lower() in self._highlighter._ignored_commands
            )
        return context, is_ignored

    def _route_data_processing(
        self,
        data: bytes,
        term: Vte.Terminal,
        context: str,
        is_ignored: bool,
        config: dict,
    ) -> None:
        """Route data to appropriate processing method."""
        is_cat_context = context and context.lower() == "cat"

        if is_cat_context and config["cat_enabled"]:
            self._handle_cat_context_data(data, term)
        elif is_ignored:
            self._handle_ignored_context_data(data, term, config["shell_enabled"])
        elif not context and self._at_shell_prompt and config["shell_enabled"]:
            self._handle_prompt_data(data, term)
        elif config["output_enabled"]:
            self._process_data_streaming(data, term)
        else:
            term.feed(data)

    def _handle_cat_context_data(self, data: bytes, term: Vte.Terminal) -> None:
        """Handle data in cat context."""
        data_len = len(data)
        is_interactive_input = (
            data_len <= 3
            and b"\n" not in data
            and b"\r" not in data
            and not data.startswith(b"\x1b")
        )
        if is_interactive_input:
            term.feed(data)
        else:
            self._process_cat_output(data, term)

    def _handle_ignored_context_data(
        self, data: bytes, term: Vte.Terminal, shell_enabled: bool
    ) -> None:
        """Handle data for ignored commands."""
        if (
            shell_enabled
            and len(data) < 1024
            and self._try_shell_input_highlighting(data, term)
        ):
            return
        term.feed(data)

    def _handle_prompt_data(self, data: bytes, term: Vte.Terminal) -> None:
        """Handle data at shell prompt without context."""
        if len(data) < 1024 and self._try_shell_input_highlighting(data, term):
            return
        term.feed(data)

    def _try_shell_input_highlighting(self, data: bytes, term: Vte.Terminal) -> bool:
        """Try to apply shell input highlighting. Returns True if handled."""
        text = data.decode("utf-8", errors="replace")
        self._check_and_update_prompt_state(text)

        if self._at_shell_prompt:
            highlighted = self._apply_shell_input_highlighting(text, term)
            if highlighted is not None:
                return True
        return False

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
            self._content_buffer = []
            self._cat_lines_processed = 0
            self._pending_lines = []
            self._php_in_multiline_comment = False

            _, ext = os.path.splitext(new_filename)
            self._pygments_needs_content_detection = not ext and new_filename

    def _ensure_cat_queue_exists(self) -> None:
        """Ensure cat queue is initialized."""
        if not hasattr(self, "_cat_queue"):
            from collections import deque

            self._cat_queue = deque()
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
            from pygments.lexers import TextLexer, guess_lexer
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
        except Exception:
            pass
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
        self._cat_filename = None
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

    def _is_readline_redraw(self, data: bytes) -> bool:
        """Check if data contains readline redraw sequences."""
        search_prompt_patterns = (
            b"(reverse-i-search)",
            b"(i-search)",
            b"(bck-i-search)",
            b"(fwd-i-search)",
            b"(failed ",
        )
        return (
            b"\r" in data
            or b"\x1b[D" in data
            or b"\x1b[C" in data
            or b"\x1b[K" in data
            or b"\x1b[0K" in data
            or b"\x1b[1D" in data
            or b"\x1b[1C" in data
            or b"\x1b[A" in data
            or b"\x1b[B" in data
            or b"\x1b[J" in data
            or b"\x1b[0J" in data
            or b"\x1b[H" in data
            or b"\x1b[?25l" in data
            or b"\x1b[?25h" in data
            or b"\x1b[P" in data
            or b"\x1b[@" in data
            or any(pattern in data for pattern in search_prompt_patterns)
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

        if self._burst_counter > 15:
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
        return (
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
        return (
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
        if result is not ...:
            return result  # Special case was detected (handled or needs fallback)

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
    ) -> Optional[bytes]:
        """Handle special cases in shell input. Returns result or None to continue."""
        # Handle backspace
        if "\x08" in text or "\x7f" in text:
            data = text.encode("utf-8", errors="replace")
            if self._handle_backspace_in_buffer(data) > 0:
                return None

        # Handle newline
        if "\n" in text:
            return self._handle_newline_in_shell_input(text)

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

        return ...  # Ellipsis sentinel: continue normal processing

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

        is_command_position = not words_before or words_before.endswith((
            "|",
            ";",
            "&&",
            "||",
            "(",
            "`",
            "$(",
        ))

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
