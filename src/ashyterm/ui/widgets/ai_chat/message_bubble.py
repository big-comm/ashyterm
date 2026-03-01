"""LoadingIndicator and MessageBubble widgets for AI chat."""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango

from ....utils.accessibility import set_label as a11y_label
from ....utils.icons import icon_image
from ....utils.logger import get_logger
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ._helpers import (
    _CODE_BLOCK_PATTERN,
    _BOLD_PATTERN,
    _HEADER1_PATTERN,
    _HEADER2_PATTERN,
    _HEADER3_PATTERN,
    _INLINE_CODE_PATTERN,
    _ITALIC_PATTERN,
    _get_pygments,
)


class LoadingIndicator(Gtk.Box):
    """Loading indicator with animated dots and a stop button."""

    __gsignals__ = {
        "stop-clicked": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("ai-loading-indicator")

        self._stop_btn = Gtk.Button()
        self._stop_btn.set_icon_name("process-stop-symbolic")
        self._stop_btn.add_css_class("flat")
        self._stop_btn.add_css_class("circular")
        self._stop_btn.set_size_request(24, 24)
        self._stop_btn.set_valign(Gtk.Align.CENTER)
        self._stop_btn.connect("clicked", lambda b: self.emit("stop-clicked"))
        a11y_label(self._stop_btn, _("Stop AI response"))
        self.append(self._stop_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(16, 16)
        self.append(self._spinner)

        self._label = Gtk.Label(label=_("AI is thinking..."))
        self._label.add_css_class("dim-label")
        self.append(self._label)

    def start(self):
        """Start the loading animation."""
        self._spinner.start()
        self._label.set_label(_("AI is thinking..."))
        self.set_visible(True)

    def set_streaming_label(self):
        """Update label once streaming content starts arriving."""
        self._label.set_label(_("AI is responding..."))

    def stop(self):
        """Stop the loading animation."""
        self._spinner.stop()
        self.set_visible(False)


class MessageBubble(Gtk.Box):
    """A chat message bubble widget with role indicator."""

    def __init__(
        self,
        role: str,
        content: str,
        commands: list[str] | None = None,
        settings_manager=None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._role = role
        self._content = content
        self._commands = commands or []
        self._commands = commands or []
        self._settings_manager = settings_manager

        # Accessible role for screen readers
        role_text = _("Your message") if role == "user" else _("AI response")
        a11y_label(self, role_text)

        self._setup_ui()

    def update_theme(self):
        """Update syntax highlighting and colors based on current theme."""
        # Re-format content with new colors
        formatted_content = self._format_content(self._content)
        try:
            self._label.set_markup(formatted_content)
        except Exception as e:
            _logger = get_logger("ashyterm.ui.widgets.ai_chat.message_bubble")
            _logger.debug(f"Markup parse failed in update_theme: {e}")
            self._label.set_text(self._content)

    def _get_palette(self) -> list:
        """Get current terminal palette if needed."""
        if (
            self._settings_manager
            and self._settings_manager.get("gtk_theme", "") == "terminal"
        ):
            scheme = self._settings_manager.get_color_scheme_data()
            return scheme.get("palette", [])
        return []

    def _add_tooltip(self, widget: Gtk.Widget, text: str):
        """Add tooltip to widget using custom helper or fallback to standard."""
        helper = get_tooltip_helper()
        if helper:
            helper.add_tooltip(widget, text)
        else:
            widget.set_tooltip_text(text)

    def _setup_ui(self):
        # Role indicator header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.set_margin_start(8)
        header_box.set_margin_end(8)
        header_box.set_margin_top(4)

        if self._role == "user":
            self.set_halign(Gtk.Align.END)
            # User icon and label
            user_icon = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
            user_icon.add_css_class("dim-label")
            a11y_label(user_icon, _("User message"))
            header_box.append(user_icon)

            role_label = Gtk.Label(label=_("You"))
            role_label.add_css_class("caption")
            role_label.add_css_class("dim-label")
            header_box.append(role_label)
        else:
            self.set_halign(Gtk.Align.START)
            # AI icon and label
            ai_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
            ai_icon.add_css_class("accent")
            a11y_label(ai_icon, _("AI Assistant message"))
            header_box.append(ai_icon)

            role_label = Gtk.Label(label=_("AI Assistant"))
            role_label.add_css_class("caption")
            role_label.add_css_class("accent")
            header_box.append(role_label)

        self.append(header_box)

        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        if self._role == "user":
            content_box.add_css_class("ai-message-user")
        else:
            content_box.add_css_class("ai-message-assistant")

        content_box.set_margin_start(8)
        content_box.set_margin_end(8)
        content_box.set_margin_bottom(4)

        # Message label with markdown-like formatting
        self._label = Gtk.Label()
        self._label.set_wrap(True)
        self._label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._label.set_xalign(0)
        self._label.set_selectable(True)
        self._label.set_max_width_chars(60)

        # Convert markdown to Pango markup with fallback
        formatted_content = self._format_content(self._content)
        try:
            self._label.set_markup(formatted_content)
        except Exception as e:
            _logger = get_logger("ashyterm.ui.widgets.ai_chat.message_bubble")
            _logger.debug(f"Markup parse failed in setup_ui: {e}")
            self._label.set_text(self._content)

        content_box.append(self._label)
        self.append(content_box)
        self._content_box = content_box

        # Add command buttons for assistant messages
        if self._role == "assistant" and self._commands:
            self._add_command_buttons()

    def _get_code_block_colors(self) -> dict:
        """Get colors for code blocks and inline code based on theme."""
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()
        palette = self._get_palette()

        # If we have a terminal palette, try to use it for better integration
        if palette and len(palette) >= 8:
            return {
                "block_bg": palette[0]
                if is_dark
                else "#f0f0f0",  # Use term bg or light gray
                "block_fg": palette[7] if is_dark else "#24292e",
                "inline_bg": palette[8] if len(palette) > 8 else "#3d3d3d",
                "inline_fg": palette[5] if len(palette) > 5 else "#ff79c6",
            }

        if is_dark:
            return {
                "block_bg": "#2d2d2d",
                "block_fg": "#e6e6e6",
                "inline_bg": "#3d3d3d",
                "inline_fg": "#ff79c6",  # Pink for inline code
            }
        else:
            return {
                "block_bg": "#f0f0f0",  # Light gray background
                "block_fg": "#24292e",  # Dark text
                "inline_bg": "#eff1f3",  # Subtle gray for inline
                "inline_fg": "#d63384",  # Magenta for inline code
            }

    def _format_content(self, text: str) -> str:
        """Convert basic markdown to Pango markup with syntax highlighting."""
        # Get theme-adaptive colors
        colors = self._get_code_block_colors()
        block_bg = colors["block_bg"]
        block_fg = colors["block_fg"]
        inline_bg = colors["inline_bg"]
        inline_fg = colors["inline_fg"]

        # Step 1: Extract and preserve code blocks and inline code
        # Store them with placeholders to prevent markdown transformations inside code
        # Use Unicode private use area characters as markers (safe from normal text)
        code_blocks: list[str] = []
        inline_codes: list[str] = []

        def store_code_block(match):
            lang = match.group(1).lower() if match.group(1) else ""
            code = match.group(2)
            highlighted = self._highlight_code_for_label(code, lang)
            idx = len(code_blocks)
            code_blocks.append(
                f'<span background="{block_bg}" foreground="{block_fg}"><tt>{highlighted}</tt></span>'
            )
            return f"\ue000CODEBLOCK{idx}\ue001"

        def store_inline_code(match):
            code = match.group(1)
            escaped_code = GLib.markup_escape_text(code)
            idx = len(inline_codes)
            inline_codes.append(
                f'<span background="{inline_bg}" foreground="{inline_fg}"><tt>{escaped_code}</tt></span>'
            )
            return f"\ue000INLINE{idx}\ue001"

        # Replace code blocks with placeholders (using pre-compiled patterns)
        text = _CODE_BLOCK_PATTERN.sub(store_code_block, text)

        # Replace inline code with placeholders
        text = _INLINE_CODE_PATTERN.sub(store_inline_code, text)

        # Step 2: Escape remaining text for Pango markup
        text = GLib.markup_escape_text(text)

        # Step 3: Apply markdown transformations (safe now - no code content)
        # Bold (**...**)
        text = _BOLD_PATTERN.sub(r"<b>\1</b>", text)

        # Italic (*...*)
        text = _ITALIC_PATTERN.sub(r"<i>\1</i>", text)

        # Headers (# ...)
        text = _HEADER3_PATTERN.sub(r"<b>\1</b>", text)
        text = _HEADER2_PATTERN.sub(r"<b><big>\1</big></b>", text)
        text = _HEADER1_PATTERN.sub(r"<b><big><big>\1</big></big></b>", text)

        # Step 4: Restore code blocks and inline codes
        for i, block in enumerate(code_blocks):
            text = text.replace(f"\ue000CODEBLOCK{i}\ue001", block)

        for i, inline in enumerate(inline_codes):
            text = text.replace(f"\ue000INLINE{i}\ue001", inline)

        return text

    def _highlight_with_pygments(self, code: str, lang: str, pygments_mod: dict) -> str:
        """Highlight code using Pygments with Pango markup output."""
        get_lexer_by_name = pygments_mod["get_lexer_by_name"]
        TextLexer = pygments_mod["TextLexer"]
        ClassNotFound = pygments_mod["ClassNotFound"]

        # Map common language aliases
        lang_map = {
            "sh": "bash",
            "shell": "bash",
            "zsh": "bash",
            "": "bash",  # Default to bash for terminal
            "py": "python",
        }
        lang = lang_map.get(lang.lower(), lang.lower())

        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            lexer = TextLexer()

        colors = self._build_pygments_color_map()

        # Tokenize and build Pango markup
        pygments = pygments_mod["pygments"]
        result = []
        for token_type, token_value in pygments.lex(code, lexer):
            escaped = GLib.markup_escape_text(token_value)

            color = None
            token_str = str(token_type)
            while token_str and not color:
                if token_str in colors:
                    color = colors[token_str]
                elif "." in token_str:
                    token_str = token_str.rsplit(".", 1)[0]
                else:
                    break

            if color:
                result.append(f'<span foreground="{color}">{escaped}</span>')
            else:
                result.append(escaped)

        return "".join(result)

    def _build_pygments_color_map(self) -> dict:
        """Build token-to-color mapping from palette or Dracula defaults."""
        palette = self._get_palette()
        if palette and len(palette) >= 8:
            return self._palette_color_map(palette)
        return self._dracula_color_map()

    # Palette index → (index, fallback_color) for extracting colors
    _PALETTE_COLORS = {
        "magenta": (5, "#ff79c6"),
        "cyan": (6, "#8be9fd"),
        "green": (2, "#50fa7b"),
        "yellow": (3, "#f1fa8c"),
        "bright_yellow": (11, "#ffb86c"),
        "number": (5, "#bd93f9"),
        "comment": (8, "#6272a4"),
        "white": (7, "#f8f8f2"),
    }

    # Token type → color name mapping (declarative)
    _TOKEN_COLOR_GROUPS: list[tuple[str, tuple[str, ...]]] = [
        (
            "magenta",
            (
                "Token.Keyword",
                "Token.Keyword.Namespace",
                "Token.Keyword.Constant",
                "Token.Keyword.Declaration",
                "Token.Keyword.Pseudo",
                "Token.Keyword.Reserved",
                "Token.Operator",
                "Token.Operator.Word",
            ),
        ),
        (
            "cyan",
            (
                "Token.Keyword.Type",
                "Token.Name.Variable",
                "Token.Name.Variable.Global",
                "Token.Name.Variable.Instance",
            ),
        ),
        (
            "green",
            (
                "Token.Name.Builtin",
                "Token.Name.Function",
                "Token.Name.Class",
                "Token.Name.Decorator",
            ),
        ),
        (
            "yellow",
            (
                "Token.String",
                "Token.String.Doc",
                "Token.String.Double",
                "Token.String.Single",
                "Token.String.Backtick",
                "Token.String.Interpol",
                "Token.Literal",
                "Token.Literal.String",
                "Token.Literal.String.Double",
                "Token.Literal.String.Single",
                "Token.Literal.String.Backtick",
                "Token.Literal.String.Doc",
                "Token.Literal.String.Interpol",
                "Token.Literal.String.Heredoc",
            ),
        ),
        ("bright_yellow", ("Token.String.Escape", "Token.Literal.String.Escape")),
        (
            "number",
            (
                "Token.Literal.Number",
                "Token.Literal.Number.Integer",
                "Token.Literal.Number.Float",
                "Token.Literal.Number.Hex",
                "Token.Literal.Number.Oct",
                "Token.Literal.Number.Bin",
                "Token.Number",
                "Token.Number.Integer",
                "Token.Number.Float",
            ),
        ),
        (
            "comment",
            (
                "Token.Comment",
                "Token.Comment.Single",
                "Token.Comment.Multiline",
                "Token.Comment.Hashbang",
                "Token.Comment.Preproc",
            ),
        ),
        ("white", ("Token.Punctuation",)),
    ]

    @staticmethod
    def _palette_color_map(p: list) -> dict:
        """Map terminal palette colors to Pygments token types."""
        colors = {
            name: p[idx] if len(p) > idx else fallback
            for name, (idx, fallback) in MessageBubble._PALETTE_COLORS.items()
        }
        m: dict[str, str] = {}
        for color_name, tokens in MessageBubble._TOKEN_COLOR_GROUPS:
            color = colors[color_name]
            for token in tokens:
                m[token] = color
        return m

    @staticmethod
    def _dracula_color_map() -> dict:
        """Default Dracula color scheme for Pygments tokens."""
        m: dict[str, str] = {}
        for k in (
            "Token.Keyword",
            "Token.Keyword.Namespace",
            "Token.Keyword.Constant",
            "Token.Keyword.Declaration",
            "Token.Keyword.Pseudo",
            "Token.Keyword.Reserved",
        ):
            m[k] = "#ff79c6"
        m["Token.Keyword.Type"] = "#8be9fd"
        for k in (
            "Token.Name.Builtin",
            "Token.Name.Function",
            "Token.Name.Class",
            "Token.Name.Decorator",
        ):
            m[k] = "#50fa7b"
        for k in (
            "Token.Name.Variable",
            "Token.Name.Variable.Global",
            "Token.Name.Variable.Instance",
        ):
            m[k] = "#8be9fd"
        for k in (
            "Token.String",
            "Token.String.Doc",
            "Token.String.Double",
            "Token.String.Single",
            "Token.String.Backtick",
            "Token.String.Interpol",
        ):
            m[k] = "#f1fa8c"
        m["Token.String.Escape"] = "#ffb86c"
        for k in (
            "Token.Literal",
            "Token.Literal.String",
            "Token.Literal.String.Double",
            "Token.Literal.String.Single",
            "Token.Literal.String.Backtick",
            "Token.Literal.String.Doc",
            "Token.Literal.String.Escape",
            "Token.Literal.String.Interpol",
            "Token.Literal.String.Heredoc",
        ):
            m[k] = "#f1fa8c"
        m["Token.Literal.String.Escape"] = "#ffb86c"
        for k in (
            "Token.Literal.Number",
            "Token.Literal.Number.Integer",
            "Token.Literal.Number.Float",
            "Token.Literal.Number.Hex",
            "Token.Literal.Number.Oct",
            "Token.Literal.Number.Bin",
            "Token.Number",
            "Token.Number.Integer",
            "Token.Number.Float",
        ):
            m[k] = "#bd93f9"
        for k in (
            "Token.Comment",
            "Token.Comment.Single",
            "Token.Comment.Multiline",
            "Token.Comment.Hashbang",
            "Token.Comment.Preproc",
        ):
            m[k] = "#6272a4"
        for k in ("Token.Operator", "Token.Operator.Word"):
            m[k] = "#ff79c6"
        m["Token.Punctuation"] = "#f8f8f2"
        return m

    def _get_syntax_colors(self) -> dict:
        """Get syntax highlighting colors based on current theme (light/dark)."""
        # Check if we're in light or dark mode
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()

        if is_dark:
            # Dracula-inspired colors for dark theme
            return {
                "keyword": "#ff79c6",  # Pink for keywords
                "string": "#f1fa8c",  # Yellow for strings
                "comment": "#6272a4",  # Blue-gray for comments
                "number": "#bd93f9",  # Purple for numbers
                "function": "#50fa7b",  # Green for functions/commands
                "variable": "#8be9fd",  # Cyan for variables
                "flag": "#ffb86c",  # Orange for flags
            }
        else:
            # Light theme colors - darker, high contrast for light backgrounds
            return {
                "keyword": "#ab296a",  # Darker magenta for keywords
                "string": "#7c5e00",  # Dark amber/gold for strings
                "comment": "#5c636a",  # Dark gray for comments
                "number": "#5a32a3",  # Dark purple for numbers
                "function": "#116d3d",  # Dark green for functions/commands
                "variable": "#0a58ca",  # Dark blue for variables
                "flag": "#ca6510",  # Dark orange for flags
            }

    def _highlight_fallback(self, code: str, lang: str) -> str:
        """Fallback regex-based syntax highlighting.

        This method handles raw (unescaped) code and produces valid Pango markup.
        Uses a token-based approach to properly handle escaping.
        Adapts colors for light/dark themes.
        """
        # Get colors based on current theme
        colors = self._get_syntax_colors()

        # Define token patterns for shell/bash (most common for terminal commands)
        if lang in ("bash", "sh", "shell", "zsh", ""):
            patterns = [
                # Comments - must be first
                (r"#[^\n]*", "comment"),
                # Double-quoted strings
                (r'"(?:[^"\\]|\\.)*"', "string"),
                # Single-quoted strings
                (r"'(?:[^'\\]|\\.)*'", "string"),
                # Variables $VAR and ${VAR}
                (r"\$\{?[\w]+\}?", "variable"),
                # Flags/options (--flag or -f)
                (r"(?<!\w)--?[\w-]+", "flag"),
                # Shell keywords
                (
                    r"\b(?:if|then|else|elif|fi|for|while|do|done|case|esac|in|function|return|exit|export|source|alias|unset|local|readonly)\b",
                    "keyword",
                ),
                # Common commands (expanded list)
                (
                    r"\b(?:sudo|cd|ls|cat|echo|grep|awk|sed|find|xargs|chmod|chown|cp|mv|rm|mkdir|touch|head|tail|sort|uniq|wc|cut|tr|tee|man|which|whereis|apt|apt-get|apt-cache|dpkg|pacman|yay|paru|pip|pip3|npm|npx|yarn|pnpm|git|docker|docker-compose|podman|kubectl|systemctl|journalctl|curl|wget|tar|gzip|gunzip|zip|unzip|ssh|scp|rsync|kill|killall|pkill|ps|top|htop|btop|df|du|free|mount|umount|ln|pwd|date|cal|whoami|hostname|uname|clear|history|alias|export|env|set|bash|zsh|sh|fish|python|python3|node|ruby|perl|make|cmake|gcc|g\+\+|clang|cargo|rustc|go|java|javac|nano|vim|nvim|vi|emacs|code|less|more|diff|patch|install|update|upgrade|remove|purge|autoremove|search|info|show|list|status|start|stop|restart|enable|disable|reload|reboot|shutdown|poweroff|suspend|hibernate|chroot|exec|nohup|screen|tmux|watch|time|timeout|sleep|true|false|test|read|printf|pushd|popd|dirs|fg|bg|jobs|disown|wait|trap|break|continue|shift|getopts|eval|source|type|command|builtin|hash|help|logout|exit|return|declare|typeset|let|readonly|local|global|unset|shopt|complete|compgen|compopt|mapfile|readarray|coproc|select|until|ulimit|umask|fc|bind|caller|enable|mapfile|readarray|times)\b",
                    "function",
                ),
                # Numbers
                (r"\b\d+\b", "number"),
            ]
        elif lang in ("python", "py"):
            patterns = [
                # Comments
                (r"#[^\n]*", "comment"),
                # Triple-quoted strings
                (r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "string"),
                # Double-quoted strings
                (r'"(?:[^"\\]|\\.)*"', "string"),
                # Single-quoted strings
                (r"'(?:[^'\\]|\\.)*'", "string"),
                # Decorators
                (r"@[\w.]+", "function"),
                # Keywords
                (
                    r"\b(?:def|class|if|elif|else|for|while|try|except|finally|with|as|import|from|return|yield|raise|pass|break|continue|and|or|not|in|is|lambda|True|False|None|async|await|global|nonlocal)\b",
                    "keyword",
                ),
                # Built-in functions
                (
                    r"\b(?:print|len|range|str|int|float|list|dict|set|tuple|open|type|isinstance|hasattr|getattr|setattr|delattr|repr|abs|all|any|bin|bool|bytes|callable|chr|complex|dir|divmod|enumerate|eval|exec|filter|format|frozenset|globals|hash|hex|id|input|iter|locals|map|max|min|next|object|oct|ord|pow|property|reversed|round|slice|sorted|staticmethod|sum|super|vars|zip)\b",
                    "function",
                ),
                # Numbers
                (r"\b\d+\.?\d*\b", "number"),
            ]
        elif lang == "json":
            patterns = [
                # Keys
                (r'"[\w_-]+"(?=\s*:)', "variable"),
                # String values
                (r'(?<=:\s*)"(?:[^"\\]|\\.)*"', "string"),
                # Booleans and null
                (r"\b(?:true|false|null)\b", "keyword"),
                # Numbers
                (r"\b\d+\.?\d*\b", "number"),
            ]
        else:
            # No highlighting for unknown languages
            return GLib.markup_escape_text(code)

        # Build a combined pattern with named groups
        combined_parts = []
        for i, (pattern, _token_type) in enumerate(patterns):
            combined_parts.append(f"(?P<t{i}>{pattern})")
        combined_pattern = "|".join(combined_parts)

        # Process the code and build highlighted output
        result = []
        last_end = 0

        for match in re.finditer(combined_pattern, code):
            # Add non-matched text before this match (escaped)
            if match.start() > last_end:
                result.append(GLib.markup_escape_text(code[last_end : match.start()]))

            # Find which group matched and get its token type
            matched_text = match.group(0)
            token_type: str | None = None
            for i, (_pattern, ttype) in enumerate(patterns):
                if match.group(f"t{i}") is not None:
                    token_type = ttype
                    break

            # Add highlighted text (escaped)
            escaped_text = GLib.markup_escape_text(matched_text)
            if token_type and token_type in colors:
                result.append(
                    f'<span foreground="{colors[token_type]}">{escaped_text}</span>'
                )
            else:
                result.append(escaped_text)

            last_end = match.end()

        # Add any remaining text after the last match
        if last_end < len(code):
            result.append(GLib.markup_escape_text(code[last_end:]))

        return "".join(result)

    def _highlight_code_for_label(self, code: str, lang: str) -> str:
        """Highlight code for use in labels (handles escaping).

        Both pygments and fallback handle escaping internally.
        For shell/bash languages, prefer the fallback as it has better
        recognition of common terminal commands.
        """
        # Normalize language
        lang_lower = lang.lower() if lang else ""

        # For shell/bash, prefer fallback highlighting as it recognizes
        # common terminal commands better than Pygments' BashLexer
        if lang_lower in ("bash", "sh", "shell", "zsh", ""):
            return self._highlight_fallback(code, lang)

        # For other languages, use pygments if available
        pygments_mod = _get_pygments()
        if pygments_mod:
            return self._highlight_with_pygments(code, lang, pygments_mod)

        # Fallback for all other cases
        return self._highlight_fallback(code, lang)

    def _add_command_buttons(self):
        """Add buttons for each detected command with visual section."""
        if not self._commands:
            return

        # Commands section container
        commands_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        commands_section.set_margin_start(8)
        commands_section.set_margin_end(8)
        commands_section.set_margin_top(12)

        # Section header
        section_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        terminal_icon = icon_image("utilities-terminal-symbolic")
        terminal_icon.add_css_class("ai-section-icon")
        a11y_label(terminal_icon, _("Suggested commands"))
        section_header.append(terminal_icon)

        section_label = Gtk.Label(label=_("Suggested Commands"))
        section_label.add_css_class("ai-section-title")
        section_header.append(section_label)
        commands_section.append(section_header)

        # Each command gets its own separate block/card
        for cmd in self._commands[:5]:  # Limit to 5 commands max
            # Individual command block - horizontal layout with command + buttons
            cmd_block = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cmd_block.add_css_class("ai-command-block")

            # Command label in monospace with syntax highlighting
            cmd_label = Gtk.Label()
            cmd_label.set_xalign(0)
            cmd_label.set_hexpand(True)
            cmd_label.set_wrap(True)
            cmd_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            cmd_label.add_css_class("ai-command-text")
            cmd_label.set_selectable(True)

            # Apply syntax highlighting for shell commands
            highlighted_cmd = self._highlight_code_for_label(cmd, "bash")
            cmd_label.set_markup(highlighted_cmd)

            cmd_block.append(cmd_label)

            # Action buttons container - compact icon-only buttons
            buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            buttons_box.set_valign(Gtk.Align.CENTER)
            buttons_box.add_css_class("ai-cmd-buttons")

            # Run button - executes command directly
            run_btn = Gtk.Button()
            run_btn.set_icon_name("media-playback-start-symbolic")
            run_btn.add_css_class("flat")
            run_btn.add_css_class("circular")
            run_btn.add_css_class("ai-cmd-btn-run")
            run_btn.connect("clicked", self._on_run_clicked, cmd)
            self._add_tooltip(run_btn, _("Run command"))
            a11y_label(run_btn, _("Run command"))
            buttons_box.append(run_btn)

            # Insert button - inserts into terminal without running
            insert_btn = Gtk.Button()
            insert_btn.set_icon_name("edit-paste-symbolic")
            insert_btn.add_css_class("flat")
            insert_btn.add_css_class("circular")
            insert_btn.add_css_class("ai-cmd-btn")
            insert_btn.connect("clicked", self._on_execute_clicked, cmd)
            self._add_tooltip(insert_btn, _("Insert into terminal"))
            a11y_label(insert_btn, _("Insert into terminal"))
            buttons_box.append(insert_btn)

            # Copy button
            copy_btn = Gtk.Button()
            copy_btn.set_icon_name("edit-copy-symbolic")
            copy_btn.add_css_class("flat")
            copy_btn.add_css_class("circular")
            copy_btn.add_css_class("ai-cmd-btn")
            copy_btn.connect("clicked", self._on_copy_clicked, cmd)
            self._add_tooltip(copy_btn, _("Copy to clipboard"))
            a11y_label(copy_btn, _("Copy to clipboard"))
            buttons_box.append(copy_btn)

            cmd_block.append(buttons_box)
            commands_section.append(cmd_block)

        self.append(commands_section)

    def _on_run_clicked(self, button: Gtk.Button, command: str):
        """Emit signal to run command directly."""
        self.emit("run-command", command)

    def _on_execute_clicked(self, button: Gtk.Button, command: str):
        """Emit signal to execute command."""
        self.emit("execute-command", command)

    def _on_copy_clicked(self, button: Gtk.Button, command: str):
        """Copy command to clipboard."""
        clipboard = button.get_clipboard()
        clipboard.set(command)

    def update_content(self, content: str, commands: list[str] | None = None):
        """Update the message content (for streaming)."""
        self._content = content
        formatted_content = self._format_content(content)

        # Try to set markup, fallback to plain text if markup parsing fails
        try:
            self._label.set_markup(formatted_content)
        except Exception as e:
            _logger = get_logger("ashyterm.ui.widgets.ai_chat.message_bubble")
            _logger.debug(f"Markup parse failed in update_content: {e}")
            self._label.set_text(content)

        # Update commands if provided
        if commands and commands != self._commands:
            self._commands = commands
            # Remove old command buttons if any (skip header and content box)
            children = list(self)
            for child in children[2:]:  # Skip header box and content box
                self.remove(child)
            self._add_command_buttons()


# Register signals for MessageBubble
GObject.signal_new(
    "execute-command",
    MessageBubble,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_NONE,
    (GObject.TYPE_STRING,),
)

GObject.signal_new(
    "run-command",
    MessageBubble,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_NONE,
    (GObject.TYPE_STRING,),
)
