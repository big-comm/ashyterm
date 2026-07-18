"""Tests for command_toolbar (pinned-command toolbar layout)."""

from unittest.mock import MagicMock, patch


from ashyterm.data.command_manager_models import (
    CommandButton,
    DisplayMode,
    ExecutionMode,
)
from ashyterm.ui.command_toolbar import (
    DEFAULT_DISPLAY_MODE,
    create_toolbar_command_button,
    populate_command_toolbar,
    set_toolbar_display_mode,
    unpin_toolbar_command,
    _ICON_MODES,
    _TEXT_MODES,
)


def _cmd(cmd_id: str = "builtin_ls", name: str = "ls") -> CommandButton:
    return CommandButton(
        id=cmd_id,
        name=name,
        description=f"desc of {name}",
        command_template="ls",
        icon_name="folder-open-symbolic",
        display_mode=DisplayMode.ICON_AND_TEXT,
        execution_mode=ExecutionMode.INSERT_AND_EXECUTE,
        is_builtin=True,
    )


def _mock_manager(pinned=None, pref_side=None):
    """Stand-in for ``get_command_button_manager()``.

    ``pref_side`` optionally overrides what ``get_command_pref`` returns
    per-call; default behavior falls through to the ``default`` argument.
    """
    manager = MagicMock()
    manager.get_pinned_commands = MagicMock(return_value=pinned or [])
    if pref_side is None:
        manager.get_command_pref = MagicMock(
            side_effect=lambda cid, key, default: default
        )
    else:
        manager.get_command_pref = MagicMock(side_effect=pref_side)
    manager.set_command_pref = MagicMock()
    manager.unpin_command = MagicMock()
    return manager


# ── mode constants (contract) ────────────────────────────────


class TestModeConstants:
    def test_default_mode_is_icon_and_text(self):
        assert DEFAULT_DISPLAY_MODE == "icon_and_text"
        assert DEFAULT_DISPLAY_MODE in _ICON_MODES
        assert DEFAULT_DISPLAY_MODE in _TEXT_MODES

    def test_icon_only_skips_text(self):
        assert "icon_only" in _ICON_MODES
        assert "icon_only" not in _TEXT_MODES

    def test_text_only_skips_icon(self):
        assert "text_only" in _TEXT_MODES
        assert "text_only" not in _ICON_MODES


# ── populate_command_toolbar ─────────────────────────────────


class TestPopulateCommandToolbar:
    def test_no_pinned_commands_hides_both_bars(self):
        toolbar = MagicMock()
        toolbar.get_first_child = MagicMock(return_value=None)
        parent_handle = MagicMock()
        manager = _mock_manager(pinned=[])

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=manager,
        ):
            populate_command_toolbar(
                toolbar,
                parent_handle=parent_handle,
                logger=MagicMock(),
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )

        toolbar.set_visible.assert_called_with(False)
        parent_handle.set_visible.assert_called_with(False)
        # Nothing was appended.
        toolbar.append.assert_not_called()

    def test_pinned_commands_show_and_populate(self):
        toolbar = MagicMock()
        toolbar.get_first_child = MagicMock(return_value=None)
        parent_handle = MagicMock()
        pinned = [_cmd("a", "A"), _cmd("b", "B"), _cmd("c", "C")]
        manager = _mock_manager(pinned=pinned)

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=manager,
        ):
            populate_command_toolbar(
                toolbar,
                parent_handle=parent_handle,
                logger=MagicMock(),
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )

        toolbar.set_visible.assert_called_with(True)
        parent_handle.set_visible.assert_called_with(True)
        # One button per pinned command.
        assert toolbar.append.call_count == 3

    def test_clears_existing_children_before_append(self):
        old_a, old_b = MagicMock(), MagicMock()
        toolbar = MagicMock()
        # Walrus-style consumption: first two calls return children, then None.
        toolbar.get_first_child = MagicMock(side_effect=[old_a, old_b, None])
        pinned = [_cmd()]

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=_mock_manager(pinned=pinned),
        ):
            populate_command_toolbar(
                toolbar,
                parent_handle=MagicMock(),
                logger=MagicMock(),
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )

        # Two children were removed before the single new button appended.
        toolbar.remove.assert_any_call(old_a)
        toolbar.remove.assert_any_call(old_b)

    def test_parent_handle_may_be_none(self):
        toolbar = MagicMock()
        toolbar.get_first_child = MagicMock(return_value=None)

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=_mock_manager(pinned=[]),
        ):
            # Must not crash when the caller passes None (e.g. the
            # toolbar isn't wrapped in a WindowHandle in some layouts).
            populate_command_toolbar(
                toolbar,
                parent_handle=None,
                logger=MagicMock(),
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )


# ── create_toolbar_command_button ────────────────────────────


class TestCreateToolbarCommandButton:
    def test_button_remembers_command_and_wires_click(self):
        cmd = _cmd()
        on_click = MagicMock()

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=_mock_manager(),
        ):
            btn = create_toolbar_command_button(
                cmd,
                on_click=on_click,
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )

        assert btn._command is cmd

    def test_tooltip_uses_command_description(self):
        cmd = _cmd(name="probe")
        helper = MagicMock()

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=_mock_manager(),
        ):
            btn = create_toolbar_command_button(
                cmd,
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=helper,
            )

        helper.add_tooltip.assert_called_once_with(btn, "desc of probe")

    def test_display_mode_pref_is_read_for_command(self):
        cmd = _cmd(cmd_id="lookup")
        manager = _mock_manager(
            pref_side=lambda cid, key, default: "icon_only"
        )

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=manager,
        ):
            create_toolbar_command_button(
                cmd,
                on_click=MagicMock(),
                on_right_click=MagicMock(),
                tooltip_helper=MagicMock(),
            )

        manager.get_command_pref.assert_called_once_with(
            "lookup", "toolbar_display_mode", DEFAULT_DISPLAY_MODE
        )


# ── set_toolbar_display_mode / unpin_toolbar_command ─────────


class TestModeMutations:
    def test_set_display_mode_persists_and_refreshes(self):
        cmd = _cmd()
        manager = _mock_manager()
        refresh = MagicMock()

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=manager,
        ):
            set_toolbar_display_mode(cmd, "text_only", refresh=refresh)

        manager.set_command_pref.assert_called_once_with(
            cmd.id, "toolbar_display_mode", "text_only"
        )
        refresh.assert_called_once_with()

    def test_unpin_delegates_to_manager_and_refreshes(self):
        cmd = _cmd()
        manager = _mock_manager()
        refresh = MagicMock()

        with patch(
            "ashyterm.ui.command_toolbar.get_command_button_manager",
            return_value=manager,
        ):
            unpin_toolbar_command(cmd, refresh=refresh)

        manager.unpin_command.assert_called_once_with(cmd.id)
        refresh.assert_called_once_with()


# ── window_ui delegation ─────────────────────────────────────


class TestWindowUiDelegation:
    def test_window_ui_still_exposes_toolbar_methods(self):
        from ashyterm.ui.window_ui import WindowUIBuilder

        for name in (
            "_populate_command_toolbar",
            "_create_toolbar_command_button",
            "_on_toolbar_command_clicked",
            "_on_toolbar_command_right_click",
            "_set_toolbar_display_mode",
            "_unpin_toolbar_command",
        ):
            assert callable(getattr(WindowUIBuilder, name))
