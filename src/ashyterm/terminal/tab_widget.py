# ashyterm/terminal/tab_widget.py
"""Tab widget factory + color helpers.

The tab button shown in the tab bar is a small ``Gtk.Box`` with an
optional icon, a label, and a close button. Constructing it is 60
lines of GTK scaffolding that wires a half-dozen gesture controllers
— keeping it out of :mod:`tabs` lets the TabManager stay focused on
tab-list bookkeeping.

Per-tab color styling (the thin colored border that marks a tab)
also lives here since it's the same lifecycle: one provider per tab
widget, installed at creation and swapped on user change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gtk, Pango

from ..sessions.models import SessionItem
from ..utils.accessibility import (
    set_description as a11y_desc,
    set_label as a11y_label,
)
# Re-export so legacy callers keep ``from .tab_widget import
# contrasting_text_for_rgba`` working after the helper moved to
# ``utils.color_luminance``.
from ..utils.color_luminance import (  # noqa: F401
    contrasting_text_for_rgba,
)
from ..utils.icons import icon_button, icon_image
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .tabs import TabManager


def generate_unique_sftp_name(
    base_session_name: str, *, existing_names: Iterable[str]
) -> str:
    """Return an SFTP tab label that doesn't collide with anything open.

    Base is ``SFTP-<session-name>``. If that's free, return it as-is;
    otherwise append ``(N)`` with the lowest N that makes the name
    unique. ``existing_names`` is the iterable of tab titles already
    in the bar — we only look at names starting with the base prefix.
    """
    base_title = f"SFTP-{base_session_name}"
    existing_titles = {
        name for name in existing_names if name.startswith(base_title)
    }

    if base_title not in existing_titles:
        return base_title

    suffix = 1
    while True:
        candidate = f"{base_title}({suffix})"
        if candidate not in existing_titles:
            return candidate
        suffix += 1


def apply_tab_color(widget: Gtk.Widget, color_string: Optional[str]) -> None:
    """Attach (or detach) a colored border style provider to ``widget``.

    The caller stores the provider on the widget as ``_color_provider``
    so a subsequent call can remove the old provider before applying a
    new one. Passing ``None`` clears the color.
    """
    style_context = widget.get_style_context()
    if hasattr(widget, "_color_provider"):
        style_context.remove_provider(widget._color_provider)
        del widget._color_provider

    if not color_string:
        return

    provider = Gtk.CssProvider()
    css = f"""
        .custom-tab-button {{
            border: 1px solid {color_string};
        }}
    """
    provider.load_from_string(css)
    style_context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    widget._color_provider = provider


def _icon_name_for_session(session: SessionItem) -> Optional[str]:
    """Pick the tab icon based on session type.

    SFTP sessions (name-prefixed by convention) get ``folder-remote``,
    plain SSH gets a server glyph, local sessions show no icon.
    """
    if session.name.startswith("SFTP-"):
        return "folder-remote-symbolic"
    if session.is_ssh():
        return "network-server-symbolic"
    return None


def create_tab_widget(
    manager: "TabManager",
    page: Adw.ViewStackPage,  # noqa: ARG001 — preserved for signature parity
    session: SessionItem,
) -> Gtk.Box:
    """Build the per-tab ``Gtk.Box`` hosted in the tab bar.

    Attaches: click gesture (left = activate, right = context menu),
    motion gesture (drop-target highlighting during move mode), and
    a close button. Stores ``label_widget``, ``close_button``,
    ``_base_title``, ``_is_local``, ``session_item`` as attributes
    so the rest of :mod:`tabs` can reach them without re-lookup.
    """
    tab_widget = Gtk.Box(spacing=6)
    tab_widget.add_css_class("custom-tab-button")
    tab_widget.add_css_class("raised")

    icon_name = _icon_name_for_session(session)
    if icon_name:
        tab_widget.append(icon_image(icon_name))

    label = Gtk.Label(
        label=session.name, ellipsize=Pango.EllipsizeMode.START, xalign=1.0
    )
    label.set_width_chars(8)
    tab_widget.append(label)

    close_button = icon_button(
        "window-close-symbolic", css_classes=["circular", "flat"]
    )
    a11y_label(close_button, _("Close tab"))
    tab_widget.append(close_button)
    a11y_label(tab_widget, session.name)
    a11y_desc(tab_widget, _("Terminal tab"))

    left_click = Gtk.GestureClick.new()
    left_click.connect("pressed", manager._on_tab_clicked, tab_widget)
    tab_widget.add_controller(left_click)

    right_click = Gtk.GestureClick.new()
    right_click.set_button(Gdk.BUTTON_SECONDARY)
    right_click.connect("pressed", manager._on_tab_right_click, tab_widget)
    tab_widget.add_controller(right_click)

    # Motion controller drives the drop-target highlight during tab/group move.
    motion_controller = Gtk.EventControllerMotion()
    motion_controller.connect("motion", manager._on_tab_motion, tab_widget)
    motion_controller.connect("leave", manager._on_tab_leave, tab_widget)
    tab_widget.add_controller(motion_controller)

    close_button.connect(
        "clicked", manager._on_tab_close_button_clicked, tab_widget
    )

    tab_widget.label_widget = label
    tab_widget.close_button = close_button
    tab_widget._base_title = session.name or f"Terminal-{manager.get_tab_count() + 1}"
    tab_widget._is_local = session.is_local()
    tab_widget.session_item = session

    apply_tab_color(tab_widget, session.tab_color)

    return tab_widget
