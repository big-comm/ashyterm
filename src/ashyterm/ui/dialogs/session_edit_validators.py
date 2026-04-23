# ashyterm/ui/dialogs/session_edit_validators.py
"""Field-level validators for SessionEditDialog.

Every public entry point here returns ``bool`` (valid/invalid) and, on
failure, both tags the offending widget with ``CSS_CLASS_ERROR`` and
appends a user-facing message to ``dialog._validation_errors``. That's
the contract the dialog's save path depends on — keeping it in one
place makes the rules auditable without needing to mount the dialog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from ...utils.exceptions import HostnameValidationError, SSHKeyError
from ...utils.security import validate_ssh_hostname, validate_ssh_key_file
from ...utils.translation_utils import _
from .base_dialog import validate_directory_path

if TYPE_CHECKING:
    from .session_edit_dialog import SessionEditDialog


def validate_basic(dialog: "SessionEditDialog") -> bool:
    """Validate fields common to both local and SSH sessions (currently: name)."""
    return dialog._validate_required_field(dialog.name_row, _("Session name"))


def validate_local(dialog: "SessionEditDialog") -> bool:
    """Validate local-terminal-only fields (working directory)."""
    if not dialog.local_working_dir_entry:
        return True

    valid = validate_directory_path(
        dialog.local_working_dir_entry,
        dialog._validation_errors,
        _("Working directory must exist and be a folder."),
        allow_empty=True,
    )

    if not valid and dialog._validation_errors:
        dialog._show_error_dialog(
            _("Validation Error"),
            "\n".join(dialog._validation_errors),
        )
    return valid


def validate_hostname(dialog: "SessionEditDialog") -> bool:
    """Validate the SSH host entry."""
    if not dialog._validate_required_field(dialog.host_entry, _("Server Address")):
        return False
    hostname = dialog.host_entry.get_text().strip()
    try:
        validate_ssh_hostname(hostname)
        dialog.host_entry.remove_css_class(dialog.CSS_CLASS_ERROR)
        return True
    except HostnameValidationError as e:
        dialog.host_entry.add_css_class(dialog.CSS_CLASS_ERROR)
        dialog._validation_errors.append(e.user_message)
        return False


def validate_ssh_key(dialog: "SessionEditDialog") -> bool:
    """Validate the SSH key path (only when key auth is selected and set)."""
    if dialog.auth_combo.get_selected() != 0:
        return True
    key_path = dialog.key_path_entry.get_text().strip()
    if not key_path:
        return True
    try:
        validate_ssh_key_file(key_path)
        dialog.key_path_entry.remove_css_class(dialog.CSS_CLASS_ERROR)
        return True
    except SSHKeyError as e:
        dialog.key_path_entry.add_css_class(dialog.CSS_CLASS_ERROR)
        dialog._validation_errors.append(e.user_message)
        return False


def validate_post_login(dialog: "SessionEditDialog") -> bool:
    """Validate the post-login command: when enabled it must be non-empty."""
    if not dialog.post_login_switch or not dialog.post_login_entry:
        return True
    if (
        dialog.post_login_switch.get_active()
        and not dialog.post_login_entry.get_text().strip()
    ):
        dialog.post_login_entry.add_css_class(dialog.CSS_CLASS_ERROR)
        dialog._validation_errors.append(
            _("Post-login command cannot be empty when enabled.")
        )
        return False
    dialog.post_login_entry.remove_css_class(dialog.CSS_CLASS_ERROR)
    return True


def validate_sftp_directory(dialog: "SessionEditDialog") -> bool:
    """Validate the SFTP local directory — only when SFTP is enabled."""
    if not dialog.sftp_switch or not dialog.sftp_switch.get_active():
        return True
    if not dialog.sftp_local_entry:
        return True
    return validate_directory_path(
        dialog.sftp_local_entry,
        dialog._validation_errors,
        _("SFTP local directory must exist and be a directory."),
        allow_empty=True,
    )


def validate_ssh_bundle(dialog: "SessionEditDialog") -> bool:
    """Run every SSH-only validator and surface collected errors at once.

    Running all of them (instead of short-circuiting on the first
    failure) lets the user fix multiple fields in one pass.
    """
    valid = True
    if not validate_hostname(dialog):
        valid = False
    if not validate_ssh_key(dialog):
        valid = False
    if not validate_post_login(dialog):
        valid = False
    if not validate_sftp_directory(dialog):
        valid = False

    if not valid and dialog._validation_errors:
        dialog._show_error_dialog(
            _("SSH Validation Error"),
            _("SSH configuration errors:\n{}").format(
                "\n".join(dialog._validation_errors)
            ),
        )
    return valid


def validate_port_forward(data: dict) -> List[str]:
    """Validate one port-forwarding entry dict. Returns a list of errors."""
    errors: List[str] = []
    local_port = data.get("local_port", 0)
    remote_port = data.get("remote_port", 0)
    local_host = data.get("local_host", "")

    if not (1024 < local_port <= 65535):
        errors.append(
            _(
                "Local port must be between 1025 and 65535 (ports below 1024 "
                "require administrator privileges)."
            )
        )
    if not (1 <= remote_port <= 65535):
        errors.append(_("Remote port must be between 1 and 65535."))
    if not local_host:
        errors.append(_("Local host cannot be empty."))

    return errors
