"""Tests for the command-editor type-choice wizard step."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ashyterm.ui.dialogs.command_manager.command_editor_step_type import (
    build_step_type_choice,
)


def _stub_dialog() -> SimpleNamespace:
    """Minimal CommandEditorDialog stub for the step builder."""
    return SimpleNamespace(
        wizard_stack=Gtk.Stack(),
        _select_command_type=MagicMock(),
    )


# ── build_step_type_choice ──────────────────────────────────


class TestBuildStepTypeChoice:
    def test_step_is_added_to_stack(self):
        dialog = _stub_dialog()
        build_step_type_choice(dialog)
        assert dialog.wizard_stack.get_child_by_name("type_choice") is not None

    def test_clicking_simple_card_selects_simple_type(self):
        dialog = _stub_dialog()
        build_step_type_choice(dialog)

        # Walk the newly-added step and find the two ``.card`` buttons.
        step = dialog.wizard_stack.get_child_by_name("type_choice")
        buttons = _collect_buttons(step)
        assert len(buttons) == 2

        # The first card is "Simple"; synthesise a click and verify the
        # dialog's selector was invoked with the right argument.
        buttons[0].emit("clicked")
        dialog._select_command_type.assert_called_with("simple")

    def test_clicking_form_card_selects_form_type(self):
        dialog = _stub_dialog()
        build_step_type_choice(dialog)
        step = dialog.wizard_stack.get_child_by_name("type_choice")
        buttons = _collect_buttons(step)

        buttons[1].emit("clicked")
        dialog._select_command_type.assert_called_with("form")

    def test_cards_get_card_and_flat_css(self):
        dialog = _stub_dialog()
        build_step_type_choice(dialog)
        step = dialog.wizard_stack.get_child_by_name("type_choice")
        buttons = _collect_buttons(step)

        for btn in buttons:
            classes = btn.get_css_classes()
            assert "card" in classes
            assert "flat" in classes


# ── dialog delegation ──────────────────────────────────────


class TestDialogDelegation:
    def test_dialog_still_exposes_build_step_type_choice(self):
        from ashyterm.ui.dialogs.command_manager.command_editor_dialog import (
            CommandEditorDialog,
        )

        assert callable(CommandEditorDialog._build_step_type_choice)


# ── helpers ─────────────────────────────────────────────────


def _collect_buttons(widget: Gtk.Widget) -> list[Gtk.Button]:
    """DFS walk of ``widget``'s subtree returning every ``Gtk.Button``."""
    buttons: list[Gtk.Button] = []

    def visit(w: Gtk.Widget) -> None:
        if isinstance(w, Gtk.Button):
            buttons.append(w)
            return  # Buttons are leaves for our purposes
        child = w.get_first_child() if hasattr(w, "get_first_child") else None
        while child is not None:
            visit(child)
            child = child.get_next_sibling()

    visit(widget)
    return buttons
