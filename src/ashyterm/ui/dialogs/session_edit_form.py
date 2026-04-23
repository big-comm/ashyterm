# ashyterm/ui/dialogs/session_edit_form.py
"""Pure-data collection layer for SessionEditDialog.

The dialog exposes ~30 GTK widgets bound to a SessionItem. Turning
that widget soup back into a validated SessionItem is pure
transformation: read widgets → dict → SessionItem. This module owns
that flow so the dialog stays focused on layout and event wiring,
and so the transformation rules stay unit-testable without mounting
the dialog.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Optional

from ...sessions.models import SessionItem

if TYPE_CHECKING:
    from .session_edit_dialog import SessionEditDialog


# ── tri-state helpers (ComboRow selection ↔ Optional[bool]) ──


def tri_state_to_selected(value: Optional[bool]) -> int:
    """Map tri-state (None/True/False) to ComboRow selection index.

    0 → Automatic (None), 1 → Enabled (True), 2 → Disabled (False).
    """
    if value is None:
        return 0
    return 1 if value else 2


def selected_to_tri_state(selected: int) -> Optional[bool]:
    """Inverse of :func:`tri_state_to_selected`."""
    if selected == 0:
        return None
    if selected == 1:
        return True
    return False


HIGHLIGHTING_ROWS = {
    "output_highlighting": "output_highlighting_row",
    "command_specific_highlighting": "command_specific_highlighting_row",
    "cat_colorization": "cat_colorization_row",
    "shell_input_highlighting": "shell_input_highlighting_row",
}


# ── collector ────────────────────────────────────────────────


class SessionFormCollector:
    """Reads SessionEditDialog widget state into a SessionItem dict."""

    def __init__(self, dialog: "SessionEditDialog") -> None:
        self.dialog = dialog

    # Public: the only entry point that matters for callers.
    def collect_data(self, is_local: bool) -> dict:
        """Build the session_data dict by applying each section in order."""
        d = self.dialog
        session_data = d.editing_session.to_dict()
        session_data.update(
            {
                "name": d.name_row.get_text().strip(),
                "session_type": "local" if is_local else "ssh",
            }
        )

        self._apply_highlighting(session_data)
        self._apply_tab_color(session_data)
        self._apply_folder(session_data)
        self._apply_post_login(session_data, is_local)
        self._apply_sftp(session_data, is_local)
        self._apply_port_forwarding(session_data, is_local)

        if is_local:
            self._apply_local_fields(session_data)
        else:
            self._apply_ssh_fields(session_data)

        return session_data

    def get_raw_password(self, session_data: dict) -> str:
        """Return the raw password string for SSH password auth, else ''."""
        if (
            session_data.get("session_type") == "ssh"
            and session_data.get("auth_type") == "password"
        ):
            return self.dialog.password_entry.get_text()
        return ""

    def build_session(self, is_local: bool) -> SessionItem:
        """Collect + construct a SessionItem, re-applying the raw password
        after ``from_dict`` (since from_dict round-trips through keyring).
        """
        session_data = self.collect_data(is_local)
        raw_password = self.get_raw_password(session_data)
        session = SessionItem.from_dict(session_data)
        if session.uses_password_auth() and raw_password:
            session.auth_value = raw_password
        return session

    # ── section writers ──────────────────────────────────────

    def _apply_highlighting(self, session_data: dict) -> None:
        d = self.dialog
        switch = getattr(d, "highlighting_customize_switch", None)
        if not switch or not switch.get_active():
            # Customization off ⇒ Automatic for every highlight key.
            for key in HIGHLIGHTING_ROWS:
                session_data[key] = None
            return
        for data_key, row_attr in HIGHLIGHTING_ROWS.items():
            row = getattr(d, row_attr, None)
            if row is not None:
                session_data[data_key] = selected_to_tri_state(row.get_selected())

    def _apply_tab_color(self, session_data: dict) -> None:
        session_data["tab_color"] = self.dialog.editing_session.tab_color or None

    def _apply_folder(self, session_data: dict) -> None:
        d = self.dialog
        if d.folder_combo and (selected := d.folder_combo.get_selected_item()):
            session_data["folder_path"] = d.folder_paths_map.get(
                selected.get_string(), ""
            )

    def _apply_post_login(self, session_data: dict, is_local: bool) -> None:
        d = self.dialog
        enabled = (
            d.post_login_switch.get_active()
            if d.post_login_switch and not is_local
            else False
        )
        command = d.post_login_entry.get_text().strip() if d.post_login_entry else ""
        session_data["post_login_command_enabled"] = enabled
        session_data["post_login_command"] = command if enabled else ""

    def _apply_sftp(self, session_data: dict, is_local: bool) -> None:
        d = self.dialog
        enabled = (
            d.sftp_switch.get_active()
            if d.sftp_switch and not is_local
            else False
        )
        local_dir = d.sftp_local_entry.get_text().strip() if d.sftp_local_entry else ""
        remote_dir = (
            d.sftp_remote_entry.get_text().strip() if d.sftp_remote_entry else ""
        )
        session_data["sftp_session_enabled"] = enabled
        session_data["sftp_local_directory"] = local_dir
        session_data["sftp_remote_directory"] = remote_dir

    def _apply_port_forwarding(self, session_data: dict, is_local: bool) -> None:
        d = self.dialog
        session_data["port_forwardings"] = (
            copy.deepcopy(d.port_forwardings) if not is_local else []
        )
        session_data["x11_forwarding"] = (
            d.x11_switch.get_active() if d.x11_switch and not is_local else False
        )
        proxy_jump_entry = getattr(d, "proxy_jump_entry", None)
        session_data["proxy_jump"] = (
            proxy_jump_entry.get_text().strip()
            if proxy_jump_entry and not is_local
            else ""
        )

    def _apply_ssh_fields(self, session_data: dict) -> None:
        d = self.dialog
        session_data.update(
            {
                "host": d.host_entry.get_text().strip(),
                "user": d.user_entry.get_text().strip(),
                "port": int(d.port_entry.get_value()),
                "auth_type": (
                    "key" if d.auth_combo.get_selected() == 0 else "password"
                ),
            }
        )
        if session_data["auth_type"] == "key":
            session_data["auth_value"] = d.key_path_entry.get_text().strip()
        else:
            session_data["auth_value"] = ""  # stored in keyring
        session_data["local_working_directory"] = ""
        session_data["local_startup_command"] = ""

    def _apply_local_fields(self, session_data: dict) -> None:
        d = self.dialog
        session_data.update(
            {
                "host": "",
                "user": "",
                "auth_type": "",
                "auth_value": "",
                "sftp_session_enabled": False,
                "port_forwardings": [],
                "x11_forwarding": False,
                "proxy_jump": "",
            }
        )
        session_data["local_working_directory"] = (
            d.local_working_dir_entry.get_text().strip()
            if d.local_working_dir_entry
            else ""
        )
        session_data["local_startup_command"] = (
            d.local_startup_command_view.get_text().strip()
            if d.local_startup_command_view
            else ""
        )
