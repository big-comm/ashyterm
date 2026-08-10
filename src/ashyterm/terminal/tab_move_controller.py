# ashyterm/terminal/tab_move_controller.py
"""Tab-move (drag-reorder) state for the tab bar.

Move mode can be entered two ways: by picking "Move Tab" from the context
menu and then clicking the drop target, or by pressing a tab and dragging
it. Both funnel through this module, which owns the mode's state — the
moving tab, the current drop target, the left/right drop side, and the CSS
tags that visualize them — plus the list-mutation logic that commits a
drop. The sibling ``tab_groups_controller`` has the same job for groups;
both collaborate with ``TabManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .tabs import TabManager


# Pointer travel, in pixels, before a press turns into a drag. Below this a
# press is still a plain click that activates the tab.
DRAG_START_THRESHOLD = 8.0


def resolve_drop_slot(
    bounds: Sequence[Tuple[float, float]], x: float
) -> Optional[Tuple[int, str]]:
    """Find which tab ``x`` is over and which half of it, or None if outside.

    ``bounds`` is one ``(left, width)`` pair per tab, in tab-bar coordinates
    and visual order. Dragging past either end clamps to the outermost tab so
    the drop still lands where the pointer is heading, which is what makes
    "drag to the far right to move it last" work.
    """
    if not bounds:
        return None

    for index, (left, width) in enumerate(bounds):
        if x < left + width:
            if x < left:
                # Left of this tab and of every earlier one: drop before it.
                return index, "left"
            return index, "left" if x < left + width / 2 else "right"

    return len(bounds) - 1, "right"


def tab_bounds_in_bar(manager: "TabManager") -> List[Tuple[float, float]]:
    """``(left, width)`` for each tab, measured against the tab bar."""
    measured: List[Tuple[float, float]] = []
    for tab in manager.tabs:
        found, rect = tab.compute_bounds(manager.tab_bar_box)
        if not found:
            return []
        measured.append((rect.origin.x, rect.size.width))
    return measured


class TabMoveController:
    """Owns the tab-move mode state attached to one TabManager."""

    def __init__(self, manager: "TabManager") -> None:
        self.manager = manager
        self.logger = get_logger("ashyterm.tabs.move")
        self._moving: Optional[Gtk.Box] = None
        self._drop_target: Optional[Gtk.Box] = None
        self._drop_side: str = "left"  # "left" | "right"

    # ── state inspection ─────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._moving is not None

    @property
    def moving_tab(self) -> Optional[Gtk.Box]:
        return self._moving

    @property
    def drop_target(self) -> Optional[Gtk.Box]:
        return self._drop_target

    @property
    def drop_side(self) -> str:
        return self._drop_side

    # ── highlight management ─────────────────────────────────

    def update_highlight(self, target_tab: Gtk.Box, side: str) -> None:
        """Clear any old drop highlight and apply a new one on ``target_tab``."""
        self.clear_highlights()
        if target_tab and side:
            self._drop_target = target_tab
            self._drop_side = side
            if side == "left":
                target_tab.add_css_class("tab-drop-left")
            else:
                target_tab.add_css_class("tab-drop-right")

    def clear_highlights(self) -> None:
        """Remove drop-target highlights from every tab."""
        self._drop_target = None
        for tab in self.manager.tabs:
            tab.remove_css_class("tab-drop-target")
            tab.remove_css_class("tab-drop-left")
            tab.remove_css_class("tab-drop-right")

    # ── move-mode lifecycle ──────────────────────────────────

    def start(self, tab_widget: Gtk.Box) -> None:
        """Enter move mode. No-op when fewer than two tabs exist."""
        if len(self.manager.tabs) < 2:
            self.logger.debug("Cannot move tab: only one tab exists.")
            return

        self._moving = tab_widget
        tab_widget.add_css_class("tab-moving")
        self.manager.tab_bar_box.add_css_class("tab-bar-move-mode")

        # Make close buttons invisible but keep layout space so the tab
        # bar doesn't jitter when the user hovers between drop targets.
        for tab in self.manager.tabs:
            close_btn = self.manager._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_opacity(0)
                close_btn.set_sensitive(False)

        self.logger.info(
            f"Tab move started for: {tab_widget.label_widget.get_text()}"
        )

    def cancel(self) -> None:
        """Exit move mode without committing, restoring visuals."""
        if self._moving is None:
            return
        self._moving.remove_css_class("tab-moving")
        self.manager.tab_bar_box.remove_css_class("tab-bar-move-mode")
        self.clear_highlights()

        for tab in self.manager.tabs:
            close_btn = self.manager._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_opacity(1)
                close_btn.set_sensitive(True)

        self.logger.debug("Tab move cancelled.")
        self._moving = None

    def set_drop_target(self, tab_widget: Gtk.Box, side: str) -> None:
        """Set the drop target + side without touching CSS.

        Used by ``_on_tab_clicked`` just before calling :meth:`perform`.
        """
        self._drop_target = tab_widget
        self._drop_side = side

    def perform(self) -> None:
        """Commit the pending move: reorder ``manager.tabs`` + rebuild."""
        if not self._moving or not self._drop_target:
            return

        moving_tab = self._moving
        target_tab = self._drop_target
        side = self._drop_side

        if moving_tab == target_tab:
            return

        moving_idx = self.manager.tabs.index(moving_tab)
        target_idx = self.manager.tabs.index(target_tab)

        # Left/right drop side maps to "insert before" / "insert after".
        new_idx = target_idx if side == "left" else target_idx + 1

        # Shifting a tab from before the target to after it means the
        # computed target index is one slot too far right.
        if moving_idx < new_idx:
            new_idx -= 1

        # Leaving the current group is implicit on any drop — the user
        # can auto-rejoin by landing adjacent to a grouped tab below.
        tab_id = self.manager.get_tab_id(moving_tab)
        self.manager.group_manager.remove_tab_from_group(tab_id)
        moving_tab.remove_css_class("in-group")

        if moving_idx != new_idx:
            self.manager.tabs.remove(moving_tab)
            self.manager.tabs.insert(new_idx, moving_tab)

        # Drop-adjacent to a grouped tab ⇒ inherit that group.
        target_tab_id = self.manager.get_tab_id(target_tab)
        target_group = self.manager.group_manager.get_group_for_tab(target_tab_id)
        if target_group:
            self.manager.group_manager.add_tab_to_group(target_group.id, tab_id)

        self.manager._rebuild_tab_bar_order()

        self.logger.info(
            f"Tab '{moving_tab.label_widget.get_text()}' "
            f"moved from {moving_idx} to {new_idx}"
        )
