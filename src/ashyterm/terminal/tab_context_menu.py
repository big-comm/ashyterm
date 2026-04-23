# ashyterm/terminal/tab_context_menu.py
"""Right-click context menu for tab widgets.

The menu offers: Move/Duplicate/Detach tab, a Tab Color section (with
an optional Clear), and a Group section (New / Remove / Add-to-X).
It's ~100 lines of ``Gio.Menu`` + ``SimpleAction`` scaffolding, so
extracting keeps ``tabs.py`` focused on widget orchestration.

The builder takes the owning ``TabManager`` — it needs the group
manager, the pages map, and half a dozen action callbacks — and
returns a ready-to-popup ``Gtk.PopoverMenu``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..helpers import create_themed_popover_menu
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .tabs import TabManager


def build_menu_model(
    tab_widget: Gtk.Box,
    *,
    manager: "TabManager",
) -> Gio.Menu:
    """Compose the ``Gio.Menu`` model for a tab right-click.

    The model reflects current state (whether the tab has a custom
    color, whether it's already in a group, which other groups exist)
    and references action ids that :func:`wire_menu_actions` below
    attaches to the popover.
    """
    menu = Gio.Menu()
    menu.append(_("Move Tab"), "win.move-tab")
    menu.append(_("Duplicate Tab"), "win.duplicate-tab")
    menu.append(_("Detach Tab"), "win.detach-tab")

    color_section = Gio.Menu()
    color_section.append(_("Tab Color…"), "win.tab-color")
    session = getattr(tab_widget, "session_item", None)
    if session and session.tab_color:
        color_section.append(_("Clear Tab Color"), "win.clear-tab-color")
    menu.append_section(None, color_section)

    # Group section
    group_section = Gio.Menu()
    tab_id = manager.get_tab_id(tab_widget)
    current_group = manager.group_manager.get_group_for_tab(tab_id)
    if current_group:
        group_section.append(_("Remove from Group"), "win.remove-from-group")
    else:
        group_section.append(_("New Group from Tab"), "win.new-group-from-tab")
    if manager.group_manager.has_groups():
        for group in manager.group_manager.groups:
            if group != current_group:
                group_section.append(
                    _("Add to '{}'").format(group.name),
                    f"win.add-to-group-{group.id}",
                )
    menu.append_section(None, group_section)
    return menu


def _wire_menu_actions(
    popover: Gtk.PopoverMenu,
    tab_widget: Gtk.Box,
    page: Adw.ViewStackPage,
    *,
    manager: "TabManager",
) -> None:
    """Attach the ``win.*`` action group that the menu model references."""
    action_group = Gio.SimpleActionGroup()

    move_action = Gio.SimpleAction.new("move-tab", None)
    move_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget: GLib.idle_add(
            manager._start_tab_move, tab
        ),
    )
    action_group.add_action(move_action)

    duplicate_action = Gio.SimpleAction.new("duplicate-tab", None)
    duplicate_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget: manager._duplicate_tab(tab),
    )
    action_group.add_action(duplicate_action)

    detach_action = Gio.SimpleAction.new("detach-tab", None)
    detach_action.connect(
        "activate", lambda _a, _p, pg=page: manager._request_detach_tab(pg)
    )
    action_group.add_action(detach_action)

    color_action = Gio.SimpleAction.new("tab-color", None)
    color_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget, pop=popover: manager._pick_tab_color(
            tab, pop
        ),
    )
    action_group.add_action(color_action)

    clear_color_action = Gio.SimpleAction.new("clear-tab-color", None)
    clear_color_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget: manager._clear_tab_color(tab),
    )
    action_group.add_action(clear_color_action)

    new_group_action = Gio.SimpleAction.new("new-group-from-tab", None)
    new_group_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget: manager.create_group_from_tabs([tab]),
    )
    action_group.add_action(new_group_action)

    remove_group_action = Gio.SimpleAction.new("remove-from-group", None)
    remove_group_action.connect(
        "activate",
        lambda _a, _p, tab=tab_widget: manager._remove_tab_from_group_action(tab),
    )
    action_group.add_action(remove_group_action)

    # Dynamic "add to group X" actions — one per non-member group.
    for group in manager.group_manager.groups:
        add_action = Gio.SimpleAction.new(f"add-to-group-{group.id}", None)
        add_action.connect(
            "activate",
            lambda _a, _p, g_id=group.id, tab=tab_widget: (
                manager._add_tab_to_group_action(tab, g_id)
            ),
        )
        action_group.add_action(add_action)

    popover.insert_action_group("win", action_group)


def show_tab_context_menu(
    manager: "TabManager",
    tab_widget: Gtk.Box,
    x: float,
    y: float,
) -> None:
    """Build, wire and pop up the right-click menu for ``tab_widget``.

    If the tab hasn't been registered in ``manager.pages`` (can happen
    transiently during tab creation), the popover is still shown but
    with no action group — the menu entries become no-ops, matching
    the pre-extraction behavior.
    """
    menu = build_menu_model(tab_widget, manager=manager)
    popover = create_themed_popover_menu(menu, tab_widget)

    page = manager.pages.get(tab_widget)
    if page:
        _wire_menu_actions(popover, tab_widget, page, manager=manager)

    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    popover.set_pointing_to(rect)
    popover.popup()
