"""
Custom exceptions for Ashy Terminal.

This module defines custom exception classes that provide more specific
error handling and better debugging information throughout the application.
"""

from typing import Optional, Dict, Any, Union
from enum import Enum


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for classification."""
    TERMINAL = "terminal"
    SESSION = "session"
    SSH = "ssh"
    UI = "ui"
    STORAGE = "storage"
    CONFIG = "config"
    PERMISSION = "permission"
    NETWORK = "network"
    SYSTEM = "system"
    VALIDATION = "validation"


class AshyTerminalError(Exception):
    """Base exception class for all Ashy Terminal errors."""
    
    def __init__(self, 
                 message: str,
                 category: ErrorCategory = ErrorCategory.SYSTEM,
                 severity: ErrorSeverity = ErrorSeverity.MEDIUM,
                 details: Optional[Dict[str, Any]] = None,
                 user_message: Optional[str] = None):
        """
        Initialize base exception.
        
        Args:
            message: Technical error message for logging
            category: Error category for classification
            severity: Error severity level
            details: Additional details for debugging
            user_message: User-friendly message for display
        """
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.details = details or {}
        self.user_message = user_message or self._generate_user_message()
    
    def _generate_user_message(self) -> str:
        """Generate a user-friendly message based on the category."""
        category_messages = {
            ErrorCategory.TERMINAL: "A terminal error occurred",
            ErrorCategory.SESSION: "A session error occurred",
            ErrorCategory.SSH: "An SSH connection error occurred",
            ErrorCategory.UI: "A user interface error occurred",
            ErrorCategory.STORAGE: "A data storage error occurred",
            ErrorCategory.CONFIG: "A configuration error occurred",
            ErrorCategory.PERMISSION: "A permission error occurred",
            ErrorCategory.NETWORK: "A network error occurred",
            ErrorCategory.SYSTEM: "A system error occurred",
            ErrorCategory.VALIDATION: "A validation error occurred"
        }
        return category_messages.get(self.category, "An unexpected error occurred")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging."""
        return {
            'type': self.__class__.__name__,
            'message': self.message,
            'category': self.category.value,
            'severity': self.severity.value,
            'details': self.details,
            'user_message': self.user_message
        }
    
    def __str__(self) -> str:
        return f"[{self.category.value.upper()}:{self.severity.value.upper()}] {self.message}"


# Terminal-related exceptions
class TerminalError(AshyTerminalError):
    """Base class for terminal-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.TERMINAL)
        super().__init__(message, **kwargs)


class TerminalCreationError(TerminalError):
    """Raised when terminal creation fails."""
    
    def __init__(self, reason: str, terminal_type: str = "unknown", **kwargs):
        message = f"Failed to create {terminal_type} terminal: {reason}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'terminal_type': terminal_type, 'reason': reason})
        kwargs.setdefault('user_message', f"Could not create terminal. {reason}")
        super().__init__(message, **kwargs)


class TerminalSpawnError(TerminalError):
    """Raised when process spawning fails."""
    
    def __init__(self, command: str, reason: str, **kwargs):
        message = f"Failed to spawn process '{command}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'command': command, 'reason': reason})
        kwargs.setdefault('user_message', "Failed to start terminal process")
        super().__init__(message, **kwargs)


class VTENotAvailableError(TerminalError):
    """Raised when VTE library is not available."""
    
    def __init__(self, **kwargs):
        message = "VTE library is not available or not properly installed"
        kwargs.setdefault('severity', ErrorSeverity.CRITICAL)
        kwargs.setdefault('user_message', 
                         "Terminal functionality requires VTE library. Please install gir1.2-vte-2.91")
        super().__init__(message, **kwargs)


# SSH-related exceptions
class SSHError(AshyTerminalError):
    """Base class for SSH-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.SSH)
        super().__init__(message, **kwargs)


class SSHConnectionError(SSHError):
    """Raised when SSH connection fails."""
    
    def __init__(self, host: str, reason: str, **kwargs):
        message = f"SSH connection to '{host}' failed: {reason}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'host': host, 'reason': reason})
        kwargs.setdefault('user_message', f"Could not connect to {host}. {reason}")
        super().__init__(message, **kwargs)


class SSHAuthenticationError(SSHError):
    """Raised when SSH authentication fails."""
    
    def __init__(self, host: str, username: str, auth_type: str, **kwargs):
        message = f"SSH authentication failed for {username}@{host} using {auth_type}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'host': host, 'username': username, 'auth_type': auth_type})
        kwargs.setdefault('user_message', f"Authentication failed for {username}@{host}")
        super().__init__(message, **kwargs)


class SSHKeyError(SSHError):
    """Raised when SSH key is invalid or not found."""
    
    def __init__(self, key_path: str, reason: str, **kwargs):
        message = f"SSH key error for '{key_path}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'key_path': key_path, 'reason': reason})
        kwargs.setdefault('user_message', f"SSH key problem: {reason}")
        super().__init__(message, **kwargs)


# Session-related exceptions
class SessionError(AshyTerminalError):
    """Base class for session-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.SESSION)
        super().__init__(message, **kwargs)


class SessionNotFoundError(SessionError):
    """Raised when session is not found."""
    
    def __init__(self, session_name: str, **kwargs):
        message = f"Session '{session_name}' not found"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'session_name': session_name})
        kwargs.setdefault('user_message', f"Session '{session_name}' could not be found")
        super().__init__(message, **kwargs)


class SessionValidationError(SessionError):
    """Raised when session validation fails."""
    
    def __init__(self, session_name: str, validation_errors: list, **kwargs):
        message = f"Session '{session_name}' validation failed: {', '.join(validation_errors)}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'session_name': session_name, 'errors': validation_errors})
        kwargs.setdefault('user_message', f"Session configuration is invalid: {validation_errors[0]}")
        super().__init__(message, **kwargs)


class SessionDuplicateError(SessionError):
    """Raised when trying to create duplicate session."""
    
    def __init__(self, session_name: str, **kwargs):
        message = f"Session '{session_name}' already exists"
        kwargs.setdefault('severity', ErrorSeverity.LOW)
        kwargs.setdefault('details', {'session_name': session_name})
        kwargs.setdefault('user_message', f"A session named '{session_name}' already exists")
        super().__init__(message, **kwargs)


# Storage-related exceptions
class StorageError(AshyTerminalError):
    """Base class for storage-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.STORAGE)
        super().__init__(message, **kwargs)


class StorageReadError(StorageError):
    """Raised when reading from storage fails."""
    
    def __init__(self, file_path: str, reason: str, **kwargs):
        message = f"Failed to read from '{file_path}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'file_path': file_path, 'reason': reason})
        kwargs.setdefault('user_message', "Could not load saved data")
        super().__init__(message, **kwargs)


class StorageWriteError(StorageError):
    """Raised when writing to storage fails."""
    
    def __init__(self, file_path: str, reason: str, **kwargs):
        message = f"Failed to write to '{file_path}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'file_path': file_path, 'reason': reason})
        kwargs.setdefault('user_message', "Could not save data")
        super().__init__(message, **kwargs)


class StorageCorruptedError(StorageError):
    """Raised when storage data is corrupted."""
    
    def __init__(self, file_path: str, details: str = "", **kwargs):
        message = f"Storage file '{file_path}' is corrupted"
        if details:
            message += f": {details}"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'file_path': file_path, 'corruption_details': details})
        kwargs.setdefault('user_message', "Saved data appears to be corrupted")
        super().__init__(message, **kwargs)


# Configuration-related exceptions
class ConfigError(AshyTerminalError):
    """Base class for configuration-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.CONFIG)
        super().__init__(message, **kwargs)


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails."""
    
    def __init__(self, config_key: str, value: Any, reason: str, **kwargs):
        message = f"Invalid configuration for '{config_key}' (value: {value}): {reason}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'config_key': config_key, 'value': value, 'reason': reason})
        kwargs.setdefault('user_message', f"Configuration error: {reason}")
        super().__init__(message, **kwargs)


class ConfigMissingError(ConfigError):
    """Raised when required configuration is missing."""
    
    def __init__(self, config_key: str, **kwargs):
        message = f"Required configuration '{config_key}' is missing"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'config_key': config_key})
        kwargs.setdefault('user_message', f"Required setting '{config_key}' is not configured")
        super().__init__(message, **kwargs)


# UI-related exceptions
class UIError(AshyTerminalError):
    """Base class for UI-related errors."""
    
    def __init__(self, component: str, message: str = None, **kwargs):
        """
        Initialize UI error.
        
        Args:
            component: UI component that failed
            message: Error message
        """
        if message is None:
            error_message = f"UI error in component: {component}"
        else:
            error_message = f"UI error in {component}: {message}"
        
        kwargs.setdefault('category', ErrorCategory.UI)
        kwargs.setdefault('details', {}).update({'component': component})
        super().__init__(error_message, **kwargs)
        self.component = component


class DialogError(UIError):
    """Raised when dialog operations fail."""
    
    def __init__(self, dialog_type: str, reason: str, **kwargs):
        message = f"Dialog error ({dialog_type}): {reason}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'dialog_type': dialog_type, 'reason': reason})
        kwargs.setdefault('user_message', f"Interface error: {reason}")
        super().__init__(message, **kwargs)


class MenuError(UIError):
    """Raised when menu operations fail."""
    
    def __init__(self, menu_type: str, reason: str, **kwargs):
        message = f"Menu error ({menu_type}): {reason}"
        kwargs.setdefault('severity', ErrorSeverity.LOW)
        kwargs.setdefault('details', {'menu_type': menu_type, 'reason': reason})
        kwargs.setdefault('user_message', "Menu operation failed")
        super().__init__(message, **kwargs)


# Validation-related exceptions
class ValidationError(AshyTerminalError):
    """Base class for validation errors."""
    
    def __init__(self, message: str, category=None, severity=None, field: str = None, value: Any = None, reason: str = None, **kwargs):
        """
        Initialize validation error with full compatibility.
        
        Args:
            message: Main error message
            category: Error category (positional or keyword)
            severity: Error severity (positional or keyword)
            field: Field that failed validation (optional)
            value: Value that failed validation (optional)
            reason: Specific reason for failure (optional)
        """
        # Handle both positional and keyword arguments for category/severity
        if category is not None:
            kwargs.setdefault('category', category)
        else:
            kwargs.setdefault('category', ErrorCategory.VALIDATION)
            
        if severity is not None:
            kwargs.setdefault('severity', severity)
        else:
            kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        
        # Build comprehensive error message
        if field and reason:
            error_message = f"Validation failed for '{field}': {reason}"
            if message and message != reason:
                error_message = f"{message} - {error_message}"
        elif field:
            error_message = f"Validation failed for '{field}': {message}"
        else:
            error_message = message
        
        kwargs.setdefault('details', {}).update({
            'field': field,
            'value': value,
            'reason': reason
        })
        
        super().__init__(error_message, **kwargs)
        self.field = field
        self.value = value
        self.reason = reason
        """
        Initialize validation error.
        
        Args:
            message: Main error message
            field: Field that failed validation (optional)
            value: Value that failed validation (optional)
            reason: Specific reason for failure (optional)
        """
        # Build comprehensive error message
        if field and reason:
            error_message = f"Validation failed for '{field}': {reason}"
            if message and message != reason:
                error_message = f"{message} - {error_message}"
        elif field:
            error_message = f"Validation failed for '{field}': {message}"
        else:
            error_message = message
        
        kwargs.setdefault('category', ErrorCategory.VALIDATION)
        kwargs.setdefault('details', {}).update({
            'field': field,
            'value': value,
            'reason': reason
        })
        
        super().__init__(error_message, **kwargs)
        self.field = field
        self.value = value
        self.reason = reason


class HostnameValidationError(ValidationError):
    """Raised when hostname validation fails."""
    
    def __init__(self, hostname: str, reason: str, **kwargs):
        message = f"Invalid hostname '{hostname}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'hostname': hostname, 'reason': reason})
        kwargs.setdefault('user_message', f"Invalid hostname: {reason}")
        super().__init__(message, **kwargs)


class PathValidationError(ValidationError):
    """Raised when path validation fails."""
    
    def __init__(self, path: str, reason: str, **kwargs):
        message = f"Invalid path '{path}': {reason}"
        kwargs.setdefault('severity', ErrorSeverity.MEDIUM)
        kwargs.setdefault('details', {'path': path, 'reason': reason})
        kwargs.setdefault('user_message', f"Invalid path: {reason}")
        super().__init__(message, **kwargs)


# Permission-related exceptions
class PermissionError(AshyTerminalError):
    """Base class for permission-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.PERMISSION)
        super().__init__(message, **kwargs)


class FilePermissionError(PermissionError):
    """Raised when file permission is denied."""
    
    def __init__(self, file_path: str, operation: str, **kwargs):
        message = f"Permission denied for {operation} operation on '{file_path}'"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'file_path': file_path, 'operation': operation})
        kwargs.setdefault('user_message', f"Permission denied accessing {file_path}")
        super().__init__(message, **kwargs)


class DirectoryPermissionError(PermissionError):
    """Raised when directory permission is denied."""
    
    def __init__(self, directory_path: str, operation: str, **kwargs):
        message = f"Permission denied for {operation} operation on directory '{directory_path}'"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'directory_path': directory_path, 'operation': operation})
        kwargs.setdefault('user_message', f"Permission denied accessing directory {directory_path}")
        super().__init__(message, **kwargs)


# Network-related exceptions
class NetworkError(AshyTerminalError):
    """Base class for network-related errors."""
    
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.NETWORK)
        super().__init__(message, **kwargs)


class ConnectionTimeoutError(NetworkError):
    """Raised when network connection times out."""
    
    def __init__(self, host: str, timeout: int, **kwargs):
        message = f"Connection to '{host}' timed out after {timeout} seconds"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'host': host, 'timeout': timeout})
        kwargs.setdefault('user_message', f"Connection to {host} timed out")
        super().__init__(message, **kwargs)


class HostUnreachableError(NetworkError):
    """Raised when host is unreachable."""
    
    def __init__(self, host: str, **kwargs):
        message = f"Host '{host}' is unreachable"
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'host': host})
        kwargs.setdefault('user_message', f"Cannot reach {host}")
        super().__init__(message, **kwargs)


# Exception utilities
def handle_exception(exception: Exception, 
                    context: str = "",
                    logger_name: str = None,
                    reraise: bool = False) -> Optional[AshyTerminalError]:
    """
    Handle an exception by logging it and optionally converting to AshyTerminalError.
    
    Args:
        exception: Exception to handle
        context: Context where the exception occurred
        logger_name: Logger name to use
        reraise: Whether to re-raise the exception
        
    Returns:
        AshyTerminalError if conversion was done, None otherwise
    """
    from .logger import get_logger, log_error_with_context
    
    # Log the original exception
    log_error_with_context(exception, context, logger_name)
    
    # Convert to AshyTerminalError if not already one
    if isinstance(exception, AshyTerminalError):
        converted_exception = exception
    else:
        # Create a generic AshyTerminalError
        converted_exception = AshyTerminalError(
            message=str(exception),
            details={'original_type': type(exception).__name__, 'context': context}
        )
    
    if reraise:
        raise converted_exception
    
    return converted_exception


def create_error_from_exception(exception: Exception, 
                               category: ErrorCategory = ErrorCategory.SYSTEM,
                               severity: ErrorSeverity = ErrorSeverity.MEDIUM,
                               user_message: str = None) -> AshyTerminalError:
    """
    Create an AshyTerminalError from a generic exception.
    
    Args:
        exception: Original exception
        category: Error category
        severity: Error severity
        user_message: User-friendly message
        
    Returns:
        AshyTerminalError instance
    """
    return AshyTerminalError(
        message=str(exception),
        category=category,
        severity=severity,
        details={'original_type': type(exception).__name__},
        user_message=user_message
    )