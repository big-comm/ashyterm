# ashyterm/ui/widgets/bash_text_view.py
"""
A multi-line text view widget with bash syntax highlighting using Pygments.
Supports terminal color scheme integration for consistent theming.
"""

import re
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..colors import SYNTAX_DARK_COLORS, SYNTAX_LIGHT_COLORS
from .base_syntax_text_view import BaseSyntaxTextView

# Try to import Pygments for bash syntax highlighting
try:
    from pygments.lexers import BashLexer

    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

# Pre-compiled patterns for extra highlighting
_PATH_PATTERN = re.compile(
    r"(?:^|\s)((?:/[\w.\-]+)+|(?:\.{1,2}/[\w.\-/]+)|(?:~/[\w.\-/]*))"
)
_FLAG_PATTERN = re.compile(r"(?:^|\s)(--?[\w\-]+=?)")
_SPECIAL_VAR_PATTERN = re.compile(r"(\$[?!@*#$0-9-])")

# Special shell variables that need distinct highlighting
_SPECIAL_VARS = frozenset(("$?", "$!", "$$", "$@", "$*", "$#", "$0", "$-"))

# Redirect operators
_REDIRECT_OPS = frozenset(
    (">", ">>", "<", "<<", ">&", "2>", "2>>", "<&", "&>")
)

# Separator/control operators
_CONTROL_OPS = frozenset((";", "&", "&&", "||"))

# Token string patterns mapped to tag names (order matters - more specific first)
_TOKEN_PATTERNS = (
    ("Keyword", "keyword"),
    ("Reserved", "keyword"),
    ("Name.Builtin", "builtin"),
    ("Name.Function", "function"),
    ("Name.Variable", "variable"),
    ("Name.Attribute", "variable"),
    ("String.Escape", "escape"),
    ("String.Backtick", "backtick"),
    ("String.Single", "string_single"),
    ("String.Interpol", "substitution"),
    ("String", "string"),
    ("Comment", "comment"),
    ("Operator", "operator"),
    ("Number", "number"),
    ("Punctuation", "punctuation"),
)

# Bash-specific token types
BASH_TOKEN_TYPES = (
    "keyword",
    "builtin",
    "command",
    "string",
    "string_single",
    "backtick",
    "comment",
    "variable",
    "special_var",
    "operator",
    "number",
    "path",
    "function",
    "redirect",
    "pipe",
    "flag",
    "escape",
    "substitution",
    "brace",
)

# Palette index to token mapping: token -> (palette_index, fallback_color)
# Special value -1 indicates the "comment" token which needs dynamic foreground
_BASH_PALETTE_MAPPING: dict[str, tuple[int, str]] = {
    "keyword": (4, "#729fcf"),  # Blue
    "builtin": (2, "#8ae234"),  # Green
    "command": (2, "#8ae234"),  # Green
    "string": (3, "#e9b96e"),  # Yellow
    "string_single": (3, "#e9b96e"),  # Yellow
    "backtick": (11, "#b8860b"),  # Bright yellow
    "comment": (-1, "#888a85"),  # Special: uses dimmed foreground
    "variable": (5, "#ad7fa8"),  # Magenta
    "special_var": (13, "#ff69b4"),  # Bright magenta
    "operator": (3, "#fcaf3e"),  # Yellow
    "number": (11, "#f4d03f"),  # Bright yellow
    "path": (6, "#87ceeb"),  # Cyan
    "function": (13, "#dda0dd"),  # Bright magenta
    "redirect": (1, "#fcaf3e"),  # Red
    "pipe": (6, "#fcaf3e"),  # Cyan
    "flag": (14, "#98d8c8"),  # Bright cyan
    "escape": (3, "#deb887"),  # Yellow
    "substitution": (12, "#b8860b"),  # Bright blue
    "brace": (6, "#20b2aa"),  # Cyan
}


class BashTextView(BaseSyntaxTextView):
    """
    A multi-line text view with bash syntax highlighting using Pygments.
    Falls back to monospace font if Pygments is unavailable.
    Auto-resizes based on content up to a maximum height.
    """

    # Use centralized color definitions filtered for bash tokens
    _DEFAULT_DARK_COLORS = {
        k: v for k, v in SYNTAX_DARK_COLORS.items() if k in BASH_TOKEN_TYPES
    }
    _DEFAULT_LIGHT_COLORS = {
        k: v for k, v in SYNTAX_LIGHT_COLORS.items() if k in BASH_TOKEN_TYPES
    }

    def __init__(
        self, auto_resize: bool = True, min_lines: int = 2, max_lines: int = 8
    ):
        """
        Initialize the BashTextView.

        Args:
            auto_resize: Whether to automatically resize based on content
            min_lines: Minimum number of visible lines
            max_lines: Maximum number of visible lines
        """
        super().__init__(
            css_class="bash-textview",
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=6,
            bottom_margin=6,
            left_margin=4,
            right_margin=4,
            accepts_tab=False,
        )

        # Auto-resize configuration
        self._auto_resize = auto_resize
        self._line_height = 22  # Pixels per line (compact spacing)
        self._min_lines = min_lines
        self._max_lines = max_lines

        # Set line spacing - minimal for compact display
        self.set_pixels_above_lines(1)
        self.set_pixels_below_lines(1)
        self.set_pixels_inside_wrap(1)

        # Initialize colors and tags
        self._syntax_colors = self._get_default_colors()
        self._setup_tags()

        # Connect to text changes for highlighting and auto-resize
        self.buffer.connect("changed", self._on_buffer_changed)

        # Apply highlighting when widget becomes visible
        self.connect("map", self._on_map)

    def update_colors_from_scheme(
        self, palette: List[str], foreground: str = "#ffffff"
    ):
        """
        Update syntax highlighting colors from a terminal color scheme palette.

        The palette is typically 16 colors:
        [0-7]: Normal colors (black, red, green, yellow, blue, magenta, cyan, white)
        [8-15]: Bright colors (bright versions of above)

        Args:
            palette: List of hex color strings from the terminal color scheme
            foreground: Foreground color for normal text/comments
        """
        if len(palette) < 8:
            return  # Not enough colors

        self._syntax_colors = self._build_colors_from_palette(palette, foreground)

        # Update existing tags
        self._update_tag_colors()

        # Re-apply highlighting
        if PYGMENTS_AVAILABLE:
            self._apply_highlighting()

    def _build_colors_from_palette(
        self, palette: List[str], foreground: str
    ) -> dict[str, str]:
        """Build syntax color mapping from terminal palette.

        Args:
            palette: List of hex color strings from terminal color scheme.
            foreground: Foreground color for text.

        Returns:
            Dictionary mapping token types to hex colors.
        """
        palette_len = len(palette)
        colors = {}

        for token, (idx, fallback) in _BASH_PALETTE_MAPPING.items():
            if idx == -1:
                # Special case: comment uses dimmed foreground
                colors[token] = (
                    foreground + "80" if len(foreground) == 7 else fallback
                )
            else:
                colors[token] = palette[idx] if idx < palette_len else fallback

        return colors

    def _on_map(self, widget):
        """Apply highlighting when widget becomes visible."""
        if PYGMENTS_AVAILABLE and self.get_text():
            self._apply_highlighting()

    def _on_buffer_changed(self, buffer):
        """Handle buffer changes for highlighting and auto-resize."""
        # Auto-resize
        if self._auto_resize:
            self._update_size()

        # Syntax highlighting (debounced)
        if PYGMENTS_AVAILABLE:
            self._schedule_highlighting(delay_ms=150)

    def _update_size(self):
        """Update the text view height based on content."""
        text = self.get_text()
        line_count = max(self._min_lines, text.count("\n") + 1)
        line_count = min(line_count, self._max_lines)
        height = line_count * self._line_height + 16  # Add padding
        self.set_size_request(-1, height)

    def _apply_highlighting(self) -> bool:
        """Apply enhanced bash syntax highlighting using Pygments."""
        self._highlight_timeout_id = None

        if not PYGMENTS_AVAILABLE:
            return False

        text = self.get_text()

        if not text:
            return False

        # Remove existing tags
        self._clear_highlighting()

        # Apply highlighting using Pygments tokens
        try:
            lexer = BashLexer()
            for index, token_type, token_value in lexer.get_tokens_unprocessed(text):
                if not token_value:
                    continue

                # Map Pygments token types to our tags
                tag_name = self._map_token_to_tag(token_type, token_value)

                if tag_name:
                    start_iter = self.buffer.get_iter_at_offset(index)
                    end_iter = self.buffer.get_iter_at_offset(index + len(token_value))
                    self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

            # Additional pass for paths and flags (not well detected by Pygments)
            self._apply_extra_highlighting(text)
        except Exception:
            pass  # Silently fail highlighting on errors

        return False

    def _map_token_to_tag(self, token_type, token_value: str) -> Optional[str]:
        """Map Pygments token type to our tag name."""
        token_str = str(token_type)
        base_tag = self._find_base_tag(token_str)

        if base_tag is None:
            return None

        # Apply value-based refinements
        return self._refine_tag_by_value(base_tag, token_value)

    def _find_base_tag(self, token_str: str) -> Optional[str]:
        """Find the base tag for a token string.

        Args:
            token_str: String representation of the token type.

        Returns:
            Base tag name or None if not found.
        """
        for pattern, tag in _TOKEN_PATTERNS:
            if pattern in token_str:
                return tag
        return None

    def _refine_tag_by_value(self, base_tag: str, token_value: str) -> str:
        """Refine tag based on the actual token value.

        Args:
            base_tag: The base tag from token type matching.
            token_value: The actual text value of the token.

        Returns:
            Refined tag name.
        """
        # Variable refinement for special variables
        if base_tag == "variable" and token_value in _SPECIAL_VARS:
            return "special_var"

        # Operator refinement
        if base_tag == "operator":
            if token_value == "|":
                return "pipe"
            if token_value in _REDIRECT_OPS:
                return "redirect"
            return "operator"

        # Punctuation refinement
        if base_tag == "punctuation":
            if token_value == "|":
                return "pipe"
            if token_value in _CONTROL_OPS:
                return "operator"
            if token_value in ("{", "}"):
                return "brace"
            return None  # Unrecognized punctuation

        return base_tag

    def _apply_extra_highlighting(self, text: str):
        """Apply additional highlighting for paths, flags, and special constructs."""
        # Highlight file paths (absolute and relative)
        for match in _PATH_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("path", start_iter, end_iter)

        # Highlight command flags (-x, --option, including with =value)
        for match in _FLAG_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("flag", start_iter, end_iter)

        # Highlight special variables ($?, $!, etc.)
        for match in _SPECIAL_VAR_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("special_var", start_iter, end_iter)
