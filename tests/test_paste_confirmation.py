"""Tests for the risky paste confirmation dialog."""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from ashyterm.terminal.paste_confirmation import build_paste_confirmation_dialog


def test_paste_is_default_and_cancel_remains_close_response():
    dialog = build_paste_confirmation_dialog("echo first\necho second")

    assert dialog.get_default_response() == "paste"
    assert dialog.get_close_response() == "cancel"
    assert (
        dialog.get_response_appearance("paste")
        == Adw.ResponseAppearance.SUGGESTED
    )


def test_preview_is_truncated():
    dialog = build_paste_confirmation_dialog("x" * 700)
    scrolled = dialog.get_extra_child()
    viewport = scrolled.get_child()
    label = viewport.get_child()

    assert label.get_text() == "x" * 600 + "…"
