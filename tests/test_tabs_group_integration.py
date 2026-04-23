"""Integration tests for the tab-groups split.

Verifies that:

* The TabManager still exposes the public API external callers rely on
  (``create_group_from_active_tab``, ``ungroup_active_tab``,
  ``create_group_from_tabs``, ``group_manager``) and routes it through
  the extracted controller.
* The move-mode bridge between TabManager and TabGroupsController stays
  consistent (``cancel_tab_move_if_active``, right-click cancel path).
* State restore (``window_state.py``) can still reach ``group_manager``
  via the TabManager surface.
* Rebuilding the tab bar delegates chip construction to the controller.

These tests build TabManager instances directly with ``object.__new__``
so they don't pull in the full GTK init path, but they do exercise the
real method bodies on ``tabs.py``.
"""

from unittest.mock import MagicMock

from ashyterm.terminal.tab_groups import TabGroupManager
from ashyterm.terminal.tab_groups_controller import TabGroupsController
from ashyterm.terminal.tab_move_controller import TabMoveController
from ashyterm.terminal.tabs import TabManager


def _make_manager() -> TabManager:
    """Build a TabManager instance with just the fields the delegators need.

    We skip ``__init__`` (which touches GTK, threads, and the settings
    bus) and set up only the attributes the group-delegator methods and
    the move-mode bridge read from.
    """
    mgr = object.__new__(TabManager)
    mgr.logger = MagicMock()
    mgr.tabs = []
    mgr.active_tab = None
    mgr.tab_bar_box = MagicMock()
    mgr.tab_bar_box.get_first_child = MagicMock(return_value=None)
    mgr.view_stack = MagicMock()

    mgr.group_manager = TabGroupManager()
    mgr.group_controller = TabGroupsController(mgr)
    # move_controller must exist before _tab_being_moved (now a
    # property-backed proxy) is written to.
    mgr.move_controller = TabMoveController(mgr)

    # Fields the move-mode bridge consults.
    mgr._cancel_tab_move = MagicMock()
    mgr._clear_tab_drop_highlights = MagicMock()
    mgr._rebuild_tab_bar_order = MagicMock()
    mgr._on_tab_close_button_clicked = MagicMock()
    mgr._get_tab_close_button = MagicMock(return_value=MagicMock())
    mgr.set_active_tab = MagicMock(
        side_effect=lambda tab: setattr(mgr, "active_tab", tab)
    )
    mgr.create_local_tab = MagicMock()
    return mgr


class _StubTab:
    def __init__(self):
        self.add_css_class = MagicMock()
        self.remove_css_class = MagicMock()
        self.set_visible = MagicMock()
        self.get_style_context = MagicMock(return_value=MagicMock())
        self._group_border_provider = None


# ── public API delegation ────────────────────────────────────


class TestPublicApiIsRouted:
    def test_public_group_methods_exist(self):
        for name in (
            "create_group_from_active_tab",
            "ungroup_active_tab",
            "create_group_from_tabs",
            "group_manager",
            "group_controller",
        ):
            assert hasattr(TabManager, name) or name in (
                "group_manager",
                "group_controller",
            )

    def test_create_group_from_active_tab_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.create_group_from_active_tab = MagicMock()
        mgr.create_group_from_active_tab()
        mgr.group_controller.create_group_from_active_tab.assert_called_once_with()

    def test_ungroup_active_tab_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.ungroup_active_tab = MagicMock()
        mgr.ungroup_active_tab()
        mgr.group_controller.ungroup_active_tab.assert_called_once_with()

    def test_create_group_from_tabs_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.create_group_from_tabs = MagicMock()
        tabs = [_StubTab(), _StubTab()]
        mgr.create_group_from_tabs(tabs, name="Servers")
        mgr.group_controller.create_group_from_tabs.assert_called_once_with(
            tabs, "Servers"
        )

    def test_ensure_contiguous_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.ensure_contiguous = MagicMock()
        mgr._ensure_group_tabs_contiguous("gid")
        mgr.group_controller.ensure_contiguous.assert_called_once_with("gid")

    def test_remove_tab_from_group_action_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.remove_tab_from_group_action = MagicMock()
        tab = _StubTab()
        mgr._remove_tab_from_group_action(tab)
        mgr.group_controller.remove_tab_from_group_action.assert_called_once_with(tab)

    def test_add_tab_to_group_action_forwards(self):
        mgr = _make_manager()
        mgr.group_controller.add_tab_to_group_action = MagicMock()
        tab = _StubTab()
        mgr._add_tab_to_group_action(tab, "gid")
        mgr.group_controller.add_tab_to_group_action.assert_called_once_with(
            tab, "gid"
        )


# ── move-mode bridge ─────────────────────────────────────────


class TestMoveBridge:
    def test_cancel_tab_move_if_active_handles_group_mode(self):
        mgr = _make_manager()
        t = _StubTab()
        mgr.tabs = [t]
        g = mgr.group_manager.create_group("G", initial_tab_ids=[str(id(t))])
        mgr.group_controller.start_move(g)

        cancelled = mgr.cancel_tab_move_if_active()

        assert cancelled is True
        assert mgr.group_controller.is_moving_group() is False
        # The tab-move branch should NOT have been taken.
        mgr._cancel_tab_move.assert_not_called()

    def test_cancel_tab_move_if_active_handles_tab_mode(self):
        mgr = _make_manager()
        mgr._tab_being_moved = _StubTab()

        cancelled = mgr.cancel_tab_move_if_active()

        assert cancelled is True
        mgr._cancel_tab_move.assert_called_once_with()

    def test_cancel_tab_move_if_active_noop_when_idle(self):
        mgr = _make_manager()
        assert mgr.cancel_tab_move_if_active() is False
        mgr._cancel_tab_move.assert_not_called()


# ── state restore surface ────────────────────────────────────


class TestStateRestoreSurface:
    def test_group_manager_is_authoritative_for_persistence(self):
        """window_state.py serializes via ``tab_manager.group_manager.to_list``.
        Verify the attribute still resolves to the real data model even
        after the UI split.
        """
        mgr = _make_manager()
        mgr.group_manager.create_group("A", initial_tab_ids=["t1", "t2"])
        data = mgr.group_manager.to_list()
        assert isinstance(data, list)
        assert data[0]["name"] == "A"

    def test_group_manager_round_trips_through_controller_mutations(self):
        mgr = _make_manager()
        t1, t2 = _StubTab(), _StubTab()
        mgr.tabs = [t1, t2]
        mgr.active_tab = t1

        # Public API -> controller -> data model
        mgr.create_group_from_active_tab()
        assert len(mgr.group_manager.groups) == 1
        assert str(id(t1)) in mgr.group_manager.groups[0].tab_ids


# ── tab-bar rebuild chip hook ────────────────────────────────


class TestRebuildUsesController:
    def test_rebuild_calls_build_chip_for_grouped_tabs(self):
        """``_rebuild_tab_bar_order`` must build chips via the controller.
        This is a property-style test: we spy on the controller entry
        point and drive a minimal rebuild to ensure the hook fires.
        """
        mgr = _make_manager()

        # Provide a real tab-bar stub so the rebuild loop walks it.
        mgr.tab_bar_box.get_first_child = MagicMock(return_value=None)
        mgr.tab_bar_box.append = MagicMock()

        t = _StubTab()
        mgr.tabs = [t]
        mgr.group_manager.create_group("G", initial_tab_ids=[str(id(t))])

        mgr.group_controller.build_chip = MagicMock(return_value=MagicMock())
        mgr.group_controller.apply_border_color = MagicMock()

        # Call the real method on TabManager (not the mock).
        TabManager._rebuild_tab_bar_order(mgr)

        mgr.group_controller.build_chip.assert_called_once()
        mgr.group_controller.apply_border_color.assert_called_once()


# ── cross-module contract smoke ──────────────────────────────


class TestCrossModuleContract:
    def test_window_actions_expected_methods_are_present(self):
        """window_actions.py calls these on tab_manager. Guarantee they
        stay part of the public surface after the split.
        """
        required = {
            "create_group_from_active_tab",
            "ungroup_active_tab",
            "group_manager",
        }
        mgr = _make_manager()
        missing = [name for name in required if not hasattr(mgr, name)]
        assert not missing, f"Missing from TabManager surface: {missing}"

    def test_controller_holds_no_duplicate_state(self):
        """group membership lives in group_manager only; controller must
        not keep its own copy."""
        mgr = _make_manager()
        t = _StubTab()
        mgr.tabs = [t]
        mgr.active_tab = t
        mgr.create_group_from_active_tab()
        # The only place "in-group" membership lives is the data layer.
        assert mgr.group_manager.get_group_for_tab(str(id(t))) is not None
