"""Risky paste confirmation dialog with an editable preview."""

from typing import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from ..utils.translation_utils import _


def _build_editor(text: str) -> Gtk.TextView:
    editor = Gtk.TextView(
        monospace=True,
        wrap_mode=Gtk.WrapMode.WORD_CHAR,
        top_margin=8,
        bottom_margin=8,
        left_margin=8,
        right_margin=8,
    )
    editor.get_buffer().set_text(text)
    return editor


def build_paste_confirmation_dialog(
    text: str, on_paste: Callable[[str], None]
) -> Adw.AlertDialog:
    """Build a review dialog; ``on_paste`` receives the possibly edited text."""
    dialog = Adw.AlertDialog(
        heading=_("Confirm paste"),
        body=_(
            "The clipboard contains multiple lines or a potentially "
            "risky command. Paste anyway?"
        ),
    )
    editor = _build_editor(text)
    scrolled = Gtk.ScrolledWindow(
        min_content_height=160,
        max_content_height=300,
        propagate_natural_height=True,
        has_frame=True,
        child=editor,
    )
    dialog.set_extra_child(scrolled)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("paste", _("Paste"))
    dialog.set_response_appearance("paste", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("paste")
    dialog.set_close_response("cancel")

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response != "paste":
            return
        buffer = editor.get_buffer()
        edited = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if edited:
            on_paste(edited)

    dialog.connect("response", on_response)
    return dialog
