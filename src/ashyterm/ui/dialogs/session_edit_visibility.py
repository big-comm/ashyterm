# ashyterm/ui/dialogs/session_edit_visibility.py
"""Visibility + sensitivity state machine for ``SessionEditDialog``.

The dialog hides/disables whole sections depending on the session
type (Local vs SSH) and the auth method (Key vs Password). The six
``_update_*`` methods used to live on the dialog but read/write only
the widget attributes it publishes — moving them here keeps the
dialog focused on widget construction and event wiring.

All helpers accept the dialog as first argument and short-circuit
safely when an optional widget isn't present (e.g. ``type_combo``
before the UI finishes building).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session_edit_dialog import SessionEditDialog


# Session-type combo indices — kept here so the whole state machine
# is readable at a glance without cross-referencing the dialog.
_SESSION_TYPE_LOCAL = 0
_SESSION_TYPE_SSH = 1

# Auth combo indices — ``0`` is SSH Key, ``1`` is Password.
_AUTH_TYPE_KEY = 0


def _is_ssh(dialog: "SessionEditDialog") -> bool:
    """True when the session-type combo is on SSH; False otherwise.

    Missing ``type_combo`` (during early widget construction) yields
    False so callers behave as if the session were local.
    """
    if not getattr(dialog, "type_combo", None):
        return False
    return dialog.type_combo.get_selected() == _SESSION_TYPE_SSH


def update_ssh_visibility(dialog: "SessionEditDialog") -> None:
    """Toggle SSH-only widgets + cascade into SSH sub-state updates.

    Off-type switches (``x11``, ``sftp``) are forced OFF when we
    switch away from SSH so the collector doesn't emit stale flags
    on save.
    """
    if not getattr(dialog, "type_combo", None):
        return

    is_ssh = _is_ssh(dialog)

    for widget in (
        getattr(dialog, "ssh_box", None),
        getattr(dialog, "test_button", None),
    ):
        if widget:
            widget.set_visible(is_ssh)

    for switch in (
        getattr(dialog, "x11_switch", None),
        getattr(dialog, "sftp_switch", None),
    ):
        if switch:
            switch.set_sensitive(is_ssh)
            if not is_ssh:
                switch.set_active(False)

    update_port_forward_state(dialog)
    update_post_login_command_state(dialog)
    update_sftp_state(dialog)


def update_local_visibility(dialog: "SessionEditDialog") -> None:
    """Show/hide the Local Terminal section based on session type.

    ``startup_commands_group`` is toggled separately so the layout
    stays stable when the local terminal section is hidden but the
    user already has a long startup-commands expander open.
    """
    is_local = False
    if getattr(dialog, "type_combo", None):
        is_local = dialog.type_combo.get_selected() == _SESSION_TYPE_LOCAL

    if getattr(dialog, "local_terminal_group", None):
        dialog.local_terminal_group.set_visible(is_local)
    if getattr(dialog, "startup_commands_group", None):
        dialog.startup_commands_group.set_visible(is_local)


def update_auth_visibility(dialog: "SessionEditDialog") -> None:
    """Swap between SSH key row and password row, then refresh sub-state.

    The ``key_box`` / ``password_box`` attributes are legacy aliases
    that point at the actual rows; the dialog still publishes them so
    this helper doesn't need to know the newer row names.
    """
    key_box = getattr(dialog, "key_box", None)
    password_box = getattr(dialog, "password_box", None)
    auth_combo = getattr(dialog, "auth_combo", None)

    if key_box and password_box and auth_combo:
        is_key = auth_combo.get_selected() == _AUTH_TYPE_KEY
        key_box.set_visible(is_key)
        password_box.set_visible(not is_key)

    update_port_forward_state(dialog)
    update_post_login_command_state(dialog)
    update_sftp_state(dialog)


def update_port_forward_state(dialog: "SessionEditDialog") -> None:
    """Show port-forward widgets only for SSH sessions."""
    is_ssh = _is_ssh(dialog)

    for row_attr in ("port_forward_add_row", "port_forward_list_row"):
        row = getattr(dialog, row_attr, None)
        if row:
            row.set_visible(is_ssh)

    if getattr(dialog, "port_forward_list", None):
        dialog.port_forward_list.set_sensitive(is_ssh)
    if getattr(dialog, "port_forward_add_button", None):
        dialog.port_forward_add_button.set_sensitive(is_ssh)


def update_post_login_command_state(dialog: "SessionEditDialog") -> None:
    """Enable the post-login command UI only when SSH AND switch is on.

    Leaving SSH mode also clears any validation error already showing
    on the command entry so the user doesn't see stale red highlights.
    """
    switch = getattr(dialog, "post_login_switch", None)
    entry = getattr(dialog, "post_login_entry", None)
    if not switch or not entry:
        return

    is_ssh = _is_ssh(dialog)
    switch.set_sensitive(is_ssh)

    container = getattr(dialog, "post_login_command_container", None)
    if container:
        container.set_visible(switch.get_active() and is_ssh)

    if not is_ssh:
        entry.remove_css_class(dialog.CSS_CLASS_ERROR)


def update_sftp_state(dialog: "SessionEditDialog") -> None:
    """Enable the SFTP section only when SSH AND the SFTP switch is on.

    Wider check than the others because the SFTP rows are inside an
    expander and need four references to be present before we touch
    anything.
    """
    switch = getattr(dialog, "sftp_switch", None)
    local_entry = getattr(dialog, "sftp_local_entry", None)
    remote_entry = getattr(dialog, "sftp_remote_entry", None)
    local_row = getattr(dialog, "sftp_local_row", None)
    remote_row = getattr(dialog, "sftp_remote_row", None)
    if not all((switch, local_entry, remote_entry, local_row, remote_row)):
        return

    is_ssh = _is_ssh(dialog)
    switch.set_sensitive(is_ssh)

    is_enabled = switch.get_active() and is_ssh
    local_row.set_visible(is_enabled)
    remote_row.set_visible(is_enabled)
    if not is_enabled:
        local_entry.remove_css_class(dialog.CSS_CLASS_ERROR)
