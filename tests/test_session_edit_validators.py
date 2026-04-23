"""Tests for session_edit_validators."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.ui.dialogs.session_edit_validators import (
    validate_hostname,
    validate_port_forward,
    validate_post_login,
    validate_sftp_directory,
    validate_ssh_bundle,
    validate_ssh_key,
)


class _FakeEntry:
    def __init__(self, text: str = ""):
        self._text = text
        self._classes: set[str] = set()
        self.add_css_class = MagicMock(side_effect=self._classes.add)
        self.remove_css_class = MagicMock(side_effect=self._classes.discard)

    def get_text(self) -> str:
        return self._text


class _FakeSwitch:
    def __init__(self, active: bool):
        self._active = active

    def get_active(self) -> bool:
        return self._active


def _make_dialog(**overrides) -> SimpleNamespace:
    """Minimal SessionEditDialog-shaped stub."""
    defaults = dict(
        CSS_CLASS_ERROR="error",
        _validation_errors=[],
        _validate_required_field=MagicMock(return_value=True),
        _show_error_dialog=MagicMock(),
        name_row=_FakeEntry("session-name"),
        host_entry=_FakeEntry("example.com"),
        key_path_entry=_FakeEntry(""),
        auth_combo=MagicMock(get_selected=MagicMock(return_value=0)),
        post_login_switch=None,
        post_login_entry=None,
        sftp_switch=None,
        sftp_local_entry=None,
        local_working_dir_entry=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── validate_hostname ────────────────────────────────────────


class TestValidateHostname:
    def test_valid_hostname(self, monkeypatch):
        dialog = _make_dialog(host_entry=_FakeEntry("example.com"))
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_hostname",
            lambda h: None,
        )
        assert validate_hostname(dialog) is True
        dialog.host_entry.remove_css_class.assert_called_with("error")

    def test_invalid_hostname_flags_widget(self, monkeypatch):
        from ashyterm.utils.exceptions import HostnameValidationError

        def raiser(h):
            raise HostnameValidationError(hostname=h, reason="bad host")

        dialog = _make_dialog(host_entry=_FakeEntry("bad/host"))
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_hostname",
            raiser,
        )

        assert validate_hostname(dialog) is False
        dialog.host_entry.add_css_class.assert_called_with("error")
        assert len(dialog._validation_errors) == 1

    def test_empty_hostname_short_circuits_via_required_field(self):
        dialog = _make_dialog(
            host_entry=_FakeEntry(""),
            _validate_required_field=MagicMock(return_value=False),
        )
        assert validate_hostname(dialog) is False
        # Validator bails before touching validate_ssh_hostname.
        dialog._validate_required_field.assert_called_once()


# ── validate_ssh_key ─────────────────────────────────────────


class TestValidateSshKey:
    def test_password_auth_skips_validation(self):
        dialog = _make_dialog(
            auth_combo=MagicMock(get_selected=MagicMock(return_value=1)),
            key_path_entry=_FakeEntry("/anywhere"),
        )
        assert validate_ssh_key(dialog) is True

    def test_empty_key_path_is_accepted(self):
        dialog = _make_dialog(key_path_entry=_FakeEntry(""))
        assert validate_ssh_key(dialog) is True

    def test_valid_key_clears_error_class(self, monkeypatch):
        dialog = _make_dialog(key_path_entry=_FakeEntry("/home/user/.ssh/id"))
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_key_file",
            lambda path: None,
        )
        assert validate_ssh_key(dialog) is True
        dialog.key_path_entry.remove_css_class.assert_called_with("error")

    def test_invalid_key_flags_widget_and_collects_error(self, monkeypatch):
        from ashyterm.utils.exceptions import SSHKeyError

        def raiser(p):
            raise SSHKeyError(key_path=p, reason="missing")

        dialog = _make_dialog(key_path_entry=_FakeEntry("/nope"))
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_key_file",
            raiser,
        )
        assert validate_ssh_key(dialog) is False
        dialog.key_path_entry.add_css_class.assert_called_with("error")
        assert dialog._validation_errors  # message pushed


# ── validate_post_login ──────────────────────────────────────


class TestValidatePostLogin:
    def test_missing_widgets_is_accepting(self):
        dialog = _make_dialog(post_login_switch=None, post_login_entry=None)
        assert validate_post_login(dialog) is True

    def test_disabled_post_login_is_accepting(self):
        dialog = _make_dialog(
            post_login_switch=_FakeSwitch(False),
            post_login_entry=_FakeEntry(""),
        )
        assert validate_post_login(dialog) is True

    def test_enabled_requires_non_empty_command(self):
        dialog = _make_dialog(
            post_login_switch=_FakeSwitch(True),
            post_login_entry=_FakeEntry("   "),
        )
        assert validate_post_login(dialog) is False
        dialog.post_login_entry.add_css_class.assert_called_with("error")
        assert dialog._validation_errors

    def test_enabled_with_command_clears_error(self):
        dialog = _make_dialog(
            post_login_switch=_FakeSwitch(True),
            post_login_entry=_FakeEntry("echo ready"),
        )
        assert validate_post_login(dialog) is True
        dialog.post_login_entry.remove_css_class.assert_called_with("error")


# ── validate_sftp_directory ──────────────────────────────────


class TestValidateSftpDirectory:
    def test_no_switch_is_accepting(self):
        dialog = _make_dialog(sftp_switch=None)
        assert validate_sftp_directory(dialog) is True

    def test_disabled_sftp_is_accepting(self):
        dialog = _make_dialog(
            sftp_switch=_FakeSwitch(False),
            sftp_local_entry=_FakeEntry("/tmp"),
        )
        assert validate_sftp_directory(dialog) is True

    def test_enabled_without_entry_is_accepting(self):
        dialog = _make_dialog(
            sftp_switch=_FakeSwitch(True),
            sftp_local_entry=None,
        )
        assert validate_sftp_directory(dialog) is True

    def test_enabled_delegates_to_directory_validator(self, tmp_path):
        dialog = _make_dialog(
            sftp_switch=_FakeSwitch(True),
            sftp_local_entry=_FakeEntry(str(tmp_path)),
        )
        assert validate_sftp_directory(dialog) is True


# ── validate_ssh_bundle ──────────────────────────────────────


class TestValidateSshBundle:
    def test_surfaces_all_failures_in_one_dialog(self, monkeypatch):
        from ashyterm.utils.exceptions import (
            HostnameValidationError,
            SSHKeyError,
        )

        dialog = _make_dialog(
            host_entry=_FakeEntry("bad"),
            key_path_entry=_FakeEntry("/missing"),
            auth_combo=MagicMock(get_selected=MagicMock(return_value=0)),
            post_login_switch=_FakeSwitch(True),
            post_login_entry=_FakeEntry(""),  # also invalid
        )

        def bad_host(h):
            raise HostnameValidationError(hostname=h, reason="nope")

        def bad_key(p):
            raise SSHKeyError(key_path=p, reason="missing")

        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_hostname",
            bad_host,
        )
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_key_file",
            bad_key,
        )

        assert validate_ssh_bundle(dialog) is False
        # All three validators should have run and collected errors.
        assert len(dialog._validation_errors) >= 3
        dialog._show_error_dialog.assert_called_once()

    def test_all_valid_returns_true(self, monkeypatch):
        dialog = _make_dialog(
            host_entry=_FakeEntry("example.com"),
            key_path_entry=_FakeEntry(""),
            auth_combo=MagicMock(get_selected=MagicMock(return_value=0)),
        )
        monkeypatch.setattr(
            "ashyterm.ui.dialogs.session_edit_validators.validate_ssh_hostname",
            lambda h: None,
        )
        assert validate_ssh_bundle(dialog) is True
        dialog._show_error_dialog.assert_not_called()


# ── validate_port_forward ────────────────────────────────────


class TestValidatePortForward:
    def test_valid_entry_returns_no_errors(self):
        errors = validate_port_forward(
            {
                "local_port": 8080,
                "remote_port": 80,
                "local_host": "localhost",
            }
        )
        assert errors == []

    # Error messages go through the translation layer, so assert on the
    # count rather than the literal text (tests run under whatever
    # locale is active).
    def test_privileged_local_port_rejected(self):
        errors = validate_port_forward(
            {"local_port": 80, "remote_port": 80, "local_host": "localhost"}
        )
        assert len(errors) == 1

    def test_local_port_above_range_rejected(self):
        errors = validate_port_forward(
            {"local_port": 70000, "remote_port": 80, "local_host": "localhost"}
        )
        assert len(errors) == 1

    def test_remote_port_zero_rejected(self):
        errors = validate_port_forward(
            {"local_port": 8080, "remote_port": 0, "local_host": "localhost"}
        )
        assert len(errors) == 1

    def test_missing_local_host_rejected(self):
        errors = validate_port_forward(
            {"local_port": 8080, "remote_port": 80, "local_host": ""}
        )
        assert len(errors) == 1

    def test_multiple_errors_reported_together(self):
        errors = validate_port_forward(
            {"local_port": 22, "remote_port": 0, "local_host": ""}
        )
        # Three independent issues, three messages.
        assert len(errors) == 3


# ── dialog delegation ────────────────────────────────────────


class TestDialogDelegation:
    def test_dialog_validator_delegators_exist(self):
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        for name in (
            "_validate_basic_fields",
            "_validate_local_fields",
            "_validate_hostname_field",
            "_validate_ssh_key_field",
            "_validate_post_login_field",
            "_validate_sftp_directory_field",
            "_validate_ssh_fields",
            "_validate_port_forward_data",
        ):
            assert callable(getattr(SessionEditDialog, name))
