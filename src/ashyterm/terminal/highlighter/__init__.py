# ashyterm/terminal/highlighter/__init__.py
"""
Syntax highlighting module for terminal output and shell input.

This package provides:
- OutputHighlighter: Rule-based highlighting for command output
- ShellInputHighlighter: Pygments-based highlighting for shell input
- HighlightedTerminalProxy: PTY proxy that applies highlighting in real-time

Usage:
    from ashyterm.terminal.highlighter import (
        OutputHighlighter,
        ShellInputHighlighter,
        HighlightedTerminalProxy,
        get_highlighter,
    )
"""

# Import constants
from .constants import (
    ALT_SCREEN_DISABLE_PATTERNS,
    ALT_SCREEN_ENABLE_PATTERNS,
    ANSI_RESET,
)

# Import rules (standalone, no dependencies on main highlighter)
from .rules import CompiledRule, LiteralKeywordRule

# Import main classes from implementation module
# These will be moved to separate files in future refactoring
from .._highlighter_impl import HighlightedTerminalProxy

# ShellInputHighlighter is now in its own module
from .shell_input import ShellInputHighlighter, get_shell_input_highlighter

# OutputHighlighter is now in its own module
from .output import OutputHighlighter, get_output_highlighter

__all__ = [
    # Constants
    "ANSI_RESET",
    "ALT_SCREEN_ENABLE_PATTERNS",
    "ALT_SCREEN_DISABLE_PATTERNS",
    # Classes
    "CompiledRule",
    "LiteralKeywordRule",
    "OutputHighlighter",
    "ShellInputHighlighter",
    "HighlightedTerminalProxy",
    # Functions
    "get_output_highlighter",
    "get_shell_input_highlighter",
]
