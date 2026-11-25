"""Clipboard and middle-click paste routed through the risky-paste review.

VTE 0.84 pastes PRIMARY on middle-click by itself and follows GTK's
``gtk-enable-primary-paste`` (off by default since GNOME 50). Ashyterm only
takes the click over when the review dialog may apply; otherwise VTE keeps
it, which also keeps middle-click reports for mouse-tracking applications.
"""

import re
from typing import Any, Callable, Optional

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from .paste_confirmation import build_paste_confirmation_dialog
from .scroll_mode_guard import ScrollModeGuard

# Intentionally narrow: mundane multi-line commits should not be blocked.
RISKY_PASTE_PATTERNS: tuple = (
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\brm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\bmkfs\.", re.IGNORECASE),
    re.compile(r"\bchmod\s+(-R\s+)?[0-7]*777\b"),
    re.compile(r">\s*/dev/sd[a-z]"),
)

# Rules spanning a pipeline run per segment: a regex like `curl[^|]*\|` retried
# from every match start backtracks quadratically on a long pasted line.
_DD = re.compile(r"\bdd\s", re.IGNORECASE)
_DD_DEVICE_TARGET = re.compile(r"of=/dev/", re.IGNORECASE)
_DOWNLOADER = re.compile(r"\b(curl|wget)\b", re.IGNORECASE)
_SHELL = re.compile(r"\s*(sudo\s+)?(bash|sh)\b", re.IGNORECASE)


def _risky_pipeline(text: str) -> bool:
    segments = text.split("|")
    for index, segment in enumerate(segments):
        dd = _DD.search(segment)
        if dd and _DD_DEVICE_TARGET.search(segment, dd.end()):
            return True
        piped_into = segments[index + 1] if index + 1 < len(segments) else ""
        if _SHELL.match(piped_into) and _DOWNLOADER.search(segment):
            return True
    return False


def paste_needs_confirmation(text: str) -> bool:
    """True when ``text`` looks multi-line or matches a risky pattern."""
    if not text:
        return False
    if "\n" in text.rstrip("\n"):  # a trailing newline alone is fine
        return True
    return any(rx.search(text) for rx in RISKY_PASTE_PATTERNS) or _risky_pipeline(text)


def middle_click_reaches_paste(terminal: Vte.Terminal, shift_held: bool) -> bool:
    """Whether VTE would paste PRIMARY for this middle-click.

    Mouse-tracking applications receive the click instead, unless Shift
    is held. Unobserved output means the tracking state is unknown.
    """
    if not terminal.get_settings().props.gtk_enable_primary_paste:
        return False
    guard = getattr(terminal, "_ashy_mode_guard", None)
    if not isinstance(guard, ScrollModeGuard) or not guard.observed:
        return False
    return shift_held or not guard.mouse_tracking


class TerminalPaste:
    """Paste entry points that honour the ``paste_warning_enabled`` review."""

    def __init__(self, settings_manager: Any, dialog_parent: Callable[[], Optional[Gtk.Widget]]):
        self._settings_manager = settings_manager
        self._dialog_parent = dialog_parent

    def _review_enabled(self) -> bool:
        return bool(self._settings_manager.get("paste_warning_enabled", True))

    def paste_clipboard(self, terminal: Vte.Terminal) -> None:
        """Paste CLIPBOARD text, reviewing multi-line or risky content."""
        if not self._review_enabled():
            terminal.paste_clipboard()
            return
        self._review_then_paste(terminal, terminal.get_clipboard(), terminal.paste_clipboard)

    def attach_middle_click(self, terminal: Vte.Terminal) -> Gtk.GestureClick:
        """Intercept middle-click before VTE's own gesture sees it."""
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", self._on_middle_pressed, terminal)
        terminal.add_controller(gesture)
        return gesture

    def _on_middle_pressed(
        self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float,
        terminal: Vte.Terminal,
    ) -> None:
        if not self._review_enabled():
            return
        shift_held = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        if not middle_click_reaches_paste(terminal, shift_held):
            return
        # Claiming in the capture phase keeps VTE from pasting a second time.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._review_then_paste(
            terminal, terminal.get_primary_clipboard(), terminal.paste_primary
        )

    def _review_then_paste(
        self, terminal: Vte.Terminal, clipboard: Gdk.Clipboard,
        paste_native: Callable[[], None],
    ) -> None:
        def on_text(clip: Gdk.Clipboard, result: Any) -> None:
            try:
                text = clip.read_text_finish(result)
            except GLib.Error:
                text = None
            if text and paste_needs_confirmation(text):
                self._show_review(terminal, text)
            else:
                paste_native()

        clipboard.read_text_async(None, on_text)

    def _show_review(self, terminal: Vte.Terminal, text: str) -> None:
        # Paste the reviewed (possibly edited) text, not whatever the
        # selection holds by the time the user confirms.
        dialog = build_paste_confirmation_dialog(text, terminal.paste_text)
        parent = self._dialog_parent()
        dialog.present(parent if isinstance(parent, Gtk.Widget) else terminal)
