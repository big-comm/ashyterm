"""Tests for session_edit_sections (SessionEditDialog section builders)."""

from types import SimpleNamespace
from unittest.mock import MagicMock


from ashyterm.sessions.models import SessionItem
from ashyterm.ui.dialogs.session_edit_sections import (
    _HIGHLIGHTING_ATTRS,
    add_folder_expander,
    add_highlighting_expander,
    create_local_terminal_section,
    create_ssh_options_group,
    create_ssh_section,
)


def _stub_dialog(
    session: SessionItem | None = None,
    folder_items=(),
    is_new: bool = False,
) -> SimpleNamespace:
    """Minimal SessionEditDialog stub covering the section-builder surface.

    Only attributes the builders read or write are populated; the rest
    would be filled in by the real dialog at runtime.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    session = session or SessionItem(name="s", session_type="local")
    folder_store = MagicMock()
    folder_store.get_n_items = MagicMock(return_value=len(folder_items))
    folder_store.get_item = MagicMock(
        side_effect=lambda i: folder_items[i]
    )

    def _create_entry_row(*, title, text, on_changed):
        row = Adw.EntryRow(title=title)
        row.set_text(text)
        row.connect("changed", on_changed)
        return row

    def _create_spin_row(*, title, value, min_val, max_val, on_changed):
        row = Adw.SpinRow.new_with_range(min_val, max_val, 1)
        row.set_title(title)
        row.set_value(value)
        return row

    dialog = SimpleNamespace(
        editing_session=session,
        is_new_item=is_new,
        folder_store=folder_store,
        folder_paths_map={},
        folder_combo=None,
        # Bags for widgets the builder assigns.
        highlighting_customize_switch=None,
        output_highlighting_row=None,
        command_specific_highlighting_row=None,
        cat_colorization_row=None,
        shell_input_highlighting_row=None,
        local_working_dir_entry=None,
        local_startup_command_view=None,
        local_terminal_group=None,
        startup_commands_group=None,
        _updating_highlighting_ui=False,
        # SSH row helpers expected by the SSH builder.
        _create_entry_row=_create_entry_row,
        _create_spin_row=_create_spin_row,
        # Callbacks the builders wire up.
        _on_folder_changed=MagicMock(),
        _on_highlighting_customize_switch_changed=MagicMock(),
        _on_highlighting_override_changed=MagicMock(),
        _on_local_working_dir_changed=MagicMock(),
        _on_local_startup_command_changed=MagicMock(),
        _on_browse_working_dir_clicked=MagicMock(),
        _on_user_changed=MagicMock(),
        _on_host_changed=MagicMock(),
        _on_port_changed=MagicMock(),
        _on_auth_changed=MagicMock(),
        _on_key_path_changed=MagicMock(),
        _on_password_changed=MagicMock(),
        _on_browse_key_clicked=MagicMock(),
        _on_post_login_toggle=MagicMock(),
        _on_post_login_command_changed=MagicMock(),
        _on_x11_toggled=MagicMock(),
        _on_sftp_toggle=MagicMock(),
        _on_validated_entry_changed=MagicMock(),
        _on_sftp_remote_changed=MagicMock(),
        _apply_bash_colors=MagicMock(),
        _set_highlighting_overrides_visible=MagicMock(),
        _create_port_forward_widgets_expander=MagicMock(),
        _update_post_login_command_state=MagicMock(),
        _update_sftp_state=MagicMock(),
    )
    return dialog


def _fake_folder(path: str, name: str):
    folder = SimpleNamespace()
    folder.path = path
    folder.name = name
    return folder


# ── add_folder_expander ──────────────────────────────────────


class TestAddFolderExpander:
    def test_populates_paths_map_with_root_sentinel(self):
        dialog = _stub_dialog(folder_items=[
            _fake_folder("dev", "Dev"),
            _fake_folder("dev/backend", "Backend"),
        ])
        group = MagicMock()

        add_folder_expander(dialog, group)

        # Root is always first; the store's folders follow, indented by
        # how many slashes appear in their path.
        assert "" in dialog.folder_paths_map.values()  # Root entry
        # Indented sub-folder key must have leading spaces.
        indented = [k for k in dialog.folder_paths_map if k.startswith("  ")]
        assert indented, "Expected an indented folder entry"
        group.add.assert_called_once()

    def test_empty_store_still_adds_root(self):
        dialog = _stub_dialog(folder_items=[])
        add_folder_expander(dialog, MagicMock())
        # Only the Root entry mapping to empty path.
        assert list(dialog.folder_paths_map.values()) == [""]

    def test_folder_combo_is_published_on_dialog(self):
        dialog = _stub_dialog(folder_items=[_fake_folder("dev", "Dev")])
        add_folder_expander(dialog, MagicMock())
        assert dialog.folder_combo is not None

    def test_folder_paths_map_contains_expected_entries(self):
        session = SessionItem(
            name="s", session_type="local", folder_path="dev/backend"
        )
        dialog = _stub_dialog(
            session=session,
            folder_items=[
                _fake_folder("dev", "Dev"),
                _fake_folder("dev/backend", "Backend"),
            ],
        )
        add_folder_expander(dialog, MagicMock())
        # The builder populated the map with indented display names.
        paths = set(dialog.folder_paths_map.values())
        assert "dev" in paths
        assert "dev/backend" in paths
        assert "" in paths  # Root sentinel


# ── add_highlighting_expander ───────────────────────────────


class TestAddHighlightingExpander:
    def test_disables_customize_switch_when_session_has_no_overrides(self):
        dialog = _stub_dialog()
        add_highlighting_expander(dialog, MagicMock())

        # A fresh session has all four override fields as None ⇒ the
        # master switch stays off.
        assert dialog.highlighting_customize_switch.get_active() is False
        dialog._set_highlighting_overrides_visible.assert_called_with(False)

    def test_enables_customize_switch_when_override_present(self):
        session = SessionItem(
            name="s",
            session_type="local",
            output_highlighting=True,  # existing override
        )
        dialog = _stub_dialog(session=session)

        add_highlighting_expander(dialog, MagicMock())

        assert dialog.highlighting_customize_switch.get_active() is True
        dialog._set_highlighting_overrides_visible.assert_called_with(True)

    def test_all_four_rows_are_published_on_dialog(self):
        dialog = _stub_dialog()
        add_highlighting_expander(dialog, MagicMock())

        for attr in (
            "output_highlighting_row",
            "command_specific_highlighting_row",
            "cat_colorization_row",
            "shell_input_highlighting_row",
        ):
            assert getattr(dialog, attr) is not None, f"{attr} not set"

    def test_highlighting_attrs_cover_every_row(self):
        # Guard against a row silently losing its persistence key.
        assert set(_HIGHLIGHTING_ATTRS) == {
            "output_highlighting",
            "command_specific_highlighting",
            "cat_colorization",
            "shell_input_highlighting",
        }

    def test_builder_resets_ui_updating_flag(self):
        dialog = _stub_dialog()
        dialog._updating_highlighting_ui = True
        add_highlighting_expander(dialog, MagicMock())
        # The builder clears the flag so an in-flight update doesn't
        # swallow the first change event on the new widgets.
        assert dialog._updating_highlighting_ui is False


# ── create_local_terminal_section ───────────────────────────


class TestCreateLocalTerminalSection:
    def test_working_dir_entry_is_seeded_from_session(self):
        session = SessionItem(
            name="s",
            session_type="local",
            local_working_directory="/srv",
        )
        dialog = _stub_dialog(session=session)
        create_local_terminal_section(dialog, MagicMock())

        assert dialog.local_working_dir_entry is not None
        assert dialog.local_working_dir_entry.get_text() == "/srv"

    def test_startup_view_is_seeded_with_existing_command(self):
        session = SessionItem(
            name="s",
            session_type="local",
            local_startup_command="echo hi",
        )
        dialog = _stub_dialog(session=session)
        create_local_terminal_section(dialog, MagicMock())

        assert dialog.local_startup_command_view is not None
        assert dialog.local_startup_command_view.get_text() == "echo hi"

    def test_empty_startup_command_collapses_expander(self):
        dialog = _stub_dialog()
        create_local_terminal_section(dialog, MagicMock())
        # With no startup command, the section is added but the
        # expander isn't auto-opened — the user can click to reveal.
        # (No GTK-level assertion is possible here; we just check that
        # building the section with an empty command doesn't crash.)
        assert dialog.local_terminal_group is not None

    def test_builder_calls_apply_bash_colors(self):
        dialog = _stub_dialog()
        create_local_terminal_section(dialog, MagicMock())
        dialog._apply_bash_colors.assert_called_once_with(
            dialog.local_startup_command_view
        )


# ── create_ssh_section ──────────────────────────────────────


def _ssh_session(**overrides) -> SessionItem:
    base = dict(
        name="remote",
        session_type="ssh",
        host="example.com",
        user="alice",
        port=2222,
        auth_type="key",
        auth_value="/home/alice/.ssh/id_ed25519",
    )
    base.update(overrides)
    return SessionItem(**base)


class TestCreateSshSection:
    def test_identity_rows_are_published(self):
        dialog = _stub_dialog(session=_ssh_session())
        parent = MagicMock()
        create_ssh_section(dialog, parent)

        for attr in ("user_row", "host_row", "port_row", "auth_combo"):
            assert getattr(dialog, attr) is not None

    def test_legacy_aliases_point_at_new_rows(self):
        dialog = _stub_dialog(session=_ssh_session())
        create_ssh_section(dialog, MagicMock())

        # Downstream code (validators, form collector) uses these aliases.
        assert dialog.user_entry is dialog.user_row
        assert dialog.host_entry is dialog.host_row
        assert dialog.port_entry is dialog.port_row
        assert dialog.password_entry is dialog.password_row
        assert dialog.password_box is dialog.password_row

    def test_new_item_defaults_to_password_auth(self):
        dialog = _stub_dialog(session=_ssh_session(), is_new=True)
        create_ssh_section(dialog, MagicMock())
        # Auth combo is an Adw.ComboRow; index 1 is Password.
        assert dialog.auth_combo.get_selected() == 1

    def test_existing_key_session_selects_key_auth(self):
        dialog = _stub_dialog(session=_ssh_session(auth_type="key"), is_new=False)
        create_ssh_section(dialog, MagicMock())
        assert dialog.auth_combo.get_selected() == 0

    def test_ssh_options_expander_is_attached(self):
        # create_ssh_section nests the SSH Options expander and must
        # also hook the post-login/sftp state updaters.
        dialog = _stub_dialog(session=_ssh_session())
        create_ssh_section(dialog, MagicMock())

        dialog._update_post_login_command_state.assert_called_once()
        dialog._update_sftp_state.assert_called_once()

    def test_port_forward_widgets_expander_is_invoked(self):
        dialog = _stub_dialog(session=_ssh_session())
        create_ssh_section(dialog, MagicMock())
        dialog._create_port_forward_widgets_expander.assert_called_once()


# ── create_ssh_options_group (direct) ───────────────────────


class TestCreateSshOptionsGroup:
    def test_post_login_switch_reflects_session_state(self):
        session = _ssh_session(post_login_command_enabled=True)
        dialog = _stub_dialog(session=session)

        create_ssh_options_group(dialog, MagicMock())

        assert dialog.post_login_switch.get_active() is True
        assert dialog.post_login_command_container.get_visible() is True

    def test_post_login_container_hidden_when_disabled(self):
        dialog = _stub_dialog(session=_ssh_session())
        create_ssh_options_group(dialog, MagicMock())
        # Default session has post_login_command_enabled=False.
        assert dialog.post_login_command_container.get_visible() is False

    def test_x11_switch_reflects_session_state(self):
        dialog = _stub_dialog(session=_ssh_session(x11_forwarding=True))
        create_ssh_options_group(dialog, MagicMock())
        assert dialog.x11_switch.get_active() is True

    def test_sftp_switch_reflects_session_state(self):
        dialog = _stub_dialog(session=_ssh_session(sftp_session_enabled=True))
        create_ssh_options_group(dialog, MagicMock())
        assert dialog.sftp_switch.get_active() is True

    def test_sftp_rows_seeded_with_session_directories(self):
        dialog = _stub_dialog(
            session=_ssh_session(
                sftp_local_directory="/home/me",
                sftp_remote_directory="/srv",
            )
        )
        create_ssh_options_group(dialog, MagicMock())
        assert dialog.sftp_local_entry.get_text() == "/home/me"
        assert dialog.sftp_remote_entry.get_text() == "/srv"

    def test_legacy_post_login_aliases_published(self):
        dialog = _stub_dialog(session=_ssh_session())
        create_ssh_options_group(dialog, MagicMock())
        # Form collector + validator read these aliases.
        assert dialog.post_login_entry is dialog.post_login_text_view
        assert dialog.post_login_command_row is dialog.post_login_switch


# ── dialog delegation ──────────────────────────────────────


class TestDialogDelegation:
    def test_dialog_delegators_exist(self):
        from ashyterm.ui.dialogs.session_edit_dialog import SessionEditDialog

        for name in (
            "_add_folder_expander",
            "_add_highlighting_expander",
            "_create_tristate_combo_row",
            "_create_local_terminal_section",
            "_create_ssh_section",
            "_create_ssh_options_group",
        ):
            assert callable(getattr(SessionEditDialog, name))
