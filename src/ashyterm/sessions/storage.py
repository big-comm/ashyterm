# ashyterm/sessions/storage.py

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gi.repository import Gio

from ..settings.config import SESSIONS_FILE
from ..utils.exceptions import (
    StorageCorruptedError,
    StorageError,
    StorageReadError,
    StorageWriteError,
    handle_exception,
)
from ..utils.logger import get_logger, log_error_with_context, log_session_event
from ..utils.platform import (
    ensure_directory_exists,
    get_config_directory,
)
from ..utils.security import (
    create_security_auditor,
    ensure_secure_directory_permissions,
    ensure_secure_file_permissions,
    validate_file_path,
)
from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem


class SessionStorageManager:
    """Enhanced storage manager with comprehensive functionality."""

    def __init__(self):
        self.logger = get_logger("ashyterm.sessions.storage")
        self._file_lock = threading.RLock()
        self.sessions_file = Path(SESSIONS_FILE)
        self.security_auditor = None
        self._initialize_storage()
        self.logger.info("Session storage manager initialized")

    def _initialize_storage(self) -> None:
        """Initialize storage subsystems and verify setup."""
        try:
            config_dir = get_config_directory()
            if not ensure_directory_exists(str(config_dir)):
                raise StorageError(f"Failed to create config directory: {config_dir}")
            ensure_secure_directory_permissions(str(config_dir))
            try:
                self.security_auditor = create_security_auditor()
            except Exception as e:
                self.logger.warning(f"Security auditor initialization failed: {e}")
            if self.sessions_file.exists():
                ensure_secure_file_permissions(str(self.sessions_file))
        except Exception as e:
            self.logger.error(f"Storage initialization failed: {e}")
            handle_exception(
                e, "storage initialization", "ashyterm.sessions.storage", reraise=True
            )

    def _check_file_preconditions(self) -> Optional[Tuple[List, List]]:
        """Check file existence and validate path. Returns empty data if file doesn't exist."""
        if not self.sessions_file.exists():
            self.logger.info("Sessions file does not exist, returning empty data")
            return [], []
        try:
            validate_file_path(str(self.sessions_file))
        except Exception as e:
            raise StorageReadError(
                str(self.sessions_file),
                _("File path validation failed: {}").format(e),
            ) from e
        return None

    def _check_file_size(self) -> Optional[Tuple[List, List]]:
        """Check file size constraints. Returns empty data if file is empty."""
        if self.sessions_file.stat().st_size == 0:
            self.logger.info("Sessions file is empty, returning empty data")
            return [], []
        if self.sessions_file.stat().st_size > 50 * 1024 * 1024:
            raise StorageReadError(str(self.sessions_file), _("File too large (>50MB)"))
        return None

    def _read_json_file(self) -> Dict[str, Any]:
        """Read and parse JSON file with proper error handling."""
        try:
            with open(self.sessions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parsing failed: {e}")
            raise StorageCorruptedError(
                str(self.sessions_file), _("Invalid JSON: {}").format(e)
            ) from e
        except UnicodeDecodeError as e:
            raise StorageReadError(
                str(self.sessions_file), _("Encoding error: {}").format(e)
            ) from e

        if not isinstance(data, dict):
            raise StorageCorruptedError(
                str(self.sessions_file), _("Root data is not a dictionary")
            )
        return data

    def _extract_and_validate_data(
        self, data: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract and validate sessions/folders from loaded data."""
        sessions = data.get("sessions", [])
        folders = data.get("folders", [])

        if not isinstance(sessions, list):
            self.logger.warning("Sessions data is not a list, converting to empty list")
            sessions = []
        if not isinstance(folders, list):
            self.logger.warning("Folders data is not a list, converting to empty list")
            folders = []

        validated_sessions = self._validate_sessions_data(sessions)
        validated_folders = self._validate_folders_data(folders)
        return validated_sessions, validated_folders

    def _schedule_security_audit(
        self, sessions: List[Dict[str, Any]], folders: List[Dict[str, Any]]
    ) -> None:
        """Defer security audit to run after startup completes."""
        if self.security_auditor:
            from gi.repository import GLib

            GLib.timeout_add(
                500,  # 500ms delay - run after startup
                self._audit_loaded_data,
                sessions,
                folders,
            )

    def load_sessions_and_folders_safe(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Safely load sessions and folders with comprehensive error handling."""
        with self._file_lock:
            try:
                early_return = self._check_file_preconditions()
                if early_return is not None:
                    return early_return

                early_return = self._check_file_size()
                if early_return is not None:
                    return early_return

                data = self._read_json_file()
                validated_sessions, validated_folders = self._extract_and_validate_data(
                    data
                )
                self._schedule_security_audit(validated_sessions, validated_folders)

                self.logger.info(
                    f"Successfully loaded {len(validated_sessions)} sessions and {len(validated_folders)} folders"
                )
                return validated_sessions, validated_folders
            except (StorageReadError, StorageCorruptedError):
                raise
            except Exception as e:
                self.logger.error(f"Unexpected error loading sessions/folders: {e}")
                log_error_with_context(
                    e, "load sessions and folders", "ashyterm.sessions.storage"
                )
                raise StorageReadError(
                    str(self.sessions_file), _("Load failed: {}").format(e)
                )

    def _validate_items_data(
        self,
        items: List[Dict[str, Any]],
        item_class,
        item_type_name: str,
        required_fields: List[str],
    ) -> List[Dict[str, Any]]:
        """Generic helper to validate and sanitize items data.

        Args:
            items: List of item dictionaries to validate
            item_class: The class to instantiate (SessionItem or SessionFolder)
            item_type_name: Name for logging ("Session" or "Folder")
            required_fields: List of required field names

        Returns:
            List of validated item dictionaries
        """
        validated_items = []
        for i, item_data in enumerate(items):
            try:
                if not isinstance(item_data, dict):
                    self.logger.warning(
                        f"{item_type_name} {i} is not a dictionary, skipping"
                    )
                    continue
                if not all(field in item_data for field in required_fields):
                    self.logger.warning(
                        f"{item_type_name} {i} missing required fields, skipping"
                    )
                    continue
                try:
                    item = item_class.from_dict(item_data)
                    if item.validate():
                        validated_items.append(item.to_dict())
                    else:
                        self.logger.warning(
                            f"{item_type_name} '{item.name}' validation failed: {item.get_validation_errors()}"
                        )
                except Exception as e:
                    self.logger.warning(f"{item_type_name} {i} creation failed: {e}")
            except Exception as e:
                self.logger.error(f"Error validating {item_type_name.lower()} {i}: {e}")
        return validated_items

    def _validate_sessions_data(
        self, sessions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Validate and sanitize sessions data."""
        return self._validate_items_data(
            sessions, SessionItem, "Session", ["name", "session_type"]
        )

    def _validate_folders_data(
        self, folders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Validate and sanitize folders data."""
        return self._validate_items_data(folders, SessionFolder, "Folder", ["name"])

    def _audit_loaded_data(
        self, sessions: List[Dict[str, Any]], folders: List[Dict[str, Any]]
    ) -> bool:
        """Perform security audit on loaded data. Returns False to not repeat GLib.idle_add."""
        try:
            security_issues = 0
            for session_data in sessions:
                if session_data.get("session_type") == "ssh":
                    for finding in self.security_auditor.audit_ssh_session(
                        session_data
                    ):
                        if finding["severity"] in ["high", "critical"]:
                            security_issues += 1
                            self.logger.warning(
                                f"Security issue in session '{session_data.get('name')}': {finding['message']}"
                            )
            if security_issues > 0:
                self.logger.warning(
                    f"Found {security_issues} security issues in loaded sessions"
                )
        except Exception as e:
            self.logger.error(f"Security audit failed: {e}")
        return False  # Don't repeat idle callback

    def _write_temp_file(self, temp_file: Path, data_to_save: Dict[str, Any]) -> None:
        """Write data to temporary file with proper sync."""
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if not temp_file.exists() or temp_file.stat().st_size == 0:
            raise StorageWriteError(
                str(temp_file),
                _("Temporary file was not written correctly"),
            )

    def _atomic_replace(self, temp_file: Path) -> None:
        """Atomically replace sessions file with temp file."""
        try:
            os.replace(temp_file, self.sessions_file)
            ensure_secure_file_permissions(str(self.sessions_file))
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise StorageWriteError(
                str(self.sessions_file), _("File write failed: {}").format(e)
            ) from e

    def _log_save_success(self, data: Dict[str, Any]) -> None:
        """Log successful save operation."""
        sessions_count = len(data.get("sessions", []))
        folders_count = len(data.get("folders", []))
        self.logger.info(
            f"Successfully saved {sessions_count} sessions and {folders_count} folders"
        )
        log_session_event(
            "storage_saved",
            f"{sessions_count} sessions, {folders_count} folders",
        )

    def _perform_save_operation(self, data_to_save: Dict[str, Any]) -> None:
        """Perform the actual save operation with validation."""
        if not self._validate_save_data(data_to_save):
            raise StorageWriteError(
                str(self.sessions_file), _("Data validation failed")
            )

        self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
        ensure_secure_directory_permissions(str(self.sessions_file.parent))

        temp_file = self.sessions_file.with_suffix(".tmp")
        self._write_temp_file(temp_file, data_to_save)
        self._atomic_replace(temp_file)

        if not self._verify_saved_file(data_to_save):
            raise StorageWriteError(
                str(self.sessions_file), _("Save verification failed")
            )

    def save_sessions_and_folders_safe(
        self,
        session_store: Optional[Gio.ListStore] = None,
        folder_store: Optional[Gio.ListStore] = None,
    ) -> bool:
        """Safely save sessions and folders with backup and validation."""
        with self._file_lock:
            try:
                data_to_save = self._prepare_save_data(session_store, folder_store)
                self._perform_save_operation(data_to_save)
                self._log_save_success(data_to_save)
                return True
            except (StorageWriteError, StorageError):
                raise
            except Exception as e:
                self.logger.error(f"Unexpected error saving sessions/folders: {e}")
                log_error_with_context(
                    e, "save sessions and folders", "ashyterm.sessions.storage"
                )
                raise StorageWriteError(
                    str(self.sessions_file), _("Save failed: {}").format(e)
                )

    def _extract_items_from_store(
        self,
        store: Gio.ListStore,
        item_class,
        item_type_name: str,
    ) -> List[Dict[str, Any]]:
        """Extract and validate items from a GIO ListStore."""
        items_list = []
        for i in range(store.get_n_items()):
            item = store.get_item(i)
            if not isinstance(item, item_class):
                continue
            try:
                if item.validate():
                    items_list.append(item.to_dict())
                else:
                    self.logger.warning(
                        f"Skipping invalid {item_type_name} '{item.name}': {item.get_validation_errors()}"
                    )
            except Exception as e:
                self.logger.error(
                    f"Error processing {item_type_name} '{item.name}': {e}"
                )
        return items_list

    def _get_sessions_data(
        self, session_store: Optional[Gio.ListStore]
    ) -> List[Dict[str, Any]]:
        """Get sessions data from store or load existing."""
        if session_store is not None:
            return self._extract_items_from_store(session_store, SessionItem, "session")
        try:
            sessions, _ = self.load_sessions_and_folders_safe()
            return sessions
        except Exception as e:
            self.logger.warning(f"Could not load existing sessions: {e}")
            return []

    def _get_folders_data(
        self, folder_store: Optional[Gio.ListStore]
    ) -> List[Dict[str, Any]]:
        """Get folders data from store or load existing."""
        if folder_store is not None:
            return self._extract_items_from_store(folder_store, SessionFolder, "folder")
        try:
            _, folders = self.load_sessions_and_folders_safe()
            return folders
        except Exception as e:
            self.logger.warning(f"Could not load existing folders: {e}")
            return []

    def _prepare_save_data(
        self,
        session_store: Optional[Gio.ListStore],
        folder_store: Optional[Gio.ListStore],
    ) -> Dict[str, Any]:
        """Prepare data for saving."""
        return {
            "sessions": self._get_sessions_data(session_store),
            "folders": self._get_folders_data(folder_store),
        }

    def _validate_save_data(self, data: Dict[str, Any]) -> bool:
        """Validate data before saving."""
        try:
            if (
                not isinstance(data, dict)
                or "sessions" not in data
                or "folders" not in data
                or not isinstance(data["sessions"], list)
                or not isinstance(data["folders"], list)
            ):
                self.logger.error("Save data has invalid structure")
                return False
            for i, item in enumerate(data["sessions"] + data["folders"]):
                if not isinstance(item, dict) or not item.get("name"):
                    self.logger.error(f"Item {i} is invalid")
                    return False
            return True
        except Exception as e:
            self.logger.error(f"Save data validation failed: {e}")
            return False

    def _verify_saved_file(self, expected_data: Dict[str, Any]) -> bool:
        """Verify that the saved file contains the expected data."""
        try:
            if not self.sessions_file.exists():
                self.logger.error("Saved file does not exist")
                return False
            with open(self.sessions_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            if len(expected_data.get("sessions", [])) != len(
                saved_data.get("sessions", [])
            ) or len(expected_data.get("folders", [])) != len(
                saved_data.get("folders", [])
            ):
                self.logger.error("Item count mismatch after saving")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Save verification failed: {e}")
            return False


_storage_manager: Optional[SessionStorageManager] = None
_storage_lock = threading.Lock()


def get_storage_manager() -> SessionStorageManager:
    """Get the global storage manager instance (thread-safe singleton)."""
    global _storage_manager
    if _storage_manager is None:
        with _storage_lock:
            if _storage_manager is None:
                _storage_manager = SessionStorageManager()
    return _storage_manager


def load_sessions_and_folders() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load sessions and folders from JSON file."""
    return get_storage_manager().load_sessions_and_folders_safe()


def load_sessions_to_store(
    session_store: Gio.ListStore, sessions_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """Load sessions and populate the given store."""
    logger = get_logger("ashyterm.sessions.storage")
    try:
        if sessions_data is None:
            sessions_data, _ = load_sessions_and_folders()
        loaded_count = 0
        for session_dict in sessions_data:
            try:
                session_item = SessionItem.from_dict(session_dict)
                if session_item.validate():
                    session_store.append(session_item)
                    loaded_count += 1
                else:
                    logger.warning(
                        f"Skipping invalid session '{session_item.name}': {session_item.get_validation_errors()}"
                    )
            except Exception as e:
                logger.error(f"Error loading session: {e}")
        logger.info(f"Loaded {loaded_count} sessions to store")
    except Exception as e:
        logger.error(f"Failed to load sessions to store: {e}")
        handle_exception(e, "load sessions to store", "ashyterm.sessions.storage")


def load_folders_to_store(
    folder_store: Gio.ListStore, folders_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """Load folders and populate the given store."""
    logger = get_logger("ashyterm.sessions.storage")
    try:
        if folders_data is None:
            _, folders_data = load_sessions_and_folders()
        loaded_count = 0
        for folder_dict in folders_data:
            try:
                folder_item = SessionFolder.from_dict(folder_dict)
                if folder_item.validate():
                    folder_store.append(folder_item)
                    loaded_count += 1
                else:
                    logger.warning(
                        f"Skipping invalid folder '{folder_item.name}': {folder_item.get_validation_errors()}"
                    )
            except Exception as e:
                logger.error(f"Error loading folder: {e}")
        logger.info(f"Loaded {loaded_count} folders to store")
    except Exception as e:
        logger.error(f"Failed to load folders to store: {e}")
        handle_exception(e, "load folders to store", "ashyterm.sessions.storage")
