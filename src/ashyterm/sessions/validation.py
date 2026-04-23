# ashyterm/sessions/validation.py

from gi.repository import Gio

from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .results import OperationResult


def validate_session_for_add(
    session: SessionItem, session_store: Gio.ListStore, folder_store: Gio.ListStore
) -> OperationResult:
    """Reject a new session on model errors, name collisions, or missing folder."""
    if not session.validate():
        errors = session.get_validation_errors()
        return OperationResult(
            False, _("Session validation failed: {}").format(", ".join(errors))
        )

    for i in range(session_store.get_n_items()):
        existing_session = session_store.get_item(i)
        if (
            existing_session.name == session.name
            and existing_session.folder_path == session.folder_path
        ):
            return OperationResult(
                False,
                _(
                    "A session with the name '{name}' already exists in this folder."
                ).format(name=session.name),
            )

    if session.folder_path:
        folder_exists = False
        for i in range(folder_store.get_n_items()):
            if folder_store.get_item(i).path == session.folder_path:
                folder_exists = True
                break
        if not folder_exists:
            return OperationResult(
                False,
                _("The target folder '{folder}' does not exist.").format(
                    folder=session.folder_path
                ),
            )

    return OperationResult(True, "Validation successful.")


def validate_folder_for_add(
    folder: SessionFolder, folder_store: Gio.ListStore
) -> OperationResult:
    """Reject a new folder on model errors, duplicate path, or missing parent."""
    if not folder.validate():
        errors = folder.get_validation_errors()
        return OperationResult(
            False, _("Folder validation failed: {}").format(", ".join(errors))
        )

    for i in range(folder_store.get_n_items()):
        if folder_store.get_item(i).path == folder.path:
            return OperationResult(
                False,
                _("A folder with the path '{path}' already exists.").format(
                    path=folder.path
                ),
            )

    if folder.parent_path:
        parent_exists = False
        for i in range(folder_store.get_n_items()):
            if folder_store.get_item(i).path == folder.parent_path:
                parent_exists = True
                break
        if not parent_exists:
            return OperationResult(
                False,
                _("The parent folder '{folder}' does not exist.").format(
                    folder=folder.parent_path
                ),
            )

    return OperationResult(True, "Validation successful.")
