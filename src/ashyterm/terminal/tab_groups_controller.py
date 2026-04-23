# ashyterm/terminal/tab_groups_controller.py
"""UI controller for tab-group workflows in the tab bar.

The pure data model (groups, membership, colors, serialization) lives in
``tab_groups.py``. This module owns the GTK widgets, event handlers, and
move-mode state that together produce the Chrome-style tab-group UX.

The controller holds a reference to the owning ``TabManager`` for access
to ``tabs``, ``active_tab``, ``tab_bar_box``, ``view_stack`` and the
rebuild/close hooks. All membership mutations go through
``manager.group_manager`` so the data layer stays authoritative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..helpers import create_themed_popover_menu
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .tab_groups import TabGroup

if TYPE_CHECKING:
    from .tabs import TabManager


from ..utils.color_luminance import contrasting_text_for_hex  # noqa: E402,F401


class TabGroupsController:
    """Owns the tab-group UI state and workflow attached to one TabManager."""

    def __init__(self, manager: "TabManager") -> None:
        self.manager = manager
        self.logger = get_logger("ashyterm.tabs.groups")
        self._chip_providers: Dict[str, Gtk.CssProvider] = {}
        self._border_providers: Dict[str, Gtk.CssProvider] = {}
        self._group_being_moved: Optional[TabGroup] = None

    # ── move-mode inspection ─────────────────────────────────

    def is_moving_group(self) -> bool:
        return self._group_being_moved is not None

    def moving_group_tab_ids(self) -> List[str]:
        return list(self._group_being_moved.tab_ids) if self._group_being_moved else []

    def is_tab_in_moving_group(self, tab_id: str) -> bool:
        return (
            self._group_being_moved is not None
            and tab_id in self._group_being_moved.tab_ids
        )

    # ── group-list helpers ───────────────────────────────────

    def next_group_name(self) -> str:
        """Generate a unique sequential name: Group A, B, ..., Z, 1A, 1B, ..."""
        existing_names = {g.name for g in self.manager.group_manager.groups}
        prefix_num = 0
        while True:
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                prefix = f"{prefix_num}" if prefix_num > 0 else ""
                candidate = f"{_('Group')} {prefix}{letter}"
                if candidate not in existing_names:
                    return candidate
            prefix_num += 1

    def ensure_contiguous(self, group_id: str) -> None:
        """Move all tabs of the group together in ``manager.tabs``."""
        group = self.manager.group_manager.get_group(group_id)
        if not group or len(group.tab_ids) < 2:
            return
        group_tab_id_set = set(group.tab_ids)
        first_idx = next(
            (
                i
                for i, t in enumerate(self.manager.tabs)
                if self.manager.get_tab_id(t) in group_tab_id_set
            ),
            None,
        )
        if first_idx is None:
            return
        group_tabs = [
            t
            for t in self.manager.tabs
            if self.manager.get_tab_id(t) in group_tab_id_set
        ]
        for t in group_tabs:
            self.manager.tabs.remove(t)
        insert_at = min(first_idx, len(self.manager.tabs))
        for j, t in enumerate(group_tabs):
            self.manager.tabs.insert(insert_at + j, t)

    def focus_first_visible_outside(self, excluded_tab_ids: Iterable[str]) -> None:
        """Activate the first tab not in ``excluded_tab_ids``."""
        excluded = set(excluded_tab_ids)
        for candidate in self.manager.tabs:
            if self.manager.get_tab_id(candidate) not in excluded:
                self.manager.set_active_tab(candidate)
                return

    # ── group creation / membership ──────────────────────────

    def create_group_from_tabs(
        self, tab_widgets: List[Gtk.Box], name: str = ""
    ) -> None:
        tab_ids = [self.manager.get_tab_id(t) for t in tab_widgets]
        if not name:
            name = self.next_group_name()
        group = self.manager.group_manager.create_group(name, initial_tab_ids=tab_ids)
        self.ensure_contiguous(group.id)
        self.manager._rebuild_tab_bar_order()
        self.logger.info(f"Created group '{group.name}' with {len(tab_ids)} tab(s)")

    def create_group_from_active_tab(self) -> None:
        if self.manager.active_tab:
            self.create_group_from_tabs([self.manager.active_tab])

    def ungroup_active_tab(self) -> None:
        active = self.manager.active_tab
        if not active:
            return
        tab_id = self.manager.get_tab_id(active)
        self.manager.group_manager.remove_tab_from_group(tab_id)
        active.remove_css_class("in-group")
        self.manager._rebuild_tab_bar_order()

    def remove_tab_from_group_action(self, tab_widget: Gtk.Box) -> None:
        """Context-menu handler: remove tab from its current group."""
        tab_id = self.manager.get_tab_id(tab_widget)
        self.manager.group_manager.remove_tab_from_group(tab_id)
        tab_widget.remove_css_class("in-group")
        self.manager._rebuild_tab_bar_order()

    def add_tab_to_group_action(self, tab_widget: Gtk.Box, group_id: str) -> None:
        """Context-menu handler: add tab to an existing group."""
        tab_id = self.manager.get_tab_id(tab_widget)
        self.manager.group_manager.add_tab_to_group(group_id, tab_id)
        self.ensure_contiguous(group_id)
        group = self.manager.group_manager.get_group(group_id)
        if group and group.is_collapsed:
            self.manager.group_manager.toggle_collapsed(group_id)
        self.manager._rebuild_tab_bar_order()

    # ── chip / CSS providers ─────────────────────────────────

    def get_chip_provider(self, color: str) -> Gtk.CssProvider:
        cached = self._chip_providers.get(color)
        if cached is not None:
            return cached
        text_color = contrasting_text_for_hex(color)
        provider = Gtk.CssProvider()
        css = (
            ".tab-group-chip {"
            f" background-color: {color};"
            f" color: {text_color};"
            " }"
        )
        provider.load_from_string(css)
        self._chip_providers[color] = provider
        return provider

    def get_border_provider(self, color: str) -> Gtk.CssProvider:
        cached = self._border_providers.get(color)
        if cached is not None:
            return cached
        provider = Gtk.CssProvider()
        css = f".custom-tab-button.in-group {{ border-bottom-color: {color}; }}"
        provider.load_from_string(css)
        self._border_providers[color] = provider
        return provider

    def apply_border_color(self, tab_widget: Gtk.Box, color: str) -> None:
        style_context = tab_widget.get_style_context()
        previous = getattr(tab_widget, "_group_border_provider", None)
        provider = self.get_border_provider(color)
        if previous is provider:
            return
        if previous is not None:
            style_context.remove_provider(previous)
        style_context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        tab_widget._group_border_provider = provider

    def build_chip(self, group: TabGroup) -> Gtk.Box:
        """Build the Chrome-style chip widget for a group header."""
        chip = Gtk.Box(spacing=0)
        chip.add_css_class("tab-group-chip")
        if group.is_collapsed:
            chip.add_css_class("collapsed")
        if not group.name:
            chip.add_css_class("unnamed")

        chip.get_style_context().add_provider(
            self.get_chip_provider(group.color),
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

        tab_count = len(group.tab_ids)
        display_name = (
            f"{group.name} ({tab_count})" if group.name else f"({tab_count})"
        )
        label = Gtk.Label(label=display_name)
        label.add_css_class("group-name-label")
        chip.append(label)
        chip.set_tooltip_text(display_name)

        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_chip_clicked, group)
        chip.add_controller(click)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_chip_right_click, chip, group)
        chip.add_controller(right_click)

        chip._group_id = group.id
        return chip

    # ── click handlers ───────────────────────────────────────

    def _on_chip_clicked(self, _gesture, _n, _x, _y, group: TabGroup) -> None:
        """Toggle collapsed state or finalize a group-move."""
        if self._group_being_moved is not None:
            if group.id != self._group_being_moved.id:
                groups = self.manager.group_manager.groups
                target_idx = next(
                    (i for i, g in enumerate(groups) if g.id == group.id), -1
                )
                if target_idx >= 0:
                    self.manager.group_manager.move_group(
                        self._group_being_moved.id, target_idx
                    )
                    self.manager._rebuild_tab_bar_order()
            self.cancel_move()
            return
        if self.manager._tab_being_moved is not None:
            self.manager._cancel_tab_move()
            return
        will_be_collapsed = self.manager.group_manager.toggle_collapsed(group.id)
        if will_be_collapsed and self.manager.active_tab is not None:
            active_tab_id = self.manager.get_tab_id(self.manager.active_tab)
            if active_tab_id in group.tab_ids:
                self.focus_first_visible_outside(group.tab_ids)
        self.manager._rebuild_tab_bar_order()

    def _on_chip_right_click(self, _gesture, _n, x, y, chip, group: TabGroup) -> None:
        """Show context menu on a group chip."""
        menu = Gio.Menu()
        menu.append(_("New Tab in Group"), "win.new-tab-in-group")

        edit_section = Gio.Menu()
        edit_section.append(_("Rename Group"), "win.rename-group")
        edit_section.append(_("Group Color…"), "win.group-color")
        edit_section.append(_("Move Group"), "win.move-group")
        menu.append_section(None, edit_section)

        action_section = Gio.Menu()
        action_section.append(_("Ungroup"), "win.ungroup-all")
        action_section.append(_("Close Group"), "win.close-group")
        menu.append_section(None, action_section)

        popover = create_themed_popover_menu(menu, chip)

        action_group = Gio.SimpleActionGroup()

        new_tab_action = Gio.SimpleAction.new("new-tab-in-group", None)
        new_tab_action.connect(
            "activate", lambda _a, _p, g=group: self.new_tab_in(g)
        )
        action_group.add_action(new_tab_action)

        rename_action = Gio.SimpleAction.new("rename-group", None)
        rename_action.connect(
            "activate", lambda _a, _p, g=group: self.rename_dialog(g)
        )
        action_group.add_action(rename_action)

        color_action = Gio.SimpleAction.new("group-color", None)
        color_action.connect(
            "activate",
            lambda _a, _p, g=group, pop=popover: self.pick_color(g, pop),
        )
        action_group.add_action(color_action)

        move_action = Gio.SimpleAction.new("move-group", None)
        move_action.connect(
            "activate",
            lambda _a, _p, g=group: GLib.idle_add(self.start_move, g),
        )
        action_group.add_action(move_action)

        ungroup_action = Gio.SimpleAction.new("ungroup-all", None)
        ungroup_action.connect(
            "activate", lambda _a, _p, g=group: self.ungroup_all(g)
        )
        action_group.add_action(ungroup_action)

        close_action = Gio.SimpleAction.new("close-group", None)
        close_action.connect(
            "activate", lambda _a, _p, g=group: self.close_group(g)
        )
        action_group.add_action(close_action)

        popover.insert_action_group("win", action_group)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        popover.set_pointing_to(rect)
        popover.popup()

    # ── group-move lifecycle ─────────────────────────────────

    def start_move(self, group: TabGroup) -> None:
        """Enter group-move mode: chip + member tabs become ghost targets."""
        self._group_being_moved = group
        self.manager.tab_bar_box.add_css_class("tab-bar-move-mode")

        child = self.manager.tab_bar_box.get_first_child()
        while child:
            if hasattr(child, "_group_id") and child._group_id == group.id:
                child.add_css_class("tab-moving")
            child = child.get_next_sibling()
        for tab_id in group.tab_ids:
            tab = self.manager._get_tab_by_id(tab_id)
            if tab:
                tab.add_css_class("tab-moving")
        for tab in self.manager.tabs:
            close_btn = self.manager._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_opacity(0)
                close_btn.set_sensitive(False)
        self.logger.info(f"Group move started for: {group.name}")

    def cancel_move(self) -> None:
        """Leave group-move mode and restore visuals."""
        if self._group_being_moved is None:
            return
        group = self._group_being_moved
        self.manager.tab_bar_box.remove_css_class("tab-bar-move-mode")
        self.manager._clear_tab_drop_highlights()
        child = self.manager.tab_bar_box.get_first_child()
        while child:
            if hasattr(child, "_group_id") and child._group_id == group.id:
                child.remove_css_class("tab-moving")
            child = child.get_next_sibling()
        for tab_id in group.tab_ids:
            tab = self.manager._get_tab_by_id(tab_id)
            if tab:
                tab.remove_css_class("tab-moving")
        for tab in self.manager.tabs:
            close_btn = self.manager._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_opacity(1)
                close_btn.set_sensitive(True)
        self._group_being_moved = None

    def perform_move(self, target_tab: Gtk.Box, side: str) -> None:
        """Move all member tabs together in ``manager.tabs`` relative to target."""
        group = self._group_being_moved
        if not group:
            return

        target_tab_id = self.manager.get_tab_id(target_tab)
        if target_tab_id in group.tab_ids:
            return

        group_tab_id_set = set(group.tab_ids)
        group_tabs = [
            t
            for t in self.manager.tabs
            if self.manager.get_tab_id(t) in group_tab_id_set
        ]
        for t in group_tabs:
            self.manager.tabs.remove(t)

        target_idx = self.manager.tabs.index(target_tab)
        insert_idx = target_idx if side == "left" else target_idx + 1

        for i, t in enumerate(group_tabs):
            self.manager.tabs.insert(insert_idx + i, t)

        self.manager._rebuild_tab_bar_order()
        self.logger.info(f"Group '{group.name}' moved to position {insert_idx}")

    # ── menu actions (invoked from the chip popover) ─────────

    def new_tab_in(self, group: TabGroup) -> None:
        """Create a new local tab and add it to the group."""
        terminal = self.manager.create_local_tab()
        if terminal and self.manager.tabs:
            new_tab = self.manager.tabs[-1]
            tab_id = self.manager.get_tab_id(new_tab)
            self.manager.group_manager.add_tab_to_group(group.id, tab_id)
            self.ensure_contiguous(group.id)
            self.manager._rebuild_tab_bar_order()

    def pick_color(self, group: TabGroup, popover: Gtk.Popover) -> None:
        popover.popdown()
        dialog = Gtk.ColorDialog(title=_("Group Color"))
        dialog.choose_rgba(
            self.manager.view_stack.get_root(),
            None,
            None,
            self._on_color_chosen,
            group,
        )

    def _on_color_chosen(self, dialog, result, group: TabGroup) -> None:
        try:
            color = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return
        r, g, b = int(color.red * 255), int(color.green * 255), int(color.blue * 255)
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self.manager.group_manager.set_group_color(group.id, hex_color)
        self.manager._rebuild_tab_bar_order()

    def close_group(self, group: TabGroup) -> None:
        """Close all tabs in a group (Chrome 'Close group' behavior)."""
        tabs_to_close = []
        for tab_id in list(group.tab_ids):
            tab = self.manager._get_tab_by_id(tab_id)
            if tab:
                tabs_to_close.append(tab)
        self.manager.group_manager.delete_group(group.id)
        for tab in tabs_to_close:
            self.manager._on_tab_close_button_clicked(None, tab)

    def rename_dialog(self, group: TabGroup) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Rename Group"),
            body=_("Enter a new name for the group:"),
            close_response="cancel",
        )
        entry = Gtk.Entry(text=group.name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.connect(
            "response",
            lambda d, r, e=entry, g=group: self._on_rename_response(r, e, g),
        )
        dialog.present(self.manager.view_stack.get_root())

    def _on_rename_response(
        self, response: str, entry: Gtk.Entry, group: TabGroup
    ) -> None:
        if response != "rename":
            return
        new_name = entry.get_text().strip()
        self.manager.group_manager.rename_group(group.id, new_name)
        self.manager._rebuild_tab_bar_order()

    def ungroup_all(self, group: TabGroup) -> None:
        """Remove all tabs from a group (tabs remain open)."""
        for tab_id in list(group.tab_ids):
            tab = self.manager._get_tab_by_id(tab_id)
            if tab:
                tab.remove_css_class("in-group")
        self.manager.group_manager.delete_group(group.id)
        self.manager._rebuild_tab_bar_order()
