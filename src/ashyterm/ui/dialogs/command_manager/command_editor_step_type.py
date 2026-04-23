# ashyterm/ui/dialogs/command_manager/command_editor_step_type.py
"""Step 0 of the command editor wizard: choose command type.

The user picks between a "simple" one-liner command and a "form"
command that renders a configuration dialog before running. The step
is pure UI — two cards with icon + title + description — so it lives
here instead of inflating the editor dialog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ....utils.accessibility import set_label as a11y_label
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog

if TYPE_CHECKING:
    from .command_editor_dialog import CommandEditorDialog


def _build_card(
    *,
    label: str,
    icon_name: str,
    title_text: str,
    description: str,
    on_clicked: Callable[[], None],
) -> Gtk.Button:
    """Build one of the two "simple/form" cards.

    Each card is a ``Gtk.Button`` styled as a card; the click handler
    is a bare callable so the wizard can swap in the right transition.
    """
    button = Gtk.Button(css_classes=["card", "flat"])
    a11y_label(button, label)

    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_top=16,
        margin_bottom=16,
        margin_start=16,
        margin_end=16,
    )

    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(48)
    box.append(icon)

    title_label = Gtk.Label(label=title_text, css_classes=["title-3"])
    box.append(title_label)

    desc_label = Gtk.Label(
        label=description,
        css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL],
        wrap=True,
    )
    box.append(desc_label)

    button.set_child(box)
    button.connect("clicked", lambda _btn: on_clicked())
    return button


def build_step_type_choice(dialog: "CommandEditorDialog") -> None:
    """Attach the type-choice step to ``dialog.wizard_stack``.

    The step is registered under the name ``"type_choice"``; the
    initial visible child of the stack is set by the dialog after all
    four steps have been built.
    """
    step = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=24,
        margin_top=32,
        margin_bottom=32,
        margin_start=24,
        margin_end=24,
        valign=Gtk.Align.CENTER,
    )

    title = Gtk.Label(
        label=_("What type of command do you want to create?"),
        css_classes=["title-2"],
    )
    step.append(title)

    buttons_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        margin_top=16,
    )

    simple_btn = _build_card(
        label=_("Simple Command"),
        icon_name="utilities-terminal-symbolic",
        title_text=_("Simple Command"),
        description=_("A button that runs a command directly"),
        on_clicked=lambda: dialog._select_command_type("simple"),
    )
    buttons_box.append(simple_btn)

    form_btn = _build_card(
        label=_("Command with Form"),
        icon_name="view-paged-symbolic",
        title_text=_("Command with Form"),
        description=_("A button that shows a form to configure the command"),
        on_clicked=lambda: dialog._select_command_type("form"),
    )
    buttons_box.append(form_btn)

    step.append(buttons_box)
    dialog.wizard_stack.add_named(step, "type_choice")
