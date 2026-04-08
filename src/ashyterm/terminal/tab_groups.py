"""Tab group data model and management for organizing tabs into named, colored groups."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TabGroup:
    """Represents a named, colored group of tabs."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    color: str = ""
    is_collapsed: bool = False
    tab_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "is_collapsed": self.is_collapsed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TabGroup:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            color=data.get("color", ""),
            is_collapsed=data.get("is_collapsed", False),
        )


class TabGroupManager:
    """Manages the lifecycle and state of tab groups.

    Tab IDs here correspond to the string identity of tab widgets
    (derived from ``id(tab_widget)``).  The manager is UI-agnostic:
    it only tracks membership and metadata.
    """

    # Predefined palette aligned with Adwaita named colors
    PALETTE: list[tuple[str, str]] = [
        ("red", "#f66151"),
        ("orange", "#ffa348"),
        ("yellow", "#f8e45c"),
        ("green", "#57e389"),
        ("cyan", "#5bc8af"),
        ("blue", "#62a0ea"),
        ("purple", "#c061cb"),
        ("pink", "#f5c2e7"),
    ]

    def __init__(self) -> None:
        self._groups: dict[str, TabGroup] = {}
        # Ordered list of group ids (visual order in tab bar)
        self._group_order: list[str] = []

    # ── queries ──────────────────────────────────────────────

    @property
    def groups(self) -> list[TabGroup]:
        """Return groups in visual order."""
        return [self._groups[gid] for gid in self._group_order if gid in self._groups]

    def get_group(self, group_id: str) -> Optional[TabGroup]:
        return self._groups.get(group_id)

    def get_group_for_tab(self, tab_id: str) -> Optional[TabGroup]:
        for group in self._groups.values():
            if tab_id in group.tab_ids:
                return group
        return None

    def has_groups(self) -> bool:
        return bool(self._groups)

    # ── CRUD ─────────────────────────────────────────────────

    def create_group(
        self,
        name: str,
        color: str = "",
        initial_tab_ids: Optional[list[str]] = None,
    ) -> TabGroup:
        """Create a new group and optionally assign tabs to it."""
        if not color:
            color = self._next_color()
        group = TabGroup(name=name, color=color)
        if initial_tab_ids:
            for tid in initial_tab_ids:
                self._remove_tab_from_current_group(tid)
            group.tab_ids = list(initial_tab_ids)
        self._groups[group.id] = group
        self._group_order.append(group.id)
        return group

    def delete_group(self, group_id: str) -> list[str]:
        """Delete a group. Returns the tab_ids that were in it (now ungrouped)."""
        group = self._groups.pop(group_id, None)
        if not group:
            return []
        if group_id in self._group_order:
            self._group_order.remove(group_id)
        return list(group.tab_ids)

    def rename_group(self, group_id: str, new_name: str) -> None:
        group = self._groups.get(group_id)
        if group:
            group.name = new_name

    def set_group_color(self, group_id: str, color: str) -> None:
        group = self._groups.get(group_id)
        if group:
            group.color = color

    def toggle_collapsed(self, group_id: str) -> bool:
        """Toggle collapsed state. Returns the new state."""
        group = self._groups.get(group_id)
        if not group:
            return False
        group.is_collapsed = not group.is_collapsed
        return group.is_collapsed

    # ── tab membership ───────────────────────────────────────

    def add_tab_to_group(self, group_id: str, tab_id: str, position: int = -1) -> bool:
        """Add a tab to a group. Removes from current group first if any."""
        group = self._groups.get(group_id)
        if not group:
            return False
        self._remove_tab_from_current_group(tab_id)
        if position < 0 or position >= len(group.tab_ids):
            group.tab_ids.append(tab_id)
        else:
            group.tab_ids.insert(position, tab_id)
        return True

    def remove_tab_from_group(self, tab_id: str) -> Optional[str]:
        """Remove a tab from whatever group it belongs to.
        Returns the group_id it was removed from, or None."""
        group = self.get_group_for_tab(tab_id)
        if not group:
            return None
        group.tab_ids.remove(tab_id)
        removed_from = group.id
        # Auto-delete empty groups
        if not group.tab_ids:
            self.delete_group(group.id)
        return removed_from

    def move_tab_in_group(self, tab_id: str, new_position: int) -> None:
        """Reorder a tab within its current group."""
        group = self.get_group_for_tab(tab_id)
        if not group or tab_id not in group.tab_ids:
            return
        group.tab_ids.remove(tab_id)
        if new_position < 0 or new_position >= len(group.tab_ids):
            group.tab_ids.append(tab_id)
        else:
            group.tab_ids.insert(new_position, tab_id)

    def on_tab_removed(self, tab_id: str) -> None:
        """Notify the manager that a tab was closed/destroyed."""
        self.remove_tab_from_group(tab_id)

    # ── serialization ────────────────────────────────────────

    def to_list(self) -> list[dict]:
        """Serialize all groups for persistence."""
        return [self._groups[gid].to_dict() for gid in self._group_order if gid in self._groups]

    def load_from_list(self, data: list[dict]) -> None:
        """Restore groups from persisted data. Tab membership is set by the caller."""
        self._groups.clear()
        self._group_order.clear()
        for item in data:
            group = TabGroup.from_dict(item)
            self._groups[group.id] = group
            self._group_order.append(group.id)

    # ── private helpers ──────────────────────────────────────

    def _remove_tab_from_current_group(self, tab_id: str) -> None:
        group = self.get_group_for_tab(tab_id)
        if group and tab_id in group.tab_ids:
            group.tab_ids.remove(tab_id)
            if not group.tab_ids:
                self.delete_group(group.id)

    def move_group(self, group_id: str, new_index: int) -> None:
        """Move a group to a new position in the visual order."""
        if group_id not in self._group_order:
            return
        self._group_order.remove(group_id)
        new_index = max(0, min(new_index, len(self._group_order)))
        self._group_order.insert(new_index, group_id)

    def _next_color(self) -> str:
        """Pick the next color from the palette that is used the least."""
        usage: dict[str, int] = {c: 0 for _, c in self.PALETTE}
        for g in self._groups.values():
            if g.color in usage:
                usage[g.color] += 1
        min_count = min(usage.values()) if usage else 0
        for _, c in self.PALETTE:
            if usage.get(c, 0) == min_count:
                return c
        return self.PALETTE[0][1]
