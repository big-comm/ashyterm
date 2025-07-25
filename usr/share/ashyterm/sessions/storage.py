"""
Enhanced storage module for Ashy Terminal sessions and folders.

This module provides robust storage functionality with comprehensive error handling,
backup integration, security validation, and platform-aware file operations.
"""

import json
import os
import threading
import time
import shutil
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from gi.repository import Gio

from .models import SessionItem, SessionFolder

# Import new utility systems
from ..utils.logger import get_logger, log_session_event, log_error_with_context
from ..utils.exceptions import (
    StorageError, StorageReadError, StorageWriteError, StorageCorruptedError,
    SessionValidationError, ValidationError, ConfigError,
    handle_exception, create_error_from_exception, ErrorCategory, ErrorSeverity
)
from ..utils.security import (
    validate_file_path, ensure_secure_file_permissions, 
    ensure_secure_directory_permissions, create_security_auditor
)
from ..utils.platform import (
    get_platform_info, get_config_directory, normalize_path,
    ensure_directory_exists
)
from ..utils.backup import get_backup_manager, BackupType
from ..utils.crypto import is_encryption_available
from ..settings.config import SESSIONS_FILE


class SessionStorageManager:
    """Enhanced storage manager with comprehensive functionality."""
    
    def __init__(self):
        """Initialize storage manager with enhanced capabilities."""
        self.logger = get_logger('ashyterm.sessions.storage')
        self.platform_info = get_platform_info()
        
        # Thread safety
        self._file_lock = threading.RLock()
        self._backup_lock = threading.Lock()
        
        # Storage paths
        self.sessions_file = Path(SESSIONS_FILE)
        self.backup_manager = None
        self.security_auditor = None
        
        # Initialize subsystems
        self._initialize_storage()
        
        # Statistics
        self._stats = {
            'loads': 0,
            'saves': 0,
            'load_errors': 0,
            'save_errors': 0,
            'backups_created': 0,
            'validations_performed': 0
        }
        
        self.logger.info("Session storage manager initialized")
    
    def _initialize_storage(self) -> None:
        """Initialize storage subsystems and verify setup."""
        try:
            # Ensure config directory exists with secure permissions
            config_dir = get_config_directory()
            if not ensure_directory_exists(str(config_dir)):
                raise ConfigError(f"Failed to create config directory: {config_dir}")
            
            ensure_secure_directory_permissions(str(config_dir))
            
            # Initialize backup system
            try:
                self.backup_manager = get_backup_manager()
                self.logger.debug("Backup manager initialized")
            except Exception as e:
                self.logger.warning(f"Backup manager initialization failed: {e}")
            
            # Initialize security auditor
            try:
                self.security_auditor = create_security_auditor()
                self.logger.debug("Security auditor initialized")
            except Exception as e:
                self.logger.warning(f"Security auditor initialization failed: {e}")
            
            # Ensure sessions file has secure permissions if it exists
            if self.sessions_file.exists():
                ensure_secure_file_permissions(str(self.sessions_file))
            
            self.logger.debug("Storage subsystems initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Storage initialization failed: {e}")
            handle_exception(e, "storage initialization", "ashyterm.sessions.storage", reraise=True)
    
    def load_sessions_and_folders_safe(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Safely load sessions and folders with comprehensive error handling.
        
        Returns:
            Tuple of (sessions_data, folders_data) as dictionaries
        """
        with self._file_lock:
            try:
                self.logger.debug("Loading sessions and folders from storage")
                
                if not self.sessions_file.exists():
                    self.logger.info("Sessions file does not exist, returning empty data")
                    return [], []
                
                # Validate file path security
                try:
                    validate_file_path(str(self.sessions_file))
                except Exception as e:
                    raise StorageReadError(str(self.sessions_file), f"File path validation failed: {e}")
                
                # Check file permissions and size
                file_stat = self.sessions_file.stat()
                if file_stat.st_size == 0:
                    self.logger.info("Sessions file is empty, returning empty data")
                    return [], []
                
                if file_stat.st_size > 50 * 1024 * 1024:  # 50MB limit
                    raise StorageReadError(str(self.sessions_file), "File too large (>50MB)")
                
                # Read and parse file
                try:
                    with open(self.sessions_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSON parsing failed: {e}")
                    
                    # Try to recover from backup
                    recovered_data = self._attempt_recovery_from_backup()
                    if recovered_data:
                        return recovered_data
                    
                    raise StorageCorruptedError(str(self.sessions_file), f"Invalid JSON: {e}")
                
                except UnicodeDecodeError as e:
                    raise StorageReadError(str(self.sessions_file), f"Encoding error: {e}")
                
                # Validate data structure
                if not isinstance(data, dict):
                    raise StorageCorruptedError(str(self.sessions_file), "Root data is not a dictionary")
                
                sessions = data.get("sessions", [])
                folders = data.get("folders", [])
                
                # Validate data types
                if not isinstance(sessions, list):
                    self.logger.warning("Sessions data is not a list, converting to empty list")
                    sessions = []
                
                if not isinstance(folders, list):
                    self.logger.warning("Folders data is not a list, converting to empty list")
                    folders = []
                
                # Validate individual items
                validated_sessions = self._validate_sessions_data(sessions)
                validated_folders = self._validate_folders_data(folders)
                
                # Perform security audit if available
                if self.security_auditor:
                    self._audit_loaded_data(validated_sessions, validated_folders)
                
                # Update statistics
                self._stats['loads'] += 1
                self._stats['validations_performed'] += 1
                
                self.logger.info(f"Successfully loaded {len(validated_sessions)} sessions and {len(validated_folders)} folders")
                
                return validated_sessions, validated_folders
                
            except (StorageReadError, StorageCorruptedError):
                self._stats['load_errors'] += 1
                raise
            except Exception as e:
                self._stats['load_errors'] += 1
                self.logger.error(f"Unexpected error loading sessions/folders: {e}")
                log_error_with_context(e, "load sessions and folders", "ashyterm.sessions.storage")
                
                # Try recovery
                recovered_data = self._attempt_recovery_from_backup()
                if recovered_data:
                    return recovered_data
                
                raise StorageReadError(str(self.sessions_file), f"Load failed: {e}")
    
    def _validate_sessions_data(self, sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Validate and sanitize sessions data.
        
        Args:
            sessions: Raw sessions data
            
        Returns:
            Validated sessions data
        """
        validated_sessions = []
        
        for i, session_data in enumerate(sessions):
            try:
                if not isinstance(session_data, dict):
                    self.logger.warning(f"Session {i} is not a dictionary, skipping")
                    continue
                
                # Validate required fields
                required_fields = ['name', 'session_type']
                missing_fields = [field for field in required_fields if field not in session_data]
                
                if missing_fields:
                    self.logger.warning(f"Session {i} missing required fields {missing_fields}, skipping")
                    continue
                
                # Validate session by creating SessionItem (which has validation)
                try:
                    session_item = SessionItem.from_dict(session_data)
                    if session_item.validate():
                        validated_sessions.append(session_item.to_dict())
                        self.logger.debug(f"Session '{session_item.name}' validated successfully")
                    else:
                        errors = session_item.get_validation_errors()
                        self.logger.warning(f"Session '{session_item.name}' validation failed: {errors}")
                except Exception as e:
                    self.logger.warning(f"Session {i} creation failed: {e}")
                    continue
                
            except Exception as e:
                self.logger.error(f"Error validating session {i}: {e}")
                continue
        
        return validated_sessions
    
    def _validate_folders_data(self, folders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Validate and sanitize folders data.
        
        Args:
            folders: Raw folders data
            
        Returns:
            Validated folders data
        """
        validated_folders = []
        
        for i, folder_data in enumerate(folders):
            try:
                if not isinstance(folder_data, dict):
                    self.logger.warning(f"Folder {i} is not a dictionary, skipping")
                    continue
                
                # Validate required fields
                if 'name' not in folder_data:
                    self.logger.warning(f"Folder {i} missing name field, skipping")
                    continue
                
                # Validate folder by creating SessionFolder (which has validation)
                try:
                    folder_item = SessionFolder.from_dict(folder_data)
                    if folder_item.validate():
                        validated_folders.append(folder_item.to_dict())
                        self.logger.debug(f"Folder '{folder_item.name}' validated successfully")
                    else:
                        errors = folder_item.get_validation_errors()
                        self.logger.warning(f"Folder '{folder_item.name}' validation failed: {errors}")
                except Exception as e:
                    self.logger.warning(f"Folder {i} creation failed: {e}")
                    continue
                
            except Exception as e:
                self.logger.error(f"Error validating folder {i}: {e}")
                continue
        
        return validated_folders
    
    def _audit_loaded_data(self, sessions: List[Dict[str, Any]], folders: List[Dict[str, Any]]) -> None:
        """
        Perform security audit on loaded data.
        
        Args:
            sessions: Validated sessions data
            folders: Validated folders data
        """
        try:
            security_issues = 0
            
            for session_data in sessions:
                if session_data.get('session_type') == 'ssh':
                    findings = self.security_auditor.audit_ssh_session(session_data)
                    
                    for finding in findings:
                        if finding['severity'] in ['high', 'critical']:
                            security_issues += 1
                            self.logger.warning(f"Security issue in session '{session_data.get('name')}': {finding['message']}")
            
            if security_issues > 0:
                self.logger.warning(f"Found {security_issues} security issues in loaded sessions")
            else:
                self.logger.debug("Security audit completed - no critical issues found")
                
        except Exception as e:
            self.logger.error(f"Security audit failed: {e}")
    
    def _attempt_recovery_from_backup(self) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        """
        Attempt to recover data from backup.
        
        Returns:
            Recovered data tuple or None if recovery failed
        """
        try:
            if not self.backup_manager:
                self.logger.warning("No backup manager available for recovery")
                return None
            
            # Get recent backups
            backups = self.backup_manager.list_backups()
            if not backups:
                self.logger.warning("No backups available for recovery")
                return None
            
            # Try the most recent backup
            backup_id, backup_metadata = backups[0]
            self.logger.info(f"Attempting recovery from backup: {backup_id}")
            
            # Create temporary directory for restore
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Restore backup
                success = self.backup_manager.restore_backup(backup_id, temp_path)
                if not success:
                    self.logger.error("Backup restore failed")
                    return None
                
                # Try to load from restored file
                restored_file = temp_path / "sessions.json"
                if not restored_file.exists():
                    self.logger.error("Restored backup does not contain sessions.json")
                    return None
                
                with open(restored_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                sessions = data.get("sessions", [])
                folders = data.get("folders", [])
                
                self.logger.info(f"Successfully recovered {len(sessions)} sessions and {len(folders)} folders from backup")
                return sessions, folders
                
        except Exception as e:
            self.logger.error(f"Backup recovery failed: {e}")
            return None
    
    def save_sessions_and_folders_safe(self, session_store: Optional[Gio.ListStore] = None, 
                                     folder_store: Optional[Gio.ListStore] = None,
                                     create_backup: bool = True) -> bool:
        """
        Safely save sessions and folders with backup and validation.
        
        Args:
            session_store: Optional store with SessionItem objects
            folder_store: Optional store with SessionFolder objects
            create_backup: Whether to create backup before saving
            
        Returns:
            True if saved successfully
        """
        with self._file_lock:
            try:
                self.logger.debug("Saving sessions and folders to storage")
                
                # Prepare data
                data_to_save = self._prepare_save_data(session_store, folder_store)
                
                # Create backup before saving if requested and file exists
                backup_id = None
                if create_backup and self.sessions_file.exists() and self.backup_manager:
                    backup_id = self._create_pre_save_backup()
                
                # Validate data before saving
                if not self._validate_save_data(data_to_save):
                    raise StorageWriteError(str(self.sessions_file), "Data validation failed")
                
                # Ensure directory exists
                self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
                ensure_secure_directory_permissions(str(self.sessions_file.parent))
                
                # Write to temporary file first (atomic operation)
                temp_file = self.sessions_file.with_suffix('.tmp')
                
                try:
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f, indent=4, ensure_ascii=False)
                    
                    # Ensure file was written correctly
                    if not temp_file.exists() or temp_file.stat().st_size == 0:
                        raise StorageWriteError(str(temp_file), "Temporary file was not written correctly")
                    
                    # Atomic move
                    if self.platform_info.is_windows():
                        # On Windows, remove target file first
                        if self.sessions_file.exists():
                            self.sessions_file.unlink()
                    
                    temp_file.rename(self.sessions_file)
                    
                    # Set secure permissions
                    ensure_secure_file_permissions(str(self.sessions_file))
                    
                except Exception as e:
                    # Clean up temp file on error
                    if temp_file.exists():
                        temp_file.unlink()
                    raise StorageWriteError(str(self.sessions_file), f"File write failed: {e}")
                
                # Verify file was saved correctly
                if not self._verify_saved_file(data_to_save):
                    # Restore from backup if verification fails
                    if backup_id and self.backup_manager:
                        self.logger.error("Save verification failed, attempting restore from backup")
                        try:
                            self.backup_manager.restore_backup(backup_id, self.sessions_file.parent)
                        except Exception as restore_error:
                            self.logger.error(f"Backup restore failed: {restore_error}")
                    
                    raise StorageWriteError(str(self.sessions_file), "Save verification failed")
                
                # Update statistics
                self._stats['saves'] += 1
                if backup_id:
                    self._stats['backups_created'] += 1
                
                # Log successful save
                sessions_count = len(data_to_save.get("sessions", []))
                folders_count = len(data_to_save.get("folders", []))
                
                self.logger.info(f"Successfully saved {sessions_count} sessions and {folders_count} folders")
                log_session_event("storage_saved", f"{sessions_count} sessions, {folders_count} folders", 
                                f"backup: {backup_id is not None}")
                
                return True
                
            except (StorageWriteError, StorageError):
                self._stats['save_errors'] += 1
                raise
            except Exception as e:
                self._stats['save_errors'] += 1
                self.logger.error(f"Unexpected error saving sessions/folders: {e}")
                log_error_with_context(e, "save sessions and folders", "ashyterm.sessions.storage")
                raise StorageWriteError(str(self.sessions_file), f"Save failed: {e}")
    
    def _prepare_save_data(self, session_store: Optional[Gio.ListStore], 
                          folder_store: Optional[Gio.ListStore]) -> Dict[str, Any]:
        """
        Prepare data for saving.
        
        Args:
            session_store: Optional store with SessionItem objects
            folder_store: Optional store with SessionFolder objects
            
        Returns:
            Data dictionary ready for saving
        """
        data_to_save = {}
        
        # Handle sessions
        if session_store is not None:
            sessions_list = []
            for i in range(session_store.get_n_items()):
                session_item = session_store.get_item(i)
                if isinstance(session_item, SessionItem):
                    try:
                        # Validate session before saving
                        if session_item.validate():
                            sessions_list.append(session_item.to_dict())
                        else:
                            errors = session_item.get_validation_errors()
                            self.logger.warning(f"Skipping invalid session '{session_item.name}': {errors}")
                    except Exception as e:
                        self.logger.error(f"Error processing session '{session_item.name}': {e}")
            
            data_to_save["sessions"] = sessions_list
        else:
            # Load existing sessions if store not provided
            try:
                existing_sessions, _ = self.load_sessions_and_folders_safe()
                data_to_save["sessions"] = existing_sessions
            except Exception as e:
                self.logger.warning(f"Could not load existing sessions: {e}")
                data_to_save["sessions"] = []
        
        # Handle folders
        if folder_store is not None:
            folders_list = []
            for i in range(folder_store.get_n_items()):
                folder_item = folder_store.get_item(i)
                if isinstance(folder_item, SessionFolder):
                    try:
                        # Validate folder before saving
                        if folder_item.validate():
                            folders_list.append(folder_item.to_dict())
                        else:
                            errors = folder_item.get_validation_errors()
                            self.logger.warning(f"Skipping invalid folder '{folder_item.name}': {errors}")
                    except Exception as e:
                        self.logger.error(f"Error processing folder '{folder_item.name}': {e}")
            
            data_to_save["folders"] = folders_list
        else:
            # Load existing folders if store not provided
            try:
                _, existing_folders = self.load_sessions_and_folders_safe()
                data_to_save["folders"] = existing_folders
            except Exception as e:
                self.logger.warning(f"Could not load existing folders: {e}")
                data_to_save["folders"] = []
        
        return data_to_save
    
    def _validate_save_data(self, data: Dict[str, Any]) -> bool:
        """
        Validate data before saving.
        
        Args:
            data: Data to validate
            
        Returns:
            True if data is valid
        """
        try:
            # Basic structure validation
            if not isinstance(data, dict):
                self.logger.error("Save data is not a dictionary")
                return False
            
            if "sessions" not in data or "folders" not in data:
                self.logger.error("Save data missing required keys (sessions, folders)")
                return False
            
            if not isinstance(data["sessions"], list) or not isinstance(data["folders"], list):
                self.logger.error("Sessions or folders data is not a list")
                return False
            
            # Validate sessions
            for i, session_data in enumerate(data["sessions"]):
                if not isinstance(session_data, dict):
                    self.logger.error(f"Session {i} is not a dictionary")
                    return False
                
                if not session_data.get("name"):
                    self.logger.error(f"Session {i} has no name")
                    return False
            
            # Validate folders
            for i, folder_data in enumerate(data["folders"]):
                if not isinstance(folder_data, dict):
                    self.logger.error(f"Folder {i} is not a dictionary")
                    return False
                
                if not folder_data.get("name"):
                    self.logger.error(f"Folder {i} has no name")
                    return False
            
            self.logger.debug("Save data validation passed")
            return True
            
        except Exception as e:
            self.logger.error(f"Save data validation failed: {e}")
            return False
    
    def _create_pre_save_backup(self) -> Optional[str]:
        """
        Create backup before saving.
        
        Returns:
            Backup ID or None if backup failed
        """
        try:
            with self._backup_lock:
                if not self.sessions_file.exists():
                    return None
                
                backup_id = self.backup_manager.create_backup(
                    [self.sessions_file],
                    BackupType.AUTOMATIC,
                    "Pre-save backup"
                )
                
                if backup_id:
                    self.logger.debug(f"Created pre-save backup: {backup_id}")
                else:
                    self.logger.warning("Pre-save backup creation failed")
                
                return backup_id
                
        except Exception as e:
            self.logger.error(f"Pre-save backup failed: {e}")
            return None
    
    def _verify_saved_file(self, expected_data: Dict[str, Any]) -> bool:
        """
        Verify that the saved file contains the expected data.
        
        Args:
            expected_data: Data that was supposed to be saved
            
        Returns:
            True if verification passed
        """
        try:
            if not self.sessions_file.exists():
                self.logger.error("Saved file does not exist")
                return False
            
            # Read back the saved file
            with open(self.sessions_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            
            # Compare counts
            expected_sessions = len(expected_data.get("sessions", []))
            expected_folders = len(expected_data.get("folders", []))
            
            saved_sessions = len(saved_data.get("sessions", []))
            saved_folders = len(saved_data.get("folders", []))
            
            if expected_sessions != saved_sessions:
                self.logger.error(f"Session count mismatch: expected {expected_sessions}, got {saved_sessions}")
                return False
            
            if expected_folders != saved_folders:
                self.logger.error(f"Folder count mismatch: expected {expected_folders}, got {saved_folders}")
                return False
            
            self.logger.debug("Save verification passed")
            return True
            
        except Exception as e:
            self.logger.error(f"Save verification failed: {e}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get storage statistics.
        
        Returns:
            Dictionary with statistics
        """
        stats = self._stats.copy()
        stats.update({
            'sessions_file': str(self.sessions_file),
            'file_exists': self.sessions_file.exists(),
            'file_size': self.sessions_file.stat().st_size if self.sessions_file.exists() else 0,
            'backup_available': self.backup_manager is not None,
            'security_audit_available': self.security_auditor is not None,
            'encryption_available': is_encryption_available(),
            'platform': self.platform_info.platform_type.value
        })
        return stats


# Global storage manager instance
_storage_manager: Optional[SessionStorageManager] = None
_storage_lock = threading.Lock()


def get_storage_manager() -> SessionStorageManager:
    """
    Get the global storage manager instance (thread-safe singleton).
    
    Returns:
        SessionStorageManager instance
    """
    global _storage_manager
    
    if _storage_manager is None:
        with _storage_lock:
            if _storage_manager is None:
                _storage_manager = SessionStorageManager()
    
    return _storage_manager


def load_sessions_and_folders() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Load sessions and folders from JSON file with enhanced error handling.
    
    Returns:
        Tuple of (sessions_data, folders_data) as dictionaries
    """
    storage_manager = get_storage_manager()
    return storage_manager.load_sessions_and_folders_safe()


def save_sessions_and_folders(session_store: Optional[Gio.ListStore] = None, 
                            folder_store: Optional[Gio.ListStore] = None,
                            create_backup: bool = True) -> bool:
    """
    Save sessions and folders to JSON file with enhanced safety.
    
    Args:
        session_store: Optional store with SessionItem objects
        folder_store: Optional store with SessionFolder objects
        create_backup: Whether to create backup before saving
        
    Returns:
        True if saved successfully
    """
    storage_manager = get_storage_manager()
    return storage_manager.save_sessions_and_folders_safe(session_store, folder_store, create_backup)


def load_sessions_to_store(session_store: Gio.ListStore) -> None:
    """
    Load sessions from file and populate the given store with error handling.
    
    Args:
        session_store: Store to populate with SessionItem objects
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        sessions_data, _ = load_sessions_and_folders()
        loaded_count = 0
        
        for session_dict in sessions_data:
            try:
                session_item = SessionItem.from_dict(session_dict)
                if session_item.validate():
                    session_store.append(session_item)
                    loaded_count += 1
                else:
                    errors = session_item.get_validation_errors()
                    logger.warning(f"Skipping invalid session '{session_item.name}': {errors}")
            except Exception as e:
                logger.error(f"Error loading session: {e}")
        
        logger.info(f"Loaded {loaded_count} sessions to store")
        
    except Exception as e:
        logger.error(f"Failed to load sessions to store: {e}")
        handle_exception(e, "load sessions to store", "ashyterm.sessions.storage")


def load_folders_to_store(folder_store: Gio.ListStore) -> None:
    """
    Load folders from file and populate the given store with error handling.
    
    Args:
        folder_store: Store to populate with SessionFolder objects
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        _, folders_data = load_sessions_and_folders()
        loaded_count = 0
        
        for folder_dict in folders_data:
            try:
                folder_item = SessionFolder.from_dict(folder_dict)
                if folder_item.validate():
                    folder_store.append(folder_item)
                    loaded_count += 1
                else:
                    errors = folder_item.get_validation_errors()
                    logger.warning(f"Skipping invalid folder '{folder_item.name}': {errors}")
            except Exception as e:
                logger.error(f"Error loading folder: {e}")
        
        logger.info(f"Loaded {loaded_count} folders to store")
        
    except Exception as e:
        logger.error(f"Failed to load folders to store: {e}")
        handle_exception(e, "load folders to store", "ashyterm.sessions.storage")


def find_session_by_name(session_store: Gio.ListStore, name: str) -> Optional[SessionItem]:
    """
    Find a session by name in the store with error handling.
    
    Args:
        session_store: Store to search
        name: Session name to find
        
    Returns:
        SessionItem if found, None otherwise
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        for i in range(session_store.get_n_items()):
            session = session_store.get_item(i)
            if isinstance(session, SessionItem) and session.name == name:
                return session
        return None
    except Exception as e:
        logger.error(f"Error finding session by name '{name}': {e}")
        return None


def find_folder_by_path(folder_store: Gio.ListStore, path: str) -> Optional[SessionFolder]:
    """
    Find a folder by path in the store with error handling.
    
    Args:
        folder_store: Store to search
        path: Folder path to find
        
    Returns:
        SessionFolder if found, None otherwise
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        for i in range(folder_store.get_n_items()):
            folder = folder_store.get_item(i)
            if isinstance(folder, SessionFolder) and folder.path == path:
                return folder
        return None
    except Exception as e:
        logger.error(f"Error finding folder by path '{path}': {e}")
        return None


def get_sessions_in_folder(session_store: Gio.ListStore, folder_path: str) -> List[SessionItem]:
    """
    Get all sessions within a specific folder with error handling.
    
    Args:
        session_store: Store to search
        folder_path: Path of the folder
        
    Returns:
        List of SessionItem objects in the folder
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        sessions = []
        for i in range(session_store.get_n_items()):
            session = session_store.get_item(i)
            if isinstance(session, SessionItem) and session.folder_path == folder_path:
                sessions.append(session)
        return sessions
    except Exception as e:
        logger.error(f"Error getting sessions in folder '{folder_path}': {e}")
        return []


def get_subfolders(folder_store: Gio.ListStore, parent_path: str) -> List[SessionFolder]:
    """
    Get all direct subfolders of a given path with error handling.
    
    Args:
        folder_store: Store to search
        parent_path: Path of the parent folder
        
    Returns:
        List of SessionFolder objects that are direct children
    """
    logger = get_logger('ashyterm.sessions.storage')
    
    try:
        subfolders = []
        for i in range(folder_store.get_n_items()):
            folder = folder_store.get_item(i)
            if isinstance(folder, SessionFolder) and folder.parent_path == parent_path:
                subfolders.append(folder)
        return subfolders
    except Exception as e:
        logger.error(f"Error getting subfolders of '{parent_path}': {e}")
        return []


def create_emergency_backup() -> Optional[str]:
    """
    Create an emergency backup of current sessions file.
    
    Returns:
        Backup ID if successful, None otherwise
    """
    try:
        storage_manager = get_storage_manager()
        if storage_manager.backup_manager and storage_manager.sessions_file.exists():
            return storage_manager.backup_manager.create_backup(
                [storage_manager.sessions_file],
                BackupType.MANUAL,
                "Emergency backup"
            )
    except Exception as e:
        logger = get_logger('ashyterm.sessions.storage')
        logger.error(f"Emergency backup failed: {e}")
    
    return None


def get_storage_statistics() -> Dict[str, Any]:
    """
    Get storage system statistics.
    
    Returns:
        Dictionary with statistics
    """
    try:
        storage_manager = get_storage_manager()
        return storage_manager.get_statistics()
    except Exception as e:
        logger = get_logger('ashyterm.sessions.storage')
        logger.error(f"Failed to get storage statistics: {e}")
        return {'error': str(e)}