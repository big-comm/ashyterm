# ashyterm/terminal/highlighter/shell_input.py
"""Pygments lexer/formatter holder for live shell-input highlighting.

The actual colorization runs inside :mod:`terminal._streaming_handler`,
which intercepts each byte echoed by the shell, buffers it, and lexes
the running command with Pygments before re-feeding colored output to
VTE. This module just owns the shared ``BashLexer`` + ``Terminal256Formatter``
pair and the theme-selection logic that decides which Pygments style
to use based on the terminal background.

Settings that feed into the choice:

* ``shell_input_highlighting_enabled`` — master on/off
* ``shell_input_theme_mode`` — ``auto`` picks light/dark by bg luminance,
  ``manual`` uses the legacy single theme
* ``shell_input_pygments_theme`` / ``shell_input_{dark,light}_theme``
"""

import threading
from typing import List, Optional

from ...utils.logger import get_logger


_shell_input_highlighter_instance: Optional["ShellInputHighlighter"] = None
_shell_input_highlighter_lock = threading.Lock()

_DEFAULT_FG = "#ffffff"
_DEFAULT_BG = "#000000"


class ShellInputHighlighter:
    """Holds the Pygments lexer/formatter used by the streaming handler.

    The streaming handler reads ``self._lexer`` and ``self._formatter``
    directly; external callers interact via :attr:`enabled` and
    :meth:`refresh_settings`. The formatter is rebuilt whenever the
    settings tuple ``(theme_mode, theme, dark_theme, light_theme, bg)``
    changes, so a color-scheme swap propagates without restart.
    """

    def __init__(self):
        self.logger = get_logger("ashyterm.terminal.shell_input")
        self._enabled = False
        self._lexer = None
        self._formatter = None
        self._theme = "monokai"
        self._lexer_config_key: Optional[str] = None

        self._palette: Optional[List[str]] = None
        self._foreground: str = _DEFAULT_FG
        self._background: str = _DEFAULT_BG

        self._lock = threading.Lock()
        self._refresh_settings()

    def _refresh_settings(self) -> None:
        """Refresh settings from configuration."""
        try:
            from ...settings.manager import get_settings_manager

            settings = get_settings_manager()
            self._enabled = settings.get("shell_input_highlighting_enabled", False)

            # Command not found highlighting (red underline for unknown commands)
            cmd_not_found = settings.get("command_not_found_highlighting", True)
            from .command_validator import CommandValidator
            CommandValidator.get_instance().enabled = self._enabled and cmd_not_found

            self._theme_mode = settings.get("shell_input_theme_mode", "auto")
            self._theme = settings.get("shell_input_pygments_theme", "monokai")
            self._dark_theme = settings.get("shell_input_dark_theme", "blinds-dark")
            self._light_theme = settings.get("shell_input_light_theme", "blinds-light")

            gtk_theme = settings.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = settings.get_color_scheme_data()
                self._palette = scheme.get("palette", [])
                self._foreground = scheme.get("foreground", _DEFAULT_FG)
                self._background = scheme.get("background", _DEFAULT_BG)
            else:
                self._palette = None
                self._foreground = _DEFAULT_FG
                self._background = _DEFAULT_BG

            if self._enabled:
                config_key = f"{self._theme_mode}:{self._theme}:{self._dark_theme}:{self._light_theme}:{self._background}"
                if config_key != self._lexer_config_key:
                    self._init_lexer()
                    self._lexer_config_key = config_key
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

            if self._theme_mode == "auto":
                is_light_bg = self._is_light_color(self._background)
                selected_theme = self._light_theme if is_light_bg else self._dark_theme
                self.logger.debug(
                    f"Auto mode: bg={self._background}, light={is_light_bg}, "
                    f"using theme={selected_theme}"
                )
            else:
                selected_theme = self._theme

            try:
                style = get_style_by_name(selected_theme)
            except ClassNotFound:
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

    def _is_light_color(self, hex_color: str) -> bool:
        """Determine if a color is light based on its luminance."""
        from ...utils.color_luminance import is_light_hex
        return is_light_hex(hex_color)

    def refresh_settings(self) -> None:
        """Public method to refresh settings (called when settings change)."""
        with self._lock:
            self._refresh_settings()

    @property
    def enabled(self) -> bool:
        """Check if shell input highlighting is enabled.

        Always reads from settings manager to ensure changes take effect immediately.
        """
        try:
            from ...settings.manager import get_settings_manager

            settings = get_settings_manager()
            is_enabled = settings.get("shell_input_highlighting_enabled", False)
            return is_enabled and self._lexer is not None
        except Exception:
            return False


def get_shell_input_highlighter() -> ShellInputHighlighter:
    """Get the global ShellInputHighlighter singleton instance."""
    global _shell_input_highlighter_instance
    if _shell_input_highlighter_instance is None:
        with _shell_input_highlighter_lock:
            if _shell_input_highlighter_instance is None:
                _shell_input_highlighter_instance = ShellInputHighlighter()
    return _shell_input_highlighter_instance


__all__ = ["ShellInputHighlighter", "get_shell_input_highlighter"]
