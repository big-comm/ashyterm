# ashyterm/ui/dialogs/command_manager/command_editor_step_simple.py
"""Wizard steps 1 and 2a: basic info + command body.

Both the "simple" and "form" paths share a near-identical Basic
Information group (name, description, icon, display mode). The common
piece lives in :func:`_build_basic_info_group`; the two step builders
wire the "simple" / "form" variant (icon-entry callback mode, optional
execution-mode row, optional command text view).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ....utils.accessibility import set_label as a11y_label
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ...widgets.bash_text_view import BashTextView
from ..base_dialog import BaseDialog

if TYPE_CHECKING:
    from .command_editor_dialog import CommandEditorDialog


_DEFAULT_ICON = "utilities-terminal-symbolic"


def _outer_step_box() -> Gtk.Box:
    """Vertical box with the margins/spacing common to both steps."""
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=16,
        margin_top=16,
        margin_bottom=16,
        margin_start=16,
        margin_end=16,
    )


def _build_icon_row(
    dialog: "CommandEditorDialog", *, mode: str
) -> Tuple[Adw.ActionRow, Gtk.Image, Gtk.Entry]:
    """Shared icon row used by both steps.

    Returns ``(row, preview, entry)`` so the caller can stash the
    preview and entry under the mode-specific attribute names.
    """
    icon_row = Adw.ActionRow(title=_("Icon"))

    preview = Gtk.Image.new_from_icon_name(_DEFAULT_ICON)
    preview.set_pixel_size(24)
    icon_row.add_prefix(preview)

    picker_btn = Gtk.Button(
        icon_name="view-grid-symbolic",
        css_classes=[BaseDialog.CSS_CLASS_FLAT],
        valign=Gtk.Align.CENTER,
    )
    get_tooltip_helper().add_tooltip(picker_btn, _("Choose icon"))
    picker_btn.connect(
        "clicked", lambda _btn: dialog._on_pick_icon_clicked(mode)
    )
    icon_row.add_suffix(picker_btn)

    entry = Gtk.Entry(
        placeholder_text=_DEFAULT_ICON,
        width_chars=20,
        valign=Gtk.Align.CENTER,
    )
    a11y_label(entry, _("Icon name"))
    entry.set_text(_DEFAULT_ICON)
    entry.connect(
        "changed", lambda e: dialog._on_icon_entry_changed(e, mode)
    )
    icon_row.add_suffix(entry)

    return icon_row, preview, entry


def _build_basic_info_group(
    dialog: "CommandEditorDialog",
    *,
    mode: str,
) -> Tuple[Adw.PreferencesGroup, Adw.EntryRow, Adw.EntryRow, Adw.ComboRow]:
    """Build the "Basic Information" group.

    Returns ``(group, name_row, description_row, display_mode_row)``;
    the caller stashes them under the mode-specific attribute names
    (``simple_name_row`` / ``form_name_row`` etc.).
    """
    group = Adw.PreferencesGroup(title=_("Basic Information"))

    name_row = Adw.EntryRow(title=_("Name"))
    group.add(name_row)

    description_row = Adw.EntryRow(title=_("Description"))
    group.add(description_row)

    icon_row, preview, entry = _build_icon_row(dialog, mode=mode)
    # Publish preview + entry on the dialog with mode-specific names.
    setattr(dialog, f"{mode}_icon_preview", preview)
    setattr(dialog, f"{mode}_icon_entry", entry)
    group.add(icon_row)

    display_mode_row = Adw.ComboRow(title=_("Display Mode"))
    display_modes = Gtk.StringList()
    for label in (_("Icon and Text"), _("Icon Only"), _("Text Only")):
        display_modes.append(label)
    display_mode_row.set_model(display_modes)
    group.add(display_mode_row)

    return group, name_row, description_row, display_mode_row


def build_step_simple(dialog: "CommandEditorDialog") -> None:
    """Build Step 1 (Simple Command).

    Populates: ``simple_name_row``, ``simple_description_row``,
    ``simple_icon_preview``, ``simple_icon_entry``,
    ``simple_display_mode_row``, ``simple_execution_mode_row`` and
    ``simple_command_textview``. Adds the step to the wizard stack
    under the name ``"simple"``.
    """
    step = _outer_step_box()

    basic_group, name_row, description_row, display_mode_row = (
        _build_basic_info_group(dialog, mode="simple")
    )
    dialog.simple_name_row = name_row
    dialog.simple_description_row = description_row
    dialog.simple_display_mode_row = display_mode_row

    # Execution mode (simple-only): Insert Only vs Insert and Execute.
    execution_mode_row = Adw.ComboRow(title=_("Execution Mode"))
    execution_modes = Gtk.StringList()
    for label in (_("Insert Only"), _("Insert and Execute")):
        execution_modes.append(label)
    execution_mode_row.set_model(execution_modes)
    execution_mode_row.set_selected(0)  # Insert Only by default
    basic_group.add(execution_mode_row)
    dialog.simple_execution_mode_row = execution_mode_row

    step.append(basic_group)

    # Command body.
    command_group = Adw.PreferencesGroup(title=_("Command"))
    help_label = Gtk.Label(
        label=_("Enter the bash command to execute:"),
        xalign=0.0,
        css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL, "caption"],
        margin_start=4,
    )
    command_group.add(help_label)

    command_frame = Gtk.Frame(css_classes=["view"])
    command_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        min_content_height=80,
        max_content_height=150,
    )
    dialog.simple_command_textview = BashTextView()
    command_scroll.set_child(dialog.simple_command_textview)
    command_frame.set_child(command_scroll)
    command_group.add(command_frame)

    step.append(command_group)
    dialog.wizard_stack.add_named(step, "simple")


def build_step_form_info(dialog: "CommandEditorDialog") -> None:
    """Build Step 2a (Form Command — basic info only).

    Populates the ``form_*`` counterparts of the Basic Information
    fields. No execution-mode row; the form path always shows a dialog
    before running. Adds the step to the wizard stack under the name
    ``"form_info"``.
    """
    step = _outer_step_box()

    basic_group, name_row, description_row, display_mode_row = (
        _build_basic_info_group(dialog, mode="form")
    )
    dialog.form_name_row = name_row
    dialog.form_description_row = description_row
    dialog.form_display_mode_row = display_mode_row

    step.append(basic_group)
    dialog.wizard_stack.add_named(step, "form_info")
