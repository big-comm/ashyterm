"""AI Chat Panel Widget — main panel with chat area, input, and command buttons."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from ....utils.accessibility import set_label as a11y_label
from ....utils.icons import icon_image
from ....utils.logger import get_logger
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ..conversation_history import ConversationHistoryPanel
from ._helpers import _extract_reply_from_json, _normalize_commands
from ._prompts import get_random_quick_prompts
from .message_bubble import LoadingIndicator, MessageBubble

if TYPE_CHECKING:
    from ....terminal.ai_assistant import AIAssistant

logger = get_logger(__name__)

# Path to CSS styles directory
_STYLES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "styles"


class AIChatPanel(Gtk.Box):
    """Persistent AI chat panel overlay."""

    __gsignals__ = {
        "execute-command": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "run-command": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "close-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(
        self, ai_assistant: AIAssistant, tooltip_helper=None, settings_manager=None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._ai_assistant = ai_assistant
        self._history_manager = ai_assistant._history_manager
        self._settings_manager = settings_manager
        self._current_assistant_bubble: MessageBubble | None = None
        self._quick_prompts = get_random_quick_prompts(6)

        # Retry support state
        self._last_request_message: str | None = None
        self._raw_streaming_content: str = ""

        # Minimum height for the panel, Paned handles resize
        self.set_size_request(-1, 200)
        self.set_vexpand(True)  # Expand in paned
        self.add_css_class("ai-chat-panel")

        self._setup_ui()
        self._connect_signals()
        self._apply_css()
        self._apply_transparency()

        # Load existing conversation if any
        self._load_conversation()

    def _add_tooltip(self, widget: Gtk.Widget, text: str):
        """Add tooltip to widget using custom helper or fallback to standard."""
        # Ensure tooltip is enabled (may have been disabled to force-close popup)
        widget.set_has_tooltip(True)
        helper = get_tooltip_helper()
        if helper:
            helper.add_tooltip(widget, text)
        else:
            widget.set_tooltip_text(text)

    def _setup_ui(self):
        """Build the chat panel UI."""
        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("ai-panel-header")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        # Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_label = Gtk.Label(label=_("AI Assistant"))
        title_label.add_css_class("title")
        title_box.append(title_label)

        header.set_title_widget(title_box)

        # New chat button (document-new-symbolic not in bundled icons, use system)
        new_chat_btn = Gtk.Button()
        new_chat_btn.set_icon_name("document-new-symbolic")
        new_chat_btn.add_css_class("flat")
        new_chat_btn.connect("clicked", self._on_new_chat)
        self._add_tooltip(new_chat_btn, _("New conversation"))
        a11y_label(new_chat_btn, _("New conversation"))
        header.pack_start(new_chat_btn)

        # History button (document-open-recent-symbolic not in bundled icons, use system)
        history_btn = Gtk.Button()
        history_btn.set_icon_name("document-open-recent-symbolic")
        history_btn.add_css_class("flat")
        history_btn.connect("clicked", self._on_show_history)
        self._add_tooltip(history_btn, _("View history"))
        a11y_label(history_btn, _("View history"))
        header.pack_start(history_btn)

        # Close button (uses bundled icon)
        close_btn = Gtk.Button()
        close_btn.set_child(icon_image("window-close-symbolic"))
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda b: self.emit("close-requested"))
        self._add_tooltip(close_btn, _("Close panel"))
        a11y_label(close_btn, _("Close panel"))
        header.pack_end(close_btn)

        self.append(header)

        # Chat content area with scrolling
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(100)  # Minimum height to prevent layout issues

        self._messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._messages_box.set_margin_start(8)
        self._messages_box.set_margin_end(8)
        self._messages_box.set_margin_top(8)
        self._messages_box.set_margin_bottom(8)

        scrolled.set_child(self._messages_box)
        self._scrolled = scrolled
        self.append(scrolled)

        # Loading indicator
        self._loading = LoadingIndicator()
        self._loading.connect("stop-clicked", self._on_stop_generation_clicked)
        self._loading.set_visible(False)
        self._loading.set_margin_start(16)
        self._loading.set_margin_end(16)
        self._loading.set_margin_bottom(8)
        self.append(self._loading)

        # Quick prompts container with header
        quick_prompts_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header with title and customize button
        prompts_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        prompts_header.set_margin_start(12)
        prompts_header.set_margin_end(8)

        prompts_title = Gtk.Label(label=_("Quick Prompts"))
        prompts_title.add_css_class("dim-label")
        prompts_title.set_xalign(0)
        prompts_title.set_hexpand(True)
        prompts_header.append(prompts_title)

        customize_btn = Gtk.Button()
        customize_btn.set_icon_name("emblem-system-symbolic")
        customize_btn.add_css_class("flat")
        customize_btn.add_css_class("circular")
        customize_btn.connect("clicked", self._on_customize_prompts)
        self._add_tooltip(customize_btn, _("Customize quick prompts"))
        a11y_label(customize_btn, _("Customize quick prompts"))
        prompts_header.append(customize_btn)

        quick_prompts_container.append(prompts_header)

        # Quick prompts area (shown when no messages)
        self._quick_prompts_box = Gtk.FlowBox()
        self._quick_prompts_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._quick_prompts_box.set_max_children_per_line(3)
        self._quick_prompts_box.set_min_children_per_line(1)
        self._quick_prompts_box.set_margin_start(8)
        self._quick_prompts_box.set_margin_end(8)
        self._quick_prompts_box.set_margin_bottom(8)
        self._populate_quick_prompts()
        quick_prompts_container.append(self._quick_prompts_box)

        self._quick_prompts_container = quick_prompts_container
        self.append(quick_prompts_container)

        # Input area with multi-line text view
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_box.set_margin_start(8)
        input_box.set_margin_end(8)
        input_box.set_margin_bottom(8)
        input_box.set_size_request(
            -1, 30
        )  # Minimum height to prevent negative allocation
        input_box.add_css_class("ai-input-box")

        # Create a scrolled window for the text view
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        text_scroll.set_min_content_height(24)  # Start as single line
        text_scroll.set_max_content_height(120)  # Max height before scrolling
        text_scroll.set_propagate_natural_height(True)
        text_scroll.set_hexpand(True)

        # Multi-line text view
        self._text_view = Gtk.TextView()
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_accepts_tab(False)  # Tab should not insert tab character
        self._text_view.add_css_class("ai-input-textview")

        # Get the buffer for text operations
        self._text_buffer = self._text_view.get_buffer()

        # Handle key press for Enter to send (Shift+Enter for newline)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self._text_view.add_controller(key_controller)

        # Auto-resize based on content
        self._text_buffer.connect("changed", self._on_text_changed)

        text_scroll.set_child(self._text_view)
        input_box.append(text_scroll)

        # Keep reference for text scroll widget
        self._text_scroll = text_scroll

        self._send_btn = Gtk.Button()
        self._send_btn.set_child(icon_image("go-up-symbolic"))
        self._send_btn.add_css_class("suggested-action")
        self._send_btn.add_css_class("circular")
        self._send_btn.set_valign(Gtk.Align.CENTER)  # Vertically center aligned
        self._send_btn.connect("clicked", self._on_send)
        self._add_tooltip(self._send_btn, _("Send message"))
        a11y_label(self._send_btn, _("Send message"))
        input_box.append(self._send_btn)

        self.append(input_box)

    def _on_text_changed(self, buffer):
        """Handle text buffer changes for auto-resize."""
        # Just trigger a queue_resize to allow natural height propagation
        self._text_view.queue_resize()

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        """Handle key press events for the text view."""
        # Escape key closes the panel
        if keyval == Gdk.KEY_Escape:
            self.emit("close-requested")
            return True  # Event handled

        # Check for Enter key without Shift
        if keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
            # Shift+Enter = newline, Enter alone = send
            if not (state & Gdk.ModifierType.SHIFT_MASK):
                self._on_send(self._text_view)
                return True  # Event handled
        return False  # Let the event propagate

    def _populate_quick_prompts(self):
        """Fill the quick prompts area with buttons."""
        for child in list(self._quick_prompts_box):
            self._quick_prompts_box.remove(child)

        # Check for custom prompts in settings
        prompts_to_use = self._quick_prompts
        if self._settings_manager:
            custom_prompts = self._settings_manager.get("ai_custom_quick_prompts", [])
            if custom_prompts:
                prompts_to_use = [
                    (p.get("emoji", "💬"), p.get("text", ""))
                    for p in custom_prompts
                    if p.get("text")
                ]

        for icon, text in prompts_to_use:
            btn = Gtk.Button()
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            icon_label = Gtk.Label(label=icon)
            btn_box.append(icon_label)

            text_label = Gtk.Label(label=text)
            text_label.set_ellipsize(Pango.EllipsizeMode.END)
            text_label.set_max_width_chars(20)
            btn_box.append(text_label)

            btn.set_child(btn_box)
            btn.add_css_class("flat")
            btn.connect("clicked", self._on_quick_prompt_clicked, text)
            self._add_tooltip(btn, text)
            a11y_label(btn, text)
            self._quick_prompts_box.append(btn)

    def _connect_signals(self):
        """Connect to AI assistant signals and theme changes."""
        self._ai_assistant.connect("streaming-chunk", self._on_streaming_chunk)
        self._ai_assistant.connect("response-ready", self._on_response_ready)
        self._ai_assistant.connect("error", self._on_error)

        # Listen for theme changes to update styles
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self._on_theme_changed)

        # Listen for application settings changes
        if self._settings_manager and hasattr(
            self._settings_manager, "add_change_listener"
        ):
            self._settings_manager.add_change_listener(self._on_settings_listener)

    def _on_settings_listener(self, key: str, old_value: Any, new_value: Any):
        """Handle settings changes relevant to the chat panel."""
        if key in (
            "gtk_theme",
            "color_scheme",
            "headerbar_transparency",
            "transparency",
        ):
            GLib.idle_add(self._on_theme_changed)

    def _on_theme_changed(self, *_):
        """Handle theme change (light/dark/custom) to update styles."""
        logger.debug("Theme changed, reapplying AI chat panel styles")

        # 1. Update panel background/transparency
        self._apply_transparency()

        # 2. Update all message bubbles
        child = self._messages_box.get_first_child()
        while child:
            if isinstance(child, MessageBubble):
                child.update_theme()
            child = child.get_next_sibling()

    def _apply_css(self):
        """Apply custom CSS for the chat panel from external file."""
        css_provider = Gtk.CssProvider()
        css_file = _STYLES_DIR / "ai_chat_panel.css"

        if css_file.exists():
            css_provider.load_from_path(str(css_file))
            logger.debug(f"Loaded AI chat panel CSS from {css_file}")
        else:
            logger.warning(f"AI chat panel CSS file not found: {css_file}")

        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_transparency(self):
        """Apply background transparency to the AI chat panel."""
        try:
            if not self._settings_manager:
                return

            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            transparency = self._settings_manager.get("headerbar_transparency", 0)

            colors = self._resolve_theme_colors(is_dark, transparency)
            css = self._build_transparency_css(colors)

            if hasattr(self, "_transparency_provider"):
                try:
                    Gtk.StyleContext.remove_provider_for_display(
                        Gdk.Display.get_default(), self._transparency_provider
                    )
                except Exception:
                    pass

            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_USER,
            )
            self._transparency_provider = provider
            theme_type = "dark" if is_dark else "light"
            logger.info(
                f"AI chat panel styles applied: {theme_type} theme, transparency={transparency}%"
            )
        except Exception as e:
            logger.warning(f"Failed to apply transparency to AI chat panel: {e}")

    def _resolve_theme_colors(self, is_dark: bool, transparency: int) -> dict:
        """Resolve all CSS color variables based on current theme."""
        gtk_theme = self._settings_manager.get("gtk_theme", "")
        if gtk_theme == "terminal":
            return self._terminal_theme_colors(is_dark, transparency)
        return self._adwaita_theme_colors(is_dark)

    def _terminal_theme_colors(self, is_dark: bool, transparency: int) -> dict:
        """Colors derived from the terminal color scheme."""
        scheme = self._settings_manager.get_color_scheme_data()
        base = scheme.get("background", "#000000" if is_dark else "#ffffff")
        fg = scheme.get("foreground", "#ffffff" if is_dark else "#000000")
        header_bg = scheme.get("headerbar_background", base)
        palette = scheme.get("palette", [])
        accent = palette[4] if len(palette) > 4 else "#3584e4"

        r, g, b = int(base[1:3], 16), int(base[3:5], 16), int(base[5:7], 16)
        if transparency > 0:
            alpha = max(0.0, min(1.0, 1.0 - (transparency / 100.0) ** 1.6))
            rgba_bg = f"rgba({r}, {g}, {b}, {alpha})"
        else:
            rgba_bg = f"rgb({r}, {g}, {b})"

        ar, ag, ab = int(accent[1:3], 16), int(accent[3:5], 16), int(accent[5:7], 16)
        lum = (0.299 * ar + 0.587 * ag + 0.114 * ab) / 255

        return {
            "rgba_bg": rgba_bg,
            "content_fg": fg,
            "bubble_user_bg": accent,
            "bubble_user_fg": "#ffffff" if lum < 0.5 else "#000000",
            "bubble_assistant_bg": header_bg,
            "bubble_assistant_border": f"color-mix(in srgb, {fg} 10%, transparent)",
            "input_bg": header_bg,
            "input_border": f"color-mix(in srgb, {fg} 10%, transparent)",
            "scroll_bg": f"rgba({r}, {g}, {b}, 0.3)"
            if transparency > 0
            else "transparent",
        }

    def _adwaita_theme_colors(self, is_dark: bool) -> dict:
        """Colors for standard Adwaita light/dark themes."""
        if is_dark:
            return {
                "rgba_bg": "rgb(30, 30, 30)",
                "content_fg": "var(--window-fg-color, #ffffff)",
                "bubble_user_bg": "var(--accent-color, #3584e4)",
                "bubble_user_fg": "#ffffff",
                "bubble_assistant_bg": "#2d2d2d",
                "bubble_assistant_border": "rgba(255, 255, 255, 0.1)",
                "input_bg": "#2d2d2d",
                "input_border": "rgba(255, 255, 255, 0.1)",
                "scroll_bg": "transparent",
            }
        return {
            "rgba_bg": "rgb(246, 245, 244)",
            "content_fg": "var(--window-fg-color, #000000)",
            "bubble_user_bg": "var(--accent-color, #3584e4)",
            "bubble_user_fg": "#ffffff",
            "bubble_assistant_bg": "#ffffff",
            "bubble_assistant_border": "rgba(0, 0, 0, 0.08)",
            "input_bg": "#ffffff",
            "input_border": "rgba(0, 0, 0, 0.12)",
            "scroll_bg": "transparent",
        }

    @staticmethod
    def _build_transparency_css(c: dict) -> str:
        """Build the full CSS string from resolved color dict."""
        return f"""
            .ai-chat-panel {{
                background-color: {c["rgba_bg"]};
                color: {c["content_fg"]};
            }}
            .ai-chat-panel scrolledwindow {{
                background-color: {c["scroll_bg"]};
            }}
            .ai-message-user {{
                background-color: {c["bubble_user_bg"]};
                background-image: linear-gradient(135deg, {c["bubble_user_bg"]}, shade({c["bubble_user_bg"]}, 0.92));
                color: {c["bubble_user_fg"]};
                border-radius: 16px 16px 4px 16px;
                padding: 10px 14px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
            }}
            .ai-message-assistant {{
                background-color: {c["bubble_assistant_bg"]};
                color: {c["content_fg"]};
                border: 1px solid {c["bubble_assistant_border"]};
                border-radius: 16px 16px 16px 4px;
                padding: 10px 14px;
                box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
            }}
            .ai-message-assistant.ai-message-error {{
                border-color: rgba(255, 60, 60, 0.8);
                background-color: rgba(255, 60, 60, 0.1);
            }}
            .ai-command-block {{
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 12px 14px;
                transition: all 200ms ease;
            }}
            .ai-command-block:hover {{
                background-color: #2d2d2d;
                border-color: alpha(@accent_color, 0.4);
                box-shadow: 0 2px 8px alpha(@accent_color, 0.1);
            }}
            .ai-command-text {{
                color: #e0e0e0;
            }}
            .ai-input-box {{
                background-color: {c["input_bg"]};
                color: {c["content_fg"]};
                border: 1px solid {c["input_border"]};
                border-radius: 14px;
                padding: 6px 10px;
                transition: border-color 200ms ease, box-shadow 200ms ease;
            }}
            .ai-input-box:focus-within {{
                border-color: @accent_color;
                box-shadow: 0 0 0 2px alpha(@accent_color, 0.2);
            }}
            .ai-input-textview {{
                background-color: transparent;
                color: {c["content_fg"]};
                padding: 4px;
                min-height: 24px;
            }}
            .ai-input-textview text {{
                background-color: transparent;
                color: {c["content_fg"]};
            }}
            .ai-panel-header {{
                background-color: {c["input_bg"]};
                color: {c["content_fg"]};
            }}
            .ai-panel-header .title {{
                color: {c["content_fg"]};
            }}
            .ai-panel-header button {{
                color: {c["content_fg"]};
            }}
            .ai-panel-header button image {{
                color: {c["content_fg"]};
            }}
            """

    def update_transparency(self):
        """Public method to update transparency when settings change."""
        self._apply_transparency()

    def _load_conversation(self):
        """Load existing conversation from history."""
        conversation = self._history_manager.get_current_conversation()
        if not conversation:
            return

        messages = conversation.get("messages", [])
        if messages:
            self._quick_prompts_container.set_visible(False)
            for msg in messages:
                # Normalize commands from history (may be list of dicts or strings)
                commands = _normalize_commands(msg.get("commands"))
                self._add_message_bubble(msg["role"], msg["content"], commands)

    def _add_message_bubble(
        self, role: str, content: str, commands: list | None = None
    ) -> MessageBubble:
        """Add a message bubble to the chat."""
        # Normalize commands to list of strings
        normalized_commands = _normalize_commands(commands)
        bubble = MessageBubble(
            role, content, normalized_commands, settings_manager=self._settings_manager
        )
        bubble.connect("execute-command", self._on_bubble_execute)
        bubble.connect("run-command", self._on_bubble_run)
        self._messages_box.append(bubble)

        # Scroll to bottom
        GLib.idle_add(self._scroll_to_bottom)

        return bubble

    def _scroll_to_bottom(self):
        """Scroll the chat to the bottom."""
        adj = self._scrolled.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _scroll_to_bottom_delayed(self):
        """Scroll to bottom with delay to allow layout to settle."""
        # First immediate scroll
        GLib.idle_add(self._scroll_to_bottom)
        # Then delayed scroll to catch layout changes (e.g., when commands appear)
        GLib.timeout_add(50, self._scroll_to_bottom)
        GLib.timeout_add(150, self._scroll_to_bottom)

    def _get_input_text(self) -> str:
        """Get text from the input text view."""
        start = self._text_buffer.get_start_iter()
        end = self._text_buffer.get_end_iter()
        text = self._text_buffer.get_text(start, end, False)
        return text.strip()

    def _set_input_text(self, text: str):
        """Set text in the input text view."""
        self._text_buffer.set_text(text)

    def _on_send(self, widget):
        """Handle send button click or Enter key."""
        text = self._get_input_text()
        if not text:
            return

        # Hide any visible tooltip on the send button immediately
        helper = get_tooltip_helper()
        if helper:
            helper.hide()

        # Store message for retry support
        self._last_request_message = text

        self._text_buffer.set_text("")
        self._text_view.set_sensitive(False)
        self._send_btn.set_sensitive(False)
        self._quick_prompts_container.set_visible(False)

        # Initialize raw streaming content tracker
        self._raw_streaming_content = ""

        # Add user message
        self._add_message_bubble("user", text)

        # Start loading indicator
        self._loading.start()

        # Create placeholder for assistant response
        self._current_assistant_bubble = self._add_message_bubble("assistant", "")

        # Send to AI using request_assistance_simple for panel context
        self._ai_assistant.request_assistance_simple(
            text, streaming_callback=self._handle_streaming_chunk
        )

    def _on_quick_prompt_clicked(self, button: Gtk.Button, text: str):
        """Handle quick prompt button click."""
        self._set_input_text(text)
        self._on_send(button)

    def _on_stop_generation_clicked(self, widget):
        """Handle stop button click to abort AI generation."""
        self._ai_assistant.cancel_request(-1)  # -1 is for chat panel

    def _on_streaming_chunk(self, _assistant, chunk: str, is_done: bool):
        """Handle streaming chunk from AI (GObject signal handler)."""
        if not is_done and self._current_assistant_bubble:
            current = self._current_assistant_bubble._content
            new_content = current + chunk
            # Try to extract reply from JSON if applicable
            display_content = _extract_reply_from_json(new_content)
            self._current_assistant_bubble.update_content(display_content)
            # Auto-scroll during streaming
            GLib.idle_add(self._scroll_to_bottom)

    def _handle_streaming_chunk(self, chunk: str, is_done: bool):
        """Handle streaming chunk from AI (callback handler)."""
        if not is_done and self._current_assistant_bubble:
            # Announce streaming start on first chunk
            if not self._raw_streaming_content:
                self.announce(
                    _("AI response streaming"),
                    Gtk.AccessibleAnnouncementPriority.MEDIUM,
                )
                self._loading.set_streaming_label()

            # Build the full accumulated content
            # We need to track raw content separately for JSON parsing
            self._raw_streaming_content += chunk

            # Try to extract reply from JSON if applicable
            display_content = _extract_reply_from_json(self._raw_streaming_content)
            self._current_assistant_bubble.update_content(display_content)
            # Auto-scroll during streaming
            GLib.idle_add(self._scroll_to_bottom)
        elif is_done:
            # Announce completion
            self.announce(
                _("AI response complete"),
                Gtk.AccessibleAnnouncementPriority.MEDIUM,
            )
            # Reset raw content tracker
            self._raw_streaming_content = ""

    def _on_response_ready(self, _assistant, response: str, commands):
        """Handle complete response from AI."""
        self._loading.stop()
        # Reset raw content tracker
        self._raw_streaming_content = ""

        # Clean up the response - remove any trailing JSON arrays
        clean_response = _extract_reply_from_json(response)
        if not clean_response:
            clean_response = response  # Fallback if extraction returns empty

        is_empty_error = False
        if not commands and not clean_response.strip():
            clean_response = _("An error occurred...")
            is_empty_error = True

        # Normalize commands to list of strings
        commands_list = _normalize_commands(list(commands) if commands else [])

        if self._current_assistant_bubble:
            self._current_assistant_bubble.update_content(clean_response, commands_list)
            if is_empty_error:
                self._current_assistant_bubble._content_box.add_css_class(
                    "ai-message-error"
                )
            self._current_assistant_bubble = None

        # Re-enable input AFTER updating content
        self._text_view.set_sensitive(True)
        self._send_btn.set_sensitive(True)
        # Restore tooltip
        self._add_tooltip(self._send_btn, _("Send message"))

        # Restore focus to input
        self._text_view.grab_focus()

        # Scroll to bottom with delay to allow command buttons to render
        self._scroll_to_bottom_delayed()

    def _on_error(self, _assistant, error_msg: str):
        """Handle error from AI with retry option."""
        self._loading.stop()
        # Reset raw content tracker
        self._raw_streaming_content = ""

        if self._current_assistant_bubble:
            # Remove the empty assistant bubble
            self._messages_box.remove(self._current_assistant_bubble)
            self._current_assistant_bubble = None

        # Re-enable input
        self._text_view.set_sensitive(True)
        self._send_btn.set_sensitive(True)
        # Restore tooltip
        self._add_tooltip(self._send_btn, _("Send message"))

        # Restore focus to input
        self._text_view.grab_focus()

        # Create error message box with retry button
        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        error_box.add_css_class("ai-message-assistant")
        error_box.set_margin_start(8)
        error_box.set_margin_end(8)

        # Error icon and message
        error_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        error_content.set_margin_start(8)
        error_content.set_margin_end(8)
        error_content.set_margin_top(8)

        error_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        error_icon.add_css_class("warning")
        error_content.append(error_icon)

        error_label = Gtk.Label()
        error_label.set_wrap(True)
        error_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        error_label.set_xalign(0)
        error_label.set_hexpand(True)
        error_label.set_selectable(True)
        error_label.set_markup(self._linkify_error(error_msg))
        error_content.append(error_label)

        error_box.append(error_content)

        # Retry button (only if we have a message to retry)
        if self._last_request_message:
            retry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            retry_box.set_halign(Gtk.Align.END)
            retry_box.set_margin_end(8)
            retry_box.set_margin_bottom(8)

            retry_btn = Gtk.Button()
            retry_btn_content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            retry_icon = icon_image("view-refresh-symbolic")
            retry_btn_content.append(retry_icon)
            retry_label = Gtk.Label(label=_("Retry"))
            retry_btn_content.append(retry_label)
            retry_btn.set_child(retry_btn_content)
            retry_btn.add_css_class("suggested-action")
            retry_btn.connect("clicked", self._on_retry_clicked, error_box)
            self._add_tooltip(retry_btn, _("Retry the last request"))

            retry_box.append(retry_btn)
            error_box.append(retry_box)

        self._messages_box.append(error_box)
        GLib.idle_add(self._scroll_to_bottom)

    def _on_bubble_execute(self, bubble: MessageBubble, command: str):
        """Handle execute command from a bubble (insert into terminal)."""
        self.emit("execute-command", command)

    def _on_bubble_run(self, bubble: MessageBubble, command: str):
        """Handle run command from a bubble (execute in terminal)."""
        self.emit("run-command", command)

    @staticmethod
    def _linkify_error(text: str) -> str:
        """Escape markup and convert URLs into clickable Pango links."""
        import re
        _URL_RE = re.compile(r'https?://[^\s<>"]+')
        parts: list[str] = []
        last = 0
        for m in _URL_RE.finditer(text):
            url = m.group()
            # Strip trailing punctuation not part of the URL
            while url and url[-1] in ')],;:.!?':
                if url[-1] == ')' and url.count('(') >= url.count(')'):
                    break
                url = url[:-1]
            parts.append(GLib.markup_escape_text(text[last:m.start()]))
            parts.append(f'<a href="{GLib.markup_escape_text(url)}">{GLib.markup_escape_text(url)}</a>')
            last = m.start() + len(url)
        parts.append(GLib.markup_escape_text(text[last:]))
        return "".join(parts)

    def _on_retry_clicked(self, button: Gtk.Button, error_box: Gtk.Box):
        """Handle retry button click - resend the last request."""
        if not self._last_request_message:
            return

        # Remove the error box
        self._messages_box.remove(error_box)

        # Disable input while processing
        self._text_view.set_sensitive(False)
        self._send_btn.set_sensitive(False)

        # Initialize raw streaming content tracker
        self._raw_streaming_content = ""

        # Start loading indicator
        self._loading.start()

        # Create placeholder for assistant response
        self._current_assistant_bubble = self._add_message_bubble("assistant", "")

        # Resend the same message
        self._ai_assistant.request_assistance_simple(
            self._last_request_message, streaming_callback=self._handle_streaming_chunk
        )

    def _on_customize_prompts(self, button: Gtk.Button):
        """Show dialog to customize quick prompts."""
        dialog = Adw.Dialog()
        dialog.set_title(_("Customize Quick Prompts"))
        dialog.set_content_width(500)
        dialog.set_content_height(450)

        # Main content box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar for the dialog
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: dialog.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label=_("Save"))
        save_btn.add_css_class("suggested-action")
        header.pack_end(save_btn)

        main_box.append(header)

        # Scrolled window for the list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # List box for prompts
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        list_box.set_margin_start(12)
        list_box.set_margin_end(12)
        list_box.set_margin_top(12)
        list_box.set_margin_bottom(12)

        # Load existing custom prompts or empty list
        custom_prompts = []
        if self._settings_manager:
            custom_prompts = self._settings_manager.get("ai_custom_quick_prompts", [])

        # Store row references for saving
        prompt_rows: list[Gtk.ListBoxRow] = []

        def create_prompt_row(emoji: str = "", text: str = "") -> Gtk.ListBoxRow:
            """Create a row for editing a prompt."""
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row_box.set_margin_start(8)
            row_box.set_margin_end(8)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)

            # Emoji entry (small)
            emoji_entry = Gtk.Entry()
            emoji_entry.set_placeholder_text("🔧")
            emoji_entry.set_text(emoji)
            emoji_entry.set_max_length(4)
            emoji_entry.set_width_chars(4)
            self._add_tooltip(emoji_entry, _("Emoji icon (optional)"))
            row_box.append(emoji_entry)

            # Text entry (expands)
            text_entry = Gtk.Entry()
            text_entry.set_placeholder_text(_("Enter prompt text..."))
            text_entry.set_text(text)
            text_entry.set_hexpand(True)
            row_box.append(text_entry)

            # Delete button (uses bundled icon)
            delete_btn = Gtk.Button()
            delete_btn.set_child(icon_image("user-trash-symbolic"))
            delete_btn.add_css_class("flat")
            delete_btn.add_css_class("destructive-action")
            self._add_tooltip(delete_btn, _("Remove this prompt"))

            def on_delete(btn):
                prompt_rows.remove((row, emoji_entry, text_entry))
                list_box.remove(row)

            delete_btn.connect("clicked", on_delete)
            row_box.append(delete_btn)

            row.set_child(row_box)
            prompt_rows.append((row, emoji_entry, text_entry))
            return row

        # Add existing prompts
        for prompt in custom_prompts:
            row = create_prompt_row(prompt.get("emoji", ""), prompt.get("text", ""))
            list_box.append(row)

        scrolled.set_child(list_box)
        main_box.append(scrolled)

        # Add button at bottom
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        add_box.set_halign(Gtk.Align.CENTER)
        add_box.set_margin_top(8)
        add_box.set_margin_bottom(12)

        add_btn = Gtk.Button()
        add_btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_icon = icon_image("list-add-symbolic")
        add_btn_content.append(add_icon)
        add_label = Gtk.Label(label=_("Add Prompt"))
        add_btn_content.append(add_label)
        add_btn.set_child(add_btn_content)
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: list_box.append(create_prompt_row()))
        add_box.append(add_btn)

        # Clear all button
        clear_btn = Gtk.Button()
        clear_btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        clear_icon = Gtk.Image.new_from_icon_name("edit-clear-all-symbolic")
        clear_btn_content.append(clear_icon)
        clear_label = Gtk.Label(label=_("Use Defaults"))
        clear_btn_content.append(clear_label)
        clear_btn.set_child(clear_btn_content)
        clear_btn.set_margin_start(12)
        self._add_tooltip(clear_btn, _("Clear custom prompts and use random defaults"))

        def on_clear(btn):
            # Remove all rows
            for row, _entry, _label in list(prompt_rows):
                list_box.remove(row)
            prompt_rows.clear()

        clear_btn.connect("clicked", on_clear)
        add_box.append(clear_btn)

        main_box.append(add_box)

        # Save handler
        def on_save(btn):
            # Collect all prompts
            new_prompts = []
            for row, emoji_entry, text_entry in prompt_rows:
                text = text_entry.get_text().strip()
                if text:  # Only save non-empty prompts
                    new_prompts.append(
                        {
                            "emoji": emoji_entry.get_text().strip() or "💬",
                            "text": text,
                        }
                    )

            # Save to settings
            if self._settings_manager:
                self._settings_manager.set("ai_custom_quick_prompts", new_prompts)

            # Refresh the quick prompts display
            self._populate_quick_prompts()

            dialog.close()

        save_btn.connect("clicked", on_save)

        dialog.set_child(main_box)
        dialog.present(self.get_root())

    def _on_new_chat(self, button: Gtk.Button):
        """Start a new conversation."""
        # Clear current messages
        for child in list(self._messages_box):
            self._messages_box.remove(child)

        # Start new conversation in history
        self._history_manager.new_conversation()

        # Refresh quick prompts with new random selection
        self._quick_prompts = get_random_quick_prompts(6)
        self._populate_quick_prompts()
        self._quick_prompts_container.set_visible(True)

        self._current_assistant_bubble = None

    def _on_show_history(self, button: Gtk.Button):
        """Show conversation history panel."""
        # Create a fresh history panel each time (widgets can't be reparented)
        history_panel = ConversationHistoryPanel(self._history_manager)
        history_panel.connect(
            "conversation-selected", self._on_history_conversation_selected
        )
        history_panel.connect("close-requested", self._on_history_close)
        history_panel.connect(
            "conversation-deleted", self._on_history_conversation_deleted
        )

        # Create a dialog window for the history panel
        dialog = Adw.Dialog()
        dialog.set_content_width(450)
        dialog.set_content_height(550)
        dialog.set_child(history_panel)

        # Store reference to close it programmatically
        self._history_dialog = dialog

        dialog.present(self.get_root())

    def _on_history_conversation_selected(
        self, _panel: ConversationHistoryPanel, conv_id: str
    ):
        """Handle conversation selection from history panel."""
        self._history_manager.load_conversation(conv_id)
        self._refresh_conversation()

        # Close the history dialog
        if hasattr(self, "_history_dialog") and self._history_dialog:
            self._history_dialog.close()
            self._history_dialog = None

    def _on_history_conversation_deleted(
        self, _panel: ConversationHistoryPanel, conv_id: str
    ):
        """Handle conversation deletion from history panel."""
        # Empty conv_id means all conversations were deleted
        if not conv_id or conv_id == self._history_manager._current_conversation_id:
            # Start a new conversation
            self._history_manager.new_conversation()
            self._refresh_conversation()

    def _on_history_close(self, _panel: ConversationHistoryPanel):
        """Handle close button from history panel."""
        # Close the history dialog
        if hasattr(self, "_history_dialog") and self._history_dialog:
            self._history_dialog.close()
            self._history_dialog = None

    def _refresh_conversation(self):
        """Refresh the display with current conversation."""
        # Clear messages
        for child in list(self._messages_box):
            self._messages_box.remove(child)

        self._quick_prompts_container.set_visible(False)
        self._load_conversation()

    def set_initial_text(self, text: str):
        """Set initial text in the input field."""
        self._set_input_text(text)
        self._text_view.grab_focus()

    def focus_input(self) -> None:
        """Focus the text input field."""
        self._text_view.grab_focus()
