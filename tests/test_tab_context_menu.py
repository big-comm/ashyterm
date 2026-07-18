"""Tests for tab_context_menu (builder for the tab right-click menu)."""

from unittest.mock import MagicMock


from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.tab_context_menu import build_menu_model
from ashyterm.terminal.tab_groups import TabGroupManager


class _StubTab:
    """Tab-widget stand-in that can carry a ``session_item`` attribute."""


def _manager_with_tabs(tabs: list, groups: list[tuple[str, list[int]]] = ()):
    """Build a TabManager-shaped stub wired to a real TabGroupManager."""
    mgr = MagicMock()
    mgr.tabs = tabs
    mgr.get_tab_id = MagicMock(side_effect=lambda t: str(id(t)))
    mgr.group_manager = TabGroupManager()
    for name, tab_indices in groups:
        mgr.group_manager.create_group(
            name, initial_tab_ids=[str(id(tabs[i])) for i in tab_indices]
        )
    return mgr


def _menu_items(menu) -> list[tuple[str | None, str | None]]:
    """Flatten a Gio.Menu into ``[(label, action)]`` including sections."""
    items: list = []
    for idx in range(menu.get_n_items()):
        label = menu.get_item_attribute_value(idx, "label")
        action = menu.get_item_attribute_value(idx, "action")
        label_str = label.get_string() if label is not None else None
        action_str = action.get_string() if action is not None else None
        if label_str is not None or action_str is not None:
            items.append((label_str, action_str))
        # Section traversal: items with no label/action are sections
        section = menu.get_item_link(idx, "section")
        if section is not None:
            items.extend(_menu_items(section))
    return items


# ── build_menu_model ────────────────────────────────────────


class TestBuildMenuModel:
    def test_always_offers_move_duplicate_detach(self):
        tab = _StubTab()
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        for required in ("win.move-tab", "win.duplicate-tab", "win.detach-tab"):
            assert required in actions

    def test_tab_color_section_has_no_clear_when_unset(self):
        tab = _StubTab()
        tab.session_item = SessionItem(name="s", session_type="local")
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        assert "win.tab-color" in actions
        assert "win.clear-tab-color" not in actions

    def test_tab_color_section_adds_clear_when_color_set(self):
        tab = _StubTab()
        tab.session_item = SessionItem(
            name="s", session_type="local", tab_color="#f00"
        )
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        assert "win.clear-tab-color" in actions

    def test_tab_without_session_still_shows_tab_color_section(self):
        tab = _StubTab()  # no session_item attached
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        assert "win.tab-color" in actions
        assert "win.clear-tab-color" not in actions

    def test_ungrouped_tab_offers_new_group(self):
        tab = _StubTab()
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        assert "win.new-group-from-tab" in actions
        assert "win.remove-from-group" not in actions

    def test_grouped_tab_offers_remove(self):
        tab = _StubTab()
        mgr = _manager_with_tabs([tab], groups=[("dev", [0])])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        assert "win.remove-from-group" in actions
        assert "win.new-group-from-tab" not in actions

    def test_dynamic_add_to_group_actions_include_every_other_group(self):
        t1, t2 = _StubTab(), _StubTab()
        mgr = _manager_with_tabs([t1, t2], groups=[("dev", [0]), ("prod", [1])])

        actions = [
            a for _, a in _menu_items(build_menu_model(t1, manager=mgr))
            if a is not None
        ]
        # t1 is in "dev"; the menu should offer a single "Add to 'prod'"
        # action pointing at the prod group's id.
        prod = next(g for g in mgr.group_manager.groups if g.name == "prod")
        assert f"win.add-to-group-{prod.id}" in actions
        dev = next(g for g in mgr.group_manager.groups if g.name == "dev")
        # Own group is not repeated in the add-to list.
        assert f"win.add-to-group-{dev.id}" not in actions

    def test_without_groups_only_new_group_action_is_shown(self):
        tab = _StubTab()
        mgr = _manager_with_tabs([tab])

        actions = [a for _, a in _menu_items(build_menu_model(tab, manager=mgr))]
        # There must be no 'add-to-group-*' actions since no group
        # exists yet — the only group-related entry is "new group".
        assert not any(a and a.startswith("win.add-to-group-") for a in actions)


# ── tabs.py delegation ──────────────────────────────────────


class TestTabManagerDelegation:
    def test_on_tab_right_click_still_exists(self):
        from ashyterm.terminal.tabs import TabManager

        # The handler is still the public entry point; the body now
        # routes through tab_context_menu.
        assert callable(TabManager._on_tab_right_click)
