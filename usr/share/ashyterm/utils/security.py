"""
Security utilities for Ashy Terminal.

This module provides security validation, input sanitization, and security-related
utilities to protect against common vulnerabilities and ensure safe operations.
"""

import os
import re
import stat
import socket
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union
from urllib.parse import urlparse
import ipaddress

from .logger import get_logger
from .exceptions import (
    ValidationError, HostnameValidationError, PathValidationError,
    SSHKeyError, FilePermissionError, DirectoryPermissionError,
    ErrorSeverity
)
from .translation_utils import _


class SecurityConfig:
    """Security configuration and limits."""
    
    # SSH validation
    MAX_HOSTNAME_LENGTH = 253
    MAX_USERNAME_LENGTH = 32
    MAX_SSH_KEY_SIZE = 16384  # 16KB
    ALLOWED_SSH_KEY_TYPES = [
        'ssh-rsa', 'ssh-dss', 'ssh-ed25519', 
        'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'
    ]
    
    # Path validation
    MAX_PATH_LENGTH = 4096
    FORBIDDEN_PATH_CHARS = ['<', '>', ':', '"', '|', '?', '*', '\0']
    FORBIDDEN_PATH_SEQUENCES = ['../', '../', '..\\', '..\\\\']
    
    # Session validation
    MAX_SESSION_NAME_LENGTH = 128
    MAX_FOLDER_NAME_LENGTH = 128
    
    # Network security
    CONNECT_TIMEOUT = 10
    PRIVATE_IP_RANGES = [
        '127.0.0.0/8',    # localhost
        '10.0.0.0/8',     # private class A
        '172.16.0.0/12',  # private class B
        '192.168.0.0/16', # private class C
        '169.254.0.0/16', # link-local
    ]
    
    # File security
    SECURE_FILE_PERMISSIONS = 0o600
    SECURE_DIR_PERMISSIONS = 0o700
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


class InputSanitizer:
    """Input sanitization utilities."""
    
    @staticmethod
    def sanitize_filename(filename: str, replacement: str = '_') -> str:
        """
        Sanitize filename for safe filesystem operations.
        
        Args:
            filename: Original filename
            replacement: Character to replace forbidden characters
            
        Returns:
            Sanitized filename
        """
        if not filename:
            return _("unnamed")
        
        # Remove forbidden characters
        forbidden_chars = '<>:"/\\|?*\0'
        sanitized = filename
        
        for char in forbidden_chars:
            sanitized = sanitized.replace(char, replacement)
        
        # Remove control characters
        sanitized = ''.join(char for char in sanitized if ord(char) >= 32)
        
        # Remove leading/trailing whitespace and dots
        sanitized = sanitized.strip(' .')
        
        # Ensure not empty
        if not sanitized:
            sanitized = _("unnamed")
        
        # Limit length
        if len(sanitized) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
            sanitized = sanitized[:SecurityConfig.MAX_SESSION_NAME_LENGTH]
        
        return sanitized
    
    @staticmethod
    def sanitize_hostname(hostname: str) -> str:
        """
        Sanitize hostname for SSH connections.
        
        Args:
            hostname: Original hostname
            
        Returns:
            Sanitized hostname
        """
        if not hostname:
            return ""
        
        # Remove whitespace
        sanitized = hostname.strip()
        
        # Convert to lowercase
        sanitized = sanitized.lower()
        
        # Remove any non-hostname characters
        sanitized = re.sub(r'[^a-z0-9.-]', '', sanitized)
        
        # Limit length
        if len(sanitized) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            sanitized = sanitized[:SecurityConfig.MAX_HOSTNAME_LENGTH]
        
        return sanitized
    
    @staticmethod
    def sanitize_username(username: str) -> str:
        """
        Sanitize username for SSH connections.
        
        Args:
            username: Original username
            
        Returns:
            Sanitized username
        """
        if not username:
            return ""
        
        # Remove whitespace
        sanitized = username.strip()
        
        # Remove any non-username characters (allow alphanumeric, underscore, hyphen, dot)
        sanitized = re.sub(r'[^a-zA-Z0-9._-]', '', sanitized)
        
        # Limit length
        if len(sanitized) > SecurityConfig.MAX_USERNAME_LENGTH:
            sanitized = sanitized[:SecurityConfig.MAX_USERNAME_LENGTH]
        
        return sanitized
    
    @staticmethod
    def sanitize_path(path: str) -> str:
        """
        Sanitize file path for safe operations.
        
        Args:
            path: Original path
            
        Returns:
            Sanitized path
        """
        if not path:
            return ""
        
        # Normalize path
        sanitized = os.path.normpath(path)
        
        # Remove forbidden sequences
        for sequence in SecurityConfig.FORBIDDEN_PATH_SEQUENCES:
            sanitized = sanitized.replace(sequence, '')
        
        # Remove null bytes and control characters
        sanitized = ''.join(char for char in sanitized if ord(char) >= 32 and char != '\0')
        
        # Limit length
        if len(sanitized) > SecurityConfig.MAX_PATH_LENGTH:
            sanitized = sanitized[:SecurityConfig.MAX_PATH_LENGTH]
        
        return sanitized


class HostnameValidator:
    """Hostname validation utilities."""
    
    @staticmethod
    def is_valid_hostname(hostname: str) -> bool:
        """
        Validate hostname format.
        
        Args:
            hostname: Hostname to validate
            
        Returns:
            True if hostname is valid
        """
        if not hostname:
            return False
        
        # Check length
        if len(hostname) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            return False
        
        # Check for valid characters
        if not re.match(r'^[a-zA-Z0-9.-]+$', hostname):
            return False
        
        # Check label rules
        labels = hostname.split('.')
        for label in labels:
            if not label:  # Empty label
                return False
            if len(label) > 63:  # Label too long
                return False
            if label.startswith('-') or label.endswith('-'):  # Invalid hyphens
                return False
        
        return True
    
    @staticmethod
    def is_valid_ip_address(ip_str: str) -> bool:
        """
        Validate IP address format.
        
        Args:
            ip_str: IP address string
            
        Returns:
            True if valid IP address
        """
        try:
            ipaddress.ip_address(ip_str)
            return True
        except ValueError:
            return False
    
    @staticmethod
    def is_private_ip(ip_str: str) -> bool:
        """
        Check if IP address is in private range.
        
        Args:
            ip_str: IP address string
            
        Returns:
            True if IP is private
        """
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip.is_private
        except ValueError:
            return False
    
    @staticmethod
    def resolve_hostname(hostname: str, timeout: float = 5.0) -> Optional[str]:
        """
        Resolve hostname to IP address with timeout.
        
        Args:
            hostname: Hostname to resolve
            timeout: Resolution timeout in seconds
            
        Returns:
            IP address string or None if resolution failed
        """
        logger = get_logger('ashyterm.security')
        
        try:
            socket.setdefaulttimeout(timeout)
            ip = socket.gethostbyname(hostname)
            return ip
        except (socket.gaierror, socket.timeout) as e:
            logger.debug(f"Hostname resolution failed for {hostname}: {e}")
            return None
        finally:
            socket.setdefaulttimeout(None)


class SSHKeyValidator:
    """SSH key validation utilities."""
    
    @staticmethod
    def validate_ssh_key_path(key_path: str) -> Tuple[bool, Optional[str]]:
        """
        Validate SSH key file path and accessibility.
        
        Args:
            key_path: Path to SSH key file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not key_path:
            return False, _("Key path is empty")
        
        try:
            path = Path(key_path)
            
            # Check if file exists
            if not path.exists():
                return False, _("Key file does not exist: {}").format(key_path)
            
            # Check if it's a file (not directory)
            if not path.is_file():
                return False, _("Key path is not a file: {}").format(key_path)
            
            # Check file size
            file_size = path.stat().st_size
            if file_size > SecurityConfig.MAX_SSH_KEY_SIZE:
                return False, _("Key file too large: {} bytes").format(file_size)
            
            if file_size == 0:
                return False, _("Key file is empty")
            
            # Check file permissions
            file_mode = path.stat().st_mode
            if file_mode & 0o077:  # Check if group/other have any permissions
                return False, _("Key file has insecure permissions (should be 600)")
            
            # Check if file is readable
            if not os.access(path, os.R_OK):
                return False, _("Key file is not readable")
            
            return True, None
            
        except OSError as e:
            return False, _("Error accessing key file: {}").format(e)
    
    @staticmethod
    def validate_ssh_key_content(key_content: str) -> Tuple[bool, Optional[str]]:
        """
        Validate SSH key content format.
        
        Args:
            key_content: SSH key content string
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not key_content:
            return False, _("Key content is empty")
        
        try:
            # Split key into parts
            parts = key_content.strip().split()
            
            if len(parts) < 2:
                return False, _("Invalid key format (missing parts)")
            
            key_type = parts[0]
            key_data = parts[1]
            
            # Validate key type
            if key_type not in SecurityConfig.ALLOWED_SSH_KEY_TYPES:
                return False, _("Unsupported key type: {}").format(key_type)
            
            # Validate key data (base64)
            import base64
            try:
                decoded = base64.b64decode(key_data)
                if len(decoded) < 32:  # Minimum reasonable key size
                    return False, _("Key data appears to be too short")
            except Exception:
                return False, _("Invalid base64 encoding in key data")
            
            return True, None
            
        except Exception as e:
            return False, _("Error validating key content: {}").format(e)
    
    @staticmethod
    def read_and_validate_ssh_key(key_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Read and validate SSH key file.
        
        Args:
            key_path: Path to SSH key file
            
        Returns:
            Tuple of (is_valid, error_message, key_content)
        """
        # First, validate the path, permissions, and accessibility of the key file.
        path_valid, path_error = SSHKeyValidator.validate_ssh_key_path(key_path)
        if not path_valid:
            return False, path_error, None
        
        # If path validation passes, we consider it valid for our application's purposes.
        # The underlying 'ssh' command is the ultimate authority for validating the key's content.
        # We return True and None for the content, as it's no longer needed for this step.
        return True, None, None


class PathValidator:
    """File path validation utilities."""
    
    @staticmethod
    def is_safe_path(path: str, base_path: Optional[str] = None) -> bool:
        """
        Check if path is safe (no directory traversal, etc.).
        
        Args:
            path: Path to validate
            base_path: Optional base path to constrain to
            
        Returns:
            True if path is safe
        """
        if not path:
            return False
        
        try:
            # Normalize the path
            normalized = os.path.normpath(path)
            
            # Check for directory traversal attempts
            if '..' in normalized.split(os.sep):
                return False
            
            # Check for forbidden characters
            for char in SecurityConfig.FORBIDDEN_PATH_CHARS:
                if char in normalized:
                    return False
            
            # Check against base path if provided
            if base_path:
                base_normalized = os.path.normpath(base_path)
                if not normalized.startswith(base_normalized):
                    return False
            
            # Check length
            if len(normalized) > SecurityConfig.MAX_PATH_LENGTH:
                return False
            
            return True
            
        except Exception:
            return False
    
    @staticmethod
    def validate_file_permissions(file_path: str, 
                                 required_permissions: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """
        Validate file permissions.
        
        Args:
            file_path: Path to file
            required_permissions: Required permission mask (e.g., 0o600)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            path = Path(file_path)
            
            if not path.exists():
                return False, _("File does not exist")
            
            file_stat = path.stat()
            current_permissions = file_stat.st_mode & 0o777
            
            if required_permissions is not None:
                if current_permissions != required_permissions:
                    return False, _("Incorrect permissions: {} (expected {})").format(oct(current_permissions), oct(required_permissions))
            
            # Check for overly permissive permissions
            if current_permissions & 0o077:  # Group or other have permissions
                return False, _("File has overly permissive permissions")
            
            return True, None
            
        except OSError as e:
            return False, _("Error checking file permissions: {}").format(e)


class SecurityAuditor:
    """Security auditing utilities."""
    
    def __init__(self):
        self.logger = get_logger('ashyterm.security.audit')
    
    def audit_ssh_session(self, session_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Audit SSH session configuration for security issues.
        
        Args:
            session_data: Session configuration dictionary
            
        Returns:
            List of security findings
        """
        findings = []
        
        # Check hostname
        hostname = session_data.get('host', '')
        if hostname:
            if not HostnameValidator.is_valid_hostname(hostname):
                findings.append({
                    'severity': 'medium',
                    'type': 'invalid_hostname',
                    'message': _("Invalid hostname format: {}").format(hostname),
                    'recommendation': _('Use a valid hostname or IP address')
                })
            
            # Check if hostname resolves
            if HostnameValidator.is_valid_hostname(hostname):
                ip = HostnameValidator.resolve_hostname(hostname)
                if ip and HostnameValidator.is_private_ip(ip):
                    findings.append({
                        'severity': 'low',
                        'type': 'private_ip',
                        'message': _("Connecting to private IP: {}").format(ip),
                        'recommendation': _('Ensure this is intentional')
                    })
        
        # Check authentication
        auth_type = session_data.get('auth_type', '')
        auth_value = session_data.get('auth_value', '')
        
        if auth_type == 'key' and auth_value:
            # Validate SSH key
            is_valid, error = SSHKeyValidator.validate_ssh_key_path(auth_value)
            if not is_valid:
                findings.append({
                    'severity': 'high',
                    'type': 'invalid_ssh_key',
                    'message': _("SSH key validation failed: {}").format(error),
                    'recommendation': _('Fix SSH key configuration')
                })
        elif auth_type == 'password':
            findings.append({
                'severity': 'medium',
                'type': 'password_auth',
                'message': _('Using password authentication'),
                'recommendation': _('Consider using SSH key authentication for better security')
            })
        
        # Check username
        username = session_data.get('user', '')
        if username == 'root':
            findings.append({
                'severity': 'medium',
                'type': 'root_user',
                'message': _('Connecting as root user'),
                'recommendation': _('Use a regular user account when possible')
            })
        
        return findings
    
    def audit_file_security(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Audit file security.
        
        Args:
            file_path: Path to file to audit
            
        Returns:
            List of security findings
        """
        findings = []
        
        try:
            path = Path(file_path)
            
            if not path.exists():
                findings.append({
                    'severity': 'high',
                    'type': 'file_not_found',
                    'message': _("File does not exist: {}").format(file_path),
                    'recommendation': _('Verify file path')
                })
                return findings
            
            # Check permissions
            is_valid, error = PathValidator.validate_file_permissions(file_path)
            if not is_valid:
                findings.append({
                    'severity': 'medium',
                    'type': 'insecure_permissions',
                    'message': error,
                    'recommendation': _('Set secure file permissions (600 for files, 700 for directories)')
                })
            
            # Check ownership (if on Unix)
            if hasattr(os, 'getuid'):
                file_stat = path.stat()
                current_uid = os.getuid()
                
                if file_stat.st_uid != current_uid:
                    findings.append({
                        'severity': 'medium',
                        'type': 'different_owner',
                        'message': _("File is owned by different user: {}").format(file_stat.st_uid),
                        'recommendation': _('Ensure file ownership is correct')
                    })
        
        except Exception as e:
            findings.append({
                'severity': 'high',
                'type': 'audit_error',
                'message': _("Error auditing file: {}").format(e),
                'recommendation': _('Check file accessibility')
            })
        
        return findings
    
    def audit_directory_security(self, dir_path: str) -> List[Dict[str, Any]]:
        """
        Audit directory security.
        
        Args:
            dir_path: Path to directory to audit
            
        Returns:
            List of security findings
        """
        findings = []
        
        try:
            path = Path(dir_path)
            
            if not path.exists():
                findings.append({
                    'severity': 'high',
                    'type': 'directory_not_found',
                    'message': _("Directory does not exist: {}").format(dir_path),
                    'recommendation': _('Create directory with secure permissions')
                })
                return findings
            
            if not path.is_dir():
                findings.append({
                    'severity': 'high',
                    'type': 'not_directory',
                    'message': _("Path is not a directory: {}").format(dir_path),
                    'recommendation': _('Use a valid directory path')
                })
                return findings
            
            # Check permissions
            dir_stat = path.stat()
            current_permissions = dir_stat.st_mode & 0o777
            
            if current_permissions & 0o077:  # Group or other have permissions
                findings.append({
                    'severity': 'medium',
                    'type': 'insecure_directory_permissions',
                    'message': _("Directory has overly permissive permissions: {}").format(oct(current_permissions)),
                    'recommendation': _('Set directory permissions to 700')
                })
        
        except Exception as e:
            findings.append({
                'severity': 'high',
                'type': 'audit_error',
                'message': _("Error auditing directory: {}").format(e),
                'recommendation': _('Check directory accessibility')
            })
        
        return findings


# Convenience functions
def sanitize_session_name(name: str) -> str:
    """Sanitize session name for safe use."""
    return InputSanitizer.sanitize_filename(name)


def sanitize_folder_name(name: str) -> str:
    """Sanitize folder name for safe use."""
    return InputSanitizer.sanitize_filename(name)


def validate_ssh_hostname(hostname: str) -> None:
    """
    Validate SSH hostname and raise exception if invalid.
    
    Args:
        hostname: Hostname to validate
        
    Raises:
        HostnameValidationError: If hostname is invalid
    """
    if not hostname:
        raise HostnameValidationError("", _("Hostname cannot be empty"))
    
    sanitized = InputSanitizer.sanitize_hostname(hostname)
    if not HostnameValidator.is_valid_hostname(sanitized):
        raise HostnameValidationError(hostname, _("Invalid hostname format"))


def validate_ssh_key_file(key_path: str) -> None:
    """
    Validate SSH key file and raise exception if invalid.
    
    Args:
        key_path: Path to SSH key file
        
    Raises:
        SSHKeyError: If key file is invalid
    """
    is_valid, error, _ = SSHKeyValidator.read_and_validate_ssh_key(key_path)
    if not is_valid:
        raise SSHKeyError(key_path, error or _("Unknown validation error"))


def validate_file_path(file_path: str, base_path: Optional[str] = None) -> None:
    """
    Validate file path and raise exception if unsafe.
    
    Args:
        file_path: Path to validate
        base_path: Optional base path constraint
        
    Raises:
        PathValidationError: If path is unsafe
    """
    if not PathValidator.is_safe_path(file_path, base_path):
        raise PathValidationError(file_path, _("Path contains unsafe elements"))


def ensure_secure_file_permissions(file_path: str) -> None:
    """
    Ensure file has secure permissions.
    
    Args:
        file_path: Path to file
        
    Raises:
        FilePermissionError: If permissions cannot be set
    """
    try:
        path = Path(file_path)
        if path.exists():
            path.chmod(SecurityConfig.SECURE_FILE_PERMISSIONS)
    except OSError as e:
        raise FilePermissionError(file_path, _("set secure permissions"), details={'reason': str(e)})


def ensure_secure_directory_permissions(dir_path: str) -> None:
    """
    Ensure directory has secure permissions.
    
    Args:
        dir_path: Path to directory
        
    Raises:
        DirectoryPermissionError: If permissions cannot be set
    """
    try:
        path = Path(dir_path)
        if path.exists():
            path.chmod(SecurityConfig.SECURE_DIR_PERMISSIONS)
    except OSError as e:
        raise DirectoryPermissionError(dir_path, _("set secure permissions"), details={'reason': str(e)})


def create_security_auditor() -> SecurityAuditor:
    """Create a new security auditor instance."""
    return SecurityAuditor()


def validate_session_data(session_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate session data for security and correctness.
    
    Args:
        session_data: Session configuration dictionary
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    try:
        # Validate session name
        name = session_data.get('name', '')
        if not name or not name.strip():
            errors.append(_("Session name cannot be empty"))
        elif len(name) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
            errors.append(_("Session name too long (max {} characters)").format(SecurityConfig.MAX_SESSION_NAME_LENGTH))
        
        # Validate hostname for non-local sessions
        host = session_data.get('host', '')
        if host:  # SSH session
            if not host.strip():
                errors.append(_("Hostname cannot be empty for SSH sessions"))
            elif not HostnameValidator.is_valid_hostname(host.strip()):
                errors.append(_("Invalid hostname format: {}").format(host))
        
        # Validate username
        username = session_data.get('user', '')
        if host and not username:  # SSH session requires username
            errors.append(_("Username is required for SSH sessions"))
        elif username and len(username) > SecurityConfig.MAX_USERNAME_LENGTH:
            errors.append(_("Username too long (max {} characters)").format(SecurityConfig.MAX_USERNAME_LENGTH))
        
        # Validate port
        port = session_data.get('port', 22)
        if port is not None:
            try:
                port_int = int(port)
                if not (1 <= port_int <= 65535):
                    errors.append(_("Port must be between 1 and 65535"))
            except (ValueError, TypeError):
                errors.append(_("Port must be a valid number"))
        
        # Validate authentication
        auth_type = session_data.get('auth_type', '')
        auth_value = session_data.get('auth_value', '')
        
        if host:  # SSH session
            if auth_type == 'key':
                if not auth_value:
                    errors.append(_("SSH key file path is required for key authentication"))
                else:
                    # Validate SSH key file
                    is_key_valid, key_error = SSHKeyValidator.validate_ssh_key_path(auth_value)
                    if not is_key_valid:
                        errors.append(_("SSH key validation failed: {}").format(key_error))
            elif auth_type == 'password':
                # Password auth is valid but not recommended (handled by security audit)
                pass
            elif auth_type not in ['key', 'password', '']:
                errors.append(_("Invalid authentication type: {}").format(auth_type))
        
        # Validate folder path if specified
        folder_path = session_data.get('folder_path', '')
        if folder_path:
            if not PathValidator.is_safe_path(folder_path):
                errors.append(_("Invalid or unsafe folder path"))
        
        # Additional session-specific validations
        session_type = session_data.get('type', 'ssh')
        if session_type not in ['local', 'ssh']:
            errors.append(_("Invalid session type: {}").format(session_type))
        
        return len(errors) == 0, errors
        
    except Exception as e:
        logger = get_logger('ashyterm.security.validation')
        logger.error(f"Session validation error: {e}")
        return False, [_("Validation error: {}").format(e)]


def validate_session_security(session_data: Dict[str, Any]) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Comprehensive session security validation including audit.
    
    Args:
        session_data: Session configuration dictionary
        
    Returns:
        Tuple of (is_valid, validation_errors, security_findings)
    """
    try:
        # First do basic validation
        is_valid, validation_errors = validate_session_data(session_data)
        
        # Then do security audit
        auditor = create_security_auditor()
        security_findings = auditor.audit_ssh_session(session_data)
        
        # Check for critical security issues that should block connection
        critical_findings = [
            finding for finding in security_findings 
            if finding['severity'] in ['critical', 'high']
        ]
        
        # If there are critical security issues, mark as invalid
        if critical_findings:
            is_valid = False
            for finding in critical_findings:
                validation_errors.append(_("Security: {}").format(finding['message']))
        
        return is_valid, validation_errors, security_findings
        
    except Exception as e:
        logger = get_logger('ashyterm.security.validation')
        logger.error(f"Comprehensive session validation error: {e}")
        return False, [_("Security validation error: {}").format(e)], []