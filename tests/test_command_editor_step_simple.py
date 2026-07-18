"""Tests for the command-editor simple/form_info wizard steps."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ashyterm.ui.dialogs.command_manager.command_editor_step_simple import (
    _DEFAULT_ICON,
    build_step_form_info,
    build_step_simple,
)


def _stub_dialog() -> SimpleNamespace:
    return SimpleNamespace(
        wizard_stack=Gtk.Stack(),
        _on_pick_icon_clicked=MagicMock(),
        _on_icon_entry_changed=MagicMock(),
    )


# ── build_step_simple ───────────────────────────────────────


class TestBuildStepSimple:
    def test_step_is_registered(self):
        dialog = _stub_dialog()
        build_step_simple(dialog)
        assert dialog.wizard_stack.get_child_by_name("simple") is not None

    def test_publishes_expected_widgets(self):
        dialog = _stub_dialog()
        build_step_simple(dialog)
        for attr in (
            "simple_name_row",
            "simple_description_row",
            "simple_icon_preview",
            "simple_icon_entry",
            "simple_display_mode_row",
            "simple_execution_mode_row",
            "simple_command_textview",
        ):
            assert getattr(dialog, attr) is not None, f"{attr} not set"

    def test_execution_mode_defaults_to_insert_only(self):
        dialog = _stub_dialog()
        build_step_simple(dialog)
        # 0 = Insert Only (safer default — user must press Enter).
        assert dialog.simple_execution_mode_row.get_selected() == 0

    def test_icon_entry_starts_with_default_name(self):
        dialog = _stub_dialog()
        build_step_simple(dialog)
        assert dialog.simple_icon_entry.get_text() == _DEFAULT_ICON

    def test_icon_picker_click_delegates_with_simple_mode(self):
        """The picker-button's click lambda calls
        ``dialog._on_pick_icon_clicked("simple")``. Walk the icon row
        to find the button and fire the signal.
        """
        dialog = _stub_dialog()
        build_step_simple(dialog)
        step = dialog.wizard_stack.get_child_by_name("simple")
        # There are multiple buttons in the step; the icon-picker is
        # the one with icon_name "view-grid-symbolic".
        picker = next(
            b for b in _walk(step) if isinstance(b, Gtk.Button)
            and b.get_icon_name() == "view-grid-symbolic"
        )
        picker.emit("clicked")
        dialog._on_pick_icon_clicked.assert_called_with("simple")


# ── build_step_form_info ────────────────────────────────────


class TestBuildStepFormInfo:
    def test_step_is_registered(self):
        dialog = _stub_dialog()
        build_step_form_info(dialog)
        assert dialog.wizard_stack.get_child_by_name("form_info") is not None

    def test_publishes_form_variant_widgets(self):
        dialog = _stub_dialog()
        build_step_form_info(dialog)
        for attr in (
            "form_name_row",
            "form_description_row",
            "form_icon_preview",
            "form_icon_entry",
            "form_display_mode_row",
        ):
            assert getattr(dialog, attr) is not None, f"{attr} not set"

    def test_form_step_has_no_execution_mode_row(self):
        # Only the simple step has an execution mode; the form variant
        # always shows a dialog and runs on submit.
        dialog = _stub_dialog()
        build_step_form_info(dialog)
        assert not hasattr(dialog, "form_execution_mode_row")

    def test_form_step_has_no_command_textview(self):
        # Command body lives in the form-builder step (step 2b), not here.
        dialog = _stub_dialog()
        build_step_form_info(dialog)
        assert not hasattr(dialog, "form_command_textview")

    def test_icon_picker_click_delegates_with_form_mode(self):
        dialog = _stub_dialog()
        build_step_form_info(dialog)
        step = dialog.wizard_stack.get_child_by_name("form_info")
        picker = next(
            b for b in _walk(step) if isinstance(b, Gtk.Button)
            and b.get_icon_name() == "view-grid-symbolic"
        )
        picker.emit("clicked")
        dialog._on_pick_icon_clicked.assert_called_with("form")


# ── dialog delegation ──────────────────────────────────────


class TestDialogDelegation:
    def test_dialog_delegators_exist(self):
        from ashyterm.ui.dialogs.command_manager.command_editor_dialog import (
            CommandEditorDialog,
        )

        for name in ("_build_step_simple", "_build_step_form_info"):
            assert callable(getattr(CommandEditorDialog, name))


# ── helpers ────────────────────────────────────────────────


def _walk(widget: Gtk.Widget):
    """Yield ``widget`` and every descendant, DFS order."""
    yield widget
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()
