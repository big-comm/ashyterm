"""Tests for SessionFormCollector (session-edit form → SessionItem)."""

from types import SimpleNamespace
from unittest.mock import MagicMock


from ashyterm.sessions.models import SessionItem
from ashyterm.ui.dialogs.session_edit_form import (
    SessionFormCollector,
    selected_to_tri_state,
    tri_state_to_selected,
)


def _entry(text: str) -> MagicMock:
    m = MagicMock()
    m.get_text = MagicMock(return_value=text)
    return m


def _switch(active: bool) -> MagicMock:
    m = MagicMock()
    m.get_active = MagicMock(return_value=active)
    return m


def _combo(selected_idx: int, value: str = "") -> MagicMock:
    m = MagicMock()
    m.get_selected = MagicMock(return_value=selected_idx)
    if value:
        item = MagicMock()
        item.get_string = MagicMock(return_value=value)
        m.get_selected_item = MagicMock(return_value=item)
    else:
        m.get_selected_item = MagicMock(return_value=None)
    return m


def _make_dialog(**overrides) -> SimpleNamespace:
    """Minimal ``SessionEditDialog`` stand-in for collector unit tests."""
    defaults = dict(
        editing_session=SessionItem(name="orig", session_type="local"),
        name_row=_entry("my-session"),
        host_entry=_entry("example.com"),
        user_entry=_entry("alice"),
        port_entry=MagicMock(get_value=MagicMock(return_value=22.0)),
        auth_combo=_combo(0),  # 0 = key
        key_path_entry=_entry("/home/alice/.ssh/id_ed25519"),
        password_entry=_entry("sekret"),
        type_combo=_combo(0),  # 0 = local (not consulted by collector)
        folder_combo=_combo(0, value=""),
        folder_paths_map={},
        post_login_switch=None,
        post_login_entry=None,
        sftp_switch=None,
        sftp_local_entry=None,
        sftp_remote_entry=None,
        port_forwardings=[],
        x11_switch=None,
        local_working_dir_entry=_entry(""),
        local_startup_command_view=None,
        highlighting_customize_switch=None,
        output_highlighting_row=None,
        command_specific_highlighting_row=None,
        cat_colorization_row=None,
        shell_input_highlighting_row=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── tri-state helpers ────────────────────────────────────────


class TestTriStateHelpers:
    def test_none_roundtrip(self):
        assert tri_state_to_selected(None) == 0
        assert selected_to_tri_state(0) is None

    def test_true_roundtrip(self):
        assert tri_state_to_selected(True) == 1
        assert selected_to_tri_state(1) is True

    def test_false_roundtrip(self):
        assert tri_state_to_selected(False) == 2
        assert selected_to_tri_state(2) is False

    def test_out_of_range_selection_defaults_to_false(self):
        assert selected_to_tri_state(9) is False


# ── collect_data: type + name pass-through ───────────────────


class TestCollectDataBasics:
    def test_local_type_and_name_are_set(self):
        dialog = _make_dialog()
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["name"] == "my-session"
        assert data["session_type"] == "local"

    def test_ssh_type_is_set(self):
        dialog = _make_dialog()
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["session_type"] == "ssh"

    def test_name_is_stripped(self):
        dialog = _make_dialog(name_row=_entry("  padded  "))
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["name"] == "padded"


# ── highlighting ─────────────────────────────────────────────


class TestHighlightingSection:
    def test_customize_off_resets_every_key_to_none(self):
        dialog = _make_dialog(
            highlighting_customize_switch=_switch(False),
            output_highlighting_row=_combo(1),
            command_specific_highlighting_row=_combo(1),
            cat_colorization_row=_combo(1),
            shell_input_highlighting_row=_combo(1),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["output_highlighting"] is None
        assert data["command_specific_highlighting"] is None
        assert data["cat_colorization"] is None
        assert data["shell_input_highlighting"] is None

    def test_customize_on_copies_each_row_to_tri_state(self):
        dialog = _make_dialog(
            highlighting_customize_switch=_switch(True),
            output_highlighting_row=_combo(1),  # Enabled
            command_specific_highlighting_row=_combo(2),  # Disabled
            cat_colorization_row=_combo(0),  # Automatic
            shell_input_highlighting_row=_combo(1),  # Enabled
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["output_highlighting"] is True
        assert data["command_specific_highlighting"] is False
        assert data["cat_colorization"] is None
        assert data["shell_input_highlighting"] is True

    def test_missing_rows_are_tolerated(self):
        # A subset of rows is a legitimate intermediate state during UI
        # construction; the collector must not crash.
        dialog = _make_dialog(
            highlighting_customize_switch=_switch(True),
            output_highlighting_row=_combo(1),
            # Other rows intentionally omitted.
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["output_highlighting"] is True


# ── tab color ────────────────────────────────────────────────


class TestTabColor:
    def test_preserves_existing_tab_color(self):
        session = SessionItem(name="s", session_type="local", tab_color="#ff0000")
        dialog = _make_dialog(editing_session=session)
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["tab_color"] == "#ff0000"

    def test_empty_tab_color_becomes_none(self):
        dialog = _make_dialog()  # default session has no tab_color
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["tab_color"] is None


# ── folder assignment ────────────────────────────────────────


class TestFolder:
    def test_folder_path_resolves_from_map(self):
        dialog = _make_dialog(
            folder_combo=_combo(0, value="Production"),
            folder_paths_map={"Production": "/prod"},
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["folder_path"] == "/prod"

    def test_unknown_folder_name_falls_back_to_empty(self):
        dialog = _make_dialog(
            folder_combo=_combo(0, value="Mystery"),
            folder_paths_map={},
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["folder_path"] == ""


# ── post-login ───────────────────────────────────────────────


class TestPostLogin:
    def test_local_sessions_never_enable_post_login(self):
        dialog = _make_dialog(
            post_login_switch=_switch(True),
            post_login_entry=_entry("echo hi"),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["post_login_command_enabled"] is False
        assert data["post_login_command"] == ""

    def test_ssh_enabled_copies_command(self):
        dialog = _make_dialog(
            post_login_switch=_switch(True),
            post_login_entry=_entry("  echo hi  "),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["post_login_command_enabled"] is True
        assert data["post_login_command"] == "echo hi"

    def test_ssh_disabled_blanks_command(self):
        dialog = _make_dialog(
            post_login_switch=_switch(False),
            post_login_entry=_entry("echo hi"),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["post_login_command_enabled"] is False
        assert data["post_login_command"] == ""


# ── sftp ─────────────────────────────────────────────────────


class TestSftp:
    def test_local_session_disables_sftp(self):
        dialog = _make_dialog(
            sftp_switch=_switch(True),
            sftp_local_entry=_entry("/home/me"),
            sftp_remote_entry=_entry("/srv"),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        # _apply_local_fields runs last and flattens SFTP for local type.
        assert data["sftp_session_enabled"] is False

    def test_ssh_session_copies_directories(self):
        dialog = _make_dialog(
            sftp_switch=_switch(True),
            sftp_local_entry=_entry("/home/me"),
            sftp_remote_entry=_entry("/srv"),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["sftp_session_enabled"] is True
        assert data["sftp_local_directory"] == "/home/me"
        assert data["sftp_remote_directory"] == "/srv"


# ── port forwarding + X11 ────────────────────────────────────


class TestPortForwardingAndX11:
    def test_local_session_clears_forwarding(self):
        dialog = _make_dialog(
            port_forwardings=[{"local_port": 1, "remote_port": 2}],
            x11_switch=_switch(True),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["port_forwardings"] == []
        assert data["x11_forwarding"] is False

    def test_ssh_session_deep_copies_forwarding(self):
        fwd = [{"local_port": 1, "remote_port": 2}]
        dialog = _make_dialog(
            port_forwardings=fwd,
            x11_switch=_switch(True),
        )
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["port_forwardings"] == fwd
        # Must be a deep copy so later UI edits don't mutate the snapshot.
        assert data["port_forwardings"] is not fwd
        assert data["port_forwardings"][0] is not fwd[0]
        assert data["x11_forwarding"] is True


# ── ssh fields ───────────────────────────────────────────────


class TestSshFields:
    def test_ssh_key_auth_copies_key_path(self):
        dialog = _make_dialog(auth_combo=_combo(0))  # 0 = key
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["host"] == "example.com"
        assert data["user"] == "alice"
        assert data["port"] == 22
        assert data["auth_type"] == "key"
        assert data["auth_value"] == "/home/alice/.ssh/id_ed25519"

    def test_ssh_password_auth_blanks_auth_value(self):
        dialog = _make_dialog(auth_combo=_combo(1))  # 1 = password
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["auth_type"] == "password"
        # auth_value stays blank in the data dict — the raw password is
        # persisted separately via build_session/get_raw_password.
        assert data["auth_value"] == ""

    def test_ssh_clears_local_fields(self):
        dialog = _make_dialog()
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=False)

        assert data["local_working_directory"] == ""
        assert data["local_startup_command"] == ""


# ── local fields ─────────────────────────────────────────────


class TestLocalFields:
    def test_local_clears_remote_fields(self):
        dialog = _make_dialog()
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["host"] == ""
        assert data["user"] == ""
        assert data["auth_type"] == ""
        assert data["auth_value"] == ""

    def test_local_startup_command_reads_bash_view(self):
        dialog = _make_dialog(local_startup_command_view=_entry("  ls -la  "))
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["local_startup_command"] == "ls -la"

    def test_local_working_dir_reads_entry(self):
        dialog = _make_dialog(local_working_dir_entry=_entry(" /srv "))
        collector = SessionFormCollector(dialog)

        data = collector.collect_data(is_local=True)

        assert data["local_working_directory"] == "/srv"


# ── password + build_session ─────────────────────────────────


class TestBuildSession:
    def test_raw_password_reapplied_on_ssh_password_auth(self, monkeypatch):
        dialog = _make_dialog(
            auth_combo=_combo(1),  # password
            password_entry=_entry("secret!"),
        )
        collector = SessionFormCollector(dialog)

        # Spy on the auth_value setter to avoid keyring round-trip flakiness.
        # SessionItem.auth_value.setter routes through the system keyring,
        # which may or may not be available depending on test ordering. We
        # only care that the collector re-applied the raw password.
        captured: list[str] = []

        from ashyterm.sessions.models import SessionItem as RealSessionItem

        original_setter = RealSessionItem.auth_value.fset

        def fake_setter(self, value):
            captured.append(value)
            original_setter(self, value)

        monkeypatch.setattr(
            RealSessionItem, "auth_value",
            RealSessionItem.auth_value.setter(fake_setter),
        )

        session = collector.build_session(is_local=False)

        assert session.session_type == "ssh"
        assert session.auth_type == "password"
        assert session.uses_password_auth()
        assert "secret!" in captured

    def test_key_auth_does_not_leak_password(self):
        dialog = _make_dialog(
            auth_combo=_combo(0),  # key
            password_entry=_entry("ignored"),
        )
        collector = SessionFormCollector(dialog)

        session = collector.build_session(is_local=False)

        assert session.auth_type == "key"
        assert session.auth_value == "/home/alice/.ssh/id_ed25519"

    def test_get_raw_password_returns_empty_for_local(self):
        dialog = _make_dialog(password_entry=_entry("whatever"))
        collector = SessionFormCollector(dialog)

        # Build a session_data dict that looks local — the helper only
        # returns the typed password when the dict says ssh/password.
        assert (
            collector.get_raw_password({"session_type": "local"}) == ""
        )
        assert (
            collector.get_raw_password(
                {"session_type": "ssh", "auth_type": "key"}
            )
            == ""
        )
        assert (
            collector.get_raw_password(
                {"session_type": "ssh", "auth_type": "password"}
            )
            == "whatever"
        )


# ── integration: dialog still wires the collector ────────────


class TestDialogIntegration:
    def test_dialog_exposes_form_collector(self):
        """``_build_updated_session`` now routes through the collector,
        but the compat delegator ``_collect_session_data`` must keep
        returning the same dict.
        """
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        dialog = _make_dialog()
        dialog.form_collector = SessionFormCollector(dialog)

        data = SessionEditDialog._collect_session_data(dialog, True)

        assert data["name"] == "my-session"
        assert data["session_type"] == "local"

    def test_dialog_get_raw_password_delegates(self):
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        dialog = _make_dialog(password_entry=_entry("pw"))
        dialog.form_collector = SessionFormCollector(dialog)

        out = SessionEditDialog._get_raw_password(
            dialog, {"session_type": "ssh", "auth_type": "password"}
        )
        assert out == "pw"

    def test_dialog_tri_state_statics_are_pure_wrappers(self):
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        assert SessionEditDialog._tri_state_to_selected(None) == 0
        assert SessionEditDialog._selected_to_tri_state(2) is False
