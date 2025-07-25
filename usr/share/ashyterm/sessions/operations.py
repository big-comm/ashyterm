"""
Enhanced operations module for Ashy Terminal sessions and folders.

This module provides robust CRUD and organizational operations with comprehensive
error handling, validation, security auditing, and backup integration.
"""

import os
import threading
import time
from typing import Optional, Set, List, Tuple, Dict, Any, Union
from gi.repository import Gio

from .models import SessionItem, SessionFolder
from .storage import save_sessions_and_folders, get_storage_manager

# Import new utility systems
from ..utils.logger import get_logger, log_session_event, log_error_with_context
from ..utils.exceptions import (
    SessionError, SessionNotFoundError, SessionValidationError, SessionDuplicateError,
    ValidationError, AshyTerminalError, ErrorCategory, ErrorSeverity,
    handle_exception, create_error_from_exception
)
from ..utils.security import (
    validate_session_data, sanitize_session_name, sanitize_folder_name,
    create_security_auditor
)
from ..utils.platform import normalize_path, get_platform_info
from ..utils.backup import get_backup_manager, BackupType
from ..utils import generate_unique_name


class OperationResult:
    """Result object for operations with detailed information."""
    
    def __init__(self, success: bool, message: str = "", 
                 item: Optional[Union[SessionItem, SessionFolder]] = None,
                 warnings: Optional[List[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None):
        """
        Initialize operation result.
        
        Args:
            success: Whether operation succeeded
            message: Result message
            item: Resulting item if applicable
            warnings: List of warnings
            metadata: Additional metadata
        """
        self.success = success
        self.message = message
        self.item = item
        self.warnings = warnings or []
        self.metadata = metadata or {}
        self.timestamp = time.time()
    
    def add_warning(self, warning: str) -> None:
        """Add a warning to the result."""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            'success': self.success,
            'message': self.message,
            'warnings': self.warnings,
            'metadata': self.metadata,
            'timestamp': self.timestamp
        }


class SessionOperations:
    """Enhanced CRUD and organizational operations for sessions and folders."""
    
    def __init__(self, session_store: Gio.ListStore, folder_store: Gio.ListStore):
        """
        Initialize session operations with comprehensive functionality.
        
        Args:
            session_store: Store containing SessionItem objects
            folder_store: Store containing SessionFolder objects
        """
        self.logger = get_logger('ashyterm.sessions.operations')
        self.session_store = session_store
        self.folder_store = folder_store
        self.platform_info = get_platform_info()
        
        # Thread safety
        self._operation_lock = threading.RLock()
        self._validation_lock = threading.Lock()
        
        # Managers
        self.storage_manager = get_storage_manager()
        self.backup_manager = None
        self.security_auditor = None
        
        # Initialize subsystems
        self._initialize_subsystems()
        
        # Operation statistics
        self._stats = {
            'sessions_added': 0,
            'sessions_updated': 0,
            'sessions_removed': 0,
            'sessions_duplicated': 0,
            'sessions_moved': 0,
            'folders_added': 0,
            'folders_updated': 0,
            'folders_removed': 0,
            'validation_failures': 0,
            'operation_errors': 0
        }
        
        self.logger.info("Session operations manager initialized")
    
    def _initialize_subsystems(self) -> None:
        """Initialize backup and security subsystems."""
        try:
            # Initialize backup manager
            self.backup_manager = get_backup_manager()
            self.logger.debug("Backup manager initialized for operations")
        except Exception as e:
            self.logger.warning(f"Backup manager initialization failed: {e}")
        
        try:
            # Initialize security auditor
            self.security_auditor = create_security_auditor()
            self.logger.debug("Security auditor initialized for operations")
        except Exception as e:
            self.logger.warning(f"Security auditor initialization failed: {e}")
    
    def add_session(self, session: SessionItem, validate_security: bool = True, 
                   create_backup: bool = True) -> OperationResult:
        """
        Add a new session to the store with comprehensive validation.
        
        Args:
            session: SessionItem to add
            validate_security: Whether to perform security validation
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                self.logger.debug(f"Adding session: '{session.name}' (type: {session.session_type})")
                
                # Validate session
                validation_result = self._validate_session_for_add(session, validate_security)
                if not validation_result.success:
                    self._stats['validation_failures'] += 1
                    return validation_result
                
                # Check for duplicates
                duplicate_check = self._check_session_duplicate(session)
                if duplicate_check:
                    self._stats['validation_failures'] += 1
                    return OperationResult(
                        False, 
                        f"Session '{session.name}' already exists in the specified folder",
                        metadata={'existing_session': duplicate_check}
                    )
                
                # Sanitize session name
                original_name = session.name
                session.name = sanitize_session_name(session.name)
                
                result = OperationResult(True, f"Session '{session.name}' added successfully", session)
                
                if original_name != session.name:
                    result.add_warning(f"Session name sanitized: '{original_name}' -> '{session.name}'")
                
                # Add validation warnings if any
                if validation_result.warnings:
                    result.warnings.extend(validation_result.warnings)
                
                # Add to store
                self.session_store.append(session)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Remove from store on save failure
                    self._remove_session_from_store(session)
                    return OperationResult(False, "Failed to save session data")
                
                # Update statistics and log
                self._stats['sessions_added'] += 1
                log_session_event("added", session.name, f"type: {session.session_type}, folder: {session.folder_path}")
                
                self.logger.info(f"Session added successfully: '{session.name}'")
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to add session '{session.name}': {e}")
                log_error_with_context(e, f"add session {session.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to add session: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def _validate_session_for_add(self, session: SessionItem, validate_security: bool) -> OperationResult:
        """
        Comprehensive validation for session addition.
        
        Args:
            session: Session to validate
            validate_security: Whether to perform security validation
            
        Returns:
            OperationResult with validation status
        """
        try:
            result = OperationResult(True, "Validation passed")
            
            # Basic validation
            if not session.validate():
                errors = session.get_validation_errors()
                return OperationResult(False, f"Session validation failed: {', '.join(errors)}")
            
            # Security validation for SSH sessions
            if validate_security and session.is_ssh() and self.security_auditor:
                try:
                    session_data = session.to_dict()
                    is_valid, validation_errors = validate_session_data(session_data)
                    
                    if not is_valid:
                        return OperationResult(False, f"Security validation failed: {', '.join(validation_errors)}")
                    
                    # Perform security audit
                    findings = self.security_auditor.audit_ssh_session(session_data)
                    
                    # Add warnings for medium/high severity issues
                    for finding in findings:
                        if finding['severity'] in ['medium', 'high']:
                            result.add_warning(f"Security: {finding['message']}")
                        elif finding['severity'] == 'critical':
                            return OperationResult(False, f"Critical security issue: {finding['message']}")
                    
                except Exception as e:
                    result.add_warning(f"Security validation error: {e}")
            
            # Folder validation
            if session.folder_path:
                if not self._folder_exists(session.folder_path):
                    return OperationResult(False, f"Target folder '{session.folder_path}' does not exist")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Session validation error: {e}")
            return OperationResult(False, f"Validation error: {e}")
    
    def update_session(self, position: int, updated_session: SessionItem,
                      validate_security: bool = True, create_backup: bool = True) -> OperationResult:
        """
        Update an existing session with comprehensive validation.
        
        Args:
            position: Position in store
            updated_session: Updated SessionItem
            validate_security: Whether to perform security validation
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                if not (0 <= position < self.session_store.get_n_items()):
                    return OperationResult(False, f"Invalid position: {position}")
                
                original_session = self.session_store.get_item(position)
                if not isinstance(original_session, SessionItem):
                    return OperationResult(False, f"Item at position {position} is not a session")
                
                original_name = original_session.name
                self.logger.debug(f"Updating session: '{original_name}' -> '{updated_session.name}'")
                
                # Validate updated session
                validation_result = self._validate_session_for_update(
                    original_session, updated_session, validate_security
                )
                if not validation_result.success:
                    self._stats['validation_failures'] += 1
                    return validation_result
                
                # Check for name conflicts (excluding current session)
                if updated_session.name != original_name:
                    duplicate_check = self._check_session_duplicate(updated_session, exclude_session=original_session)
                    if duplicate_check:
                        self._stats['validation_failures'] += 1
                        return OperationResult(
                            False,
                            f"Session name '{updated_session.name}' already exists in the target folder"
                        )
                
                # Sanitize session name
                original_updated_name = updated_session.name
                updated_session.name = sanitize_session_name(updated_session.name)
                
                # Update all properties with validation
                result = self._update_session_properties(original_session, updated_session)
                if not result.success:
                    return result
                
                # Add warnings
                if original_updated_name != updated_session.name:
                    result.add_warning(f"Session name sanitized: '{original_updated_name}' -> '{updated_session.name}'")
                
                if validation_result.warnings:
                    result.warnings.extend(validation_result.warnings)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    return OperationResult(False, "Failed to save session data")
                
                # Update statistics and log
                self._stats['sessions_updated'] += 1
                log_session_event("updated", updated_session.name, f"from: {original_name}")
                
                self.logger.info(f"Session updated successfully: '{original_name}' -> '{updated_session.name}'")
                result.message = f"Session '{updated_session.name}' updated successfully"
                result.item = original_session
                
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to update session at position {position}: {e}")
                log_error_with_context(e, f"update session at {position}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to update session: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def _validate_session_for_update(self, original: SessionItem, updated: SessionItem, 
                                   validate_security: bool) -> OperationResult:
        """
        Validate session update operation.
        
        Args:
            original: Original session
            updated: Updated session
            validate_security: Whether to perform security validation
            
        Returns:
            OperationResult with validation status
        """
        try:
            result = OperationResult(True, "Update validation passed")
            
            # Basic validation of updated session
            if not updated.validate():
                errors = updated.get_validation_errors()
                return OperationResult(False, f"Updated session validation failed: {', '.join(errors)}")
            
            # Security validation for SSH sessions
            if validate_security and updated.is_ssh() and self.security_auditor:
                try:
                    session_data = updated.to_dict()
                    is_valid, validation_errors = validate_session_data(session_data)
                    
                    if not is_valid:
                        return OperationResult(False, f"Security validation failed: {', '.join(validation_errors)}")
                    
                    # Compare security scores
                    original_score = original.get_security_score()
                    updated_score = updated.get_security_score()
                    
                    if updated_score < original_score - 10:  # Significant security degradation
                        result.add_warning(f"Security score decreased: {original_score} -> {updated_score}")
                    
                    # Perform security audit
                    findings = self.security_auditor.audit_ssh_session(session_data)
                    
                    for finding in findings:
                        if finding['severity'] in ['medium', 'high']:
                            result.add_warning(f"Security: {finding['message']}")
                        elif finding['severity'] == 'critical':
                            return OperationResult(False, f"Critical security issue: {finding['message']}")
                    
                except Exception as e:
                    result.add_warning(f"Security validation error: {e}")
            
            # Folder validation
            if updated.folder_path != original.folder_path:
                if updated.folder_path and not self._folder_exists(updated.folder_path):
                    return OperationResult(False, f"Target folder '{updated.folder_path}' does not exist")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Update validation error: {e}")
            return OperationResult(False, f"Update validation error: {e}")
    
    def _update_session_properties(self, original: SessionItem, updated: SessionItem) -> OperationResult:
        """
        Safely update session properties with validation.
        
        Args:
            original: Original session to update
            updated: Source of updated values
            
        Returns:
            OperationResult with operation status
        """
        try:
            # Store original values for rollback
            original_values = {
                'name': original.name,
                'session_type': original.session_type,
                'host': original.host,
                'user': original.user,
                'auth_type': original.auth_type,
                'auth_value': original.auth_value,
                'folder_path': original.folder_path
            }
            
            try:
                # Update properties with validation
                original.name = updated.name
                original.session_type = updated.session_type
                original.host = updated.host
                original.user = updated.user
                original.auth_type = updated.auth_type
                original.auth_value = updated.auth_value
                original.folder_path = updated.folder_path
                
                # Mark as used to update metadata
                original.mark_used()
                
                return OperationResult(True, "Properties updated successfully")
                
            except Exception as e:
                # Rollback on error
                for prop, value in original_values.items():
                    setattr(original, prop, value)
                
                raise e
                
        except Exception as e:
            self.logger.error(f"Property update failed: {e}")
            return OperationResult(False, f"Property update failed: {e}")
    
    def remove_session(self, session: SessionItem, create_backup: bool = True) -> OperationResult:
        """
        Remove a session from the store with safety checks.
        
        Args:
            session: SessionItem to remove
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                session_name = session.name
                self.logger.debug(f"Removing session: '{session_name}'")
                
                # Find and remove session
                position = self._find_session_position(session)
                if position == -1:
                    return OperationResult(False, f"Session '{session_name}' not found in store")
                
                # Remove from store
                removed_session = self.session_store.get_item(position)
                self.session_store.remove(position)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Re-add on save failure
                    self.session_store.insert(position, removed_session)
                    return OperationResult(False, "Failed to save after session removal")
                
                # Update statistics and log
                self._stats['sessions_removed'] += 1
                log_session_event("removed", session_name, f"type: {session.session_type}")
                
                self.logger.info(f"Session removed successfully: '{session_name}'")
                
                result = OperationResult(True, f"Session '{session_name}' removed successfully")
                result.metadata['removed_position'] = position
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to remove session '{session.name}': {e}")
                log_error_with_context(e, f"remove session {session.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to remove session: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def duplicate_session(self, session: SessionItem, custom_name: Optional[str] = None,
                         create_backup: bool = True) -> OperationResult:
        """
        Create a duplicate of a session with enhanced naming.
        
        Args:
            session: SessionItem to duplicate
            custom_name: Custom name for duplicate (auto-generated if None)
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                original_name = session.name
                self.logger.debug(f"Duplicating session: '{original_name}'")
                
                # Create duplicate
                duplicate_data = session.to_dict()
                duplicate = SessionItem.from_dict(duplicate_data)
                
                # Generate unique name
                if custom_name:
                    duplicate.name = sanitize_session_name(custom_name)
                else:
                    existing_names = self._get_session_names_in_folder(session.folder_path)
                    duplicate.name = generate_unique_name(f"Copy of {session.name}", existing_names)
                
                # Validate duplicate
                if not duplicate.validate():
                    errors = duplicate.get_validation_errors()
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Duplicate validation failed: {', '.join(errors)}")
                
                # Check for name conflicts
                duplicate_check = self._check_session_duplicate(duplicate)
                if duplicate_check:
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Duplicate name '{duplicate.name}' already exists")
                
                # Add duplicate
                add_result = self.add_session(duplicate, validate_security=False, create_backup=create_backup)
                if not add_result.success:
                    return add_result
                
                # Update statistics and log
                self._stats['sessions_duplicated'] += 1
                log_session_event("duplicated", f"{original_name} -> {duplicate.name}", "session duplication")
                
                self.logger.info(f"Session duplicated successfully: '{original_name}' -> '{duplicate.name}'")
                
                result = OperationResult(True, f"Session duplicated as '{duplicate.name}'", duplicate)
                result.metadata['original_name'] = original_name
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to duplicate session '{session.name}': {e}")
                log_error_with_context(e, f"duplicate session {session.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to duplicate session: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def move_session_to_folder(self, session: SessionItem, target_folder_path: str,
                              create_backup: bool = True) -> OperationResult:
        """
        Move a session to a different folder with validation.
        
        Args:
            session: SessionItem to move
            target_folder_path: Path of target folder (empty string for root)
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                original_folder = session.folder_path
                session_name = session.name
                
                self.logger.debug(f"Moving session '{session_name}': '{original_folder}' -> '{target_folder_path}'")
                
                # Validate target folder exists (if not root)
                if target_folder_path and not self._folder_exists(target_folder_path):
                    return OperationResult(False, f"Target folder '{target_folder_path}' does not exist")
                
                # Check for name conflicts in target folder
                existing_names = self._get_session_names_in_folder(target_folder_path)
                if session.name in existing_names:
                    # Generate unique name in target folder
                    new_name = generate_unique_name(session.name, existing_names)
                    session.name = new_name
                    
                    result = OperationResult(True, f"Session moved and renamed to '{new_name}'", session)
                    result.add_warning(f"Session renamed to avoid conflict: '{session_name}' -> '{new_name}'")
                else:
                    result = OperationResult(True, f"Session moved to '{target_folder_path or 'root'}'", session)
                
                # Update folder path
                session.folder_path = target_folder_path
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Rollback changes
                    session.folder_path = original_folder
                    session.name = session_name
                    return OperationResult(False, "Failed to save after moving session")
                
                # Update statistics and log
                self._stats['sessions_moved'] += 1
                log_session_event("moved", session.name, f"from: {original_folder}, to: {target_folder_path}")
                
                self.logger.info(f"Session moved successfully: '{session_name}' to '{target_folder_path or 'root'}'")
                
                result.metadata = {
                    'original_folder': original_folder,
                    'target_folder': target_folder_path,
                    'name_changed': session.name != session_name
                }
                
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to move session '{session.name}': {e}")
                log_error_with_context(e, f"move session {session.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to move session: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def add_folder(self, folder: SessionFolder, create_backup: bool = True) -> OperationResult:
        """
        Add a new folder to the store with validation.
        
        Args:
            folder: SessionFolder to add
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                self.logger.debug(f"Adding folder: '{folder.name}' (path: {folder.path})")
                
                # Validate folder
                if not folder.validate():
                    errors = folder.get_validation_errors()
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Folder validation failed: {', '.join(errors)}")
                
                # Check path conflicts
                if self._folder_path_exists(folder.path):
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Folder path '{folder.path}' already exists")
                
                # Sanitize folder name
                original_name = folder.name
                folder.name = sanitize_folder_name(folder.name)
                
                # Update path if name changed
                if original_name != folder.name:
                    folder.path = normalize_path(f"{folder.parent_path}/{folder.name}" if folder.parent_path else f"/{folder.name}")
                
                # Add to store
                self.folder_store.append(folder)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Remove from store on save failure
                    self._remove_folder_from_store(folder)
                    return OperationResult(False, "Failed to save folder data")
                
                # Update statistics and log
                self._stats['folders_added'] += 1
                log_session_event("folder_added", folder.name, f"path: {folder.path}")
                
                self.logger.info(f"Folder added successfully: '{folder.name}' (path: {folder.path})")
                
                result = OperationResult(True, f"Folder '{folder.name}' added successfully", folder)
                
                if original_name != folder.name:
                    result.add_warning(f"Folder name sanitized: '{original_name}' -> '{folder.name}'")
                
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to add folder '{folder.name}': {e}")
                log_error_with_context(e, f"add folder {folder.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to add folder: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def update_folder(self, position: int, updated_folder: SessionFolder,
                     create_backup: bool = True) -> OperationResult:
        """
        Update an existing folder with path management.
        
        Args:
            position: Position in store
            updated_folder: Updated SessionFolder
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                if not (0 <= position < self.folder_store.get_n_items()):
                    return OperationResult(False, f"Invalid position: {position}")
                
                original_folder = self.folder_store.get_item(position)
                if not isinstance(original_folder, SessionFolder):
                    return OperationResult(False, f"Item at position {position} is not a folder")
                
                old_path = original_folder.path
                old_name = original_folder.name
                
                self.logger.debug(f"Updating folder: '{old_name}' -> '{updated_folder.name}'")
                
                # Validate updated folder
                if not updated_folder.validate():
                    errors = updated_folder.get_validation_errors()
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Updated folder validation failed: {', '.join(errors)}")
                
                # Check path conflicts (excluding current folder)
                if updated_folder.path != old_path and self._folder_path_exists(updated_folder.path, exclude_folder=original_folder):
                    self._stats['validation_failures'] += 1
                    return OperationResult(False, f"Folder path '{updated_folder.path}' already exists")
                
                # Sanitize folder name
                original_updated_name = updated_folder.name
                updated_folder.name = sanitize_folder_name(updated_folder.name)
                
                # Update folder properties
                original_folder.name = updated_folder.name
                original_folder.parent_path = updated_folder.parent_path
                original_folder.path = updated_folder.path
                
                # Update child paths if folder path changed
                if old_path != updated_folder.path:
                    self._update_child_paths(old_path, updated_folder.path)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Rollback changes
                    original_folder.name = old_name
                    original_folder.path = old_path
                    return OperationResult(False, "Failed to save folder data")
                
                # Update statistics and log
                self._stats['folders_updated'] += 1
                log_session_event("folder_updated", updated_folder.name, f"from: {old_name}, path: {updated_folder.path}")
                
                self.logger.info(f"Folder updated successfully: '{old_name}' -> '{updated_folder.name}'")
                
                result = OperationResult(True, f"Folder '{updated_folder.name}' updated successfully", original_folder)
                
                if original_updated_name != updated_folder.name:
                    result.add_warning(f"Folder name sanitized: '{original_updated_name}' -> '{updated_folder.name}'")
                
                if old_path != updated_folder.path:
                    result.metadata['path_updated'] = True
                    result.metadata['old_path'] = old_path
                    result.metadata['new_path'] = updated_folder.path
                
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to update folder at position {position}: {e}")
                log_error_with_context(e, f"update folder at {position}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to update folder: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    def remove_folder(self, folder: SessionFolder, force: bool = False,
                     create_backup: bool = True) -> OperationResult:
        """
        Remove a folder from the store with safety checks.
        
        Args:
            folder: SessionFolder to remove
            force: Whether to force removal of non-empty folders
            create_backup: Whether to create backup before operation
            
        Returns:
            OperationResult with operation details
        """
        with self._operation_lock:
            try:
                folder_name = folder.name
                folder_path = folder.path
                
                self.logger.debug(f"Removing folder: '{folder_name}' (path: {folder_path}, force: {force})")
                
                # Check if folder has children (unless force removal)
                if not force and self._folder_has_children(folder_path):
                    return OperationResult(
                        False,
                        f"Cannot remove folder '{folder_name}' - it contains sessions or subfolders. Use force=True to remove anyway."
                    )
                
                # If force removal, handle children
                removed_children = []
                if force and self._folder_has_children(folder_path):
                    removed_children = self._remove_folder_children(folder_path)
                
                # Find and remove folder
                position = self._find_folder_position(folder)
                if position == -1:
                    return OperationResult(False, f"Folder '{folder_name}' not found in store")
                
                # Remove from store
                removed_folder = self.folder_store.get_item(position)
                self.folder_store.remove(position)
                
                # Save changes
                if not self._save_changes(create_backup):
                    self._stats['operation_errors'] += 1
                    # Re-add on save failure
                    self.folder_store.insert(position, removed_folder)
                    # Restore children if they were removed
                    if removed_children:
                        self._restore_folder_children(removed_children)
                    return OperationResult(False, "Failed to save after folder removal")
                
                # Update statistics and log
                self._stats['folders_removed'] += 1
                log_session_event("folder_removed", folder_name, f"path: {folder_path}, children: {len(removed_children)}")
                
                self.logger.info(f"Folder removed successfully: '{folder_name}' (children: {len(removed_children)})")
                
                result = OperationResult(True, f"Folder '{folder_name}' removed successfully")
                result.metadata = {
                    'removed_position': position,
                    'children_removed': len(removed_children),
                    'removed_children': removed_children
                }
                
                if removed_children:
                    result.add_warning(f"Removed {len(removed_children)} child items")
                
                return result
                
            except Exception as e:
                self._stats['operation_errors'] += 1
                self.logger.error(f"Failed to remove folder '{folder.name}': {e}")
                log_error_with_context(e, f"remove folder {folder.name}", "ashyterm.sessions.operations")
                
                error_result = OperationResult(False, f"Failed to remove folder: {e}")
                error_result.metadata['exception'] = str(e)
                return error_result
    
    # Helper methods
    def _update_child_paths(self, old_path: str, new_path: str) -> None:
        """
        Update paths of all children when a folder path changes.
        
        Args:
            old_path: Original folder path
            new_path: New folder path
        """
        try:
            updated_sessions = 0
            updated_folders = 0
            
            # Update sessions in this folder
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem):
                    if session.folder_path == old_path:
                        session.folder_path = new_path
                        updated_sessions += 1
                    elif session.folder_path and session.folder_path.startswith(old_path + "/"):
                        session.folder_path = session.folder_path.replace(old_path + "/", new_path + "/", 1)
                        updated_sessions += 1
            
            # Update subfolders
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder):
                    if folder.parent_path == old_path:
                        folder.parent_path = new_path
                        folder.path = normalize_path(new_path + "/" + folder.name)
                        updated_folders += 1
                    elif folder.parent_path and folder.parent_path.startswith(old_path + "/"):
                        folder.parent_path = folder.parent_path.replace(old_path + "/", new_path + "/", 1)
                        folder.path = normalize_path(folder.parent_path + "/" + folder.name)
                        updated_folders += 1
            
            self.logger.debug(f"Updated child paths: {updated_sessions} sessions, {updated_folders} folders")
            
        except Exception as e:
            self.logger.error(f"Failed to update child paths: {e}")
            raise
    
    def _remove_folder_children(self, folder_path: str) -> List[Dict[str, Any]]:
        """
        Remove all children of a folder (for force removal).
        
        Args:
            folder_path: Path of folder whose children to remove
            
        Returns:
            List of removed items for potential restoration
        """
        removed_children = []
        
        try:
            # Remove sessions
            for i in range(self.session_store.get_n_items() - 1, -1, -1):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem) and session.folder_path == folder_path:
                    removed_children.append({
                        'type': 'session',
                        'position': i,
                        'item': SessionItem.from_dict(session.to_dict())
                    })
                    self.session_store.remove(i)
            
            # Remove subfolders (recursively)
            for i in range(self.folder_store.get_n_items() - 1, -1, -1):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder) and folder.parent_path == folder_path:
                    # Recursively remove children of subfolder
                    subfolder_children = self._remove_folder_children(folder.path)
                    removed_children.extend(subfolder_children)
                    
                    # Remove the subfolder itself
                    removed_children.append({
                        'type': 'folder',
                        'position': i,
                        'item': SessionFolder.from_dict(folder.to_dict())
                    })
                    self.folder_store.remove(i)
            
            return removed_children
            
        except Exception as e:
            self.logger.error(f"Failed to remove folder children: {e}")
            raise
    
    def _restore_folder_children(self, removed_children: List[Dict[str, Any]]) -> None:
        """
        Restore previously removed folder children.
        
        Args:
            removed_children: List of removed items to restore
        """
        try:
            # Sort by position to maintain order
            sessions = [item for item in removed_children if item['type'] == 'session']
            folders = [item for item in removed_children if item['type'] == 'folder']
            
            sessions.sort(key=lambda x: x['position'])
            folders.sort(key=lambda x: x['position'])
            
            # Restore folders first
            for item_data in folders:
                self.folder_store.insert(item_data['position'], item_data['item'])
            
            # Then restore sessions
            for item_data in sessions:
                self.session_store.insert(item_data['position'], item_data['item'])
            
            self.logger.debug(f"Restored {len(removed_children)} folder children")
            
        except Exception as e:
            self.logger.error(f"Failed to restore folder children: {e}")
    
    def _check_session_duplicate(self, session: SessionItem, 
                               exclude_session: Optional[SessionItem] = None) -> Optional[SessionItem]:
        """
        Check if a session with the same name exists in the same folder.
        
        Args:
            session: Session to check
            exclude_session: Session to exclude from check (for updates)
            
        Returns:
            Existing session if found, None otherwise
        """
        try:
            for i in range(self.session_store.get_n_items()):
                existing_session = self.session_store.get_item(i)
                if (isinstance(existing_session, SessionItem) and 
                    existing_session != exclude_session and
                    existing_session.name == session.name and 
                    existing_session.folder_path == session.folder_path):
                    return existing_session
            return None
        except Exception as e:
            self.logger.error(f"Error checking session duplicate: {e}")
            return None
    
    def _folder_path_exists(self, path: str, exclude_folder: Optional[SessionFolder] = None) -> bool:
        """
        Check if a folder path already exists.
        
        Args:
            path: Path to check
            exclude_folder: Folder to exclude from check (for updates)
            
        Returns:
            True if path exists
        """
        try:
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if (isinstance(folder, SessionFolder) and 
                    folder != exclude_folder and 
                    folder.path == path):
                    return True
            return False
        except Exception as e:
            self.logger.error(f"Error checking folder path existence: {e}")
            return False
    
    def _folder_exists(self, folder_path: str) -> bool:
        """Check if a folder with the given path exists."""
        return self._folder_path_exists(folder_path)
    
    def _folder_has_children(self, folder_path: str) -> bool:
        """
        Check if a folder has any sessions or subfolders.
        
        Args:
            folder_path: Path of folder to check
            
        Returns:
            True if folder has children
        """
        try:
            # Check for sessions
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem) and session.folder_path == folder_path:
                    return True
            
            # Check for subfolders
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder) and folder.parent_path == folder_path:
                    return True
            
            return False
        except Exception as e:
            self.logger.error(f"Error checking folder children: {e}")
            return False
    
    def _get_session_names_in_folder(self, folder_path: str) -> Set[str]:
        """
        Get set of session names in a specific folder.
        
        Args:
            folder_path: Path of folder
            
        Returns:
            Set of session names
        """
        try:
            names = set()
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem) and session.folder_path == folder_path:
                    names.add(session.name)
            return names
        except Exception as e:
            self.logger.error(f"Error getting session names in folder: {e}")
            return set()
    
    def _find_session_position(self, session: SessionItem) -> int:
        """Find position of session in store."""
        try:
            for i in range(self.session_store.get_n_items()):
                if self.session_store.get_item(i) == session:
                    return i
            return -1
        except Exception as e:
            self.logger.error(f"Error finding session position: {e}")
            return -1
    
    def _find_folder_position(self, folder: SessionFolder) -> int:
        """Find position of folder in store."""
        try:
            for i in range(self.folder_store.get_n_items()):
                if self.folder_store.get_item(i) == folder:
                    return i
            return -1
        except Exception as e:
            self.logger.error(f"Error finding folder position: {e}")
            return -1
    
    def _remove_session_from_store(self, session: SessionItem) -> bool:
        """Remove session from store by reference."""
        try:
            position = self._find_session_position(session)
            if position != -1:
                self.session_store.remove(position)
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error removing session from store: {e}")
            return False
    
    def _remove_folder_from_store(self, folder: SessionFolder) -> bool:
        """Remove folder from store by reference."""
        try:
            position = self._find_folder_position(folder)
            if position != -1:
                self.folder_store.remove(position)
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error removing folder from store: {e}")
            return False
    
    def _save_changes(self, create_backup: bool = True) -> bool:
        """Save changes to storage with optional backup."""
        try:
            return save_sessions_and_folders(self.session_store, self.folder_store, create_backup)
        except Exception as e:
            self.logger.error(f"Failed to save changes: {e}")
            return False
    
    # Public utility methods
    def get_sessions_in_folder(self, folder_path: str) -> List[SessionItem]:
        """
        Get all sessions in a specific folder.
        
        Args:
            folder_path: Folder path
            
        Returns:
            List of SessionItem objects
        """
        try:
            sessions = []
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem) and session.folder_path == folder_path:
                    sessions.append(session)
            return sessions
        except Exception as e:
            self.logger.error(f"Error getting sessions in folder: {e}")
            return []
    
    def get_subfolders(self, parent_path: str) -> List[SessionFolder]:
        """
        Get all direct subfolders of a path.
        
        Args:
            parent_path: Parent folder path
            
        Returns:
            List of SessionFolder objects
        """
        try:
            subfolders = []
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder) and folder.parent_path == parent_path:
                    subfolders.append(folder)
            return subfolders
        except Exception as e:
            self.logger.error(f"Error getting subfolders: {e}")
            return []
    
    def find_session_by_name_and_path(self, name: str, folder_path: str) -> Optional[Tuple[SessionItem, int]]:
        """
        Find a session by name and folder path.
        
        Args:
            name: Session name
            folder_path: Folder path
            
        Returns:
            Tuple of (SessionItem, position) or None if not found
        """
        try:
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if (isinstance(session, SessionItem) and 
                    session.name == name and 
                    session.folder_path == folder_path):
                    return session, i
            return None
        except Exception as e:
            self.logger.error(f"Error finding session by name and path: {e}")
            return None
    
    def find_folder_by_path(self, path: str) -> Optional[Tuple[SessionFolder, int]]:
        """
        Find a folder by path.
        
        Args:
            path: Folder path
            
        Returns:
            Tuple of (SessionFolder, position) or None if not found
        """
        try:
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder) and folder.path == path:
                    return folder, i
            return None
        except Exception as e:
            self.logger.error(f"Error finding folder by path: {e}")
            return None
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get operations statistics.
        
        Returns:
            Dictionary with statistics
        """
        try:
            stats = self._stats.copy()
            stats.update({
                'total_sessions': self.session_store.get_n_items(),
                'total_folders': self.folder_store.get_n_items(),
                'backup_available': self.backup_manager is not None,
                'security_audit_available': self.security_auditor is not None
            })
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {'error': str(e)}
    
    def validate_all_sessions(self) -> Dict[str, Any]:
        """
        Validate all sessions in the store.
        
        Returns:
            Validation summary
        """
        try:
            valid_sessions = 0
            invalid_sessions = 0
            security_issues = 0
            validation_details = []
            
            for i in range(self.session_store.get_n_items()):
                session = self.session_store.get_item(i)
                if isinstance(session, SessionItem):
                    if session.validate():
                        valid_sessions += 1
                        
                        # Security audit for SSH sessions
                        if session.is_ssh() and self.security_auditor:
                            try:
                                findings = self.security_auditor.audit_ssh_session(session.to_dict())
                                high_severity = [f for f in findings if f['severity'] in ['high', 'critical']]
                                if high_severity:
                                    security_issues += 1
                                    validation_details.append({
                                        'session': session.name,
                                        'type': 'security_issues',
                                        'issues': [f['message'] for f in high_severity]
                                    })
                            except Exception as e:
                                self.logger.warning(f"Security audit failed for {session.name}: {e}")
                    else:
                        invalid_sessions += 1
                        errors = session.get_validation_errors()
                        validation_details.append({
                            'session': session.name,
                            'type': 'validation_errors',
                            'errors': errors
                        })
            
            return {
                'valid_sessions': valid_sessions,
                'invalid_sessions': invalid_sessions,
                'security_issues': security_issues,
                'total_sessions': self.session_store.get_n_items(),
                'validation_details': validation_details
            }
            
        except Exception as e:
            self.logger.error(f"Session validation failed: {e}")
            return {'error': str(e)}