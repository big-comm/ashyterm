# ashyterm/ui/ssh_dialogs.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from ..utils.translation_utils import _


def create_generic_ssh_error_dialog(
    parent_window: Gtk.Window, session_name: str, connection_string: str
) -> Adw.MessageDialog:
    """Creates and presents a generic dialog for any SSH connection failure."""
    dialog = Adw.MessageDialog(
        transient_for=parent_window,
        modal=True,
        heading=_("SSH Connection Failed"),
        body=_("Could not connect to session '{session_name}'.").format(
            session_name=session_name
        ),
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
