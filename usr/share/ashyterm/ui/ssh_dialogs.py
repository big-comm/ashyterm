# ashyterm/ui/ssh_dialogs.py

# -*- coding: utf-8 -*-
"""
SSH-specific dialog components for Ashyterm.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from typing import Dict, Optional

from gi.repository import Adw, Gtk

from ..utils.translation_utils import _


class SSHTimeoutDialog(Adw.MessageDialog):
    """
    Dialog shown when SSH connection times out.
    Provides clear information about the failed connection attempt.
    """

    def __init__(
        self,
        parent_window: Optional[Gtk.Window],
        ssh_target: str,
        timeout_seconds: int = 30,
    ):
        """
        Initialize SSH timeout dialog.

        Args:
            parent_window: Parent window for modal behavior
            ssh_target: Original SSH target string (user@host:port:/path)
            timeout_seconds: Connection timeout duration
        """
        super().__init__()

        if parent_window:
            self.set_transient_for(parent_window)

        self.set_modal(True)

        # Parse SSH target components
        target_info = self._parse_ssh_target(ssh_target)

        # Set dialog properties
        self.set_heading(_("SSH Connection Timeout"))

        # Build detailed message
        message_parts = [
            _("The SSH connection attempt timed out after {seconds} seconds.").format(
                seconds=timeout_seconds
            ),
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

        message_parts.extend([
            "",
            _(
                "Please check your network connection and verify the server is accessible."
            ),
        ])

        self.set_body("\n".join(message_parts))

        # Add response buttons
        self.add_response("close", _("Close"))
        self.add_response("retry", _("Retry Connection"))

        # Make Close the default response
        self.set_default_response("close")
        self.set_close_response("close")

    def _parse_ssh_target(self, ssh_target: str) -> Dict[str, str]:
        """
        Parse SSH target string into components.

        Args:
            ssh_target: SSH target in format [user@]host[:port][:/path]

        Returns:
            Dictionary with parsed components
        """
        target_info = {}

        try:
            # Handle user@host portion
            if "@" in ssh_target:
                user_host, remainder = ssh_target.split("@", 1)
                target_info["username"] = user_host
            else:
                remainder = ssh_target

            # Handle port and path
            if ":" in remainder:
                parts = remainder.split(":", 2)  # Split at most 2 times
                target_info["hostname"] = parts[0]

                if len(parts) >= 2:
                    # Check if second part is numeric (port) or path
                    second_part = parts[1]
                    if second_part.isdigit():
                        target_info["port"] = second_part
                        if len(parts) == 3:
                            target_info["path"] = parts[2]
                    else:
                        # Second part is path, no port specified
                        target_info["path"] = second_part
            else:
                target_info["hostname"] = remainder

        except Exception:
            # Fallback to showing raw target if parsing fails
            target_info["hostname"] = ssh_target

        return target_info


class SSHPasswordDialog(Adw.MessageDialog):
    """
    Dialog for SSH password input when key authentication fails.
    """

    def __init__(
        self, parent_window: Optional[Gtk.Window], hostname: str, username: str = None
    ):
        """
        Initialize SSH password dialog.

        Args:
            parent_window: Parent window for modal behavior
            hostname: SSH server hostname
            username: SSH username (optional)
        """
        super().__init__()

        if parent_window:
            self.set_transient_for(parent_window)

        self.set_modal(True)

        # Set dialog properties
        target_display = f"{username}@{hostname}" if username else hostname
        self.set_heading(_("SSH Authentication Required"))
        self.set_body(
            _("Please enter the password for {target_display}").format(
                target_display=target_display
            )
        )

        # Create password entry
        self.password_entry = Gtk.PasswordEntry()
        self.password_entry.set_placeholder_text(_("Password"))
        self.password_entry.set_show_peek_icon(True)

        # Add entry to dialog
        self.set_extra_child(self.password_entry)

        # Add response buttons
        self.add_response("cancel", _("Cancel"))
        self.add_response("connect", _("Connect"))

        # Make Connect the default response
        self.set_default_response("connect")
        self.set_close_response("cancel")

        # Focus password entry
        self.password_entry.grab_focus()

    def get_password(self) -> str:
        """Get the entered password."""
        return self.password_entry.get_text()


def create_generic_ssh_error_dialog(
    parent_window: Gtk.Window, session_name: str, connection_string: str
) -> Adw.MessageDialog:
    """
    Creates and presents a generic dialog for any SSH connection failure.

    Args:
        parent_window: The parent window for the dialog.
        session_name: The name of the session that failed.
        connection_string: The user@host string for the connection.
        error_details: The raw error message captured from the terminal.
    """
    dialog = Adw.MessageDialog(
        transient_for=parent_window,
        modal=True,
        heading=_("SSH Connection Failed"),
        body=_("Could not connect to session '{session_name}'.").format(
            session_name=session_name
        ),
    )

    # UI/UX Rationale: Using a TextView in a ScrolledWindow is superior to an Expander.
    # 1. Visibility: The error is immediately visible, not hidden.
    # 2. Usability: Text is easily selectable and copyable for debugging.
    # 3. Aesthetics: Monospace font is appropriate for terminal output.
    # 4. Layout: ScrolledWindow prevents the dialog from becoming too large.

    # Create a text view to display the error details in a copy-paste friendly way
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

    # Put the text view inside a scrolled window
    scrolled_window = Gtk.ScrolledWindow()
    scrolled_window.set_child(text_view)
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled_window.set_min_content_height(120)  # Ensure a reasonable initial size

    # Add the scrolled window as the extra content for the dialog
    dialog.set_extra_child(scrolled_window)

    dialog.add_response("close", _("Close"))
    dialog.set_default_response("close")

    return dialog
