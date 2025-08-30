# ashyterm/sessions/operations.py

import threading
from typing import Optional, Tuple, Union

from gi.repository import Gio

from ..helpers import generate_unique_name
from ..utils.backup import BackupType, get_backup_manager
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .results import OperationResult
from .storage import get_storage_manager
from .validation import validate_folder_for_add, validate_session_for_add


class SessionOperations:
    """Handles CRUD and organizational operations for sessions and folders."""

    def __init__(
        self,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
    ):
        self.logger = get_logger("ashyterm.sessions.operations")
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager
        self._operation_lock = threading.RLock()
        self.storage_manager = get_storage_manager()
        self.backup_manager = get_backup_manager()

    def add_session(self, session: SessionItem) -> OperationResult:
        """Adds a new session to the store with validation."""
        with self._operation_lock:
            validation_result = validate_session_for_add(
                session, self.session_store, self.folder_store
            )
            if not validation_result.success:
                return validation_result

            self.session_store.append(session)
            if not self._save_changes_with_backup("Session added"):
                self._remove_item_from_store(session)
                return OperationResult(False, _("Failed to save session data."))

            self.logger.info(f"Session added successfully: '{session.name}'")
            return OperationResult(
                True,
                _("Session '{name}' added successfully.").format(name=session.name),
                session,
            )

    def update_session(
        self, position: int, updated_session: SessionItem
    ) -> OperationResult:
        """Updates an existing session."""
        with self._operation_lock:
            original_session = self.session_store.get_item(position)
            if not isinstance(original_session, SessionItem):
                return OperationResult(False, _("Item at position is not a session."))

            original_session.name = updated_session.name
            original_session.session_type = updated_session.session_type
            original_session.host = updated_session.host
            original_session.user = updated_session.user
            original_session.auth_type = updated_session.auth_type
            original_session.auth_value = updated_session.auth_value
            original_session.folder_path = updated_session.folder_path
            original_session.port = updated_session.port

            if not self._save_changes_with_backup("Session updated"):
                return OperationResult(False, _("Failed to save updated session data."))

            self.logger.info(f"Session updated successfully: '{original_session.name}'")
            return OperationResult(
                True,
                _("Session '{name}' updated successfully.").format(
                    name=original_session.name
                ),
                original_session,
            )

    def remove_session(self, session: SessionItem) -> OperationResult:
        """Removes a session from the store."""
        with self._operation_lock:
            position = self._find_item_position(session)
            if position == -1:
                return OperationResult(False, _("Session not found."))

            self.session_store.remove(position)
            if not self._save_changes_with_backup("Session removed"):
                self.session_store.insert(position, session)  # Rollback
                return OperationResult(
                    False, _("Failed to save after session removal.")
                )

            self.logger.info(f"Session removed successfully: '{session.name}'")
            return OperationResult(
                True,
                _("Session '{name}' removed successfully.").format(name=session.name),
            )

    def add_folder(self, folder: SessionFolder) -> OperationResult:
        """Adds a new folder to the store."""
        with self._operation_lock:
            validation_result = validate_folder_for_add(folder, self.folder_store)
            if not validation_result.success:
                return validation_result

            self.folder_store.append(folder)
            if not self._save_changes_with_backup("Folder added"):
                self._remove_item_from_store(folder)
                return OperationResult(False, _("Failed to save folder data."))

            self.logger.info(f"Folder added successfully: '{folder.name}'")
            return OperationResult(
                True,
                _("Folder '{name}' added successfully.").format(name=folder.name),
                folder,
            )

    def update_folder(
        self, position: int, updated_folder: SessionFolder
    ) -> OperationResult:
        """Updates an existing folder and handles path changes for children."""
        with self._operation_lock:
            original_folder = self.folder_store.get_item(position)
            if not isinstance(original_folder, SessionFolder):
                return OperationResult(False, _("Item at position is not a folder."))

            old_path = original_folder.path
            new_path = updated_folder.path

            original_folder.name = updated_folder.name
            original_folder.parent_path = updated_folder.parent_path
            original_folder.path = new_path

            if old_path != new_path:
                self._update_child_paths(old_path, new_path)

            if not self._save_changes_with_backup("Folder updated"):
                return OperationResult(False, _("Failed to save updated folder data."))

            self.logger.info(f"Folder updated successfully: '{original_folder.name}'")
            return OperationResult(
                True,
                _("Folder '{name}' updated successfully.").format(
                    name=original_folder.name
                ),
                original_folder,
            )

    def remove_folder(
        self, folder: SessionFolder, force: bool = False
    ) -> OperationResult:
        """Removes a folder, and optionally its contents."""
        with self._operation_lock:
            if not force and self._folder_has_children(folder.path):
                return OperationResult(False, _("Cannot remove a non-empty folder."))

            if force:
                self._remove_folder_children(folder.path)

            position = self._find_item_position(folder)
            if position == -1:
                return OperationResult(False, _("Folder not found."))

            self.folder_store.remove(position)
            if not self._save_changes_with_backup("Folder removed"):
                return OperationResult(False, _("Failed to save after folder removal."))

            self.logger.info(f"Folder removed successfully: '{folder.name}'")
            return OperationResult(
                True,
                _("Folder '{name}' removed successfully.").format(name=folder.name),
            )

    def move_session_to_folder(
        self, session: SessionItem, target_folder_path: str
    ) -> OperationResult:
        """Moves a session to a different folder."""
        with self._operation_lock:
            if session.folder_path == target_folder_path:
                return OperationResult(True, "Session already in target folder.")

            original_folder = session.folder_path
            session.folder_path = target_folder_path

            if not self._save_changes_with_backup("Session moved"):
                session.folder_path = original_folder  # Rollback
                return OperationResult(False, _("Failed to save after moving session."))

            self.logger.info(
                f"Session '{session.name}' moved to '{target_folder_path or 'root'}'"
            )
            return OperationResult(True, _("Session moved successfully."), session)

    def move_folder(
        self, folder: SessionFolder, target_parent_path: str
    ) -> OperationResult:
        """Moves a folder to a new parent folder."""
        with self._operation_lock:
            if folder.parent_path == target_parent_path:
                return OperationResult(True, "Folder already in target parent.")
            if target_parent_path.startswith(folder.path + "/"):
                return OperationResult(False, _("Cannot move a folder into itself."))

            _found, position = self.find_folder_by_path(folder.path)
            if position == -1:
                return OperationResult(False, _("Folder not found."))

            updated_folder = SessionFolder.from_dict(folder.to_dict())
            updated_folder.parent_path = target_parent_path
            updated_folder.path = (
                f"{target_parent_path}/{updated_folder.name}"
                if target_parent_path
                else f"/{updated_folder.name}"
            )
            return self.update_folder(position, updated_folder)

    def duplicate_session(self, session: SessionItem) -> OperationResult:
        """Duplicates a session, giving it a unique name."""
        with self._operation_lock:
            new_item = SessionItem.from_dict(session.to_dict())
            existing_names = self._get_session_names_in_folder(session.folder_path)
            new_item.name = generate_unique_name(new_item.name, existing_names)
            return self.add_session(new_item)

    def paste_item(
        self,
        item_to_paste: Union[SessionItem, SessionFolder],
        target_folder_path: str,
        is_cut: bool,
    ) -> OperationResult:
        """Pastes an item from clipboard logic (cut/copy)."""
        with self._operation_lock:
            if is_cut:
                if isinstance(item_to_paste, SessionItem):
                    return self.move_session_to_folder(item_to_paste, target_folder_path)
                elif isinstance(item_to_paste, SessionFolder):
                    return self.move_folder(item_to_paste, target_folder_path)
            else:  # Is copy
                if isinstance(item_to_paste, SessionItem):
                    new_item = SessionItem.from_dict(item_to_paste.to_dict())
                    new_item.folder_path = target_folder_path
                    return self.duplicate_session(new_item)
                elif isinstance(item_to_paste, SessionFolder):
                    # Recursive copy is complex, for now, we can just create a new folder
                    new_folder = SessionFolder.from_dict(item_to_paste.to_dict())
                    new_folder.parent_path = target_folder_path
                    new_folder.path = (
                        f"{target_folder_path}/{new_folder.name}"
                        if target_folder_path
                        else f"/{new_folder.name}"
                    )
                    return self.add_folder(new_folder)
            return OperationResult(False, _("Unsupported item type for paste operation."))

    def find_session_by_name_and_path(
        self, name: str, path: str
    ) -> Optional[Tuple[SessionItem, int]]:
        """Finds a session by its name and folder path."""
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.name == name and session.folder_path == path:
                return session, i
        return None, -1

    def find_folder_by_path(self, path: str) -> Optional[Tuple[SessionFolder, int]]:
        """Finds a folder by its full path."""
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if folder.path == path:
                return folder, i
        return None, -1

    def _save_changes_with_backup(self, description: str) -> bool:
        """Saves all data and creates a backup if enabled."""
        success = self.storage_manager.save_sessions_and_folders_safe(
            self.session_store, self.folder_store
        )
        if (
            success
            and self.settings_manager.get("auto_backup_enabled", False)
            and self.settings_manager.get("backup_on_change", True)
        ):
            source_file = self.storage_manager.sessions_file
            if source_file.exists():
                self.backup_manager.create_backup_async(
                    [source_file], BackupType.AUTOMATIC, description
                )
        return success

    def _update_child_paths(self, old_path: str, new_path: str):
        """Updates the paths of all children when a folder is moved or renamed."""
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.folder_path == old_path:
                session.folder_path = new_path

        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if folder.parent_path == old_path:
                folder.parent_path = new_path
                # Recursively update paths of sub-folders
                old_sub_path = folder.path
                new_sub_path = f"{new_path}/{folder.name}"
                folder.path = new_sub_path
                self._update_child_paths(old_sub_path, new_sub_path)

    def _folder_has_children(self, folder_path: str) -> bool:
        """Checks if a folder contains any sessions or subfolders."""
        for i in range(self.session_store.get_n_items()):
            if self.session_store.get_item(i).folder_path == folder_path:
                return True
        for i in range(self.folder_store.get_n_items()):
            if self.folder_store.get_item(i).parent_path == folder_path:
                return True
        return False

    def _remove_folder_children(self, folder_path: str):
        """Recursively removes all sessions and subfolders within a given path."""
        for i in range(self.session_store.get_n_items() - 1, -1, -1):
            if self.session_store.get_item(i).folder_path == folder_path:
                self.session_store.remove(i)

        for i in range(self.folder_store.get_n_items() - 1, -1, -1):
            folder = self.folder_store.get_item(i)
            if folder.parent_path == folder_path:
                self._remove_folder_children(folder.path)
                self.folder_store.remove(i)

    def _find_item_position(
        self, item_to_find: Union[SessionItem, SessionFolder]
    ) -> int:
        """Finds the position of a session or folder in its respective store."""
        store = (
            self.session_store
            if isinstance(item_to_find, SessionItem)
            else self.folder_store
        )
        for i in range(store.get_n_items()):
            if store.get_item(i) == item_to_find:
                return i
        return -1

    def _remove_item_from_store(
        self, item_to_remove: Union[SessionItem, SessionFolder]
    ):
        """Removes an item from its store, used for rolling back failed saves."""
        position = self._find_item_position(item_to_remove)
        if position != -1:
            store = (
                self.session_store
                if isinstance(item_to_remove, SessionItem)
                else self.folder_store
            )
            store.remove(position)

    def _get_session_names_in_folder(self, folder_path: str) -> set:
        """Gets a set of session names within a specific folder."""
        return {s.name for s in self.session_store if s.folder_path == folder_path}
