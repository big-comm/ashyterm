"""Risky paste confirmation dialog."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from ..utils.translation_utils import _


def build_paste_confirmation_dialog(text: str) -> Adw.AlertDialog:
    """Build a paste dialog that defaults to the affirmative action."""
    preview = text if len(text) <= 600 else text[:600] + "…"
    dialog = Adw.AlertDialog(
        heading=_("Confirm paste"),
        body=_(
            "The clipboard contains multiple lines or a potentially "
            "risky command. Paste anyway?"
        ),
    )
    scrolled = Gtk.ScrolledWindow(
        min_content_height=160,
        max_content_height=300,
        has_frame=True,
    )
    label = Gtk.Label(label=preview, xalign=0.0, yalign=0.0, wrap=True)
    label.add_css_class("monospace")
    label.set_margin_start(8)
    label.set_margin_end(8)
    label.set_margin_top(8)
    label.set_margin_bottom(8)
    scrolled.set_child(label)
    dialog.set_extra_child(scrolled)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("paste", _("Paste"))
    dialog.set_response_appearance("paste", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("paste")
    dialog.set_close_response("cancel")
    return dialog
