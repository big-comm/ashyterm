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
from gi.repository import GLib, Vte

from ..utils.logger import get_logger

# Import constants and rules from highlighter package
from .highlighter.constants import (
    CSI_CONTROL_PATTERN as _CSI_CONTROL_PATTERN,
)
from .highlighter.constants import (
    SGR_RESET_LINE_PATTERN as _SGR_RESET_LINE_PATTERN,
)

if TYPE_CHECKING:
    from ..sessions.models import SessionItem  # noqa: F401

# Import OutputHighlighter from its own module
from .highlighter.output import OutputHighlighter, get_output_highlighter

# Import ShellInputHighlighter from its own module
from .highlighter.shell_input import ShellInputHighlighter, get_shell_input_highlighter

# Import mixins
from ._cat_handler import CatModeHandler, _PROMPT_MARKER
from ._streaming_handler import StreamingHandler

# Re-export _PROMPT_MARKER for any external consumers
__all__ = [
    "HighlightedTerminalProxy",
    "get_output_highlighter",
    "get_shell_input_highlighter",
]


class HighlightedTerminalProxy(CatModeHandler, StreamingHandler):
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
        self._cat_filename: str = ""
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

        # Pause flag: when True, skip highlighting and feed raw data
        self._highlight_paused = False

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

    def pause_highlighting(self) -> None:
        """Pause highlighting — feed raw data while tab is inactive."""
        self._highlight_paused = True

    def resume_highlighting(self) -> None:
        """Resume highlighting when tab becomes visible again."""
        self._highlight_paused = False

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

        self._cat_filename = ""
        self._input_highlight_buffer = ""
        self._at_shell_prompt = False
        self._suppress_shell_input_highlighting = False
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

        self._highlighter.unregister_proxy(self._proxy_id)
        self._shell_input_highlighter.unregister_proxy(self._proxy_id)

        if from_destroy or self._widget_destroyed:
            self._terminal_ref = None  # type: ignore[assignment]
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

            self._terminal_ref = None  # type: ignore[assignment]

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
            self.logger.error(f"PTY read error ({len(data)} bytes lost): {e}")
            # Re-feed raw data to terminal to avoid silent output loss
            try:
                if term is not None and not self._widget_destroyed:
                    term.feed(data)
            except Exception:
                pass
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
        # Skip highlighting entirely when paused (tab not visible)
        if self._highlight_paused:
            term.feed(data)
            return True

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
            "burst_threshold": settings.get("highlight_burst_threshold", 15),
        }

    def _get_context_state(self) -> tuple[str, bool]:
        """Get current context and ignored state."""
        with self._highlighter._lock:
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            is_ignored = bool(
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
            self._burst_threshold = config["burst_threshold"]
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
