from typing import Dict, Any, Optional, List
import time
import hashlib
from pathlib import Path
from gi.repository import GObject

# Import new utility systems
from ..utils.logger import get_logger, log_session_event
from ..utils.exceptions import (
    SessionValidationError, ValidationError, 
    ErrorCategory, ErrorSeverity, handle_exception
)
from ..utils.security import (
    validate_ssh_hostname, InputSanitizer, SSHKeyValidator,
    HostnameValidator, sanitize_session_name, sanitize_folder_name
)
from ..utils.crypto import (
    is_encryption_available, encrypt_password, decrypt_password,
    get_secure_storage
)
from ..utils.platform import normalize_path
from ..utils.translation_utils import _


class SessionItem(GObject.GObject):
    """Enhanced session item with security, validation, and encryption features."""
    
    def __init__(self, name: str, session_type: str = "local",
             host: str = "", user: str = "",
             auth_type: str = "key", auth_value: str = "",
             folder_path: str = "", port: int = 22):
        """
        Initialize a session item with validation and security features.
        
        Args:
            name: Display name for the session
            session_type: Type of session ("local" or "ssh")
            host: Hostname for SSH connections
            user: Username for SSH connections
            auth_type: Authentication method ("key" or "password")
            auth_value: Key file path or password value
            folder_path: Path of parent folder, empty string for root
        """
        super().__init__()
        
        self.logger = get_logger('ashyterm.sessions.model')
        
        # Core properties with validation
        self._name = ""
        self._session_type = ""
        self._host = ""
        self._user = ""
        self._auth_type = ""
        self._auth_value = ""
        self._folder_path = ""
        self._port = 22
        
        # Metadata
        self._created_at = time.time()
        self._modified_at = time.time()
        self._last_used = None
        self._use_count = 0
        self._version = 1
        
        # Validation flags
        self._validated = False
        self._validation_errors: List[str] = []
        
        # Set properties with validation
        self.name = name
        self.session_type = session_type
        self.host = host
        self.user = user
        self.auth_type = auth_type
        self.auth_value = auth_value
        self.folder_path = folder_path
        self.port = port
        
        
        self.logger.debug(f"Session item created: '{self.name}' (type: {self.session_type})")
    
    @property
    def name(self) -> str:
        """Get session name."""
        return self._name
    
    @name.setter
    def name(self, value: str) -> None:
        """Set session name with validation."""
        try:
            if not value or not value.strip():
                raise SessionValidationError("", [_("Session name cannot be empty")])
            
            # Sanitize the name
            sanitized = sanitize_session_name(value.strip())
            
            if sanitized != value.strip():
                self.logger.debug(f"Session name sanitized: '{value}' -> '{sanitized}'")
            
            self._name = sanitized
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Session name validation failed: {e}")
            raise
    
    @property
    def session_type(self) -> str:
        """Get session type."""
        return self._session_type
    
    @session_type.setter
    def session_type(self, value: str) -> None:
        """Set session type with validation."""
        try:
            valid_types = ["local", "ssh"]
            if value not in valid_types:
                raise SessionValidationError(self.name, [_("Invalid session type: {type}. Must be one of: {valid_types}").format(type=value, valid_types=", ".join(valid_types))])
            
            self._session_type = value
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Session type validation failed: {e}")
            raise
    
    @property
    def host(self) -> str:
        """Get host."""
        return self._host
    
    @host.setter
    def host(self, value: str) -> None:
        """Set host with validation."""
        try:
            if value:
                # Sanitize hostname
                sanitized = InputSanitizer.sanitize_hostname(value)
                
                # Validate hostname format
                if not HostnameValidator.is_valid_hostname(sanitized):
                    raise SessionValidationError(self.name, [_("Invalid hostname format: {hostname}").format(hostname=value)])
                
                if sanitized != value:
                    self.logger.debug(f"Hostname sanitized: '{value}' -> '{sanitized}'")
                
                self._host = sanitized
            else:
                self._host = ""
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Host validation failed: {e}")
            raise
    
    @property
    def user(self) -> str:
        """Get user."""
        return self._user
    
    @user.setter
    def user(self, value: str) -> None:
        """Set user with validation."""
        try:
            if value:
                # Sanitize username
                sanitized = InputSanitizer.sanitize_username(value)
                
                if sanitized != value:
                    self.logger.debug(f"Username sanitized: '{value}' -> '{sanitized}'")
                
                self._user = sanitized
            else:
                self._user = ""
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"User validation failed: {e}")
            raise
    
    @property
    def auth_type(self) -> str:
        """Get authentication type."""
        return self._auth_type
    
    @auth_type.setter
    def auth_type(self, value: str) -> None:
        """Set authentication type with validation."""
        try:
            valid_types = ["key", "password", ""]
            if value not in valid_types:
                raise SessionValidationError(self.name, [_("Invalid authentication type: {type}. Must be one of: {valid_types}").format(type=value, valid_types=", ".join(valid_types))])
            
            self._auth_type = value
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Auth type validation failed: {e}")
            raise
    
    @property
    def auth_value(self) -> str:
        """Get authentication value (decrypted if password)."""
        if self.uses_password_auth() and self._auth_value:
            try:
                if is_encryption_available():
                    storage = get_secure_storage()
                    if storage.is_initialized():
                        return decrypt_password(self._auth_value)
                # Fallback to plain text if encryption not available
                return self._auth_value
            except Exception as e:
                self.logger.error(f"Password decryption failed for session '{self.name}': {e}")
                return self._auth_value
        
        return self._auth_value
    
    @auth_value.setter
    def auth_value(self, value: str) -> None:
        """Set authentication value with encryption for passwords."""
        try:
            if self.uses_password_auth() and value:
                # Encrypt password if encryption is available
                if is_encryption_available():
                    storage = get_secure_storage()
                    if storage.is_initialized():
                        try:
                            encrypted_value = encrypt_password(value)
                            self._auth_value = encrypted_value
                            self.logger.debug(f"Password encrypted for session '{self.name}'")
                        except Exception as e:
                            self.logger.warning(f"Password encryption failed for session '{self.name}': {e}")
                            self._auth_value = value
                    else:
                        self.logger.warning(f"Encryption not initialized, storing password as plain text for session '{self.name}'")
                        self._auth_value = value
                else:
                    self.logger.warning(f"Encryption not available, storing password as plain text for session '{self.name}'")
                    self._auth_value = value
            else:
                # For keys and other auth types, store as-is
                if self.uses_key_auth() and value:
                    # Validate SSH key path
                    try:
                        key_path = Path(value)
                        if key_path.exists():
                            is_valid, error = SSHKeyValidator.validate_ssh_key_path(value)
                            if not is_valid:
                                self.logger.warning(f"SSH key validation failed for session '{self.name}': {error}")
                    except Exception as e:
                        self.logger.debug(f"SSH key path validation failed: {e}")
                
                self._auth_value = value
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Auth value setting failed: {e}")
            raise
    
    @property
    def folder_path(self) -> str:
        """Get folder path."""
        return self._folder_path
    
    @folder_path.setter
    def folder_path(self, value: str) -> None:
        """Set folder path with validation."""
        try:
            if value:
                # Normalize and validate path
                normalized = normalize_path(value)
                self._folder_path = str(normalized)
            else:
                self._folder_path = ""
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Folder path validation failed: {e}")
            raise
        
    @property
    def port(self) -> int:
        """Get SSH port."""
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        """Set SSH port with validation."""
        try:
            port_val = int(value)
            if not (1 <= port_val <= 65535):
                raise SessionValidationError(self.name, [_("Port must be between 1 and 65535")])
            
            self._port = port_val
            self._mark_modified()
            
        except ValueError:
            raise SessionValidationError(self.name, [_("Port must be a valid number")])
        except Exception as e:
            self.logger.error(f"Port validation failed: {e}")
            raise
    
    def _mark_modified(self) -> None:
        """Mark session as modified."""
        self._modified_at = time.time()
        self._validated = False  # Require re-validation after modification
    
    def mark_used(self) -> None:
        """Mark session as used (for statistics)."""
        try:
            self._last_used = time.time()
            self._use_count += 1
            self.logger.debug(f"Session '{self.name}' marked as used (count: {self._use_count})")
            log_session_event("used", self.name, f"use count: {self._use_count}")
        except Exception as e:
            self.logger.error(f"Failed to mark session as used: {e}")
    
    def validate(self) -> bool:
        """
        Validate session configuration.
        
        Returns:
            True if session is valid
        """
        try:
            self._validation_errors.clear()
            
            # Basic validation
            if not self.name:
                self._validation_errors.append(_("Session name is required"))
            
            if not self.session_type:
                self._validation_errors.append(_("Session type is required"))
            
            # SSH-specific validation
            if self.is_ssh():
                if not self.host:
                    self._validation_errors.append(_("Host is required for SSH sessions"))
                
                if self.uses_key_auth() and self.auth_value:
                    # Validate SSH key
                    try:
                        is_valid, error = SSHKeyValidator.validate_ssh_key_path(self.auth_value)
                        if not is_valid:
                            self._validation_errors.append(_("SSH key validation failed: {error}").format(error=error))
                    except Exception as e:
                        self._validation_errors.append(_("SSH key validation error: {error}").format(error=str(e)))
                
                if not self.auth_type:
                    self._validation_errors.append(_("Authentication type is required for SSH sessions"))
            
            self._validated = True
            
            if self._validation_errors:
                self.logger.warning(f"Session validation failed for '{self.name}': {self._validation_errors}")
                return False
            
            self.logger.debug(f"Session validation passed for '{self.name}'")
            return True
            
        except Exception as e:
            self.logger.error(f"Session validation error for '{self.name}': {e}")
            self._validation_errors.append(_("Validation error: {error}").format(error=str(e)))
            return False
    
    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors."""
        return self._validation_errors.copy()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary for serialization."""
        try:
            data = {
                "name": self.name,
                "session_type": self.session_type,
                "host": self.host,
                "user": self.user,
                "auth_type": self.auth_type,
                "auth_value": self._auth_value,  # Store encrypted/raw value
                "folder_path": self.folder_path,
                "port": self.port,
                # Metadata
                "created_at": self._created_at,
                "modified_at": self._modified_at,
                "last_used": self._last_used,
                "use_count": self._use_count,
                "version": self._version
            }
            
            return data
            
        except Exception as e:
            self.logger.error(f"Session serialization failed for '{self.name}': {e}")
            raise
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionItem":
        """Create session from dictionary data with migration support."""
        try:
            # Handle legacy data (version migration)
            version = data.get("version", 0)
            if version == 0:
                # Migrate from legacy format
                session = cls._migrate_from_legacy(data)
            else:
                # Current format
                session = cls(
                    name=data.get("name", _("Unnamed Session")),
                    session_type=data.get("session_type", "local"),
                    host=data.get("host", ""),
                    user=data.get("user", ""),
                    auth_type=data.get("auth_type", "key"),
                    auth_value="",  # Set separately to avoid encryption during init
                    folder_path=data.get("folder_path", ""),
                    port=data.get("port", 22)
                )
                
                # Set auth value separately to handle encryption properly
                session._auth_value = data.get("auth_value", "")
                
                # Restore metadata
                session._created_at = data.get("created_at", time.time())
                session._modified_at = data.get("modified_at", time.time())
                session._last_used = data.get("last_used")
                session._use_count = data.get("use_count", 0)
                session._version = data.get("version", 1)
            
            return session
            
        except Exception as e:
            logger = get_logger('ashyterm.sessions.model')
            logger.error(f"Session deserialization failed: {e}")
            raise
    
    @classmethod
    def _migrate_from_legacy(cls, data: Dict[str, Any]) -> "SessionItem":
        """Migrate session from legacy format."""
        logger = get_logger('ashyterm.sessions.model')
        logger.info(f"Migrating legacy session: {data.get('name', 'Unknown')}")
        
        session = cls(
            name=data.get("name", _("Unnamed Session")),
            session_type=data.get("session_type", "local"),
            host=data.get("host", ""),
            user=data.get("user", ""),
            auth_type=data.get("auth_type", "key"),
            auth_value=data.get("auth_value", ""),
            folder_path=data.get("folder_path", "")
        )
        
        # Mark as migrated
        session._version = 1
        session._created_at = time.time()
        session._modified_at = time.time()
        
        return session
    
    def get_security_score(self) -> int:
        """
        Calculate security score for the session (0-100).
        
        Returns:
            Security score (higher is better)
        """
        try:
            score = 100
            
            if self.is_ssh():
                # Deduct points for security issues
                if self.uses_password_auth():
                    score -= 20  # Password auth is less secure
                    if not is_encryption_available():
                        score -= 30  # Plain text password storage
                
                if not self.auth_value:
                    score -= 40  # No authentication configured
                
                if self.user == "root":
                    score -= 10  # Root user is risky
                
                # Check hostname
                if self.host:
                    if HostnameValidator.is_valid_ip_address(self.host):
                        if HostnameValidator.is_private_ip(self.host):
                            score += 5  # Private IP is safer
                    else:
                        # Hostname - slightly less secure than IP
                        score -= 5
            
            return max(0, min(100, score))
            
        except Exception as e:
            self.logger.error(f"Security score calculation failed: {e}")
            return 50  # Default to medium security
    
    def get_checksum(self) -> str:
        """
        Calculate checksum for data integrity verification.
        
        Returns:
            MD5 checksum of session data
        """
        try:
            # Create string representation of core data
            data_string = f"{self.name}|{self.session_type}|{self.host}|{self.user}|{self.auth_type}|{self.folder_path}|{self.port}"
            
            # Calculate MD5 hash
            checksum = hashlib.md5(data_string.encode('utf-8')).hexdigest()
            return checksum
            
        except Exception as e:
            self.logger.error(f"Checksum calculation failed: {e}")
            return ""
    
    def is_local(self) -> bool:
        """Check if this is a local session."""
        return self.session_type == "local"
    
    def is_ssh(self) -> bool:
        """Check if this is an SSH session."""
        return self.session_type == "ssh"
    
    def uses_key_auth(self) -> bool:
        """Check if SSH session uses key authentication."""
        return self.auth_type == "key"
    
    def uses_password_auth(self) -> bool:
        """Check if SSH session uses password authentication."""
        return self.auth_type == "password"
    
    def get_connection_string(self) -> str:
        """Get SSH connection string for display purposes."""
        if self.is_local():
            return _("Local Terminal")
        
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host
    
    def get_metadata(self) -> Dict[str, Any]:
        """Get session metadata."""
        return {
            'created_at': self._created_at,
            'modified_at': self._modified_at,
            'last_used': self._last_used,
            'use_count': self._use_count,
            'version': self._version,
            'security_score': self.get_security_score(),
            'checksum': self.get_checksum(),
            'validated': self._validated
        }
    
    def __str__(self) -> str:
        return f"SessionItem(name='{self.name}', type='{self.session_type}', security_score={self.get_security_score()})"


class SessionFolder(GObject.GObject):
    """Enhanced session folder with validation and metadata tracking."""
    
    def __init__(self, name: str, path: str = "", parent_path: str = ""):
        """
        Initialize a session folder with validation.
        
        Args:
            name: Display name for the folder
            path: Full path of the folder (e.g., "/folder1/subfolder")
            parent_path: Path of parent folder (e.g., "/folder1")
        """
        super().__init__()
        
        self.logger = get_logger('ashyterm.sessions.folder')
        
        # Core properties with validation
        self._name = ""
        self._path = ""
        self._parent_path = ""
        
        # Metadata
        self._created_at = time.time()
        self._modified_at = time.time()
        self._session_count = 0
        self._version = 1
        
        # Validation flags
        self._validated = False
        self._validation_errors: List[str] = []
        
        # Set properties with validation
        self.name = name
        self.path = path
        self.parent_path = parent_path
        
        self.logger.debug(f"Folder created: '{self.name}' (path: {self.path})")
    
    @property
    def name(self) -> str:
        """Get folder name."""
        return self._name
    
    @name.setter
    def name(self, value: str) -> None:
        """Set folder name with validation."""
        try:
            if not value or not value.strip():
                raise ValidationError(
                    _("Folder name cannot be empty"), 
                    category=ErrorCategory.VALIDATION, 
                    severity=ErrorSeverity.MEDIUM
                )
            
            # Sanitize the name
            sanitized = sanitize_folder_name(value.strip())
            
            if sanitized != value.strip():
                self.logger.debug(f"Folder name sanitized: '{value}' -> '{sanitized}'")
            
            self._name = sanitized
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Folder name validation failed: {e}")
            raise
    
    @property
    def path(self) -> str:
        """Get folder path."""
        return self._path
    
    @path.setter
    def path(self, value: str) -> None:
        """Set folder path with validation."""
        try:
            if value:
                # Normalize and validate path
                normalized = normalize_path(value)
                self._path = str(normalized)
            else:
                self._path = ""
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Folder path validation failed: {e}")
            raise
    
    @property
    def parent_path(self) -> str:
        """Get parent path."""
        return self._parent_path
    
    @parent_path.setter
    def parent_path(self, value: str) -> None:
        """Set parent path with validation."""
        try:
            if value:
                # Normalize and validate path
                normalized = normalize_path(value)
                self._parent_path = str(normalized)
            else:
                self._parent_path = ""
            
            self._mark_modified()
            
        except Exception as e:
            self.logger.error(f"Parent path validation failed: {e}")
            raise
    
    def _mark_modified(self) -> None:
        """Mark folder as modified."""
        self._modified_at = time.time()
        self._validated = False
    
    def update_session_count(self, count: int) -> None:
        """Update session count for this folder."""
        try:
            self._session_count = max(0, count)
            self.logger.debug(f"Folder '{self.name}' session count updated: {count}")
        except Exception as e:
            self.logger.error(f"Failed to update session count: {e}")
    
    def validate(self) -> bool:
        """
        Validate folder configuration.
        
        Returns:
            True if folder is valid
        """
        try:
            self._validation_errors.clear()
            
            if not self.name:
                self._validation_errors.append(_("Folder name is required"))
            
            # Check for invalid path characters
            if self.path and any(char in self.path for char in ['<', '>', ':', '"', '|', '?', '*']):
                self._validation_errors.append(_("Path contains invalid characters"))
            
            # Check parent-child relationship
            if self.parent_path and self.path:
                if not self.path.startswith(self.parent_path + "/"):
                    self._validation_errors.append(_("Path is not consistent with parent path"))
            
            self._validated = True
            
            if self._validation_errors:
                self.logger.warning(f"Folder validation failed for '{self.name}': {self._validation_errors}")
                return False
            
            self.logger.debug(f"Folder validation passed for '{self.name}'")
            return True
            
        except Exception as e:
            self.logger.error(f"Folder validation error for '{self.name}': {e}")
            self._validation_errors.append(_("Validation error: {error}").format(error=str(e)))
            return False
    
    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors."""
        return self._validation_errors.copy()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert folder to dictionary for serialization."""
        try:
            return {
                "name": self.name,
                "path": self.path,
                "parent_path": self.parent_path,
                # Metadata
                "created_at": self._created_at,
                "modified_at": self._modified_at,
                "session_count": self._session_count,
                "version": self._version
            }
        except Exception as e:
            self.logger.error(f"Folder serialization failed for '{self.name}': {e}")
            raise
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionFolder":
        """Create folder from dictionary data with migration support."""
        try:
            # Handle legacy data
            version = data.get("version", 0)
            if version == 0:
                # Migrate from legacy format
                folder = cls._migrate_from_legacy(data)
            else:
                # Current format
                folder = cls(
                    name=data.get("name", _("Unnamed Folder")),
                    path=data.get("path", ""),
                    parent_path=data.get("parent_path", "")
                )
                
                # Restore metadata
                folder._created_at = data.get("created_at", time.time())
                folder._modified_at = data.get("modified_at", time.time())
                folder._session_count = data.get("session_count", 0)
                folder._version = data.get("version", 1)
            
            return folder
            
        except Exception as e:
            logger = get_logger('ashyterm.sessions.folder')
            logger.error(f"Folder deserialization failed: {e}")
            raise
    
    @classmethod
    def _migrate_from_legacy(cls, data: Dict[str, Any]) -> "SessionFolder":
        """Migrate folder from legacy format."""
        logger = get_logger('ashyterm.sessions.folder')
        logger.info(f"Migrating legacy folder: {data.get('name', 'Unknown')}")
        
        folder = cls(
            name=data.get("name", _("Unnamed Folder")),
            path=data.get("path", ""),
            parent_path=data.get("parent_path", "")
        )
        
        # Mark as migrated
        folder._version = 1
        folder._created_at = time.time()
        folder._modified_at = time.time()
        
        return folder
    
    def get_checksum(self) -> str:
        """
        Calculate checksum for data integrity verification.
        
        Returns:
            MD5 checksum of folder data
        """
        try:
            data_string = f"{self.name}|{self.path}|{self.parent_path}"
            checksum = hashlib.md5(data_string.encode('utf-8')).hexdigest()
            return checksum
        except Exception as e:
            self.logger.error(f"Checksum calculation failed: {e}")
            return ""
    
    def is_root_folder(self) -> bool:
        """Check if this folder is at root level."""
        return not self.parent_path
    
    def get_depth(self) -> int:
        """Get folder depth (number of levels from root)."""
        if not self.path:
            return 0
        return self.path.count('/')
    
    def is_child_of(self, other_path: str) -> bool:
        """Check if this folder is a child of the given path."""
        if not other_path:
            return self.is_root_folder()
        return self.parent_path == other_path
    
    def is_descendant_of(self, other_path: str) -> bool:
        """Check if this folder is a descendant of the given path."""
        if not other_path:
            return True
        return self.path.startswith(other_path + "/")
    
    def get_metadata(self) -> Dict[str, Any]:
        """Get folder metadata."""
        return {
            'created_at': self._created_at,
            'modified_at': self._modified_at,
            'session_count': self._session_count,
            'version': self._version,
            'depth': self.get_depth(),
            'checksum': self.get_checksum(),
            'validated': self._validated
        }
    
    def __str__(self) -> str:
        return f"SessionFolder(name='{self.name}', path='{self.path}', sessions={self._session_count})"