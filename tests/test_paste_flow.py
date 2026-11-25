"""Middle-click and clipboard paste routing (issues #22 and #51)."""

from types import SimpleNamespace

import gi
import pytest

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from ashyterm.terminal.paste_flow import TerminalPaste, middle_click_reaches_paste
from ashyterm.terminal.scroll_mode_guard import ScrollModeGuard


class FakeClipboard:
    def __init__(self, text):
        self.text = text

    def read_text_async(self, _cancellable, callback):
        callback(self, None)

    def read_text_finish(self, _result):
        return self.text


class FakeTerminal:
    def __init__(self, primary_paste=True, output=b"", observed=True, text="ls"):
        self._settings = SimpleNamespace(
            props=SimpleNamespace(gtk_enable_primary_paste=primary_paste)
        )
        if observed:
            ScrollModeGuard.for_terminal(self).feed(output)
        self.primary = FakeClipboard(text)
        self.clipboard = FakeClipboard(text)
        self.calls = []

    def get_settings(self):
        return self._settings

    def get_primary_clipboard(self):
        return self.primary

    def get_clipboard(self):
        return self.clipboard

    def paste_primary(self):
        self.calls.append("primary")

    def paste_clipboard(self):
        self.calls.append("clipboard")

    def paste_text(self, text):
        self.calls.append(("text", text))


class FakeGesture:
    def __init__(self, shift=False):
        self._state = Gdk.ModifierType.SHIFT_MASK if shift else Gdk.ModifierType(0)
        self.claimed = False

    def get_current_event_state(self):
        return self._state

    def set_state(self, state):
        self.claimed = state == Gtk.EventSequenceState.CLAIMED


MOUSE_ON = b"\x1b[?1049h\x1b[?1000h\x1b[?1006h"


class TestMiddleClickReachesPaste:
    def test_follows_system_primary_paste_setting(self):
        assert middle_click_reaches_paste(FakeTerminal(primary_paste=True), False)
        assert not middle_click_reaches_paste(FakeTerminal(primary_paste=False), False)

    def test_mouse_tracking_application_gets_the_click(self):
        assert not middle_click_reaches_paste(FakeTerminal(output=MOUSE_ON), False)

    def test_shift_overrides_mouse_tracking(self):
        assert middle_click_reaches_paste(FakeTerminal(output=MOUSE_ON), True)

    def test_unobserved_output_is_left_to_vte(self):
        assert not middle_click_reaches_paste(FakeTerminal(observed=False), False)


def _paste(review=True):
    settings = SimpleNamespace(get=lambda key, default=None: review)
    paste = TerminalPaste(settings, lambda: None)
    paste.reviews = []
    paste._show_review = lambda terminal, text: paste.reviews.append(text)
    return paste


def _middle_click(paste, terminal, shift=False):
    gesture = FakeGesture(shift)
    paste._on_middle_pressed(gesture, 1, 0.0, 0.0, terminal)
    return gesture


class TestMiddleClickPaste:
    def test_single_line_pastes_once_and_blocks_vte(self):
        paste, terminal = _paste(), FakeTerminal(text="ls")
        gesture = _middle_click(paste, terminal)

        assert gesture.claimed
        assert terminal.calls == ["primary"]

    def test_multi_line_selection_is_reviewed(self):
        paste, terminal = _paste(), FakeTerminal(text="a\nb")
        _middle_click(paste, terminal)

        assert paste.reviews == ["a\nb"]
        assert terminal.calls == []

    @pytest.mark.parametrize(
        "terminal_kwargs",
        [{"primary_paste": False}, {"output": MOUSE_ON}, {"observed": False}],
    )
    def test_vte_keeps_clicks_it_does_not_paste(self, terminal_kwargs):
        paste, terminal = _paste(), FakeTerminal(text="a\nb", **terminal_kwargs)
        gesture = _middle_click(paste, terminal)

        assert not gesture.claimed
        assert terminal.calls == [] and paste.reviews == []

    def test_review_disabled_leaves_click_to_vte(self):
        paste, terminal = _paste(review=False), FakeTerminal(text="a\nb")
        gesture = _middle_click(paste, terminal)

        assert not gesture.claimed and terminal.calls == []


class TestClipboardPaste:
    def test_review_disabled_pastes_natively(self):
        paste, terminal = _paste(review=False), FakeTerminal(text="a\nb")
        paste.paste_clipboard(terminal)

        assert terminal.calls == ["clipboard"]

    def test_multi_line_clipboard_is_reviewed(self):
        paste, terminal = _paste(), FakeTerminal(text="a\nb")
        paste.paste_clipboard(terminal)

        assert paste.reviews == ["a\nb"] and terminal.calls == []
