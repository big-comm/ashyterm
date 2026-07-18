"""Stable terminal body widgets shared by tabs and split panes."""

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gtk, Vte


def create_terminal_body(terminal: Vte.Terminal) -> Gtk.Box:
    """Build a terminal body with a scroll-only event capture host."""
    scrolled_window = Gtk.ScrolledWindow(child=terminal)
    scrolled_window.set_vexpand(True)
    scrolled_window.set_hexpand(True)

    scroll_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    scroll_host.set_vexpand(True)
    scroll_host.set_hexpand(True)
    scroll_host.append(scrolled_window)

    terminal_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    terminal_body.set_vexpand(True)
    terminal_body.set_hexpand(True)
    terminal_body.append(scroll_host)

    terminal_body._ashy_terminal_body = True
    terminal_body.terminal = terminal
    terminal_body.scrolled_window = scrolled_window
    terminal_body.scroll_host = scroll_host
    scroll_host._ashy_scroll_host = True
    scrolled_window._ashy_terminal_body = terminal_body
    scrolled_window._ashy_scroll_host = scroll_host
    return terminal_body


def is_terminal_body(widget: object) -> bool:
    """Return whether ``widget`` is an AshyTerm terminal body."""
    return getattr(widget, "_ashy_terminal_body", False) is True


def get_terminal_scrolled_window(
    widget: object,
) -> Optional[Gtk.ScrolledWindow]:
    """Return the terminal scrolled window stored on a body or pane."""
    if isinstance(widget, Gtk.ScrolledWindow):
        return widget
    scrolled_window = getattr(widget, "scrolled_window", None)
    if isinstance(scrolled_window, Gtk.ScrolledWindow):
        return scrolled_window
    return None


def get_terminal_scroll_host(widget: object) -> Optional[Gtk.Widget]:
    """Return the scroll capture host stored on a body, pane, or window."""
    scroll_host = getattr(widget, "scroll_host", None)
    if isinstance(scroll_host, Gtk.Widget):
        return scroll_host
    scroll_host = getattr(widget, "_ashy_scroll_host", None)
    if isinstance(scroll_host, Gtk.Widget):
        return scroll_host
    return None
