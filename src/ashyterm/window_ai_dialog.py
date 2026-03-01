# ashyterm/window_ai_dialog.py
"""AI dialog builder mixin for CommTerminalWindow."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gtk, Pango, Vte

from .utils.syntax_utils import get_bash_pango_markup
from .utils.translation_utils import _

from typing import Dict, List


class AIDialogBuilder:
    """Mixin providing AI assistant dialog creation and interaction."""

    def _on_ai_assistant_requested(self, *_args) -> None:
        if not getattr(self, "ai_assistant", None):
            return

        if not self.settings_manager.get("ai_assistant_enabled", False):
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_(
                        "Enable the AI assistant in Preferences > Terminal > AI Assistant."
                    )
                )
            )
            return

        missing = self.ai_assistant.missing_configuration()
        if missing:
            labels = {
                "provider": _("Provider"),
                "model": _("Model"),
                "api_key": _("API key"),
                "base_url": _("Base URL"),
            }
            readable = ", ".join(labels.get(item, item) for item in missing)
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_("Configure {items} in AI Assistant settings.").format(
                        items=readable
                    )
                )
            )
            return

        # Toggle AI overlay panel instead of showing dialog
        self.ui_builder.toggle_ai_panel()

    def show_ai_response_dialog(
        self,
        terminal: Vte.Terminal,
        reply: str,
        commands: List[Dict[str, str]],
        _code_snippets: List[Dict[str, str]],
    ) -> None:
        dialog_dimensions = self._calculate_ai_dialog_dimensions(reply, commands)

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("AI Assistant"),
            body=_("Here is what I found."),
            close_response="close",
        )
        dialog.set_default_size(*dialog_dimensions)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")

        content_box = self._create_ai_dialog_content(reply, commands, terminal, dialog)
        dialog.set_extra_child(content_box)

        def on_dialog_response(dlg, _response_id):
            dlg.destroy()

        dialog.connect("response", on_dialog_response)
        dialog.present()

    def _calculate_ai_dialog_dimensions(
        self, reply: str, commands: List[Dict[str, str]]
    ) -> tuple:
        """Calculate dialog dimensions based on content."""
        reply_lines = reply.splitlines() or [reply]
        max_line_length = max(len(line) for line in reply_lines)
        total_lines = len(reply_lines)

        for item in commands:
            if isinstance(item, dict):
                command_text = (item.get("command") or "").strip()
                description_text = (item.get("description") or "").strip()
                max_line_length = max(
                    max_line_length, len(command_text), len(description_text)
                )
            elif isinstance(item, str):
                max_line_length = max(max_line_length, len(item))

        approx_width = max(780, min(1200, max_line_length * 7 + 320))
        base_height = 460 if total_lines < 10 else 500
        height = min(820, max(420, base_height))

        return int(approx_width), int(height)

    def _create_ai_dialog_content(
        self,
        reply: str,
        commands: List[Dict[str, str]],
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> Gtk.Box:
        """Create the content box for AI response dialog."""
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        self._add_reply_section(content_box, reply)
        if commands:
            self._add_commands_section(content_box, commands, terminal, dialog)

        return content_box

    def _create_info_block(
        self, content_box: Gtk.Box, title: str, margin_top: int = 0
    ) -> Gtk.Box:
        """Create a styled info block with title."""
        frame = Gtk.Frame()
        frame.add_css_class("card")
        frame.set_hexpand(True)
        if margin_top:
            frame.set_margin_top(margin_top)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(12)
        inner.set_margin_bottom(12)
        inner.set_margin_start(16)
        inner.set_margin_end(16)

        heading = Gtk.Label(label=title, halign=Gtk.Align.START)
        heading.add_css_class("heading")
        inner.append(heading)

        frame.set_child(inner)
        content_box.append(frame)
        return inner

    def _add_reply_section(self, content_box: Gtk.Box, reply: str) -> None:
        """Add the reply section to the dialog."""
        reply_box = self._create_info_block(content_box, _("Response"))
        reply_lines = reply.splitlines() or [reply]

        reply_view = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            hexpand=True,
            vexpand=True,
        )
        reply_view.add_css_class("monospace")
        reply_buffer = reply_view.get_buffer()
        reply_buffer.set_text(reply.strip())

        reply_scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        reply_scrolled.set_min_content_height(max(140, min(300, len(reply_lines) * 20)))
        reply_scrolled.set_child(reply_view)
        reply_box.append(reply_scrolled)

    def _add_commands_section(
        self,
        content_box: Gtk.Box,
        commands: List[Dict[str, str]],
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> None:
        """Add the commands section to the dialog."""
        commands_box = self._create_info_block(
            content_box, _("Suggested Commands"), margin_top=6
        )

        for command_info in commands:
            command_text, description = self._extract_command_info(command_info)
            if not command_text:
                continue

            row = self._create_command_row(command_text, description, terminal, dialog)
            commands_box.append(row)

    def _extract_command_info(self, command_info) -> tuple:
        """Extract command and description from command info."""
        if isinstance(command_info, dict):
            command_text = (command_info.get("command") or "").strip()
            description = (command_info.get("description") or "").strip()
        elif isinstance(command_info, str):
            command_text = command_info.strip()
            description = ""
        else:
            command_text = ""
            description = ""
        return command_text, description

    def _create_command_row(
        self,
        command_text: str,
        description: str,
        terminal: Vte.Terminal,
        dialog: Adw.MessageDialog,
    ) -> Gtk.Box:
        """Create a row for a command in the dialog."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        info_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )

        highlighted_cmd = get_bash_pango_markup(command_text)
        command_label = Gtk.Label(
            label=f"<tt>{highlighted_cmd}</tt>",
            use_markup=True,
            halign=Gtk.Align.START,
            hexpand=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        info_box.append(command_label)

        if description:
            desc_label = Gtk.Label(
                label=description,
                halign=Gtk.Align.START,
                hexpand=True,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
            desc_label.add_css_class("dim-label")
            info_box.append(desc_label)

        row.append(info_box)

        run_button = Gtk.Button(label=_("Run"))
        run_button.connect(
            "clicked", self._on_ai_command_clicked, dialog, terminal, command_text
        )
        row.append(run_button)

        return row

    def _on_ai_command_clicked(
        self,
        _button: Gtk.Button,
        dialog: Adw.MessageDialog,
        terminal: Vte.Terminal,
        command: str,
    ) -> None:
        if self._execute_ai_command(terminal, command):
            dialog.destroy()

    def _execute_ai_command(self, terminal: Vte.Terminal, command: str) -> bool:
        command = (command or "").strip()
        if not command:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Command is empty, nothing to run."))
            )
            return False
        try:
            terminal.feed_child(f"{command}\n".encode("utf-8"))
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Command sent to the terminal."))
            )
            return True
        except Exception as exc:
            self.logger.error("Failed to execute AI command '%s': %s", command, exc)
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Failed to execute command: {}").format(exc))
            )
            return False
