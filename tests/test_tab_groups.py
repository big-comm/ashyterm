"""Tests for TabGroup and TabGroupManager."""

import pytest

from ashyterm.terminal.tab_groups import TabGroup, TabGroupManager


class TestTabGroup:
    def test_defaults(self):
        g = TabGroup(name="Dev", color="#f66151")
        assert g.name == "Dev"
        assert g.color == "#f66151"
        assert g.is_collapsed is False
        assert g.tab_ids == []
        assert g.id  # auto-generated uuid

    def test_to_dict_roundtrip(self):
        g = TabGroup(id="abc", name="Servers", color="#62a0ea", is_collapsed=True)
        d = g.to_dict()
        g2 = TabGroup.from_dict(d)
        assert g2.id == "abc"
        assert g2.name == "Servers"
        assert g2.color == "#62a0ea"
        assert g2.is_collapsed is True

    def test_from_dict_missing_fields(self):
        g = TabGroup.from_dict({})
        assert g.name == ""
        assert g.color == ""
        assert g.is_collapsed is False


class TestTabGroupManager:
    def setup_method(self):
        self.mgr = TabGroupManager()

    def test_create_group(self):
        g = self.mgr.create_group("Servers")
        assert g.name == "Servers"
        assert g.color  # auto-picked from palette
        assert self.mgr.get_group(g.id) is g
        assert self.mgr.has_groups()

    def test_create_group_with_initial_tabs(self):
        g = self.mgr.create_group("Dev", initial_tab_ids=["t1", "t2"])
        assert g.tab_ids == ["t1", "t2"]
        assert self.mgr.get_group_for_tab("t1") is g
        assert self.mgr.get_group_for_tab("t2") is g

    def test_delete_group_returns_orphaned_tabs(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1", "t2"])
        orphans = self.mgr.delete_group(g.id)
        assert set(orphans) == {"t1", "t2"}
        assert not self.mgr.has_groups()

    def test_delete_nonexistent_group(self):
        assert self.mgr.delete_group("nope") == []

    def test_rename_group(self):
        g = self.mgr.create_group("Old")
        self.mgr.rename_group(g.id, "New")
        assert g.name == "New"

    def test_set_group_color(self):
        g = self.mgr.create_group("X")
        self.mgr.set_group_color(g.id, "#000000")
        assert g.color == "#000000"

    def test_toggle_collapsed(self):
        g = self.mgr.create_group("X")
        assert g.is_collapsed is False
        state = self.mgr.toggle_collapsed(g.id)
        assert state is True
        assert g.is_collapsed is True
        state = self.mgr.toggle_collapsed(g.id)
        assert state is False

    def test_add_tab_to_group(self):
        g = self.mgr.create_group("X")
        assert self.mgr.add_tab_to_group(g.id, "t1")
        assert "t1" in g.tab_ids

    def test_add_tab_at_position(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1", "t3"])
        self.mgr.add_tab_to_group(g.id, "t2", position=1)
        assert g.tab_ids == ["t1", "t2", "t3"]

    def test_add_tab_moves_from_existing_group(self):
        g1 = self.mgr.create_group("A", initial_tab_ids=["t1", "t2"])
        g2 = self.mgr.create_group("B")
        self.mgr.add_tab_to_group(g2.id, "t1")
        assert "t1" not in g1.tab_ids
        assert "t1" in g2.tab_ids

    def test_remove_tab_from_group(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1", "t2"])
        removed_from = self.mgr.remove_tab_from_group("t1")
        assert removed_from == g.id
        assert "t1" not in g.tab_ids

    def test_remove_tab_auto_deletes_empty_group(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1"])
        self.mgr.remove_tab_from_group("t1")
        assert not self.mgr.has_groups()

    def test_remove_tab_not_in_group(self):
        assert self.mgr.remove_tab_from_group("t99") is None

    def test_move_tab_in_group(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1", "t2", "t3"])
        self.mgr.move_tab_in_group("t3", 0)
        assert g.tab_ids == ["t3", "t1", "t2"]

    def test_on_tab_removed(self):
        g = self.mgr.create_group("X", initial_tab_ids=["t1", "t2"])
        self.mgr.on_tab_removed("t1")
        assert "t1" not in g.tab_ids

    def test_groups_order_preserved(self):
        g1 = self.mgr.create_group("First")
        g2 = self.mgr.create_group("Second")
        g3 = self.mgr.create_group("Third")
        assert self.mgr.groups == [g1, g2, g3]

    def test_serialization_roundtrip(self):
        g1 = self.mgr.create_group("A", color="#f66151", initial_tab_ids=["t1"])
        g2 = self.mgr.create_group("B", color="#62a0ea", initial_tab_ids=["t2", "t3"])
        self.mgr.toggle_collapsed(g2.id)

        data = self.mgr.to_list()
        assert len(data) == 2

        mgr2 = TabGroupManager()
        mgr2.load_from_list(data)
        # tab_ids must be restored separately by the caller
        assert len(mgr2.groups) == 2
        assert mgr2.groups[0].name == "A"
        assert mgr2.groups[1].name == "B"
        assert mgr2.groups[1].is_collapsed is True

    def test_next_color_rotates(self):
        colors_seen = set()
        for i in range(len(TabGroupManager.PALETTE)):
            g = self.mgr.create_group(f"G{i}")
            colors_seen.add(g.color)
        # All palette colors should have been used
        palette_colors = {c for _, c in TabGroupManager.PALETTE}
        assert colors_seen == palette_colors

    def test_get_group_for_ungrouped_tab(self):
        assert self.mgr.get_group_for_tab("nonexistent") is None
