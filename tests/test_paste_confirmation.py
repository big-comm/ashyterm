"""Tests for the risky paste confirmation dialog."""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from ashyterm.terminal.paste_confirmation import build_paste_confirmation_dialog


def _editor_buffer(dialog):
    return dialog.get_extra_child().get_child().get_buffer()


def test_paste_is_default_and_cancel_remains_close_response():
    dialog = build_paste_confirmation_dialog("echo first\necho second", print)

    assert dialog.get_default_response() == "paste"
    assert dialog.get_close_response() == "cancel"
    assert (
        dialog.get_response_appearance("paste")
        == Adw.ResponseAppearance.SUGGESTED
    )


def test_full_text_is_shown_for_review():
    dialog = build_paste_confirmation_dialog("x" * 700, print)

    assert _editor_buffer(dialog).props.text == "x" * 700


def test_paste_sends_edited_text():
    pasted = []
    dialog = build_paste_confirmation_dialog("rm -rf /\necho ok", pasted.append)
    _editor_buffer(dialog).set_text("echo ok")

    dialog.emit("response", "paste")

    assert pasted == ["echo ok"]


def test_cancel_and_emptied_text_paste_nothing():
    pasted = []
    dialog = build_paste_confirmation_dialog("a\nb", pasted.append)
    dialog.emit("response", "cancel")
    _editor_buffer(dialog).set_text("")
    dialog.emit("response", "paste")

    assert pasted == []
