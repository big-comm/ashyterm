"""GTK CSS utilities — apply inline CSS without boilerplate."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk


def apply_inline_css(
    css: str,
    priority: int = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    display: Gdk.Display | None = None,
) -> Gtk.CssProvider:
    """Create a CssProvider, load *css*, and attach it to *display*.

    Returns the provider so callers can later remove it if needed.
    """
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(
        display or Gdk.Display.get_default(), provider, priority,
    )
    return provider
