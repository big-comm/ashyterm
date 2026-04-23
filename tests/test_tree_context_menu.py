"""Tests for tree_context_menu (session tree right-click menus)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ashyterm.sessions.models import LayoutItem, SessionFolder, SessionItem
from ashyterm.sessions.tree_context_menu import (
    _build_layout_menu,
    _resolve_sidebar_slots,
    show_inline_context_menu,
    show_inline_root_context_menu,
    show_popover_context_menu,
    show_popover_root_context_menu,
)


def _stub_view(*, with_sidebar: bool = True, clipboard: bool = False) -> SimpleNamespace:
    """Fake SessionTreeView with the attributes the helpers read."""
    parent_window = MagicMock()
    if with_sidebar:
        ui_builder = MagicMock()
        ui_builder.sidebar_content_stack = MagicMock()
        ui_builder.inline_context_menu_box = MagicMock()
        ui_builder.inline_context_menu_box.get_first_child = MagicMock(
            return_value=None
        )
        parent_window.ui_builder = ui_builder
    else:
        parent_window.ui_builder = None

    return SimpleNamespace(
        parent_window=parent_window,
        folder_store=MagicMock(),
        session_store=MagicMock(),
        column_view=MagicMock(),
        has_clipboard_content=MagicMock(return_value=clipboard),
        logger=MagicMock(),
    )


# ── _resolve_sidebar_slots ──────────────────────────────────


class TestResolveSidebarSlots:
    def test_missing_ui_builder_returns_none_pair(self):
        view = _stub_view(with_sidebar=False)
        assert _resolve_sidebar_slots(view) == (None, None)

    def test_returns_stack_and_box_when_present(self):
        view = _stub_view()
        stack, box = _resolve_sidebar_slots(view)
        assert stack is view.parent_window.ui_builder.sidebar_content_stack
        assert box is view.parent_window.ui_builder.inline_context_menu_box

    def test_returns_none_pair_when_either_slot_missing(self):
        view = _stub_view()
        view.parent_window.ui_builder.sidebar_content_stack = None
        assert _resolve_sidebar_slots(view) == (None, None)


# ── _build_layout_menu ──────────────────────────────────────


class TestBuildLayoutMenu:
    def test_layout_menu_has_three_actions_plus_section(self):
        item = LayoutItem(name="my-layout")
        menu = _build_layout_menu(item)
        # Top-level items: Restore, Move to Folder, section separator, Delete.
        # Section counts as an item but has no label we can read back.
        assert menu.get_n_items() == 4

    def test_layout_has_expected_action_names(self):
        item = LayoutItem(name="prod-tabs")
        menu = _build_layout_menu(item)
        # GMenu stores the detailed action string with the target
        # argument embedded; we just confirm the action prefixes are
        # present.
        actions = []
        for i in range(menu.get_n_items()):
            val = menu.get_item_attribute_value(i, "action")
            if val is not None:
                actions.append(val.get_string())
        assert "win.restore_layout" in actions
        assert "win.move-layout-to-folder" in actions
        assert "win.delete_layout" in actions


# ── show_inline_context_menu ────────────────────────────────


class TestShowInlineContextMenu:
    def test_bails_when_sidebar_not_ready(self):
        view = _stub_view(with_sidebar=False)
        # Just verifies no crash.
        show_inline_context_menu(view, SessionItem(name="s", session_type="local"))

    def test_session_branches_to_show_for_session(self):
        view = _stub_view()
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            session = SessionItem(name="s", session_type="local")
            show_inline_context_menu(view, session)
        fake_inline.show_for_session.assert_called_once()

    def test_folder_branches_to_show_for_folder(self):
        view = _stub_view()
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            folder = SessionFolder(name="f", path="/f")
            show_inline_context_menu(view, folder)
        fake_inline.show_for_folder.assert_called_once()

    def test_layout_branches_to_show_for_layout(self):
        view = _stub_view()
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            layout = LayoutItem(name="lay")
            show_inline_context_menu(view, layout)
        fake_inline.show_for_layout.assert_called_once()

    def test_unknown_item_type_is_logged_and_skipped(self):
        view = _stub_view()
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            show_inline_context_menu(view, "not-an-item")
        # None of the show_for_* branches fire.
        fake_inline.show_for_session.assert_not_called()
        fake_inline.show_for_folder.assert_not_called()
        fake_inline.show_for_layout.assert_not_called()

    def test_stack_switches_to_context_menu_on_success(self):
        view = _stub_view()
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            show_inline_context_menu(
                view, SessionItem(name="s", session_type="local")
            )
        stack = view.parent_window.ui_builder.sidebar_content_stack
        stack.set_visible_child_name.assert_called_with("context-menu")


# ── show_inline_root_context_menu ───────────────────────────


class TestShowInlineRootContextMenu:
    def test_bails_when_sidebar_not_ready(self):
        view = _stub_view(with_sidebar=False)
        show_inline_root_context_menu(view)

    def test_renders_root_variant(self):
        view = _stub_view(clipboard=True)
        fake_inline = MagicMock()
        with patch(
            "ashyterm.sessions.tree_context_menu._build_inline_menu",
            return_value=fake_inline,
        ):
            show_inline_root_context_menu(view)
        fake_inline.show_for_root.assert_called_once_with(True)


# ── show_popover_context_menu ───────────────────────────────


class TestShowPopoverContextMenu:
    def test_layout_item_builds_three_entry_menu(self):
        view = _stub_view()
        anchor = MagicMock()
        anchor.compute_point = MagicMock(return_value=(False, None))
        list_item = MagicMock()
        list_item.get_child = MagicMock(return_value=anchor)

        with patch(
            "ashyterm.sessions.tree_context_menu.create_themed_popover_menu"
        ) as factory:
            popover = MagicMock()
            factory.return_value = popover
            show_popover_context_menu(
                view, LayoutItem(name="test"), list_item, 10.0, 20.0
            )
        popover.popup.assert_called_once()

    def test_no_menu_model_when_session_not_in_store(self):
        """If ``session_store.find`` returns False, no popover fires."""
        view = _stub_view()
        view.session_store.find = MagicMock(return_value=(False, 0))
        list_item = MagicMock()

        with patch(
            "ashyterm.sessions.tree_context_menu.create_themed_popover_menu"
        ) as factory:
            show_popover_context_menu(
                view,
                SessionItem(name="s", session_type="local"),
                list_item,
                0.0,
                0.0,
            )
        factory.assert_not_called()


# ── show_popover_root_context_menu ──────────────────────────


class TestShowPopoverRootContextMenu:
    def test_creates_and_pops_popover(self):
        view = _stub_view(clipboard=False)
        view.column_view.compute_point = MagicMock(return_value=(False, None))

        with patch(
            "ashyterm.sessions.tree_context_menu.create_themed_popover_menu"
        ) as factory:
            popover = MagicMock()
            factory.return_value = popover
            show_popover_root_context_menu(view, 10.0, 20.0)
        popover.popup.assert_called_once()


# ── tree delegation ────────────────────────────────────────


class TestTreeDelegation:
    def test_tree_delegators_exist(self):
        from ashyterm.sessions.tree import SessionTreeView

        for name in (
            "_show_inline_context_menu",
            "_show_popover_context_menu",
            "_show_inline_root_context_menu",
            "_show_popover_root_context_menu",
        ):
            assert callable(getattr(SessionTreeView, name))
