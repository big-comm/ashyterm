# ashyterm/ui/command_toolbar.py
"""Command toolbar (pinned commands row under the header).

The toolbar is a flat list of buttons, one per pinned user command.
Each button respects a per-command ``toolbar_display_mode`` pref
(icon-only / text-only / icon-and-text), and a right-click popover
lets the user change the mode or unpin. The module owns all of that
— the window builder just hosts the ``Gtk.Box`` and routes clicks
back to the window for actual command execution.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk, Pango

from ..data.command_manager_models import (
    CommandButton,
    get_command_button_manager,
)
from ..helpers import clear_children
from ..utils.accessibility import set_label as a11y_label
from ..utils.translation_utils import _


DEFAULT_DISPLAY_MODE = "icon_and_text"
_ICON_MODES = frozenset({"icon_only", "icon_and_text"})
_TEXT_MODES = frozenset({"text_only", "icon_and_text"})


def populate_command_toolbar(
    toolbar: Gtk.Box,
    *,
    parent_handle: Gtk.Widget | None,
    logger,
    on_click: Callable[[Gtk.Button], None],
    on_right_click: Callable[
        [Gtk.GestureClick, int, float, float, CommandButton, Gtk.Button], None
    ],
    tooltip_helper,
) -> None:
    """Replace ``toolbar`` contents with one button per pinned command.

    Hides the outer ``parent_handle`` when no command is pinned — the
    bar must not reserve vertical space in that state.
    """
    clear_children(toolbar)

    manager = get_command_button_manager()
    pinned = manager.get_pinned_commands()

    if not pinned:
        toolbar.set_visible(False)
        if parent_handle is not None:
            parent_handle.set_visible(False)
        return

    logger.debug(
        f"Command toolbar: showing {len(pinned)} pinned commands: "
        f"{[c.id for c in pinned]}"
    )
    toolbar.set_visible(True)
    if parent_handle is not None:
        parent_handle.set_visible(True)

    for cmd in pinned:
        btn = create_toolbar_command_button(
            cmd,
            on_click=on_click,
            on_right_click=on_right_click,
            tooltip_helper=tooltip_helper,
        )
        toolbar.append(btn)


def create_toolbar_command_button(
    command: CommandButton,
    *,
    on_click: Callable[[Gtk.Button], None],
    on_right_click: Callable[
        [Gtk.GestureClick, int, float, float, CommandButton, Gtk.Button], None
    ],
    tooltip_helper,
) -> Gtk.Button:
    """Build a single toolbar button for ``command``.

    The command is attached to the button as ``_command`` so the click
    handler can retrieve it without a closure (keeps the button cheap
    to recycle on toolbar rebuild).
    """
    btn = Gtk.Button(css_classes=["flat", "toolbar-command-button"])
    content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

    manager = get_command_button_manager()
    toolbar_mode = manager.get_command_pref(
        command.id, "toolbar_display_mode", DEFAULT_DISPLAY_MODE
    )

    if toolbar_mode in _ICON_MODES:
        icon = Gtk.Image.new_from_icon_name(command.icon_name)
        icon.set_icon_size(Gtk.IconSize.NORMAL)
        content_box.append(icon)

    if toolbar_mode in _TEXT_MODES:
        label = Gtk.Label(label=command.name)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(15)
        content_box.append(label)

    btn.set_child(content_box)
    a11y_label(btn, command.name)
    tooltip_helper.add_tooltip(btn, command.description)

    btn._command = command
    btn.connect("clicked", on_click)

    gesture = Gtk.GestureClick.new()
    gesture.set_button(3)  # Right click
    gesture.connect("pressed", on_right_click, command, btn)
    btn.add_controller(gesture)

    return btn


def build_right_click_popover(
    *,
    command: CommandButton,
    anchor: Gtk.Widget,
    on_set_mode: Callable[[CommandButton, str], None],
    on_unpin: Callable[[CommandButton], None],
) -> Gtk.PopoverMenu:
    """Build the right-click menu for a pinned toolbar button.

    Wires the "Icon/Text/Icon+Text" and "Unpin" actions to the callbacks
    and returns the popover ready to ``popup()``.
    """
    menu = Gio.Menu()
    display_section = Gio.Menu()
    display_section.append(_("Icon Only"), "toolbar.display_icon")
    display_section.append(_("Text Only"), "toolbar.display_text")
    display_section.append(_("Icon and Text"), "toolbar.display_both")
    menu.append_section(_("Display"), display_section)
    menu.append(_("Unpin from Toolbar"), "toolbar.unpin")

    popover = Gtk.PopoverMenu.new_from_model(menu)
    popover.add_css_class("ashyterm-popover")
    popover.set_parent(anchor)

    action_group = Gio.SimpleActionGroup()

    def _wire(name: str, payload) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_: payload())
        action_group.add_action(action)

    _wire("display_icon", lambda: on_set_mode(command, "icon_only"))
    _wire("display_text", lambda: on_set_mode(command, "text_only"))
    _wire("display_both", lambda: on_set_mode(command, "icon_and_text"))
    _wire("unpin", lambda: on_unpin(command))

    popover.insert_action_group("toolbar", action_group)
    return popover


def set_toolbar_display_mode(
    command: CommandButton,
    mode: str,
    *,
    refresh: Callable[[], None],
) -> None:
    """Persist ``mode`` for ``command`` and ask the host to rebuild."""
    manager = get_command_button_manager()
    manager.set_command_pref(command.id, "toolbar_display_mode", mode)
    refresh()


def unpin_toolbar_command(
    command: CommandButton,
    *,
    refresh: Callable[[], None],
) -> None:
    """Remove ``command`` from the pinned list and ask the host to rebuild."""
    manager = get_command_button_manager()
    manager.unpin_command(command.id)
    refresh()
