# ashyterm/terminal/pane.py

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango, Vte


class TerminalPane(Gtk.Box):
    """
    A container for a Vte.Terminal that includes an optional, integrated title bar
    for use in split-pane views.
    """

    def __init__(self, terminal: Vte.Terminal, title: str = "Terminal"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.terminal = terminal
        self._title = title
        self.on_close_requested: Optional[Callable[[Vte.Terminal], None]] = None

        # The title bar is created but only shown when needed (in a split)
        self.title_bar = self._create_title_bar()
        self.title_bar.set_visible(False)
        self.append(self.title_bar)

        # The terminal is always inside a ScrolledWindow
        self.scrolled_window = Gtk.ScrolledWindow(child=self.terminal)
        self.scrolled_window.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        self.scrolled_window.set_vexpand(True)
        self.append(self.scrolled_window)

    def _create_title_bar(self) -> Adw.HeaderBar:
        """Creates the slim header bar used for split panes."""
        title_bar = Adw.HeaderBar()
        title_bar.set_show_end_title_buttons(False)
        title_bar.set_show_start_title_buttons(False)

        self.title_label = Gtk.Label(
            label=self._title,
            ellipsize=Pango.EllipsizeMode.END,
            xalign=0.0,
            css_classes=["title-4"],
        )
        title_bar.set_title_widget(self.title_label)

        close_button = Gtk.Button(icon_name="window-close-symbolic")
        close_button.set_tooltip_text("Close Pane")
        close_button.add_css_class("flat")
        close_button.connect("clicked", self._on_close_clicked)
        title_bar.pack_end(close_button)

        return title_bar

    def _on_close_clicked(self, button) -> None:
        """Emits the close request when the close button is clicked."""
        if self.on_close_requested:
            self.on_close_requested(self.terminal)

    def set_title(self, title: str):
        """Sets the title of the pane."""
        self._title = title
        if hasattr(self, "title_label"):
            self.title_label.set_text(title)

    def show_title_bar(self, show: bool):
        """Shows or hides the integrated title bar."""
        self.title_bar.set_visible(show)

    def get_terminal(self) -> Vte.Terminal:
        """Returns the Vte.Terminal widget contained in this pane."""
        return self.terminal
