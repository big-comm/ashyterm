"""Tests for TabMoveController (drag-reorder state for the tab bar)."""

from unittest.mock import MagicMock

import pytest

from ashyterm.terminal.tab_groups import TabGroupManager
from ashyterm.terminal.tab_move_controller import TabMoveController


class _FakeTab:
    """Tab-widget stand-in with the surface TabMoveController touches."""

    def __init__(self, name: str = ""):
        self.name = name
        self._classes: set[str] = set()
        self.add_css_class = MagicMock(side_effect=self._classes.add)
        self.remove_css_class = MagicMock(side_effect=self._classes.discard)
        self.label_widget = MagicMock()
        self.label_widget.get_text = MagicMock(return_value=name)
        self.close_button = MagicMock()


class _FakeManager:
    """Minimal TabManager stand-in."""

    def __init__(self):
        self.tabs: list[_FakeTab] = []
        self.tab_bar_box = MagicMock()
        self.group_manager = TabGroupManager()
        self._rebuild_tab_bar_order = MagicMock()
        self._get_tab_close_button = MagicMock(
            side_effect=lambda tab: tab.close_button
        )
        self.logger = MagicMock()

    def get_tab_id(self, tab) -> str:
        return str(id(tab))


@pytest.fixture
def manager():
    return _FakeManager()


@pytest.fixture
def controller(manager):
    return TabMoveController(manager)


# ── state inspection ─────────────────────────────────────────


class TestInspection:
    def test_fresh_controller_is_idle(self, controller):
        assert controller.is_active is False
        assert controller.moving_tab is None
        assert controller.drop_target is None
        assert controller.drop_side == "left"


# ── start / cancel lifecycle ─────────────────────────────────


class TestLifecycle:
    def test_start_requires_at_least_two_tabs(self, manager, controller):
        only_tab = _FakeTab("only")
        manager.tabs = [only_tab]

        controller.start(only_tab)

        assert controller.is_active is False
        only_tab.add_css_class.assert_not_called()
        manager.tab_bar_box.add_css_class.assert_not_called()

    def test_start_flips_mode_and_hides_close_buttons(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]

        controller.start(a)

        assert controller.is_active is True
        assert controller.moving_tab is a
        a.add_css_class.assert_any_call("tab-moving")
        manager.tab_bar_box.add_css_class.assert_called_with("tab-bar-move-mode")
        # Every close button is hidden (but kept laid out).
        for tab in (a, b):
            tab.close_button.set_opacity.assert_called_with(0)
            tab.close_button.set_sensitive.assert_called_with(False)

    def test_cancel_restores_visuals(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]
        controller.start(a)

        # Reset mocks so we can assert only what cancel did.
        a.close_button.reset_mock()
        b.close_button.reset_mock()
        manager.tab_bar_box.reset_mock()

        controller.cancel()

        assert controller.is_active is False
        a.remove_css_class.assert_any_call("tab-moving")
        manager.tab_bar_box.remove_css_class.assert_called_with(
            "tab-bar-move-mode"
        )
        for tab in (a, b):
            tab.close_button.set_opacity.assert_called_with(1)
            tab.close_button.set_sensitive.assert_called_with(True)

    def test_cancel_is_noop_when_idle(self, manager, controller):
        controller.cancel()
        assert controller.is_active is False
        manager.tab_bar_box.remove_css_class.assert_not_called()


# ── highlight management ─────────────────────────────────────


class TestHighlights:
    def test_update_left_highlight(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]

        controller.update_highlight(b, "left")

        assert controller.drop_target is b
        assert controller.drop_side == "left"
        b.add_css_class.assert_any_call("tab-drop-left")

    def test_update_right_highlight(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]

        controller.update_highlight(b, "right")

        b.add_css_class.assert_any_call("tab-drop-right")

    def test_update_clears_prior_highlights_first(self, manager, controller):
        a, b, c = _FakeTab("a"), _FakeTab("b"), _FakeTab("c")
        manager.tabs = [a, b, c]
        controller.update_highlight(b, "left")
        b.remove_css_class.reset_mock()

        controller.update_highlight(c, "right")

        # The prior left highlight on b is cleaned during clear_highlights.
        b.remove_css_class.assert_any_call("tab-drop-left")
        assert controller.drop_target is c

    def test_clear_highlights_nukes_every_tab(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]

        controller.clear_highlights()

        for tab in (a, b):
            tab.remove_css_class.assert_any_call("tab-drop-target")
            tab.remove_css_class.assert_any_call("tab-drop-left")
            tab.remove_css_class.assert_any_call("tab-drop-right")
        assert controller.drop_target is None


# ── perform (commit a drop) ──────────────────────────────────


class TestPerform:
    def test_perform_without_active_move_is_noop(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]

        controller.perform()

        assert manager.tabs == [a, b]
        manager._rebuild_tab_bar_order.assert_not_called()

    def test_drop_right_of_target_moves_forward(self, manager, controller):
        a, b, c, d = [_FakeTab(n) for n in "abcd"]
        manager.tabs = [a, b, c, d]
        controller.start(a)
        controller.set_drop_target(c, "right")

        controller.perform()

        # a should land right after c, i.e. [b, c, a, d].
        assert manager.tabs == [b, c, a, d]
        manager._rebuild_tab_bar_order.assert_called_once()

    def test_drop_left_of_target_moves_backward(self, manager, controller):
        a, b, c = [_FakeTab(n) for n in "abc"]
        manager.tabs = [a, b, c]
        controller.start(c)
        controller.set_drop_target(a, "left")

        controller.perform()

        # c should land right before a, i.e. [c, a, b].
        assert manager.tabs == [c, a, b]

    def test_drop_on_self_is_noop_for_order(self, manager, controller):
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]
        controller.start(a)
        controller.set_drop_target(a, "right")

        controller.perform()

        assert manager.tabs == [a, b]

    def test_dropping_adjacent_to_grouped_tab_joins_group(self, manager, controller):
        a, b, c = [_FakeTab(n) for n in "abc"]
        manager.tabs = [a, b, c]
        # b is in "dev". Drop a adjacent to b ⇒ a joins "dev".
        group = manager.group_manager.create_group(
            "dev", initial_tab_ids=[manager.get_tab_id(b)]
        )
        controller.start(a)
        controller.set_drop_target(b, "right")

        controller.perform()

        assert manager.get_tab_id(a) in group.tab_ids

    def test_dropping_away_from_group_removes_membership(self, manager, controller):
        a, b, c = [_FakeTab(n) for n in "abc"]
        manager.tabs = [a, b, c]
        group = manager.group_manager.create_group(
            "dev", initial_tab_ids=[manager.get_tab_id(a)]
        )
        controller.start(a)
        controller.set_drop_target(c, "right")

        controller.perform()

        # a was in dev; dropping onto c (not in dev) ⇒ leaves dev.
        # Group auto-deletes when empty.
        assert manager.group_manager.get_group(group.id) is None

    def test_drop_after_neighbor_does_not_shift(self, manager, controller):
        # Dropping a "right" of b in [a, b] computes new_idx = 2, then
        # corrects by -1 (moving_idx=0 < 2), landing at index 1. The
        # list is [b, a] afterwards.
        a, b = _FakeTab("a"), _FakeTab("b")
        manager.tabs = [a, b]
        controller.start(a)
        controller.set_drop_target(b, "right")

        controller.perform()

        assert manager.tabs == [b, a]


# ── tabs.py integration ──────────────────────────────────────


class TestTabsIntegration:
    def test_tabmanager_exposes_move_controller(self):
        from ashyterm.terminal.tabs import TabManager

        # The property proxies still need to read from the controller.
        assert hasattr(TabManager, "_update_move_highlight")
        assert hasattr(TabManager, "_clear_tab_drop_highlights")
        assert hasattr(TabManager, "_perform_tab_move")
        assert hasattr(TabManager, "_start_tab_move")
        assert hasattr(TabManager, "_cancel_tab_move")

    def test_legacy_state_attrs_proxy_through_controller(self):
        from ashyterm.terminal.tabs import TabManager

        mgr = object.__new__(TabManager)
        mgr.move_controller = TabMoveController(_FakeManager())

        stub = _FakeTab("x")
        mgr._tab_being_moved = stub
        assert mgr.move_controller.moving_tab is stub
        assert mgr._tab_being_moved is stub

        mgr._drop_side = "right"
        assert mgr._drop_side == "right"
