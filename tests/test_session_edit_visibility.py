"""Tests for session_edit_visibility (dialog state-machine)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.ui.dialogs.session_edit_visibility import (
    update_auth_visibility,
    update_local_visibility,
    update_port_forward_state,
    update_post_login_command_state,
    update_sftp_state,
    update_ssh_visibility,
)


def _switch(active: bool = False, sensitive: bool = True) -> MagicMock:
    """Fake Adw.SwitchRow with visible/active/sensitive state."""
    mock = MagicMock()
    mock._active = active
    mock._sensitive = sensitive
    mock.get_active = MagicMock(side_effect=lambda: mock._active)
    mock.set_active = MagicMock(side_effect=lambda v: setattr(mock, "_active", v))
    mock.set_sensitive = MagicMock(
        side_effect=lambda v: setattr(mock, "_sensitive", v)
    )
    mock.get_sensitive = MagicMock(side_effect=lambda: mock._sensitive)
    return mock


def _widget(visible: bool = True) -> MagicMock:
    mock = MagicMock()
    mock._visible = visible
    mock.get_visible = MagicMock(side_effect=lambda: mock._visible)
    mock.set_visible = MagicMock(
        side_effect=lambda v: setattr(mock, "_visible", v)
    )
    return mock


def _combo(selected: int) -> MagicMock:
    mock = MagicMock()
    mock.get_selected = MagicMock(return_value=selected)
    return mock


def _entry() -> MagicMock:
    m = MagicMock()
    m._classes = set()
    m.add_css_class = MagicMock(side_effect=m._classes.add)
    m.remove_css_class = MagicMock(side_effect=m._classes.discard)
    return m


def _dialog(**overrides) -> SimpleNamespace:
    """Fully-populated dialog stub. Overrides replace specific fields."""
    defaults = dict(
        CSS_CLASS_ERROR="error",
        type_combo=_combo(1),  # default: SSH
        ssh_box=_widget(visible=True),
        test_button=_widget(visible=True),
        x11_switch=_switch(active=True),
        sftp_switch=_switch(active=True),
        local_terminal_group=_widget(),
        startup_commands_group=_widget(),
        key_box=_widget(),
        password_box=_widget(),
        auth_combo=_combo(0),  # default: Key
        port_forward_add_row=_widget(),
        port_forward_list_row=_widget(),
        port_forward_list=MagicMock(),
        port_forward_add_button=MagicMock(),
        post_login_switch=_switch(active=True),
        post_login_entry=_entry(),
        post_login_command_container=_widget(),
        sftp_local_entry=_entry(),
        sftp_remote_entry=_entry(),
        sftp_local_row=_widget(),
        sftp_remote_row=_widget(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── update_ssh_visibility ───────────────────────────────────


class TestUpdateSshVisibility:
    def test_no_type_combo_is_noop(self):
        dialog = _dialog(type_combo=None)
        update_ssh_visibility(dialog)  # must not crash

    def test_ssh_shows_ssh_box_and_test_button(self):
        dialog = _dialog(type_combo=_combo(1))
        update_ssh_visibility(dialog)
        assert dialog.ssh_box.get_visible() is True
        assert dialog.test_button.get_visible() is True

    def test_local_hides_ssh_box_and_test_button(self):
        dialog = _dialog(type_combo=_combo(0))
        update_ssh_visibility(dialog)
        assert dialog.ssh_box.get_visible() is False
        assert dialog.test_button.get_visible() is False

    def test_local_disables_and_turns_off_switches(self):
        dialog = _dialog(type_combo=_combo(0))
        update_ssh_visibility(dialog)
        assert dialog.x11_switch.get_sensitive() is False
        assert dialog.x11_switch.get_active() is False
        assert dialog.sftp_switch.get_sensitive() is False
        assert dialog.sftp_switch.get_active() is False

    def test_ssh_keeps_switches_sensitive_and_untouched(self):
        dialog = _dialog(type_combo=_combo(1))
        update_ssh_visibility(dialog)
        assert dialog.x11_switch.get_sensitive() is True
        # Staying on SSH shouldn't flip the active state the user set.
        assert dialog.x11_switch.get_active() is True


# ── update_local_visibility ─────────────────────────────────


class TestUpdateLocalVisibility:
    def test_local_shows_local_terminal_group(self):
        dialog = _dialog(type_combo=_combo(0))
        update_local_visibility(dialog)
        assert dialog.local_terminal_group.get_visible() is True
        assert dialog.startup_commands_group.get_visible() is True

    def test_ssh_hides_local_terminal_group(self):
        dialog = _dialog(type_combo=_combo(1))
        update_local_visibility(dialog)
        assert dialog.local_terminal_group.get_visible() is False

    def test_missing_type_combo_is_treated_as_ssh(self):
        dialog = _dialog(type_combo=None)
        update_local_visibility(dialog)
        # is_local defaults to False ⇒ hide group.
        assert dialog.local_terminal_group.get_visible() is False


# ── update_auth_visibility ──────────────────────────────────


class TestUpdateAuthVisibility:
    def test_key_auth_shows_key_box(self):
        dialog = _dialog(auth_combo=_combo(0))
        update_auth_visibility(dialog)
        assert dialog.key_box.get_visible() is True
        assert dialog.password_box.get_visible() is False

    def test_password_auth_shows_password_box(self):
        dialog = _dialog(auth_combo=_combo(1))
        update_auth_visibility(dialog)
        assert dialog.key_box.get_visible() is False
        assert dialog.password_box.get_visible() is True

    def test_missing_widgets_skip_the_swap(self):
        dialog = _dialog(auth_combo=None)
        # Should not crash; sub-updates still run.
        update_auth_visibility(dialog)


# ── update_port_forward_state ──────────────────────────────


class TestUpdatePortForwardState:
    def test_ssh_shows_port_forward_rows(self):
        dialog = _dialog(type_combo=_combo(1))
        update_port_forward_state(dialog)
        assert dialog.port_forward_add_row.get_visible() is True
        assert dialog.port_forward_list_row.get_visible() is True
        dialog.port_forward_list.set_sensitive.assert_called_with(True)
        dialog.port_forward_add_button.set_sensitive.assert_called_with(True)

    def test_local_hides_port_forward_rows(self):
        dialog = _dialog(type_combo=_combo(0))
        update_port_forward_state(dialog)
        assert dialog.port_forward_add_row.get_visible() is False
        assert dialog.port_forward_list_row.get_visible() is False
        dialog.port_forward_list.set_sensitive.assert_called_with(False)


# ── update_post_login_command_state ────────────────────────


class TestUpdatePostLoginCommandState:
    def test_missing_widgets_is_noop(self):
        dialog = _dialog(post_login_switch=None, post_login_entry=None)
        update_post_login_command_state(dialog)

    def test_container_visible_when_ssh_and_switch_on(self):
        dialog = _dialog(
            type_combo=_combo(1), post_login_switch=_switch(active=True)
        )
        update_post_login_command_state(dialog)
        assert dialog.post_login_switch.get_sensitive() is True
        assert dialog.post_login_command_container.get_visible() is True

    def test_container_hidden_when_switch_off(self):
        dialog = _dialog(
            type_combo=_combo(1), post_login_switch=_switch(active=False)
        )
        update_post_login_command_state(dialog)
        assert dialog.post_login_command_container.get_visible() is False

    def test_container_hidden_when_local(self):
        dialog = _dialog(
            type_combo=_combo(0), post_login_switch=_switch(active=True)
        )
        update_post_login_command_state(dialog)
        assert dialog.post_login_command_container.get_visible() is False
        assert dialog.post_login_switch.get_sensitive() is False

    def test_local_clears_error_class(self):
        dialog = _dialog(
            type_combo=_combo(0),
            post_login_switch=_switch(active=False),
        )
        dialog.post_login_entry._classes.add("error")
        update_post_login_command_state(dialog)
        assert "error" not in dialog.post_login_entry._classes


# ── update_sftp_state ──────────────────────────────────────


class TestUpdateSftpState:
    def test_missing_widgets_is_noop(self):
        dialog = _dialog(sftp_switch=None)
        update_sftp_state(dialog)  # no crash

    def test_ssh_enabled_shows_both_rows(self):
        dialog = _dialog(
            type_combo=_combo(1), sftp_switch=_switch(active=True)
        )
        update_sftp_state(dialog)
        assert dialog.sftp_local_row.get_visible() is True
        assert dialog.sftp_remote_row.get_visible() is True

    def test_switch_off_hides_rows(self):
        dialog = _dialog(
            type_combo=_combo(1), sftp_switch=_switch(active=False)
        )
        update_sftp_state(dialog)
        assert dialog.sftp_local_row.get_visible() is False
        assert dialog.sftp_remote_row.get_visible() is False

    def test_local_session_disables_switch(self):
        dialog = _dialog(
            type_combo=_combo(0), sftp_switch=_switch(active=True)
        )
        update_sftp_state(dialog)
        assert dialog.sftp_switch.get_sensitive() is False

    def test_disabled_state_clears_local_entry_error(self):
        dialog = _dialog(
            type_combo=_combo(0), sftp_switch=_switch(active=True)
        )
        dialog.sftp_local_entry._classes.add("error")
        update_sftp_state(dialog)
        assert "error" not in dialog.sftp_local_entry._classes


# ── dialog delegation ──────────────────────────────────────


class TestDialogDelegation:
    def test_dialog_visibility_delegators_exist(self):
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        for name in (
            "_update_ssh_visibility",
            "_update_local_visibility",
            "_update_auth_visibility",
            "_update_port_forward_state",
            "_update_post_login_command_state",
            "_update_sftp_state",
        ):
            assert callable(getattr(SessionEditDialog, name))
