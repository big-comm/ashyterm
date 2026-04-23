"""Unit tests for TabGroupsController.

These exercise the pure helpers and the stateful flows (membership,
move-mode) with a fake TabManager. The GTK-heavy paths (chip widget
build, dialog construction) live under conftest's mocked ``gi``, so
we just assert that the expected mock calls fire without asserting on
render output.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.terminal.tab_groups import TabGroupManager
from ashyterm.terminal.tab_groups_controller import (
    TabGroupsController,
    contrasting_text_for_hex,
)


class FakeTab:
    """Minimal tab-widget stand-in with mockable CSS/visibility API."""

    def __init__(self, name: str = ""):
        self.name = name
        self._css_classes: set[str] = set()
        self._visible = True
        self._tooltip: str = ""
        self._group_border_provider = None
        # Methods used by controller paths
        self.add_css_class = MagicMock(side_effect=self._css_classes.add)
        self.remove_css_class = MagicMock(side_effect=self._css_classes.discard)
        self.set_visible = MagicMock(side_effect=lambda v: setattr(self, "_visible", v))
        self.get_style_context = MagicMock(return_value=MagicMock())

    def __repr__(self):  # pragma: no cover - debug aid
        return f"<FakeTab {self.name!r}>"


class FakeTabManager:
    """Fake TabManager exposing just the attributes the controller touches."""

    def __init__(self):
        self.logger = MagicMock()
        self.group_manager = TabGroupManager()
        self.tabs: list[FakeTab] = []
        self.active_tab: FakeTab | None = None

        # Move-mode companion state owned by the real TabManager
        self._tab_being_moved = None
        self._cancel_tab_move = MagicMock()
        self._clear_tab_drop_highlights = MagicMock()

        # Hooks the controller calls back into
        self.set_active_tab = MagicMock(side_effect=self._set_active)
        self._rebuild_tab_bar_order = MagicMock()
        self._on_tab_close_button_clicked = MagicMock(
            side_effect=lambda _btn, tab: self.tabs.remove(tab) if tab in self.tabs else None
        )
        self.create_local_tab = MagicMock(side_effect=self._make_local_tab)
        self._get_tab_close_button = MagicMock(return_value=MagicMock())

        # Fake GTK containers
        self.tab_bar_box = MagicMock()
        self.tab_bar_box.get_first_child = MagicMock(return_value=None)
        self.view_stack = MagicMock()

    def _set_active(self, tab):
        self.active_tab = tab

    def _make_local_tab(self):
        tab = FakeTab(f"local-{len(self.tabs) + 1}")
        self.tabs.append(tab)
        if self.active_tab is None:
            self.active_tab = tab
        return MagicMock()  # stand-in Vte.Terminal

    def get_tab_id(self, tab) -> str:
        return str(id(tab))

    def _get_tab_by_id(self, tab_id: str):
        for t in self.tabs:
            if str(id(t)) == tab_id:
                return t
        return None


@pytest.fixture
def manager():
    return FakeTabManager()


@pytest.fixture
def controller(manager):
    return TabGroupsController(manager)


# ── contrasting_text_for_hex ─────────────────────────────────


class TestContrastingText:
    def test_dark_background_yields_white(self):
        assert contrasting_text_for_hex("#000000") == "#FFFFFF"
        assert contrasting_text_for_hex("#1a1a1a") == "#FFFFFF"

    def test_light_background_yields_black(self):
        assert contrasting_text_for_hex("#FFFFFF") == "#000000"
        assert contrasting_text_for_hex("#f8e45c") == "#000000"

    def test_missing_hash_is_accepted(self):
        # lstrip("#") is a no-op when there is no "#" — same palette entry
        # is accepted and resolves to the same contrast as the "#" form.
        with_hash = contrasting_text_for_hex("#1a1a1a")
        without_hash = contrasting_text_for_hex("1a1a1a")
        assert with_hash == without_hash == "#FFFFFF"

    def test_invalid_hex_falls_back_to_black(self):
        assert contrasting_text_for_hex("not-a-color") == "#000000"
        assert contrasting_text_for_hex("") == "#000000"
        assert contrasting_text_for_hex("#zzz") == "#000000"


# ── next_group_name ──────────────────────────────────────────


class TestNextGroupName:
    def test_first_name_is_letter_a(self, controller):
        name = controller.next_group_name()
        # Exact prefix depends on active locale — only assert on the suffix.
        assert name.endswith(" A")

    def test_skips_existing_names(self, controller, manager):
        first = controller.next_group_name()
        manager.group_manager.create_group(first)
        second = controller.next_group_name()
        assert second != first
        assert second.endswith(" B")

    def test_rolls_over_alphabet(self, controller, manager):
        # Seed the manager with the candidates the controller would emit,
        # so this test is independent of the active locale's translation
        # of "Group".
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            manager.group_manager.create_group(controller.next_group_name())
        name = controller.next_group_name()
        # After 26 single-letter names, the next rollover slot starts with "1A".
        assert name.endswith(" 1A")


# ── ensure_contiguous ────────────────────────────────────────


class TestEnsureContiguous:
    def test_noop_for_group_with_one_tab(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])
        controller.ensure_contiguous(g.id)
        assert manager.tabs == [t1]

    def test_reorders_scattered_group_tabs_next_to_first_member(
        self, controller, manager
    ):
        t1, t2, t3, t4 = [FakeTab(n) for n in "abcd"]
        manager.tabs = [t1, t2, t3, t4]
        # Group contains t1 and t3 (t2 is between them, outside the group).
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t1)), str(id(t3))]
        )
        controller.ensure_contiguous(g.id)
        # Group tabs must end up adjacent, with t1 still first among them.
        assert manager.tabs[:2] == [t1, t3]
        assert set(manager.tabs) == {t1, t2, t3, t4}

    def test_unknown_group_is_noop(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        controller.ensure_contiguous("no-such-group")
        assert manager.tabs == [t1]


# ── create / ungroup ─────────────────────────────────────────


class TestCreateAndUngroup:
    def test_create_group_from_tabs_assigns_name_and_color(self, controller, manager):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        controller.create_group_from_tabs([t1, t2])
        groups = manager.group_manager.groups
        assert len(groups) == 1
        g = groups[0]
        assert g.tab_ids == [str(id(t1)), str(id(t2))]
        assert g.color
        manager._rebuild_tab_bar_order.assert_called()

    def test_create_group_from_active_tab_is_noop_when_no_active(
        self, controller, manager
    ):
        controller.create_group_from_active_tab()
        assert manager.group_manager.groups == []

    def test_create_group_from_active_tab_groups_active(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        manager.active_tab = t1
        controller.create_group_from_active_tab()
        groups = manager.group_manager.groups
        assert len(groups) == 1
        assert groups[0].tab_ids == [str(id(t1))]

    def test_ungroup_active_tab_removes_membership(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        manager.active_tab = t1
        manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])

        controller.ungroup_active_tab()

        assert manager.group_manager.get_group_for_tab(str(id(t1))) is None
        t1.remove_css_class.assert_any_call("in-group")
        manager._rebuild_tab_bar_order.assert_called()


# ── context-menu actions ─────────────────────────────────────


class TestMenuActions:
    def test_remove_tab_from_group_action(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])

        controller.remove_tab_from_group_action(t1)

        assert manager.group_manager.get_group_for_tab(str(id(t1))) is None
        t1.remove_css_class.assert_any_call("in-group")

    def test_add_tab_to_group_action_expands_collapsed_group(
        self, controller, manager
    ):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])
        manager.group_manager.toggle_collapsed(g.id)
        assert g.is_collapsed

        controller.add_tab_to_group_action(t2, g.id)

        assert str(id(t2)) in g.tab_ids
        assert not g.is_collapsed  # auto-expanded so user sees the new tab

    def test_add_tab_to_group_keeps_contiguity(self, controller, manager):
        t1, t2, t3 = FakeTab("a"), FakeTab("b"), FakeTab("c")
        manager.tabs = [t1, t2, t3]
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])

        controller.add_tab_to_group_action(t3, g.id)

        # After add + ensure_contiguous, t1 and t3 must be adjacent.
        group_positions = sorted(manager.tabs.index(tab) for tab in (t1, t3))
        assert group_positions[1] - group_positions[0] == 1


# ── focus_first_visible_outside ──────────────────────────────


class TestFocusFirstVisibleOutside:
    def test_activates_first_non_excluded(self, controller, manager):
        t1, t2, t3 = FakeTab("a"), FakeTab("b"), FakeTab("c")
        manager.tabs = [t1, t2, t3]
        controller.focus_first_visible_outside([str(id(t1))])
        assert manager.active_tab is t2

    def test_noop_when_all_excluded(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        manager.active_tab = None
        controller.focus_first_visible_outside([str(id(t1))])
        assert manager.active_tab is None


# ── move-mode lifecycle ──────────────────────────────────────


class TestMoveMode:
    def test_is_moving_group_reflects_state(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])

        assert not controller.is_moving_group()
        controller.start_move(g)
        assert controller.is_moving_group()
        controller.cancel_move()
        assert not controller.is_moving_group()

    def test_moving_group_tab_ids_snapshot(self, controller, manager):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t1)), str(id(t2))]
        )
        controller.start_move(g)
        assert set(controller.moving_group_tab_ids()) == {str(id(t1)), str(id(t2))}

    def test_is_tab_in_moving_group(self, controller, manager):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t1))]
        )
        controller.start_move(g)
        assert controller.is_tab_in_moving_group(str(id(t1))) is True
        assert controller.is_tab_in_moving_group(str(id(t2))) is False

    def test_cancel_without_active_move_is_noop(self, controller, manager):
        # Should not explode or touch the tab bar.
        controller.cancel_move()
        manager._clear_tab_drop_highlights.assert_not_called()

    def test_perform_move_repositions_group_tabs(self, controller, manager):
        # [a, b, c, d] with group=[a, b]; drop at right of d should end [c, d, a, b]
        t_a, t_b, t_c, t_d = FakeTab("a"), FakeTab("b"), FakeTab("c"), FakeTab("d")
        manager.tabs = [t_a, t_b, t_c, t_d]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t_a)), str(id(t_b))]
        )
        controller.start_move(g)

        controller.perform_move(t_d, "right")

        assert manager.tabs == [t_c, t_d, t_a, t_b]
        manager._rebuild_tab_bar_order.assert_called()

    def test_perform_move_left_of_target(self, controller, manager):
        t_a, t_b, t_c = FakeTab("a"), FakeTab("b"), FakeTab("c")
        manager.tabs = [t_a, t_b, t_c]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t_c))]
        )
        controller.start_move(g)

        controller.perform_move(t_a, "left")

        assert manager.tabs == [t_c, t_a, t_b]

    def test_perform_move_onto_own_tab_is_noop(self, controller, manager):
        t_a, t_b = FakeTab("a"), FakeTab("b")
        manager.tabs = [t_a, t_b]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t_a))]
        )
        controller.start_move(g)

        controller.perform_move(t_a, "right")

        # Dropping a single-member group onto itself is a no-op.
        assert manager.tabs == [t_a, t_b]

    def test_perform_move_without_active_move_is_noop(self, controller, manager):
        t_a, t_b = FakeTab("a"), FakeTab("b")
        manager.tabs = [t_a, t_b]
        controller.perform_move(t_a, "right")
        assert manager.tabs == [t_a, t_b]
        manager._rebuild_tab_bar_order.assert_not_called()


# ── css provider caching ─────────────────────────────────────


class TestProviderCaching:
    def test_chip_provider_cached_per_color(self, controller):
        p1 = controller.get_chip_provider("#f66151")
        p2 = controller.get_chip_provider("#f66151")
        p3 = controller.get_chip_provider("#62a0ea")
        assert p1 is p2
        assert p1 is not p3

    def test_border_provider_cached_per_color(self, controller):
        p1 = controller.get_border_provider("#f66151")
        p2 = controller.get_border_provider("#f66151")
        p3 = controller.get_border_provider("#62a0ea")
        assert p1 is p2
        assert p1 is not p3

    def test_apply_border_color_skips_when_same_provider(self, controller):
        tab = FakeTab("a")
        controller.apply_border_color(tab, "#f66151")
        first_provider = tab._group_border_provider
        ctx = tab.get_style_context.return_value
        ctx.reset_mock()

        controller.apply_border_color(tab, "#f66151")

        # Same color: no context churn on the second call.
        ctx.add_provider.assert_not_called()
        ctx.remove_provider.assert_not_called()
        assert tab._group_border_provider is first_provider

    def test_apply_border_color_swaps_provider(self, controller):
        tab = FakeTab("a")
        controller.apply_border_color(tab, "#f66151")
        red = tab._group_border_provider
        controller.apply_border_color(tab, "#62a0ea")
        assert tab._group_border_provider is not red


# ── close_group ──────────────────────────────────────────────


class TestCloseGroup:
    def test_close_group_closes_each_member_tab(self, controller, manager):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t1)), str(id(t2))]
        )

        controller.close_group(g)

        assert manager.group_manager.get_group(g.id) is None
        assert manager._on_tab_close_button_clicked.call_count == 2


# ── ungroup_all ──────────────────────────────────────────────


class TestUngroupAll:
    def test_ungroup_all_keeps_tabs_drops_group(self, controller, manager):
        t1, t2 = FakeTab("a"), FakeTab("b")
        manager.tabs = [t1, t2]
        g = manager.group_manager.create_group(
            "G", initial_tab_ids=[str(id(t1)), str(id(t2))]
        )

        controller.ungroup_all(g)

        assert manager.group_manager.get_group(g.id) is None
        assert manager.tabs == [t1, t2]
        t1.remove_css_class.assert_any_call("in-group")
        t2.remove_css_class.assert_any_call("in-group")


# ── on_color_chosen ──────────────────────────────────────────


class TestColorChosen:
    def test_rgba_is_converted_to_lowercase_hex(self, controller, manager):
        t1 = FakeTab("a")
        manager.tabs = [t1]
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(t1))])

        color = SimpleNamespace(red=1.0, green=0.0, blue=0.0, alpha=1.0)
        dialog = MagicMock()
        dialog.choose_rgba_finish = MagicMock(return_value=color)

        controller._on_color_chosen(dialog, MagicMock(), g)

        assert g.color == "#ff0000"
        manager._rebuild_tab_bar_order.assert_called()


# ── new_tab_in ───────────────────────────────────────────────


class TestNewTabInGroup:
    def test_new_tab_is_added_and_grouped(self, controller, manager):
        seed = FakeTab("seed")
        manager.tabs = [seed]
        manager.active_tab = seed
        g = manager.group_manager.create_group("G", initial_tab_ids=[str(id(seed))])

        controller.new_tab_in(g)

        assert len(manager.tabs) == 2
        new_tab = manager.tabs[-1]
        assert str(id(new_tab)) in g.tab_ids
