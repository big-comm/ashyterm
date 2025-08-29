# ashyterm/ui/ssh_dialogs.py

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from typing import Dict, Optional

from ..utils.translation_utils import _


class SSHTimeoutDialog(Adw.MessageDialog):
    """Dialog shown when SSH connection times out."""
    def __init__(self, parent_window: Optional[Gtk.Window], ssh_target: str, timeout_seconds: int = 30):
        super().__init__(transient_for=parent_window, modal=True)

        target_info = self._parse_ssh_target(ssh_target)
        self.set_heading(_("SSH Connection Timeout"))

        message_parts = [
            _("The SSH connection attempt timed out after {seconds} seconds.").format(seconds=timeout_seconds),
            "",
            _("Connection details:"),
        ]
        if target_info.get("username"):
            message_parts.append(f"• {_('Username')}: {target_info['username']}")
        message_parts.append(f"• {_('Host')}: {target_info['hostname']}")
        if target_info.get("port"):
            message_parts.append(f"• {_('Port')}: {target_info['port']}")
        if target_info.get("path"):
            message_parts.append(f"• {_('Remote path')}: {target_info['path']}")
        message_parts.extend(["", _("Please check your network connection and verify the server is accessible.")])
        self.set_body("\n".join(message_parts))

        self.add_response("close", _("Close"))
        self.add_response("retry", _("Retry Connection"))
        self.set_default_response("close")
        self.set_close_response("close")

    def _parse_ssh_target(self, ssh_target: str) -> Dict[str, str]:
        """Parse SSH target string into components."""
        target_info = {}
        try:
            if "@" in ssh_target:
                user_host, remainder = ssh_target.split("@", 1)
                target_info["username"] = user_host
            else:
                remainder = ssh_target

            if ":" in remainder:
                parts = remainder.split(":", 2)
                target_info["hostname"] = parts[0]
                if len(parts) >= 2:
                    second_part = parts[1]
                    if second_part.isdigit():
                        target_info["port"] = second_part
                        if len(parts) == 3:
                            target_info["path"] = parts[2]
                    else:
                        target_info["path"] = second_part
            else:
                target_info["hostname"] = remainder
        except Exception:
            target_info["hostname"] = ssh_target
        return target_info


class SSHPasswordDialog(Adw.MessageDialog):
    """Dialog for SSH password input when key authentication fails."""
    def __init__(self, parent_window: Optional[Gtk.Window], hostname: str, username: str = None):
        super().__init__(transient_for=parent_window, modal=True)

        target_display = f"{username}@{hostname}" if username else hostname
        self.set_heading(_("SSH Authentication Required"))
        self.set_body(_("Please enter the password for {target_display}").format(target_display=target_display))

        self.password_entry = Gtk.PasswordEntry(placeholder_text=_("Password"), show_peek_icon=True)
        self.set_extra_child(self.password_entry)

        self.add_response("cancel", _("Cancel"))
        self.add_response("connect", _("Connect"))
        self.set_default_response("connect")
        self.set_close_response("cancel")
        self.password_entry.grab_focus()

    def get_password(self) -> str:
        """Get the entered password."""
        return self.password_entry.get_text()


def create_generic_ssh_error_dialog(parent_window: Gtk.Window, session_name: str, connection_string: str) -> Adw.MessageDialog:
    """Creates and presents a generic dialog for any SSH connection failure."""
    dialog = Adw.MessageDialog(
        transient_for=parent_window,
        modal=True,
        heading=_("SSH Connection Failed"),
        body=_("Could not connect to session '{session_name}'.").format(session_name=session_name),
    )

    text_view = Gtk.TextView(
        editable=False,
        cursor_visible=False,
        wrap_mode=Gtk.WrapMode.WORD_CHAR,
        left_margin=12,
        right_margin=12,
        top_margin=12,
        bottom_margin=12,
    )
    text_view.add_css_class("monospace")

    scrolled_window = Gtk.ScrolledWindow()
    scrolled_window.set_child(text_view)
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled_window.set_min_content_height(120)

    dialog.set_extra_child(scrolled_window)
    dialog.add_response("close", _("Close"))
    dialog.set_default_response("close")

    return dialog
